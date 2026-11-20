# IOCL GeM Tender Evaluation Pipeline — System Code Review

**Reviewed:** 2026-06-13  
**Scope:** Full codebase — pipeline library, scripts, server, dashboard, tests, config, chatbot  
**Severity:** 🔴 Critical Bug | 🟠 High | 🟡 Medium (Code Quality) | 🔵 Security | 🟢 Low/Suggestion

---

## Executive Summary

The system is well-architected at a high level, with clean separation of models (`models.py`), routing (`llm_router.py`), and evaluation (`pqc_evaluator.py`). However, a detailed code review reveals:

- **The entire extraction layer is duplicated** between `scripts/run_pipeline.py` and `pipeline/data_extractor.py` — two parallel implementations that can diverge silently
- **Three critical bugs** that cause incorrect data extraction or silently fail in production
- **No API authentication** on any endpoint — the server is fully open on the local network
- **An absolute hardcoded path** in `run_ocr_batch.py` that will fail on any non-developer machine
- **The `/api/run-pipeline` endpoint will always fail** due to a wrong import name
- **35+ distinct findings** across all files

---

## CRITICAL BUGS 🔴

### C1. `/api/run-pipeline` endpoint always fails — wrong import name
**File**: `server.py` L.407  
**Code**: `from pipeline.run_pipeline import run_full_pipeline`  
The function in `scripts/run_pipeline.py` is named `run_pipeline`, not `run_full_pipeline`. This import will always raise `ImportError`, which is caught by the generic `except Exception` handler and returned as an HTTP 500. The endpoint has never worked.  
**Fix**: Change to `from scripts.run_pipeline import run_pipeline` and call it as `run_pipeline(...)`.

---

### C2. Entire extraction layer duplicated — `run_pipeline.py` vs `data_extractor.py`
**File**: `scripts/run_pipeline.py` L.242–L.440 vs `pipeline/data_extractor.py`  
`run_pipeline.py` contains fully self-contained extraction functions `extract_udyam_data`, `extract_gst_data`, `extract_maf_data`, `extract_po_data` that are entirely separate implementations from the ones in `pipeline/data_extractor.py`. Both use the same prompts and regex logic. Any bug fix or improvement in `data_extractor.py` is silently ignored at pipeline runtime.  
**Impact**: Two maintenance branches; inconsistent extraction results; wasted development effort.  
**Fix**: Delete all extraction functions from `run_pipeline.py` and import from `pipeline.data_extractor`.

---

### C3. PDF file handle leak in `_find_text_source`
**File**: `pipeline/data_extractor.py` L.174–L.196  
```python
pdf = fitz.open(doc.filepath)
for page_index, page in enumerate(pdf):
    ...
    if rects:
        ...
        pdf.close()
        return page_index + 1, bbox, context  # ← returns before closing all paths
pdf.close()
```
When a match is found, `pdf.close()` is called before `return` — safe. But if the loop exits without finding a match (no `return` inside), `pdf.close()` at L.196 is the only close. However, if any exception occurs between L.175 and L.196, the PDF handle leaks. Additionally, `fitz.open` is called **every time any field value needs to be located** — for a 6-field PO, the PDF is opened and closed 6 times.  
**Fix**: Use `with fitz.open(...) as pdf:` context manager. Cache the open document per extraction call.

---

### C4. GeM PO block boundary bug — last PO captures entire remaining document
**File**: `scripts/run_pipeline.py` L.346 and `pipeline/data_extractor.py` L.283  
```python
end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
```
For the last PO in a multi-PO document, `end = len(text)` — the block extends to the **end of the entire OCR text file**, even if the PO is on pages 1-4 of a 20-page document. All subsequent text (other document types) is incorrectly included in that PO's extraction block, potentially extracting wrong `issuing_organization` or `po_value`.  
**Fix**: For the last PO, use the page boundary from `doc.segments` or limit to a reasonable character budget (e.g., 5000 chars after the GEMC reference).

---

### C5. `startup_exemption` config key read but never enforced
**File**: `pipeline/pqc_evaluator.py`; `config/tender_config.json` L.25  
`"startup_exemption": true` is in the config. The evaluator reads `mse_exemption` but has **no corresponding `startup_exemption` logic**. DPIIT-recognized startups are entitled to the same PQC exemption as MSEs per GeM rules. A startup bidder with no qualifying POs would be incorrectly `REJECTED`.  
**Fix**: Add startup check analogous to MSE check; detect DPIIT registration from vendor documents.

