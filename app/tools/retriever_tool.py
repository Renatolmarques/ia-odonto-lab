# app/tools/retriever_tool.py
"""
IA Odonto Lab — RAG Retriever Tool

Performs similarity search against the clinic knowledge base stored in pgvector.
Automatically detects whether running inside Docker or locally and adjusts
the connection string accordingly — no manual configuration switch needed.

Each query is logged to the rag_audit table for observability and analytics.
Audit writes are buffered in memory and flushed every 30s in a background thread
— never blocking the main query flow.

LGPD: This tool queries ONLY institutional knowledge (FAQs, services, pricing).
      Patient data never enters the vector database.
      query_text is stored as sha256 hash — never in plain text.
"""
import hashlib
import logging
import os
import socket
import threading
import time
from collections import deque
from urllib.parse import quote_plus

import psycopg2
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector

load_dotenv()
logger = logging.getLogger(__name__)

COLLECTION_NAME = "clinica_docs"
EMBEDDING_MODEL = "text-embedding-3-small"
AUDIT_FLUSH_INTERVAL = 30  # seconds

# ── Audit buffer ──────────────────────────────────────────────────────────────
_audit_buffer: deque = deque()
_audit_lock = threading.Lock()


def _start_audit_flush_thread() -> None:
    """Starts the background thread that flushes the audit buffer periodically."""

    def _flush_loop():
        while True:
            time.sleep(AUDIT_FLUSH_INTERVAL)
            _flush_audit_buffer()

    t = threading.Thread(target=_flush_loop, daemon=True, name="rag-audit-flusher")
    t.start()
    logger.info("rag_audit flush thread started (interval: %ds)", AUDIT_FLUSH_INTERVAL)


def _flush_audit_buffer() -> None:
    """
    Drains the in-memory buffer and writes all pending rows to rag_audit in a
    single INSERT. Fails silently — a flush error never affects the API.
    """
    with _audit_lock:
        if not _audit_buffer:
            return
        rows = list(_audit_buffer)
        _audit_buffer.clear()

    try:
        conn = _get_psycopg2_conn()
        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO rag_audit
                        (collection, query_text, k, results_returned, avg_score, patient_id, query_category)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
        conn.close()
        logger.debug("rag_audit flushed %d row(s)", len(rows))
    except Exception as exc:
        logger.warning("rag_audit flush failed (non-critical): %s", str(exc))


# Start flush thread when module is loaded
_start_audit_flush_thread()

# ── Connection helpers ────────────────────────────────────────────────────────


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
    password = quote_plus(os.getenv("DB_PASSWORD", "postgres"))
    name = os.getenv("DB_NAME", "ia_odonto")
    if _is_running_in_docker():
        host, port = os.getenv("DB_HOST", "db"), os.getenv("DB_PORT", "5432")
    else:
        host, port = os.getenv("DB_HOST_LOCAL", "localhost"), os.getenv(
            "DB_PORT_LOCAL", "5433"
        )
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


def _get_psycopg2_conn():
    """Returns a raw psycopg2 connection for direct SQL writes (rag_audit)."""
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    name = os.getenv("DB_NAME", "ia_odonto")
    if _is_running_in_docker():
        host, port = os.getenv("DB_HOST", "db"), os.getenv("DB_PORT", "5432")
    else:
        host, port = os.getenv("DB_HOST_LOCAL", "localhost"), os.getenv(
            "DB_PORT_LOCAL", "5433"
        )
    return psycopg2.connect(
        host=host, port=port, dbname=name, user=user, password=password
    )


# ── Query classification ──────────────────────────────────────────────────────


def _classify_query(query_text: str) -> str:
    """
    Returns a semantic category for the query without storing PII.
    Extend the keyword lists as needed.
    """
    q = query_text.lower()
    if any(w in q for w in ["consulta", "agendamento", "horário", "appointment"]):
        return "scheduling"
    if any(w in q for w in ["dor", "sintoma", "sangramento", "pain", "symptom"]):
        return "clinical_symptom"
    if any(w in q for w in ["tratamento", "procedimento", "treatment", "procedure"]):
        return "treatment"
    if any(w in q for w in ["pagamento", "valor", "plano", "payment", "price"]):
        return "billing"
    if any(w in q for w in ["histórico", "history", "anterior", "previous"]):
        return "patient_history"
    return "general"


# ── Audit logging ─────────────────────────────────────────────────────────────


def _log_rag_audit(
    collection: str,
    query_text: str,
    k: int,
    results_returned: int,
    avg_score: float,
    patient_id: str | None = None,
) -> None:
    """
    Enqueues a rag_audit row in the in-memory buffer.
    Returns immediately — never blocks the main query flow.
    query_text is stored as sha256 hex — never in plain text.
    """
    query_hash = hashlib.sha256(query_text.encode()).hexdigest()
    query_category = _classify_query(query_text)
    row = (
        collection,
        query_hash,
        k,
        results_returned,
        avg_score,
        patient_id,
        query_category,
    )
    with _audit_lock:
        _audit_buffer.append(row)


# ── Main retriever ────────────────────────────────────────────────────────────


def buscar_contexto(pergunta: str, k: int = 3) -> list[dict]:
    """
    Retrieves the k most relevant knowledge base chunks for a given query.

    Args:
        pergunta: Patient message or query text.
        k: Number of results to return (default: 3).

    Returns:
        List of dicts with 'texto' (content) and 'relevancia' (0.0-1.0 score).
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
        logger.info("RAG returned %d result(s)", len(context))

        avg_score = (
            round(sum(r["relevancia"] for r in context) / len(context), 3)
            if context
            else 0.0
        )
        _log_rag_audit(
            collection=COLLECTION_NAME,
            query_text=pergunta,
            k=k,
            results_returned=len(context),
            avg_score=avg_score,
        )

        return context
    except Exception as exc:
        logger.error("RAG query failed: %s", str(exc))
        return []
