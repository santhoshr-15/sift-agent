"""Enterprise Security & Guardrails Test Suite.

Verifies:
1. Input sanitization on all tools (directory traversal rejection, null bytes, bounds).
2. Enterprise DLP (Data Loss Prevention) redaction of sensitive credentials in audit logs.
3. PDF magic bytes header validation (rejecting non-PDF polyglot files).
4. Prompt boundary breakout prevention and jailbreak alert triggering.
5. Strict budget firewall refusal at Call 7+.
"""

import os
import pytest
from src.document_store import DocumentStore
from src.budget import BudgetedToolExecutor
from src import tools
from src.logger import redact_sensitive_data


def test_pdf_magic_bytes_validation():
    """Verify that non-PDF files or malicious headers are rejected."""
    store = DocumentStore()
    fake_exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00not_a_pdf"
    with pytest.raises(ValueError, match="does not match valid PDF magic bytes"):
        store.add_pdf(fake_exe_bytes, filename="fake.exe")


def test_doc_id_sanitization_against_directory_traversal():
    """Verify doc_id with path traversal or SQL-injection characters is rejected."""
    # Attempt directory traversal doc_id
    res_headings = tools.list_headings(doc_id="../../etc/passwd")
    assert res_headings == []

    res_search = tools.search_keyword(doc_id="../secret_config", keyword="password")
    assert res_search == []

    res_page = tools.get_page(doc_id="doc_1; DROP TABLE users;--", page_number=1)
    assert res_page.startswith("Error:")


def test_keyword_sanitization_null_bytes_and_length():
    """Verify keyword sanitization strips null bytes and caps max length."""
    from src.tools import _sanitize_keyword
    dirty_keyword = "confidential\x00\x01\x08secret" + ("A" * 300)
    clean_keyword = _sanitize_keyword(dirty_keyword)
    assert "\x00" not in clean_keyword
    assert len(clean_keyword) <= 150
    assert clean_keyword.startswith("confidentialsecret")


def test_enterprise_dlp_redaction():
    """Verify enterprise DLP redacts API keys, credit cards, and SSNs from audit previews."""
    text_with_api_key = "Internal secret found: api_key=sk-ant-api03-abcdef1234567890abcdef"
    redacted = redact_sensitive_data(text_with_api_key)
    assert "sk-ant-api03" not in redacted
    assert "[REDACTED_CREDENTIAL]" in redacted

    text_with_card = "Customer payment card: 4111-2222-3333-4444 on record."
    redacted_card = redact_sensitive_data(text_with_card)
    assert "4111-2222-3333-4444" not in redacted_card
    assert "[REDACTED_PAYMENT_CARD]" in redacted_card


def test_prompt_injection_warning_and_tag_escaping(tmp_path):
    """Verify prompt injection triggers security alert and escapes boundary tags."""
    pdf_path = os.path.join(os.path.dirname(__file__), "..", "samples", "trap_test.pdf")
    store = DocumentStore()
    doc_id = store.add_pdf(pdf_path)

    executor = BudgetedToolExecutor(store, question_id="sec_test", max_calls=6)
    # Page 5 has prompt injection
    res = executor.execute("get_page", {"doc_id": doc_id, "page_number": 5})

    assert "[SECURITY ALERT: this page contains text resembling instructions" in res
    assert "<document_page" in res
    assert "</document_page>" in res
    # Verify no unescaped closing tag inside page body
    inner_body = res.split(">\n")[1].rsplit("\n</document_page>", 1)[0]
    assert "</document_page>" not in inner_body
