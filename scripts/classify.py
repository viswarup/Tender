import os
import sys
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("classify_bids")

WORKSPACE = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=WORKSPACE / ".env")

sys.path.append(str(WORKSPACE))

from pipeline.models import DocumentMetadata, PageData, DocumentType
from pipeline.doc_classifier import classify_vendor_documents

OCR_DIR = WORKSPACE / "ocr_cache"
OUTPUT_FILE = WORKSPACE / "data/classified_documents.json"
# Load target bidders dynamically from config or bids folder
CONFIG_FILE = WORKSPACE / "config/tender_config.json"
TARGET_BIDDERS = []
if CONFIG_FILE.exists():
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
            TARGET_BIDDERS = cfg.get("target_bidders", [])
    except Exception:
        pass

if not TARGET_BIDDERS:
    # Discover dynamically from bids folder
    bids_folder = WORKSPACE / "raw_bids/ARC Cartridges_Bids"
    if bids_folder.exists():
        TARGET_BIDDERS = sorted([d.name for d in bids_folder.iterdir() if d.is_dir() and not d.name.startswith(".")])
    else:
        TARGET_BIDDERS = [
            "RAVECHI CARTRIDGE",
            "ALANKAR DISTRIBUTORS",
            "ARMS PERIPHERALS",
            "HEAMONS SYSTEMS",
            "LUXMI ENTERPRISES"
        ]

def parse_num_pages_and_text(md_content: str) -> tuple[int, list[PageData]]:
    pages_dict = {}
    lines = md_content.split("\n")
    for line in lines:
        import re
        match = re.match(r"^\[P(\d+)-L\d+\](.*)", line.strip())
        if match:
            p_num = int(match.group(1))
            body = match.group(2)
            if p_num not in pages_dict:
                pages_dict[p_num] = []
            pages_dict[p_num].append(body)
        else:
            if pages_dict:
                last_p = max(pages_dict.keys())
                pages_dict[last_p].append(line)
                
    pages = []
    for p_num, text_lines in sorted(pages_dict.items()):
        pages.append(PageData(
            page_number=p_num,
            text="\n".join(text_lines),
            has_extractable_text=True
        ))
    return len(pages), pages

def classify_all():
    logger.info("Starting batch classification using core pipeline modules...")
    results = {}
    
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE) as f:
                results = json.load(f)
        except Exception:
            results = {}

    for bidder in TARGET_BIDDERS:
        if bidder in results and results[bidder]:
            logger.info(f"Skipping {bidder} (already classified)")
            continue

        bidder_ocr_dir = OCR_DIR / bidder
        if not bidder_ocr_dir.exists():
            logger.info(f"OCR folder not found for {bidder}, skipping...")
            continue
            
        md_files = list(bidder_ocr_dir.glob("*.md"))
        if not md_files:
            continue
            
        logger.info(f"Processing Bidder: {bidder}")
        if bidder not in results:
            results[bidder] = {}
            
        documents_meta = []
        for file in md_files:
            pdf_filename = f"{file.stem}.pdf"
            
            with open(file, "r", encoding="utf-8") as f:
                content = f.read()
                
            num_pages, pages = parse_num_pages_and_text(content)
            
            doc_meta = DocumentMetadata(
                filename=pdf_filename,
                filepath=str(WORKSPACE / "raw_bids/ARC Cartridges_Bids" / bidder / pdf_filename),
                file_size_bytes=len(content),
                num_pages=num_pages,
                document_type=DocumentType.OTHER,
                classification_confidence=0.5,
                pages=pages,
                summary=""
            )
            documents_meta.append(doc_meta)
            
        # Call core pipeline batch classifier
        classified_docs = classify_vendor_documents(documents_meta)
        
        # Update results registry
        for doc in classified_docs:
            segments_list = []
            for seg in doc.segments:
                segments_list.append({
                    "start_page": seg.start_page,
                    "end_page": seg.end_page,
                    "document_type": seg.document_type.value,
                    "confidence": seg.confidence,
                    "summary": seg.summary,
                    "reasoning": seg.reasoning
                })
                
            results[bidder][doc.filename] = {
                "document_type": doc.document_type.value,
                "confidence": doc.classification_confidence,
                "reasoning": doc.summary,
                "source": "liquid_decision_model",
                "is_segmented": len(doc.segments) > 1,
                "segments": segments_list
            }
            logger.info(f"  Result: {doc.filename} -> {doc.document_type.value} | Segments: {len(doc.segments)}")
            
        with open(OUTPUT_FILE, "w") as f:
            json.dump(results, f, indent=2)
            
    logger.info("Classification successfully completed!")

if __name__ == "__main__":
    classify_all()
