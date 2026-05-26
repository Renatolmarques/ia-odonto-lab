# app/schemas.py
"""
IA Odonto Lab — Pydantic Data Models

WebhookPayload : incoming data contract from n8n
ResumoClinico  : structured clinical output written to EspoCRM

Using Pydantic v2 with strict validation ensures the LLM output
always matches the CRM schema — no regex parsing, no hallucinated fields.

Sprint 1 additions:
  origem_lead           → cCOrigemLead   (lead source classification)
  procedimento_interesse → cCProcedimentoInteresse (procedure of interest)
  fobia_dentaria        → cCFobiasDentarias (dental fears/phobias)
  intencao_principal    → cCLinaIntencaoPrincipal (fine-grained intent)
  etapa_funil_sugerida  → cCEtapaFunil (suggested funnel stage)
"""
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class WebhookPayload(BaseModel):
    """
    Payload sent by n8n after the message buffer window closes.
    Typically triggered 15 seconds after the last message to capture
    multi-message conversations as a single unit.
    """

    phone: str = Field(..., description="Patient phone number (WhatsApp E.164 format)")
    patient_name: Optional[str] = Field(
        None, description="Display name from WhatsApp profile"
    )
    message_text: Optional[str] = Field(
        None, description="Concatenated plain-text messages"
    )
    audio_transcription: Optional[str] = Field(
        None, description="Transcribed audio — takes priority over text"
    )

    @field_validator("message_text", "audio_transcription", mode="before")
    @classmethod
    def strip_empty(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class ResumoClinico(BaseModel):
    """
    Structured clinical summary produced by Lina and written to EspoCRM.

    Generated via llm.with_structured_output(ResumoClinico) — guarantees
    type-safe, validated output without any post-processing.

    Field → EspoCRM mapping:
      cliente              → firstName + lastName
      intencao             → cIntencao (legacy intent classification)
      potencial            → cPotencialVenda (estimated deal value)
      qtd_consultas        → cQtdConsultas
      historico            → part of cAisummary (main CRM field)
      origem_lead          → cCOrigemLead
      procedimento_interesse → cCProcedimentoInteresse
      fobia_dentaria       → cCFobiasDentarias
      intencao_principal   → cCLinaIntencaoPrincipal
      etapa_funil_sugerida → cCEtapaFunil

    Note on EspoCRM field naming: custom fields created with prefix 'c'
    are stored by EspoCRM with a doubled prefix 'cC' (e.g. cOrigemLead → cCOrigemLead).
    All API payloads must use the 'cC' prefix.

    Language note:
      - Default values and CRM output are in Portuguese (patient-facing).
      - Internal field names and descriptions remain in English (CV/showcase).
    """

    # ── Legacy fields (unchanged) ──────────────────────────────────────────
    cliente: str = Field(default="Não identificado", description="Patient full name")
    intencao: Literal["Consulta", "Agendamento", "Reclamação", "Outro"] = Field(
        default="Outro",
        description="Conversation intent (legacy, kept for compatibility)",
    )
    solicitacao: str = Field(
        default="Não identificada", description="What the patient is asking for"
    )
    obs: str = Field(
        default="Nenhuma observação adicional",
        description="Fears, objections, chronology",
    )
    fobias_alergias: str = Field(
        default="Nenhuma relatada", description="Phobias and allergies mentioned"
    )
    ltv_pago: float = Field(
        default=0.0, ge=0, description="Payments already made (if mentioned by patient)"
    )
    potencial: float = Field(
        default=0.0, ge=0, description="Estimated potential value in BRL"
    )
    qtd_consultas: int = Field(
        default=0, ge=0, description="Number of past visits identified in conversation"
    )
    historico: str = Field(
        default="Sem histórico disponível",
        description="Chronological interaction summary",
    )

    # ── Sprint 1: lead source, funnel stage, fine-grained intent ──────────
    origem_lead: Literal[
        "instagram",
        "google",
        "indicacao",
        "anuncio_pago",
        "material_fisico",
        "nao_identificada",
    ] = Field(
        default="nao_identificada",
        description=(
            "Lead acquisition channel. Inferred from conversation text or UTM param. "
            "Maps to cCOrigemLead in EspoCRM."
        ),
    )

    procedimento_interesse: Optional[str] = Field(
        default=None,
        description=(
            "Dental procedure the patient mentioned interest in. "
            "E.g. 'clareamento', 'implante', 'aparelho'. "
            "Maps to cCProcedimentoInteresse in EspoCRM."
        ),
    )

    fobia_dentaria: Optional[str] = Field(
        default=None,
        description=(
            "Any fear, phobia or anxiety about dental treatment mentioned by the patient. "
            "E.g. 'medo de agulha', 'trauma com dentista'. "
            "Maps to cCFobiasDentarias in EspoCRM."
        ),
    )

    intencao_principal: Literal[
        "inquiry",
        "scheduling",
        "confirmation",
        "cancellation",
        "rescheduling",
        "question",
        "ambiguous",
    ] = Field(
        default="inquiry",
        description=(
            "Fine-grained intent classification. More specific than the legacy 'intencao' field. "
            "Maps to cCLinaIntencaoPrincipal in EspoCRM."
        ),
    )

    etapa_funil_sugerida: Literal[
        "lead_novo",
        "lead_qualificado",
        "orcamento_enviado",
        "consulta_agendada",
        "consulta_realizada",
        "tratamento_andamento",
        "paciente_ativo",
        "perdido",
    ] = Field(
        default="lead_novo",
        description=(
            "Suggested CRM funnel stage based on conversation analysis. "
            "Maps to cCEtapaFunil in EspoCRM."
        ),
    )

    @staticmethod
    def _mask_pii(text: str) -> str:
        """
        Masks PII patterns for LGPD compliance before writing to CRM.

        Patterns masked:
          CPF:   123.456.789-00  →  ***.***.***-**
          CNPJ:  12.345.678/0001-90  →  **.***.***/****-**
        """
        text = re.sub(r"\d{3}\.\d{3}\.\d{3}-\d{2}", "***.***.***-**", text)
        text = re.sub(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", "**.***.***/****-**", text)
        return text

    def formatar_para_crm(self) -> str:
        """
        Formats the summary for the EspoCRM cAisummary field in markdown.
        Applies LGPD PII masking before output — CPF and CNPJ are never written to CRM.
        Output is in Portuguese — visible to the dentist in Metabase and EspoCRM.
        """
        historico_limpo = self._mask_pii(self.historico)
        obs_limpo = self._mask_pii(self.obs)

        return (
            f"**Resumo Clínico (IA):**\n"
            f"{self.cliente}. {self.fobias_alergias}. {historico_limpo}\n\n"
            f"**Nota Técnica:**\n"
            f"- Cliente: {self.cliente}\n"
            f"- Intenção: {self.intencao}\n"
            f"- Solicitação: {self.solicitacao}\n"
            f"- Observações: {obs_limpo}\n"
            f"- Potencial estimado: R$ {self.potencial:.2f}\n"
            f"- Qtd. consultas: {self.qtd_consultas}\n"
            f"- Origem: {self.origem_lead}\n"
            f"- Procedimento de interesse: {self.procedimento_interesse or 'Não identificado'}\n"
            f"- Etapa do funil: {self.etapa_funil_sugerida}"
        )
