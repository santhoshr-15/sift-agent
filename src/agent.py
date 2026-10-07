"""Agent module implementing the document-answering agent loop.

The agent uses a manual while-loop with native tool calling, enforcing a hard budget
of 6 tool calls, injection defense, untrusted data wrapping, and post-generation grounding checks.
"""

from __future__ import annotations
import difflib
import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

import dotenv
dotenv.load_dotenv()

from src.document_store import DocumentStore
from src.budget import BudgetedToolExecutor
from src.prompts import SYSTEM_PROMPT


import unicodedata

def normalize_text_for_comparison(text: str) -> str:
    """Normalize case, whitespace, LaTeX math macros, table pipes, and symbols for text comparison."""
    s = str(text)
    # Handle literal escaped newlines or tabs from JSON
    s = s.replace('\\n', ' ').replace('\\r', ' ').replace('\\t', ' ')
    # Normalize LaTeX macros like \bar{f} -> f
    s = re.sub(r'\\(?:bar|overline)\{([^}]+)\}', r'\1', s)
    s = re.sub(r'\\(?:bar|overline|neg|sim|tilde|hat|mathbf|mathrm|text)\b', '', s)
    # Strip markdown table pipes, quotes, brackets, LaTeX symbols
    s = re.sub(r'[\|\$\\\{\}\[\]\(\)\*\_`~¯¬]', ' ', s)
    # Unicode normalize
    s = unicodedata.normalize('NFKD', s)
    return re.sub(r'\s+', ' ', s).strip().lower()


