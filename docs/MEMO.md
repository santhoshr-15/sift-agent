# Architecture & Design Memo: Sift Agent

## 1. System Architecture
The application coordinates seven purpose-built components in a unidirectional pipeline designed to strictly eliminate cross-question state leakage and enforce a hard resource ceiling:

```
[PDF Upload]
     │ (Magic-byte %PDF- & size validation)
     ▼
[DocumentStore]  <─── Parses ONCE: Text, TOC, Heuristic Headings, Bordered/Borderless Tables,
     │                Figure/Caption references, and On-Demand Multimodal Vision Inspection
     ▼
[Tools Layer]    <─── 4 isolated tools: list_documents, list_headings, get_page, search_keyword
     │                Auto-resolves doc_id, sanitizes input, normalizes math/logic symbols
     ▼
[BudgetExecutor] <─── Hard 6-call firewall, injection warnings, pages_read tracking, DLP redaction
     │
     ▼
[Agent Loop]     <─── Plain while-loop (Zero-RAG, no frameworks) with native tool calling
     │
     ▼
[Final Answer]   <─── Enforces status, answer, cited_pages, evidence_quotes, notes
     │
     ▼
[Grounding Check]<─── Verifies cited pages read & quotes exist in read corpus (LaTeX/table-normalized)
     │
     ▼
[Streamlit UI]   <─── Chat interface with status badges, trace breakdown, XSS escaping & JSON export
```

## 2. Key Design Decisions & Rationale
1. **Hard Budget Enforced in Code (Not Prompting)**: Prompting an LLM to stop at 6 calls is unreliable under complex tasks. The `BudgetedToolExecutor` enforces a strict counter gate at call 7+, refusing execution and logging an unallowed attempt without spending system resources.
2. **Skipping `list_documents` in Default Flow**: Because the system passes `doc_id`, title, and page count in the initial prompt metadata, spending 1 of only 6 budget calls on `list_documents` is wasteful.
3. **Headings-Then-Search-Then-Read Strategy**: Rather than random scanning, the agent executes `list_headings` to map the outline, 1–2 distinctive `search_keyword` queries to identify candidate pages, and then inspects targeted pages with `get_page`.
4. **Structured Table & Borderless Alignment Extraction**: Raw text extraction flattens 2D tables into disjointed vertical strings. The system combines PyMuPDF `find_tables()` for bordered tables with 2D coordinate-based line clustering for borderless tables (like conditional probability tables, matrices, and listings), structuring them directly into clean Markdown tables.
5. **On-Demand Multimodal Vision for Figures, Graphs & Diagrams**: When `get_page` is invoked on pages containing visual media (drawings, plots, images), the system enriches the page text with detected captions and an on-demand Gemini Vision analysis detailing axes, labels, curves, trends, and diagram components.
6. **Untrusted Data Tagging & Injection Defense**: Content retrieved via `get_page` is enclosed inside `<document_page>` tags with closing tag escaping. Injection-like instructions trigger an automated prepended warning header instructing the LLM to treat instructions strictly as passive data.
7. **Post-Generation Grounding Verification with LaTeX/Table Normalization**: To stop confident hallucinations, code-level string and difflib similarity checks verify that all cited pages were read and all evidence quotes exist in the retrieved text. LaTeX math notation (`\bar{f}`, `\neg`), table pipes, and Unicode symbols are normalized to prevent false downgrades on technical expressions.
8. **No Cross-Question State Caching**: Every question instantiates a brand new `BudgetedToolExecutor` with empty `pages_read` and fresh traces, ensuring complete independence across inquiries.

## 3. Enterprise Confidential Data Guardrails & Tool Security
- **Magic-Byte Header Validation**: Verifies `%PDF-` binary magic bytes prior to opening, blocking malicious polyglot executables and corrupted uploads.
- **Strict Parameter Sanitization & Doc-ID Resolution**: `doc_id` is restricted to `^[a-zA-Z0-9_\-]{1,64}$`, thwarting path traversal attacks (`../../etc/passwd`). For single-document uploads, doc_ids are safely auto-resolved if omitted by the LLM. `keyword` strips null bytes (`\x00`) and control characters, capped at 150 characters to prevent ReDoS.
- **Enterprise DLP (Data Loss Prevention) Redaction**: Automatically redacts API credentials, bearer tokens, credit cards, and SSNs from audit previews before appending to persistent logs (`logs/tool_calls.jsonl`).
- **Jailbreak & Indirect Injection Detection**: Regex scans for developer mode, instruction overrides, and roleplay bypasses, alerting the LLM with security headers and escaping XML tags to prevent prompt breakout.
- **Cross-Site Scripting (XSS) Sanitization**: All tool names, parameters, result previews, and grounding diagnostics are HTML-escaped before rendering in custom UI components.

## 4. Known Failure Modes & Production Solutions
- **Purely Scanned PDFs with Low DPI Bitmaps**: Very low resolution scans without OCR.
  *Solution*: Integrate local Tesseract OCR or cloud document OCR when page text length is near zero.
- **Keyword Search Missing Paraphrases/Synonyms**: Strict exact string matching fails when documents use synonyms (e.g., "remuneration" vs "salary").
  *Solution*: Implement a lightweight pre-tool query-expansion step where the LLM produces 2–3 canonical synonyms before executing keyword searches.
- **Information Discovery in 1000+ Page Encyclopedic Documents**: An arbitrary 6-call budget cannot exhaustively search deeply buried facts across thousands of pages without hierarchical index partitioning.
  *Solution*: Partition document outlines into chapter-level indices and dynamically focus candidate page sets before drilling down with `get_page`.
