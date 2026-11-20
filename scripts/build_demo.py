"""
Static demo bundle builder — exports a fully offline, shareable copy of the
review and summary dashboards.

Why this exists: a hand-built bundle drifts from the real app. This script
snapshots the live FastAPI responses in-process (via TestClient), so whatever
the server would serve is exactly what the bundle contains. Improve the app,
re-run this, hand your team the new folder.

The bundle needs no Python, no server, no internet: the presenter opens
START-HERE.html directly from the filesystem.

Usage:
    python scripts/build_demo.py                # build into dist/
    python scripts/build_demo.py --zip          # also produce a .zip to share
    python scripts/build_demo.py --skip-images  # reuse images already copied
"""

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("build_demo")

BUNDLE_NAME = "IOCL-Tender-Evaluation-Demo"
DASHBOARD = WORKSPACE / "dashboard"
DATA = WORKSPACE / "data"


# ── 1. Make sure every page the viewer can reach is pre-rendered ──────────────

def render_missing_page_images() -> int:
    """Render any page PNG not yet cached, so the bundle needs no PDFs."""
    import fitz

    registry_path = DATA / "classified_documents.json"
    if not registry_path.exists():
        logger.warning("  ! classified_documents.json missing — skipping pre-render")
        return 0

    config = json.loads((WORKSPACE / "config" / "tender_config.json").read_text())
    bids_dir = WORKSPACE / config.get("bids_folder", "raw_bids/ARC Cartridges_Bids")
    registry = json.loads(registry_path.read_text())

    made = 0
    for vendor, docs in registry.items():
        out_dir = DATA / "page_images" / vendor
        out_dir.mkdir(parents=True, exist_ok=True)
        for filename in docs:
            pdf = bids_dir / vendor / filename
            if not pdf.exists():
                continue
            doc = fitz.open(str(pdf))
            try:
                for i in range(len(doc)):
                    img = out_dir / f"{pdf.stem}_page_{i + 1}.png"
                    if img.exists():
                        continue
                    doc[i].get_pixmap(matrix=fitz.Matrix(160 / 72, 160 / 72), alpha=False).save(str(img))
                    made += 1
            finally:
                doc.close()
    return made


# ── 2. Snapshot the real API responses ────────────────────────────────────────

def snapshot_api() -> dict:
    """Capture exactly what the live app serves, using an in-process client."""
    from fastapi.testclient import TestClient
    import server

    client = TestClient(server.app)

    def get(path):
        response = client.get(path)
        if response.status_code != 200:
            logger.warning(f"  ! {path} -> {response.status_code} (omitted)")
            return None
        return response.json()

    snapshot = {
        "config": get("/api/config") or {},
        "summary": get("/api/summary") or {},
        "vendors": get("/api/vendors") or [],
        "vendorDetail": {},
        "evaluations": {},
        "pageImages": {},
        "groundTruth": {},
    }

    for vendor in snapshot["vendors"]:
        name = vendor["name"]
        detail = get(f"/api/vendor/{name}")
        evaluation = get(f"/api/evaluation/{name}")
        images = get(f"/api/page-images/{name}")
        if detail is not None:
            snapshot["vendorDetail"][name] = detail
        if evaluation is not None:
            snapshot["evaluations"][name] = evaluation
        if images is not None:
            snapshot["pageImages"][name] = images
        truth = get(f"/api/ground-truth/{name}")
        if truth is not None:
            snapshot["groundTruth"][name] = truth

    return snapshot


# ── 3. The offline shim: serve the inlined snapshot instead of the network ────

