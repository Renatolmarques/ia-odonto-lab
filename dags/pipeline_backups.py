"""
DAG: pipeline_backups
Schedule: daily at 06:00 UTC (03:00 Recife, UTC-3)
Purpose: Disaster-recovery backups for all stateful services.

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

On failure: sends alert email to operator.

Note: n8n workflow backup (daily 00h) runs inside n8n itself and is NOT
managed here — it has no dependency on this DAG.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.utils.email import send_email

# ---------------------------------------------------------------------------
# Default arguments
# ---------------------------------------------------------------------------
DEFAULT_ARGS = {
    "owner": "renato",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "email": [os.environ.get("AIRFLOW_ALERT_EMAIL", "renato_marques_17@hotmail.com")],
    "email_on_failure": True,
    "email_on_retry": False,
}

# Backup destination — writable by Airflow user (uid 50000)
BACKUP_PATH = "/opt/ia-odonto-lab/backups"


# ---------------------------------------------------------------------------
# Failure callback
# ---------------------------------------------------------------------------
def _on_failure_callback(context):
    dag_id = context["dag"].dag_id
    task_id = context["task_instance"].task_id
    execution_date = context["execution_date"]
    log_url = context["task_instance"].log_url

    subject = f"[IA Odonto] BACKUP FAILED — {dag_id} / {task_id}"
    body = (
        f"Backup task '{task_id}' failed in DAG '{dag_id}'.\n\n"
        f"Execution date: {execution_date}\n"
        f"Log: {log_url}\n\n"
        "Manual backup recommended. Check Airflow UI for details."
    )
    send_email(
        to=DEFAULT_ARGS["email"],
        subject=subject,
        html_content=f"<pre>{body}</pre>",
    )


# ---------------------------------------------------------------------------
# Task: MariaDB backup (EspoCRM — 438MB, allow up to 30 min)
#
# Uses espo-user (MYSQL_PASSWORD) instead of root.
# Root auth fails with special chars (#) in .my.cnf config files.
# espo-user has full access to espocrm and handles the password correctly.
#
# CRITICAL: redirect (>) must be OUTSIDE sh -c to write to VPS filesystem.
# Inside sh -c it writes to container filesystem and hangs indefinitely.
# ---------------------------------------------------------------------------
MARIADB_BACKUP_CMD = (
    "docker exec ia_mariadb sh -c "
    "'mariadb-dump -u espo-user -p\"$MYSQL_PASSWORD\" espocrm' "
    f"> {BACKUP_PATH}/mariadb_espocrm_$(date +%Y%m%d).sql"
)

# ---------------------------------------------------------------------------
# Task: PostgreSQL backup (Lina — pgvector embeddings + RAG audit)
# postgres user requires no password inside the container.
# Same redirect pattern: > outside sh -c writes to VPS filesystem.
# ---------------------------------------------------------------------------
POSTGRES_BACKUP_CMD = (
    "docker exec ia-odonto-db pg_dump -U postgres ia_odonto "
    f"> {BACKUP_PATH}/postgres_lina_$(date +%Y%m%d).sql"
)

# ---------------------------------------------------------------------------
# Task: Infrastructure backup (docker-compose files → GitHub via n8n webhook)
# Sends both docker-compose files base64-encoded to the n8n backup webhook,
# which commits them to the private sistema-odonto-crm GitHub repository.
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
# Prevents unbounded disk growth on the backup directory.
# ---------------------------------------------------------------------------
CLEANUP_CMD = (
    f"find {BACKUP_PATH} -name '*.sql' -mtime +7 -delete && "
    "echo 'Old SQL backups cleaned up.'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup EspoCRM internal job logs (keep last 1 day)
#
# EspoCRM accumulates internal job execution logs indefinitely by default.
# In 2 months of testing this generated 1.17M rows and 831MB of waste.
# Tables affected:
#   job                      — job queue entries (Process Webhook Queue, etc.)
#   scheduled_job_log_record — scheduled job execution history
#
# Retention: 1 day is sufficient for operational debugging.
# After OPTIMIZE TABLE, size dropped from 831MB to 28MB (96% reduction).
# This task prevents the problem from recurring automatically.
# ---------------------------------------------------------------------------
CLEANUP_ESPOCRM_JOBS_CMD = (
    "docker exec ia_mariadb sh -c '"
    'mariadb -u espo-user -p"$MYSQL_PASSWORD" espocrm -e "'
    'DELETE FROM job WHERE status = \\"Success\\" AND executed_at < NOW() - INTERVAL 1 DAY;'
    "DELETE FROM scheduled_job_log_record WHERE created_at < NOW() - INTERVAL 1 DAY;"
    "\"'"
)

# ---------------------------------------------------------------------------
# Task: Cleanup Airflow metadata database (keep last 30 days)
#
# Airflow accumulates DAG run history, task instance logs, and XCom entries
# indefinitely in the PostgreSQL backend. Without cleanup, this grows
# silently and can cause performance degradation over time.
#
# Uses: airflow db clean --clean-before-timestamp (built-in Airflow command)
# Retention: 30 days is sufficient for operational debugging and auditing.
# ---------------------------------------------------------------------------
CLEANUP_AIRFLOW_HISTORY_CMD = (
    "docker exec ia_airflow airflow db clean "
    "--clean-before-timestamp $(date -d '30 days ago' '+%Y-%m-%dT%H:%M:%S+00:00') "
    "--yes "
    "&& echo 'Airflow history cleaned up.'"
)

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="pipeline_backups",
    description="Daily disaster-recovery backups: MariaDB, PostgreSQL, docker-compose → GitHub",
    schedule_interval="0 6 * * *",  # 06:00 UTC = 03:00 Recife (UTC-3)
    start_date=datetime(2026, 5, 3),
    catchup=False,
    default_args=DEFAULT_ARGS,
    on_failure_callback=_on_failure_callback,
    tags=["backup", "production", "infrastructure"],
) as dag:

    # MariaDB dump — up to 30 min (438MB confirmed in production)
    t_mariadb = BashOperator(
        task_id="backup_mariadb",
        bash_command=MARIADB_BACKUP_CMD,
        execution_timeout=timedelta(minutes=30),
        on_failure_callback=_on_failure_callback,
    )

    # PostgreSQL dump — independent, runs in parallel with mariadb
    t_postgres = BashOperator(
        task_id="backup_postgres",
        bash_command=POSTGRES_BACKUP_CMD,
        execution_timeout=timedelta(minutes=15),
        on_failure_callback=_on_failure_callback,
    )

    # Infrastructure backup — runs after both DB backups succeed
    t_infra = BashOperator(
        task_id="backup_infra_github",
        bash_command=INFRA_BACKUP_CMD,
        execution_timeout=timedelta(minutes=5),
        on_failure_callback=_on_failure_callback,
    )

    # Cleanup old SQL backup files — runs last
    t_cleanup = BashOperator(
        task_id="cleanup_old_backups",
        bash_command=CLEANUP_CMD,
        on_failure_callback=_on_failure_callback,
    )

    # Cleanup EspoCRM internal job logs — runs in parallel, independent of backups
    t_cleanup_espo = BashOperator(
        task_id="cleanup_espocrm_job_logs",
        bash_command=CLEANUP_ESPOCRM_JOBS_CMD,
        execution_timeout=timedelta(minutes=10),
        on_failure_callback=_on_failure_callback,
    )

    # Cleanup Airflow metadata DB — runs in parallel, independent of backups
    t_cleanup_airflow = BashOperator(
        task_id="cleanup_airflow_history",
        bash_command=CLEANUP_AIRFLOW_HISTORY_CMD,
        execution_timeout=timedelta(minutes=10),
        on_failure_callback=_on_failure_callback,
    )

    # Dependency graph:
    # mariadb ──┐
    #           ├──► infra ──► cleanup
    # postgres ──┘
    # cleanup_espo     (independent, parallel)
    # cleanup_airflow  (independent, parallel)
    [t_mariadb, t_postgres] >> t_infra >> t_cleanup
    t_cleanup_espo
    t_cleanup_airflow
