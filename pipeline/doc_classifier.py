"""
Document Classifier — classifies and segments each PDF in a vendor's bid
into logical document types using heuristic pre-classification
and local Llama-3-8b fallback segmentation.
"""

import os
import json
import re
import logging
import time
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from pipeline.models import DocumentMetadata, DocumentType, DocumentSegment
from pipeline.pdf_processor import get_full_text
from pipeline.ollama_client import OllamaClient, ollama
from pipeline.liquid_decision import liquid_decision_client

logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).parent.parent
load_dotenv(dotenv_path=WORKSPACE / ".env")

from pipeline.llm_router import query_llm_json, parse_json_response, get_last_used_backend


CLASSIFIER_MODEL = "llama3:8b"

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

def segment_pdf_via_llm(filename: str, snippets: dict[int, str], client: Optional[OllamaClient] = None, model: str = CLASSIFIER_MODEL) -> list[DocumentSegment]:
    # Construct snippets text
    snippets_text = ""
    for p_num, snippet in sorted(snippets.items()):
        snippets_text += f"Page {p_num} Snippet:\n<document_content>\n{snippet}\n</document_content>\n\n"
        
    total_pages = len(snippets)
    prompt = f"""You are an expert procurement auditor. You are analyzing the page-by-page text snippets of a PDF file submitted by a bidder for a government tender.
The PDF file may contain a single unified document (e.g. Udyam Registration spanning 3 pages) OR multiple distinct documents clubbed/merged together (e.g. a GeM Contract Purchase Order on pages 1-4, followed by a Tax Invoice on page 5, and a Delivery Challan on page 6).

Below are the page-by-page text snippets from the PDF:
{snippets_text}

Your task is to segment this PDF into one or more distinct logical documents.
For each segment, identify:
- start_page (1-indexed, integer)
- end_page (1-indexed, integer)
- document_type (Choose exactly ONE from: PURCHASE_ORDER, INVOICE, EXECUTION_PROOF, MAF_OEM_AUTH, UDYAM_MSME, GST_CERTIFICATE, PAN_CARD, ISO_CERTIFICATE, BID_SECURITY, BOQ_COMPLIANCE, SELF_DECLARATION, TURNOVER_CERT, ATC_ACCEPTANCE, GEM_BID_COPY, OTHER)
- summary (A 1-sentence summary of what this segment contains)
- reasoning (A brief explanation of why this page range belongs to this document type)

Rules:
1. Every page in the PDF (from page 1 to page {total_pages}) MUST be covered by exactly one segment. Do not overlap segments.
2. Contiguous pages of the same document (like a 4-page Purchase Order) should form a single segment.
3. If the entire PDF is a single document, return a single segment spanning from page 1 to page {total_pages}.

STRICT INSTRUCTION: Do NOT copy the document types, summaries, or page ranges from the few-shot JSON example below. The few-shot example is ONLY for illustrating the structure of the JSON output. You must analyze the actual page content of this specific PDF to identify the real document types and page boundaries. For instance, standard bid documents contain multiple GeM contracts, invoices, and CRAC receipt proofs on different pages. Do not assume any page ranges or categories exist unless you clearly see them in the snippets.

Respond with JSON only:
{{
  "segmented_documents": [
    {{
      "start_page": 1,
      "end_page": 4,
      "document_type": "PURCHASE_ORDER",
      "summary": "GeM Contract Agreement No. GEMC-5116877 for supply of cartridges.",
      "reasoning": "Contains standard GeM contract headings, order value, and product specifications."
    }}
  ]
}}"""

    logger.info(f"Segmenting {filename} ({total_pages} pages) via API pipeline...")
    try:
        # Query unified router (returns dict)
        data = query_llm_json(prompt, local_model=model)
        segments_list = data.get("segmented_documents", [])
        
        validated_segments = []
        for seg in segments_list:
            start = int(seg.get("start_page", 1))
            end = int(seg.get("end_page", 1))
            doc_type_str = seg.get("document_type", "OTHER").upper()
            try:
                doc_type = DocumentType(doc_type_str)
            except ValueError:
                doc_type = DocumentType.OTHER
            summary = seg.get("summary", "")
            reasoning = seg.get("reasoning", "")
            
            # Score confidence using Liquid AI's Zero-Token Decision Model instead of generative LLM token guessing
            segment_text = " ".join(snippets.get(p, "") for p in range(start, end + 1))
            conf, model_source = liquid_decision_client.score_decision(
                text=segment_text,
                proposed_type=doc_type.value,
            )
            
            validated_segments.append(DocumentSegment(
                start_page=start,
                end_page=end,
                document_type=doc_type,
                confidence=conf,
                summary=summary,
                reasoning=reasoning
            ))
            
        # Sort by start_page
        validated_segments.sort(key=lambda x: x.start_page)
        return validated_segments
    except Exception as e:
        logger.error(f"Failed to segment PDF {filename} via LLM API: {e}")
        return []


