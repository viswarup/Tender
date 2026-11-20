"""
Structured Data Extractor — extracts typed data from classified
documents using the configured local text model.

Each document type has a specialized extraction prompt that returns
structured JSON, which is then parsed into Pydantic models.
"""

import json
import re
import logging
from typing import Optional
from pathlib import Path

import fitz

from pipeline.models import (
    DocumentMetadata,
    DocumentType,
    VendorExtraction,
    PurchaseOrderData,
    MAFData,
    UdyamData,
    GSTData,
    TurnoverData,
    DeclarationData,
    ISOCertData,
    ExtractedValue,
    SourceReference,
    BoundingBox,
    ExecutionProofStatus,
)
from pipeline.pdf_processor import get_full_text
from pipeline.ollama_client import OllamaClient, ollama
from pipeline.llm_router import query_llm_json, parse_json_response
from pipeline.utils import clean_tags, parse_monetary_value, _extract_first, find_text_in_pdf


logger = logging.getLogger(__name__)

EXTRACTOR_MODEL = "gpt-oss:20b"


# Shared helpers

def _vendor_name_from_doc(doc: DocumentMetadata) -> str:
    try:
        return Path(doc.filepath).parent.name
    except Exception:
        return "unknown"


def _render_source_page(doc: DocumentMetadata, page_num: int, dpi: int = 160) -> Optional[str]:
    """Render a source page so the dashboard can display traceability."""
    for page in doc.pages:
        if page.page_number == page_num and page.image_path:
            return page.image_path

    try:
        from pipeline.pdf_processor import render_single_page
        out_dir = Path("data") / "page_images" / _vendor_name_from_doc(doc)
        return render_single_page(doc.filepath, page_num, out_dir, dpi=dpi)
    except Exception as e:
        logger.debug(f"Could not render source page for {doc.filename} page {page_num}: {e}")
        return None



def _find_text_source(doc: DocumentMetadata, value: str) -> tuple[int, Optional[dict], str]:
    """Find an extracted value in the source PDF and return page, bbox, context."""
    return find_text_in_pdf(doc.filepath, value)


def _make_source_ref(
    doc: DocumentMetadata,
    page_num: int = 1,
    value: str = "",
) -> SourceReference:
    """Create a SourceReference for a value extracted from a document."""
    bbox = None
    raw_text = ""
    if value:
        page_num, bbox_dict, raw_text = _find_text_source(doc, value)
        if bbox_dict:
            bbox = BoundingBox(**bbox_dict)

    page_data = None
    if 1 <= page_num <= len(doc.pages):
        page_data = doc.pages[page_num - 1]

    page_image_path = page_data.image_path if page_data else None
    if not page_image_path:
        page_image_path = _render_source_page(doc, page_num)

    return SourceReference(
        source_file=doc.filename,
        source_page=page_num,
        bbox=bbox,
        confidence=0.85 if bbox else 0.5,
        page_image_path=page_image_path,
        raw_text=raw_text,
    )


def _make_extracted_value(value: str, doc: DocumentMetadata, page: int = 1) -> ExtractedValue:
    """Create an ExtractedValue with source reference."""
    return ExtractedValue(
        value=value,
        source=_make_source_ref(doc, page, value),
    )


# Shared helpers are now imported from utils