---

### C6. Off-by-one: `_make_source_ref` accepts `page_num = 0`
**File**: `pipeline/data_extractor.py` L.228–L.230  
```python
if page_num <= len(doc.pages):
    page_data = doc.pages[page_num - 1]  # ← page_num=0 → doc.pages[-1] = last page!
```
When `page_num` is 0 (possible if `_find_text_source` returns 0 in an edge case), `doc.pages[-1]` silently returns the **last page** of the document instead of raising an error. Source references will be attributed to the wrong page.  
**Fix**: Change condition to `if 1 <= page_num <= len(doc.pages):`.

---

### C7. `run_pipeline.py` mutates module-level globals — thread-unsafe
**File**: `scripts/run_pipeline.py` L.469  
```python
def run_pipeline(...):
    global CONFIG_PATH, BIDS_DIR, OCR_DIR, DATA_DIR, VENDORS_OUT, EVALS_OUT, TENDER_CONFIG
```
FastAPI's `/api/run-pipeline` endpoint calls this. Any concurrent pipeline trigger would race on these module-global path variables, corrupting each other's configuration.  
**Fix**: Pass all paths as function-local variables or a `PipelineConfig` dataclass rather than module globals.

---

### C8. `run_ocr_batch.py` has absolute hardcoded path
**File**: `scripts/run_ocr_batch.py` L.19  
```python
WORKSPACE = Path("/Users/viswarup/Downloads/tender evaluation")
```
This hardcoded path will fail on any other machine, any CI system, or any deployment. Every other script uses `Path(__file__).resolve().parent.parent`.  
**Fix**: Replace with `WORKSPACE = Path(__file__).resolve().parent.parent`.

---

### C9. PNG image sent with `mime_type='image/jpeg'` 
**File**: `scripts/run_ocr_batch.py` L.90, L.327  
The temp image file is saved as `.png` (via `pix.save(str(img_path))`, PyMuPDF default for `.png` extension), but uploaded to Gemini API with `mime_type='image/jpeg'`. Sending a PNG as JPEG causes the API to misparse the image data, producing garbled or empty OCR output.  
**Fix**: Either save as JPEG (`pix.save(str(img_path), "jpg")`) or change `mime_type` to `'image/png'`.

---

### C10. `execution_proof_status` does NOT affect `is_qualifying` in PQC evaluator
**File**: `pipeline/pqc_evaluator.py` L.289–L.294  
```python
has_proof = po.execution_proof_status in (ExecutionProofStatus.SUBMITTED,)
# ...
is_qualifying = meets_similar and meets_value and date_in_window  # ← has_proof NOT included!
```
`has_proof` is computed but never included in the `is_qualifying` condition. A vendor with no execution proof at all can still have POs marked as `qualifying_orders`, incorrectly triggering `ACCEPTED` status.  
**Fix**: Change to `is_qualifying = meets_similar and meets_value and date_in_window and has_proof`.

---

### C11. `PARTIAL` similar-item match treated as disqualification
**File**: `pipeline/pqc_evaluator.py` L.272  
```python
meets_similar = similar_match == SimilarItemMatch.YES
```
A `PARTIAL` match is treated identically to `NO` (rejected). Per tender evaluation practice, a PARTIAL match should trigger `QUERIED` status (needs human review), not outright rejection.  
**Fix**: Handle `PARTIAL` separately — set `meets_similar = True` but add a query flag.

---

## SECURITY ISSUES 🔵

### S1. No authentication on any API endpoint
**File**: `server.py`  
All endpoints — including `/api/run-pipeline` (triggers AI pipeline at your cost) and `/api/feedback` (writes to disk) — have zero authentication. Anyone on the local network can:
- Trigger unlimited LLM API calls at your expense
- Overwrite `user_feedback.json` with arbitrary data
- Read all classified vendor confidential bid data  
**Fix**: Add a shared-secret API key header check using `FastAPI.Depends`. Even `X-API-Key: <random-secret-from-env>` is sufficient for an internal tool.

---