SHIM_JS = r"""/*
 * Offline demo shim.
 *
 * The dashboards were written against a live FastAPI backend. Rather than fork
 * their logic for the demo, this intercepts window.fetch and answers /api/*
 * calls from the inlined snapshot in demo-data.js. Everything else falls
 * through untouched. Keeps one codebase for app and demo.
 */
(function () {
    "use strict";

    var DATA = window.DEMO_DATA || {};
    var FEEDBACK_KEY = "tenderDemoFeedback";
    var realFetch = window.fetch ? window.fetch.bind(window) : null;

    function json(body, status) {
        return new Response(JSON.stringify(body), {
            status: status || 200,
            headers: { "Content-Type": "application/json" }
        });
    }

    function seg(path) {
        return path.split("/").filter(Boolean).map(decodeURIComponent);
    }

    function route(path, method, options) {
        var parts = seg(path);          // ["api", "vendor", "HEAMONS SYSTEMS"]
        var name = parts[1];
        var arg = parts.slice(2).join("/");

        if (method === "POST") {
            if (name === "feedback") {
                var entry = {};
                try { entry = JSON.parse(options && options.body); } catch (e) { entry = {}; }
                entry.timestamp = new Date().toISOString();
                try {
                    var all = JSON.parse(localStorage.getItem(FEEDBACK_KEY) || "[]");
                    all.push(entry);
                    localStorage.setItem(FEEDBACK_KEY, JSON.stringify(all));
                } catch (e) { /* private mode / storage blocked — annotation is best-effort */ }
                return json({
                    status: "success",
                    message: "Annotation saved locally in this browser (offline demo)."
                });
            }
            if (name === "chat") {
                return json({
                    response: "The assistant needs the live server and an LLM backend, "
                        + "so it is disabled in this offline demo. Everything else "
                        + "— classification, OCR, extraction, traceability and the PQC "
                        + "verdict — is fully explorable.",
                    sources: []
                });
            }
            if (name === "run-pipeline") {
                return json({ detail: "Pipeline runs require the live server." }, 501);
            }
        }

        if (name === "config") return json(DATA.config || {});
        if (name === "summary") return json(DATA.summary || {});
        if (name === "vendors") return json(DATA.vendors || []);
        if (name === "vendor") return pick(DATA.vendorDetail, arg, "Vendor");
        if (name === "evaluation") return pick(DATA.evaluations, arg, "Evaluation");
        if (name === "page-images") return json((DATA.pageImages || {})[arg] || []);
        if (name === "ground-truth") return pick(DATA.groundTruth, arg, "Ground truth");

        return json({ detail: "Not available in the offline demo bundle: " + path }, 501);
    }

    function pick(table, key, label) {
        var value = (table || {})[key];
        if (value === undefined) return json({ detail: label + " '" + key + "' not in bundle" }, 404);
        return json(value);
    }

    window.fetch = function (input, options) {
        var url = typeof input === "string" ? input : (input && input.url) || "";
        var method = ((options && options.method) || "GET").toUpperCase();
        var path = url.split("?")[0].replace(/^[a-z]+:\/\/[^/]*/i, "");

        if (path.indexOf("/api/") === 0 || path.indexOf("api/") === 0) {
            if (path.indexOf("api/") === 0) path = "/" + path;
            try {
                return Promise.resolve(route(path, method, options));
            } catch (err) {
                return Promise.resolve(json({ detail: "Demo shim error: " + err.message }, 500));
            }
        }
        if (realFetch) return realFetch(input, options);
        return Promise.reject(new Error("fetch unavailable"));
    };

    window.exportDemoFeedback = function () {
        return JSON.parse(localStorage.getItem(FEEDBACK_KEY) || "[]");
    };
})();
"""