def classify_document(
    doc: DocumentMetadata,
    client: Optional[OllamaClient] = None,
    model: str = CLASSIFIER_MODEL,
) -> DocumentMetadata:
    """
    Classify and segment a single document into logical sub-documents.
    """
    client = client or ollama
    full_text = get_full_text(doc)

    if not full_text.strip():
        logger.warning(f"{doc.filename}: No text available for classification")
        doc.document_type = DocumentType.OTHER
        conf, _ = liquid_decision_client.score_decision("", DocumentType.OTHER.value)
        doc.classification_confidence = conf
        doc.segments = [DocumentSegment(
            start_page=1,
            end_page=max(1, doc.num_pages),
            document_type=DocumentType.OTHER,
            confidence=conf,
            summary="Empty document",
            reasoning="No text was extracted from this document. Liquid AI Decision Model scored prior confidence."
        )]
        return doc

    num_pages = doc.num_pages if doc.num_pages > 0 else 1
    
    # Run LLM segmentation / classification for all documents (heuristics disabled)
    snippets = extract_page_snippets(doc)
    llm_segments = segment_pdf_via_llm(doc.filename, snippets, client, model)
    
    if llm_segments:
        segments = llm_segments
        primary_type = segments[0].document_type
        primary_conf = segments[0].confidence
        # Try to find a PURCHASE_ORDER segment if any exists
        for seg in segments:
            if seg.document_type == DocumentType.PURCHASE_ORDER:
                primary_type = DocumentType.PURCHASE_ORDER
                primary_conf = seg.confidence
                break
        backend = get_last_used_backend()
        primary_reason = f"Segmented into {len(segments)} logical document(s) via {backend} (confidence scored by Liquid AI Decision Model)."
    else:
        # Fallback if LLM fails
        primary_type = DocumentType.OTHER
        doc_text = " ".join(snippets.values())
        primary_conf, _ = liquid_decision_client.score_decision(doc_text, primary_type.value)
        primary_reason = "Classification failed, defaulted to OTHER (scored via Liquid AI Decision Model)."
        
        segments = [DocumentSegment(
            start_page=1,
            end_page=num_pages,
            document_type=primary_type,
            confidence=primary_conf,
            summary=primary_reason,
            reasoning="Segmentation failed or timed out. Scored via Liquid AI Decision Model."
        )]

    doc.document_type = primary_type
    doc.classification_confidence = primary_conf
    doc.summary = primary_reason
    doc.segments = segments
    return doc

def classify_vendor_documents(
    documents: list[DocumentMetadata],
    client: Optional[OllamaClient] = None,
    model: str = CLASSIFIER_MODEL,
) -> list[DocumentMetadata]:
    """
    Classify and segment all documents for a vendor.
    """
    client = client or ollama
    classified = []
    for doc in documents:
        classified_doc = classify_document(doc, client, model)
        classified.append(classified_doc)

    # Log summary with both top-level and segment breakdown
    type_counts = {}
    segment_counts = {}
    for doc in classified:
        t = doc.document_type.value
        type_counts[t] = type_counts.get(t, 0) + 1
        for seg in doc.segments:
            st = seg.document_type.value
            segment_counts[st] = segment_counts.get(st, 0) + 1
    logger.info(f"Classification summary (top-level): {type_counts}")
    logger.info(f"Classification summary (segments): {segment_counts}")

    return classified

