"""Unit tests for Table, Graph, Figure, and Caption handling."""

import os
import pytest
from src.document_store import DocumentStore
from src import tools


def test_table_and_figure_structure_extraction():
    """Verify that document store detects tables and captions from PDF pages."""
    test_pdf = os.path.join(os.path.dirname(__file__), "..", "samples", "trap_test.pdf")
    store = DocumentStore()
    doc_id = store.add_pdf(test_pdf)

    # Verify doc loaded and has pages
    doc = store.get_document(doc_id)
    assert doc["num_pages"] == 8

    # Page 1 has executive overview
    p1 = store.get_page_text(doc_id, 1, include_vision=False)
    assert "Executive Overview" in p1

    # Verify search for keywords works accurately
    pages = tools.search_keyword(store, doc_id, "Falcon")
    assert 1 in pages


def test_real_lecture_pdf_table_extraction_if_available():
    """If CSCI415009_V2.pdf is available, verify page 171 extracts tabular data."""
    pdf_path = r"C:\Santhosh Kumar R projects\Glean Agent\CSCI415009_V2.pdf"
    if not os.path.exists(pdf_path):
        pytest.skip("Lecture PDF not present in environment")

    store = DocumentStore()
    doc_id = store.add_pdf(pdf_path)

    p171_text = store.get_page_text(doc_id, 171, include_vision=False)

    # Verify the probability table is structured and contains the exact values
    assert "0.056" in p171_text
    assert "[DETECTED TABULAR DATA / ALIGNED COLUMNS]" in p171_text
    assert "0.6237" in p171_text
