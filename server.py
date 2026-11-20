"""
FastAPI Server — serves the dashboard UI and provides API endpoints
for vendor data, evaluations, document viewing, and chatbot.

Start with: python server.py
Then open: http://localhost:8000
"""

import os
import json
import logging
import sys
import re
import datetime
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import APIKeyHeader, APIKeyQuery
from pydantic import BaseModel
import uvicorn
import fitz  # PyMuPDF

# Thread-safe global check for RAG availability
logger = logging.getLogger(__name__)

# ── Paths ──
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
DASHBOARD_DIR = BASE_DIR / "dashboard"

# Cache for config file
_config_cache = None

def get_cached_config():
    global _config_cache
    if _config_cache is None:
        config_path = CONFIG_DIR / "tender_config.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    _config_cache = json.load(f)
            except Exception as e:
                logger.error(f"Failed to read config: {e}")
                _config_cache = {}
        else:
            _config_cache = {}
    return _config_cache

# Load config to get bids folder
config_data = get_cached_config()
BIDS_DIR = BASE_DIR / config_data.get("bids_folder", "raw_bids/ARC Cartridges_Bids")

app = FastAPI(
    title="Tender Evaluation System",
    description="AI-powered commercial evaluation for IOCL GeM tender bids",
    version="1.0.0",
)

# RAG availability check at startup
RAG_AVAILABLE = False
try:
    from chatbot.rag_engine import answer_question
    RAG_AVAILABLE = True
    logger.info("Chatbot RAG engine successfully loaded.")
except ImportError as e:
    logger.warning(f"RAG engine not available, falling back to simple chat: {e}")

# API Key Verification
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)
api_key_query = APIKeyQuery(name="api_key", auto_error=False)

def verify_api_key(
    api_key_h: Optional[str] = Depends(api_key_header),
    api_key_q: Optional[str] = Depends(api_key_query)
):
    expected_key = os.environ.get("API_KEY")
    if expected_key:
        api_key = api_key_h or api_key_q
        if not api_key or api_key != expected_key:
            raise HTTPException(status_code=403, detail="Could not validate credentials")

# Input Path Sanitization
def sanitize_input(value: str, name: str = "parameter"):
    if not value:
        return
    if ".." in value or "/" in value or "\\" in value:
        raise HTTPException(status_code=400, detail=f"Invalid character in {name}")

