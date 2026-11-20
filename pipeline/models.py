"""
Pydantic data models for all entities extracted from tender bid documents.
These models ensure type safety, validation, and JSON serialization.
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


# ─── Enums ────────────────────────────────────────────────────────────────────

class DocumentType(str, Enum):
    PURCHASE_ORDER = "PURCHASE_ORDER"
    INVOICE = "INVOICE"
    EXECUTION_PROOF = "EXECUTION_PROOF"
    MAF_OEM_AUTH = "MAF_OEM_AUTH"
    UDYAM_MSME = "UDYAM_MSME"
    GST_CERTIFICATE = "GST_CERTIFICATE"
    PAN_CARD = "PAN_CARD"
    ISO_CERTIFICATE = "ISO_CERTIFICATE"
    BID_SECURITY = "BID_SECURITY"
    BOQ_COMPLIANCE = "BOQ_COMPLIANCE"
    SELF_DECLARATION = "SELF_DECLARATION"
    TURNOVER_CERT = "TURNOVER_CERT"
    ATC_ACCEPTANCE = "ATC_ACCEPTANCE"
    GEM_BID_COPY = "GEM_BID_COPY"
    OTHER = "OTHER"


class PQCStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    QUERIED = "QUERIED"


class SimilarItemMatch(str, Enum):
    YES = "YES"
    NO = "NO"
    PARTIAL = "PARTIAL"


class ExecutionProofStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    NOT_SUBMITTED = "NOT_SUBMITTED"
    PARTIAL = "PARTIAL"


# ─── Source Traceability ──────────────────────────────────────────────────────

class BoundingBox(BaseModel):
    """Bounding box coordinates for source traceability."""
    x_min: float = Field(..., description="Left edge as percentage of page width (0-100)")
    y_min: float = Field(..., description="Top edge as percentage of page height (0-100)")
    x_max: float = Field(..., description="Right edge as percentage of page width (0-100)")
    y_max: float = Field(..., description="Bottom edge as percentage of page height (0-100)")


class SourceReference(BaseModel):
    """Links an extracted value back to its source document location."""
    source_file: str = Field(..., description="Filename of the source PDF")
    source_page: int = Field(..., description="1-indexed page number")
    bbox: Optional[BoundingBox] = Field(None, description="Bounding box of the value on the page")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Extraction confidence score")
    raw_text: str = Field("", description="Surrounding raw text for context")
    page_image_path: Optional[str] = Field(None, description="Path to the rendered page image PNG")


class ExtractedValue(BaseModel):
    """A single extracted data point with full traceability."""
    value: str = Field(..., description="The extracted value as a string")
    source: Optional[SourceReference] = Field(None, description="Source location reference")


# ─── Document-Level Models ────────────────────────────────────────────────────

class PageData(BaseModel):
    """Data for a single page of a PDF."""
    page_number: int
    text: str = ""
    has_extractable_text: bool = True
    image_path: Optional[str] = None
    ocr_text: Optional[str] = None
    bboxes: list[dict] = Field(default_factory=list, description="List of {text, bbox} from VLM")


class DocumentSegment(BaseModel):
    """Represents a logical sub-document segment within a PDF."""
    start_page: int = Field(..., description="1-indexed starting page number")
    end_page: int = Field(..., description="1-indexed ending page number")
    document_type: DocumentType = Field(..., description="Classified type of this segment")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Calibrated classification confidence from Liquid AI Decision Model")
    summary: str = Field("", description="1-sentence summary of this segment")
    reasoning: str = Field("", description="Auditing rationale for classification")


class DocumentMetadata(BaseModel):
    """Metadata for a single document (PDF file) in a vendor's bid."""
    filename: str
    filepath: str
    file_size_bytes: int = 0
    num_pages: int = 0
    document_type: DocumentType = DocumentType.OTHER
    classification_confidence: float = Field(0.0, ge=0.0, le=1.0, description="Calibrated confidence score from Liquid AI Decision Model")
    pages: list[PageData] = Field(default_factory=list)
    summary: str = ""
    segments: list[DocumentSegment] = Field(default_factory=list)



# ─── Extracted Entity Models ──────────────────────────────────────────────────

