"""Command-line interface for Glean Agent.

Usage:
    python cli.py path/to.pdf "your question here"
"""

from __future__ import annotations
import os
import sys
from src.document_store import DocumentStore
from src.agent import answer_question


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python cli.py <path_to_pdf> \"<question>\"")
        sys.exit(1)

    pdf_path = sys.argv[1]
    question = sys.argv[2]

    if not os.path.exists(pdf_path):
        print(f"Error: File not found at '{pdf_path}'")
        sys.exit(1)

    print(f"Loading PDF: {pdf_path}")
    store = DocumentStore()
    doc_id = store.add_pdf(pdf_path)
    doc_meta = store.get_document(doc_id)
    print(f"Loaded '{doc_meta['title']}' ({doc_meta['num_pages']} pages, id={doc_id})")
    print(f"Asking: \"{question}\"\n")

    result = answer_question(store, question)

    print("=" * 60)
    print(f"STATUS:          {result['status'].upper()}")
    print(f"CALLS USED:      {result['calls_used']}/6")
    print(f"CITED PAGES:     {result['cited_pages']}")
    print("-" * 60)
    print(f"ANSWER:\n{result['answer']}")
    print("-" * 60)
    if result["evidence_quotes"]:
        print(f"EVIDENCE QUOTES: {result['evidence_quotes']}")
    if result["notes"]:
        print(f"NOTES:           {result['notes']}")
    print(f"GROUNDING:       {result['grounding_check']}")
    print("=" * 60)
    print("EXECUTION TRACE:")
    for step in result["trace"]:
        status_tag = "ALLOWED" if step["allowed"] else "REFUSED"
        print(f"  [{step['call_number']}] {status_tag} - {step['tool']}({step['args']})")
        print(f"      Result preview: {step['result_preview'][:100]}...")
    print("=" * 60)


if __name__ == "__main__":
    main()
