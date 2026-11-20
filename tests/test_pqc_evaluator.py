import json
import pytest
from datetime import datetime
from pathlib import Path
from pipeline.models import (
    VendorExtraction, PurchaseOrderData, ExtractedValue, UdyamData,
    MAFData, SimilarItemMatch, ExecutionProofStatus, DocumentMetadata, DocumentType, PageData
)
from pipeline.pqc_evaluator import PQCEvaluator, parse_date
from pipeline.utils import parse_monetary_value


@pytest.fixture
def temp_config(tmp_path):
    config_content = {
        "tender_no": "TEST-1234",
        "bid_no": "GEM/2026/123",
        "bid_end_date": "2025-10-27",
        "experience_window_years": 5,
        "financial_pqc": "Not Applicable",
        "mse_exemption": True,
        "criteria_options": [
            {"label": "Option A", "min_orders": 3, "min_value_per_order": 182000},
            {"label": "Option B", "min_orders": 2, "min_value_per_order": 243000},
            {"label": "Option C", "min_orders": 1, "min_value_per_order": 303000}
        ],
        "similar_item_keywords": ["toner", "cartridge"],
        "similar_item_definition": "Supply of toners or cartridges",
        "models": {
            "evaluator": "gpt-oss:20b"
        }
    }
    config_file = tmp_path / "tender_config.json"
    config_file.write_text(json.dumps(config_content))
    return config_file


def test_parse_date():
    # Test cached parser
    assert parse_date("12-Aug-2024") == datetime(2024, 8, 12)
    assert parse_date("2024-08-12") == datetime(2024, 8, 12)
    assert parse_date("12/08/2024") == datetime(2024, 8, 12)
    assert parse_date("invalid-date") is None


def test_experience_window_calculation(temp_config):
    evaluator = PQCEvaluator(temp_config)
    # Bid date: 2025-10-27
    # Last day of preceding month: 2025-09-30 (window end)
    # 5 years back: 2020-09-30 (window start)
    assert evaluator.window_end == datetime(2025, 9, 30)
    assert evaluator.window_start == datetime(2020, 9, 30)


def test_evaluate_vendor_mse_exemption(temp_config):
    evaluator = PQCEvaluator(temp_config)
    
    # Vendor with small enterprise udyam status
    vendor = VendorExtraction(
        vendor_name="Test MSE Vendor",
        vendor_folder="Bid documents/test",
        udyam_registrations=[
            UdyamData(
                udyam_number=ExtractedValue(value="UDYAM-MH-12-1234567"),
                enterprise_type=ExtractedValue(value="MICRO ENTERPRISE"),
                enterprise_name=ExtractedValue(value="Test MSE Vendor")
            )
        ]
    )
    
    evaluation = evaluator.evaluate_vendor(vendor)
    assert evaluation.status.value == "ACCEPTED"
    assert evaluation.mse_exemption_applicable is True
    assert "exempted" in evaluation.recommendation.lower()


def test_evaluate_vendor_criteria_option_c(temp_config):
    evaluator = PQCEvaluator(temp_config)
    
    # Option C requires 1 order >= 303,000 within window with proof, similar item
    po = PurchaseOrderData(
        po_number=ExtractedValue(value="PO-123"),
        issuing_organization=ExtractedValue(value="IOCL"),
        po_date=ExtractedValue(value="15-Jun-2024"),
        po_value=ExtractedValue(value="INR 310,000"),
        po_value_numeric=310000.0,
        items_description=ExtractedValue(value="Laser Toner CartridgesCF277A"),
        execution_proof_status=ExecutionProofStatus.SUBMITTED,
        execution_proof_docs=["invoice.pdf"]
    )
    
    # HP and Kyocera MAFs to avoid QUERIED status
    hp_maf = MAFData(oem_name=ExtractedValue(value="HP India"))
    kyo_maf = MAFData(oem_name=ExtractedValue(value="Kyocera Document Solutions"))
    
    vendor = VendorExtraction(
        vendor_name="Option C Bidders",
        vendor_folder="Bid documents/test",
        purchase_orders=[po],
        maf_authorizations=[hp_maf, kyo_maf]
    )
    
    # Let's bypass AI similar item check by matching positive keywords directly in description
    evaluation = evaluator.evaluate_vendor(vendor)
    assert evaluation.status.value == "ACCEPTED"
    assert "Option C" in evaluation.criteria_met
    assert len(evaluation.qualifying_orders) == 1


def test_evaluate_vendor_rejected_date_outside_window(temp_config):
    evaluator = PQCEvaluator(temp_config)
    
    # Date outside window (e.g. 2019)
    po = PurchaseOrderData(
        po_number=ExtractedValue(value="PO-123"),
        issuing_organization=ExtractedValue(value="IOCL"),
        po_date=ExtractedValue(value="15-Jun-2019"),
        po_value=ExtractedValue(value="INR 350,000"),
        po_value_numeric=350000.0,
        items_description=ExtractedValue(value="Laser Toner Cartridges"),
        execution_proof_status=ExecutionProofStatus.SUBMITTED
    )
    
    vendor = VendorExtraction(
        vendor_name="Rejected Vendor Date",
        vendor_folder="Bid documents/test",
        purchase_orders=[po]
    )
    
    evaluation = evaluator.evaluate_vendor(vendor)
    assert evaluation.status.value == "REJECTED"
    assert len(evaluation.qualifying_orders) == 0
    assert "outside" in evaluation.po_evaluations[0].disqualification_reason.lower()