### S2. Path traversal vulnerability in page-image endpoint
**File**: `server.py` L.223–L.270  
```python
image_path = DATA_DIR / "page_images" / vendor_name / filename
```
`vendor_name` and `filename` come directly from the URL with no sanitization. A request to `/api/page-image/../../.env/secret` would attempt to serve sensitive files.  
**Fix**: After constructing the path, validate: `assert image_path.resolve().is_relative_to(DATA_DIR.resolve())`.

---

### S3. No input sanitization before injecting PDF text into LLM prompts
**File**: `pipeline/doc_classifier.py` L.127–L.161  
Raw PDF text content is injected directly into the segmentation prompt. A maliciously crafted PDF could contain text that overrides the system instructions (prompt injection attack), causing the LLM to output manipulated classification results.  
**Fix**: Delimit user-controlled content more robustly using XML-style tags (`<document_content>...</document_content>`) which most modern LLMs treat as data rather than instructions.

---

### S4. Gemini API key used even when None
**File**: `scripts/run_ocr_batch.py` L.26  
```python
google_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
```
If `GEMINI_API_KEY` is not set, `api_key=None` is passed — the SDK may succeed at construction but fail silently at the first call, or expose auth details in error messages.  
**Fix**: `key = os.environ.get("GEMINI_API_KEY"); if not key: logger.warning("GEMINI_API_KEY not set, Gemini OCR will be unavailable")`

---

## HIGH PRIORITY ISSUES 🟠

### H1. `GenerateContentConfig.system_instruction` assigned after construction
**File**: `pipeline/llm_router.py` L.159–L.160 and L.250–L.251  
```python
config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
if system:
    config.system_instruction = system  # ← post-construction mutation; may be silently dropped
```
The Google GenAI SDK's config objects may be immutable or validate fields only at construction. Post-construction attribute assignment is not guaranteed to work.  
**Fix**: Pass `system_instruction=system or None` inside the constructor.

---

### H2. `gemini-flash-latest` is not a valid model name
**File**: `pipeline/llm_router.py` L.147, L.239  
`models_to_try = ['gemini-2.5-flash', 'gemini-flash-latest']` — `gemini-flash-latest` is not a valid Google GenAI model ID. Every call with this name will fail, burning retry budget and producing misleading logs.  
**Fix**: Replace with `'gemini-2.0-flash'` or `'gemini-1.5-flash'`.

---

### H3. `heuristics disabled` comment but `HEURISTIC_RULES` kept alive — dead code confusion
**File**: `pipeline/doc_classifier.py` L.32–L.83, L.236  
The `_heuristic_classify()` function (80+ lines) and `HEURISTIC_RULES` table are never called from `classify_document()`. The comment at L.236 says "heuristics disabled." This creates confusion: developers may think heuristics are active, or may attempt to enable them without knowing they were intentionally disabled.  
**Fix**: Either re-enable heuristics as a fast pre-pass (saving LLM calls), or delete the dead code and the `PAN_REGEX` constant.

---

### H4. Classify.py resumption logic is broken — always re-classifies
**File**: `scripts/classify.py` L.64–L.69, L.82, L.107, L.122  
The script loads `OUTPUT_FILE` at startup to support resuming from a previous run (`if bidder not in results`). However, after the `if` check, `classify_vendor_documents(documents_meta)` runs unconditionally and overwrites the existing entry for that bidder. Previously classified documents are always re-classified, invalidating the resume feature.  
**Fix**: Add `if bidder in results: logger.info(f"Skipping {bidder} (already classified)"); continue`.

---

### H5. OCR cache skip check doesn't verify file content
**File**: `scripts/run_ocr_batch.py` L.375–L.376  
```python
if md_path.exists() and html_path.exists():
    continue  # skip
```
A 0-byte `.md` file from a previously crashed OCR run causes the PDF to be permanently skipped. The OCR data is then missing, and downstream classification/extraction runs on empty text.  
**Fix**: Add `and md_path.stat().st_size > 0` to the condition.

---

### H6. Three separate `TARGET_BIDDERS` hardcoded lists
**File**: `scripts/run_pipeline.py` L.49, `scripts/classify.py` (separate list), `scripts/run_ocr_batch.py`  
All three scripts have independent hardcoded bidder lists. Adding a new vendor requires updating 3 files manually. If one is missed, that vendor is silently excluded from some pipeline stages.  
**Fix**: Read from `tender_config.json` or discover dynamically from `bids_folder` directory.

