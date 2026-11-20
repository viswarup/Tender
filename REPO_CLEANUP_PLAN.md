# Proposal: Repository Cleanup, Refactoring & Reorganization

This document outlines the proposed restructuring plan for the **IOCL GeM Tender Evaluation Pipeline** repository. The goals are to eliminate code duplication between scratch runners and core libraries, establish standard naming conventions, and organize code into logical, production-ready folders.

---

## 1. Observations on Current Inefficiencies

### A. Code Duplication (Scratch vs. Pipeline)
Several runner scripts in the `scratch/` directory contain copy-pasted duplicates of core library helper functions, class definitions, schemas, and prompting templates.
* **`scratch/classify_bids.py`** duplicates the heuristics, configuration, and LLM classification/segmentation logic from **`pipeline/doc_classifier.py`**.
* **`scratch/execute_pipeline.py`** duplicates helpers like `parse_monetary_value`, `parse_date`, and unified `query_llm_json` calls that are already refactored into the core `pipeline/` package.
* **Risk:** This leads to "code drift"—a bug fixed in `pipeline/` remains broken in `scratch/` runners, causing inconsistent test and execution results.

### B. Folder Naming & Semantics
* **`scratch/`:** Contains core, production-critical execution scripts (like `execute_pipeline.py` and `classify_bids.py`). Placing these in a folder named `scratch` implies they are temporary or disposable.
* **`Bid documents/`:** The folder contains a space in its name, which requires manual escaping (e.g. `Bid\ documents/`) in terminal commands and makes shell scripts brittle.
* **`gemini_ocr/`:** Named after the specific OCR method used. If the OCR engine is changed in the future, the folder name becomes obsolete.

---

## 2. Proposed Cleanup Tasks

### Task 1: Refactor Runner Scripts (Remove Duplicates)
* Simplify `scratch/classify_bids.py` and `scratch/execute_pipeline.py` to be **lightweight wrappers**.
* They will import the modular classes and functions (e.g., `OllamaClient`, `PQCEvaluator`, `classify_document`, `run_pipeline`) directly from the `pipeline/` package.
* Delete all duplicate helper logic, regexes, and f-string LLM prompts from the runner files.

### Task 2: Rename Directories for Clean Semantics
We will rename folders to follow standard development conventions (lowercase, snake_case, no spaces):
1. **`Bid documents/`** $\rightarrow$ **`raw_bids/`**
2. **`gemini_ocr/`** $\rightarrow$ **`ocr_cache/`**

### Task 3: Restructure Script Files (Separate Production from Playgrounds)
We will split the `scratch/` folder into two folders:
1. **`scripts/`**: Holds production CLI runners (e.g. classification runner, main pipeline executor, and ground-truth validation).
2. **`playground/`**: Holds temporary, experimental, or one-off utility scripts (e.g. `count_pdf_pages.py` and API test scripts).

---

## 3. Proposed Target Repository Layout

```
.
├── raw_bids/                 # Original PDF files submitted by bidders (no spaces in path)
├── ocr_cache/                # Cached page-by-page OCR text/layout markdown outputs
├── data/                     # Output evaluations, vendor extractions, and user feedback
├── pipeline/                 # Core Python Library Package (ONLY holds clean, modular logic)
│   ├── models.py             # Pydantic data schemas
│   ├── llm_router.py         # Unified LLM API router (Gemini/OpenRouter/Ollama fallback)
│   ├── doc_classifier.py     # Classification and segmentation logic
│   ├── data_extractor.py     # Entity extraction logic
│   └── pqc_evaluator.py      # PQC rules checking
├── scripts/                  # Clean CLI runner scripts (Import from pipeline/, NO duplicate code)
│   ├── classify.py           # Classification runner
│   ├── run_pipeline.py       # Main pipeline runner
│   └── test_ground_truth.py  # Ground truth verification test
├── playground/               # One-off utility/experimental scripts
│   ├── count_pdf_pages.py
│   └── test_gemini.py
├── dashboard/                # Web UI files
├── tests/                    # Pytest suite
└── server.py                 # FastAPI web server
```