# ── Serve Dashboard ──
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the main dashboard HTML."""
    index_path = DASHBOARD_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>Dashboard not built yet. Run the pipeline first.</h1>", 404)
    api_key = os.environ.get("API_KEY", "")
    html_content = index_path.read_text(encoding="utf-8")
    # Inject api-key meta tag so app.js can read it
    html_content = html_content.replace("</head>", f'<meta name="api-key" content="{api_key}"></head>')
    return HTMLResponse(html_content)

@app.get("/review", response_class=HTMLResponse)
async def serve_review():
    """Serve the review UI HTML."""
    review_path = DASHBOARD_DIR / "review.html"
    if not review_path.exists():
        return HTMLResponse("<h1>Review UI not built yet.</h1>", 404)
    api_key = os.environ.get("API_KEY", "")
    html_content = review_path.read_text(encoding="utf-8")
    # Inject api-key meta tag so review page can read it
    html_content = html_content.replace("</head>", f'<meta name="api-key" content="{api_key}"></head>')
    return HTMLResponse(html_content)


@app.get("/style.css")
async def serve_css():
    return FileResponse(DASHBOARD_DIR / "style.css", media_type="text/css")


@app.get("/app.js")
async def serve_js():
    return FileResponse(DASHBOARD_DIR / "app.js", media_type="application/javascript")


@app.get("/fonts.css")
async def serve_fonts_css():
    """Locally vendored webfont stylesheet (keeps the UI fully offline)."""
    return FileResponse(DASHBOARD_DIR / "fonts.css", media_type="text/css")


@app.get("/fonts/{filename}")
async def serve_font(filename: str):
    """Serve a vendored woff2 face."""
    sanitize_input(filename, "filename")
    font_path = (DASHBOARD_DIR / "fonts" / filename).resolve()
    if not font_path.is_relative_to((DASHBOARD_DIR / "fonts").resolve()) or not font_path.exists():
        raise HTTPException(404, "Font not found")
    return FileResponse(font_path, media_type="font/woff2")


# ── API: Config ──
@app.get("/api/config", dependencies=[Depends(verify_api_key)])
async def get_config():
    """Get tender configuration."""
    return get_cached_config()


# ── API: Pipeline Summary ──
@app.get("/api/summary", dependencies=[Depends(verify_api_key)])
async def get_summary():
    """Get pipeline execution summary."""
    summary_path = DATA_DIR / "pipeline_summary.json"
    if not summary_path.exists():
        raise HTTPException(404, "Pipeline has not been run yet")
    with open(summary_path) as f:
        return json.load(f)


# ── API: Vendor List ──
@app.get("/api/vendors", dependencies=[Depends(verify_api_key)])
async def list_vendors():
    """List all vendors with their evaluation status."""
    vendors_dir = DATA_DIR / "vendors"
    evals_dir = DATA_DIR / "evaluations"

    if not vendors_dir.exists():
        return []

    vendors = []
    for vendor_file in sorted(vendors_dir.glob("*.json")):
        vendor_name = vendor_file.stem
        eval_file = evals_dir / f"{vendor_name}.json"

        vendor_info = {"name": vendor_name, "status": "UNKNOWN"}
        try:
            with open(vendor_file) as f:
                vendor_data = json.load(f)
                vendor_info["doc_count"] = len(vendor_data.get("documents", []))
                vendor_info["po_count"] = len(vendor_data.get("purchase_orders", []))
                vendor_info["has_maf"] = len(vendor_data.get("maf_authorizations", [])) > 0
                vendor_info["has_udyam"] = len(vendor_data.get("udyam_registrations", [])) > 0

            if eval_file.exists():
                with open(eval_file) as f:
                    eval_data = json.load(f)
                    vendor_info["status"] = eval_data.get("status", "UNKNOWN")
                    vendor_info["criteria_met"] = eval_data.get("criteria_met")
                    vendor_info["recommendation"] = eval_data.get("recommendation")
        except Exception as e:
            logger.error(f"Error loading vendor {vendor_name}: {e}")

        vendors.append(vendor_info)

    return vendors


# ── API: Vendor Detail ──
@app.get("/api/vendor/{vendor_name}", dependencies=[Depends(verify_api_key)])
async def get_vendor(vendor_name: str):
    """Get detailed vendor extraction data."""
    sanitize_input(vendor_name, "vendor_name")
    vendor_file = DATA_DIR / "vendors" / f"{vendor_name}.json"
    if not vendor_file.exists():
        raise HTTPException(404, f"Vendor '{vendor_name}' not found")
    with open(vendor_file) as f:
        return json.load(f)


# ── API: Evaluation Detail ──
@app.get("/api/evaluation/{vendor_name}", dependencies=[Depends(verify_api_key)])
async def get_evaluation(vendor_name: str):
    """Get PQC evaluation for a vendor."""
    sanitize_input(vendor_name, "vendor_name")
    eval_file = DATA_DIR / "evaluations" / f"{vendor_name}.json"
    if not eval_file.exists():
        raise HTTPException(404, f"Evaluation for '{vendor_name}' not found")
    with open(eval_file) as f:
        return json.load(f)


@app.get("/api/ground-truth/{vendor_name}", dependencies=[Depends(verify_api_key)])
async def get_ground_truth_validation(vendor_name: str):
    """Compare one vendor's current evaluation with PQC1 ground truth."""
    sanitize_input(vendor_name, "vendor_name")
    if vendor_name.strip().upper() != "HEAMONS SYSTEMS":
        raise HTTPException(501, "Ground truth validation is currently configured for HEAMONS only")

    gt_file = DATA_DIR / "ground_truth" / "pqc1_criteria_summary.json"
    eval_file = DATA_DIR / "evaluations" / "HEAMONS SYSTEMS.json"
    vendor_file = DATA_DIR / "vendors" / "HEAMONS SYSTEMS.json"
    if not gt_file.exists():
        raise HTTPException(404, "Ground truth file not found")
    if not eval_file.exists() or not vendor_file.exists():
        raise HTTPException(404, "Run HEAMONS pipeline before validation")

    with open(gt_file) as f:
        gt = json.load(f)
    with open(eval_file) as f:
        evaluation = json.load(f)
    with open(vendor_file) as f:
        vendor = json.load(f)

    heamons_gt = gt.get("heamons_ground_truth", {})
    expected_po = heamons_gt.get("qualifying_po", {})
    extracted_pos = vendor.get("purchase_orders", [])
    po_match = next(
        (
            po for po in extracted_pos
            if po.get("po_number", {}).get("value") == expected_po.get("po_number")
        ),
        None,
    )
    actual_status = evaluation.get("status")
    expected_status = heamons_gt.get("status")
    actual_qualifying = [
        po.get("po_number") for po in evaluation.get("qualifying_orders", [])
    ]

    checks = [
        {
            "label": "Final status",
            "expected": expected_status,
            "actual": actual_status,
            "passed": actual_status == expected_status,
        },
        {
            "label": "Ground-truth PO extracted",
            "expected": expected_po.get("po_number"),
            "actual": po_match.get("po_number", {}).get("value") if po_match else None,
            "passed": po_match is not None,
        },
        {
            "label": "Ground-truth PO value",
            "expected": expected_po.get("po_value_inr"),
            "actual": po_match.get("po_value_numeric") if po_match else None,
            "passed": bool(po_match) and po_match.get("po_value_numeric") == expected_po.get("po_value_inr"),
        },
        {
            "label": "Ground-truth PO qualifies",
            "expected": expected_po.get("po_number"),
            "actual": ", ".join(actual_qualifying),
            "passed": expected_po.get("po_number") in actual_qualifying,
        },
    ]

    return {
        "vendor": "HEAMONS SYSTEMS",
        "criteria": gt.get("commercial_experience", {}),
        "ground_truth": heamons_gt,
        "evaluation": evaluation,
        "matched_po": po_match,
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
    }


