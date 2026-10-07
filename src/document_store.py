"""Document Store module with Enterprise Security & Multimodal Table/Figure Extraction.

Parses an uploaded PDF ONCE at upload into an internal store:
doc_id, title (metadata title or filename), num_pages, per-page structured text (PyMuPDF),
TOC via doc.get_toc(), native bordered table detection, borderless tabular alignment,
figure/graph/caption tagging, and on-demand multimodal visual inspection for graphs/charts.

This is the backing store for the tools ONLY; the agent never touches it directly.

Includes Enterprise Guardrails:
- Magic byte validation (%PDF-) to prevent polyglot file uploads
- Filename sanitization against directory traversal attacks
- Decompression bomb protection (page count & file size thresholds)
- Zero vector database / zero embeddings compliance
"""

from __future__ import annotations
import os
import re
import uuid
import statistics
import time
from typing import Any, Dict, List, Optional
import pymupdf  # PyMuPDF

# Enterprise Limits
MAX_DOCUMENT_PAGES = 3000
MAX_FILE_BYTES = 100 * 1024 * 1024  # 100 MB


def _is_tabular_block(lines: List[List[Any]]) -> bool:
    """Detect whether a cluster of words on consecutive lines forms a tabular structure.
    
    WHY: Handles borderless tables (like conditional probability tables, matrices, and listings)
    which lack line graphics but align tokens in 2D columns.
    """
    if len(lines) < 2:
        return False
    counts = [len(l) for l in lines]
    avg_count = sum(counts) / len(counts)
    # Tables typically have 2 to 10 columns per row
    if avg_count < 2 or avg_count > 10:
        return False
    all_words = [w[4] for l in lines for w in l]
    if any(len(w) > 35 for w in all_words):
        return False
    # Check if lines have consistent column counts
    var = sum((c - avg_count) ** 2 for c in counts) / len(counts)
    if var > 4.0:
        return False
    # Check if there are numbers, mathematical symbols, or probability values
    has_numbers_or_symbols = sum(
        1 for w in all_words if any(char.isdigit() or char in '¯$%=><' for char in w)
    )
    if has_numbers_or_symbols < 2 and len(lines) > 2:
        return False
    return True


