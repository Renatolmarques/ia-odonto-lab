"""
data_lake/gold/load_gold.py
IA Odonto Lab — Silver -> Snowflake Gold Layer loader

Reads Silver models directly from silver.duckdb (produced by dbt run)
and upserts into the Snowflake Star Schema defined in gold_schema.sql.

Usage:
    python data_lake/gold/load_gold.py

Requirements (in requirements.txt):
    snowflake-connector-python==3.6.0
    pandas
    pyarrow
    duckdb

Environment variables required (.env):
    SNOWFLAKE_ACCOUNT
    SNOWFLAKE_USER
    SNOWFLAKE_PASSWORD
    SNOWFLAKE_DATABASE=IA_ODONTO_DW
    SNOWFLAKE_SCHEMA=GOLD
    SNOWFLAKE_WAREHOUSE=COMPUTE_WH
    BRONZE_PATH
    DBT_SALT
"""

import logging
import os
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BRONZE_PATH = os.environ["BRONZE_PATH"]
SILVER_DB = Path(BRONZE_PATH).parent / "silver" / "silver.duckdb"

SF_ACCOUNT = os.environ["SNOWFLAKE_ACCOUNT"]
SF_USER = os.environ["SNOWFLAKE_USER"]
SF_PASSWORD = os.environ["SNOWFLAKE_PASSWORD"]
SF_DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "IA_ODONTO_DW")
SF_SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "GOLD")
SF_WAREHOUSE = os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")


def read_silver(query):
    log.info("Reading from silver.duckdb: %s", SILVER_DB)
    con = duckdb.connect(str(SILVER_DB), read_only=True)
    try:
        df = con.execute(query).df()
    finally:
        con.close()
    return df


def load_contacts():
    return read_silver(
        """
        SELECT
            contato_hash               AS PATIENT_KEY,
            status_atendimento         AS STATUS,
            CAST(created_at AS VARCHAR)    AS CREATED_DATE,
            CAST(modified_at AS VARCHAR)   AS UPDATED_DATE,
           potencial_venda            AS POTENCIAL_VENDA,
            lifetime_value             AS LIFETIME_VALUE,
            qtd_consultas              AS QTD_CONSULTAS
        FROM main_staging.stg_contacts
    """
    )


def load_ltv():
    return read_silver(
        """
        SELECT
            contato_hash                              AS PATIENT_KEY,
            CAST(ltv_acumulado AS DOUBLE)             AS LTV_ACUMULADO,
            CAST(frequencia_visitas AS INTEGER)       AS FREQUENCIA_VISITAS,
            CAST(dias_desde_ultima_visita AS INTEGER) AS DIAS_DESDE_ULTIMA_VISITA,
            CAST(ticket_medio AS DOUBLE)              AS TICKET_MEDIO
        FROM main_marts.fct_ltv
    """
    )


def load_pipeline():
    return read_silver(
        """
        SELECT
            contato_hash                              AS PATIENT_KEY,
            status_atendimento                        AS STATUS,
            pipeline_segment                          AS PIPELINE_SEGMENT,
            CAST(total_pago_recebimentos AS DOUBLE)   AS LTV_REALIZADO,
            CAST(potencial_venda AS DOUBLE)           AS POTENCIAL_VENDA,
            CAST(qtd_consultas AS INTEGER)            AS QTD_CONSULTAS,
            CAST(dias_desde_ultima_visita AS INTEGER) AS DIAS_DESDE_ULTIMA_VISITA
        FROM main_marts.fct_pipeline
    """
    )


def load_ai_performance():
    return read_silver(
        """
        SELECT
            summary_quality                           AS SUMMARY_QUALITY,
            CAST(total_contacts AS INTEGER)           AS TOTAL_CONTACTS,
            CAST(total_ltv AS DOUBLE)                 AS TOTAL_LTV,
            CAST(avg_ltv AS DOUBLE)                   AS AVG_LTV,
            CAST(total_potencial AS DOUBLE)           AS TOTAL_POTENCIAL,
            CAST(coverage_pct AS DOUBLE)              AS COVERAGE_PCT
        FROM main_marts.fct_ai_performance
    """
    )