def _extract_gem_purchase_orders(doc: DocumentMetadata, text: str) -> list[PurchaseOrderData]:
    """
    Deterministically extract GeM contract-style POs.

    GeM contracts are templated enough that regex extraction is safer and much
    faster than asking an LLM to rediscover the same fields.
    """
    matches = list(re.finditer(r"\bGEMC-\d{10,}\b", text, re.IGNORECASE))
    extracted: list[PurchaseOrderData] = []
    seen: set[str] = set()

    for index, match in enumerate(matches):
        po_number = match.group(0).upper()
        if po_number in seen:
            continue
        seen.add(po_number)

        start = max(0, match.start() - 500)
        end = matches[index + 1].start() if index + 1 < len(matches) else min(len(text), match.end() + 5000)
        block = text[start:end]
        cleaned_block = clean_tags(block)

        po_date = _extract_first(
            r"(?:Generated Date|Contract Date|Dated)[^:\n]*:\s*([0-9]{1,2}[-/ ][A-Za-z]{3,9}[-/ ][0-9]{2,4}|[0-9]{1,2}[-/.][0-9]{1,2}[-/.][0-9]{2,4})",
            cleaned_block,
        )
        issuing_org = _extract_first(
            r"Organisation Name\s*:?\s*([^\n|]+)",
            cleaned_block,
        )
        if issuing_org == "Unknown":
            issuing_org = _extract_first(r"Department\s*:?\s*([^\n|]+)", cleaned_block)

        value = _extract_first(
            r"Total Order Value[\s\S]*?([0-9][0-9,]*(?:\.\d+)?)",
            cleaned_block,
            "0",
        )
        if value == "0":
            value = _extract_first(
                r"Price\s*\(Inclusive of[\s\S]*?all[\s\S]*?Duties[\s\S]*?and[\s\S]*?Taxes[\s\S]*?in[\s\S]*?INR\)[\s\S]*?([0-9][0-9,]*(?:\.\d+)?)",
                cleaned_block,
                "0",
            )

        product_names = re.findall(
            r"Product Name\s*:?\s*([^\n|]+(?:\n(?!Brand|Model|HSN|Quantity|Total).{1,120})?)",
            cleaned_block,
            re.IGNORECASE,
        )
        items_description = "; ".join(
            re.sub(r"\s+", " ", item).strip() for item in product_names[:6]
        )
        if not items_description:
            items_description = _extract_first(r"Item Description\s*:?\s*([^\n|]+)", cleaned_block, "")

        po_value_numeric = parse_monetary_value(value)
        po = PurchaseOrderData(
            po_number=_make_extracted_value(po_number, doc),
            issuing_organization=_make_extracted_value(issuing_org, doc),
            po_date=_make_extracted_value(po_date, doc),
            po_value=_make_extracted_value(f"INR {value}", doc),
            po_value_numeric=po_value_numeric,
            currency="INR",
            items_description=_make_extracted_value(items_description, doc) if items_description else None,
            contract_type="GeM Contract",
        )
        extracted.append(po)

    if extracted:
        logger.info(f"  Heuristic GeM extraction found {len(extracted)} PO(s) in {doc.filename}")
    return extracted



# ─── Purchase Order Extraction ────────────────────────────────────────────────

PO_EXTRACTION_PROMPT = """Extract ALL Purchase Orders from this document. The document may contain multiple POs (e.g., a bundle of GeM contracts).

For each PO found, extract:
- po_number: The PO/Contract number (e.g., GEMC-511687706010310)
- issuing_organization: Who issued the PO (e.g., Controller General of Defence Accounts)
- po_date: Date of the PO (preserve original format)
- po_value: Total PO value with currency (e.g., INR 53,84,223)
- items_description: Brief description of items/services ordered
- contract_type: Type of contract (Rate Contract, One-time, GeM Contract, etc.)

DOCUMENT TEXT:
---
{text}
---

Respond with JSON only:
{{
  "purchase_orders": [
    {{
      "po_number": "...",
      "issuing_organization": "...",
      "po_date": "...",
      "po_value": "...",
      "items_description": "...",
      "contract_type": "..."
    }}
  ]
}}

If no purchase orders are found, return: {{"purchase_orders": []}}
Extract ALL POs found in the document, even if there are many."""


def extract_purchase_orders(
    doc: DocumentMetadata,
    client: Optional[OllamaClient] = None,
    model: str = EXTRACTOR_MODEL,
) -> list[PurchaseOrderData]:
    """Extract purchase order data from a document."""
    client = client or ollama
    full_text = get_full_text(doc)
    if not full_text.strip():
        return []

    heuristic_pos = _extract_gem_purchase_orders(doc, full_text)
    if heuristic_pos:
        return heuristic_pos

    try:
        # Truncate to fit context but keep as much as possible
        text_for_llm = full_text[:12000]
        prompt = PO_EXTRACTION_PROMPT.format(text=text_for_llm)

        # Query LLM via unified router
        result = query_llm_json(prompt, local_model=model)
        pos = result.get("purchase_orders", [])

        extracted = []
        for po_data in pos:
            po_number = po_data.get("po_number", "Unknown")
            po_value_str = po_data.get("po_value", "0")
            po_value_numeric = parse_monetary_value(po_value_str)

            po = PurchaseOrderData(
                po_number=_make_extracted_value(po_number, doc),
                issuing_organization=_make_extracted_value(
                    po_data.get("issuing_organization", "Unknown"), doc
                ),
                po_date=_make_extracted_value(po_data.get("po_date", "Unknown"), doc),
                po_value=_make_extracted_value(po_value_str, doc),
                po_value_numeric=po_value_numeric,
                currency=po_data.get("currency", "INR"),
                items_description=_make_extracted_value(
                    po_data.get("items_description", ""), doc
                ) if po_data.get("items_description") else None,
                contract_type=po_data.get("contract_type", ""),
            )
            extracted.append(po)
            logger.info(
                f"  Extracted PO: {po_number} | "
                f"Value: {po_value_str} (₹{po_value_numeric:,.0f} parsed) | "
                f"Org: {po_data.get('issuing_organization', '?')}"
            )

        return extracted

    except json.JSONDecodeError as e:
        logger.warning(f"PO extraction returned invalid JSON for {doc.filename}: {e}")
        return []
    except Exception as e:
        logger.error(f"PO extraction failed for {doc.filename}: {e}")
        return []


