"""
data_lake/gold/load_gold_vps.py
IA Odonto Lab — Silver (DuckDB) -> Gold Layer (PostgreSQL VPS)

Reads Silver models from silver.duckdb and loads the Star Schema
defined in gold_schema_vps.sql into ia-odonto-db on the VPS.

Usage (from project root):
    python data_lake/gold/load_gold_vps.py

Environment variables (.env):
    DB_USER, DB_PASSWORD, DB_NAME
    DB_HOST / DB_HOST_LOCAL
    DB_PORT / DB_PORT_LOCAL
    BRONZE_PATH
"""

import logging
import os
import socket
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BRONZE_PATH = os.environ["BRONZE_PATH"]
SILVER_DB = Path(BRONZE_PATH).parent / "silver" / "silver.duckdb"


# -- Connection ----------------------------------------------------------------


def _is_docker() -> bool:
    try:
        socket.gethostbyname("db")
        return True
    except socket.gaierror:
        return False


def get_pg_conn():
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    name = os.getenv("DB_NAME", "ia_odonto")
    if _is_docker():
        host = os.getenv("DB_HOST", "db")
        port = os.getenv("DB_PORT", "5432")
    else:
        host = os.getenv("DB_HOST_LOCAL", "127.0.0.1")
        port = os.getenv("DB_PORT_LOCAL", "5433")
    log.info("Connecting to PostgreSQL at %s:%s/%s", host, port, name)
    return psycopg2.connect(
        host=host, port=port, dbname=name, user=user, password=password
    )


# -- Silver readers ------------------------------------------------------------


def read_silver(query: str) -> pd.DataFrame:
    log.info("Reading silver.duckdb: %s", SILVER_DB)
    con = duckdb.connect(str(SILVER_DB), read_only=True)
    try:
        return con.execute(query).df()
    finally:
        con.close()


def load_contacts() -> pd.DataFrame:
    """
    Joins fct_pipeline (analytical) with stg_contacts (identity fields).
    fct_pipeline has contato_hash as key but does not carry nome/telefone/address.
    stg_contacts is the source of truth for identity fields.
    """
    return read_silver(
        """
        SELECT
            p.contato_hash          AS patient_key,
            c.nome,
            c.telefone,
            c.bairro,
            c.cidade,
            c.endereco_entrega,
            p.status_atendimento,
            p.pipeline_segment,
            p.created_at            AS first_contact,
            p.ultima_visita         AS last_contact,
            p.qtd_consultas         AS total_visits
        FROM main_marts.fct_pipeline p
        LEFT JOIN main_staging.stg_contacts c
            ON p.contato_hash = c.contato_hash
        """
    )


def load_ltv() -> pd.DataFrame:
    return read_silver(
        """
        SELECT
            contato_hash,
            CAST(ltv_acumulado            AS DOUBLE)  AS ltv_acumulado,
            CAST(ticket_medio             AS DOUBLE)  AS ticket_medio,
            CAST(maior_pagamento          AS DOUBLE)  AS maior_pagamento,
            CAST(frequencia_visitas       AS INTEGER) AS visit_count,
            CAST(dias_desde_ultima_visita AS INTEGER) AS dias_ultima_visita,
            ultima_visita
        FROM main_marts.fct_ltv
        """
    )


def load_pipeline() -> pd.DataFrame:
    return read_silver(
        """
        SELECT
            contato_hash,
            CAST(potencial_venda AS DOUBLE) AS estimated_potential
        FROM main_marts.fct_pipeline
        """
    )


# -- DIM_DATE builder ----------------------------------------------------------


def build_dim_date(start: date = date(2024, 1, 1), years: int = 3) -> pd.DataFrame:
    rows, d, end = [], start, date(start.year + years, 1, 1)
    while d < end:
        rows.append(
            {
                "date_key": d,
                "year": d.year,
                "month": d.month,
                "quarter": (d.month - 1) // 3 + 1,
                "day_of_week": d.isoweekday(),
                "is_weekend": d.isoweekday() >= 6,
            }
        )
        d += timedelta(days=1)
    return pd.DataFrame(rows)


# -- Loaders ------------------------------------------------------------------


