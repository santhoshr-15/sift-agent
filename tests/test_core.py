"""Unit tests for the Glean Agent core components.

Tests cover:
1. 7th tool call is refused and not executed
2. New question gets fresh state (no cross-question cache)
3. search_keyword returns only a sorted list of ints
4. get_page returns exactly one page; out of range returns an error string
5. Grounding check downgrades answers citing unread pages or fabricated quotes
"""

import os
import pytest
from src.document_store import DocumentStore
from src.budget import BudgetedToolExecutor
from src import tools
from src.agent import perform_grounding_check


@pytest.fixture(scope="module")
def sample_pdf_path():
    """Ensure sample trap PDF exists and return path."""
    path = os.path.join(os.path.dirname(__file__), "..", "samples", "trap_test.pdf")
    if not os.path.exists(path):
        from make_test_pdf import create_trap_test_pdf
        create_trap_test_pdf(path)
    return path


@pytest.fixture
def loaded_store(sample_pdf_path):
    """Provide a fresh DocumentStore loaded with the trap test PDF."""
    store = DocumentStore()
    doc_id = store.add_pdf(sample_pdf_path)
    return store, doc_id


def test_budget_exhaustion_at_seventh_call(loaded_store):
    """Verify that a 7th tool call is strictly refused and not executed."""
    store, doc_id = loaded_store
    executor = BudgetedToolExecutor(store, question_id="test_q1", max_calls=6)

    # First 6 calls must succeed
    for i in range(1, 7):
        page_to_get = (i % 8) + 1
        res = executor.execute("get_page", {"doc_id": doc_id, "page_number": page_to_get})
        assert not res.startswith("BUDGET_EXHAUSTED"), f"Call {i} should be permitted."
        assert executor.trace[-1]["allowed"] is True
        assert executor.calls_used == i

    # 7th call must be refused and not executed
    seventh_res = executor.execute("get_page", {"doc_id": doc_id, "page_number": 1})
    assert "BUDGET_EXHAUSTED" in seventh_res
    assert executor.trace[-1]["allowed"] is False
    assert executor.calls_used == 7
    # Verify no 7th page was added to read_pages_content or modified
    assert len(executor.trace) == 7


def test_fresh_state_per_question(loaded_store):
    """Verify that each question gets completely fresh state without cross-question caching."""
    store, doc_id = loaded_store

    # Session 1: reads page 2
    executor1 = BudgetedToolExecutor(store, question_id="q1", max_calls=6)
    executor1.execute("get_page", {"doc_id": doc_id, "page_number": 2})
    assert 2 in executor1.pages_read
    assert executor1.calls_used == 1
    assert len(executor1.trace) == 1

    # Session 2: completely fresh state
    executor2 = BudgetedToolExecutor(store, question_id="q2", max_calls=6)
    assert len(executor2.pages_read) == 0, "New question must start with empty pages_read."
    assert executor2.calls_used == 0, "New question must start with 0 calls used."
    assert len(executor2.trace) == 0, "New question must start with empty trace."


def test_search_keyword_returns_only_int_list(loaded_store):
    """Verify search_keyword returns sorted list of page numbers ONLY."""
    store, doc_id = loaded_store

    # Match search
    hits = tools.search_keyword(store, doc_id, "Falcon")
    assert isinstance(hits, list), "Result must be a list."
    assert len(hits) > 0, "Should match 'Falcon' in document."
    for p in hits:
        assert isinstance(p, int), f"Item {p} must be an int page number, not a string or dict."
    assert hits == sorted(hits), "Page numbers must be sorted in ascending order."

    # Non-match search returns empty list
    no_hits = tools.search_keyword(store, doc_id, "NonExistentTermXYZ123")
    assert no_hits == []


def test_get_page_single_page_and_out_of_range(loaded_store):
    """Verify get_page returns exactly one page and returns error string on invalid range."""
    store, doc_id = loaded_store

    # Valid page
    page_text = tools.get_page(store, doc_id, 1)
    assert isinstance(page_text, str)
    assert not page_text.startswith("Error:"), "Valid page should not return error."
    assert "Executive Overview" in page_text

    # Out of range pages
    err_high = tools.get_page(store, doc_id, 999)
    assert isinstance(err_high, str)
    assert err_high.startswith("Error:"), "Page 999 out of range should return error string."

    err_low = tools.get_page(store, doc_id, 0)
    assert isinstance(err_low, str)
    assert err_low.startswith("Error:"), "Page 0 out of range should return error string."


def test_grounding_check_downgrades_unread_or_fabricated():
    """Verify grounding check downgrades hallucinated citations directly without LLM calls."""
    pages_read = {2, 3}
    read_pages_content = {
        2: "The initial budget is $45,000,000 for fiscal year 2026.",
        3: "The primary database cluster is deployed across North America and Europe."
    }

    # Case A: Cited an unread page (Page 7 was not read)
    status_a, info_a = perform_grounding_check(
        status="answered",
        cited_pages=[2, 7],
        evidence_quotes=["$45,000,000"],
        pages_read=pages_read,
        read_pages_content=read_pages_content
    )
    assert status_a == "insufficient_information", "Must downgrade when citing unread page 7."
    assert info_a["passed"] is False

    # Case B: Fabricated quote not in read pages
    status_b, info_b = perform_grounding_check(
        status="answered",
        cited_pages=[2],
        evidence_quotes=["This quantum initiative has received two hundred billion in gold bullion."],
        pages_read=pages_read,
        read_pages_content=read_pages_content
    )
    assert status_b == "insufficient_information", "Must downgrade when evidence quote is fabricated."
    assert info_b["passed"] is False

    # Case C: Legitimate quote in read pages passes
    status_c, info_c = perform_grounding_check(
        status="answered",
        cited_pages=[2],
        evidence_quotes=["initial budget is $45,000,000"],
        pages_read=pages_read,
        read_pages_content=read_pages_content
    )
    assert status_c == "answered", "Legitimate quote should pass grounding check."
    assert info_c["passed"] is True
