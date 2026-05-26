# app/services/crm_service.py
"""
IA Odonto Lab — EspoCRM Integration Service

Handles Contact upsert via EspoCRM REST API:
  1. Search contact by phone number (LIKE query)
  2. If found: update AI summary and estimated fields
  3. If not found: create new contact with status "New Lead"

Responsibility boundaries (do NOT overwrite fields owned by other systems):
  THIS SERVICE writes:
    cAisummary, cPotencialVenda, cQtdConsultas, cDisplayPotencial  (legacy)
    cCOrigemLead, cCProcedimentoInteresse, cCFobiasDentarias        (Sprint 1)
    cCLinaIntencaoPrincipal, cCEtapaFunil                          (Sprint 1)

  n8n WORKFLOW writes:
    cLifetimeValue, cCKanbanCard, cCUltimoRecebimento (from billing DB)
    cCDiasUltimaInteracao, cCStatusRisco              (recalculated daily)

Note on EspoCRM field naming:
  Custom fields created with prefix 'c' are stored by EspoCRM with a doubled
  prefix 'cC' (e.g. cOrigemLead → cCOrigemLead). All API payloads use 'cC'.
"""
import logging
import os
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

from app.schemas import ResumoClinico

load_dotenv()
logger = logging.getLogger(__name__)

ESPO_URL = os.getenv("ESPO_URL", "").rstrip("/")
ESPO_API_KEY = os.getenv("ESPO_API_KEY", "")
ESPO_TIMEOUT = 15


def _get_headers() -> Dict[str, str]:
    if not ESPO_API_KEY:
        raise ValueError("ESPO_API_KEY not configured in .env")
    return {"X-Api-Key": ESPO_API_KEY, "Content-Type": "application/json"}


def _clean_phone(phone: str) -> str:
    """Strips +, spaces and formatting. E.g. '+5511999999999' → '5511999999999'"""
    return phone.replace("+", "").replace(" ", "").strip()


def _format_potential_display(value: float) -> str:
    """Formats potential value as a visual card string matching CRM display format."""
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"\U0001f680 Potential: R$ {formatted}"


def _build_sprint1_payload(resumo: ResumoClinico) -> Dict[str, Any]:
    """
    Builds the Sprint 1 fields payload for EspoCRM API.
    Only includes fields with actual values — avoids overwriting with nulls.
    Uses the doubled 'cC' prefix required by EspoCRM custom field naming.
    """
    payload: Dict[str, Any] = {
        "cCOrigemLead": resumo.origem_lead,
        "cCLinaIntencaoPrincipal": resumo.intencao_principal,
        "cCEtapaFunil": resumo.etapa_funil_sugerida,
    }
    # Only write optional fields if Lina actually extracted a value
    if resumo.procedimento_interesse:
        payload["cCProcedimentoInteresse"] = resumo.procedimento_interesse
    if resumo.fobia_dentaria:
        payload["cCFobiasDentarias"] = resumo.fobia_dentaria
    return payload


