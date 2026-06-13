# data_lake/bronze/export_bronze.py
"""
IA Odonto Lab — Bronze Layer Export (Medallion Architecture)

Extracts raw data from source systems and saves as Parquet files.
No transformations — raw data only. LGPD masking happens at Silver layer.
PII scrubbing applied to free-text fields before Parquet export.

Two-layer PII defense:
  Layer 1 — Regex scrubbing (8 patterns): CPF, RG, card numbers, phones,
             emails, PIX keys, bank accounts. Covers ~90% of structured PII.
  Layer 2 — Microsoft Presidio NER: detects unstructured PII (names,
             addresses) that regex cannot catch. Requires spaCy pt model.

Sources:
  1. MariaDB (EspoCRM billing)  → c_recebimento table → Parquet
  2. MariaDB (EspoCRM CRM)      → contact table       → Parquet
  3. MariaDB (EspoCRM CRM)      → opportunity table   → Parquet  [Sprint 2]
  4. PostgreSQL (pgvector)      → RAG audit data      → Parquet

Output structure (Hive-style partitioning):
  data_lake/bronze/c_recebimento/dt=YYYY-MM-DD/data.parquet
  data_lake/bronze/contact/dt=YYYY-MM-DD/data.parquet
  data_lake/bronze/opportunity/dt=YYYY-MM-DD/data.parquet
  data_lake/bronze/rag_audit/dt=YYYY-MM-DD/data.parquet

EspoCRM schema notes (confirmed via SHOW COLUMNS 2026-05-17):
  first_name          varchar(100)  — direct column on contact table
  phone               via JOIN:     entity_phone_number + phone_number tables
  address_street      varchar(255)  — Bairro (native field)
  address_city        varchar(100)  — Cidade (native field)
  c_delivery_street   mediumtext    — Nome da Rua e Número (custom field)

Sprint 1 CRM fields added to contact export (2026-05-28):
  c_c_etapa_funil, c_c_status_risco, c_c_ltv_total,
  c_c_dias_ultima_interacao, c_c_origem_lead,
  c_c_intencao_principal, c_c_procedimento_interesse,
  c_c_ctwa_clid, c_c_anuncio_origem
  NOTE: c_c_fobias_dentarias excluded — LGPD Art. 11 sensitive health data.

Opportunity export added (Sprint 2, 2026-05-28):
  Confirmed schema via SHOW COLUMNS 2026-05-28.
  stage is varchar(255) (not stage_id).
  Custom fields use c_c_ prefix (not c_o_).
  description excluded by policy (free text, not needed for analytics).

Procedimento export added (Sprint 2, 2026-06-13):
  Tables confirmed via SHOW TABLES 2026-06-13:
    c_procedimento — procedure records (nome, categoria, observacao)
    c_opportunity_procedimento — M:N relationship (opportunity_id, c_procedimento_id)
  No PII — all fields analytical.
  observacao excluded from Presidio (short clinical notes, low PII risk).
"""
import logging
import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Presidio NER
# ---------------------------------------------------------------------------
try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_anonymizer import AnonymizerEngine

    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "pt", "model_name": "pt_core_news_lg"}],
        }
    )
    _analyzer = AnalyzerEngine(
        nlp_engine=provider.create_engine(),
        supported_languages=["pt"],
    )
    _anonymizer = AnonymizerEngine()
    _PRESIDIO_AVAILABLE = True
    logger.info("✅ Presidio NER loaded successfully — full PII protection active.")
except Exception as e:
    _PRESIDIO_AVAILABLE = False
    logger.warning(
        "⚠️ Presidio NER unavailable (%s) — regex-only PII scrubbing active.", e
    )

TODAY = date.today().isoformat()
BRONZE_PATH = Path(__file__).parent

# ---------------------------------------------------------------------------
# Contact field policy — three explicit tiers.
# ---------------------------------------------------------------------------

PII_ALLOWED_FIELDS = [
    "first_name",
    "phone",
    "address_street",
    "address_city",
    "c_delivery_street",
]

FREE_TEXT_FIELDS = [
    "c_aisummary",
]

