# tests/test_silver_models.py
"""
Unit tests for Silver Layer transformation logic.
Tests SHA-256 hashing, field normalization, data quality rules,
and PII scrubbing patterns including edge cases.
No database required — pure Python logic tests.
"""
import hashlib
import re

import pytest

# ---------------------------------------------------------------------------
# Helper functions mirroring dbt SQL and export_bronze.py logic
# ---------------------------------------------------------------------------


def hash_id(raw_id: str, salt: str) -> str:
    """Mirrors: sha256(cast(id as varchar) || env_var('DBT_SALT'))"""
    return hashlib.sha256(f"{raw_id}{salt}".encode()).hexdigest()


def normalize_status(raw: str) -> str:
    """Mirrors: lower(trim(c_status_atendimento))"""
    return raw.strip().lower()


def classify_summary_quality(summary) -> str:
    """Mirrors: case when in stg_ai_summaries.sql"""
    if summary is None:
        return "missing"
    if len(summary.strip()) == 0:
        return "empty"
    if len(summary) < 50:
        return "too_short"
    return "ok"


def classify_pipeline_segment(
    status: str,
    total_pago: float,
    potencial_venda: float,
    lifetime_value: float,
    qtd_consultas: int,
) -> str:
    """Mirrors: case when in fct_pipeline.sql"""
    if status == "finalizado" and total_pago == 0:
        return "churned"
    if potencial_venda > lifetime_value:
        return "upsell_opportunity"
    if qtd_consultas >= 3:
        return "loyal"
    return "active"


# PII patterns mirroring _PII_PATTERNS in export_bronze.py
_PII_PATTERNS = [
    # PIX key (UUID) — must come BEFORE CPF to avoid partial digit capture
    (
        re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        ),
        "[PIX_REDACTED]",
    ),
    # Credit/debit card: 16 digits — must come BEFORE CPF
    (
        re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
        "[CARD_REDACTED]",
    ),
    # Brazilian phone international: +5511999998888 — specific, before CPF
    (
        re.compile(r"\+55\s?\(?\d{2}\)?\s?\d{4,5}[\s\-]?\d{4}"),
        "[PHONE_REDACTED]",
    ),
    # Brazilian phone local: (11) 99999-8888 or 11 99999-8888
    (
        re.compile(r"\(?\d{2}\)?\s?\d{4,5}[\s\-]?\d{4}"),
        "[PHONE_REDACTED]",
    ),
    # Brazilian CPF: 123.456.789-00 or 12345678900 — after longer patterns
    (re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}"), "[CPF_REDACTED]"),
    # Brazilian RG: 12.345.678-9 — after CPF
    (re.compile(r"\d{2}\.?\d{3}\.?\d{3}-?\d{1}"), "[RG_REDACTED]"),
    # Email address
    (re.compile(r"[\w\.\-]+@[\w\.\-]+\.\w+"), "[EMAIL_REDACTED]"),
    # Brazilian bank account: agency + account
    (re.compile(r"\b\d{4}[\s\-]?\d{5,6}[\s\-]?\d{1}\b"), "[ACCOUNT_REDACTED]"),
]


def scrub_pii(text):
    """Mirrors scrub_pii() in export_bronze.py — kept in sync manually."""
    if text is None:
        return None
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# SHA-256 + salt tests
# ---------------------------------------------------------------------------


def test_hash_is_deterministic():
    """Same input + same salt must always produce same hash."""
    assert hash_id("abc123", "my-salt") == hash_id("abc123", "my-salt")


def test_hash_changes_with_different_salt():
    """Different salt must produce different hash — core LGPD guarantee."""
    assert hash_id("abc123", "salt-A") != hash_id("abc123", "salt-B")


def test_hash_changes_with_different_id():
    """Different patients must never share the same hash."""
    assert hash_id("patient-1", "salt") != hash_id("patient-2", "salt")


def test_hash_is_hex_string():
    """SHA-256 output must be a 64-char hex string."""
    result = hash_id("any-id", "any-salt")
    assert len(result) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", result)


def test_hash_never_exposes_original_id():
    """Hash output must not contain the original id."""
    original = "patient-secret-id-42"
    result = hash_id(original, "salt")
    assert original not in result


# ---------------------------------------------------------------------------
# Status normalization tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Finalizado", "finalizado"),
        ("FINALIZADO", "finalizado"),
        ("  finalizado  ", "finalizado"),
        ("Novo Lead", "novo lead"),
        ("EM ANDAMENTO", "em andamento"),
    ],
)
def test_normalize_status(raw, expected):
    assert normalize_status(raw) == expected


# ---------------------------------------------------------------------------
# AI summary quality classification tests
# ---------------------------------------------------------------------------


def test_summary_quality_none_is_missing():
    assert classify_summary_quality(None) == "missing"


def test_summary_quality_empty_string():
    assert classify_summary_quality("   ") == "empty"


def test_summary_quality_too_short():
    assert classify_summary_quality("short") == "too_short"


def test_summary_quality_ok():
    long_summary = "A" * 51
    assert classify_summary_quality(long_summary) == "ok"


