# tests/test_retriever.py
"""
Unit tests for the RAG retriever tool.
Uses mocked pgvector to test retrieval logic without a live database.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.tools.retriever_tool import buscar_contexto


@pytest.fixture
def mock_vectorstore():
    mock_doc = MagicMock()
    mock_doc.page_content = (
        "Teeth whitening session lasts 1h30. Avoid staining foods for 48h."
    )
    with patch("app.tools.retriever_tool.PGVector") as mock_pg:
        instance = mock_pg.return_value
        instance.similarity_search_with_score.return_value = [(mock_doc, 0.15)]
        yield mock_pg


def test_returns_list(mock_vectorstore):
    result = buscar_contexto("how much does whitening cost", k=1)
    assert isinstance(result, list)


def test_returns_correct_format(mock_vectorstore):
    result = buscar_contexto("whitening treatment", k=1)
    assert len(result) == 1
    assert "texto" in result[0]
    assert "relevancia" in result[0]
    assert 0.0 <= result[0]["relevancia"] <= 1.0


def test_returns_empty_list_on_error():
    with patch(
        "app.tools.retriever_tool.PGVector", side_effect=Exception("connection refused")
    ):
        result = buscar_contexto("any question", k=1)
    assert result == []


def test_rag_does_not_return_patient_pii(mock_vectorstore):
    """LGPD: RAG must never return patient personal data."""
    import re

    result = buscar_contexto("business hours", k=1)
    cpf_pattern = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
    phone_pattern = re.compile(r"\+?\d{10,13}")
    for item in result:
        assert not cpf_pattern.search(item["texto"]), "CPF found in RAG result"
        assert not phone_pattern.search(
            item["texto"]
        ), "Phone number found in RAG result"
