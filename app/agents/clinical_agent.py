# app/agents/clinical_agent.py
"""
IA Odonto Lab — Lina Clinical Agent

Lina is a silent AI listener. She analyzes patient WhatsApp conversations
and returns a structured clinical summary for the CRM.

She does NOT respond to patients. She only reads, thinks, and documents.

Pipeline:
  1. RAG: retrieve relevant context from the clinic knowledge base (pgvector)
  2. Episodic memory: retrieve past conversations for this patient (pgvector)
  3. Build system prompt with clinical guardrails + RAG context + patient history
  4. Invoke GPT-4o-mini with structured output (Pydantic ResumoClinico)
  5. Return typed model ready for EspoCRM upsert

LGPD compliance: pgvector contains ONLY institutional knowledge (clinica_docs)
                 and hashed conversation summaries (patient_history).
                 Raw patient PII never enters the vector database.
"""
import logging
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.schemas import ResumoClinico
from app.tools.episodic_memory import buscar_historico_paciente
from app.tools.retriever_tool import buscar_contexto

load_dotenv()
logger = logging.getLogger(__name__)

MODEL_NAME = "gpt-4o-mini"
TEMPERATURE_STRUCTURED = 0.1
TEMPERATURE_CHAT = 0.3


def _build_rag_block(retrieved_context: list[dict]) -> str:
    """Formats RAG results as a context block for the system prompt."""
    if not retrieved_context:
        return ""
    lines = "\n".join(
        f"  - [{round(doc['relevancia'] * 100)}% relevant] {doc['texto']}"
        for doc in retrieved_context
    )
    return f"""
CLINIC KNOWLEDGE BASE CONTEXT (retrieved automatically via RAG):
{lines}

Use this context to estimate the 'potencial' field based on real service prices.
"""


def _build_history_block(history: list[dict]) -> str:
    """Formats episodic memory results as a patient history block."""
    if not history:
        return ""
    lines = "\n".join(
        f"  - [{round(doc['relevancia'] * 100)}% relevant] {doc['texto']}"
        for doc in history
    )
    return f"""
PATIENT CONVERSATION HISTORY (retrieved from episodic memory):
{lines}

Use this history to enrich the summary with longitudinal context.
If the patient mentioned something before (fear, procedure interest, past visit),
incorporate it into 'resumo' and 'fobias_alergias' fields.
"""


async def processar_conversa(
    mensagem: str,
    phone: str,
    patient_name: Optional[str] = None,
) -> ResumoClinico:
    """
    Analyzes a patient conversation and returns a structured clinical summary.

    Args:
        mensagem:     Concatenated message text or audio transcription.
        phone:        Patient phone number (hashed for episodic memory lookup).
        patient_name: Name from CRM if already known.

    Returns:
        ResumoClinico: Validated Pydantic model ready for EspoCRM upsert.
    """
    logger.info("🧠 Clinical analysis started | phone: ...%s", phone[-4:])

    # Step 1: Retrieve relevant context from knowledge base (institutional)
    retrieved_context = buscar_contexto(mensagem, k=3)
    logger.info("📚 %d RAG result(s) retrieved", len(retrieved_context))

    # Step 2: Retrieve past conversations for this patient (episodic)
    history = buscar_historico_paciente(phone=phone, query=mensagem, k=3)
    logger.info("📖 %d episodic result(s) retrieved", len(history))

    name_hint = f"The patient's name may be '{patient_name}'." if patient_name else ""

    system_prompt = f"""
IDENTITY
You are Lina, a clinical intelligence analyst for a dental clinic.
Your role is to analyze patient conversations and extract structured data for the CRM.
You do NOT respond to patients — you only analyze and document.

{name_hint}

GUARDRAILS
1. NEVER invent data. Use default values if information cannot be extracted.
2. NEVER make medical diagnoses.
3. NEVER include CPF, ID numbers, or passwords in the output.
4. 'potencial': use prices from the RAG context below. If unavailable, use 0.0.
5. 'intencao': classify as Inquiry | Scheduling | Complaint | Other.
6. 'ltv_pago': only fill if the patient explicitly mentioned past payments.
7. 'fobias_alergias': capture any mention of fear, phobia, or allergy.
8. If patient history exists below, use it to enrich the summary — do not ignore it.

{_build_rag_block(retrieved_context)}
{_build_history_block(history)}

RETURN ONLY THE STRUCTURED JSON. No text outside the JSON.
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Analyze this conversation:\n\n{mensagem}"),
    ]

    llm = ChatOpenAI(
        model=MODEL_NAME, temperature=TEMPERATURE_STRUCTURED, max_tokens=1500
    )
    resumo: ResumoClinico = llm.with_structured_output(ResumoClinico).invoke(messages)

    logger.info(
        "✅ Intent: %s | Estimated potential: R$ %.2f",
        resumo.intencao,
        resumo.potencial,
    )
    return resumo


def testar_agente_langchain(mensagem_paciente: str) -> str:
    """
    Legacy test function — returns free-text response for manual testing.
    Used by test_rag_integration.py (Sprint 3 compatibility).

    For production use, call processar_conversa() instead.
    """
    print("\n[SYSTEM] Initializing Lina agent...")
    retrieved_context = buscar_contexto(mensagem_paciente, k=3)

    llm = ChatOpenAI(model=MODEL_NAME, temperature=TEMPERATURE_CHAT)

    system_prompt = """
    IDENTITY
    You are Lina, the AI assistant for a dental clinic.
    Tone: Warm, professional, empathetic.
    Goal: Answer basic questions and guide the patient toward booking an evaluation.

    GUARDRAILS
    1. NEVER invent information. If unsure: "Please verify this with the doctor."
    2. NEVER give medical diagnoses. Say: "The doctor needs to evaluate this clinically."
    3. Off-topic questions: "I'm the clinic assistant — I can only help with dental matters."
    4. Keep messages short and WhatsApp-friendly. Use emojis sparingly (🦷, ✨, 📅).

    SALES APPROACH
    - Price questions: explain that exact pricing requires an evaluation. Invite to schedule.
    - Scheduling: ask for full name, then preferred day/time.
    - Fear/anxiety: show empathy, offer humanized care with the dentist.
    """ + _build_rag_block(
        retrieved_context
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=mensagem_paciente),
    ]

    print(f"[PATIENT]: '{mensagem_paciente}'\n")
    response = llm.invoke(messages)
    print("=== LINA RESPONSE ===")
    print(response.content)
    print("====================\n")
    return response.content


if __name__ == "__main__":
    testar_agente_langchain(
        "Hi, I'm terrified of dentists but I think I need an implant. How much does it cost?"
    )