# ---------------------------------------------------------------------------
# Pipeline segment classification tests
# ---------------------------------------------------------------------------


def test_segment_churned():
    assert classify_pipeline_segment("finalizado", 0, 500, 1000, 1) == "churned"


def test_segment_upsell_opportunity():
    assert (
        classify_pipeline_segment("ativo", 500, 2000, 1000, 1) == "upsell_opportunity"
    )


def test_segment_loyal():
    assert classify_pipeline_segment("ativo", 500, 100, 1000, 5) == "loyal"


def test_segment_active():
    assert classify_pipeline_segment("ativo", 200, 100, 300, 1) == "active"


# ---------------------------------------------------------------------------
# PII scrubber tests — standard formats
# ---------------------------------------------------------------------------


def test_scrubber_removes_cpf_formatted():
    """CPF with dots and dash: 123.456.789-00"""
    result = scrub_pii("Patient CPF: 123.456.789-00 wants implant")
    assert "123.456.789-00" not in result
    assert "[CPF_REDACTED]" in result


def test_scrubber_removes_cpf_unformatted():
    """CPF without formatting: 12345678900"""
    result = scrub_pii("CPF 12345678900 informed")
    assert "12345678900" not in result


def test_scrubber_removes_credit_card_with_spaces():
    """Card number with spaces: 4111 1111 1111 1111"""
    result = scrub_pii("Card 4111 1111 1111 1111 informed")
    assert "4111 1111 1111 1111" not in result
    assert "[CARD_REDACTED]" in result


def test_scrubber_removes_credit_card_with_hyphens():
    """Card number with hyphens: 4111-1111-1111-1111"""
    result = scrub_pii("Card 4111-1111-1111-1111 informed")
    assert "4111-1111-1111-1111" not in result
    assert "[CARD_REDACTED]" in result


def test_scrubber_removes_credit_card_no_separator():
    """Card number without separator: 4111111111111111"""
    result = scrub_pii("Card 4111111111111111 informed")
    assert "4111111111111111" not in result


def test_scrubber_removes_phone_full_international():
    """Full international format: +5581999998888"""
    result = scrub_pii("Call me at +5581999998888")
    assert "999998888" not in result
    assert "[PHONE_REDACTED]" in result


def test_scrubber_removes_phone_without_plus():
    """Without plus sign: 5581999998888"""
    result = scrub_pii("My number is 5581999998888")
    assert "999998888" not in result


def test_scrubber_removes_phone_without_country_code():
    """Without country code: 81999998888 or (81) 99999-8888"""
    result = scrub_pii("Call (81) 99999-8888 please")
    assert "99999-8888" not in result


def test_scrubber_removes_phone_without_hyphen():
    """Without hyphen: 99999 8888"""
    result = scrub_pii("My number 81 99999 8888")
    assert "99999 8888" not in result


def test_scrubber_removes_phone_bare_digits():
    """Bare digits only: 81999998888"""
    result = scrub_pii("Phone 81999998888 here")
    assert "81999998888" not in result


def test_scrubber_removes_email():
    """Standard email address"""
    result = scrub_pii("Email: joao@gmail.com for confirmation")
    assert "joao@gmail.com" not in result
    assert "[EMAIL_REDACTED]" in result


def test_scrubber_removes_pix_uuid():
    """PIX key in UUID format"""
    result = scrub_pii("PIX: 123e4567-e89b-12d3-a456-426614174000")
    assert "123e4567-e89b-12d3-a456-426614174000" not in result
    assert "[PIX_REDACTED]" in result


# ---------------------------------------------------------------------------
# PII scrubber edge case tests — known limitations documented
# ---------------------------------------------------------------------------


def test_scrubber_preserves_clinical_content():
    """Scrubber must not destroy legitimate clinical information."""
    text = "Patient needs implant on tooth 36. Afraid of needles. Budget R$ 5000."
    result = scrub_pii(text)
    assert "implant" in result
    assert "tooth 36" in result
    assert "needles" in result


def test_scrubber_handles_none():
    """None input must return None — no crash."""
    assert scrub_pii(None) is None


def test_scrubber_handles_empty_string():
    """Empty string must return empty string — no crash."""
    assert scrub_pii("") == ""


def test_scrubber_multiple_pii_in_same_text():
    """Multiple PII types in same message must all be redacted."""
    text = "CPF 123.456.789-00, phone +5581999998888, email test@test.com"
    result = scrub_pii(text)
    assert "123.456.789-00" not in result
    assert "999998888" not in result
    assert "test@test.com" not in result


# ---------------------------------------------------------------------------
# LGPD guardrail tests
# ---------------------------------------------------------------------------


def test_hash_output_contains_no_pii_patterns():
    """Hash must never accidentally contain CPF or phone patterns."""
    cpf_pattern = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
    phone_pattern = re.compile(r"\+?\d{10,13}")
    result = hash_id("123.456.789-00", "salt")
    assert not cpf_pattern.search(result)
    assert not phone_pattern.search(result)
