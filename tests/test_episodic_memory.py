# tests/test_episodic_memory.py
"""
Sprint 7 — Episodic Memory Tests

Verifies that:
1. salvar_conversa() stores a conversation without errors.
2. buscar_historico_paciente() retrieves results for a known patient.
3. buscar_historico_paciente() returns empty for an unknown patient.
4. PII scrubbing removes CPF patterns before embedding.
5. POST /conversations/save returns 200 with valid auth.
6. POST /conversations/save returns 401 without auth.
"""
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.tools.episodic_memory import _hash_phone, _scrub_pii, salvar_conversa

client = TestClient(app)
VALID_KEY = "test-api-key"


# ---------------------------------------------------------------------------
# Unit tests — PII scrubbing
# ---------------------------------------------------------------------------


def test_scrub_pii_removes_cpf():
    text = "Paciente CPF 123.456.789-09 quer implante"
    result = _scrub_pii(text)
    assert "123.456.789-09" not in result
    assert "[REDACTED]" in result


def test_scrub_pii_keeps_clean_text():
    text = "Patient asked about implant pricing"
    assert _scrub_pii(text) == text


def test_hash_phone_is_deterministic():
    h1 = _hash_phone("81999990000")
    h2 = _hash_phone("81999990000")
    assert h1 == h2


def test_hash_phone_differs_per_number():
    assert _hash_phone("81999990000") != _hash_phone("81988880000")


# ---------------------------------------------------------------------------
# Unit tests — salvar_conversa and buscar_historico_paciente (mocked pgvector)
# ---------------------------------------------------------------------------


@patch("app.tools.episodic_memory._get_vectorstore")
def test_salvar_conversa_success(mock_vs):
    vs_instance = MagicMock()
    mock_vs.return_value = vs_instance
    result = salvar_conversa(
        phone="81999990000",
        message_text="Patient wants a dental implant",
        patient_name="Ana Silva",
    )
    assert result is True
    vs_instance.add_documents.assert_called_once()


@patch("app.tools.episodic_memory._get_vectorstore")
def test_salvar_conversa_handles_error(mock_vs):
    mock_vs.side_effect = Exception("DB connection failed")
    result = salvar_conversa(
        phone="81999990000",
        message_text="Patient wants a dental implant",
        patient_name="Ana",
    )
    assert result is False


@patch("app.tools.episodic_memory._get_vectorstore")
def test_buscar_historico_returns_results(mock_vs):
    from langchain_core.documents import Document

    from app.tools.episodic_memory import buscar_historico_paciente

    mock_doc = Document(page_content="Patient asked about implant last month")
    vs_instance = MagicMock()
    vs_instance.similarity_search_with_score.return_value = [(mock_doc, 0.2)]
    mock_vs.return_value = vs_instance

    results = buscar_historico_paciente(phone="81999990000", query="implant")
    assert len(results) == 1
    assert results[0]["relevancia"] == 0.8
    assert "implant" in results[0]["texto"]


@patch("app.tools.episodic_memory._get_vectorstore")
def test_buscar_historico_empty_for_unknown(mock_vs):
    from app.tools.episodic_memory import buscar_historico_paciente

    vs_instance = MagicMock()
    vs_instance.similarity_search_with_score.return_value = []
    mock_vs.return_value = vs_instance

    results = buscar_historico_paciente(phone="00000000000", query="anything")
    assert results == []


# ---------------------------------------------------------------------------
# Integration tests — POST /conversations/save endpoint
# ---------------------------------------------------------------------------


@patch("app.routers.conversations.salvar_conversa", return_value=True)
def test_save_conversation_success(mock_save, monkeypatch):
    monkeypatch.setenv("LINA_API_KEY", VALID_KEY)
    resp = client.post(
        "/conversations/save",
        json={
            "phone": "81999990000",
            "message_text": "Patient wants whitening",
            "patient_name": "Carlos",
        },
        headers={"X-API-Key": VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "saved"


@patch("app.routers.conversations.salvar_conversa", return_value=False)
def test_save_conversation_storage_error(mock_save, monkeypatch):
    monkeypatch.setenv("LINA_API_KEY", VALID_KEY)
    resp = client.post(
        "/conversations/save",
        json={
            "phone": "81999990000",
            "message_text": "Patient wants whitening",
            "patient_name": "Carlos",
        },
        headers={"X-API-Key": VALID_KEY},
    )
    assert resp.status_code == 500


def test_save_conversation_no_auth():
    resp = client.post(
        "/conversations/save",
        json={
            "phone": "81999990000",
            "message_text": "Patient wants whitening",
            "patient_name": "Carlos",
        },
    )
    assert resp.status_code == 401
