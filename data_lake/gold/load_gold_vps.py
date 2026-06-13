"""
data_lake/gold/load_gold_vps.py
IA Odonto Lab — Silver (DuckDB) -> Gold Layer (PostgreSQL VPS)

Reads Silver models from silver.duckdb and loads the Star Schema
defined in gold_schema_vps.sql into ia-odonto-db on the VPS.

Sprint 2 (2026-05-28):
  - dim_patients extended with 9 Sprint 1 CRM fields
  - dim_opportunities added (new table, requires migration first)
  - load_contacts() reads Sprint 1 fields from fct_pipeline
  - load_dim_opportunities() reads stg_opportunities

Sprint 2 (2026-06-13):
  - load_procedimentos() added
  - load_fact_procedimentos() added
  - All NA checks use pd.isna() consistently (no ambiguous bool on NA)

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
    Joins fct_pipeline (analytical + Sprint 1 CRM fields) with stg_contacts (identity).
    Sprint 2: includes etapa_funil, status_risco, ltv_crm, dias_ultima_interacao,
              origem_lead, intencao_principal, procedimento_interesse,
              ctwa_clid, anuncio_origem.
    """
    return read_silver(
        """
        SELECT
            p.contato_hash              AS patient_key,
            c.nome,
            c.telefone,
            c.bairro,
            c.cidade,
            c.endereco_entrega,
            p.status_atendimento,
            p.pipeline_segment,
            p.created_at                AS first_contact,
            p.ultima_visita             AS last_contact,
            p.qtd_consultas             AS total_visits,
            -- Sprint 1 CRM fields
            p.etapa_funil,
            p.status_risco,
            CAST(p.ltv_crm AS DOUBLE)   AS ltv_crm,
            p.dias_ultima_interacao,
            p.origem_lead,
            p.intencao_principal,
            p.procedimento_interesse,
            p.ctwa_clid,
            p.anuncio_origem
        FROM main_marts.fct_pipeline p
        LEFT JOIN main_staging.stg_contacts c
            ON p.contato_hash = c.contato_hash
        """
    )


def load_opportunities() -> pd.DataFrame:
    """Reads stg_opportunities from Silver DuckDB."""
    return read_silver(
        """
        SELECT
            opportunity_hash,
            contato_hash,
            nome_oportunidade,
            stage,
            last_stage,
            CAST(valor AS DOUBLE)            AS valor,
            moeda,
            probabilidade,
            origem_lead,
            data_fechamento_prevista,
            procedimento,
            capi_enviado,
            capi_enviado_em,
            CAST(valor_realizado AS DOUBLE)  AS valor_realizado,
            valor_realizado_moeda,
            capi_evento_tipo,
            ctwa_clid,
            origem_lead_custom,
            created_at,
            modified_at
        FROM main_staging.stg_opportunities
        """
    )


