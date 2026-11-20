IOCL GeM TENDER EVALUATION — OFFLINE DEMO
=========================================

HOW TO RUN
----------
Double-click  START-HERE.html

That is all. It opens in your default browser and runs entirely from this
folder: no internet, no Python, no installation, no server, no ports.

Works on Windows, macOS and Linux, in Chrome, Edge or Firefox.


HOW TO SHARE
------------
Copy or zip this whole folder and send it. Keep the folder structure intact —
the pages load the document images from the data/ subfolder next to them.


WHAT TO SHOW (about 5 minutes)
------------------------------
1. Open the Verification Dashboard and pick a bidder, e.g. HEAMONS SYSTEMS.

2. Step 1 - Classification & Segments
   Each submitted PDF is identified and split into logical documents. Point at
   the bundle that splits into PURCHASE_ORDER / EXECUTION_PROOF / INVOICE with
   per-segment confidence: bidders merge many documents into one file, and the
   system separates them automatically.

3. Step 2 - OCR & Page Viewer
   The extracted text sits beside the rendered page. The badge names the engine
   used per page (native text vs. vision OCR). Page through a 36-page contract.

4. Step 3 - Extraction & Traceability
   The strongest part of the demo. Click any extracted value on the left; the
   right pane jumps to the exact source page and highlights where the value was
   read from. Nothing is asserted without a visible provenance trail.

5. Step 4 - PO Mapping
   Each purchase order and the proof documents linked to it.

6. Step 5 - PQC Evaluation
   The verdict plus the full evaluator decision log: every criterion, threshold
   and date-window check written out in auditable prose.

The flag icon next to any field opens the annotation form used by human
reviewers. In this offline bundle annotations are stored in your own browser
only (they do not write back to the pipeline).


WHAT IS NOT IN THIS BUNDLE
--------------------------
- The AI assistant on the Summary Dashboard needs a live LLM, so it replies
  with a short notice instead.
- The pipeline cannot be re-run from the demo; these are frozen results.
- Original bidder PDFs are excluded. Pages are pre-rendered images, which is
  why the folder stays small and needs no PDF reader.

Read KNOWN-ISSUES.txt before presenting.
