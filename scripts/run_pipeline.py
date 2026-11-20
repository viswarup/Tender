import os
import sys
import re
import json
import logging
from pathlib import Path
from typing import Optional, Any
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("execute_pipeline")

WORKSPACE = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=WORKSPACE / ".env")
sys.path.append(str(WORKSPACE))

from pipeline.models import (
    VendorExtraction, DocumentMetadata, DocumentType, PageData,
    DocumentSegment, PipelineOutput, PQCEvaluation
)
from pipeline.data_extractor import extract_vendor_data
from pipeline.pqc_evaluator import PQCEvaluator

def extract_pages_from_markdown(md_content: str) -> list[PageData]:
    """Parse line-tagged Markdown into PageData objects."""
    pages_dict = {}
    lines = md_content.split("\n")
    for line in lines:
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
        text = "\n".join(text_lines).strip()
        pages.append(PageData(
            page_number=p_num,
            text=text,
            has_extractable_text=True
        ))
    return pages

def run_pipeline(config_path=None, output_dir=None, vendor_filter=None, force_rerun=False, dry_run=False):
    # local variables instead of modifying globals
    c_path = Path(config_path) if config_path else WORKSPACE / "config/tender_config.json"
    d_dir = Path(output_dir) if output_dir else WORKSPACE / "data"
    
    vendors_out = d_dir / "vendors"
    evals_out = d_dir / "evaluations"
    vendors_out.mkdir(parents=True, exist_ok=True)
    evals_out.mkdir(parents=True, exist_ok=True)

    if not c_path.exists():
        logger.error(f"Config path {c_path} does not exist.")
        sys.exit(1)
        
    try:
        with open(c_path) as f:
            tender_config = json.load(f)
    except Exception as e:
        logger.error(f"Failed to parse tender_config.json: {e}")
        sys.exit(1)

    bids_dir = WORKSPACE / tender_config.get("bids_folder", "raw_bids/ARC Cartridges_Bids")
    ocr_dir = WORKSPACE / "ocr_cache"

    # Check LLM clients / Ollama
    from pipeline.ollama_client import ollama
    ollama_available = ollama.is_available()
    gemini_key = os.environ.get("GEMINI_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    
    logger.info(f"Ollama server available: {ollama_available}")
    logger.info(f"Gemini API key set: {bool(gemini_key)}")
    logger.info(f"OpenRouter API key set: {bool(openrouter_key)}")
    
    if not ollama_available and not gemini_key and not openrouter_key:
        logger.error("No LLM backends (Ollama, Gemini, or OpenRouter) are available/configured.")
        sys.exit(1)

    # Validate classification registry schema
    class_reg_path = d_dir / "classified_documents.json"
    if not class_reg_path.exists():
        logger.error(f"Classification registry not found at {class_reg_path}")
        sys.exit(1)
        
    classification_registry = {}
    try:
        with open(class_reg_path) as f:
            classification_registry = json.load(f)
        for bidder, docs in classification_registry.items():
            for doc, info in docs.items():
                DocumentType(info.get("document_type", "OTHER"))
    except Exception as e:
        logger.error(f"Invalid schema in classified_documents.json: {e}")
        sys.exit(1)

    # Get target bidders from config or dynamically discover
    target_bidders = tender_config.get("target_bidders")
    if not target_bidders:
        if bids_dir.exists():
            target_bidders = sorted([d.name for d in bids_dir.iterdir() if d.is_dir() and not d.name.startswith(".")])
        else:
            target_bidders = []

    bidders_to_process = target_bidders
    if vendor_filter:
        bidders_to_process = [b for b in target_bidders if vendor_filter.lower() in b.lower()]
        if not bidders_to_process:
            logger.error(f"No target bidders matching filter '{vendor_filter}' found.")
            sys.exit(1)

    for bidder in bidders_to_process:
        bidder_ocr_dir = ocr_dir / bidder
        bidder_bid_dir = bids_dir / bidder
        if not bidder_ocr_dir.exists():
            logger.warning(f"OCR directory not found for {bidder}: {bidder_ocr_dir}")
        if not bidder_bid_dir.exists():
            logger.warning(f"Bid documents directory not found for {bidder}: {bidder_bid_dir}")

    if dry_run:
        logger.info("Dry run check complete. All configuration, directories, and files validated successfully.")
        return []

    evaluator = PQCEvaluator(c_path)
    results = []

    for bidder in bidders_to_process:
        bidder_ocr_dir = ocr_dir / bidder
        if not bidder_ocr_dir.exists():
            logger.info(f"OCR output folder not found for {bidder}, skipping...")
            continue
            
        md_files = list(bidder_ocr_dir.glob("*.md"))
        if not md_files:
            logger.info(f"No processed Markdown files found in {bidder_ocr_dir}, skipping...")
            continue
            
        eval_json_path = evals_out / f"{bidder}.json"
        vendor_json_path = vendors_out / f"{bidder}.json"
        if eval_json_path.exists() and vendor_json_path.exists() and not force_rerun:
            logger.info(f"Skipping bidder {bidder} — already processed. Use --force to reprocess.")
            try:
                with open(vendor_json_path) as vf:
                    vendor_data = VendorExtraction.model_validate_json(vf.read())
                with open(eval_json_path) as ef:
                    evaluation = PQCEvaluation.model_validate_json(ef.read())
                results.append(PipelineOutput(vendor=vendor_data, evaluation=evaluation))
            except Exception as e:
                logger.error(f"Failed to load cached results for {bidder}: {e}. Reprocessing.")
            else:
                continue

        logger.info(f"\n==================================================")
        logger.info(f"Processing Bidder: {bidder}")
        logger.info(f"==================================================")

        documents_meta = []
        bidder_classifications = classification_registry.get(bidder, {})

        for file in md_files:
            pdf_filename = f"{file.stem}.pdf"
            file_info = bidder_classifications.get(pdf_filename, {})
            doc_type_str = file_info.get("document_type", "OTHER")
            doc_type = DocumentType(doc_type_str)
            confidence = file_info.get("confidence", 0.5)
            reasoning = file_info.get("reasoning", "")
            
            with open(file, "r", encoding="utf-8") as f:
                content = f.read()
                
            pages = extract_pages_from_markdown(content)
            
            # Reconstruct segments
            segments = []
            for seg in file_info.get("segments", []):
                try:
                    seg_type = DocumentType(seg["document_type"])
                except ValueError:
                    seg_type = DocumentType.OTHER
                segments.append(DocumentSegment(
                    start_page=seg["start_page"],
                    end_page=seg["end_page"],
                    document_type=seg_type,
                    confidence=seg.get("confidence", 1.0),
                    summary=seg.get("summary", ""),
                    reasoning=seg.get("reasoning", "")
                ))
            
            doc_meta = DocumentMetadata(
                filename=pdf_filename,
                filepath=str(bids_dir / bidder / pdf_filename),
                file_size_bytes=len(content),
                num_pages=len(pages),
                document_type=doc_type,
                classification_confidence=confidence,
                pages=pages,
                summary=reasoning,
                segments=segments
            )
            documents_meta.append(doc_meta)

        # Call the unified data extractor library (eliminates C2 duplication!)
        vendor_data = extract_vendor_data(
            vendor_name=bidder,
            vendor_folder=f"Bid documents/ARC Cartridges_Bids/{bidder}",
            documents=documents_meta
        )

        # Save Vendor data
        with open(vendor_json_path, "w", encoding="utf-8") as f:
            json.dump(vendor_data.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
        logger.info(f"Saved extracted vendor data to: {vendor_json_path}")
        
        # Evaluate PQC Criteria using PQCEvaluator
        evaluation = evaluator.evaluate_vendor(vendor_data)
        
        # Save evaluation results
        with open(eval_json_path, "w", encoding="utf-8") as f:
            json.dump(evaluation.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
        logger.info(f"Saved evaluation results to: {eval_json_path}")
        
        results.append(PipelineOutput(vendor=vendor_data, evaluation=evaluation))
        
    # Generate and save pipeline summary report
    generate_pipeline_summary(evals_out, vendors_out, d_dir, tender_config)
    
    return results

def generate_pipeline_summary(evals_out, vendors_out, d_dir, tender_config):
    logger.info("Generating global pipeline summary...")
    results = []
    
    accepted = []
    rejected = []
    queried = []
    
    for eval_file in evals_out.glob("*.json"):
        with open(eval_file) as f:
            data = json.load(f)
            status = data.get("status")
            v_name = data.get("vendor_name")
            
            if status == "ACCEPTED": accepted.append(v_name)
            elif status == "REJECTED": rejected.append(v_name)
            elif status == "QUERIED": queried.append(v_name)
            
            # Load corresponding vendor file for PO details
            po_count = 0
            vendor_file_path = vendors_out / f"{v_name}.json"
            if vendor_file_path.exists():
                with open(vendor_file_path) as vf:
                    v_data = json.load(vf)
                    po_count = len(v_data.get("purchase_orders", []))
                
            results.append({
                "vendor": v_name,
                "status": status,
                "criteria_met": data.get("criteria_met"),
                "recommendation": data.get("recommendation"),
                "po_count": po_count,
                "qualifying_count": len(data.get("qualifying_orders", [])),
            })
            
    summary = {
        "tender_no": tender_config.get("tender_no", "9400M25A37"),
        "bid_no": tender_config.get("bid_no", "GEM/2025/B/6755890"),
        "total_vendors": len(results),
        "accepted": accepted,
        "rejected": rejected,
        "queried": queried,
        "results": results
    }
    
    summary_path = d_dir / "pipeline_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    logger.info(f"Saved pipeline summary to: {summary_path}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run GeM Tender Evaluation Pipeline")
    parser.add_argument("--force", action="store_true", help="Force reprocessing of all vendors")
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration and inputs without making LLM API calls")
    parser.add_argument("--vendor", type=str, default=None, help="Process only this vendor (exact or substring match)")
    args = parser.parse_args()
    
    run_pipeline(vendor_filter=args.vendor, force_rerun=args.force, dry_run=args.dry_run)