def load_procedimentos() -> pd.DataFrame:
    """Reads stg_procedimentos from Silver DuckDB."""
    return read_silver(
        """
        SELECT
            opportunity_hash,
            procedimento_hash,
            procedimento,
            categoria,
            observacao,
            created_at,
            modified_at
        FROM main_staging.stg_procedimentos
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
    """
    Loads dim_patients with Sprint 1 CRM fields.
    Requires migration 003_sprint2_gold_schema.sql to have been run.
    All NA checks use pd.isna() to avoid ambiguous boolean on pandas NA.
    """
    df = df.copy()

    for col in ["first_contact", "last_contact"]:
        df[col] = df[col].where(df[col].notna(), other=None)
    df["total_visits"] = df["total_visits"].fillna(0).astype(int)

    for col in ["nome", "telefone", "bairro", "cidade", "endereco_entrega"]:
        df[col] = df[col].fillna("") if col in df.columns else ""

    for col in [
        "etapa_funil",
        "status_risco",
        "origem_lead",
        "intencao_principal",
        "procedimento_interesse",
        "ctwa_clid",
        "anuncio_origem",
        "ltv_crm",
        "dias_ultima_interacao",
    ]:
        if col not in df.columns:
            df[col] = None

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
            None if pd.isna(r.etapa_funil) else r.etapa_funil,
            None if pd.isna(r.status_risco) else r.status_risco,
            None if pd.isna(r.ltv_crm) else float(r.ltv_crm),
            None if pd.isna(r.dias_ultima_interacao) else int(r.dias_ultima_interacao),
            None if pd.isna(r.origem_lead) else r.origem_lead,
            None if pd.isna(r.intencao_principal) else r.intencao_principal,
            None if pd.isna(r.procedimento_interesse) else r.procedimento_interesse,
            None if pd.isna(r.ctwa_clid) else r.ctwa_clid,
            None if pd.isna(r.anuncio_origem) else r.anuncio_origem,
        )
        for r in df.itertuples(index=False)
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO gold.dim_patients
                (patient_key, nome, telefone, bairro, cidade, endereco_entrega,
                 status_atendimento, pipeline_segment, first_contact, last_contact,
                 total_visits,
                 etapa_funil, status_risco, ltv_crm, dias_ultima_interacao,
                 origem_lead, intencao_principal, procedimento_interesse,
                 ctwa_clid, anuncio_origem)
            VALUES %s
            ON CONFLICT (patient_key) DO UPDATE SET
                nome                   = EXCLUDED.nome,
                telefone               = EXCLUDED.telefone,
                bairro                 = EXCLUDED.bairro,
                cidade                 = EXCLUDED.cidade,
                endereco_entrega       = EXCLUDED.endereco_entrega,
                status_atendimento     = EXCLUDED.status_atendimento,
                pipeline_segment       = EXCLUDED.pipeline_segment,
                last_contact           = EXCLUDED.last_contact,
                total_visits           = EXCLUDED.total_visits,
                etapa_funil            = EXCLUDED.etapa_funil,
                status_risco           = EXCLUDED.status_risco,
                ltv_crm                = EXCLUDED.ltv_crm,
                dias_ultima_interacao  = EXCLUDED.dias_ultima_interacao,
                origem_lead            = EXCLUDED.origem_lead,
                intencao_principal     = EXCLUDED.intencao_principal,
                procedimento_interesse = EXCLUDED.procedimento_interesse,
                ctwa_clid              = EXCLUDED.ctwa_clid,
                anuncio_origem         = EXCLUDED.anuncio_origem
            """,
            rows,
        )
    conn.commit()
    log.info("dim_patients: %d rows upserted", len(rows))


