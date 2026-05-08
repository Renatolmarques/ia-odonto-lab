# app/tools/episodic_memory.py
"""
IA Odonto Lab — Episodic Memory

Saves and retrieves per-patient conversation history using pgvector.
Each entry is stored under the SHA-256 hash of the patient's phone number,
ensuring no raw PII is ever written to the vector database.

Collection: patient_history (separate from clinica_docs institutional knowledge)

Each retrieval is logged to rag_audit for observability and analytics.

LGPD:
  - Phone number is hashed with SHA-256 + salt before storage.
  - Only conversation summaries/intents are stored — never raw CPF, RG, or card data.
  - Raw message text is scrubbed via _scrub_pii() before embedding.
"""
import hashlib
import logging
import os
import re

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector

from app.tools.retriever_tool import _get_connection_string, _log_rag_audit

load_dotenv()
logger = logging.getLogger(__name__)

COLLECTION_HISTORY = "patient_history"
EMBEDDING_MODEL = "text-embedding-3-small"

# PII scrubbing patterns (mirrors export_bronze.py regexes)
_PII_PATTERNS = [
    r"\d{3}[\.\-]?\d{3}[\.\-]?\d{3}[\-]?\d{2}",  # CPF
    r"\d{2}[\.\-]?\d{3}[\.\-]?\d{3}[\/]?\d{4}[\-]?\d{2}",  # CNPJ
    r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b",  # card
    r"\b\d{5}[\-]?\d{3}\b",  # CEP
]
_PII_RE = re.compile("|".join(_PII_PATTERNS))


def _scrub_pii(text: str) -> str:
    """Removes common PII patterns from text before embedding."""
    return _PII_RE.sub("[REDACTED]", text)


def _hash_phone(phone: str) -> str:
    """
    Returns SHA-256(phone + salt) for use as the patient identifier.
    Salt is read from the EPISODIC_SALT env var (falls back to DBT_SALT).
    """
    salt = os.getenv("EPISODIC_SALT") or os.getenv("DBT_SALT", "default_salt")
    return hashlib.sha256(f"{phone}{salt}".encode()).hexdigest()


def _get_vectorstore() -> PGVector:
    return PGVector(
        embeddings=OpenAIEmbeddings(model=EMBEDDING_MODEL),
        collection_name=COLLECTION_HISTORY,
        connection=_get_connection_string(),
    )


def salvar_conversa(phone: str, message_text: str, patient_name: str) -> bool:
    """
    Embeds and stores a conversation summary in the patient_history collection.

    Args:
        phone:        Raw phone number (will be hashed before storage).
        message_text: Concatenated conversation text (will be PII-scrubbed).
        patient_name: First name only — used as a label, never raw PII.

    Returns:
        True on success, False on any error.
    """
    patient_id = _hash_phone(phone)
    clean_text = _scrub_pii(message_text)

    doc = Document(
        page_content=clean_text,
        metadata={
            "patient_id": patient_id,
            "patient_name": patient_name.split()[0],  # first name only
            "source": "whatsapp_conversation",
        },
    )
    try:
        vs = _get_vectorstore()
        vs.add_documents([doc])
        logger.info("Episodic memory saved | patient_id: %s...", patient_id[:8])
        return True
    except Exception as exc:
        logger.error("Failed to save episodic memory: %s", str(exc))
        return False


def buscar_historico_paciente(phone: str, query: str, k: int = 3) -> list[dict]:
    """
    Retrieves the k most relevant past conversation chunks for a patient.

    Args:
        phone: Raw phone number (hashed internally for the filter).
        query: Current conversation text used as the similarity query.
        k:     Number of results to return (default: 3).

    Returns:
        List of dicts with 'texto' and 'relevancia'. Empty list on any error.
    """
    patient_id = _hash_phone(phone)
    logger.info("Episodic search | patient_id: %s...", patient_id[:8])
    try:
        vs = _get_vectorstore()
        results = vs.similarity_search_with_score(
            query,
            k=k,
            filter={"patient_id": patient_id},
        )
        history = [
            {"texto": doc.page_content, "relevancia": round(1 - score, 2)}
            for doc, score in results
        ]
        logger.info("%d episodic result(s) retrieved", len(history))

        avg_score = (
            round(sum(r["relevancia"] for r in history) / len(history), 3)
            if history
            else 0.0
        )
        _log_rag_audit(
            collection=COLLECTION_HISTORY,
            query_text=query,
            k=k,
            results_returned=len(history),
            avg_score=avg_score,
            patient_id=patient_id,
        )

        return history
    except Exception as exc:
        logger.error("Episodic search failed: %s", str(exc))
        return []
