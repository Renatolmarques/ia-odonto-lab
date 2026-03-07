# app/tools/ingest_knowledge.py
"""
IA Odonto Lab — Knowledge Base Ingestion Pipeline

Loads the clinic knowledge base (Markdown), splits it into chunks,
generates embeddings via OpenAI, and stores vectors in pgvector.

Run this script whenever clinica_knowledge.md is updated.

Usage:
  python app/tools/ingest_knowledge.py

Prerequisites:
  - Docker running (docker-compose up -d)
  - pgvector initialized (python init_db.py)
  - OPENAI_API_KEY set in .env
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_community.document_loaders import UnstructuredMarkdownLoader
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

KNOWLEDGE_BASE_PATH = Path(__file__).parent.parent / "context" / "clinica_knowledge.md"
COLLECTION_NAME = "clinica_docs"
EMBEDDING_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _get_connection_string() -> str:
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST_LOCAL", "localhost")
    port = os.getenv("DB_PORT_LOCAL", "5433")
    name = os.getenv("DB_NAME", "ia_odonto")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


def main():
    logger.info("=== Knowledge base ingestion started ===")

    logger.info("[1/4] Loading: %s", KNOWLEDGE_BASE_PATH)
    loader = UnstructuredMarkdownLoader(str(KNOWLEDGE_BASE_PATH))
    documents = loader.load()
    logger.info("      %d document(s) loaded", len(documents))

    logger.info(
        "[2/4] Splitting into chunks (size=%d, overlap=%d)...",
        CHUNK_SIZE,
        CHUNK_OVERLAP,
    )
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    chunks = splitter.split_documents(documents)
    logger.info("      %d chunk(s) generated", len(chunks))

    logger.info("[3/4] Initializing embedding model: '%s'...", EMBEDDING_MODEL)
    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)
    logger.info("      Model ready")

    logger.info("[4/4] Saving vectors to collection '%s'...", COLLECTION_NAME)
    PGVector.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=COLLECTION_NAME,
        connection=_get_connection_string(),
        pre_delete_collection=True,
    )
    logger.info("      ✅ %d chunk(s) indexed successfully!", len(chunks))
    logger.info("=== Ingestion complete ===")
    logger.info("Next step: test with python test_rag_integration.py")


if __name__ == "__main__":
    main()
