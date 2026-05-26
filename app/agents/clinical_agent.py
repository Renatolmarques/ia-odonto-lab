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

Sprint 1 additions:
  - Lead source classification (origem_lead) from conversation text or UTM param
  - Dental procedure extraction (procedimento_interesse)
  - Dental fear/phobia extraction (fobia_dentaria)
  - Fine-grained intent classification (intencao_principal)
  - Funnel stage suggestion (etapa_funil_sugerida)

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
        f"  - [{round(doc['relevancia'] * 100)}% relevante] {doc['texto']}"
        for doc in retrieved_context
    )
    return f"""
CONTEXTO DA BASE DE CONHECIMENTO DA CLÍNICA (recuperado via RAG):
{lines}

Use este contexto para estimar o campo 'potencial' com base nos preços reais dos serviços.
"""


def _build_history_block(history: list[dict]) -> str:
    """Formats episodic memory results as a patient history block."""
    if not history:
        return ""
    lines = "\n".join(
        f"  - [{round(doc['relevancia'] * 100)}% relevante] {doc['texto']}"
        for doc in history
    )
    return f"""
HISTÓRICO DE CONVERSAS DO PACIENTE (memória episódica):
{lines}

Use este histórico para enriquecer o resumo com contexto longitudinal.
Se o paciente mencionou algo antes (medo, interesse em procedimento, visita anterior),
incorpore em 'historico' e 'fobias_alergias'.
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

    name_hint = f"O nome do paciente pode ser '{patient_name}'." if patient_name else ""

    system_prompt = f"""
IDENTIDADE
Você é Lina, analista de inteligência clínica de uma clínica odontológica.
Sua função é analisar conversas de pacientes e extrair dados estruturados para o CRM.
Você NÃO responde aos pacientes — apenas analisa e documenta.
Todos os campos de texto devem ser preenchidos em PORTUGUÊS BRASILEIRO.

{name_hint}

REGRAS GERAIS
1. NUNCA invente dados. Use os valores padrão se a informação não puder ser extraída.
2. NUNCA faça diagnósticos médicos.
3. NUNCA inclua CPF, RG, senhas ou números de documentos na saída.
4. 'potencial': use os preços do contexto RAG abaixo. Se indisponível, use 0.0.
5. 'intencao': classifique como Consulta | Agendamento | Reclamação | Outro.
6. 'ltv_pago': preencha apenas se o paciente mencionou pagamentos passados explicitamente.
7. 'fobias_alergias': capture qualquer menção a medo, fobia ou alergia.
8. Se houver histórico do paciente abaixo, use-o para enriquecer o resumo — não ignore.

CLASSIFICAÇÃO DE ORIGEM DO LEAD (campo: origem_lead)
Analise o texto da conversa e classifique a origem usando estas regras:
- Mencionou "Instagram", "Insta", "post", "story", "reel", "feed", "perfil"
  → "instagram"
- Mencionou "Google", "Maps", "pesquisei", "achei no site", "no mapa", "busquei"
  → "google"
- Mencionou "indicação", "amigo indicou", "fulano falou", "me indicaram", "recomendação"
  → "indicacao"
- Mencionou "anúncio", "vi um anúncio", "propaganda", "patrocinado", "publicidade"
  → "anuncio_pago"
- Mencionou "cartão", "QR code", "flyer", "impresso", "panfleto"
  → "material_fisico"
- Primeira mensagem começa com "Vim pelo Instagram", "Vim pelo Google" (UTM param)
  → usar o canal correspondente acima
- Nenhuma indicação clara de origem
  → "nao_identificada"

CLASSIFICAÇÃO DE INTENÇÃO PRINCIPAL (campo: intencao_principal)
- Paciente fazendo perguntas gerais sobre serviços, preços, localização
  → "inquiry"
- Paciente pedindo para marcar, agendar ou confirmar horário
  → "scheduling"
- Paciente confirmando consulta já agendada
  → "confirmation"
- Paciente cancelando consulta
  → "cancellation"
- Paciente pedindo para remarcar ou trocar horário
  → "rescheduling"
- Paciente fazendo pergunta técnica sobre procedimento ou pós-operatório
  → "question"
- Intenção não clara ou mista
  → "ambiguous"

EXTRAÇÃO DE PROCEDIMENTO DE INTERESSE (campo: procedimento_interesse)
Se o paciente mencionou qualquer procedimento odontológico, extraia o nome em português.
Exemplos: "clareamento", "implante", "limpeza", "aparelho", "canal", "faceta", "extração".
Se não mencionou nenhum procedimento, retorne null.

EXTRAÇÃO DE FOBIA DENTÁRIA (campo: fobia_dentaria)
Se o paciente mencionou medo, ansiedade, trauma ou fobia relacionada a tratamento dentário,
descreva brevemente em português. Exemplos: "medo de agulha", "ansiedade com barulho do motor",
"trauma com dentista anterior". Se não mencionou, retorne null.

SUGESTÃO DE ETAPA DO FUNIL (campo: etapa_funil_sugerida)
Com base na conversa completa, sugira a etapa mais adequada:
- Paciente nunca foi à clínica, só perguntando
  → "lead_novo"
- Paciente demonstrou interesse real, perguntou preço ou disponibilidade
  → "lead_qualificado"
- Paciente pediu orçamento detalhado
  → "orcamento_enviado"
- Paciente agendou ou está tentando agendar consulta
  → "consulta_agendada"
- Paciente mencionou que já foi à consulta recentemente
  → "consulta_realizada"
- Paciente está em tratamento ativo (múltiplas sessões mencionadas)
  → "tratamento_andamento"
- Paciente recorrente, retornando para manutenção
  → "paciente_ativo"
- Paciente cancelou, sumiu ou demonstrou desistência clara
  → "perdido"

{_build_rag_block(retrieved_context)}
{_build_history_block(history)}

RETORNE APENAS O JSON ESTRUTURADO. Nenhum texto fora do JSON.
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Analise esta conversa:\n\n{mensagem}"),
    ]

    llm = ChatOpenAI(
        model=MODEL_NAME, temperature=TEMPERATURE_STRUCTURED, max_tokens=1500
    )
    resumo: ResumoClinico = llm.with_structured_output(ResumoClinico).invoke(messages)

    logger.info(
        "✅ Intent: %s | Funnel: %s | Source: %s | Estimated potential: R$ %.2f",
        resumo.intencao_principal,
        resumo.etapa_funil_sugerida,
        resumo.origem_lead,
        resumo.potencial,
    )
    return resumo


def testar_agente_langchain(mensagem_paciente: str) -> str:
    """
    Legacy test function — returns free-text response for manual testing.
    Used by test_rag_integration.py (Sprint 3 compatibility).
    Kept in English for CV/showcase purposes.

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
