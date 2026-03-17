# data_lake/bronze/export_bronze.py
"""
IA Odonto Lab — Bronze Layer Export (Medallion Architecture)

Extracts raw data from source systems and saves as Parquet files.
No transformations — raw data only. LGPD masking happens at Silver layer.
PII scrubbing applied to free-text fields before Parquet export.

Sources:
  1. MariaDB (EspoCRM billing) → c_recebimento table → Parquet
  2. MariaDB (EspoCRM CRM)     → contact table       → Parquet
  3. PostgreSQL (pgvector)     → RAG audit data      → Parquet

Output structure (Hive-style partitioning):
  data_lake/bronze/c_recebimento/dt=YYYY-MM-DD/data.parquet
  data_lake/bronze/contact/dt=YYYY-MM-DD/data.parquet
  data_lake/bronze/rag_audit/dt=YYYY-MM-DD/data.parquet

Usage:
  python data_lake/bronze/export_bronze.py

Prerequisites:
  - MARIADB_HOST, MARIADB_USER, MARIADB_PASSWORD in .env
  - pip install pymysql pyarrow
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

TODAY = date.today().isoformat()
BRONZE_PATH = Path(__file__).parent

# ---------------------------------------------------------------------------
# PII scrubbing patterns — applied to free-text fields before Parquet export
# Defense-in-depth: LLM prompt guardrail is first; this regex layer is second
# Column names kept in Portuguese to match EspoCRM DDL
# ---------------------------------------------------------------------------
_PII_PATTERNS = [
    # PIX key (UUID) — must come BEFORE CPF to avoid partial digit capture
    (
        re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        ),
        "[PIX_REDACTED]",
    ),
    # Credit/debit card: 16 digits — must come BEFORE CPF
    (
        re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
        "[CARD_REDACTED]",
    ),
    # Brazilian phone international: +5511999998888 — specific, before CPF
    (
        re.compile(r"\+55\s?\(?\d{2}\)?\s?\d{4,5}[\s\-]?\d{4}"),
        "[PHONE_REDACTED]",
    ),
    # Brazilian phone local: (11) 99999-8888 or 11 99999-8888
    (
        re.compile(r"\(?\d{2}\)?\s?\d{4,5}[\s\-]?\d{4}"),
        "[PHONE_REDACTED]",
    ),
    # Brazilian CPF: 123.456.789-00 or 12345678900 — after longer patterns
    (re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}"), "[CPF_REDACTED]"),
    # Brazilian RG: 12.345.678-9 — after CPF
    (re.compile(r"\d{2}\.?\d{3}\.?\d{3}-?\d{1}"), "[RG_REDACTED]"),
    # Email address
    (re.compile(r"[\w\.\-]+@[\w\.\-]+\.\w+"), "[EMAIL_REDACTED]"),
    # Brazilian bank account: agency + account
    (re.compile(r"\b\d{4}[\s\-]?\d{5,6}[\s\-]?\d{1}\b"), "[ACCOUNT_REDACTED]"),
]


def scrub_pii(text):
    """
    Removes PII patterns from AI-generated free-text fields before Bronze export.

    Applies regex patterns for CPF, card numbers, phones, emails, RG, PIX keys,
    and bank account numbers. Returns None if input is None.

    This is a defense-in-depth layer — the LLM prompt guardrail in n8n is first.
    Regex covers ~90% of structured PII patterns; unstructured PII (names,
    addresses written in full prose) requires NER models (future sprint).
    """
    if text is None:
        return None
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _mariadb_engine():
    """Builds SQLAlchemy engine for MariaDB (EspoCRM database)."""
    host = os.getenv("MARIADB_HOST", "ia_mariadb")
    port = os.getenv("MARIADB_PORT", "3306")
    db = os.getenv("MARIADB_DATABASE", "espocrm")
    user = os.getenv("MARIADB_USER", "")
    pwd = quote_plus(os.getenv("MARIADB_PASSWORD", ""))
    url = f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{db}"
    return create_engine(url, pool_pre_ping=True)


def _postgres_engine():
    """Builds SQLAlchemy engine for PostgreSQL (pgvector / lab2 database)."""
    host = os.getenv("DB_HOST_LOCAL", "localhost")
    port = os.getenv("DB_PORT_LOCAL", "5433")
    db = os.getenv("DB_NAME", "ia_odonto")
    user = os.getenv("DB_USER", "postgres")
    pwd = os.getenv("DB_PASSWORD", "postgres")
    url = f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{db}"
    return create_engine(url, pool_pre_ping=True)


def export_recebimentos():
    """
    Exports the billing table from MariaDB.
    Uses contato_id (not contact_id — legacy column).
    Always filters deleted=0 for soft-deleted rows.
    """
    logger.info("[1/3] Exporting c_recebimento (MariaDB)...")
    query = text(
        """
        SELECT
            id,
            contato_id,
            valor,
            valor_currency,
            data_recebimento,
            status,
            created_at,
            modified_at
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
    Exports contact analytical fields from EspoCRM (MariaDB).

    Privacy: NO PII exported (no first_name, last_name, address,
    description, phone or email). Only behavioral/analytical fields.

    Fields selected (DDL-validated column names and types):
      - id                         varchar(17)   → contact key
      - c_status_atendimento       varchar(100)  → care status
      - c_lifetime_value           decimal(13,4) → numeric LTV
      - c_lifetime_value_currency  varchar(3)    → currency code
      - c_potencial_venda          decimal(13,4) → numeric sales potential
      - c_potencial_venda_currency varchar(3)    → currency code
      - c_qtd_consultas            int(11)       → visit count
      - c_ultima_visita            date          → last visit date
      - c_aisummary                mediumtext    → AI clinical summary
      - created_at                 datetime      → record creation
      - modified_at                datetime      → last modification

    NOTE: Display fields (c_display_ltv, c_display_potencial,
    c_display_visitas, c_c_kanban_card) intentionally excluded —
    formatted strings for UI only, not analytical data.
    Always filters deleted=0 for soft-deleted rows.
    """
    logger.info("[2/3] Exporting contact (MariaDB)...")
    query = text(
        """
        SELECT
            id,
            c_status_atendimento,
            c_lifetime_value,
            c_lifetime_value_currency,
            c_potencial_venda,
            c_potencial_venda_currency,
            c_qtd_consultas,
            c_ultima_visita,
            c_aisummary,
            created_at,
            modified_at
        FROM contact
        WHERE deleted = 0
        """
    )
    try:
        engine = _mariadb_engine()
        with engine.connect() as conn:
            df = pd.read_sql(query, conn)
        # Scrub PII from AI-generated free-text field before saving to Parquet
        df["c_aisummary"] = df["c_aisummary"].apply(scrub_pii)
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


def export_rag_audit():
    """
    Exports RAG collection metadata from PostgreSQL for audit purposes.
    Contains only institutional knowledge records — no patient data.
    """
    logger.info("[3/3] Exporting RAG audit (PostgreSQL)...")
    query = text("SELECT uuid::text, name, cmetadata FROM langchain_pg_collection")
    try:
        engine = _postgres_engine()
        with engine.connect() as conn:
            df = pd.read_sql(query, conn)
        logger.info("      %d collection(s) found", len(df))
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
        logger.error("Failed to export RAG audit: %s", str(exc))


def main():
    logger.info("=== Bronze export started | dt=%s ===", TODAY)
    export_recebimentos()
    export_contacts()
    export_rag_audit()
    logger.info("=== Bronze export complete ===")
    logger.info("Next step: python data_lake/silver/silver_transform.py")


if __name__ == "__main__":
    main()