class PurchaseOrderData(BaseModel):
    """Extracted data from a Purchase Order document."""
    po_number: ExtractedValue
    issuing_organization: ExtractedValue
    po_date: ExtractedValue
    po_value: ExtractedValue
    po_value_numeric: Optional[float] = None
    currency: str = "INR"
    items_description: Optional[ExtractedValue] = None
    contract_type: Optional[str] = None
    meets_similar_item: SimilarItemMatch = SimilarItemMatch.NO
    similar_item_reasoning: str = ""
    execution_proof_status: ExecutionProofStatus = ExecutionProofStatus.NOT_SUBMITTED
    execution_proof_docs: list[str] = Field(default_factory=list)
    date_within_window: Optional[bool] = None


class MAFData(BaseModel):
    """Extracted data from a Manufacturer Authorization Form."""
    oem_name: ExtractedValue
    authorized_vendor: Optional[ExtractedValue] = None
    product_category: Optional[ExtractedValue] = None
    validity_period: Optional[ExtractedValue] = None
    reference_tender: Optional[ExtractedValue] = None
    maf_number: Optional[ExtractedValue] = None


class UdyamData(BaseModel):
    """Extracted data from Udyam/MSME Registration Certificate."""
    udyam_number: ExtractedValue
    enterprise_name: Optional[ExtractedValue] = None
    enterprise_type: Optional[ExtractedValue] = None
    major_activity: Optional[ExtractedValue] = None
    social_category: Optional[ExtractedValue] = None


class GSTData(BaseModel):
    """Extracted data from GST Registration Certificate."""
    gstin: ExtractedValue
    legal_name: Optional[ExtractedValue] = None
    trade_name: Optional[ExtractedValue] = None
    registration_date: Optional[ExtractedValue] = None


class TurnoverData(BaseModel):
    """Extracted data from CA-certified Turnover Certificate."""
    ca_name: Optional[ExtractedValue] = None
    vendor_name: Optional[ExtractedValue] = None
    turnover_entries: list[dict] = Field(default_factory=list, description="List of {year, amount}")


class DeclarationData(BaseModel):
    """Extracted data from a self-declaration document."""
    declaration_type: str = ""
    declaration_text: str = ""
    signatory_name: Optional[ExtractedValue] = None


class ISOCertData(BaseModel):
    """Extracted data from an ISO certificate."""
    certificate_number: Optional[ExtractedValue] = None
    standard: Optional[ExtractedValue] = None
    scope: Optional[ExtractedValue] = None
    validity: Optional[ExtractedValue] = None


# ─── Vendor-Level Models ─────────────────────────────────────────────────────

class VendorExtraction(BaseModel):
    """All extracted data for a single vendor."""
    vendor_name: str
    vendor_folder: str
    schema_version: str = "1.0"
    documents: list[DocumentMetadata] = Field(default_factory=list)
    purchase_orders: list[PurchaseOrderData] = Field(default_factory=list)
    maf_authorizations: list[MAFData] = Field(default_factory=list)
    udyam_registrations: list[UdyamData] = Field(default_factory=list)
    gst_certificates: list[GSTData] = Field(default_factory=list)
    turnover_certificates: list[TurnoverData] = Field(default_factory=list)
    declarations: list[DeclarationData] = Field(default_factory=list)
    iso_certificates: list[ISOCertData] = Field(default_factory=list)
    vendor_summary: str = ""


# ─── PQC Evaluation Models ───────────────────────────────────────────────────

class POEvaluation(BaseModel):
    """Evaluation result for a single Purchase Order against PQC criteria."""
    po_number: str
    issuing_org: str
    po_date: str
    po_value: float
    meets_similar_item: bool
    similar_item_reasoning: str
    similar_item_match_type: SimilarItemMatch = SimilarItemMatch.NO
    meets_value_threshold: bool
    threshold_used: str = ""
    execution_proof_available: bool
    execution_proof_type: str = ""
    date_within_window: bool
    is_qualifying: bool
    disqualification_reason: str = ""


class PQCEvaluation(BaseModel):
    """Full PQC evaluation result for a single vendor."""
    vendor_name: str
    schema_version: str = "1.0"
    financial_pqc: str = "Not Applicable"
    po_evaluations: list[POEvaluation] = Field(default_factory=list)
    qualifying_orders: list[POEvaluation] = Field(default_factory=list)
    criteria_met: Optional[str] = None
    status: PQCStatus
    recommendation: str
    queries: list[str] = Field(default_factory=list)
    detailed_reasoning: str = ""
    mse_exemption_applicable: bool = False


# ─── Top-Level Pipeline Output ────────────────────────────────────────────────

class PipelineOutput(BaseModel):
    """Complete output of the processing pipeline for one vendor."""
    vendor: VendorExtraction
    evaluation: PQCEvaluation