---

### H7. Config values mismatch — `SYSTEM_OVERVIEW.md` vs `tender_config.json`
`SYSTEM_OVERVIEW.md` states `"Option A: 3 orders ≥ ₹1,51,500"` but `tender_config.json` has `"min_value_per_order": 182000`. The correct values are in config (ground truth confirms). The overview document has stale values from a previous draft.  
**Fix**: Update `SYSTEM_OVERVIEW.md` to `₹1,82,000` (Option A), `₹2,43,000` (Option B), `₹3,03,000` (Option C).

---

### H8. `GEM_BID_COPY`/`PURCHASE_ORDER` confusion violates domain rule
**File**: `pipeline/doc_classifier.py` L.71, `pipeline/pqc_evaluator.py`  
Per the critical business rule (codified in `AGENTS.md`): **A GeM contract is NOT a proof of work completion.** However, the classifier classifies GeM contracts as `PURCHASE_ORDER`, and the PQC evaluator considers POs as experience evidence. The pipeline **must** separately verify that an `INVOICE`, `EXECUTION_PROOF`, or `BANK_CERTIFICATE` exists alongside the PO before marking `execution_proof_status = SUBMITTED`.  
The current execution proof detection in `data_extractor._cross_reference_execution_proofs` does run, but its results are not surfaced clearly in the dashboard or enforced in `is_qualifying` (see C10 above).

---

## CODE QUALITY & REDUNDANCY 🟡

### Q1. `query_llm_json` and `query_llm_text` are ~120 lines of copy-pasted code
**File**: `pipeline/llm_router.py` L.139–L.314  
Both functions have identical 3-stage (Gemini → OpenRouter → Ollama) fallback chains with the same retry/backoff logic. Only the return type and `response_mime_type` differ. ~120 lines of duplication.  
**Fix**: Refactor into a private `_query_llm(prompt, system, mode: Literal["json","text"], local_model)` helper.

---

### Q2. `clean_tags` / `_extract_first` / `_extract_regex` duplicated
- `run_pipeline.py:67`: `clean_tags`  vs  `data_extractor.py:261`: `_clean_tags` — identical functions, different names
- `run_pipeline.py:443`: `_extract_regex` vs `data_extractor.py:254`: `_extract_first` — identical functions, different names
- `run_pipeline.py:83`: `extract_pages_from_markdown` vs `classify.py:34`: `parse_num_pages_and_text` — same function with slight differences in text stripping behavior

**Fix**: Consolidate all shared text utilities into `pipeline/utils.py`.

---

### Q3. `ensure_page_image` / `_render_source_page` duplicated
- `run_pipeline.py:113`: `ensure_page_image()` — opens PDF, renders page, saves PNG
- `data_extractor.py:99`: `_render_source_page()` — calls `pipeline.pdf_processor.render_single_page`

Both do the same thing via different paths.  
**Fix**: Both should call `pipeline.pdf_processor.render_single_page`.

---

### Q4. `find_bbox_and_text_on_page` / `_find_text_source` duplicated
- `run_pipeline.py:149`: `find_bbox_and_text_on_page(bidder, pdf_filename, page_num, term, fitz_doc)`
- `data_extractor.py:115`: `_find_text_source(doc, value)` — same fitz-based search logic

**Fix**: Consolidate into a single `pipeline/utils.py:find_text_in_pdf(filepath, value)` function.

---

### Q5. `create_source_ref` / `_make_source_ref` / helper stack duplicated
- `run_pipeline.py:184`: `create_source_ref`, `make_extracted_value_helper`, `extract_field_value`, `extract_po_field_value`
- `data_extractor.py:215`: `_make_source_ref`, `_make_extracted_value`

Parallel helper stacks for creating `SourceReference` and `ExtractedValue` objects.  
**Fix**: Use only `data_extractor`'s helpers everywhere.

---

