"""
DAG: pipeline_backups
Schedule: daily at 06:00 UTC (03:00 Recife, UTC-3)
Purpose: Disaster-recovery backups for all stateful services + log cleanup.

This DAG replaces the following VPS crontab entries:
  10 3 * * * mariadb-dump → /opt/ia-odonto-lab/backups/
  15 3 * * * pg_dump      → /opt/ia-odonto-lab/backups/
  35 3 * * * curl webhook → n8n → GitHub (docker-compose files)

Backup user strategy:
  MariaDB  → espo-user (MYSQL_PASSWORD env var inside container)
             Root authentication fails with special chars (#) in .my.cnf config files.
             espo-user has full access to espocrm database and works correctly.
             Redirect (>) must be OUTSIDE sh -c to write to VPS filesystem.
  PostgreSQL → postgres user via pg_dump (no password required inside container)

Backup path: /opt/ia-odonto-lab/backups/
  This path is mounted in the Airflow container with write permissions.
  The Airflow user (uid 50000) cannot write to /root/backups/ due to
  /root directory permissions (drwx------ root root), even with chmod 777
  on the subdirectory. Moving to /opt/ia-odonto-lab/backups/ solves this.

Stages:
  t_mariadb  ──┐
  t_postgres ──┼──► t_infra ──► t_cleanup_old_backups
  (parallel)   │
               └── (parallel)
  t_cleanup_espo         (independent, parallel)
  t_cleanup_airflow      (independent, parallel)
  t_cleanup_metabase     (independent, parallel)
  t_cleanup_rag_audit    (independent, parallel)
  t_cleanup_app_logs     (independent, parallel)
  t_cleanup_n8n          (independent, parallel)

On failure: sends alert via n8n webhook → Gmail.

Note: n8n workflow backup (daily 00h) runs inside n8n itself and is NOT
managed here — it has no dependency on this DAG.

Note: Docker container log rotation is handled by daemon.json (max-size: 10m,
max-file: 3) — no Airflow task needed for container logs.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

# ---------------------------------------------------------------------------
# Default arguments
# ---------------------------------------------------------------------------
DEFAULT_ARGS = {
    "owner": "renato",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "email_on_failure": False,
    "email_on_retry": False,
}

# Backup destination — writable by Airflow user (uid 50000)
BACKUP_PATH = "/opt/ia-odonto-lab/backups"


# ---------------------------------------------------------------------------
# Failure callback — sends alert via n8n webhook → Gmail
# ---------------------------------------------------------------------------
def _on_failure_callback(context):
    dag_id = context["dag"].dag_id
    task_id = context["task_instance"].task_id
    execution_date = str(context["execution_date"])
    log_url = context["task_instance"].log_url

    payload = json.dumps(
        {
            "dag_id": dag_id,
            "task_id": task_id,
            "execution_date": execution_date,
            "log_url": log_url,
        }
    ).encode()

    try:
        req = urllib.request.Request(
            "http://ia_n8n:5678/webhook/airflow-alert",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Task: MariaDB backup (EspoCRM — 438MB, allow up to 30 min)
# ---------------------------------------------------------------------------
MARIADB_BACKUP_CMD = (
    "docker exec ia_mariadb sh -c "
    "'mariadb-dump -u espo-user -p\"$MYSQL_PASSWORD\" espocrm' "
    f"> {BACKUP_PATH}/mariadb_espocrm_$(date +%Y%m%d).sql"
)

# ---------------------------------------------------------------------------
# Task: PostgreSQL backup (Lina — pgvector embeddings + RAG audit)
# ---------------------------------------------------------------------------
POSTGRES_BACKUP_CMD = (
    "docker exec ia-odonto-db pg_dump -U postgres ia_odonto "
    f"> {BACKUP_PATH}/postgres_lina_$(date +%Y%m%d).sql"
)

# ---------------------------------------------------------------------------
# Task: Infrastructure backup (docker-compose files → GitHub via n8n webhook)
# ---------------------------------------------------------------------------
INFRA_BACKUP_CMD = (
    "curl -s -X POST 'http://ia_n8n:5678/webhook/backup-infra' "
    "-H 'Content-Type: application/json' "
    '-d "{'
    '\\"stack_ia\\":\\"$(base64 -w0 /home/adminlumina/stack-ia/docker-compose.yml)\\",'
    '\\"lina_api\\":\\"$(base64 -w0 /opt/ia-odonto-lab/docker-compose.yml)\\"'
    '}"'
)

# ---------------------------------------------------------------------------
# Task: Cleanup old SQL backups (keep last 7 days)
# ---------------------------------------------------------------------------
CLEANUP_CMD = (
    f"find {BACKUP_PATH} -name '*.sql' -mtime +7 -delete && "
    "echo 'Old SQL backups cleaned up.'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup EspoCRM internal job logs (keep last 1 day)
# ---------------------------------------------------------------------------
CLEANUP_ESPOCRM_JOBS_CMD = (
    "docker exec ia_mariadb sh -c '"
    'mariadb -u espo-user -p"$MYSQL_PASSWORD" espocrm -e "'
    "DELETE FROM job WHERE executed_at < NOW() - INTERVAL 1 DAY;"
    "DELETE FROM scheduled_job_log_record WHERE created_at < NOW() - INTERVAL 1 DAY;"
    "\"'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup Airflow metadata database (keep last 30 days)
# ---------------------------------------------------------------------------
CLEANUP_AIRFLOW_HISTORY_CMD = (
    "docker exec ia_airflow airflow db clean "
    "--clean-before-timestamp $(date -d '30 days ago' '+%Y-%m-%dT%H:%M:%S+00:00') "
    "--yes "
    "&& echo 'Airflow history cleaned up.'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup Metabase query execution logs (keep last 7 days)
# ---------------------------------------------------------------------------
CLEANUP_METABASE_LOGS_CMD = (
    "docker exec ia-odonto-db psql -U postgres -d metabase -c "
    "\"DELETE FROM query_execution WHERE started_at < NOW() - INTERVAL '7 days';\" "
    "&& echo 'Metabase query_execution cleaned up.'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup rag_audit table (keep last 90 days)
# ---------------------------------------------------------------------------
CLEANUP_RAG_AUDIT_CMD = (
    "docker exec ia-odonto-db psql -U postgres -d ia_odonto -c "
    "\"DELETE FROM rag_audit WHERE created_at < NOW() - INTERVAL '90 days';\" "
    "&& echo 'rag_audit cleaned up.'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup application log files (daily truncation)
# ---------------------------------------------------------------------------
CLEANUP_APP_LOGS_CMD = (
    "docker exec ia_airflow truncate -s 0 /app/logs/silver_run.log && "
    "docker exec ia-odonto-api truncate -s 0 /app/data_lake/silver/ia_odonto_silver/logs/dbt.log && "
    "echo 'Application logs truncated.'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup old n8n backups (keep last 7 days)
#
# n8n workflow data is backed up daily via crontab to /root/backups/n8n/.
# Without cleanup, backups grow indefinitely (~7MB/day = ~2.5GB/year).
# ---------------------------------------------------------------------------
CLEANUP_N8N_BACKUPS_CMD = (
    "find /root/backups/n8n -name '*.tar.gz' -mtime +7 -delete && "
    "echo 'Old n8n backups cleaned up.'"
)

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="pipeline_backups",
    description="Daily backups + log cleanup: MariaDB, PostgreSQL, docker-compose → GitHub",
    schedule_interval="0 6 * * *",  # 06:00 UTC = 03:00 Recife (UTC-3)
    start_date=datetime(2026, 5, 3),
    catchup=False,
    default_args=DEFAULT_ARGS,
    on_failure_callback=_on_failure_callback,
    tags=["backup", "production", "infrastructure"],
) as dag:

    t_mariadb = BashOperator(
        task_id="backup_mariadb",
        bash_command=MARIADB_BACKUP_CMD,
        execution_timeout=timedelta(minutes=30),
        on_failure_callback=_on_failure_callback,
    )

    t_postgres = BashOperator(
        task_id="backup_postgres",
        bash_command=POSTGRES_BACKUP_CMD,
        execution_timeout=timedelta(minutes=15),
        on_failure_callback=_on_failure_callback,
    )

    t_infra = BashOperator(
        task_id="backup_infra_github",
        bash_command=INFRA_BACKUP_CMD,
        execution_timeout=timedelta(minutes=5),
        on_failure_callback=_on_failure_callback,
    )

    t_cleanup = BashOperator(
        task_id="cleanup_old_backups",
        bash_command=CLEANUP_CMD,
        on_failure_callback=_on_failure_callback,
    )

    t_cleanup_espo = BashOperator(
        task_id="cleanup_espocrm_job_logs",
        bash_command=CLEANUP_ESPOCRM_JOBS_CMD,
        execution_timeout=timedelta(minutes=10),
        on_failure_callback=_on_failure_callback,
    )

    t_cleanup_airflow = BashOperator(
        task_id="cleanup_airflow_history",
        bash_command=CLEANUP_AIRFLOW_HISTORY_CMD,
        execution_timeout=timedelta(minutes=10),
        on_failure_callback=_on_failure_callback,
    )

    t_cleanup_metabase = BashOperator(
        task_id="cleanup_metabase_logs",
        bash_command=CLEANUP_METABASE_LOGS_CMD,
        execution_timeout=timedelta(minutes=5),
        on_failure_callback=_on_failure_callback,
    )

    t_cleanup_rag_audit = BashOperator(
        task_id="cleanup_rag_audit",
        bash_command=CLEANUP_RAG_AUDIT_CMD,
        execution_timeout=timedelta(minutes=5),
        on_failure_callback=_on_failure_callback,
    )

    t_cleanup_app_logs = BashOperator(
        task_id="cleanup_app_logs",
        bash_command=CLEANUP_APP_LOGS_CMD,
        execution_timeout=timedelta(minutes=2),
        on_failure_callback=_on_failure_callback,
    )

    t_cleanup_n8n = BashOperator(
        task_id="cleanup_n8n_backups",
        bash_command=CLEANUP_N8N_BACKUPS_CMD,
        execution_timeout=timedelta(minutes=2),
        on_failure_callback=_on_failure_callback,
    )

    [t_mariadb, t_postgres] >> t_infra >> t_cleanup
    t_cleanup_espo
    t_cleanup_airflow
    t_cleanup_metabase
    t_cleanup_rag_audit
    t_cleanup_app_logs
    t_cleanup_n8n
