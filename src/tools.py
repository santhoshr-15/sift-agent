"""Tools module with Enterprise Guardrails, Auto-Resolving Doc IDs, & Normalization.

Contains exactly the 4 required functions for the document-answering agent:
- list_documents()
- list_headings(doc_id)
- get_page(doc_id, page_number)
- search_keyword(doc_id, keyword)

All functions strictly return only their defined schemas and enforce enterprise sanitization:
- doc_id format validation (prevents directory traversal & injection)
- auto-resolution for single active uploaded document if doc_id is omitted or generic
- keyword length bounds, null-byte stripping, and unicode normalization
- page_number type enforcement and bounds checking
"""

from __future__ import annotations
import re
import unicodedata
from typing import Any, Dict, List, Optional
from src.document_store import DocumentStore

# Global active document store instance for convenience when tools are invoked directly
_GLOBAL_STORE: Optional[DocumentStore] = None

# Enterprise Input Guardrail Limits
DOC_ID_REGEX = re.compile(r'^[a-zA-Z0-9_\-]{1,64}$')
MAX_KEYWORD_LENGTH = 150


def set_global_store(store: DocumentStore) -> None:
    """Set the active document store for standalone tool invocations."""
    global _GLOBAL_STORE
    _GLOBAL_STORE = store


def get_active_store(explicit_store: Optional[DocumentStore] = None) -> DocumentStore:
    """Resolve the active store instance."""
    store = explicit_store or _GLOBAL_STORE
    if store is None:
        raise RuntimeError("No DocumentStore provided and no global store set.")
    return store


def _resolve_and_validate_doc_id(raw_doc_id: Any, store: Optional[DocumentStore]) -> Optional[str]:
    """Validate and auto-resolve doc_id.
    
    WHY: In a single-document chat session, LLMs occasionally omit doc_id or pass a placeholder.
    Auto-resolving to the sole loaded document prevents unnecessary tool failures while maintaining
    strict directory traversal security checks.
    """
    # 1. If raw_doc_id is provided, strictly validate its regex pattern first
    if raw_doc_id is not None:
        if not isinstance(raw_doc_id, str):
            return None
        cleaned = raw_doc_id.strip()
        if not DOC_ID_REGEX.match(cleaned):
            # Security rejection: invalid format, directory traversal, or injection chars
            return None
        # If valid format, check if store knows it
        if store is not None:
            try:
                available_docs = store.list_documents()
                available_ids = {d["doc_id"] for d in available_docs}
                if cleaned in available_ids:
                    return cleaned
                if len(available_docs) == 1:
                    return available_docs[0]["doc_id"]
            except Exception:
                pass
        return cleaned

    # 2. If raw_doc_id is None / empty, auto-resolve if exactly 1 document in store
    if store is not None:
        try:
            available_docs = store.list_documents()
            if len(available_docs) == 1:
                return available_docs[0]["doc_id"]
        except Exception:
            pass

    return None


def _sanitize_keyword(keyword: Any) -> str:
    """Strip null bytes, control characters, and enforce maximum keyword length."""
    if not keyword:
        return ""
    s = str(keyword)
    # Strip null bytes and control chars (except standard whitespace)
    s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s)
    return s[:MAX_KEYWORD_LENGTH].strip()


def _normalize_text_for_search(text: str) -> str:
    """Normalize whitespace, line breaks, accents, and logic/math symbols for robust matching."""
    # De-hyphenate line breaks (e.g. "com-\nmittee" -> "committee")
    no_hyphens = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', text)
    # Normalize unicode (NFC/NFKD) to handle accents and special glyphs
    normalized = unicodedata.normalize('NFKD', no_hyphens)
    # Replace overbar/negation symbols commonly used in logic/probability
    normalized = re.sub(r'[¯~¬]', '', normalized)
    # Collapse all whitespace and newlines to a single space, lowercase
    return re.sub(r'\s+', ' ', normalized).strip().lower()


def list_documents(doc_store: Optional[DocumentStore] = None) -> List[Dict[str, Any]]:
    """List all documents in the store.
    
    Returns:
        [{doc_id, title, num_pages, metadata}] only
    """
    try:
        store = get_active_store(doc_store)
        docs = store.list_documents()
        return [
            {
                "doc_id": d["doc_id"],
                "title": d["title"],
                "num_pages": d["num_pages"],
                "metadata": d["metadata"],
            }
            for d in docs
        ]
    except RuntimeError:
        return []


