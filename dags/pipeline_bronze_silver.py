"""
DAG: pipeline_bronze_silver
Schedule: every 2 hours
Purpose: Orchestrates the full Bronze → Silver data pipeline for the dental clinic.

Pipeline stages:
  1. export_bronze        — Extracts raw data from MariaDB (EspoCRM) and PostgreSQL (Lina)
                            into partitioned Parquet files. Applies PII scrubbing (regex, 8 patterns).
  2. validate_parquet     — Checks that Parquet files exist and have expected schema.
                            Empty files are allowed (no patients on a given day) — logged as warning.
  3. dbt_run              — Runs dbt models to transform Bronze Parquet into Silver DuckDB.
                            Produces: stg_contacts, stg_recebimentos, stg_ai_summaries,
                                      fct_ltv, fct_pipeline, fct_ai_performance.
  4. dbt_test             — Runs dbt data quality tests (35 tests, 2 expected warnings).
                            Fails the DAG if any test fails beyond known warnings.
  5. rotate_parquet       — DParquet partitions older than 90 days.
                            Industry standard: Bronze is a raw snapshot; Silver consolidates the data.
                            90-day window covers auditing, emergency reprocessing, and debugging.
  6. cleanup_buffer       — Deletes processed WhatsApp messages older than 30 days from
                            message_buffer table. LGPD compliance — no retention beyond need.

On failure: sends alert email to operator.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from airflow.utils.email import send_email

# ---------------------------------------------------------------------------
# Default arguments — applied to every task unless overridden
# ---------------------------------------------------------------------------
DEFAULT_ARGS = {
    "owner": "renato",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email": [os.environ.get("AIRFLOW_ALERT_EMAIL", "renato_marques_17@hotmail.com")],
    "email_on_failure": True,
    "email_on_retry": False,
}

# ---------------------------------------------------------------------------
# Paths — resolved from environment variables set in the Airflow container
# ---------------------------------------------------------------------------
BRONZE_PATH = os.environ.get("BRONZE_PATH", "/app/data_lake/bronze")
SILVER_PATH = os.environ.get("SILVER_PATH", "/app/data_lake/silver")
DBT_PROJECT = os.environ.get(
    "DBT_PROJECT_PATH", "/app/data_lake/silver/ia_odonto_silver"
)
DBT_PROFILES = os.environ.get(
    "DBT_PROFILES_DIR", "/app/data_lake/silver/ia_odonto_silver"
)

# Tables exported to Bronze — used for validation
BRONZE_TABLES = ["contact", "c_recebimento", "rag_audit"]


# ---------------------------------------------------------------------------
# Helper — failure callback sends a formatted alert email
# ---------------------------------------------------------------------
def _on_failure_callback(context):
    """Sends a plain-text failure alert to the operator email."""
    dag_id = context["dag"].dag_id
    task_id = context["task_instance"].task_id
    execution_date = context["execution_date"]
    log_url = context["task_instance"].log_url

    subject = f"[IA Odonto] DAG FAILED — {dag_id} / {task_id}"
    body = (
        f"Task '{task_id}' in DAG '{dag_id}' failed.\n\n"
        f"Execution date: {execution_date}\n"
        f"Log: {log_url}\n\n"
        "Check Airflow UI for details."
    )
    send_email(
        to=DEFAULT_ARGS["email"],
        subject=subject,
        html_content=f"<pre>{body}</pre>",
    )


# ---------------------------------------------------------------------------
# Task 1 — Export Bronze
# Runs export_bronze.py inside the ia-odonto-api container via docker exec.
# The script connects to MariaDB + PostgreSQL, scrubs PII, and writes Parquet.
# -----------------------------------------------------------------------
EXPORT_CMD = (
    "docker exec ia-odonto-api " "python /app/data_lake/bronze/export_bronze.py"
)


# ---------------------------------------------------------------------------
# Task 2 — Validate Parquet
# Pure Python — runs inside the Airflow container.
# Checks schema and row presence. Empty files are warnings, not failures.
# ---------------------------------------------------------------------------
def validate_parquet(**context):
    """
    Validates Bronze Parquet files generated in the current partition.

    Rules:
    - File must exist for today's partition.
    - File must have the expected columns.
    - Zero rows is allowed (no activity today) — logged as WARNING, not failure.
    - Any other read error raises an exception and fails the task.
    """
    import logging
    from datetime import date
    from pathlib import Path

    import pandas as pd

    log = logging.getLogger(__name__)
    today = date.today().strftime("%Y-%m-%d")

    EXPECTED_COLUMNS = {
        "contact": ["id", "c_status_atendimento", "c_aisummary", "created_at"],
        "c_recebimento": ["id", "contato_id", "valor", "created_at"],
        "rag_audit": ["id", "created_at", "collection", "results_returned"],
    }

    for table in BRONZE_TABLES:
        parquet_path = Path(BRONZE_PATH) / table / f"dt={today}" / "data.parquet"

        if not parquet_path.exists():
            raise FileNotFoundError(
                f"Bronze file not found for table '{table}': {parquet_path}\n"
                "export_bronze.py may have failed silently."
            )

        try:
            df = pd.read_parquet(parquet_path)
        except Exception as exc:
            raise RuntimeError(f"Failed to read Parquet for '{table}': {exc}") from exc

        if df.empty:
            log.warning(
                "Bronze table '%s' has 0 rows for %s — no activity today. "
                "Pipeline continues normally.",
                table,
                today,
            )
            continue

        missing = [c for c in EXPECTED_COLUMNS.get(table, []) if c not in df.columns]
        if missing:
            raise ValueError(
                f"Bronze table '{table}' is missing expected columns: {missing}. "
                f"Actual columns: {list(df.columns)}"
            )

        log.info(
            "✅ Bronze '%s' validated — %d rows, %d columns.",
            table,
            len(df),
            len(df.columns),
        )

    log.info("✅ All Bronze tables validated for %s.", today)


# ---------------------------------------------------------------------------
# Task 3 — dbt run (Silver transformation)
# ---------------------------------------------------------------------------
DBT_RUN_CMD = (
    "docker run --rm "
    "--env-file /opt/ia-odonto-lab/.env "
    "-v /opt/ia-odonto-lab/data_lake/bronze:/app/data_lake/bronze:ro "
    "-v /opt/ia-odonto-lab/data_lake/silver:/app/data_lake/silver "
    "-v /opt/ia-odonto-lab/logs:/app/logs "
    "ia-odonto-dbt run"
)

# -------------------------------------------------------------------
# Task 4 — dbt test (Silver quality gates)
# ---------------------------------------------------------------------------
DBT_TEST_CMD = (
    "docker run --rm "
    "--env-file /opt/ia-odonto-lab/.env "
    "-v /opt/ia-odonto-lab/data_lake/bronze:/app/data_lake/bronze:ro "
    "-v /opt/ia-odonto-lab/data_lake/silver:/app/data_lake/silver "
    "-v /opt/ia-odonto-lab/logs:/app/logs "
    "ia-odonto-dbt test"
)

# ---------------------------------------------------------------------------
# Task 5 — Rotate Bronze Parquet (keep last 90 days)
#
# Industry standard for Bronze layer retention:
#   - Bronze is a raw daily snapshot — Silver consolidates the data permanently.
#   - 90 days covers auditing, emergency reprocessing, and debugging needs.
#   - Older partitions are safe to delete: Silver already contains the derived data.
#
# Runs inside ia-odonto-api container (Bronze path lives there).
# Uses find with -mtime +90 to match partitions older than 90 days.
# Delee partition directory and its contents (data.parquet + metadata).
# ---------------------------------------------------------------------------
ROTATE_PARQUET_CMD = (
    "docker exec ia-odonto-api "
    "find /app/data_lake/bronze -mindepth 2 -maxdepth 2 -type d -mtime +90 "
    r"-exec rm -rf {} + "
    "&& echo 'Bronze rotation complete — partitions older than 90 days deleted.'"
)

# ---------------------------------------------------------------------------
# Task 6 — Cleanup message_buffer
# Deletes processed WhatsApp messages older than 30 days.
# LGPD: no retention of personal data beyond operational need.
# ---------------------------------------------------------------------------
CLEANUP_CMD = (
    "docker exec ia_postgres psql -U evolution -d evolution -c "
    '"DELETE FROM message_buffer '
    "WHERE processed = TRUE "
    "AND created_at < NOW() - INTERVAL '30 days';\""
)

# ---------------------------------------------------------------------------
# DAG definition
# -----------------------------------------------------------------------
with DAG(
    dag_id="pipeline_bronze_silver",
    description="Bronze export → Parquet validation → dbt Silver → Parquet rotation (every 2h)",
    schedule_interval="0 11-23 * * *",
    start_date=datetime(2026, 5, 3),
    catchup=False,
    default_args=DEFAULT_ARGS,
    on_failure_callback=_on_failure_callback,
    tags=["bronze", "silver", "dbt", "production"],
) as dag:

    t_export = BashOperator(
        task_id="export_bronze",
        bash_command=EXPORT_CMD,
        on_failure_callback=_on_failure_callback,
    )

    t_validate = PythonOperator(
        task_id="validate_parquet",
        python_callable=validate_parquet,
        on_failure_callback=_on_failure_callback,
    )

    t_dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command=DBT_RUN_CMD,
        on_failure_callback=_on_failure_callback,
    )

    t_dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=DBT_TEST_CMD,
        on_failure_callback=_on_failure_callback,
    )

    t_rotate = BashOperator(
        task_id="rotate_parquet",
        bash_command=ROTATE_PARQUET_CMD,
        on_failure_callback=_on_failure_callback,
    )

    t_cleanup = BashOperator(
        task_id="cleanup_message_buffer",
        bash_command=CLEANUP_CMD,
        on_failure_callback=_on_failure_callback,
    )

    # Pipeline dependency chain
    t_export >> t_validate >> t_dbt_run >> t_dbt_test >> t_rotate >> t_cleanup
