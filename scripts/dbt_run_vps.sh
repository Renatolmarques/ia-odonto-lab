#!/bin/bash
# scripts/dbt_run_vps.sh - Entrypoint for the ia-odonto-dbt container on VPS
# IA Odonto Lab | Sprint 8 - Silver Layer on VPS
#
# Validates required environment variables, then delegates all arguments to dbt.
# Logs are written to /app/logs/silver_run.log (mounted as a persistent volume).
#
# Required environment variables (injected via --env-file .env):
#   BRONZE_PATH - absolute path to the Bronze layer inside the container
#                 e.g. /app/data_lake/bronze
#   DBT_SALT    - secret salt for SHA-256 contact anonymization (LGPD compliance)
#
# Usage:
#   docker run --rm --env-file .env ia-odonto-dbt run
#   docker run --rm --env-file .env ia-odonto-dbt test

set -euo pipefail

LOG_FILE="/app/logs/silver_run.log"
mkdir -p /app/logs

echo "========================================" | tee -a "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting dbt $*" | tee -a "$LOG_FILE"
echo "BRONZE_PATH: ${BRONZE_PATH:-not set}" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

if [ -z "${BRONZE_PATH:-}" ]; then
    echo "[ERROR] BRONZE_PATH is not set. Aborting." | tee -a "$LOG_FILE"
    exit 1
fi

if [ -z "${DBT_SALT:-}" ]; then
    echo "[ERROR] DBT_SALT is not set. Aborting." | tee -a "$LOG_FILE"
    exit 1
fi

cd /app/data_lake/silver/ia_odonto_silver

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running: dbt $*" | tee -a "$LOG_FILE"
dbt "$@" --profiles-dir /app/data_lake/silver/ia_odonto_silver 2>&1 | tee -a "$LOG_FILE"

EXIT_CODE=${PIPESTATUS[0]}

echo "[$(date '+%Y-%m-%d %H:%M:%S')] dbt finished with exit code: $EXIT_CODE" | tee -a "$LOG_FILE"
exit $EXIT_CODE
