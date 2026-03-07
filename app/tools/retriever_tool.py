# app/tools/retriever_tool.py
"""
IA Odonto Lab — RAG Retriever Tool

Performs similarity search against the clinic knowledge base stored in pgvector.
Automatically detects whether running inside Docker or locally and adjusts
the connection string accordingly — no manual configuration switch needed.

LGPD: This tool queries ONLY institutional knowledge (FAQs, services, pricing).
      Patient data never enters the vector database.
"""
import logging
import os
import socket

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector

load_dotenv()
logger = logging.getLogger(__name__)

COLLECTION_NAME = "clinica_docs"
EMBEDDING_MODEL = "text-embedding-3-small"


def _is_running_in_docker() -> bool:
    """Detects Docker environment by resolving the 'db' service hostname."""
    try:
        socket.gethostbyname("db")
        return True
    except socket.gaierror:
        return False


def _get_connection_string() -> str:
    """Builds the correct connection string for the current environment."""
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    name = os.getenv("DB_NAME", "ia_odonto")
    if _is_running_in_docker():
        host, port = os.getenv("DB_HOST", "db"), os.getenv("DB_PORT", "5432")
    else:
        host, port = os.getenv("DB_HOST_LOCAL", "localhost"), os.getenv(
            "DB_PORT_LOCAL", "5433"
        )
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


def buscar_contexto(pergunta: str, k: int = 3) -> list[dict]:
    """
    Retrieves the k most relevant knowledge base chunks for a given query.

    Args:
        pergunta: Patient message or query text.
        k: Number of results to return (default: 3).

    Returns:
        List of dicts with 'texto' (content) and 'relevancia' (0.0–1.0 score).
        Returns empty list on any error — never propagates exceptions to the agent.
    """
    logger.info("RAG query: %s", pergunta[:80])
    try:
        vectorstore = PGVector(
            embeddings=OpenAIEmbeddings(model=EMBEDDING_MODEL),
            collection_name=COLLECTION_NAME,
            connection=_get_connection_string(),
        )
        results = vectorstore.similarity_search_with_score(pergunta, k=k)
        context = [
            {"texto": doc.page_content, "relevancia": round(1 - score, 2)}
            for doc, score in results
        ]
        logger.info("📚 RAG returned %d result(s)", len(context))
        return context
    except Exception as exc:
        logger.error("RAG query failed: %s", str(exc))
        return []