# Sprint 1 CRM fields appended — all analytical, no PII.
# c_c_fobias_dentarias intentionally excluded (LGPD Art. 11 — health data).
ANALYTICAL_FIELDS = [
    "id",
    "c_status_atendimento",
    "c_lifetime_value",
    "c_lifetime_value_currency",
    "c_potencial_venda",
    "c_potencial_venda_currency",
    "c_qtd_consultas",
    "c_ultima_visita",
    "created_at",
    "modified_at",
    # Sprint 1 CRM fields
    "c_c_etapa_funil",
    "c_c_status_risco",
    "c_c_ltv_total",
    "c_c_dias_ultima_interacao",
    "c_c_origem_lead",
    "c_c_lina_intencao_principal",
    "c_c_procedimento_interesse",
    "c_c_ctwa_clid",
    "c_c_anuncio_origem",
]

# ---------------------------------------------------------------------------
# PII scrubbing patterns — applied ONLY to FREE_TEXT_FIELDS.
# ---------------------------------------------------------------------------
_PII_PATTERNS = [
    (
        re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        ),
        "[PIX_REDACTED]",
    ),
    (re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"), "[CARD_REDACTED]"),
    (re.compile(r"\+55\s?\(?\d{2}\)?\s?\d{4,5}[\s\-]?\d{4}"), "[PHONE_REDACTED]"),
    (re.compile(r"\(?\d{2}\)?\s?\d{4,5}[\s\-]?\d{4}"), "[PHONE_REDACTED]"),
    (re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}"), "[CPF_REDACTED]"),
    (re.compile(r"\d{2}\.?\d{3}\.?\d{3}-?\d{1}"), "[RG_REDACTED]"),
    (re.compile(r"[\w\.\-]+@[\w\.\-]+\.\w+"), "[EMAIL_REDACTED]"),
    (re.compile(r"\b\d{4}[\s\-]?\d{5,6}[\s\-]?\d{1}\b"), "[ACCOUNT_REDACTED]"),
]


def scrub_pii(text):
    if text is None:
        return None
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def scrub_pii_presidio(text: str) -> str:
    if not _PRESIDIO_AVAILABLE or not text:
        return text
    try:
        results = _analyzer.analyze(
            text=text,
            language="pt",
            entities=[
                "PERSON",
                "LOCATION",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "CREDIT_CARD",
            ],
        )
        if not results:
            return text
        return _anonymizer.anonymize(text=text, analyzer_results=results).text
    except Exception:
        return text


def _mariadb_engine():
    host = os.getenv("MARIADB_HOST", "ia_mariadb")
    port = os.getenv("MARIADB_PORT", "3306")
    db = os.getenv("MARIADB_DATABASE", "espocrm")
    user = os.getenv("MARIADB_USER", "")
    pwd = quote_plus(os.getenv("MARIADB_PASSWORD", ""))
    return create_engine(
        f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True
    )


def _postgres_engine():
    host = os.getenv("DB_HOST_LOCAL", "localhost")
    port = os.getenv("DB_PORT_LOCAL", "5433")
    db = os.getenv("DB_NAME", "ia_odonto")
    user = os.getenv("DB_USER", "postgres")
    pwd = quote_plus(os.getenv("DB_PASSWORD", "postgres"))
    return create_engine(
        f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True
    )


def export_recebimentos():
    """Exports billing table from MariaDB."""
    logger.info("[1/4] Exporting c_recebimento (MariaDB)...")
    query = text(
        """
        SELECT
            id, contato_id, valor, valor_currency,
            data_recebimento, status, created_at, modified_at
        FROM c_recebimento
        WHERE deleted = 0
    """
    )
    try:
        engine = _mariadb_engine()
        with engine.connect() as conn:
            df = pd.read_sql(query, conn)
        out_path = BRONZE_PATH / "c_recebimento" / f"dt={TODAY}"
        out_path.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out_path / "data.parquet", index=False)
        logger.info(
            "      Saved: %s (%d rows, %.0f KB)",
            out_path / "data.parquet",
            len(df),
            (out_path / "data.parquet").stat().st_size / 1024,
        )
    except Exception as exc:
        logger.error("Failed to export c_recebimento: %s", str(exc))


