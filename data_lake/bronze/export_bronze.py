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
  1. MariaDB (EspoCRM billing) → c_recebimento table   → Parquet
  2. MariaDB (EspoCRM CRM)     → contact table         → Parquet
  3. MariaDB (EspoCRM CRM)     → opportunity table     → Parquet  [Sprint 2]
  4. PostgreSQL (pgvector)     → RAG audit data        → Parquet

Output structure (Hive-style partitioning):
  data_lake/bronze/c_recebimento/dt=YYYY-MM-DD/data.parquet
  data_lake/bronze/contact/dt=YYYY-MM-DD/data.parquet
  data_lake/bronze/opportunity/dt=YYYY-MM-DD/data.parquet
  data_lake/bronze/rag_audit/dt=YYYY-MM-DD/data.parquet

Usage:
  python data_lake/bronze/export_bronze.py

Prerequisites:
  - MARIADB_HOST, MARIADB_USER, MARIADB_PASSWORD in .env
  - pip install pymysql pyarrow presidio-analyzer presidio-anonymizer spacy
  - python -m spacy download pt_core_news_lg

EspoCRM schema notes (confirmed via SHOW COLUMNS 2026-05-17):
  first_name          varchar(100)  — direct column on contact table
  phone               via JOIN:     entity_phone_number + phone_number tables
                                    (EspoCRM stores phones in separate M:N tables)
  address_street      varchar(255)  — Bairro (native field)
  address_city        varchar(100)  — Cidade (native field)
  c_delivery_street   mediumtext    — Nome da Rua e Número (custom field)

EspoCRM custom field naming convention (confirmed 2026-05-27):
  EspoCRM doubles the 'c' prefix — a field created as 'cOrigemLead' becomes
  'cCOrigemLead' in the UI and 'c_c_origem_lead' in MariaDB snake_case.
  All Sprint 1 custom fields follow this c_c_ prefix pattern in the DB.

LGPD field policy for Sprint 1 fields:
  c_c_fobias_dentarias is health data (LGPD Art. 11) — NOT exported.
  All other Sprint 1 fields are analytical (no PII) — exported to Gold.
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
# Presidio NER — loaded once per process to avoid repeated model loading cost.
# Uses pt_core_news_lg (Portuguese) — installed in Dockerfile via pip whl.
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
#
# Sprint 1 added 10 new custom fields to EspoCRM Contact. Nine are exported
# here (all analytical). The tenth — c_c_fobias_dentarias — is health data
# under LGPD Art. 11 and intentionally excluded from Bronze/Silver/Gold.
#
# To add a new field:
#   - Liberated PII: add to PII_ALLOWED_FIELDS + SQL query
#   - AI free text:  add to FREE_TEXT_FIELDS + SQL query
#   - Analytical:    add to ANALYTICAL_FIELDS + SQL query
# ---------------------------------------------------------------------------

# Tier 1 — PII intentionally exposed to BI (LGPD: legítimo interesse clínico).
# NOT scrubbed — must reach Gold intact.
# Note: phone comes from JOIN with entity_phone_number + phone_number tables.
PII_ALLOWED_FIELDS = [
    "first_name",  # varchar(100) — direct column on contact
    "phone",  # via JOIN entity_phone_number + phone_number
    "address_street",  # varchar(255) — Bairro
    "address_city",  # varchar(100) — Cidade
    "c_delivery_street",  # mediumtext   — Nome da Rua e Número
]

# Tier 2 — AI-generated free text. Scrubbed: regex (L1) + Presidio NER (L2).
FREE_TEXT_FIELDS = [
    "c_aisummary",
]

# Tier 3 — Pure analytical. No scrubbing.
# Sprint 1 adds 9 new fields (c_c_* prefix = EspoCRM doubles the 'c').
# c_c_fobias_dentarias intentionally excluded — LGPD Art. 11 health data.
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
    # Sprint 1 fields — analytical, no PII
    "c_c_etapa_funil",
    "c_c_status_risco",
    "c_c_ltv_total",
    "c_c_dias_ultima_interacao",
    "c_c_origem_lead",
    "c_c_intencao_principal",
    "c_c_procedimento_interesse",
    "c_c_ctwa_clid",
    "c_c_anuncio_origem",
]

