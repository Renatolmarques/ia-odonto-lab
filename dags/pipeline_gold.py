"""
DAG: pipeline_gold
Schedule: every 2 hours, 5 minutes after pipeline_bronze_silver completes
Purpose: Loads Silver (DuckDB) into the Gold Layer (PostgreSQL Star Schema).

Pipeline stages:
  1. load_gold — Runs load_gold_vps.py inside ia-odonto-api container.
                 Reads fct_ltv, fct_pipeline from silver.duckdb and upserts
                 into gold.dim_patients, gold.dim_date, gold.fact_interactions.

On failure: sends alert via n8n webhook → Gmail.
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
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
    "email_on_retry": False,
}


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
# Task — Load Gold
# ---------------------------------------------------------------------------
LOAD_GOLD_CMD = (
    "docker exec ia-odonto-api " "python /app/data_lake/gold/load_gold_vps.py"
)

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="pipeline_gold",
    description="Silver (DuckDB) → Gold Layer PostgreSQL Star Schema (every 2h)",
    schedule_interval="5 8-23 * * *",
    start_date=datetime(2026, 5, 14),
    catchup=False,
    default_args=DEFAULT_ARGS,
    on_failure_callback=_on_failure_callback,
    tags=["gold", "star-schema", "production"],
) as dag:

    t_load_gold = BashOperator(
        task_id="load_gold",
        bash_command=LOAD_GOLD_CMD,
        on_failure_callback=_on_failure_callback,
    )