# ── API: Document Page Image ──
@app.get("/api/page-image/{vendor_name}/{filename}", dependencies=[Depends(verify_api_key)])
async def get_page_image(vendor_name: str, filename: str):
    """Get a rendered page image, rendering it on the fly if it doesn't exist."""
    sanitize_input(vendor_name, "vendor_name")
    sanitize_input(filename, "filename")

    image_path = DATA_DIR / "page_images" / vendor_name / filename
    
    # Path traversal protection validation (S2)
    resolved_image_path = image_path.resolve()
    resolved_page_images_dir = (DATA_DIR / "page_images").resolve()
    if not resolved_image_path.is_relative_to(resolved_page_images_dir):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not image_path.exists():
        # Try to render on the fly
        match = re.match(r"^(.*)_page_(\d+)\.png$", filename)
        if match:
            pdf_stem = match.group(1)
            page_num = int(match.group(2))
            
            # Find the original PDF in Bid documents folder
            pdf_dir = BIDS_DIR / vendor_name
            pdf_path = None
            if pdf_dir.exists():
                for p in pdf_dir.glob("*.pdf"):
                    if p.stem == pdf_stem:
                        pdf_path = p
                        break
            
            if pdf_path and pdf_path.exists():
                try:
                    logger.info(f"Dynamically rendering page {page_num} for {pdf_path.name}...")
                    image_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Render using PyMuPDF (fitz)
                    doc = fitz.open(str(pdf_path))
                    if 0 <= page_num - 1 < len(doc):
                        page = doc[page_num - 1]
                        zoom = 160 / 72  # 160 DPI (matches pipeline standard)
                        mat = fitz.Matrix(zoom, zoom)
                        pix = page.get_pixmap(matrix=mat)
                        pix.save(str(image_path))
                        doc.close()
                    else:
                        doc.close()
                        raise HTTPException(404, f"Page number {page_num} out of bounds")
                except Exception as e:
                    logger.error(f"Failed to render page image dynamically: {e}", exc_info=True)
                    raise HTTPException(500, f"Error rendering page: {e}")
            else:
                raise HTTPException(404, f"Original PDF not found for stem {pdf_stem}")
        else:
            raise HTTPException(404, f"Filename format not matched: {filename}")
            
    return FileResponse(image_path, media_type="image/png")


# ── API: List Page Images ──
@app.get("/api/page-images/{vendor_name}", dependencies=[Depends(verify_api_key)])
async def list_page_images(vendor_name: str):
    """List all page images for a vendor."""
    sanitize_input(vendor_name, "vendor_name")
    images_dir = DATA_DIR / "page_images" / vendor_name
    if not images_dir.exists():
        return []
    return sorted([f.name for f in images_dir.glob("*.png")])


# ── API: Chatbot ──
class ChatMessage(BaseModel):
    message: str
    history: list[dict] = []


@app.post("/api/chat", dependencies=[Depends(verify_api_key)])
async def chat(msg: ChatMessage):
    """
    RAG-powered chatbot endpoint.
    Uses knowledge graph + vector store for context retrieval.
    """
    if RAG_AVAILABLE:
        try:
            response = answer_question(msg.message, msg.history)
            return {"response": response["answer"], "sources": response.get("sources", [])}
        except Exception as e:
            logger.error(f"RAG Chat error: {e}")
            # fall through to fallback
            
    return _simple_chat_fallback(msg.message)


