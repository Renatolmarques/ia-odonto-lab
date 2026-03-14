# data_lake/bronze/export_bronze.py
"""
IA Odonto Lab — Bronze Layer Export (Medallion Architecture)

Extracts raw data from source systems and saves as Parquet files.
No transformations — raw data only. LGPD masking happens at the Silver layer.

Sources:
  1. MariaDB (EspoCRM billing) → c_recebimento table → Parquet
  2. PostgreSQL (pgvector)     → RAG audit data     → Parquet

Output structure (Hive-style partitioning):
  data_lake/bronze/c_recebimento/dt=YYYY-MM-DD/data.parquet
  data_lake/bronze/rag_audit/dt=YYYY-MM-DD/data.parquet

Usage:
  python data_lake/bronze/export_bronze.py

Prerequisites:
  - MARIADB_HOST, MARIADB_USER, MARIADB_PASSWORD in .env
  - pip install pymysql pyarrow
"""
import logging
import os
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus

load_dotenv()
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

TODAY = date.today().isoformat()
BRONZE_PATH = Path(__file__).parent


def _mariadb_engine():
    """Builds SQLAlchemy engine for MariaDB (EspoCRM billing database)."""
    from urllib.parse import quote_plus
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
    Uses contato_id (not contact_id or contacto_id — those are legacy columns).
    Always filters deleted=0 for soft-deleted rows.
    """
    logger.info("[1/2] Exporting c_recebimento (MariaDB)...")
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


def export_rag_audit():
    """
    Exports RAG collection metadata from PostgreSQL for audit purposes.
    Contains only institutional knowledge records — no patient data.
    """
    logger.info("[2/2] Exporting RAG audit (PostgreSQL)...")
    query = text("SELECT uuid, name, cmetadata FROM langchain_pg_collection")
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
    export_rag_audit()
    logger.info("=== Bronze export complete ===")
    logger.info("Next step: python data_lake/silver/silver_transform.py")


if __name__ == "__main__":
    main()