# ─── MAF / OEM Authorization Extraction ──────────────────────────────────────

MAF_EXTRACTION_PROMPT = """Extract OEM/Manufacturer Authorization details from this document.

Extract:
- oem_name: Name of the OEM (e.g., HP India, Kyocera)
- authorized_vendor: Name of the authorized vendor/partner
- product_category: Products covered (e.g., toner cartridges, printers)
- validity_period: Validity dates if mentioned
- reference_tender: Tender reference if mentioned

DOCUMENT TEXT:
---
{text}
---

Respond with JSON only:
{{"oem_name": "...", "authorized_vendor": "...", "product_category": "...", "validity_period": "...", "reference_tender": "..."}}"""


def extract_maf(
    doc: DocumentMetadata,
    client: Optional[OllamaClient] = None,
    model: str = EXTRACTOR_MODEL,
) -> list[MAFData]:
    """Extract MAF/OEM authorization data from a document."""
    client = client or ollama
    full_text = get_full_text(doc)
    if not full_text.strip():
        return []

    try:
        prompt = MAF_EXTRACTION_PROMPT.format(text=full_text[:4000])
        result = query_llm_json(prompt, local_model=model)


        maf = MAFData(
            oem_name=_make_extracted_value(result.get("oem_name", "Unknown"), doc),
            authorized_vendor=_make_extracted_value(result.get("authorized_vendor", ""), doc) if result.get("authorized_vendor") else None,
            product_category=_make_extracted_value(result.get("product_category", ""), doc) if result.get("product_category") else None,
            validity_period=_make_extracted_value(result.get("validity_period", ""), doc) if result.get("validity_period") else None,
            reference_tender=_make_extracted_value(result.get("reference_tender", ""), doc) if result.get("reference_tender") else None,
        )
        logger.info(f"  Extracted MAF: OEM={result.get('oem_name')} for {result.get('authorized_vendor')}")
        return [maf]

    except Exception as e:
        logger.error(f"MAF extraction failed for {doc.filename}: {e}")
        return []


# ─── Udyam / MSME Extraction ─────────────────────────────────────────────────

UDYAM_EXTRACTION_PROMPT = """Extract Udyam/MSME Registration details from this document.

Extract:
- udyam_number: The Udyam Registration Number (e.g., UDYAM-XX-XX-XXXXXXX)
- enterprise_name: Name of the enterprise
- enterprise_type: Micro/Small/Medium
- major_activity: Manufacturing or Services

DOCUMENT TEXT:
---
{text}
---

Respond with JSON only:
{{"udyam_number": "...", "enterprise_name": "...", "enterprise_type": "...", "major_activity": "..."}}"""


def extract_udyam(
    doc: DocumentMetadata,
    client: Optional[OllamaClient] = None,
    model: str = EXTRACTOR_MODEL,
) -> list[UdyamData]:
    """Extract Udyam registration data."""
    client = client or ollama
    full_text = get_full_text(doc)
    if not full_text.strip():
        return []

    try:
        prompt = UDYAM_EXTRACTION_PROMPT.format(text=full_text[:4000])
        result = query_llm_json(prompt, local_model=model)


        udyam = UdyamData(
            udyam_number=_make_extracted_value(result.get("udyam_number", "Unknown"), doc),
            enterprise_name=_make_extracted_value(result.get("enterprise_name", ""), doc) if result.get("enterprise_name") else None,
            enterprise_type=_make_extracted_value(result.get("enterprise_type", ""), doc) if result.get("enterprise_type") else None,
            major_activity=_make_extracted_value(result.get("major_activity", ""), doc) if result.get("major_activity") else None,
        )
        logger.info(f"  Extracted Udyam: {result.get('udyam_number')} ({result.get('enterprise_type')})")
        return [udyam]

    except Exception as e:
        logger.error(f"Udyam extraction failed for {doc.filename}: {e}")
        return []


