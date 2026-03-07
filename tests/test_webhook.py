# tests/test_webhook.py
"""
Integration tests for the FastAPI webhook endpoint.
Uses TestClient with mocked agent and CRM dependencies.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import ResumoClinico

client = TestClient(app)

MOCK_RESUMO = ResumoClinico(
    cliente="Test Patient",
    intencao="Inquiry",
    solicitacao="Dental implant pricing",
    obs="Patient mentioned fear of needles",
    fobias_alergias="Fear of needles",
    potencial=3000.0,
    qtd_consultas=0,
    historico="First contact — asked about implants.",
)


@pytest.fixture(autouse=True)
def mock_dependencies():
    with patch(
        "app.main.processar_conversa", new_callable=AsyncMock, return_value=MOCK_RESUMO
    ), patch(
        "app.main.upsert_paciente_no_crm",
        new_callable=AsyncMock,
        return_value={"id": "abc123"},
    ):
        yield


def test_healthcheck():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_webhook_returns_200_with_valid_payload():
    payload = {
        "phone": "+5511999999999",
        "patient_name": "Test Patient",
        "message_text": "I need a dental implant. How much does it cost?",
    }
    response = client.post("/webhook/n8n_handoff", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "processed"


def test_webhook_returns_422_with_empty_message():
    payload = {"phone": "+5511999999999", "message_text": ""}
    response = client.post("/webhook/n8n_handoff", json=payload)
    assert response.status_code == 422


def test_webhook_prioritizes_audio_transcription():
    payload = {
        "phone": "+5511999999999",
        "message_text": "text message",
        "audio_transcription": "audio transcription takes priority",
    }
    response = client.post("/webhook/n8n_handoff", json=payload)
    assert response.status_code == 200
