# Airflow DAGs — IA Odonto Lab

This directory contains the Airflow DAGs that orchestrate all automated
pipelines for the IA Odonto Lab production system.

---

## DAGs Overview

| DAG | Schedule | Purpose |
|-----|----------|---------|
| `pipeline_bronze_silver` | Every 2 hours | Export Bronze Parquet → validate → dbt Silver → cleanup message buffer |
| `pipeline_backups` | Daily 06:00 UTC (03:00 Recife) | MariaDB + PostgreSQL dumps → GitHub infra backup → cleanup |

---

## Infrastructure Dependencies

### Containers required (must be running)
| Container | Role | Used by |
|-----------|------|---------|
| `ia-odonto-api` | Lina FastAPI + export_bronze.py | `pipeline_bronze_silver` |
| `ia-odonto-db` | PostgreSQL 16 + pgvector (ia_odonto + airflow DBs) | both DAGs |
| `ia_mariadb` | MariaDB — EspoCRM data source | `pipeline_bronze_silver`, `pipeline_backups` |
| `ia_postgres` | PostgreSQL 12 — n8n_buffer | `pipeline_bronze_silver` |
| `ia_airflow` | Airflow itself | both DAGs |
| `ia_n8n` | n8n — receives infra backup webhook | `pipeline_backups` |

### Database map
| Database | Container | Engine | Purpose |
|----------|-----------|--------|---------|
| `espocrm` | `ia_mariadb` | MariaDB 10.6 | CRM — contacts, leads, notes, payments |
| `ia_odonto` | `ia-odonto-db` | PostgreSQL 16 + pgvector | Lina RAG + episodic memory |
| `airflow` | `ia-odonto-db` | PostgreSQL 16 | Airflow metadata (DAG runs, task history) |
| `n8n_buffer` | `ia_postgres` | PostgreSQL 12 | WhatsApp message buffer (n8n) |
| `silver.duckdb` | file on VPS | DuckDB | dbt Silver layer (recreated each run) |

### Volume paths (VPS)
| Path | Purpose |
|------|---------|
| `/opt/ia-odonto-lab/data_lake/bronze/` | Parquet files partitioned by date |
| `/opt/ia-odonto-lab/data_lake/silver/` | dbt project + DuckDB file |
| `/opt/ia-odonto-lab/backups/` | SQL dump files (kept 7 days) — writable by Airflow uid 50000 |

---

## pipeline_bronze_silver

### What it does
1. `export_bronze` — runs `export_bronze.py` inside `ia-odonto-api`, exports
   MariaDB contacts + payments and PostgreSQL RAG audit to partitioned Parquet files.
2. `validate_parquet` — checks that expected columns exist in each Parquet partition.
   Fails fast if schema drift is detected, preventing silent data quality issues.
3. `dbt_run` — runs all dbt Silver models (staging + marts) using DuckDB as engine.
   Reads from Bronze Parquet, writes to `silver.duckdb`.
4. `dbt_test` — runs all dbt tests. Warns on optional nullable fields
   (`lifetime_value`, `potencial_venda`) — these are expected for new patients.
5. `cleanup_message_buffer` — deletes processed messages older than 30 days
   from the `n8n_buffer` database, preventing unbounded growth.

### Expected results (healthy run)

export_bronze      → success
validate_parquet   → success
dbt_run            → PASS=6 WARN=0 ERROR=0
dbt_test           → PASS=35 WARN=2 ERROR=0  ← WARN is expected, not a bug
cleanup_message_buffer → success

### Known warnings (not bugs)
- `not_null_stg_contacts_lifetime_value` — WARN, severity: warn. New patients have no LTV yet.
- `not_null_stg_contacts_potencial_venda` — WARN, severity: warn. New patients have no estimate yet.

---

## pipeline_backups

### What it does
1. `backup_mariadb` + `backup_postgres` — run in parallel. Dump EspoCRM (MariaDB)
   and Lina (PostgreSQL) to `/opt/ia-odonto-lab/backups/` with date-stamped filenames.
2. `backup_infra_github` — sends both `docker-compose` files base64-encoded
   to the n8n backup webhook, which commits them to the private
   `sistema-odonto-crm` GitHub repository.
3. `cleanup_old_backups` — deletes SQL dump files older than 7 days.
4. `cleanup_espocrm_job_logs` — deletes EspoCRM internal job queue entries
   and scheduled job logs older than 1 day. Prevents the 831MB bloat problem
   observed in production (1.17M rows in 2 months).
5. `cleanup_airflow_history` — runs `airflow db clean` to purge DAG run history,
   task instances, and XCom entries older than 30 days from the PostgreSQL backend.

### Backup path
Backups are written to `/opt/ia-odonto-lab/backups/` (not `/root/backups/`).
The Airflow container runs as uid 50000, which cannot traverse `/root` (drwx------
root root) even with chmod 777 on the subdirectory. The `/opt/ia-odonto-lab/backups/`
path is owned by root with 777 permissions and is fully accessible to uid 50000.

### Dependency graph

backup_mariadb ──┐
├──► backup_infra_github ──► cleanup_old_backups
backup_postgres ──┘
cleanup_espocrm_job_logs   (independent, parallel)
cleanup_airflow_history    (independent, parallel)

---

## Troubleshooting

### Docker socket permission denied
The Airflow container must run with GID 112 (docker group on host).
Check: `stat /var/run/docker.sock` — the group must match the GID in the container.
Fix: recreate the container via `/tmp/start_airflow.sh` on the VPS.

### Task stuck in "queued" state
The LocalExecutor requires at least one free slot. Check parallelism settings
in `AIRFLOW__CORE__PARALLELISM` and `AIRFLOW__CORE__MAX_ACTIVE_TASKS_PER_DAG`.

### dbt_test shows ERROR (not WARN)
A new mandatory field may have been added without a corresponding Bronze export.
Check `export_bronze.py` and compare `EXPECTED_COLUMNS` in `pipeline_bronze_silver.py`.

### backup_mariadb times out
The EspoCRM database grows over time. Current production size: ~438MB.
If timeout occurs, increase `execution_timeout` in the task definition.

### Airflow webserver crashes during DAG run
Add or increase `AIRFLOW__WEBSERVER__WEB_SERVER_WORKER_TIMEOUT=300` in the
container environment. Recreate via `/tmp/start_airflow.sh`.

### message_buffer not found
The `n8n_buffer` database lives in `ia_postgres` (PostgreSQL 12), not `ia-odonto-db`.
Connection: `host=ia_postgres user=chatwoot dbname=n8n_buffer`.

### backup_mariadb / backup_postgres: Permission denied on /root/backups
The Airflow user (uid 50000) cannot write to /root/backups even with chmod 777
because the parent directory /root is drwx------ and blocks traversal.
Solution: use /opt/ia-odonto-lab/backups/ — already configured in this DAG.

---

## Recreating the Airflow container

If the `ia_airflow` container needs to be recreated, use the script on the VPS:

```bash
bash /tmp/start_airflow.sh
```

This script sets all required environment variables, mounts the correct volumes,
configures the docker socket GID, and connects to both Docker networks.

**Warning:** if the VPS is rebooted, this script must be run again manually,
as the container is not managed by docker-compose.

---

## Adding a new DAG

1. Create the `.py` file in this `dags/` directory.
2. The Airflow container mounts this directory as a volume — no rebuild needed.
3. The DAG appears in the UI within 30 seconds (default dag file processor interval).
4. Always test with **Trigger DAG** manually before enabling the schedule.
5. Update this README with the new DAG's purpose and dependencies.