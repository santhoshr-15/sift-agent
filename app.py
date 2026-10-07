"""Streamlit Chat Interface for Glean Agent.

Port: 8502 (Antigravity)
Features:
- PDF upload and single-pass parsing
- Sidebar document metadata (TOC found vs heuristic headings)
- Session trace JSON export
- Chat history with status badges, cited pages, notes, and call traces
- Hard budget enforcement visualization (X/6 calls)
"""

from __future__ import annotations
import html
import json
import os
import streamlit as st
import dotenv

dotenv.load_dotenv()

# Bridge Streamlit Cloud Secrets into os.environ
try:
    if hasattr(st, "secrets"):
        for key in ["GEMINI_API_KEY", "GEMINI_MODEL", "ANTHROPIC_API_KEY"]:
            if key in st.secrets and key not in os.environ:
                os.environ[key] = str(st.secrets[key])
except Exception:
    pass

from src.document_store import DocumentStore
from src.agent import answer_question

# Page Configuration
st.set_page_config(
    page_title="Glean Agent",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for rich aesthetics and badges
st.markdown("""
<style>
    .badge-answered {
        background: linear-gradient(135deg, #10b981 0%, #059669 100%);
        color: white;
        padding: 4px 12px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 600;
        display: inline-block;
        margin-bottom: 8px;
    }
    .badge-insufficient {
        background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
        color: white;
        padding: 4px 12px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 600;
        display: inline-block;
        margin-bottom: 8px;
    }
    .trace-item {
        background-color: rgba(255, 255, 255, 0.05);
        border-left: 3px solid #6366f1;
        padding: 8px 12px;
        margin-bottom: 6px;
        border-radius: 0 6px 6px 0;
        font-family: monospace;
        font-size: 0.85rem;
    }
    .grounding-box {
        background-color: rgba(16, 185, 129, 0.1);
        border: 1px solid rgba(16, 185, 129, 0.3);
        border-radius: 6px;
        padding: 8px 12px;
        margin-top: 8px;
        font-size: 0.85rem;
    }
    .grounding-box-fail {
        background-color: rgba(239, 68, 68, 0.1);
        border: 1px solid rgba(239, 68, 68, 0.3);
        border-radius: 6px;
        padding: 8px 12px;
        margin-top: 8px;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# Initialize Session State
if "messages" not in st.session_state:
    st.session_state.messages = []
if "all_traces" not in st.session_state:
    st.session_state.all_traces = []
if "doc_store" not in st.session_state:
    st.session_state.doc_store = DocumentStore()
if "current_file_id" not in st.session_state:
    st.session_state.current_file_id = None
if "doc_id" not in st.session_state:
    st.session_state.doc_id = None


# --- SIDEBAR ---
with st.sidebar:
    st.title("Glean Agent")
    st.caption("Budgeted Tool Calling · Hard 6-Call Limit · Zero-RAG")
    st.divider()

    # API Key Configuration Fallback
    if not os.environ.get("GEMINI_API_KEY"):
        st.warning("⚠ GEMINI_API_KEY is missing")
        key_input = st.text_input(
            "Enter Gemini API Key",
            type="password",
            help="Add GEMINI_API_KEY in Streamlit Cloud Secrets or paste it here."
        )
        if key_input:
            os.environ["GEMINI_API_KEY"] = key_input.strip()
            st.success("API Key saved!")
            st.rerun()
        st.divider()

    uploaded_file = st.file_uploader("Upload Document (PDF)", type=["pdf"], key="pdf_uploader")

    # Handle New Document Upload
    if uploaded_file is not None:
        file_id = f"{uploaded_file.name}_{uploaded_file.size}"
        if st.session_state.current_file_id != file_id:
            # WHY: Uploading a new PDF strictly resets all session state, chat history, and document store.
            st.session_state.doc_store.clear()
            st.session_state.messages = []
            st.session_state.all_traces = []
            st.session_state.current_file_id = file_id

            with st.spinner("Parsing document structure once..."):
                file_bytes = uploaded_file.read()
                doc_id = st.session_state.doc_store.add_pdf(file_bytes, filename=uploaded_file.name)
                st.session_state.doc_id = doc_id
            st.success("Document parsed and indexed!")
            st.rerun()

    # Display Document Metadata
    if st.session_state.doc_id:
        try:
            doc_data = st.session_state.doc_store.get_document(st.session_state.doc_id)
            st.subheader("Document Details")
            st.markdown(f"**Title:** {doc_data['title']}")
            st.markdown(f"**Pages:** {doc_data['num_pages']}")
            
            if doc_data.get("has_native_toc"):
                st.success("TOC: Found (Native Outline)")
            else:
                st.info("TOC: Heuristic Headings (Font Extracted)")

            st.divider()
        except KeyError:
            st.warning("Document not loaded.")
    else:
        st.info("Upload a PDF to activate the agent.")

    # Trace Download Button
    if st.session_state.all_traces:
        trace_json = json.dumps(st.session_state.all_traces, indent=2, ensure_ascii=False)
        st.download_button(
            label="Download Trace (JSON)",
            data=trace_json,
            file_name="session_trace.json",
            mime="application/json",
            use_container_width=True
        )

    # Clear Chat Button
    if st.button("Clear Chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.all_traces = []
        st.rerun()

    st.markdown("---")
    st.markdown("""
    **Hard Rules Enforced:**
    - Max 6 tool calls per question
    - Keyword search only (No RAG / vectors)
    - Untrusted data tags & prompt injection warnings
    - Post-generation grounding validation
    """)


# --- MAIN AREA ---
st.title("Glean Agent")
st.write("Answers questions using a handwritten agent loop with native tool calling within a hard 6-call budget.")

# Display Chat Messages
for msg in st.session_state.messages:
    if msg["role"] == "user":
        with st.chat_message("user"):
            st.write(msg["content"])
    else:
        with st.chat_message("assistant"):
            status = msg.get("status", "answered")
            if status == "answered":
                st.markdown('<span class="badge-answered">✓ Answered</span>', unsafe_allow_html=True)
            else:
                st.markdown('<span class="badge-insufficient">⚠ Insufficient Information</span>', unsafe_allow_html=True)

            st.markdown(msg["content"])

            # Cited Pages & Notes
            cited = msg.get("cited_pages", [])
            if cited:
                st.markdown(f"**Cited Pages:** `{cited}`")

            evidence = msg.get("evidence_quotes", [])
            if evidence:
                with st.expander("Verbatim Evidence Quotes"):
                    for q in evidence:
                        st.markdown(f"> *\"{q}\"*")

            notes = msg.get("notes")
            if notes:
                st.caption(f"**Notes:** {notes}")

            # Execution Trace Expander
            calls_used = msg.get("calls_used", 0)
            trace = msg.get("trace", [])
            with st.expander(f"Agent Trace ({calls_used}/6 calls)"):
                if not trace:
                    st.write("No tools were called for this response.")
                else:
                    for t in trace:
                        allowed_str = "ALLOWED" if t.get("allowed", True) else "REFUSED"
                        tool_escaped = html.escape(str(t.get("tool", "")))
                        args_escaped = html.escape(str(t.get("args", "")))
                        preview_escaped = html.escape(str(t.get("result_preview", ""))[:180])
                        st.markdown(f"""
                        <div class="trace-item">
                            <b>Call #{t.get('call_number')}:</b> [{allowed_str}] <code>{tool_escaped}</code>({args_escaped})<br/>
                            <span style="opacity: 0.8;">Preview: {preview_escaped}...</span>
                        </div>
                        """, unsafe_allow_html=True)

                # Grounding Check Result
                grounding = msg.get("grounding_check", {})
                reason_escaped = html.escape(str(grounding.get("reason", "Verified.")))
                if grounding.get("passed", True):
                    st.markdown(f'<div class="grounding-box"><b>Grounding Check: PASSED</b> — {reason_escaped}</div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div class="grounding-box-fail"><b>Grounding Check: FAILED</b> — {reason_escaped}</div>', unsafe_allow_html=True)


# User Question Input
if prompt := st.chat_input("Ask a question about the uploaded document...", disabled=not bool(st.session_state.doc_id)):
    # Render user prompt
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    # Run Agent
    with st.chat_message("assistant"):
        with st.spinner("Agent exploring document within budget (max 6 calls)..."):
            try:
                # WHY: Agent receives doc_store and executes question with zero cross-question state leakage.
                result = answer_question(st.session_state.doc_store, prompt)

                status = result["status"]
                answer = result["answer"]
                cited_pages = result["cited_pages"]
                evidence_quotes = result["evidence_quotes"]
                notes = result["notes"]
                calls_used = result["calls_used"]
                trace = result["trace"]
                grounding_check = result["grounding_check"]

                # Save trace record
                st.session_state.all_traces.append({
                    "question": prompt,
                    "result": result,
                })

                # Append assistant message to chat history
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer,
                    "status": status,
                    "cited_pages": cited_pages,
                    "evidence_quotes": evidence_quotes,
                    "notes": notes,
                    "calls_used": calls_used,
                    "trace": trace,
                    "grounding_check": grounding_check,
                })

                st.rerun()

            except Exception as e:
                # WHY: Catch and display errors cleanly in UI rather than letting the Streamlit server crash.
                st.error(f"Error processing question: {str(e)}")
