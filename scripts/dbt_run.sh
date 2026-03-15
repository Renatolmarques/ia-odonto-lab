#!/bin/bash
# Load .env and run dbt
set -a
source /Users/amarante/Documents/Pessoal/ia-odonto-lab2/.env
set +a
cd /Users/amarante/Documents/Pessoal/ia-odonto-lab2/data_lake/silver/ia_odonto_silver
dbt "$@"