def test_evaluate_vendor_rejected_below_value_threshold(temp_config):
    evaluator = PQCEvaluator(temp_config)
    
    # Value below minimum Option A threshold (182,000)
    po = PurchaseOrderData(
        po_number=ExtractedValue(value="PO-123"),
        issuing_organization=ExtractedValue(value="IOCL"),
        po_date=ExtractedValue(value="15-Jun-2024"),
        po_value=ExtractedValue(value="INR 150,000"),
        po_value_numeric=150000.0,
        items_description=ExtractedValue(value="Laser Toner Cartridges"),
        execution_proof_status=ExecutionProofStatus.SUBMITTED
    )
    
    vendor = VendorExtraction(
        vendor_name="Rejected Vendor Value",
        vendor_folder="Bid documents/test",
        purchase_orders=[po]
    )
    
    evaluation = evaluator.evaluate_vendor(vendor)
    assert evaluation.status.value == "REJECTED"
    assert len(evaluation.qualifying_orders) == 0
    assert "below minimum thresholds" in evaluation.po_evaluations[0].disqualification_reason.lower()


def test_evaluate_vendor_queried_missing_proof(temp_config):
    evaluator = PQCEvaluator(temp_config)
    
    # Qualifies by value, date, similar, but execution proof is NOT submitted/available
    po = PurchaseOrderData(
        po_number=ExtractedValue(value="PO-123"),
        issuing_organization=ExtractedValue(value="IOCL"),
        po_date=ExtractedValue(value="15-Jun-2024"),
        po_value=ExtractedValue(value="INR 310,000"),
        po_value_numeric=310000.0,
        items_description=ExtractedValue(value="Laser Toner Cartridges"),
        execution_proof_status=ExecutionProofStatus.NOT_SUBMITTED
    )
    
    hp_maf = MAFData(oem_name=ExtractedValue(value="HP India"))
    kyo_maf = MAFData(oem_name=ExtractedValue(value="Kyocera"))
    
    vendor = VendorExtraction(
        vendor_name="Queried Vendor Proof",
        vendor_folder="Bid documents/test",
        purchase_orders=[po],
        maf_authorizations=[hp_maf, kyo_maf]
    )
    
    evaluation = evaluator.evaluate_vendor(vendor)
    assert evaluation.status.value == "QUERIED"
    assert "pending execution proof" in evaluation.criteria_met


def test_parse_monetary_value():
    assert parse_monetary_value("₹ 10,00,000") == 1000000.0
    assert parse_monetary_value("Rs. 5.5 lakh") == 550000.0
    assert parse_monetary_value("INR 2 Crore") == 20000000.0
    assert parse_monetary_value("1,25,000/-") == 125000.0
    assert parse_monetary_value("invalid") is None
    assert parse_monetary_value("") is None


def test_evaluate_vendor_startup_exemption(temp_config):
    evaluator = PQCEvaluator(temp_config)
    
    # Document with DPIIT recognition text
    doc = DocumentMetadata(
        filename="startup_certificate.pdf",
        filepath="Bid documents/test/startup_certificate.pdf",
        pages=[
            PageData(page_number=1, text="We are recognized by DPIIT as a Startup.")
        ]
    )
    
    vendor = VendorExtraction(
        vendor_name="Test Startup Vendor",
        vendor_folder="Bid documents/test",
        documents=[doc]
    )
    
    evaluation = evaluator.evaluate_vendor(vendor)
    assert evaluation.status.value == "ACCEPTED"
    assert evaluation.mse_exemption_applicable is True
    assert "Startup Exemption" in evaluation.criteria_met


class MockEvaluator(PQCEvaluator):
    def __init__(self, config_path, mock_match=SimilarItemMatch.YES, mock_reason="Mocked"):
        super().__init__(config_path)
        self.mock_match = mock_match
        self.mock_reason = mock_reason

    def _ai_similar_item_check(self, po_data):
        return self.mock_match, self.mock_reason


def test_evaluate_vendor_partial_match_query(temp_config):
    # Setup evaluator that returns PARTIAL similar item match
    evaluator = MockEvaluator(temp_config, mock_match=SimilarItemMatch.PARTIAL, mock_reason="Partial toner match")
    
    po = PurchaseOrderData(
        po_number=ExtractedValue(value="PO-999"),
        issuing_organization=ExtractedValue(value="IOCL"),
        po_date=ExtractedValue(value="15-Jun-2024"),
        po_value=ExtractedValue(value="INR 310,000"),
        po_value_numeric=310000.0,
        items_description=ExtractedValue(value="Laser Toner Cartridges"),
        execution_proof_status=ExecutionProofStatus.SUBMITTED,
        execution_proof_docs=["invoice.pdf"]
    )
    
    vendor = VendorExtraction(
        vendor_name="Partial Match Vendor",
        vendor_folder="Bid documents/test",
        purchase_orders=[po]
    )
    
    evaluation = evaluator.evaluate_vendor(vendor)
    assert evaluation.status.value == "QUERIED"
    assert "pending technical review" in evaluation.criteria_met
    assert any("Technical review of similar-item similarity required" in q for q in evaluation.queries)
