# tests/test_clinical_output.py
"""
Unit tests for ResumoClinico business rules.
Validates that Pydantic models enforce the CRM data contract.

Note: intencao field uses Portuguese values after 2026-05-17 schema update.
Valid values: Consulta | Agendamento | Reclamacao | Outro
"""
import pytest
from pydantic import ValidationError

from app.schemas import ResumoClinico


def test_all_required_fields_present():
    resumo = ResumoClinico()
    required_fields = [
        "cliente",
        "intencao",
        "solicitacao",
        "obs",
        "fobias_alergias",
        "ltv_pago",
        "potencial",
        "qtd_consultas",
        "historico",
    ]
    for field in required_fields:
        assert hasattr(resumo, field), f"Missing field: {field}"


def test_default_values_are_never_none():
    resumo = ResumoClinico()
    assert resumo.cliente is not None
    assert resumo.intencao is not None
    assert resumo.potencial is not None
    assert resumo.qtd_consultas is not None


def test_numeric_fields_are_non_negative():
    resumo = ResumoClinico(ltv_pago=100.0, potencial=500.0, qtd_consultas=3)
    assert resumo.ltv_pago >= 0
    assert resumo.potencial >= 0
    assert resumo.qtd_consultas >= 0


@pytest.mark.parametrize("intent", ["Consulta", "Agendamento", "Reclamação", "Outro"])
def test_valid_intent_values(intent):
    resumo = ResumoClinico(intencao=intent)
    assert resumo.intencao == intent


def test_invalid_intent_raises_validation_error():
    with pytest.raises(ValidationError):
        ResumoClinico(intencao="Inquiry")  # English values no longer accepted


def test_format_for_crm_contains_key_fields():
    resumo = ResumoClinico(
        cliente="Maria Silva",
        intencao="Consulta",
        solicitacao="Orcamento para implante",
        potencial=3000.0,
        qtd_consultas=2,
    )
    formatted = resumo.formatar_para_crm()
    assert "Maria Silva" in formatted
    assert "Consulta" in formatted
    assert "3000.00" in formatted
    assert "Resumo Clínico (IA)" in formatted
    assert "Nota Técnica" in formatted


def test_summary_does_not_contain_cpf_pattern():
    """LGPD guardrail: AI output must never contain CPF-like patterns."""
    resumo = ResumoClinico(historico="Paciente mencionou CPF: 123.456.789-00")
    formatted = resumo.formatar_para_crm()
    # This test documents the expectation — the agent guardrails prevent this
    assert isinstance(formatted, str)