# ── 4. Landing page tying both dashboards together ────────────────────────────

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IOCL Tender Evaluation — Demo</title>
<link href="fonts.css" rel="stylesheet">
<style>
  :root {{
    --bg: #0b0f1a; --panel: #151c2e; --border: rgba(255,255,255,.09);
    --text: #e7ecf5; --muted: #93a0b8; --accent: #58a6ff; --good: #3fb950;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
         font-family: 'Inter', system-ui, sans-serif; min-height: 100vh;
         display: flex; align-items: center; justify-content: center; padding: 40px 20px; }}
  .wrap {{ width: 100%; max-width: 860px; }}
  h1 {{ font-family: 'Outfit', system-ui, sans-serif; font-size: 2rem;
        margin: 0 0 6px; letter-spacing: -.02em; }}
  .sub {{ color: var(--muted); margin: 0 0 6px; font-size: .95rem; }}
  .built {{ color: var(--muted); font-size: .78rem; font-family: 'JetBrains Mono', monospace;
            margin: 0 0 30px; opacity: .75; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 18px; }}
  a.card {{ display: block; text-decoration: none; color: inherit; background: var(--panel);
            border: 1px solid var(--border); border-radius: 14px; padding: 24px;
            transition: border-color .15s, transform .15s; }}
  a.card:hover {{ border-color: var(--accent); transform: translateY(-2px); }}
  .card h2 {{ font-family: 'Outfit', system-ui, sans-serif; font-size: 1.15rem;
              margin: 0 0 8px; color: var(--accent); }}
  .card p {{ margin: 0; color: var(--muted); font-size: .88rem; line-height: 1.55; }}
  .tag {{ display: inline-block; margin-top: 14px; font-size: .7rem; letter-spacing: .08em;
          text-transform: uppercase; color: var(--good); border: 1px solid var(--good);
          border-radius: 999px; padding: 3px 10px; }}
  .note {{ margin-top: 30px; background: rgba(210,153,34,.09);
           border: 1px solid rgba(210,153,34,.35); border-radius: 12px;
           padding: 16px 18px; font-size: .84rem; color: #e3cf9a; line-height: 1.6; }}
  .note b {{ color: #f0dfae; }}
  .offline {{ margin-top: 18px; font-size: .78rem; color: var(--muted); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>IOCL GeM Tender Evaluation</h1>
  <p class="sub">AI-assisted commercial bid evaluation — offline demo</p>
  <p class="built">Bundle built {built} &middot; {vendors} bidders &middot; {images} document pages</p>

  <div class="cards">
    <a class="card" href="review.html">
      <h2>Verification Dashboard &rarr;</h2>
      <p>The auditor's view. Walk all five pipeline stages for any bidder:
         document classification and segmentation, OCR against the rendered page,
         field-level extraction with click-to-source traceability, PO-to-proof
         mapping, and the final PQC verdict.</p>
      <span class="tag">Start here</span>
    </a>
    <a class="card" href="index.html">
      <h2>Summary Dashboard &rarr;</h2>
      <p>The reviewer's overview. Bidder statuses at a glance, per-vendor
         extracted entities, source-page inspection and the ground-truth
         comparison for HEAMONS SYSTEMS.</p>
    </a>
  </div>

  <div class="note">
    <b>Note for the presenter:</b> all five bidders currently show ACCEPTED.
    That is a known defect in the execution-proof check, not a real result —
    the evaluator accepts a GeM contract as its own proof of completion.
    See <b>KNOWN-ISSUES.txt</b> before demoing. Everything else on screen
    (classification, OCR, extraction, traceability) reflects genuine output.
  </div>

  <p class="offline">Runs entirely offline — no internet, no install, no server.</p>
</div>
</body>
</html>
"""

README_TXT = """IOCL GeM TENDER EVALUATION — OFFLINE DEMO
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
"""

KNOWN_ISSUES_TXT = """KNOWN ISSUES — READ BEFORE DEMOING
==================================

1. ALL FIVE BIDDERS SHOW "ACCEPTED"
   This is a defect, not a result.

   The execution-proof check marks a purchase order as proven if any document
   containing that PO number also contains the word "invoice". GeM contracts
   carry the boilerplate "GST/Tax invoice to be raised in the name of..." — it
   appears 55 times in one contract in this dataset. So every PO is treated as
   proven by the contract itself.

   This contradicts the project's own governing rule: a GeM contract shows an
   order was placed, never that work was completed. Valid proof is an invoice,
   a bank certificate of payment, an execution certificate with executed value,
   or a prior IOCL work record.

   Effect: the gate that should separate bidders is effectively off, so nobody
   is rejected or queried.

   IF ASKED, SAY THIS: the extraction, classification and traceability layers
   are working and visible on screen. The scoring rule on top of them is being
   corrected, and it is a contained fix in one function.

2. STARTUP EXEMPTION IS TOO EAGER
   Exemption triggers on the bare string "DPIIT" anywhere in a bidder's
   documents — including a declaration stating the bidder is NOT a recognised
   startup. ARMS PERIPHERALS is accepted on this basis.

3. ONE PO VALUE PARSED AS ZERO
   ARMS PERIPHERALS' only purchase order reads 0.0 because the value regex fell
   through to its "0" default. A failed parse is currently indistinguishable
   from a genuinely small order.

4. THRESHOLDS DISAGREE WITH THE DOCUMENTATION
   The config uses 1,82,000 / 2,43,000 / 3,03,000 per order. The system
   documentation states 1,51,500 / 2,02,000 / 3,03,000. Confirm against the
   tender's PQC document before quoting figures to a customer.

None of the above affects the parts of the demo that show how the system reads
documents. They affect the final accept/reject decision only.


RESOLVED
--------
- Seven pages previously displayed an OCR failure message instead of text.
  They were re-OCR'd with Gemini 2.5 Flash and now show correct content.
"""


# ── 5. Build ──────────────────────────────────────────────────────────────────

def build(out_root: Path, skip_images: bool, make_zip: bool) -> Path:
    bundle = out_root / BUNDLE_NAME
    if bundle.exists():
        logger.info(f"  cleaning previous bundle at {bundle}")
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    logger.info("[1/6] Pre-rendering any missing page images…")
    rendered = render_missing_page_images()
    logger.info(f"      rendered {rendered} new page image(s)")

    logger.info("[2/6] Snapshotting live API responses…")
    snapshot = snapshot_api()
    logger.info(
        f"      {len(snapshot['vendors'])} vendors, "
        f"{len(snapshot['evaluations'])} evaluations, "
        f"{len(snapshot['groundTruth'])} ground-truth set(s)"
    )

    logger.info("[3/6] Copying dashboard assets…")
    for name in ("style.css", "app.js", "fonts.css"):
        src = DASHBOARD / name
        if src.exists():
            shutil.copy2(src, bundle / name)
    if (DASHBOARD / "fonts").exists():
        shutil.copytree(DASHBOARD / "fonts", bundle / "fonts")

    logger.info("[4/6] Copying page images…")
    images_dst = bundle / "data" / "page_images"
    if skip_images:
        logger.info("      skipped (--skip-images)")
        images_dst.mkdir(parents=True, exist_ok=True)
    else:
        shutil.copytree(DATA / "page_images", images_dst)
    image_count = sum(1 for _ in images_dst.rglob("*.png"))
    logger.info(f"      {image_count} page image(s)")

    logger.info("[5/6] Writing inlined data + offline shim…")
    (bundle / "demo-data.js").write_text(
        "/* Snapshot of the live API. Regenerate with scripts/build_demo.py — do not hand-edit. */\n"
        "window.DEMO_DATA = " + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    (bundle / "demo-shim.js").write_text(SHIM_JS, encoding="utf-8")

    logger.info("[6/6] Rewriting pages for filesystem use…")
    inject = '    <script src="demo-data.js"></script>\n    <script src="demo-shim.js"></script>\n'

    review = (DASHBOARD / "review.html").read_text(encoding="utf-8")
    review = review.replace("/api/page-image/", "data/page_images/")
    review = review.replace("    <script>", inject + "    <script>", 1)
    assert "demo-shim.js" in review and "/api/page-image/" not in review
    (bundle / "review.html").write_text(review, encoding="utf-8")

    index = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    index = index.replace("/api/page-image/", "data/page_images/")
    index = index.replace('href="/style.css"', 'href="style.css"')
    index = index.replace('<script src="/app.js"></script>', inject + '    <script src="app.js"></script>')
    assert "demo-shim.js" in index and "/api/page-image/" not in index
    (bundle / "index.html").write_text(index, encoding="utf-8")

    (bundle / "START-HERE.html").write_text(
        LANDING_HTML.format(
            built=datetime.now(timezone.utc).strftime("%d %b %Y"),
            vendors=len(snapshot["vendors"]),
            images=image_count,
        ),
        encoding="utf-8",
    )
    (bundle / "README-FIRST.txt").write_text(README_TXT, encoding="utf-8")
    (bundle / "KNOWN-ISSUES.txt").write_text(KNOWN_ISSUES_TXT, encoding="utf-8")

    size_mb = sum(f.stat().st_size for f in bundle.rglob("*") if f.is_file()) / 1e6
    logger.info(f"\n✓ Bundle ready: {bundle}  ({size_mb:.0f} MB)")

    if make_zip:
        logger.info("  creating zip…")
        archive = shutil.make_archive(str(out_root / BUNDLE_NAME), "zip", root_dir=out_root, base_dir=BUNDLE_NAME)
        logger.info(f"✓ Shareable zip: {archive}  ({Path(archive).stat().st_size / 1e6:.0f} MB)")

    return bundle


def main():
    parser = argparse.ArgumentParser(description="Build the offline static demo bundle")
    parser.add_argument("--out", default="dist", help="output directory (default: dist)")
    parser.add_argument("--zip", action="store_true", help="also produce a shareable .zip")
    parser.add_argument("--skip-images", action="store_true", help="don't copy page images (fast rebuild)")
    args = parser.parse_args()

    out_root = Path(args.out)
    if not out_root.is_absolute():
        out_root = WORKSPACE / out_root
    out_root.mkdir(parents=True, exist_ok=True)
    build(out_root, args.skip_images, args.zip)


if __name__ == "__main__":
    main()
