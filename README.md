# Sift Agent

A precise, zero-RAG, agentic PDF question-answering system that operates under a **strict execution budget of 6 tool calls** per query. Designed without heavy external agent frameworks, this system focuses on granular document retrieval, post-generation grounding validation, and robust untrusted-data injection defenses.

## 🚀 Key Features

- **Agentic PDF Question Answering**: Autonomously plans and extracts information using targeted tool calls rather than relying on generic chunk retrieval (RAG).
- **Evidence-Grounded Responses**: Validates its own answers by confirming that claims are supported by the extracted page content.
- **Page-Level Citations**: Explicitly returns the pages where the evidence was found.
- **Insufficient-Information Detection**: Accurately recognizes when the document does not contain enough information to answer a question, rather than hallucinating.
- **Prompt Injection Resistance**: Protects against malicious prompts embedded within document content or metadata.
- **Strict Tool-Call Budget Enforcement**: Systematically constrained to a maximum of 6 operations per query to manage computational overhead.
- **Dual Interfaces**: Includes a standalone CLI and an interactive Streamlit web application.
- **Automated Evaluation Suite**: End-to-end testing pipeline to verify accuracy, budgeting, and guardrails.

---

## 🏗 Architecture & Execution Flow

The system uses a hand-crafted `while` loop agent equipped with native tool-calling capabilities. 

**Execution Flow:**
1. **User Question**: The user uploads a PDF and submits a question.
2. **Agent Reasoning**: The agent analyzes the question and determines the required document information.
3. **Budgeted Tool Execution**: The agent calls specialized tools (`list_documents`, `list_headings`, `get_page`, `search_keyword`), up to a maximum of 6 times.
4. **Relevant PDF Content Extraction**: Raw document data is retrieved into the context window.
5. **Answer Generation & Evidence Validation**: The agent formulates an answer and runs a distinct grounding check to ensure the response is strictly based on the extracted text.
6. **Final Output**: The user receives a status, the verified answer, exact evidence quotes, and page citations.

---

## 💻 Installation & Setup

### 1. Prerequisites & Virtual Environment
Ensure Python 3.10+ is installed.

```bash
# Create and activate a virtual environment
python -m venv .venv

# Windows PowerShell:
.\.venv\Scripts\Activate.ps1

# Linux / macOS:
source .venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure API Key
Create a `.env` file from the example template:
```bash
cp .env.example .env
```
Provide your API key in the `.env` file (e.g., `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`):
```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-flash-lite-latest
```

---

## 🚀 Running the Application

### Run the Streamlit Web Application (Port 8502)
Launch the interactive web UI:
```bash
streamlit run app.py --server.port 8502
```
Access the application in your browser at: `http://localhost:8502`

### Run the Command Line Interface (CLI)
Debug or query documents quickly without launching the UI:
```bash
python cli.py samples/sample_policy_1.pdf "What is this policy about?"
```

---

## 🧪 Evaluation & Testing

The repository contains an automated evaluation suite that tests edge cases (e.g., split facts, amendments, prompt injections) and asserts budget adherence and accuracy.

**Current Evaluation Results:**
- 4/4 evaluation cases passed (100% score)
- Average tool calls used: 3.25
- Maximum observed calls: 4
- Maximum allowed calls: 6

### Run the Evaluation Suite
```bash
# Generate test artifacts (if not already generated)
python tests/make_test_pdf.py

# Run evaluation on test questions
python -m src.eval samples/trap_test.pdf samples/trap_questions.json
```

### Run Unit Tests (PyTest)
Verify the core mechanics like budget enforcement, isolation state, and grounding checks:
```bash
pytest tests/
```

---

## 🛠️ Project Structure

- `src/document_store.py`: In-memory PDF parser and backing store (using PyMuPDF).
- `src/tools.py`: Contains the 4 core retrieval functions (`list_documents`, `list_headings`, `get_page`, `search_keyword`).
- `src/budget.py`: Enforces the 6-call max budget and provides untrusted data wrapping logic.
- `src/logger.py`: System auditing that appends JSONL traces to `./logs/tool_calls.jsonl`.
- `src/prompts.py`: Core system instructions, navigation strategies, and injection defenses.
- `src/agent.py`: Central `while`-loop agent orchestrator for tool calling and grounding validation.
- `src/eval.py`: Automated benchmark script for asserting logic and boundaries.
- `app.py`: Streamlit chat UI featuring real-time status badges, cited pages, and expandable execution traces.
- `cli.py`: Standalone command-line runner.
- `tests/`: Pytest suite and script to generate local evaluation PDF artifacts (`make_test_pdf.py`).
- `docs/ARCHITECTURE.md`: In-depth architectural documentation, design rationale, and failure mitigations.

---

## 🔒 Security & Reliability Implementations

- **Prompt Injection Handling**: The agent architecture is designed to safely handle untrusted data coming from document content without being hijacked.
- **Grounding Validation**: A secondary validation step ensures the model doesn't hallucinate facts absent from the PDF.
- **Tool-Call Budget**: Execution halts strictly at 6 calls, preventing runaway loops and unpredictable API costs.
- **Input Sanitization**: Keywords and inputs passed to search tools are sanitized to prevent internal injection or oversized queries.

## ⚠️ Limitations

- **Memory Constraints**: Relies on keeping document pages in memory for parsing; very large documents may cause excessive memory consumption depending on environment constraints.
- **No Vector Search**: Does not utilize embedding-based semantic search; relies heavily on exact keyword searches and TOC navigation.
- **Format Support**: Optimized primarily for PDFs.

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.
