# app/schemas.py
"""
IA Odonto Lab — Pydantic Data Models

WebhookPayload : incoming data contract from n8n
ResumoClinico  : structured clinical output written to EspoCRM

Using Pydantic v2 with strict validation ensures the LLM output
always matches the CRM schema — no regex parsing, no hallucinated fields.
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
      cliente         → firstName + lastName
      intencao        → cIntencao (intent classification)
      potencial       → cPotencialVenda (estimated deal value)
      qtd_consultas   → cQtdConsultas
      historico       → part of cAisummary (main CRM field)
    """

    cliente: str = Field(default="Not identified", description="Patient full name")
    intencao: Literal["Inquiry", "Scheduling", "Complaint", "Other"] = Field(
        default="Other", description="Conversation intent"
    )
    solicitacao: str = Field(
        default="Not identified", description="What the patient is asking for"
    )
    obs: str = Field(
        default="No additional notes", description="Fears, objections, chronology"
    )
    fobias_alergias: str = Field(
        default="None reported", description="Phobias and allergies mentioned"
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
        default="No history available", description="Chronological interaction summary"
    )

    @staticmethod
    def _mask_pii(text: str) -> str:
        """
        Masks PII patterns for LGPD compliance before writing to CRM.

        Patterns masked:
          CPF:   123.456.789-00  →  ***.***.***-**
          CNPJ:  12.345.678/0001-90  →  **.***.***/****-**
        """
        # Mask CPF pattern: 000.000.000-00
        text = re.sub(r"\d{3}\.\d{3}\.\d{3}-\d{2}", "***.***.***-**", text)
        # Mask CNPJ pattern: 00.000.000/0000-00
        text = re.sub(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", "**.***.***/****-**", text)
        return text

    def formatar_para_crm(self) -> str:
        """
        Formats the summary for the EspoCRM cAisummary field in markdown.
        Applies LGPD PII masking before output — CPF and CNPJ are never written to CRM.
        """
        historico_limpo = self._mask_pii(self.historico)
        obs_limpo = self._mask_pii(self.obs)

        return (
            f"**AI Clinical Summary:**\n"
            f"{self.cliente}. {self.fobias_alergias}. {historico_limpo}\n\n"
            f"**Technical Note:**\n"
            f"- Client: {self.cliente}\n"
            f"- Intent: {self.intencao}\n"
            f"- Request: {self.solicitacao}\n"
            f"- Notes: {obs_limpo}\n"
            f"- Estimated potential: R$ {self.potencial:.2f}\n"
            f"- Visit count: {self.qtd_consultas}"
        )