def _simple_chat_fallback(question: str) -> dict:
    """Simple fallback chat using pipeline data when RAG is not set up."""
    summary_path = DATA_DIR / "pipeline_summary.json"
    if not summary_path.exists():
        return {"response": "Pipeline has not been run yet. Run the pipeline first.", "sources": []}

    with open(summary_path) as f:
        summary = json.load(f)

    # Build a context string
    context_lines = [
        f"Tender: {summary.get('tender_no', 'N/A')} (Bid: {summary.get('bid_no', 'N/A')})",
        f"Total vendors: {summary.get('total_vendors', 0)}",
        f"Accepted: {', '.join(summary.get('accepted', []))}",
        f"Rejected: {', '.join(summary.get('rejected', []))}",
        f"Queried: {', '.join(summary.get('queried', []))}",
    ]

    for r in summary.get("results", []):
        context_lines.append(
            f"  {r['vendor']}: {r['status']} — {r.get('recommendation', 'N/A')}"
        )

    context = "\n".join(context_lines)

    # Read chatbot model from cached config
    config = get_cached_config()
    config_model = config.get("models", {}).get("chatbot", "gpt-oss:20b")

    try:
        from pipeline.ollama_client import ollama
        response = ollama.generate(
            model=config_model,
            prompt=f"Based on this tender evaluation data, answer the question.\n\nData:\n{context}\n\nQuestion: {question}\n\nProvide a clear, factual answer.",
            temperature=0.3,
            num_ctx=8192,
        )
        return {"response": response, "sources": ["pipeline_summary.json"]}
    except Exception:
        return {"response": f"Summary data:\n{context}", "sources": []}


# ── API: User Feedback Loop ──
class FeedbackRequest(BaseModel):
    vendor_name: str
    document: str
    field_path: str
    original_value: str
    corrected_value: Optional[str] = None
    status: str
    comment: Optional[str] = None


@app.post("/api/feedback", dependencies=[Depends(verify_api_key)])
async def save_feedback(feedback: FeedbackRequest):
    """Save user feedback on a data extraction field."""
    sanitize_input(feedback.vendor_name, "vendor_name")
    sanitize_input(feedback.document, "document")
    
    feedback_file = DATA_DIR / "user_feedback.json"
    
    feedbacks = []
    if feedback_file.exists():
        try:
            with open(feedback_file, "r") as f:
                feedbacks = json.load(f)
                if not isinstance(feedbacks, list):
                    feedbacks = []
        except Exception as e:
            logger.error(f"Error reading user_feedback.json: {e}")
            feedbacks = []
            
    feedback_data = feedback.model_dump()
    feedback_data["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    feedbacks.append(feedback_data)
    
    try:
        with open(feedback_file, "w") as f:
            json.dump(feedbacks, f, indent=2)
    except Exception as e:
        logger.error(f"Error writing to user_feedback.json: {e}")
        raise HTTPException(500, f"Failed to save feedback: {e}")
        
    return {"status": "success", "message": "Feedback saved successfully"}


# ── API: Run Pipeline ──
class PipelineRequest(BaseModel):
    vendor: Optional[str] = None


@app.post("/api/run-pipeline", dependencies=[Depends(verify_api_key)])
async def trigger_pipeline(req: PipelineRequest):
    """Trigger the processing pipeline (runs asynchronously via asyncio.to_thread)."""
    try:
        from scripts.run_pipeline import run_pipeline
        # Run in a separate thread to avoid blocking the event loop (async blocking fix)
        results = await asyncio.to_thread(
            run_pipeline,
            config_path=CONFIG_DIR / "tender_config.json",
            output_dir=DATA_DIR,
            vendor_filter=req.vendor,
            force_rerun=True
        )
        return {
            "status": "complete",
            "vendors_processed": len(results),
            "accepted": sum(1 for r in results if r.evaluation.status.value == "ACCEPTED"),
            "rejected": sum(1 for r in results if r.evaluation.status.value == "REJECTED"),
            "queried": sum(1 for r in results if r.evaluation.status.value == "QUERIED"),
        }
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        raise HTTPException(500, str(e))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print("\n🚀 Starting Tender Evaluation Server...")
    print(f"   Dashboard: http://localhost:{port}")
    print(f"   API Docs:  http://localhost:{port}/docs\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