async def _search_contact_by_phone(phone: str) -> Optional[str]:
    """
    Searches EspoCRM for a contact by phone using LIKE query.
    Handles phone numbers with or without the Brazilian 9th digit variation.
    """
    clean = _clean_phone(phone)
    async with httpx.AsyncClient(timeout=ESPO_TIMEOUT) as client:
        response = await client.get(
            f"{ESPO_URL}/api/v1/Contact",
            headers=_get_headers(),
            params={
                "where[0][type]": "like",
                "where[0][attribute]": "phoneNumber",
                "where[0][value]": f"%{clean}%",
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("total", 0) > 0:
            contact_id = data["list"][0]["id"]
            logger.info("Contact found | id: %s", contact_id)
            return contact_id
        logger.info("Contact not found | phone: ...%s", clean[-4:])
        return None


async def _create_contact(phone: str, resumo: ResumoClinico) -> Dict[str, Any]:
    """
    Creates a new CRM contact with AI summary, estimated fields,
    and Sprint 1 lead intelligence fields.
    """
    parts = (
        resumo.cliente.split(" ")
        if resumo.cliente != "Not identified"
        else ["Patient", "."]
    )
    payload = {
        # Identity
        "firstName": parts[0],
        "lastName": " ".join(parts[1:]) if len(parts) > 1 else ".",
        "phoneNumber": phone,
        "cStatusAtendimento": "Novo Lead",
        # Legacy AI fields
        "cAisummary": resumo.formatar_para_crm(),
        "cPotencialVenda": resumo.potencial,
        "cPotencialVendaCurrency": "BRL",
        "cQtdConsultas": resumo.qtd_consultas,
        "cDisplayPotencial": _format_potential_display(resumo.potencial),
        "cDisplayVisitas": f"\U0001f3e5 Visits: {resumo.qtd_consultas}",
        # Sprint 1: lead intelligence fields
        **_build_sprint1_payload(resumo),
    }
    async with httpx.AsyncClient(timeout=ESPO_TIMEOUT) as client:
        response = await client.post(
            f"{ESPO_URL}/api/v1/Contact", headers=_get_headers(), json=payload
        )
        if response.status_code == 400:
            logger.error("400 creating contact | response: %s", response.text)
        response.raise_for_status()
        result = response.json()
        logger.info("Contact created | id: %s", result.get("id"))
        return result


async def _update_contact(contact_id: str, resumo: ResumoClinico) -> Dict[str, Any]:
    """
    Updates AI-owned fields on an existing contact.
    Does not touch LTV, Kanban, or n8n-owned fields.
    Includes Sprint 1 lead intelligence fields on every update.
    """
    payload = {
        # Legacy AI fields
        "cAisummary": resumo.formatar_para_crm(),
        "cPotencialVenda": resumo.potencial,
        "cPotencialVendaCurrency": "BRL",
        "cQtdConsultas": resumo.qtd_consultas,
        "cDisplayPotencial": _format_potential_display(resumo.potencial),
        "cDisplayVisitas": f"\U0001f3e5 Visits: {resumo.qtd_consultas}",
        # Sprint 1: lead intelligence fields
        **_build_sprint1_payload(resumo),
    }
    if resumo.cliente and resumo.cliente != "Not identified":
        parts = resumo.cliente.split(" ")
        payload["firstName"] = parts[0]
        if len(parts) > 1:
            payload["lastName"] = " ".join(parts[1:])
    async with httpx.AsyncClient(timeout=ESPO_TIMEOUT) as client:
        response = await client.put(
            f"{ESPO_URL}/api/v1/Contact/{contact_id}",
            headers=_get_headers(),
            json=payload,
        )
        if response.status_code == 400:
            logger.error(
                "400 updating contact %s | response: %s", contact_id, response.text
            )
        response.raise_for_status()
        logger.info("Contact updated | id: %s", contact_id)
        return {"id": contact_id, "updated": True}


async def upsert_paciente_no_crm(phone: str, resumo: ResumoClinico) -> Dict[str, Any]:
    """
    Main entry point. Upserts a patient contact in EspoCRM.

    Search by phone → update if exists, create if not.
    Skips gracefully if ESPO_URL is not configured.
    """
    if not ESPO_URL:
        logger.warning("ESPO_URL not configured. Skipping CRM integration.")
        return {"status": "skipped", "reason": "ESPO_URL not configured"}
    try:
        contact_id = await _search_contact_by_phone(phone)
        return (
            await _update_contact(contact_id, resumo)
            if contact_id
            else await _create_contact(phone, resumo)
        )
    except httpx.HTTPStatusError as exc:
        logger.error("EspoCRM HTTP error: %s | %s", str(exc), exc.response.text)
        raise
    except Exception as exc:
        logger.error("Unexpected CRM error: %s", str(exc), exc_info=True)
        raise
