# app/main.py
"""
IA Odonto Lab — FastAPI Application Entry Point

Orchestrates the full AI pipeline:
  1. Receive webhook from n8n (WhatsApp message buffer)
  2. Invoke Lina clinical agent (LangChain + RAG + structured output)
  3. Upsert structured clinical summary into EspoCRM

Endpoints:
  POST /webhook/n8n_handoff  — main pipeline trigger
  GET  /health               — liveness check
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.agents.clinical_agent import processar_conversa
from app.schemas import ResumoClinico, WebhookPayload
from app.services.crm_service import upsert_paciente_no_crm

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🦷 IA Odonto Lab API starting...")
    yield
    logger.info("IA Odonto Lab API shutdown complete.")


app = FastAPI(
    title="IA Odonto Lab",
    version="0.4.0",
    description="AI-powered CRM intelligence layer for dental clinics.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.get("/health")
async def health_check():
    """Liveness check used by Docker healthcheck and monitoring."""
    return {"status": "ok", "version": "0.4.0", "service": "ia-odonto-lab"}


@app.post("/webhook/n8n_handoff")
async def n8n_handoff(payload: WebhookPayload):
    """
    Main pipeline endpoint.

    Receives a WhatsApp conversation (already buffered and concatenated by n8n),
    runs it through the Lina AI agent, and writes the structured result to EspoCRM.

    Audio transcriptions take priority over plain text when both are present.
    """
    message = payload.audio_transcription or payload.message_text
    if not message or not message.strip():
        raise HTTPException(
            status_code=422, detail="message_text or audio_transcription is required."
        )

    logger.info("📩 Webhook received | phone: %s", payload.phone)

    try:
        logger.info("🧠 Invoking Lina clinical agent...")
        resumo: ResumoClinico = await processar_conversa(
            mensagem=message,
            phone=payload.phone,
            patient_name=payload.patient_name,
        )
        logger.info("✅ Clinical summary generated | intent: %s", resumo.intencao)

        logger.info("📋 Upserting into EspoCRM...")
        crm_result = await upsert_paciente_no_crm(phone=payload.phone, resumo=resumo)
        logger.info("✅ EspoCRM updated | id: %s", crm_result.get("id"))

        return {"status": "processed", "resumo": resumo.model_dump(), "crm": crm_result}

    except Exception as exc:
        logger.error("❌ Processing error: %s", str(exc), exc_info=True)
        raise HTTPException(status_code=502, detail=f"Processing error: {str(exc)}")