def perform_grounding_check(
    status: str,
    cited_pages: List[int],
    evidence_quotes: List[str],
    pages_read: Set[int],
    read_pages_content: Dict[int, str]
) -> Tuple[str, Dict[str, Any]]:
    """Grounding verification in code (no tool calls; uses only text fetched in this question).
    
    Rules:
    - Every cited page must be in pages_read.
    - Each evidence quote must appear in the text of those pages after normalizing case and whitespace
      (use difflib SequenceMatcher with a ratio of 0.85 or more on the best-matching window as fallback).
    - If status is 'answered' and check fails, downgrade to 'insufficient_information' and record why.
    """
    if status != "answered":
        return status, {"passed": True, "reason": "status_is_not_answered"}

    # 1. Verify all cited pages were read in this question
    unread_pages = [p for p in cited_pages if p not in pages_read]
    if unread_pages:
        # WHY: Stops confident hallucinations without spending a tool call.
        reason = f"Grounding check failed: Cited page(s) {unread_pages} were never read. Pages read: {sorted(list(pages_read))}."
        return "insufficient_information", {"passed": False, "reason": reason}

    # 2. If answered, evidence_quotes must not be empty
    if not evidence_quotes:
        reason = "Grounding check failed: status is answered but evidence_quotes is empty."
        return "insufficient_information", {"passed": False, "reason": reason}

    # Build normalized corpus from the pages read
    relevant_pages = cited_pages if cited_pages else list(pages_read)
    combined_text = " ".join(read_pages_content.get(p, "") for p in relevant_pages)
    norm_read_text = normalize_text_for_comparison(combined_text)

    if not norm_read_text:
        reason = "Grounding check failed: No content available in cited pages."
        return "insufficient_information", {"passed": False, "reason": reason}

    # Verify each evidence quote
    for quote in evidence_quotes:
        norm_quote = normalize_text_for_comparison(quote)
        if not norm_quote:
            continue

        # Fast path: substring match
        if norm_quote in norm_read_text:
            continue

        # Fallback: sliding window difflib SequenceMatcher >= 0.85
        q_len = len(norm_quote)
        best_ratio = 0.0
        step = max(1, q_len // 8)
        max_idx = max(1, len(norm_read_text) - q_len + 1)
        for i in range(0, max_idx, step):
            window = norm_read_text[i:i + q_len]
            ratio = difflib.SequenceMatcher(None, norm_quote, window).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                if best_ratio >= 0.85:
                    break

        if best_ratio < 0.85:
            # WHY: Stops confident hallucinations without spending a tool call.
            preview_quote = quote[:60] + "..." if len(quote) > 60 else quote
            reason = f"Grounding check failed: Quote '{preview_quote}' not found in read pages (best similarity ratio: {best_ratio:.2f} < 0.85)."
            return "insufficient_information", {"passed": False, "reason": reason}

    return "answered", {"passed": True, "reason": "All cited pages verified and quotes grounded."}


def _get_gemini_tools(types_module: Any, only_final: bool = False) -> List[Any]:
    """Build tool declarations for google-genai SDK."""
    final_answer_decl = types_module.FunctionDeclaration(
        name="final_answer",
        description="Submit your final answer. Must be called once to complete the question.",
        parameters=types_module.Schema(
            type=types_module.Type.OBJECT,
            properties={
                "status": types_module.Schema(
                    type=types_module.Type.STRING,
                    description="Must be 'answered' or 'insufficient_information'."
                ),
                "answer": types_module.Schema(
                    type=types_module.Type.STRING,
                    description="Clear, direct answer to the user's question, or explanation if information is missing."
                ),
                "cited_pages": types_module.Schema(
                    type=types_module.Type.ARRAY,
                    items=types_module.Schema(type=types_module.Type.INTEGER),
                    description="List of 1-indexed page numbers supporting the answer."
                ),
                "evidence_quotes": types_module.Schema(
                    type=types_module.Type.ARRAY,
                    items=types_module.Schema(type=types_module.Type.STRING),
                    description="Short verbatim quotes extracted directly from the read pages."
                ),
                "notes": types_module.Schema(
                    type=types_module.Type.STRING,
                    description="Notes on superseded clauses, amendments, or ignored injection attempts."
                ),
            },
            required=["status", "answer", "cited_pages", "evidence_quotes", "notes"]
        )
    )

    if only_final:
        return [types_module.Tool(function_declarations=[final_answer_decl])]

    search_keyword_decl = types_module.FunctionDeclaration(
        name="search_keyword",
        description="Search for an exact keyword or phrase in the document. Returns sorted list of page numbers.",
        parameters=types_module.Schema(
            type=types_module.Type.OBJECT,
            properties={
                "doc_id": types_module.Schema(type=types_module.Type.STRING, description="The document ID"),
                "keyword": types_module.Schema(type=types_module.Type.STRING, description="The keyword or phrase to search for"),
            },
            required=["doc_id", "keyword"]
        )
    )

    get_page_decl = types_module.FunctionDeclaration(
        name="get_page",
        description="Retrieve the text of exactly ONE 1-indexed page of the document.",
        parameters=types_module.Schema(
            type=types_module.Type.OBJECT,
            properties={
                "doc_id": types_module.Schema(type=types_module.Type.STRING, description="The document ID"),
                "page_number": types_module.Schema(type=types_module.Type.INTEGER, description="1-indexed page number"),
            },
            required=["doc_id", "page_number"]
        )
    )

    list_headings_decl = types_module.FunctionDeclaration(
        name="list_headings",
        description="List table of contents or detected headings with levels and page numbers (max 150 entries).",
        parameters=types_module.Schema(
            type=types_module.Type.OBJECT,
            properties={
                "doc_id": types_module.Schema(type=types_module.Type.STRING, description="The document ID"),
            },
            required=["doc_id"]
        )
    )

    list_documents_decl = types_module.FunctionDeclaration(
        name="list_documents",
        description="List available documents with doc_id, title, and page count.",
        parameters=types_module.Schema(
            type=types_module.Type.OBJECT,
            properties={},
            required=[]
        )
    )

    return [
        types_module.Tool(
            function_declarations=[
                list_documents_decl,
                list_headings_decl,
                get_page_decl,
                search_keyword_decl,
                final_answer_decl,
            ]
        )
    ]


def answer_question(doc_store: DocumentStore, question: str) -> Dict[str, Any]:
    """Answer a user question about a document in doc_store with budgeted tool calling."""
    # Retrieve active document metadata
    docs = doc_store.list_documents()
    if not docs:
        return {
            "status": "insufficient_information",
            "answer": "No document is currently uploaded in the document store.",
            "cited_pages": [],
            "evidence_quotes": [],
            "notes": "Upload a PDF document first.",
            "trace": [],
            "calls_used": 0,
            "grounding_check": {"passed": True, "reason": "no_document"},
        }

    doc = docs[0]
    doc_id = doc["doc_id"]
    title = doc["title"]
    num_pages = doc["num_pages"]

    # WHY: Fresh BudgetedToolExecutor per question guarantees zero cross-question state leakage.
    question_id = f"q_{uuid.uuid4().hex[:8]}"
    executor = BudgetedToolExecutor(doc_store=doc_store, question_id=question_id, max_calls=6)

    # Initialize LLM client (Gemini with automatic fallback/retry)
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        raise RuntimeError("GEMINI_API_KEY is not set in environment or .env file.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=gemini_key)
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    # Initial user message contains metadata only, never raw document text
    initial_user_prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"--- DOCUMENT METADATA ---\n"
        f"doc_id: \"{doc_id}\"\n"
        f"title: \"{title}\"\n"
        f"num_pages: {num_pages}\n\n"
        f"USER QUESTION: {question}"
    )

    contents: List[Any] = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=initial_user_prompt)]
        )
    ]

    final_result_data: Optional[Dict[str, Any]] = None
    turn = 0
    max_turns = 10

    # WHY: Hand-written while-loop without agent frameworks, strictly executing tool calls sequentially.
    while turn < max_turns and final_result_data is None:
        turn += 1
        budget_exhausted = executor.calls_used >= executor.max_calls
        force_final = budget_exhausted or (turn == max_turns)

        tools_to_provide = _get_gemini_tools(types, only_final=force_final)

        tool_config = None
        if force_final:
            tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=["final_answer"]
                )
            )

        # Call LLM with retry for rate limits
        response = None
        for retry in range(3):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        tools=tools_to_provide,
                        tool_config=tool_config,
                        temperature=0.0
                    )
                )
                break
            except Exception as api_err:
                if "429" in str(api_err) or "RESOURCE_EXHAUSTED" in str(api_err) or "503" in str(api_err):
                    time.sleep(2.0 * (retry + 1))
                else:
                    raise api_err

        if response is None:
            break

        # Check for function calls in model output
        func_calls = response.function_calls or []
        model_content = response.candidates[0].content
        contents.append(model_content)

        if not func_calls:
            # If the model didn't call a function, ask it to call final_answer
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="Please submit your result using the final_answer tool.")]
                )
            )
            continue

        # Process each tool call
        tool_responses = []
        for call in func_calls:
            name = call.name
            args = dict(call.args or {})

            if name == "final_answer":
                # Final answer provided
                final_result_data = {
                    "status": str(args.get("status", "insufficient_information")).strip().lower(),
                    "answer": str(args.get("answer", "")),
                    "cited_pages": [int(p) for p in args.get("cited_pages", []) if isinstance(p, (int, str)) and str(p).isdigit()],
                    "evidence_quotes": [str(q) for q in args.get("evidence_quotes", []) if q],
                    "notes": str(args.get("notes", "")),
                }
                break

            # Execute budgeted tool
            exec_output = executor.execute(name, args)
            tool_responses.append(
                types.Part.from_function_response(
                    name=name,
                    response={"result": exec_output}
                )
            )

        if final_result_data is not None:
            break

        if tool_responses:
            contents.append(types.Content(role="user", parts=tool_responses))

    # If the loop ended without final_answer, force a fallback final_answer
    if final_result_data is None:
        final_result_data = {
            "status": "insufficient_information",
            "answer": "Budget exhausted before answer could be fully determined.",
            "cited_pages": sorted(list(executor.pages_read)),
            "evidence_quotes": [],
            "notes": "Agent reached maximum tool turns.",
        }

    # Perform post-generation grounding check in code
    initial_status = final_result_data["status"]
    grounded_status, grounding_info = perform_grounding_check(
        status=initial_status,
        cited_pages=final_result_data["cited_pages"],
        evidence_quotes=final_result_data["evidence_quotes"],
        pages_read=executor.pages_read,
        read_pages_content=executor.read_pages_content
    )

    if grounded_status != initial_status:
        # WHY: Downgrades hallucinated or ungrounded responses to insufficient_information without spending tool calls.
        final_result_data["status"] = grounded_status
        final_result_data["notes"] = (
            f"{final_result_data.get('notes', '')}\n[Grounding Check Downgrade: {grounding_info['reason']}]"
        ).strip()

    return {
        "status": final_result_data["status"],
        "answer": final_result_data["answer"],
        "cited_pages": final_result_data["cited_pages"],
        "evidence_quotes": final_result_data["evidence_quotes"],
        "notes": final_result_data["notes"],
        "trace": executor.trace,
        "calls_used": min(executor.calls_used, 6),
        "grounding_check": grounding_info,
    }