# ─── GST Certificate Extraction ──────────────────────────────────────────────

def extract_gst(
    doc: DocumentMetadata,
    client: Optional[OllamaClient] = None,
    model: str = EXTRACTOR_MODEL,
) -> list[GSTData]:
    """Extract GST certificate data."""
    client = client or ollama
    full_text = get_full_text(doc)
    if not full_text.strip():
        return []

    try:
        prompt = f"""Extract GST Registration details from this document.
Extract: gstin (15-char GSTIN), legal_name, trade_name, registration_date.

DOCUMENT TEXT:
---
{full_text[:3000]}
---

Respond with JSON only:
{{"gstin": "...", "legal_name": "...", "trade_name": "...", "registration_date": "..."}}"""

        result = query_llm_json(prompt, local_model=model)


        gst = GSTData(
            gstin=_make_extracted_value(result.get("gstin", "Unknown"), doc),
            legal_name=_make_extracted_value(result.get("legal_name", ""), doc) if result.get("legal_name") else None,
            trade_name=_make_extracted_value(result.get("trade_name", ""), doc) if result.get("trade_name") else None,
            registration_date=_make_extracted_value(result.get("registration_date", ""), doc) if result.get("registration_date") else None,
        )
        logger.info(f"  Extracted GST: {result.get('gstin')}")
        return [gst]

    except Exception as e:
        logger.error(f"GST extraction failed for {doc.filename}: {e}")
        return []


# ─── Declaration Extraction ───────────────────────────────────────────────────

def extract_declaration(
    doc: DocumentMetadata,
    client: Optional[OllamaClient] = None,
    model: str = EXTRACTOR_MODEL,
) -> list[DeclarationData]:
    """Extract self-declaration details."""
    full_text = get_full_text(doc)
    if not full_text.strip():
        return []

    # Simple heuristic extraction — declarations are usually straightforward
    text_lower = full_text.lower()
    declarations = []

    if "liquidation" in text_lower or "bankrupt" in text_lower:
        declarations.append(DeclarationData(
            declaration_type="Non-Liquidation/Bankruptcy",
            declaration_text=full_text[:500],
        ))

    if "blacklist" in text_lower:
        declarations.append(DeclarationData(
            declaration_type="Non-Blacklisting",
            declaration_text=full_text[:500],
        ))

    if not declarations:
        declarations.append(DeclarationData(
            declaration_type="General Declaration",
            declaration_text=full_text[:500],
        ))

    return declarations


# ─── ISO Certificate Extraction ──────────────────────────────────────────────

def extract_iso(
    doc: DocumentMetadata,
    client: Optional[OllamaClient] = None,
    model: str = EXTRACTOR_MODEL,
) -> list[ISOCertData]:
    """Extract ISO certificate data."""
    full_text = get_full_text(doc)
    if not full_text.strip():
        return []

    cert_number_match = re.search(r'(?:Certificate|Cert)\s*(?:No|Number)[.:]\s*(\S+)', full_text, re.IGNORECASE)
    standard_match = re.search(r'(ISO\s*\d{4,5}(?::\d{4})?)', full_text, re.IGNORECASE)

    cert = ISOCertData(
        certificate_number=_make_extracted_value(
            cert_number_match.group(1) if cert_number_match else "Unknown", doc
        ),
        standard=_make_extracted_value(
            standard_match.group(1) if standard_match else "Unknown", doc
        ),
    )
    return [cert]


# ─── Turnover Certificate Extraction ─────────────────────────────────────────