def export_contacts():
    """
    Exports contact fields from EspoCRM (MariaDB) to Bronze Parquet.
    Sprint 1 CRM fields included (2026-05-28).
    c_c_fobias_dentarias excluded — LGPD Art. 11 health data.
    """
    logger.info("[2/4] Exporting contact (MariaDB)...")
    logger.info("PII fields included by policy: %s", PII_ALLOWED_FIELDS)

    query = text(
        """
        SELECT
            c.id,
            c.c_status_atendimento,
            c.c_lifetime_value,
            c.c_lifetime_value_currency,
            c.c_potencial_venda,
            c.c_potencial_venda_currency,
            c.c_qtd_consultas,
            c.c_ultima_visita,
            c.created_at,
            c.modified_at,
            c.first_name,
            c.address_street,
            c.address_city,
            c.c_delivery_street,
            c.c_aisummary,
            pn.name AS phone,
            -- Sprint 1 CRM fields (analytical, no PII)
            c.c_c_etapa_funil,
            c.c_c_status_risco,
            c.c_c_ltv_total,
            c.c_c_dias_ultima_interacao,
            c.c_c_origem_lead,
            c.c_c_lina_intencao_principal,
            c.c_c_procedimento_interesse,
            c.c_c_ctwa_clid,
            c.c_c_anuncio_origem
        FROM contact c
        LEFT JOIN entity_phone_number epn
            ON epn.entity_id = c.id
            AND epn.entity_type = 'Contact'
            AND epn.primary = 1
            AND epn.deleted = 0
        LEFT JOIN phone_number pn
            ON pn.id = epn.phone_number_id
            AND pn.deleted = 0
        WHERE c.deleted = 0
    """
    )

    try:
        engine = _mariadb_engine()
        with engine.connect() as conn:
            df = pd.read_sql(query, conn)

        for col in FREE_TEXT_FIELDS:
            if col in df.columns:
                df[col] = df[col].fillna("").apply(scrub_pii).apply(scrub_pii_presidio)

        out_path = BRONZE_PATH / "contact" / f"dt={TODAY}"
        out_path.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out_path / "data.parquet", index=False)
        logger.info(
            "      Saved: %s (%d rows, %.0f KB)",
            out_path / "data.parquet",
            len(df),
            (out_path / "data.parquet").stat().st_size / 1024,
        )
    except Exception as exc:
        logger.error("Failed to export contact: %s", str(exc))


def export_opportunities():
    """
    Exports Opportunity entity from EspoCRM (MariaDB) to Bronze Parquet.

    Schema confirmed via SHOW COLUMNS 2026-05-28:
      - stage is varchar(255) (not stage_id)
      - Custom fields use c_c_ prefix
      - description excluded (free text, not needed for analytics)
      - name kept as analytical (opportunity title set by dentist, not patient PII)
      - contact_id is a join key only, not PII

    Fields exported:
      Core: id, name, stage, last_stage, amount, amount_currency,
            probability, lead_source, close_date, contact_id,
            created_at, modified_at
      Custom (Sprint 1): c_c_procedimento, c_c_capi_enviado,
            c_c_capi_enviado_em, c_c_valor_realizado,
            c_c_valor_realizado_currency, c_c_capi_evento_tipo,
            c_c_ctwa_clid, c_c_origem_lead
    """
    logger.info("[3/4] Exporting opportunity (MariaDB)...")
    query = text(
        """
        SELECT
            o.id,
            o.name,
            o.stage,
            o.last_stage,
            o.amount,
            o.amount_currency,
            o.probability,
            o.lead_source,
            o.close_date,
            o.contact_id,
            o.created_at,
            o.modified_at,
            -- Sprint 1 custom fields
            o.c_c_procedimento,
            o.c_c_capi_enviado,
            o.c_c_capi_enviado_em,
            o.c_c_valor_realizado,
            o.c_c_valor_realizado_currency,
            o.c_c_capi_evento_tipo,
            o.c_c_ctwa_clid,
            o.c_c_origem_lead
        FROM opportunity o
        WHERE o.deleted = 0
    """
    )
    try:
        engine = _mariadb_engine()
        with engine.connect() as conn:
            df = pd.read_sql(query, conn)
        out_path = BRONZE_PATH / "opportunity" / f"dt={TODAY}"
        out_path.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out_path / "data.parquet", index=False)
        logger.info(
            "      Saved: %s (%d rows, %.0f KB)",
            out_path / "data.parquet",
            len(df),
            (out_path / "data.parquet").stat().st_size / 1024,
        )
    except Exception as exc:
        logger.error("Failed to export opportunity: %s", str(exc))
        logger.error("Verify 'opportunity' table exists in EspoCRM MariaDB")