def load_dim_opportunities(conn, df: pd.DataFrame) -> None:
    """
    Loads dim_opportunities Gold table.
    All NA checks use pd.isna() to avoid ambiguous boolean on pandas NA.
    """
    if df.empty:
        log.info("dim_opportunities: no rows to load (opportunity table empty)")
        return

    df = df.copy()
    for col in ["data_fechamento_prevista", "capi_enviado_em"]:
        df[col] = df[col].where(df[col].notna(), other=None)
    for col in ["valor", "valor_realizado"]:
        df[col] = df[col].fillna(0.0)
    df["probabilidade"] = df["probabilidade"].fillna(0).astype(int)
    df["capi_enviado"] = df["capi_enviado"].fillna(False).astype(bool)

    rows = [
        (
            r.opportunity_hash,
            None if pd.isna(r.contato_hash) else r.contato_hash,
            None if pd.isna(r.nome_oportunidade) else r.nome_oportunidade,
            None if pd.isna(r.stage) else r.stage,
            None if pd.isna(r.last_stage) else r.last_stage,
            float(r.valor),
            None if pd.isna(r.moeda) else r.moeda,
            int(r.probabilidade),
            None if pd.isna(r.origem_lead) else r.origem_lead,
            None if pd.isna(r.data_fechamento_prevista) else r.data_fechamento_prevista,
            None if pd.isna(r.procedimento) else r.procedimento,
            bool(r.capi_enviado),
            None if pd.isna(r.capi_enviado_em) else r.capi_enviado_em,
            float(r.valor_realizado),
            None if pd.isna(r.valor_realizado_moeda) else r.valor_realizado_moeda,
            None if pd.isna(r.capi_evento_tipo) else r.capi_evento_tipo,
            None if pd.isna(r.ctwa_clid) else r.ctwa_clid,
            None if pd.isna(r.origem_lead_custom) else r.origem_lead_custom,
            None if pd.isna(r.created_at) else r.created_at,
            None if pd.isna(r.modified_at) else r.modified_at,
        )
        for r in df.itertuples(index=False)
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO gold.dim_opportunities
                (opportunity_key, patient_key, nome_oportunidade, stage, last_stage,
                 valor, moeda, probabilidade, origem_lead, data_fechamento_prevista,
                 procedimento, capi_enviado, capi_enviado_em, valor_realizado,
                 valor_realizado_moeda, capi_evento_tipo, ctwa_clid, origem_lead_custom,
                 created_at, modified_at)
            VALUES %s
            ON CONFLICT (opportunity_key) DO UPDATE SET
                patient_key              = EXCLUDED.patient_key,
                nome_oportunidade        = EXCLUDED.nome_oportunidade,
                stage                    = EXCLUDED.stage,
                last_stage               = EXCLUDED.last_stage,
                valor                    = EXCLUDED.valor,
                moeda                    = EXCLUDED.moeda,
                probabilidade            = EXCLUDED.probabilidade,
                origem_lead              = EXCLUDED.origem_lead,
                data_fechamento_prevista = EXCLUDED.data_fechamento_prevista,
                procedimento             = EXCLUDED.procedimento,
                capi_enviado             = EXCLUDED.capi_enviado,
                capi_enviado_em          = EXCLUDED.capi_enviado_em,
                valor_realizado          = EXCLUDED.valor_realizado,
                valor_realizado_moeda    = EXCLUDED.valor_realizado_moeda,
                capi_evento_tipo         = EXCLUDED.capi_evento_tipo,
                ctwa_clid                = EXCLUDED.ctwa_clid,
                origem_lead_custom       = EXCLUDED.origem_lead_custom,
                modified_at              = EXCLUDED.modified_at
            """,
            rows,
        )
    conn.commit()
    log.info("dim_opportunities: %d rows upserted", len(rows))


def load_fact_procedimentos(conn, df: pd.DataFrame) -> None:
    """
    Loads fact_procedimentos Gold table.
    Requires migration 004_create_fact_procedimentos.sql to have been run.
    opportunity_key joins to dim_opportunities.opportunity_key.
    """
    if df.empty:
        log.info("fact_procedimentos: no rows to load")
        return

    df = df.copy()
    df["observacao"] = df["observacao"].fillna("")

    rows = [
        (
            r.opportunity_hash,
            r.procedimento_hash,
            None if pd.isna(r.procedimento) else r.procedimento,
            None if pd.isna(r.categoria) else r.categoria,
            r.observacao,
            None if pd.isna(r.created_at) else r.created_at,
            None if pd.isna(r.modified_at) else r.modified_at,
        )
        for r in df.itertuples(index=False)
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO gold.fact_procedimentos
                (opportunity_key, procedimento_key, procedimento, categoria,
                 observacao, created_at, modified_at)
            VALUES %s
            ON CONFLICT (opportunity_key, procedimento_key) DO UPDATE SET
                procedimento = EXCLUDED.procedimento,
                categoria    = EXCLUDED.categoria,
                observacao   = EXCLUDED.observacao,
                modified_at  = EXCLUDED.modified_at
            """,
            rows,
        )
    conn.commit()
    log.info("fact_procedimentos: %d rows upserted", len(rows))


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
    opportunities_df = load_opportunities()
    procedimentos_df = load_procedimentos()
    ltv_df = load_ltv()
    pipeline_df = load_pipeline()
    dim_date_df = build_dim_date()

    log.info(
        "Silver rows — contacts:%d opportunities:%d procedimentos:%d ltv:%d pipeline:%d dim_date:%d",
        len(contacts_df),
        len(opportunities_df),
        len(procedimentos_df),
        len(ltv_df),
        len(pipeline_df),
        len(dim_date_df),
    )

    conn = get_pg_conn()
    try:
        load_dim_date(conn, dim_date_df)
        load_dim_patients(conn, contacts_df)
        load_dim_opportunities(conn, opportunities_df)
        load_fact_procedimentos(conn, procedimentos_df)
        load_fact_interactions(conn, ltv_df, pipeline_df)
    finally:
        conn.close()

    log.info("=== Gold Layer Load (VPS) — Complete ===")


if __name__ == "__main__":
    main()
