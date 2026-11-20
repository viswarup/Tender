# Walkthrough: Fixing the Document Classification & Evaluation Failure

This document details the diagnostic steps, the identified root cause, the resolution implemented, and the final verification of the **IOCL GeM Tender Evaluation Pipeline**.

---

## 1. Problem Statement & Diagnostics

### Symptom
When verifying the shortlist results against the ground-truth criteria:
* **HEAMONS SYSTEMS** was marked as `REJECTED` (Expected: `ACCEPTED`).
* No purchase orders or purchase order values were extracted for them (Actual: `None`, Expected: `GEMC-511687703758674`).

### Diagnostics
1. We checked how the source document containing the target PO (`17622499231073.pdf`) was classified in the master registry file `data/classified_documents.json`.
2. The document was incorrectly segmented and classified as **`UDYAM_MSME`** on pages 1-3. Because of this classification, the extraction pipeline skipped commercial purchase order checks on those pages, resulting in a failure to extract the target PO.
3. In addition, the classification summaries and reasons in the registry had duplicate placeholder texts (e.g., `"Udyam Registration Certificate for XYZ Enterprises."` or `"Invoice No. INV-001 for supply of goods to ABC Corporation."`) across several distinct bidders.

---

## 2. Root Cause Analysis

We identified a mismatch between text formats in the classification pipeline:
* **The Parser:** In `scripts/classify.py`, `parse_num_pages_and_text` extracts text per page into the `DocumentMetadata` pages structure and **strips out** the `[P{page}-L{line}]` line prefixes.
* **The Snippet Builder:** In `pipeline/doc_classifier.py`, `extract_page_snippets` was expecting a flat text string containing these `[P{page}-L{line}]` prefixes to match page boundaries using regex:
  ```python
  match = re.match(r"^\[P(\d+)-L\d+\](.*)", line.strip())
  ```
* **The Failure:** Because the prefixes were already stripped, the regex never matched. The `snippets` dictionary returned by `extract_page_snippets` was completely **empty** (`0 pages`).
* **The Hallucination:** Because the LLM was given an empty prompt with no page snippets, the model had to guess the document content. It fell back to generating standard placeholder examples from its pre-training (such as `XYZ Enterprises` and `INV-001`), classifying the purchase order as `UDYAM_MSME`.

---

## 3. Resolution Implemented

We refactored the snippet extraction to leverage the already structured `doc.pages` metadata instead of using regex on a flat text block:

### A. Refactored Snippet Extraction (`pipeline/doc_classifier.py`)
We updated `extract_page_snippets` to process `DocumentMetadata.pages` directly:
```python
def extract_page_snippets(doc: DocumentMetadata, head_len: int = 400, tail_len: int = 300) -> dict[int, str]:
    snippets = {}
    if doc.pages:
        for page in doc.pages:
            p_num = page.page_number
            page_text = page.ocr_text or page.text or ""
            if len(page_text) <= (head_len + tail_len + 50):
                snippets[p_num] = page_text
            else:
                head = page_text[:head_len]
                tail = page_text[-tail_len:]
                snippets[p_num] = f"[START] {head} ... [END] {tail}"
    return snippets
```
And updated its call in `classify_document`:
```python
snippets = extract_page_snippets(doc)
```

### B. Client Configuration Optimizations
We reverted the classification default model back to `llama3:8b` (which natively supports JSON mode in Ollama, unlike `gpt-oss:20b` which was failing to generate JSON). Since the correct page snippets are now passed, it segments and classifies all documents accurately.

---

## 4. Verification Results

We executed the full rebuild pipeline and validated the outcomes:

1. **Re-ran Batch Classification:** 
   `PYTHONPATH=. .venv/bin/python3 scripts/classify.py`
   * Segmented and classified all bidder documents correctly. 
   * `17622499231073.pdf` was correctly segmented into a `PURCHASE_ORDER` (pages 1-2), `EXECUTION_PROOF` (page 3), and `INVOICE` (pages 4-6).
2. **Re-ran Evaluation Pipeline:**
   `PYTHONPATH=. .venv/bin/python3 scripts/run_pipeline.py --force`
   * Extracted all PO values and validated them.
3. **Ran Ground-Truth Verification:**
   `PYTHONPATH=. .venv/bin/python3 scripts/test_ground_truth.py`
   * **Result:** **`SUCCESS: All ground truth checks passed!`**
   * HEAMONS SYSTEMS is now correctly `ACCEPTED` and the target PO `GEMC-511687703758674` was successfully extracted with value `814515.0`.
4. **Ran Unit Tests:**
   `PYTHONPATH=. .venv/bin/pytest tests/test_pqc_evaluator.py`
   * **Result:** **`7 passed`** (100% green).