def extract_turnover(
    doc: DocumentMetadata,
    client: Optional[OllamaClient] = None,
    model: str = EXTRACTOR_MODEL,
) -> list[TurnoverData]:
    """Extract turnover certificate data."""
    client = client or ollama
    full_text = get_full_text(doc)
    if not full_text.strip():
        return []

    try:
        prompt = f"""Extract annual turnover details from this CA-certified turnover certificate.

DOCUMENT TEXT:
---
{full_text[:4000]}
---

Respond with JSON only:
{{"ca_name": "...", "vendor_name": "...", "turnover_entries": [{{"year": "2023-24", "amount": "50,00,000"}}]}}"""

        result = query_llm_json(prompt, local_model=model)


        turnover = TurnoverData(
            ca_name=_make_extracted_value(result.get("ca_name", ""), doc) if result.get("ca_name") else None,
            vendor_name=_make_extracted_value(result.get("vendor_name", ""), doc) if result.get("vendor_name") else None,
            turnover_entries=result.get("turnover_entries", []),
        )
        return [turnover]

    except Exception as e:
        logger.error(f"Turnover extraction failed for {doc.filename}: {e}")
        return []


# ─── Master Extraction Router ────────────────────────────────────────────────

def extract_all(
    doc: DocumentMetadata,
    client: Optional[OllamaClient] = None,
) -> dict:
    """
    Route extraction based on document type.

    Returns:
        Dict with keys like 'purchase_orders', 'maf', 'udyam', etc.
    """
    client = client or ollama
    result = {}

    dt = doc.document_type

    if dt == DocumentType.PURCHASE_ORDER:
        result["purchase_orders"] = extract_purchase_orders(doc, client)
    elif dt == DocumentType.MAF_OEM_AUTH:
        result["maf"] = extract_maf(doc, client)
    elif dt == DocumentType.UDYAM_MSME:
        result["udyam"] = extract_udyam(doc, client)
    elif dt == DocumentType.GST_CERTIFICATE:
        result["gst"] = extract_gst(doc, client)
    elif dt == DocumentType.SELF_DECLARATION:
        result["declarations"] = extract_declaration(doc, client)
    elif dt == DocumentType.ISO_CERTIFICATE:
        result["iso"] = extract_iso(doc, client)
    elif dt == DocumentType.TURNOVER_CERT:
        result["turnover"] = extract_turnover(doc, client)
    elif dt == DocumentType.EXECUTION_PROOF:
        # Try to extract PO data from execution proof docs too
        result["purchase_orders"] = extract_purchase_orders(doc, client)
    elif dt in (DocumentType.BID_SECURITY, DocumentType.BOQ_COMPLIANCE,
                DocumentType.ATC_ACCEPTANCE, DocumentType.GEM_BID_COPY,
                DocumentType.PAN_CARD):
        # These don't need deep extraction — just note they exist
        pass
    elif dt == DocumentType.OTHER:
        # Try PO extraction on unclassified docs only when strong PO signals exist.
        full_text = get_full_text(doc).lower()
        if "gemc-" in full_text or "purchase order" in full_text or "contract no" in full_text:
            pos = extract_purchase_orders(doc, client)
            if pos:
                result["purchase_orders"] = pos

    return result


def extract_vendor_data(
    vendor_name: str,
    vendor_folder: str,
    documents: list[DocumentMetadata],
    client: Optional[OllamaClient] = None,
) -> VendorExtraction:
    """
    Process all documents for a vendor and aggregate extraction results.

    Args:
        vendor_name: Display name of the vendor.
        vendor_folder: Path to vendor's folder.
        documents: List of classified DocumentMetadata.
        client: OllamaClient instance.

    Returns:
        VendorExtraction with all data aggregated.
    """
    client = client or ollama
    vendor = VendorExtraction(
        vendor_name=vendor_name,
        vendor_folder=vendor_folder,
        documents=documents,
    )

    logger.info(f"\n{'='*60}")
    logger.info(f"Extracting data for: {vendor_name}")
    logger.info(f"Documents: {len(documents)}")
    logger.info(f"{'='*60}")

    for doc in documents:
        logger.info(f"\n  Processing: {doc.filename} ({doc.document_type.value})")

        try:
            extracted = extract_all(doc, client)

            # Aggregate results
            if "purchase_orders" in extracted:
                vendor.purchase_orders.extend(extracted["purchase_orders"])
            if "maf" in extracted:
                vendor.maf_authorizations.extend(extracted["maf"])
            if "udyam" in extracted:
                vendor.udyam_registrations.extend(extracted["udyam"])
            if "gst" in extracted:
                vendor.gst_certificates.extend(extracted["gst"])
            if "declarations" in extracted:
                vendor.declarations.extend(extracted["declarations"])
            if "iso" in extracted:
                vendor.iso_certificates.extend(extracted["iso"])
            if "turnover" in extracted:
                vendor.turnover_certificates.extend(extracted["turnover"])

        except Exception as e:
            logger.error(f"  Extraction failed for {doc.filename}: {e}")

    # Cross-reference execution proofs
    _cross_reference_execution_proofs(vendor)

    # Generate vendor summary
    vendor.vendor_summary = _generate_vendor_summary(vendor)

    logger.info(f"\n  Summary for {vendor_name}:")
    logger.info(f"    POs: {len(vendor.purchase_orders)}")
    logger.info(f"    MAFs: {len(vendor.maf_authorizations)}")
    logger.info(f"    Udyam: {len(vendor.udyam_registrations)}")
    logger.info(f"    GST: {len(vendor.gst_certificates)}")
    logger.info(f"    Declarations: {len(vendor.declarations)}")
    logger.info(f"    ISO: {len(vendor.iso_certificates)}")

    return vendor