# ---------------------------------------------------------------------------
# Opportunity field policy — all analytical, no PII.
# contact_id is a foreign key (join key), not PII.
# c_o_* are custom fields added in Sprint 1 to the Opportunity entity.
# ---------------------------------------------------------------------------
OPPORTUNITY_FIELDS = [
    "id",
    "name",
    "stage",
    "amount",
    "amount_currency",
    "probability",
    "close_date",
    "contact_id",  # FK to contact — used for join in Silver/Gold
    "created_at",
    "modified_at",
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
    """Layer 1 — Regex scrubbing. Only applied to FREE_TEXT_FIELDS."""
    if text is None:
        return None
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def scrub_pii_presidio(text: str) -> str:
    """Layer 2 — Presidio NER. Only applied to FREE_TEXT_FIELDS."""
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
        logger.error("Check MARIADB_HOST, MARIADB_USER, MARIADB_PASSWORD in .env")


def export_contacts():
    """
    Exports contact fields from EspoCRM (MariaDB) to Bronze Parquet.

    Privacy policy — three tiers:
      Tier 1 — PII_ALLOWED_FIELDS: exposed to BI (LGPD legítimo interesse).
        phone requires JOIN with entity_phone_number + phone_number tables
        because EspoCRM stores phones in a separate M:N relationship.
        epn.primary=1 ensures only the primary phone is returned.
      Tier 2 — FREE_TEXT_FIELDS (c_aisummary): regex + Presidio NER scrubbing.
      Tier 3 — ANALYTICAL_FIELDS: no scrubbing needed.

    Sprint 1 fields exported (9 of 10):
      c_c_etapa_funil, c_c_status_risco, c_c_ltv_total,
      c_c_dias_ultima_interacao, c_c_origem_lead, c_c_intencao_principal,
      c_c_procedimento_interesse, c_c_ctwa_clid, c_c_anuncio_origem.
      EXCLUDED: c_c_fobias_dentarias (LGPD Art. 11 — health data).
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
            c.c_c_etapa_funil,
            c.c_c_status_risco,
            c.c_c_ltv_total,
            c.c_c_dias_ultima_interacao,
            c.c_c_origem_lead,
            c.c_c_intencao_principal,
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

        # Tier 2 only — PII_ALLOWED_FIELDS intentionally skipped
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
        logger.error("Check MARIADB_HOST, MARIADB_USER, MARIADB_PASSWORD in .env")


def export_opportunities():
    """
    Exports opportunity data from EspoCRM (MariaDB) to Bronze Parquet.

    All fields are analytical — no PII. contact_id is a foreign key used
    for joining with dim_patients in Silver/Gold; it is not PII itself.

    In the Gold layer, this becomes dim_opportunities. Each Opportunity
    represents one treatment plan (Sprint 2 architectural decision: no
    Consulta entity — each treatment = 1 Opportunity, sessions = Stream notes).

    EspoCRM Opportunity table is named 'opportunity' in MariaDB.
    """
    logger.info("[3/4] Exporting opportunity (MariaDB)...")
    query = text(
        """
        SELECT
            id,
            name,
            stage,
            amount,
            amount_currency,
            probability,
            close_date,
            contact_id,
            created_at,
            modified_at
        FROM opportunity
        WHERE deleted = 0
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
        logger.error("Check MARIADB_HOST, MARIADB_USER, MARIADB_PASSWORD in .env")


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
        logger.error("Run migration first: app/db/migrations/001_create_rag_audit.sql")


def main():
    logger.info("=== Bronze export started | dt=%s ===", TODAY)
    export_recebimentos()
    export_contacts()
    export_opportunities()
    export_rag_audit()
    logger.info("=== Bronze export complete ===")
    logger.info("Next step: run pipeline_bronze_silver DAG or dbt manually")


if __name__ == "__main__":
    main()
