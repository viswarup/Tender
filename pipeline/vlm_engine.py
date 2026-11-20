"""
Unified VLM Engine — uses qwen2.5vl:3b for OCR and bounding box extraction
in a single pass. Retries the same VLM with a simpler prompt for difficult pages.

SPEED OPTIMISATIONS (v2):
 - Plain text OCR mode (no JSON/bbox) — 3-5× faster
 - Retry with cooldown to survive Ollama crashes
 - Inter-call sleep to prevent GPU memory pressure
 - Smart page skipping (skip pages that already have good text)
"""

import json
import logging
import time
import threading
from pathlib import Path
from typing import Optional

from pipeline.ollama_client import OllamaClient, ollama
from pipeline.models import PageData, DocumentMetadata, BoundingBox

logger = logging.getLogger(__name__)
_cache_lock = threading.Lock()

# Default models — using same VLM for fallback to avoid model swap overhead
VLM_MODEL = "qwen2.5vl:3b"
OCR_FALLBACK_MODEL = "qwen2.5vl:3b"  # Same model, simpler prompt

# ── Fast plain-text OCR (no bounding boxes — 3-5× faster) ──
FAST_OCR_PROMPT = """Extract ALL text from this document image exactly as written.
Preserve all numbers, dates, currency amounts, names, and formatting.
If text is unclear, include your best guess with [?] marker.
Do NOT add any commentary or explanation — only return the text content."""

# ── Full structured OCR (JSON with bboxes — slower, use selectively) ──
VLM_SYSTEM_PROMPT = """You are a document OCR and analysis system. Given an image of a document page, extract ALL visible text faithfully.

Return your response as a JSON object with this structure:
{
  "page_text": "The complete text content of the page, preserving layout and order",
  "blocks": [
    {
      "text": "text content of this block",
      "type": "heading|paragraph|table_cell|value|label|signature|stamp",
      "bbox": [x_min, y_min, x_max, y_max]
    }
  ]
}

RULES:
- bbox coordinates are percentages (0-100) of image width/height: [left%, top%, right%, bottom%]
- Extract ALL text, including headers, footers, stamps, handwritten notes
- Preserve the reading order (top-to-bottom, left-to-right)
- For tables, extract each cell as a separate block with type "table_cell"
- For monetary values, include currency symbols
- For dates, preserve original format
- If text is unclear, include your best guess with [?] marker
- page_text should be the complete text in natural reading order"""

# Config
MAX_RETRIES = 2
RETRY_DELAY_S = 5    # seconds between retries
INTER_CALL_S = 1     # seconds between consecutive OCR calls