def _cross_reference_execution_proofs(vendor: VendorExtraction):
    """
    Cross-reference PO documents with execution proof documents.
    If a document classified as EXECUTION_PROOF or INVOICE exists,
    and mentions the same PO number, link them.

    Also checks for self-certification patterns:
    - If PO and invoice are in the same document, treat as self-certified
    - If "self-certification" or "successfully executed" appears in text
    """
    proof_docs = [
        d for d in vendor.documents
        if d.document_type in (
            DocumentType.EXECUTION_PROOF,
            DocumentType.INVOICE,
        )
    ]

    # Also check PO documents for self-certification
    po_docs = [d for d in vendor.documents if d.document_type == DocumentType.PURCHASE_ORDER]

    for po in vendor.purchase_orders:
        po_num = po.po_number.value.lower()

        # Check if the PO document itself contains self-certification
        for doc in po_docs:
            doc_text = get_full_text(doc).lower()
            if po_num.lower() in doc_text:
                if any(kw in doc_text for kw in [
                    "self-certification", "self certification",
                    "successfully executed", "invoice",
                    "supplies against invoice", "execution proof",
                ]):
                    po.execution_proof_status = ExecutionProofStatus.SUBMITTED
                    po.execution_proof_docs.append(doc.filename)
                    break

        # Check dedicated proof documents
        if po.execution_proof_status != ExecutionProofStatus.SUBMITTED:
            for proof_doc in proof_docs:
                proof_text = get_full_text(proof_doc).lower()
                if po_num.lower() in proof_text:
                    po.execution_proof_status = ExecutionProofStatus.SUBMITTED
                    po.execution_proof_docs.append(proof_doc.filename)
                    break

        # If a PO document has multiple pages with invoice data, treat as having proof
        if po.execution_proof_status != ExecutionProofStatus.SUBMITTED:
            for doc in vendor.documents:
                doc_text = get_full_text(doc).lower()
                if (po_num.lower() in doc_text and
                        any(kw in doc_text for kw in ["invoice", "challan", "receipt", "crac"])):
                    po.execution_proof_status = ExecutionProofStatus.SUBMITTED
                    po.execution_proof_docs.append(doc.filename)
                    break


def _generate_vendor_summary(vendor: VendorExtraction) -> str:
    """Generate a human-readable summary of extracted vendor data."""
    lines = [f"Vendor: {vendor.vendor_name}"]
    lines.append(f"Documents submitted: {len(vendor.documents)}")

    if vendor.purchase_orders:
        lines.append(f"Purchase Orders: {len(vendor.purchase_orders)}")
        for po in vendor.purchase_orders:
            val = f"₹{po.po_value_numeric:,.0f}" if po.po_value_numeric else po.po_value.value
            proof = "✓" if po.execution_proof_status == ExecutionProofStatus.SUBMITTED else "✗"
            lines.append(f"  - {po.po_number.value}: {val} from {po.issuing_organization.value} [Proof: {proof}]")

    if vendor.maf_authorizations:
        lines.append(f"OEM Authorizations: {len(vendor.maf_authorizations)}")
        for maf in vendor.maf_authorizations:
            lines.append(f"  - {maf.oem_name.value}")

    if vendor.udyam_registrations:
        lines.append(f"Udyam Registration: {vendor.udyam_registrations[0].udyam_number.value}")

    if vendor.gst_certificates:
        lines.append(f"GST: {vendor.gst_certificates[0].gstin.value}")

    return "\n".join(lines)
