# init_db.py
"""
IA Odonto Lab — Database Initialization

Activates the pgvector extension in PostgreSQL.
Run once after docker-compose up -d.

Usage:
  python init_db.py
"""
import logging
import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    host = os.getenv("DB_HOST_LOCAL", "localhost")
    port = os.getenv("DB_PORT_LOCAL", "5433")
    name = os.getenv("DB_NAME", "ia_odonto")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")

    conn_str = f"host={host} port={port} dbname={name} user={user} password={password}"

    try:
        with psycopg.connect(conn_str) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            conn.commit()
            logger.info("✅ pgvector extension activated")
            logger.info("Next step: python app/tools/ingest_knowledge.py")
    except Exception as exc:
        logger.error("Failed to initialize database: %s", str(exc))
        logger.error("Is Docker running? Try: docker-compose up -d")
        raise


if __name__ == "__main__":
    main()