def build_dim_date(start, years=3):
    rows = []
    end = date(start.year + years, 1, 1)
    d = start
    while d < end:
        rows.append(
            {
                "DATE_KEY": str(d),
                "YEAR": d.year,
                "QUARTER": (d.month - 1) // 3 + 1,
                "MONTH": d.month,
                "DAY_OF_WEEK": d.isoweekday(),
                "IS_WEEKEND": d.isoweekday() >= 6,
            }
        )
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def get_sf_connection():
    log.info(
        "Connecting to Snowflake account=%s db=%s schema=%s",
        SF_ACCOUNT,
        SF_DATABASE,
        SF_SCHEMA,
    )
    return snowflake.connector.connect(
        account=SF_ACCOUNT,
        user=SF_USER,
        password=SF_PASSWORD,
        database=SF_DATABASE,
        schema=SF_SCHEMA,
        warehouse=SF_WAREHOUSE,
    )


def upsert_table(conn, df, table, key_col):
    if df.empty:
        log.warning("DataFrame for %s is empty — skipping.", table)
        return
    cur = conn.cursor()
    cur.execute(f"TRUNCATE TABLE IF EXISTS {table}")
    cur.close()
    success, nchunks, nrows, _ = write_pandas(conn, df, table)
    if success:
        log.info("Loaded %d rows into %s (%d chunks)", nrows, table, nchunks)
    else:
        raise RuntimeError(f"write_pandas failed for table {table}")


def main():
    log.info("=== IA Odonto Gold Layer Load — Start ===")
    log.info("Silver DB path: %s", SILVER_DB)

    if not SILVER_DB.exists():
        raise FileNotFoundError(
            f"silver.duckdb not found at {SILVER_DB}. "
            "Run dbt run inside data_lake/silver/ia_odonto_silver first."
        )

    # --- DIM_DATE ---
    dim_date_df = build_dim_date(date(2024, 1, 1))

    # --- DIM_PATIENTS: from stg_ntacts ---
    contacts_raw = load_contacts()
    dim_patients = pd.DataFrame(
        {
            "PATIENT_KEY": contacts_raw["PATIENT_KEY"],
            "INTENT_SEGMENT": None,
            "FEAR_SEGMENT": None,
            "FIRST_CONTACT": contacts_raw["CREATED_DATE"],
            "LAST_CONTACT": contacts_raw["UPDATED_DATE"],
            "TOTAL_VISITS": contacts_raw["QTD_CONSULTAS"].fillna(0).astype(int),
        }
    )

    # --- FACT_INTERACTIONS: from fct_ltv joined with stg_contacts ---
    ltv_raw = load_ltv()
    fact_interactions = pd.DataFrame(
        {
            "PATIENT_KEY": ltv_raw["PATIENT_KEY"],
            "SERVICE_KEY": None,
            "DATE_KEY": pd.Timestamp.now().date(),
            "ESTIMATED_POTENTIAL": 0.0,
            "AI_INTENT": None,
            "VISIT_COUNT": ltv_raw["FREQUENCIA_VISITAS"].fillna(0).astype(int),
            "VALOR_PAGO": ltv_raw["TICKET_MEDIO"].fillna(0),
            "LTV_ACUMULADO": ltv_raw["LTV_ACUMULADO"].fillna(0),
        }
    )

    log.info(
        "Rows — dim_date:%dim_patients:%d fact_interactions:%d",
        len(dim_date_df),
        len(dim_patients),
        len(fact_interactions),
    )

    conn = get_sf_connection()
    try:
        upsert_table(conn, dim_date_df, "DIM_DATE", "DATE_KEY")
        upsert_table(conn, dim_patients, "DIM_PATIENTS", "PATIENT_KEY")
        upsert_table(conn, fact_interactions, "FACT_INTERACTIONS", "PATIENT_KEY")
    finally:
        conn.close()

    log.info("=== Gold Layer Load — Complete ===")


if __name__ == "__main__":
    main()