def load_dim_date(conn, df: pd.DataFrame) -> None:
    rows = [tuple(r) for r in df.itertuples(index=False)]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO gold.dim_date (date_key, year, month, quarter, day_of_week, is_weekend)
            VALUES %s
            ON CONFLICT (date_key) DO NOTHING
            """,
            rows,
        )
    conn.commit()
    log.info("dim_date: %d rows upserted", len(rows))


def load_dim_patients(conn, df: pd.DataFrame) -> None:
    df = df.copy()

    # Convert NaT to None so psycopg2 inserts NULL instead of the string "NaT"
    for col in ["first_contact", "last_contact"]:
        df[col] = df[col].where(df[col].notna(), other=None)
    df["total_visits"] = df["total_visits"].fillna(0).astype(int)

    # Fill missing identity fields with empty string (patients with no address yet)
    for col in ["nome", "telefone", "bairro", "cidade", "endereco_entrega"]:
        df[col] = df[col].fillna("") if col in df.columns else ""

    rows = [
        (
            r.patient_key,
            r.nome,
            r.telefone,
            r.bairro,
            r.cidade,
            r.endereco_entrega,
            r.status_atendimento,
            r.pipeline_segment,
            None if pd.isna(r.first_contact) else r.first_contact,
            None if pd.isna(r.last_contact) else r.last_contact,
            r.total_visits,
        )
        for r in df.itertuples(index=False)
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO gold.dim_patients
                (patient_key, nome, telefone, bairro, cidade, endereco_entrega,
                 status_atendimento, pipeline_segment, first_contact, last_contact, total_visits)
            VALUES %s
            ON CONFLICT (patient_key) DO UPDATE SET
                nome               = EXCLUDED.nome,
                telefone           = EXCLUDED.telefone,
                bairro             = EXCLUDED.bairro,
                cidade             = EXCLUDED.cidade,
                endereco_entrega   = EXCLUDED.endereco_entrega,
                status_atendimento = EXCLUDED.status_atendimento,
                pipeline_segment   = EXCLUDED.pipeline_segment,
                last_contact       = EXCLUDED.last_contact,
                total_visits       = EXCLUDED.total_visits
            """,
            rows,
        )
    conn.commit()
    log.info("dim_patients: %d rows upserted", len(rows))


def load_fact_interactions(
    conn, ltv_df: pd.DataFrame, pipeline_df: pd.DataFrame
) -> None:
    merged = ltv_df.merge(pipeline_df, on="contato_hash", how="left")
    merged["date_key"] = merged["ultima_visita"].where(
        merged["ultima_visita"].notna(), date.today()
    )
    merged["estimated_potential"] = merged["estimated_potential"].fillna(0)
    merged["ltv_acumulado"] = merged["ltv_acumulado"].fillna(0)
    merged["ticket_medio"] = merged["ticket_medio"].fillna(0)
    merged["maior_pagamento"] = merged["maior_pagamento"].fillna(0)
    merged["visit_count"] = merged["visit_count"].fillna(0).astype(int)
    merged["dias_ultima_visita"] = merged["dias_ultima_visita"].fillna(0).astype(int)

    rows = [
        (
            r.contato_hash,
            None,
            r.date_key,
            float(r.estimated_potential),
            float(r.ltv_acumulado),
            float(r.ticket_medio),
            float(r.maior_pagamento),
            int(r.visit_count),
            int(r.dias_ultima_visita),
        )
        for r in merged.itertuples(index=False)
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO gold.fact_interactions
                (patient_key, service_key, date_key, estimated_potential,
                 ltv_acumulado, ticket_medio, maior_pagamento, visit_count, dias_ultima_visita)
            VALUES %s
            """,
            rows,
        )
    conn.commit()
    log.info("fact_interactions: %d rows inserted", len(rows))


# -- Main ---------------------------------------------------------------------


def main():
    log.info("=== IA Odonto Gold Layer Load (VPS) — Start ===")

    if not SILVER_DB.exists():
        raise FileNotFoundError(
            f"silver.duckdb not found at {SILVER_DB}. "
            "Run: cd data_lake/silver/ia_odonto_silver && dbt run"
        )

    contacts_df = load_contacts()
    ltv_df = load_ltv()
    pipeline_df = load_pipeline()
    dim_date_df = build_dim_date()

    log.info(
        "Silver rows — contacts:%d ltv:%d pipeline:%d dim_date:%d",
        len(contacts_df),
        len(ltv_df),
        len(pipeline_df),
        len(dim_date_df),
    )

    conn = get_pg_conn()
    try:
        load_dim_date(conn, dim_date_df)
        load_dim_patients(conn, contacts_df)
        load_fact_interactions(conn, ltv_df, pipeline_df)
    finally:
        conn.close()

    log.info("=== Gold Layer Load (VPS) — Complete ===")


if __name__ == "__main__":
    main()
