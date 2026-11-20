# System Observations: Document Classification & Repository Structure

This document compiles the observations and proposals regarding the **IOCL GeM Tender Evaluation Pipeline** codebase, specifically focusing on document classification methods (heuristics vs. LLM) and repository layout inefficiencies.

---

## 1. Document Classification: Heuristics vs. LLM

### Current State & Limitations of Heuristics
Previously, document classification relied heavily on quick regex checks and string matches (heuristics) before invoking any LLM:
* **Heuristic Rules:** Checked for keywords like `"udyam"`, `"PAN"`, `"GST"`, `"ISO 9001"`, etc., on the first page or two.
* **Limitations:**
  1. **Fragility:** If a document layout changed or OCR quality dropped slightly, the heuristics failed silently and defaulted to `"OTHER"`.
  2. **Multi-Page Segmentation Bypass:** For multi-page PDF documents, heuristics often shortcut the process, missing subsequent logical boundaries (e.g., a purchase order followed by an invoice in the same PDF).
  3. **Code Duplication:** Heuristics rules were duplicated between `scratch/classify_bids.py` and `pipeline/doc_classifier.py`.

### Transition to LLM-Only Classification
To ensure robust, context-aware document classification and segmentation, the pipeline is transitioned to be **purely LLM-driven**:
1. **Disable Heuristics:** Bypass keyword matching and regex rules entirely for all files.
2. **Page-by-Page Snippet Extraction:** Extract text/layout snippets for each page of the document.
3. **Structured LLM Prompting:** Pass all snippets to the LLM (Gemini API with fallbacks to OpenRouter and local Ollama `llama3:8b`) to segment the document page-by-page.
4. **Result Format & Decision Scoring:** The generative LLM returns a structured JSON list of segments specifying `start_page`, `end_page`, `document_type`, `summary`, and `reasoning`. In place of generative LLMs hallucinating arbitrary confidence numbers, **Liquid AI's Decision Models (`d1.2` / `d1.2-ultra`)** evaluate each segment against criteria to assign mathematically calibrated statistical confidence scores (`confidence`) with zero output tokens.

---

## 2. Repository Structure & Code Redundancy

### Role of Key Classification Scripts
* **`pipeline/doc_classifier.py`**: The core package module holding the schema definitions (`DocumentSegment`, `DocumentMetadata`), Ollama client connectors, and the core LLM execution functions.
* **`scripts/classify.py`** (formerly `scratch/classify_bids.py`): A runner script that scans the bidders' folders, reads OCR markdown outputs, runs the classifier, and saves results to `data/classified_documents.json`.

### Inefficiencies Identified
1. **Code Duplication:** Runner scripts in `scratch/` duplicated full prompt strings, class definitions, and extraction utilities instead of importing them from the `pipeline/` package.
2. **Folder Naming & Conventions:**
   * `scratch/` was used to store production-critical scripts like `execute_pipeline.py`.
   * `Bid documents/` contained spaces, making command-line execution and shell scripts error-prone.
   * `gemini_ocr/` hardcoded the OCR engine name, making future migration to other engines confusing.

---

## 3. Restructuring & Clean-up Plan

To clean up the repository, the following organization is implemented:

### A. Folder Renaming
* `Bid documents/` $\rightarrow$ **`raw_bids/`**
* `gemini_ocr/` $\rightarrow$ **`ocr_cache/`**

### B. Directory Reorganization
* **`scripts/`**: Houses all production CLI scripts (such as classification, pipeline execution, OCR batching, and ground-truth testing).
* **`playground/`**: Houses experimental, temporary, or diagnostic files.
* **`scratch/`**: Completely removed.

### C. Runner Refactoring
* Runner scripts in `scripts/` are refactored to be thin import wrappers.
* They import functions and classes directly from the `pipeline/` library module rather than maintaining duplicate helper code.

---

## 4. Proposed Target Repository Layout

```
.
├── raw_bids/                 # Original PDF files submitted by bidders
├── ocr_cache/                # Cached page-by-page OCR text/layout markdown
├── data/                     # Output evaluations, vendor extractions, and user feedback
├── pipeline/                 # Core Python Library Package (Modular logic only)
│   ├── models.py             # Pydantic data schemas
│   ├── llm_router.py         # Unified LLM API router (Gemini -> OpenRouter -> Ollama fallback)
│   ├── doc_classifier.py     # Classification and segmentation logic
│   ├── data_extractor.py     # Entity extraction logic
│   └── pqc_evaluator.py      # PQC rules checking
├── scripts/                  # Production CLI runner scripts (Import from pipeline/)
│   ├── classify.py           # Classification runner
│   ├── run_pipeline.py       # Main pipeline runner
│   ├── run_ocr_batch.py      # OCR preprocessing runner
│   └── test_ground_truth.py  # Ground truth verification test
├── playground/               # Utility / experimental scripts
│   ├── count_pdf_pages.py
│   └── test_gemini.py
├── dashboard/                # Web UI files
├── tests/                    # Pytest suite
└── server.py                 # FastAPI web server
```

---

## 5. Work Completion Proof Validation Rule

A crucial business logic rule is established for evaluations:
* **Rule**: A **GeM Contract** (such as a GeM Purchase Order or `GEM_BID_COPY`) only signifies that an order was placed, but does **not** serve as proof of work completion/execution.
* **Valid Proofs of Completion**:
  1. An **Invoice** (`INVOICE`)
  2. A **Bank Certificate** indicating payment receipt
  3. An **Execution Certificate** (`EXECUTION_PROOF`) indicating the final executed value
  4. A **previous IOCL work document** (e.g. internal completion record)