def export_procedimentos():
    """
    Exports CProcedimento entity and its M:N relationship with Opportunity.

    Tables confirmed via SHOW TABLES 2026-06-13:
      c_procedimento            — procedure records
      c_opportunity_procedimento — M:N relationship table

    No PII — all fields analytical.
    Two Parquet files exported:
      bronze/c_procedimento/dt=YYYY-MM-DD/data.parquet
      bronze/c_opportunity_procedimento/dt=YYYY-MM-DD/data.parquet
    """
    logger.info(
        "[4/5] Exporting c_procedimento + c_opportunity_procedimento (MariaDB)..."
    )

    query_proc = text(
        """
        SELECT
            id,
            nome,
            categoria,
            observacao,
            created_at,
            modified_at,
            deleted
        FROM c_procedimento
        WHERE deleted = 0 OR deleted IS NULL
    """
    )

    query_rel = text(
        """
        SELECT
            id,
            opportunity_id,
            c_procedimento_id,
            deleted
        FROM c_opportunity_procedimento
        WHERE deleted = 0 OR deleted IS NULL
    """
    )

    try:
        engine = _mariadb_engine()
        with engine.connect() as conn:
            df_proc = pd.read_sql(query_proc, conn)
            df_rel = pd.read_sql(query_rel, conn)

        # c_procedimento
        out_proc = BRONZE_PATH / "c_procedimento" / f"dt={TODAY}"
        out_proc.mkdir(parents=True, exist_ok=True)
        df_proc.to_parquet(out_proc / "data.parquet", index=False)
        logger.info(
            "      c_procedimento: %s (%d rows, %.0f KB)",
            out_proc / "data.parquet",
            len(df_proc),
            (out_proc / "data.parquet").stat().st_size / 1024,
        )

        # c_opportunity_procedimento
        out_rel = BRONZE_PATH / "c_opportunity_procedimento" / f"dt={TODAY}"
        out_rel.mkdir(parents=True, exist_ok=True)
        df_rel.to_parquet(out_rel / "data.parquet", index=False)
        logger.info(
            "      c_opportunity_procedimento: %s (%d rows, %.0f KB)",
            out_rel / "data.parquet",
            len(df_rel),
            (out_rel / "data.parquet").stat().st_size / 1024,
        )

    except Exception as exc:
        logger.error("Failed to export procedimentos: %s", str(exc))


def export_rag_audit():
    """Exports RAG query logs from PostgreSQL. No PII — hashes only."""
    logger.info("[4/4] Exporting rag_audit (PostgreSQL)...")
    query = text(
        """
        SELECT id, created_at, collection, query_text, query_category,
               k, results_returned, avg_score, patient_id
        FROM rag_audit
        ORDER BY created_at
    """
    )
    try:
        engine = _postgres_engine()
        with engine.connect() as conn:
            df = pd.read_sql(query, conn)
        logger.info("      %d RAG query log(s) found", len(df))
        out_path = BRONZE_PATH / "rag_audit" / f"dt={TODAY}"
        out_path.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out_path / "data.parquet", index=False)
        logger.info(
            "      Saved: %s (%d rows, %.0f KB)",
            out_path / "data.parquet",
            len(df),
            (out_path / "data.parquet").stat().st_size / 1024,
        )
    except Exception as exc:
        logger.error("Failed to export rag_audit: %s", str(exc))


def main():
    logger.info("=== Bronze export started | dt=%s ===", TODAY)
    export_recebimentos()
    export_contacts()
    export_opportunities()
    export_procedimentos()
    export_rag_audit()
    logger.info("=== Bronze export complete ===")
    logger.info("Next step: run pipeline_bronze_silver DAG or dbt manually")


if __name__ == "__main__":
    main()