### Q6. `_parse_json_response` is a trivial private wrapper of a public function
**File**: `data_extractor.py` L.87–L.88  
```python
def _parse_json_response(response: str) -> dict:
    return parse_json_response(response)
```
This private function is exported and imported in `pqc_evaluator.py` as `from pipeline.data_extractor import _parse_json_response` — importing a `_`-prefixed function from another module is a code smell.  
**Fix**: Delete the wrapper; import `parse_json_response` directly from `llm_router` everywhere.

---

### Q7. `client` parameter is dead code in all extraction functions
**File**: `pipeline/data_extractor.py` L.377, L.456, L.507, etc.  
Every extraction function takes `client: Optional[OllamaClient]` and does `client = client or ollama`. But `query_llm_json` (which does the actual LLM call) ignores the `client` parameter entirely — it uses its own module-level clients. The parameter is completely inert and misleading.  
**Fix**: Remove the `client` parameter from all extraction functions.

---

### Q8. `BIDS_DIR` hardcoded in 3 files instead of reading from config
**File**: `server.py` L.28, `run_pipeline.py` L.43, `run_pipeline.py` L.478  
All hardcode `"raw_bids/ARC Cartridges_Bids"` when `tender_config.json` already has `"bids_folder": "raw_bids/ARC Cartridges_Bids"`.  
**Fix**: All three should read `BIDS_DIR = WORKSPACE / config["bids_folder"]`.

---

### Q9. LLM model names hardcoded in modules despite `tender_config.json` having a `"models"` section
- `data_extractor.py:40`: `EXTRACTOR_MODEL = "gpt-oss:20b"` (unusual model name)
- `doc_classifier.py:28`: `CLASSIFIER_MODEL = "llama3:8b"` (not what config says: `"classifier": "gpt-oss:20b"`)
- `pqc_evaluator.py:38`: `DEFAULT_EVALUATOR_MODEL = "gpt-oss:20b"`

The `doc_classifier.py` default `"llama3:8b"` doesn't match the config `"gpt-oss:20b"`. If config loading fails, the wrong (less capable) model is used silently.  
**Fix**: Load all model names from `tender_config.json["models"]` at startup.

---

