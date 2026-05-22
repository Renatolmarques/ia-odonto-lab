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
  t_cleanup_espo_logs    (independent, parallel) — job + scheduled_job_log_record
  t_cleanup_espo_misc    (independent, parallel) — note, auth_log, action_history,
                                                   webhook_queue, email_queue,
                                                   two_factor_code, lead_capture_log_record,
                                                   campaign_log_record, notification
  t_cleanup_evolution    (independent, parallel) — Message, Session, Chat
  t_cleanup_airflow      (independent, parallel) — DB metadata + log files on disk
  t_cleanup_metabase     (independent, parallel) — query_execution + task_history
  t_cleanup_rag_audit    (independent, parallel)
  t_cleanup_app_logs     (independent, parallel)
  t_cleanup_n8n          (independent, parallel)
  t_cleanup_docker       (independent, parallel)
  t_cleanup_parquet      (independent, parallel) — Bronze Parquet partitions > 90 days

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

# Bronze data lake path — Parquet partitions exported daily by export_bronze.py
BRONZE_PATH = "/opt/ia-odonto-lab/data_lake/bronze"


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
# Task: MariaDB backup (EspoCRM — allow up to 30 min)
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
# Task: Cleanup EspoCRM job logs (keep last 1 day)
# Covers: job, scheduled_job_log_record
# These are the two largest tables in EspoCRM (25MB + 16MB confirmed 2026-05-17).
# ---------------------------------------------------------------------------
CLEANUP_ESPOCRM_JOBS_CMD = (
    "docker exec ia_mariadb sh -c '"
    'mariadb -u espo-user -p"$MYSQL_PASSWORD" espocrm -e "'
    "DELETE FROM job WHERE executed_at < NOW() - INTERVAL 1 DAY;"
    "DELETE FROM scheduled_job_log_record WHERE created_at < NOW() - INTERVAL 1 DAY;"
    "\"'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup EspoCRM miscellaneous log tables
#
# Tables and retention policies (confirmed via SHOW COLUMNS 2026-05-17):
#   note                    — Lina AI timeline notes        → keep 90 days
#   auth_log_record         — authentication logs           → keep 30 days
#   action_history_record   — user action history           → keep 30 days
#   webhook_queue_item      — processed webhook items       → keep 7 days
#   email_queue_item        — processed email items         → keep 7 days
#   two_factor_code         — expired 2FA codes             → keep 1 day
#   lead_capture_log_record — lead capture logs             → keep 90 days
#   campaign_log_record     — campaign activity logs        → keep 90 days (action_date)
#   notification            — user notifications            → keep 30 days (created_at)
#
# Note: stream_subscription excluded — no date column available for filtering.
# ---------------------------------------------------------------------------
CLEANUP_ESPOCRM_MISC_CMD = (
    "docker exec ia_mariadb sh -c '"
    'mariadb -u espo-user -p"$MYSQL_PASSWORD" espocrm -e "'
    "DELETE FROM note WHERE created_at < NOW() - INTERVAL 90 DAY;"
    "DELETE FROM auth_log_record WHERE created_at < NOW() - INTERVAL 30 DAY;"
    "DELETE FROM action_history_record WHERE created_at < NOW() - INTERVAL 30 DAY;"
    "DELETE FROM webhook_queue_item WHERE created_at < NOW() - INTERVAL 7 DAY;"
    "DELETE FROM email_queue_item WHERE created_at < NOW() - INTERVAL 7 DAY;"
    "DELETE FROM two_factor_code WHERE created_at < NOW() - INTERVAL 1 DAY;"
    "DELETE FROM lead_capture_log_record WHERE created_at < NOW() - INTERVAL 90 DAY;"
    "DELETE FROM campaign_log_record WHERE action_date < NOW() - INTERVAL 90 DAY;"
    "DELETE FROM notification WHERE created_at < NOW() - INTERVAL 30 DAY;"
    "\"'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup Evolution API PostgreSQL log tables
#
# Tables and retention policies (confirmed via SHOW COLUMNS):
#   Message  — WhatsApp messages     → keep 90 days (messageTimestamp, epoch int)
#   Session  — connection sessions   → keep 30 days (createdAt)
#   Chat     — chat history          → keep 90 days (updatedAt)
#
# Note: MessageUpdate excluded — no date column available for filtering.
# ---------------------------------------------------------------------------
CLEANUP_EVOLUTION_CMD = (
    'docker exec ia_postgres psql -U evolution -d evolution -c "'
    'DELETE FROM \\"Message\\" WHERE \\"messageTimestamp\\" < '
    "EXTRACT(EPOCH FROM NOW() - INTERVAL '90 days')::bigint;"
    'DELETE FROM \\"Session\\" WHERE \\"createdAt\\" < NOW() - INTERVAL \'30 days\';'
    'DELETE FROM \\"Chat\\" WHERE \\"updatedAt\\" < NOW() - INTERVAL \'90 days\';'
    "\" && echo 'Evolution API tables cleaned up.'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup Airflow metadata DB + log files on disk
#
# Two-part cleanup:
#   1. airflow db clean — removes old DAG run metadata from the DB (keep 30 days)
#   2. find + delete   — removes log files on disk older than 30 days
#      Airflow log files are stored in /opt/airflow/logs/ (123MB confirmed 2026-05-17).
#      Without file cleanup, logs grow indefinitely even after DB cleanup.
# ---------------------------------------------------------------------------
CLEANUP_AIRFLOW_HISTORY_CMD = (
    "docker exec ia_airflow airflow db clean "
    "--clean-before-timestamp $(date -d '30 days ago' '+%Y-%m-%dT%H:%M:%S+00:00') "
    "--yes "
    "&& docker exec ia_airflow find /opt/airflow/logs -name '*.log' -mtime +30 -delete "
    "&& echo 'Airflow history and log files cleaned up.'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup Metabase log tables
#
# Tables and retention policies (confirmed via \d 2026-05-17):
#   query_execution — query logs   → keep 30 days (started_at)
#   task_history    — async tasks  → keep 30 days (started_at) — 760kB confirmed
# ---------------------------------------------------------------------------
CLEANUP_METABASE_LOGS_CMD = (
    'docker exec ia-odonto-db psql -U postgres -d metabase -c "'
    "DELETE FROM query_execution WHERE started_at < NOW() - INTERVAL '30 days';"
    "DELETE FROM task_history WHERE started_at < NOW() - INTERVAL '30 days';"
    "\" && echo 'Metabase logs cleaned up.'"
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
    "docker exec ia_airflow truncate -s 0 /app/logs/silver_run.log 2>/dev/null || true && "
    "docker exec ia-odonto-api truncate -s 0 "
    "/app/data_lake/silver/ia_odonto_silver/logs/dbt.log 2>/dev/null || true && "
    "echo 'Application logs truncated.'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup old n8n backups (keep last 7 days)
#
# n8n workflow data is backed up daily via crontab to /opt/ia-odonto-lab/backups/n8n/.
# Without cleanup, backups grow indefinitely (~7MB/day = ~2.5GB/year).
# ---------------------------------------------------------------------------
CLEANUP_N8N_BACKUPS_CMD = (
    "find /opt/ia-odonto-lab/backups/n8n -name '*.tar.gz' -mtime +7 -delete 2>/dev/null || true && "
    "echo 'Old n8n backups cleaned up.'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup dangling Docker images, stopped containers, unused networks
#
# docker system prune -f removes only dangling resources — it never removes
# images in use by running containers. Safe to run daily.
# ---------------------------------------------------------------------------
CLEANUP_DOCKER_SYSTEM_CMD = "docker system prune -f && " "echo 'Docker system pruned.'"

# ---------------------------------------------------------------------------
# Task: Cleanup Bronze Parquet partitions older than 90 days
#
# export_bronze.py runs daily at 02:00 and writes partitioned Parquet files:
#   /opt/ia-odonto-lab/data_lake/bronze/{table}/dt=YYYY-MM-DD/data.parquet
#
# Retention policy: keep last 90 days (3 months of raw data).
# Silver and Gold layers already consolidate this data, so older Bronze
# partitions are redundant. Without cleanup, partitions accumulate indefinitely.
#
# Uses -mtime +90 on directories named dt=* to target only date partitions.
# The || true ensures the task never fails if no partitions are found.
# ---------------------------------------------------------------------------
CLEANUP_PARQUET_CMD = (
    f"find {BRONZE_PATH} -type d -name 'dt=*' -mtime +90 "
    "-exec rm -rf {} + 2>/dev/null || true && "
    f"echo 'Bronze Parquet partitions older than 90 days cleaned up.' && "
    f"find {BRONZE_PATH} -type d -name 'dt=*' | wc -l | "
    "xargs -I{} echo 'Remaining partitions: {}'"
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

    t_cleanup_espo_misc = BashOperator(
        task_id="cleanup_espocrm_misc_logs",
        bash_command=CLEANUP_ESPOCRM_MISC_CMD,
        execution_timeout=timedelta(minutes=10),
        on_failure_callback=_on_failure_callback,
    )

    t_cleanup_evolution = BashOperator(
        task_id="cleanup_evolution_logs",
        bash_command=CLEANUP_EVOLUTION_CMD,
        execution_timeout=timedelta(minutes=10),
        on_failure_callback=_on_failure_callback,
    )

    t_cleanup_airflow = BashOperator(
        task_id="cleanup_airflow_history",
        bash_command=CLEANUP_AIRFLOW_HISTORY_CMD,
        execution_timeout=timedelta(minutes=15),
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

    t_cleanup_docker = BashOperator(
        task_id="cleanup_docker_system",
        bash_command=CLEANUP_DOCKER_SYSTEM_CMD,
        execution_timeout=timedelta(minutes=5),
        on_failure_callback=_on_failure_callback,
    )

    t_cleanup_parquet = BashOperator(
        task_id="cleanup_bronze_parquet",
        bash_command=CLEANUP_PARQUET_CMD,
        execution_timeout=timedelta(minutes=5),
        on_failure_callback=_on_failure_callback,
    )

    # Backup chain: MariaDB + Postgres (parallel) → Infra → Cleanup old backups
    [t_mariadb, t_postgres] >> t_infra >> t_cleanup

    # All cleanup tasks run independently and in parallel
    t_cleanup_espo
    t_cleanup_espo_misc
    t_cleanup_evolution
    t_cleanup_airflow
    t_cleanup_metabase
    t_cleanup_rag_audit
    t_cleanup_app_logs
    t_cleanup_n8n
    t_cleanup_docker
    t_cleanup_parquet