def list_headings(
    *args: Any,
    **kwargs: Any
) -> List[Dict[str, Any]]:
    """List headings/table of contents for a document.
    
    Supports list_headings(doc_id) or list_headings(doc_store, doc_id).
    
    Returns:
        [{level, title, page}] only, capped at 150 entries.
    """
    store: Optional[DocumentStore] = kwargs.get("doc_store")
    raw_doc_id: Optional[str] = kwargs.get("doc_id")

    if args:
        if isinstance(args[0], DocumentStore):
            store = args[0]
            raw_doc_id = args[1] if len(args) > 1 else raw_doc_id
        else:
            raw_doc_id = args[0]
            if len(args) > 1 and isinstance(args[1], DocumentStore):
                store = args[1]

    target_store = store or _GLOBAL_STORE
    doc_id = _resolve_and_validate_doc_id(raw_doc_id, target_store)
    if not doc_id:
        return []

    try:
        resolved_store = get_active_store(store)
        doc = resolved_store.get_document(doc_id)
    except (KeyError, ValueError, RuntimeError):
        return []

    # 1. Try native TOC first
    raw_toc = doc.get("toc") or []
    headings: List[Dict[str, Any]] = []

    if raw_toc:
        for item in raw_toc:
            if len(item) >= 3:
                headings.append({
                    "level": int(item[0]),
                    "title": str(item[1]).strip()[:200],
                    "page": int(item[2])
                })
    else:
        # 2. Fall back to heuristic headings extracted during document store parsing
        heuristic = doc.get("heuristic_headings") or []
        for h in heuristic:
            headings.append({
                "level": int(h["level"]),
                "title": str(h["title"]).strip()[:200],
                "page": int(h["page"])
            })

    return headings[:150]


def get_page(
    *args: Any,
    **kwargs: Any
) -> str:
    """Get the text of exactly ONE 1-indexed page.
    
    Supports get_page(doc_id, page_number) or get_page(doc_store, doc_id, page_number).
    Out of range returns an error string (still counts as a call).
    """
    store: Optional[DocumentStore] = kwargs.get("doc_store")
    raw_doc_id: Optional[str] = kwargs.get("doc_id")
    page_number: Optional[int] = kwargs.get("page_number")

    if args:
        if isinstance(args[0], DocumentStore):
            store = args[0]
            if len(args) > 1:
                raw_doc_id = args[1]
            if len(args) > 2:
                page_number = args[2]
        else:
            raw_doc_id = args[0]
            if len(args) > 1:
                page_number = args[1]
            if len(args) > 2 and isinstance(args[2], DocumentStore):
                store = args[2]

    target_store = store or _GLOBAL_STORE
    doc_id = _resolve_and_validate_doc_id(raw_doc_id, target_store)
    if not doc_id:
        return "Error: Invalid or missing doc_id identifier."

    try:
        resolved_store = get_active_store(store)
    except RuntimeError:
        return "Error: No active DocumentStore found."

    try:
        page_num_int = int(page_number)  # type: ignore
    except (TypeError, ValueError):
        return f"Error: Invalid page number '{page_number}'. Must be an integer."

    try:
        return resolved_store.get_page_text(doc_id, page_num_int)
    except (IndexError, KeyError, ValueError) as e:
        return f"Error: {str(e)}"


def search_keyword(
    *args: Any,
    **kwargs: Any
) -> List[int]:
    """Search for keyword in document. Case-insensitive with whitespace and unicode normalization.
    
    Supports search_keyword(doc_id, keyword) or search_keyword(doc_store, doc_id, keyword).
    
    Returns:
        sorted list of page numbers ONLY (1-indexed). Returns [] if no match.
    """
    store: Optional[DocumentStore] = kwargs.get("doc_store")
    raw_doc_id: Optional[str] = kwargs.get("doc_id")
    raw_keyword: Optional[str] = kwargs.get("keyword")

    if args:
        if isinstance(args[0], DocumentStore):
            store = args[0]
            if len(args) > 1:
                raw_doc_id = args[1]
            if len(args) > 2:
                raw_keyword = args[2]
        else:
            raw_doc_id = args[0]
            if len(args) > 1:
                raw_keyword = args[1]
            if len(args) > 2 and isinstance(args[2], DocumentStore):
                store = args[2]

    target_store = store or _GLOBAL_STORE
    doc_id = _resolve_and_validate_doc_id(raw_doc_id, target_store)
    if not doc_id:
        return []

    clean_keyword = _sanitize_keyword(raw_keyword)
    if not clean_keyword:
        return []

    norm_query = _normalize_text_for_search(clean_keyword)
    if not norm_query:
        return []

    try:
        resolved_store = get_active_store(store)
        doc = resolved_store.get_document(doc_id)
    except (KeyError, ValueError, RuntimeError):
        return []

    matching_pages: List[int] = []
    num_pages = doc["num_pages"]

    for page_num in range(1, num_pages + 1):
        raw_text = doc["pages"].get(page_num, "")
        norm_text = _normalize_text_for_search(raw_text)
        if norm_query in norm_text:
            matching_pages.append(page_num)

    return sorted(matching_pages)