### Q10. Imports inside function bodies
- `server.py` L.229–L.230: `import re`, `import fitz` inside `get_page_image`
- `server.py` L.383: `import datetime` inside `save_feedback`
- `llm_router.py` L.154, L.246`: `from google.genai import types` inside retry loops (called up to 6× per function)

**Fix**: Move all imports to module top level.

---

### Q11. Config re-read from disk on every chatbot request
**File**: `server.py` L.333–L.341  
`tender_config.json` is read fresh from disk every time a chat message is received to look up the chatbot model name. Should be loaded once at server startup.

---

### Q12. `VendorExtraction.schema_version` and `PQCEvaluation.schema_version` are never checked
**File**: `pipeline/models.py` L.186, L.220  
`schema_version = "1.0"` is hardcoded and never validated, incremented, or migrated against. Provides false versioning assurance.  
**Fix**: Either remove or implement actual schema migration logic.

---

### Q13. `PurchaseOrderData.meets_similar_item` defaults to `NO`
**File**: `pipeline/models.py` L.124  
If extraction fails and the field is never set, every PO is silently treated as not qualifying for the similar-item criterion. Should default to `PARTIAL` (pending review).

---

### Q14. `OCR run_hybrid_ocr` reimplements 3-stage fallback chain
**File**: `scripts/run_ocr_batch.py`  
The script independently reimplements Gemini → OpenRouter → Ollama fallback without the retry/backoff logic from `llm_router.py`. Two separate fallback implementations can diverge. The script should use `llm_router.query_llm_text` with a vision-capable model for OCR.

---

### Q15. Files not closed with `with` statement in `generate_pipeline_summary`
**File**: `scripts/run_pipeline.py` L.778  
`open(eval_file)` and `open(vendor_file_path)` are called without `with` statements. If `json.load` throws, file handles leak.

---

## TEST COVERAGE GAPS 🟡

The test suite (`tests/test_pqc_evaluator.py`) only covers the `PQCEvaluator`. Critical untested paths:

| Missing Test | Risk Level |
|---|---|
| `parse_monetary_value()` — lakh/crore/₹/INR parsing | 🔴 Silent parse failures on real data |
| `parse_json_response()` — markdown fence / balanced brace | 🔴 LLM response failures undetected |
| `_extract_gem_purchase_orders()` — GEMC regex extraction | 🔴 Wrong PO numbers/values extracted |
| `execution_proof_status` enforcement in PQC evaluation | 🔴 Vendors accepted without proof |
| `PARTIAL` similar-item match triggers QUERIED | 🟠 Incorrect REJECTED outcomes |
| Ground truth regression for all 5 bidders (not just HEAMONS) | 🟠 Regressions in other vendors |
| FastAPI endpoint smoke tests (`/api/vendors`, `/api/config`) | 🟠 Silent endpoint breakage |
| `classify.py` resumption logic | 🟡 Wasted re-classification time |
| `parse_date()` boundary cases (first/last day of experience window) | 🟡 POs near window edge incorrectly included/excluded |
| `segment_pdf_via_llm` with single-page and zero-page documents | 🟡 Edge case crashes |

**Recommendation**: Add `conftest.py` or `pyproject.toml` with `pythonpath = ["."]` so pytest runs without `PYTHONPATH=.` exports.

---

## ARCHITECTURE RECOMMENDATIONS 🟢

### A1. Introduce `pipeline/utils.py`
All shared utilities — `clean_tags`, `parse_monetary_value`, `_extract_first`, `render_single_page`, `find_text_in_pdf` — should live in a single module imported by both `data_extractor.py` and `run_pipeline.py`. This eliminates items Q2–Q5 above.

---

### A2. Reduce `run_pipeline.py` from 828 lines to ~150
`scripts/run_pipeline.py` should be a **thin orchestration script** only:
1. Loads OCR'd docs and classified segments from cache
2. Calls `pipeline.data_extractor.extract_vendor_data()`
3. Calls `pipeline.pqc_evaluator.PQCEvaluator.evaluate_vendor()`
4. Saves results

It should contain **zero** extraction functions or LLM prompts.

---

### A3. Add vendor-level content-hash caching
After OCR, hash the `.md` cache file and store the hash. On subsequent pipeline runs, skip extraction + evaluation for vendors whose hash hasn't changed. This saves significant LLM API costs during iterative testing.

---

### A4. Dashboard: Show execution proof type per PO in Step 3 sidebar
The Step 3 sidebar shows `Status: SUBMITTED/NOT_SUBMITTED` but doesn't show **what type** of proof was found (Invoice, Bank Certificate, Execution Certificate). Per the domain rule that a GeM contract alone is NOT proof of completion, the reviewer needs to see what proof was actually found and from which document.

---

### A5. Add per-model token budget tracking
After each `query_llm_json` call, log `(model, approx_prompt_tokens, latency_ms)`. This identifies which prompts are expensive and which models are handling the load, enabling cost optimization.

---

### A6. Ground truth: Add all 5 vendors
Currently only `HEAMONS SYSTEMS` has ground truth data. Add expected outcomes for all 5 bidders to make regression testing comprehensive.

---

## Summary Priority Table

| ID | Issue | Severity | File | Effort |
|---|---|---|---|---|
| C1 | `/api/run-pipeline` imports non-existent function | 🔴 | `server.py` | Low |
| C2 | Extraction layer fully duplicated in `run_pipeline.py` | 🔴 | `scripts/` | High |
| C3 | PDF file handle leak in `_find_text_source` | 🔴 | `data_extractor.py` | Low |
| C4 | Last PO block captures entire doc text | 🔴 | `run_pipeline.py`, `data_extractor.py` | Low |
| C7 | Thread-unsafe global mutation | 🔴 | `run_pipeline.py` | Med |
| C8 | Absolute hardcoded path in OCR script | 🔴 | `run_ocr_batch.py` | Low |
| C9 | PNG sent as JPEG to Gemini API | 🔴 | `run_ocr_batch.py` | Low |
| C10 | `execution_proof_status` not enforced in `is_qualifying` | 🔴 | `pqc_evaluator.py` | Low |
| C5 | `startup_exemption` never enforced | 🔴 | `pqc_evaluator.py` | Med |
| S1 | No API authentication | 🔵 | `server.py` | Med |
| S2 | Path traversal in page-image endpoint | 🔵 | `server.py` | Low |
| H1 | `system_instruction` post-construction mutation | 🟠 | `llm_router.py` | Low |
| H2 | `gemini-flash-latest` invalid model name | 🟠 | `llm_router.py` | Low |
| H3 | Dead code: 80+ lines of heuristics never called | 🟠 | `doc_classifier.py` | Low |
| H4 | Classify.py resume logic is broken | 🟠 | `scripts/classify.py` | Low |
| H5 | OCR skip check doesn't verify file content | 🟠 | `run_ocr_batch.py` | Low |
| H6 | 3 separate `TARGET_BIDDERS` lists | 🟠 | Multiple | Low |
| H7 | `SYSTEM_OVERVIEW.md` has stale PQC values | 🟠 | `SYSTEM_OVERVIEW.md` | Low |
| H8 | GeM contract as PO without proof enforcement | 🟠 | `pqc_evaluator.py` | Med |
| C11 | `PARTIAL` similar-item match → rejection | 🟠 | `pqc_evaluator.py` | Low |
| C6 | Off-by-one: `page_num=0` returns last page | 🟠 | `data_extractor.py` | Low |
| Q1 | `query_llm_json/text` are 120-line duplicates | 🟡 | `llm_router.py` | Med |
| Q2 | `clean_tags`, `_extract_first` duplicated 2x | 🟡 | Multiple | Low |
| Q3–Q5 | Page-image and source-ref helpers duplicated | 🟡 | Multiple | Med |
| Q6 | `_parse_json_response` useless wrapper | 🟡 | `data_extractor.py` | Low |
| Q7 | `client` param is dead/inert in all extractors | 🟡 | `data_extractor.py` | Low |
| Q8 | `BIDS_DIR` hardcoded in 3 files | 🟡 | Multiple | Low |
| Q9 | LLM model names hardcoded, not from config | 🟡 | Multiple | Low |
| Q10 | Imports inside function bodies | 🟡 | Multiple | Low |
| Q14 | OCR fallback chain reimplemented | 🟡 | `run_ocr_batch.py` | Med |
| Test gaps | `parse_monetary_value`, proof enforcement | 🟡 | `tests/` | High |

---

## SERVER, DASHBOARD & CHATBOT FINDINGS

### `server.py` Additional Bugs

| Finding | Location | Details |
|---|---|---|
| Async blocking — pipeline runs sync in event loop | L.407 | `async def trigger_pipeline` calls `run_full_pipeline` synchronously, blocking ALL other requests. Fix: `await asyncio.to_thread(run_pipeline, ...)` |
| UTC vs local time in timestamps | L.385 | `datetime.datetime.now().isoformat()` uses local time. Use `datetime.timezone.utc` |
| Ground truth vendor match is substring, not exact | L.150 | `"heamons" in vendor_name.lower()` matches "notaheamons". Use `==` after normalization |
| `actual_qualifying` may contain dicts, not strings | L.182–L.207 | Ground truth PO match compares `expected_po["po_number"]` against list that may contain nested dicts |
| `except ImportError` too broad on RAG import | L.299 | Partial import failures from missing deps (e.g. chromadb) silently fall back to simple chat |
| No `Cache-Control` on static CSS/JS | L.55–62 | Hand-rolled CSS/JS routes return no cache headers. Use `StaticFiles` middleware |
| No CORS policy | all | FastAPI defaults to accepting all origins |

### `dashboard/review.html` Bugs

| Finding | Location | Details |
|---|---|---|
| `sumData` fetched but never used | L.927 | Wasted API call to `/api/summary` on every page load |
| No HTTP status check on vendors response | L.932 | `vendorsRes.json()` called without `if (!vendorsRes.ok)` guard |
| Missing 404 handling for evaluation endpoint | L.960 | If evaluation file doesn't exist, `evaluationData.status` is `undefined` and badge renders blank |
| **Event listener accumulation** (serious bug) | L.1075 | `selectEl.addEventListener("change", ...)` inside `populateOcrDropdown()`, called every vendor selection. After N vendor switches, OCR handler fires N times per document change |
| Wrong text fallback order | L.1109 | `page.text || page.ocr_text` — pipeline stores OCR in `ocr_text`, not `text`. Should be reversed |
| Feedback submitted with empty vendor | L.1523 | No guard if `selectedVendorName` is empty string when submitting feedback |
| XSS via `innerHTML` from server data | L.1314, L.1043, L.1458 | `rawContext`, `doc.summary`, `evaluationData.detailed_reasoning` injected as innerHTML without sanitization |
| Typo | L.1475 | "Similiar Item Match" → "Similar Item Match" |

### `dashboard/app.js` Bugs

| Finding | Location | Details |
|---|---|---|
| XSS in onclick attribute | L.104 | `onclick="openVendor('${v.name}')"` — vendor name with `'` injects JS. Use `data-vendor` + delegated listener |
| Fragile PDF extension replace | L.456 | `.replace('.pdf', '')` misses uppercase `.PDF` and multi-extensions |
| Hardcoded HEAMONS fallback in source modal | L.485 | `currentVendor || 'HEAMONS SYSTEMS'` hardcoded — shows wrong images for other vendors |
| XSS in status badge | L.992 | `badge.innerHTML = ...${status}...` — status from API rendered as HTML |
| Chat history silently discarded by backend | L.544–547 | Frontend sends `history` correctly but `rag_engine.py` never uses it in the prompt |