def _wait_for_ollama(client: OllamaClient, max_wait: int = 30) -> bool:
    """Wait until Ollama server is responsive again."""
    for i in range(max_wait // 3):
        if client.is_available():
            return True
        logger.info(f"  Waiting for Ollama to recover... ({(i+1)*3}s)")
        time.sleep(3)
    return False


def run_vlm_ocr(
    page: PageData,
    client: Optional[OllamaClient] = None,
    model: str = VLM_MODEL,
    fast_mode: bool = True,
) -> PageData:
    """
    Run VLM OCR on a single page.

    Args:
        page: PageData with image_path set.
        client: OllamaClient instance.
        model: VLM model to use.
        fast_mode: If True, extract plain text only (3-5× faster).
                   If False, extract JSON with bounding boxes.

    Returns:
        Updated PageData with ocr_text (and bboxes if not fast_mode).
    """
    if not page.image_path or not Path(page.image_path).exists():
        logger.warning(f"No image available for page {page.page_number}")
        return page

    client = client or ollama

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            img_b64 = OllamaClient.image_to_base64(page.image_path)

            if fast_mode:
                # ── FAST MODE: plain text, no JSON, no bboxes ──
                response = client.generate(
                    model=model,
                    prompt=FAST_OCR_PROMPT,
                    images=[img_b64],
                    temperature=0.0,
                    num_ctx=4096,
                )
                page.ocr_text = response.strip()
                page.bboxes = []
            else:
                # ── FULL MODE: JSON with bounding boxes ──
                messages = [
                    {"role": "system", "content": VLM_SYSTEM_PROMPT},
                    {"role": "user", "content": "Extract all text and bounding boxes from this document page."},
                ]
                response = client.chat(
                    model=model,
                    messages=messages,
                    images=[img_b64],
                    temperature=0.05,
                    num_ctx=8192,
                    format_json=True,
                )
                try:
                    result = json.loads(response)
                    page.ocr_text = result.get("page_text", "")
                    page.bboxes = _validate_bboxes(result.get("blocks", []))
                except json.JSONDecodeError:
                    page.ocr_text = response.strip()
                    page.bboxes = []

            if page.ocr_text:
                logger.info(
                    f"  OCR page {page.page_number}: {len(page.ocr_text)} chars "
                    f"({'fast' if fast_mode else 'full'} mode)"
                )
                return page
            else:
                logger.warning(f"  OCR page {page.page_number}: empty result (attempt {attempt})")

        except Exception as e:
            logger.error(f"  OCR page {page.page_number} failed (attempt {attempt}): {e}")
            if attempt < MAX_RETRIES:
                logger.info(f"  Cooling down {RETRY_DELAY_S}s before retry...")
                time.sleep(RETRY_DELAY_S)
                _wait_for_ollama(client)

    # All VLM retries exhausted → try fallback
    logger.info(f"  VLM failed for page {page.page_number}, trying fallback OCR...")
    return run_ocr_fallback(page, client)


def _validate_bboxes(blocks: list) -> list[dict]:
    """Validate and clamp bounding box coordinates."""
    validated = []
    for block in blocks:
        if isinstance(block, dict) and "text" in block:
            bbox = block.get("bbox", [])
            if isinstance(bbox, list) and len(bbox) == 4:
                try:
                    bbox = [max(0, min(100, float(v))) for v in bbox]
                    block["bbox"] = bbox
                except (ValueError, TypeError):
                    block["bbox"] = []
            validated.append(block)
    return validated


def run_ocr_fallback(
    page: PageData,
    client: Optional[OllamaClient] = None,
    model: str = OCR_FALLBACK_MODEL,
) -> PageData:
    """
    Fallback OCR using the same VLM for pages where the first attempt fails.
    """
    if not page.image_path or not Path(page.image_path).exists():
        return page

    client = client or ollama

    try:
        img_b64 = OllamaClient.image_to_base64(page.image_path)
        response = client.generate(
            model=model,
            prompt=FAST_OCR_PROMPT,
            images=[img_b64],
            temperature=0.0,
            num_ctx=4096,
        )
        page.ocr_text = response.strip()
        page.bboxes = []

        if page.ocr_text:
            logger.info(f"  Fallback OCR page {page.page_number}: {len(page.ocr_text)} chars")
        else:
            logger.warning(f"  Fallback OCR page {page.page_number}: empty")

    except Exception as e:
        logger.error(f"  Fallback OCR also failed page {page.page_number}: {e}")

    return page


def ocr_document(
    doc: DocumentMetadata,
    client: Optional[OllamaClient] = None,
    model: str = VLM_MODEL,
    fast_mode: bool = True,
) -> DocumentMetadata:
    """
    Run OCR on pages that need it (no extractable text).
    Utilises an OCR cache to avoid re-running identical pages.

    Args:
        doc: DocumentMetadata with pages populated.
        client: OllamaClient instance.
        model: VLM model for OCR.
        fast_mode: Use fast plain-text mode (recommended for speed).

    Returns:
        Updated DocumentMetadata with OCR text filled in.
    """
    client = client or ollama

    pages_needing_ocr = [
        p for p in doc.pages
        if not p.has_extractable_text and p.image_path and Path(p.image_path).exists()
    ]

    if not pages_needing_ocr:
        return doc

    # Load OCR cache if exists
    cache_path = Path("data/ocr_cache.json")
    ocr_cache = {}
    with _cache_lock:
        if cache_path.exists():
            try:
                with cache_path.open("r", encoding="utf-8") as f:
                    ocr_cache = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load OCR cache: {e}")

    logger.info(f"  OCR: {len(pages_needing_ocr)} of {len(doc.pages)} pages need processing")
    cache_updated = False

    for i, page in enumerate(pages_needing_ocr):
        cache_key = f"{doc.filename}_page_{page.page_number}"
        if cache_key in ocr_cache:
            page.ocr_text = ocr_cache[cache_key]
            page.bboxes = []
            page.has_extractable_text = True
            logger.info(f"  OCR page {page.page_number} loaded from cache")
            continue

        # Inter-call sleep to prevent GPU overload (skip before first call)
        if i > 0:
            time.sleep(INTER_CALL_S)

        run_vlm_ocr(page, client, model, fast_mode=fast_mode)

        # Mark as having text if OCR succeeded
        if page.ocr_text and len(page.ocr_text.strip()) >= 30:
            page.has_extractable_text = True
            ocr_cache[cache_key] = page.ocr_text
            cache_updated = True

    # Save updated cache if needed
    if cache_updated:
        with _cache_lock:
            # Re-load the cache to avoid overwriting concurrent updates
            if cache_path.exists():
                try:
                    with cache_path.open("r", encoding="utf-8") as f:
                        current_cache = json.load(f)
                        current_cache.update(ocr_cache)
                        ocr_cache = current_cache
                except Exception:
                    pass
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with cache_path.open("w", encoding="utf-8") as f:
                    json.dump(ocr_cache, f, indent=2, ensure_ascii=False)
                logger.info("  OCR cache updated and saved")
            except Exception as e:
                logger.warning(f"Failed to save OCR cache: {e}")

    return doc


def find_value_bbox(
    page: PageData,
    target_value: str,
) -> Optional[BoundingBox]:
    """
    Find the bounding box for a specific value on a page.
    """
    if not page.bboxes:
        return None

    target_lower = target_value.lower().strip()

    for block in page.bboxes:
        block_text = block.get("text", "").lower().strip()
        bbox = block.get("bbox", [])

        if target_lower in block_text and len(bbox) == 4:
            return BoundingBox(
                x_min=bbox[0],
                y_min=bbox[1],
                x_max=bbox[2],
                y_max=bbox[3],
            )

    return None
