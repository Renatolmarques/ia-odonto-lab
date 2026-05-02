# app/routers/conversations.py
"""
IA Odonto Lab — Conversations Router

Exposes POST /conversations/save so n8n can persist each processed
conversation into the episodic memory (patient_history pgvector collection).

Called by n8n after "Marcar Processado" — after the buffer is flushed
and CRM has already been updated. This is a fire-and-forget write;
n8n does not need the response body, only the 200 status.

Auth: X-API-Key header (same key used by /webhook/n8n_handoff).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import verify_api_key
from app.tools.episodic_memory import salvar_conversa

logger = logging.getLogger(__name__)
router = APIRouter()


class ConversationPayload(BaseModel):
    phone: str
    message_text: str
    patient_name: str = "Patient"


@router.post("/conversations/save", dependencies=[Depends(verify_api_key)])
async def save_conversation(payload: ConversationPayload):
    """
    Persists a processed WhatsApp conversation into episodic memory.

    Called by n8n after the CRM has been updated. Stores a PII-scrubbed,
    SHA-256-keyed embedding in the patient_history pgvector collection.
    """
    logger.info(
        "📥 /conversations/save | phone: ...%s | name: %s",
        payload.phone[-4:],
        payload.patient_name,
    )
    success = salvar_conversa(
        phone=payload.phone,
        message_text=payload.message_text,
        patient_name=payload.patient_name,
    )
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save episodic memory")

    return {"status": "saved", "phone_suffix": payload.phone[-4:]}