### `dashboard/style.css`

| Finding | Details |
|---|---|
| `--radius` and `--radius-sm` are identical (both `8px`) | Redundant variable |
| Uppercase CSS class names `.ACCEPTED`, `.REJECTED`, `.QUERIED` | Non-conventional; silently breaks if status comes back lowercase |
| Chat user bubble border is purple (`rgba(139, 92, 246, 0.2)`) but accent is blue | Leftover from previous theme |
| No `focus-visible` styles | Accessibility gap for keyboard navigation |
| Mobile chat panel vanishes with no toggle | `@media (max-width: 900px) { .chat-panel { display: none; } }` — chat completely inaccessible on mobile |

### `dashboard/index.html`

- Chat panel has class `collapsed` with `width: 0` on load — **no button to open it** exists anywhere in the UI. The primary AI feature is inaccessible without code changes.
- Ground truth view shows spinner immediately on load even though the fetch only happens when navigating to that tab.

### `chatbot/rag_engine.py`

| Finding | Location | Details |
|---|---|---|
| Hardcoded relative paths | L.34–35 | `Path("data")` and `Path("config/tender_config.json")` break if CWD ≠ project root |
| `CHATBOT_MODEL`, `ROUTER_MODEL` are dead code | L.22–23 | Declared but never read — all calls re-read config from disk |
| Chat history accepted but never used | L.238–241 | `history` param accepted but `ANSWER_PROMPT` has no `{history}` slot |
| Config re-read on every chat message | L.69–76, L.280–286 | Config loaded from disk on each question instead of cached at startup |
| Routing keywords too simplistic | L.102–116 | Word "accepted" triggers accepted-vendor lookup even in unrelated questions |