class DocumentStore:
    """In-memory backing store for parsed PDF documents with enterprise guardrails and table/graph extraction."""

    def __init__(self) -> None:
        self._documents: Dict[str, Dict[str, Any]] = {}
        self._visual_cache: Dict[str, str] = {}

    def add_pdf(self, file_source: bytes | str, filename: Optional[str] = None) -> str:
        """Parse a PDF once from bytes or filepath and store its extracted content.
        
        Returns the generated doc_id.
        """
        # WHY: Generate a clean doc_id prefix so the LLM has a short, unambiguous identifier to reference.
        doc_id = f"doc_{uuid.uuid4().hex[:8]}"

        # Enterprise Filename Sanitization: eliminate path traversal patterns
        clean_filename = "document.pdf"
        if filename:
            base = os.path.basename(filename)
            clean_filename = re.sub(r'[^a-zA-Z0-9_\-\. ]', '_', base)

        pdf_bytes: bytes
        if isinstance(file_source, str):
            if not os.path.exists(file_source):
                raise FileNotFoundError(f"PDF source file '{file_source}' does not exist.")
            file_size = os.path.getsize(file_source)
            if file_size > MAX_FILE_BYTES:
                raise ValueError(f"File size exceeds enterprise policy limit ({MAX_FILE_BYTES // (1024*1024)} MB).")
            with open(file_source, "rb") as f_check:
                header = f_check.read(5)
                if not header.startswith(b"%PDF-"):
                    raise ValueError("Security Violation: File header does not match valid PDF magic bytes (%PDF-).")
                f_check.seek(0)
                pdf_bytes = f_check.read()
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            if not filename:
                clean_filename = os.path.basename(file_source)
        elif isinstance(file_source, bytes):
            if len(file_source) > MAX_FILE_BYTES:
                raise ValueError(f"File size exceeds enterprise policy limit ({MAX_FILE_BYTES // (1024*1024)} MB).")
            if not file_source.startswith(b"%PDF-"):
                raise ValueError("Security Violation: File payload does not match valid PDF magic bytes (%PDF-).")
            pdf_bytes = file_source
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        else:
            raise ValueError("file_source must be filepath string or bytes")

        try:
            num_pages = len(doc)
            # Enterprise Decompression Bomb Check
            if num_pages > MAX_DOCUMENT_PAGES:
                raise ValueError(f"Document contains {num_pages} pages, exceeding enterprise policy threshold of {MAX_DOCUMENT_PAGES}.")

            meta = doc.metadata or {}
            raw_title = (meta.get("title") or "").strip()
            title = raw_title if raw_title else clean_filename

            # Extract TOC (list of [level, title, page_1_indexed])
            raw_toc = doc.get_toc() or []

            # Extract per-page structured text, tables, captions, and visual elements
            pages_text: Dict[int, str] = {}
            page_visual_metadata: Dict[int, Dict[str, Any]] = {}
            all_spans: List[Dict[str, Any]] = []

            for page_index in range(num_pages):
                page_num = page_index + 1
                page = doc[page_index]

                raw_text = page.get_text("text") or ""
                text_blocks = page.get_text("blocks") or []
                images = page.get_images() or []
                drawings = page.get_drawings() or []

                # 1. Native Bordered Tables via PyMuPDF TableFinder
                bordered_tables: List[str] = []
                try:
                    tabs = page.find_tables()
                    if tabs and hasattr(tabs, "tables"):
                        for t in tabs.tables:
                            md = t.to_markdown()
                            if md and md.strip():
                                bordered_tables.append(md.strip())
                except Exception:
                    pass

                # 2. Borderless Tabular Alignment via Word Coordinates
                borderless_tables: List[str] = []
                words = page.get_text("words") or []
                blocks_words: Dict[int, List[Any]] = {}
                for w in words:
                    blocks_words.setdefault(w[5], []).append(w)

                for b_no, b_words in sorted(blocks_words.items()):
                    lines: List[List[Any]] = []
                    b_words_sorted = sorted(b_words, key=lambda w: (round(w[1], 1), w[0]))
                    cur_line: List[Any] = []
                    cur_y = None
                    for w in b_words_sorted:
                        if cur_y is None or abs(w[1] - cur_y) > 5.5:
                            if cur_line:
                                lines.append(sorted(cur_line, key=lambda w: w[0]))
                            cur_line = [w]
                            cur_y = w[1]
                        else:
                            cur_line.append(w)
                    if cur_line:
                        lines.append(sorted(cur_line, key=lambda w: w[0]))

                    if _is_tabular_block(lines):
                        t_lines = [" | ".join(w[4] for w in l) for l in lines]
                        borderless_tables.append("\n".join(t_lines))

                # 3. Figure & Caption Tagging
                caption_blocks: List[str] = []
                for b in text_blocks:
                    b_txt = b[4].strip()
                    if not b_txt:
                        continue
                    if re.match(r'^(fig\.|figure|table|graph|chart|diagram|illustration|plot)\s*\d', b_txt, re.IGNORECASE):
                        caption_blocks.append(b_txt)

                # Assemble clean structured text for the page
                sections: List[str] = []
                if raw_text.strip():
                    sections.append(raw_text.strip())

                if bordered_tables:
                    # WHY: Formats bordered tables directly as markdown so the LLM easily reasons over columns/rows.
                    sections.append("[DETECTED STRUCTURED TABLES]:\n" + "\n\n".join(bordered_tables))

                if borderless_tables:
                    # WHY: Reconstructs borderless probability/matrix tables without losing horizontal relationships.
                    sections.append("[DETECTED TABULAR DATA / ALIGNED COLUMNS]:\n" + "\n\n".join(borderless_tables))

                if caption_blocks:
                    sections.append("[FIGURE / CAPTION REFERENCES]:\n" + "\n\n".join(caption_blocks))

                has_visuals = bool(images or len(drawings) > 10)
                if has_visuals:
                    sections.append(f"[VISUAL MEDIA PRESENT ON PAGE {page_num}: {len(images)} embedded image(s), {len(drawings)} vector graphic/chart elements]")

                pages_text[page_num] = "\n\n".join(sections)
                page_visual_metadata[page_num] = {
                    "num_images": len(images),
                    "num_drawings": len(drawings),
                    "has_captions": bool(caption_blocks),
                    "has_tables": bool(bordered_tables or borderless_tables),
                }

                # If TOC is empty, collect font size / bold information for heuristic headings
                if not raw_toc:
                    page_dict = page.get_text("dict")
                    for block in page_dict.get("blocks", []):
                        if block.get("type") == 0:
                            for line in block.get("lines", []):
                                line_text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                                if line_text:
                                    max_size = max((span.get("size", 10.0) for span in line.get("spans", [])), default=10.0)
                                    is_bold = any((span.get("flags", 0) & 2 != 0 or "bold" in (span.get("font", "")).lower()) for span in line.get("spans", []))
                                    all_spans.append({
                                        "page": page_num,
                                        "text": line_text,
                                        "size": max_size,
                                        "is_bold": is_bold
                                    })

            # Pre-compute heuristic headings if TOC is empty
            heuristic_headings: List[Dict[str, Any]] = []
            if not raw_toc and all_spans:
                sizes = [s["size"] for s in all_spans]
                median_size = statistics.median(sizes) if sizes else 10.0
                for s in all_spans:
                    txt = s["text"]
                    if len(txt) > 100:
                        continue
                    is_large = s["size"] > (median_size * 1.15)
                    is_bold_short = s["is_bold"] and len(txt) < 80
                    if is_large or is_bold_short:
                        heuristic_headings.append({
                            "level": 1 if s["size"] > median_size * 1.3 else 2,
                            "title": txt,
                            "page": s["page"]
                        })
                        if len(heuristic_headings) >= 150:
                            break

            self._documents[doc_id] = {
                "doc_id": doc_id,
                "title": title,
                "num_pages": num_pages,
                "pages": pages_text,
                "visual_metadata": page_visual_metadata,
                "pdf_bytes": pdf_bytes,
                "toc": raw_toc,
                "heuristic_headings": heuristic_headings,
                "has_native_toc": bool(raw_toc),
                "metadata": {
                    "author": meta.get("author") or "",
                    "creator": meta.get("creator") or "",
                    "producer": meta.get("producer") or "",
                    "creationDate": meta.get("creationDate") or "",
                    "filename": clean_filename,
                }
            }
            return doc_id
        finally:
            doc.close()

    def _get_visual_analysis(self, doc_id: str, page_number: int) -> Optional[str]:
        """Generate multimodal visual interpretation of charts, diagrams, and figures on demand."""
        cache_key = f"{doc_id}_{page_number}"
        if cache_key in self._visual_cache:
            return self._visual_cache[cache_key]

        gemini_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_key:
            return None

        doc_record = self._documents.get(doc_id)
        if not doc_record:
            return None

        # Check if page has visual media (images or drawings)
        meta = doc_record.get("visual_metadata", {}).get(page_number, {})
        if not (meta.get("num_images", 0) > 0 or meta.get("num_drawings", 0) > 10 or meta.get("has_captions", False)):
            return None

        # Render page pixmap to PNG bytes
        try:
            doc = pymupdf.open(stream=doc_record["pdf_bytes"], filetype="pdf")
            try:
                page = doc[page_number - 1]
                pix = page.get_pixmap(dpi=110)
                img_bytes = pix.tobytes("png")
            finally:
                doc.close()

            # Call Gemini Vision with lightweight model
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=gemini_key)
            model_name = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")

            prompt = (
                "Analyze any figures, charts, graphs, plots, diagrams, or visual tables on this page. "
                "For graphs/plots: state labels, axes, visual trends, and meaning. "
                "For diagrams: describe the visual parts and relationships. "
                "For tables: transcribe rows and numbers into a Markdown table. "
                "Be concise and factual."
            )

            response = client.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                    prompt
                ]
            )

            analysis = (response.text or "").strip()
            if analysis:
                self._visual_cache[cache_key] = analysis
                return analysis
        except Exception:
            # WHY: Fallback cleanly to local structured text if Gemini API is unreachable or rate limited.
            return None

        return None

    def get_document(self, doc_id: str) -> Dict[str, Any]:
        """Retrieve document record by doc_id with identifier validation."""
        if not re.match(r'^[a-zA-Z0-9_\-]+$', str(doc_id)):
            raise ValueError(f"Security Alert: Invalid doc_id format '{doc_id}'.")
        if doc_id not in self._documents:
            raise KeyError(f"Document '{doc_id}' not found in store.")
        return self._documents[doc_id]

    def list_documents(self) -> List[Dict[str, Any]]:
        """List all loaded documents with metadata only."""
        return [
            {
                "doc_id": d["doc_id"],
                "title": d["title"],
                "num_pages": d["num_pages"],
                "metadata": d["metadata"],
            }
            for d in self._documents.values()
        ]

    def get_page_text(self, doc_id: str, page_number: int, include_vision: bool = True) -> str:
        """Get the structured text of 1-indexed page_number with strict bounds enforcement.
        
        Includes structured tables, aligned tabular data, and on-demand visual analysis of charts/graphs.
        """
        doc = self.get_document(doc_id)
        if not isinstance(page_number, int) or page_number < 1 or page_number > doc["num_pages"]:
            raise IndexError(f"Page number {page_number} is out of range [1, {doc['num_pages']}].")

        base_text = doc["pages"].get(page_number, "")

        if include_vision:
            # On-demand visual inspection for figures, charts, and graphs
            vision_desc = self._get_visual_analysis(doc_id, page_number)
            if vision_desc:
                # WHY: Appends visual interpretation of graphs/figures directly to page text so the agent sees them.
                return f"{base_text}\n\n[AI VISUAL ANALYSIS OF CHARTS / FIGURES / IMAGES / TABLES ON PAGE {page_number}]:\n{vision_desc}"

        return base_text

    def clear(self) -> None:
        """Reset internal store."""
        self._documents.clear()
        self._visual_cache.clear()
