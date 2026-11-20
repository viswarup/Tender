"""
Re-run OCR on pages whose stored text is an OCR failure message.

When all three OCR backends failed, run_ocr_batch stored its own error string
as the page text. Those pages then display the error in the review UI. This
finds them, re-OCRs from the source PDF via Gemini (OpenRouter as fallback),
and patches both the ocr_cache Markdown and the page text already baked into
data/vendors/*.json.

It deliberately does NOT re-run extraction or PQC evaluation, so vendor
verdicts stay exactly as they are.

Usage:
    python scripts/refix_failed_ocr.py --dry-run   # list affected pages only
    python scripts/refix_failed_ocr.py            # fix them
"""

import argparse
import base64
import json
import logging
import os
import re
import sys
from pathlib import Path

import fitz
from dotenv import load_dotenv

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))
load_dotenv(WORKSPACE / ".env")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("refix_ocr")

FAILURE_MARKER = "[OCR Failed:"
OCR_PROMPT = (
    "Extract all text and layout data from this image exactly as it appears. "
    "Use clean Markdown format. If tables are present, format them as markdown tables. "
    "Do NOT add any conversational headers, explanations, or chatter."
)


def find_failed_pages(md_text: str) -> list[int]:
    """Page numbers whose body contains the stored failure marker."""
    failed = set()
    for line in md_text.split("\n"):
        match = re.match(r"^\[P(\d+)-L\d+\](.*)", line.strip())
        if match and FAILURE_MARKER in match.group(2):
            failed.add(int(match.group(1)))
    return sorted(failed)


def ocr_page(pdf_path: Path, page_number: int, zoom: float = 2.0) -> tuple[str, str]:
    """OCR one PDF page. Returns (text, backend_label). Raises if all fail."""
    doc = fitz.open(str(pdf_path))
    try:
        pix = doc[page_number - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        png = pix.tobytes("png")
    finally:
        doc.close()

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=gemini_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[OCR_PROMPT, types.Part.from_bytes(data=png, mime_type="image/png")],
            )
            if response.text and response.text.strip():
                return response.text.strip(), "Google Gemini (gemini-2.5-flash)"
            logger.warning("      Gemini returned empty text; trying OpenRouter")
        except Exception as e:
            logger.warning(f"      Gemini failed: {str(e)[:140]}")

    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        for model in ("google/gemini-2.5-flash", "meta-llama/llama-3.2-90b-vision-instruct"):
            try:
                from openai import OpenAI
                client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_key, timeout=90.0)
                b64 = base64.b64encode(png).decode("utf-8")
                completion = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": OCR_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]}],
                )
                text = completion.choices[0].message.content
                if text and text.strip():
                    return text.strip(), f"OpenRouter ({model})"
            except Exception as e:
                logger.warning(f"      OpenRouter {model} failed: {str(e)[:140]}")

    raise RuntimeError("all OCR backends failed")


def rewrite_page_block(md_text: str, page_number: int, new_text: str, backend: str) -> str:
    """Replace every line belonging to `page_number` with freshly tagged lines."""
    lines = md_text.split("\n")
    start = end = None
    current = None
    for i, line in enumerate(lines):
        match = re.match(r"^\[P(\d+)-L\d+\]", line.strip())
        if match:
            current = int(match.group(1))
            if current == page_number and start is None:
                start = i
            elif current > page_number and start is not None and end is None:
                end = i
                break
    if start is None:
        raise ValueError(f"page {page_number} not found in markdown")
    if end is None:
        end = len(lines)

    # Trim trailing blank lines so we re-insert exactly one separator
    block_end = end
    while block_end > start and not lines[block_end - 1].strip():
        block_end -= 1

    rebuilt = [f"[P{page_number}-L0] [OCR_MODEL: {backend}]"]
    idx = 1
    for raw in new_text.split("\n"):
        stripped = raw.strip()
        if not stripped:
            rebuilt.append("")
            continue
        rebuilt.append(f"[P{page_number}-L{idx}] {stripped}")
        idx += 1
    rebuilt.append("")

    return "\n".join(lines[:start] + rebuilt + lines[block_end:])


def patch_vendor_json(vendor: str, pdf_filename: str, md_path: Path, pages: list[int]) -> int:
    """Sync the repaired page text into data/vendors/<vendor>.json."""
    from scripts.run_pipeline import extract_pages_from_markdown

    vendor_json = WORKSPACE / "data" / "vendors" / f"{vendor}.json"
    if not vendor_json.exists():
        return 0

    data = json.loads(vendor_json.read_text(encoding="utf-8"))
    fresh = {p.page_number: p.text for p in extract_pages_from_markdown(md_path.read_text(encoding="utf-8"))}

    patched = 0
    for doc in data.get("documents", []):
        if doc.get("filename") != pdf_filename:
            continue
        for page in doc.get("pages", []):
            num = page.get("page_number")
            if num in pages and num in fresh:
                page["text"] = fresh[num]
                if page.get("ocr_text"):
                    page["ocr_text"] = fresh[num]
                patched += 1

    if patched:
        vendor_json.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return patched


def main():
    parser = argparse.ArgumentParser(description="Re-OCR pages that stored an OCR failure message")
    parser.add_argument("--dry-run", action="store_true", help="list affected pages, change nothing")
    args = parser.parse_args()

    config = json.loads((WORKSPACE / "config" / "tender_config.json").read_text())
    bids_dir = WORKSPACE / config.get("bids_folder", "raw_bids/ARC Cartridges_Bids")
    ocr_dir = WORKSPACE / "ocr_cache"

    targets = []
    for md_path in sorted(ocr_dir.glob("*/*.md")):
        md_text = md_path.read_text(encoding="utf-8")
        if FAILURE_MARKER not in md_text:
            continue
        failed = find_failed_pages(md_text)
        if failed:
            targets.append((md_path.parent.name, md_path, failed))

    total = sum(len(f) for _, _, f in targets)
    logger.info(f"Found {total} failed page(s) across {len(targets)} document(s):")
    for vendor, md_path, failed in targets:
        logger.info(f"  {vendor} | {md_path.stem}.pdf | pages {failed}")

    if args.dry_run or not targets:
        return

    fixed = failed_again = 0
    for vendor, md_path, failed in targets:
        pdf_path = bids_dir / vendor / f"{md_path.stem}.pdf"
        if not pdf_path.exists():
            logger.warning(f"  ! source PDF missing: {pdf_path}")
            continue

        md_text = md_path.read_text(encoding="utf-8")
        repaired = []
        for page_number in failed:
            logger.info(f"\n  OCR {vendor} | {pdf_path.name} | page {page_number}…")
            try:
                text, backend = ocr_page(pdf_path, page_number)
            except Exception as e:
                logger.error(f"      still failing: {e}")
                failed_again += 1
                continue
            md_text = rewrite_page_block(md_text, page_number, text, backend)
            repaired.append(page_number)
            fixed += 1
            preview = re.sub(r"\s+", " ", text)[:110]
            logger.info(f"      {backend} -> {len(text)} chars | {preview}…")

        if repaired:
            md_path.write_text(md_text, encoding="utf-8")
            n = patch_vendor_json(vendor, f"{md_path.stem}.pdf", md_path, repaired)
            logger.info(f"      patched markdown + {n} page(s) in data/vendors/{vendor}.json")

    logger.info(f"\nRepaired {fixed} page(s); {failed_again} still failing.")
    if fixed:
        logger.info("Vendor verdicts untouched — rebuild the demo with: python scripts/build_demo.py --zip")


if __name__ == "__main__":
    main()