### `chatbot/knowledge_graph.py`

| Finding | Location | Details |
|---|---|---|
| Unguarded `doc['filename']` | L.98 | Raises `KeyError` on malformed docs, silently skips entire vendor |
| NetworkX 3.x API break | L.276 | `nx.node_link_graph` signature changed in NetworkX ≥ 3.2 |

### `chatbot/vector_store.py`

| Finding | Location | Details |
|---|---|---|
| Infinite loop risk | L.59–66 | `while` loop infinite if `CHUNK_OVERLAP >= CHUNK_SIZE`. Add `if end - CHUNK_OVERLAP <= start: break` |
| ChromaDB multi-field `where` filter broken | L.190–193 | ChromaDB v0.4+ requires `$and`/`$or` operators for multi-field filters; bare dict silently ignores one condition |
| Double glob for log message | L.168 | `len(list(vendors_dir.glob('*.json')))` re-globs directory just for log count |
| `nomic-embed-text` prefix hardcoded | L.44–46 | `"search_document: "` prefix is model-specific; breaks silently if embedding model is changed |

### `config/tender_config.json`

| Finding | Details |
|---|---|
| `bid_end_date` == `bid_open_date` (both `"2025-10-27"`) | A bid cannot open and close the same day; likely data entry error |
| `"gpt-oss:20b"` is not a standard Ollama model tag | All LLM pipeline stages using this model will fail with "model not found". Run `ollama list` to verify the actual installed model name |
| `valid_execution_proof_types` list never consumed by evaluator | Documented policy but not enforced in code |
