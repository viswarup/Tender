"""
Unit tests for Liquid AI Decision Models (Zero-Token Calibrated Decision & Confidence Scoring)
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.models import DocumentMetadata, DocumentType, PageData, DocumentSegment
from pipeline.liquid_decision import (
    LiquidDecisionClient,
    score_document_decision,
    DOCUMENT_TYPE_CRITERIA,
)
from pipeline.doc_classifier import segment_pdf_via_llm, classify_document


def test_liquid_decision_client_init():
    client = LiquidDecisionClient(api_key="test_liquid_key", model="d1.2-ultra")
    assert client.api_key == "test_liquid_key"
    assert client.model == "d1.2-ultra"
    assert "https://api.liquid.ai/decisions" in client.base_url


def test_liquid_decision_criteria_defined():
    # Verify all standard DocumentTypes have formal criteria defined
    required_types = [
        "PURCHASE_ORDER", "INVOICE", "EXECUTION_PROOF", "UDYAM_MSME",
        "MAF_OEM_AUTH", "GST_CERTIFICATE", "PAN_CARD", "ISO_CERTIFICATE",
        "BID_SECURITY", "BOQ_COMPLIANCE", "SELF_DECLARATION", "TURNOVER_CERT",
        "ATC_ACCEPTANCE", "GEM_BID_COPY", "OTHER"
    ]
    for doc_type in required_types:
        assert doc_type in DOCUMENT_TYPE_CRITERIA, f"Missing criteria definition for {doc_type}"
        assert len(DOCUMENT_TYPE_CRITERIA[doc_type]) > 20


def test_liquid_decision_remote_api_success():
    client = LiquidDecisionClient(api_key="test_key", model="d1.2")
    
    mock_resp_data = {
        "model": "d1.2",
        "answers": {
            "document_type": {
                "choice": "PURCHASE_ORDER",
                "confidence": 0.965,
                "probabilities": {
                    "PURCHASE_ORDER": 0.965,
                    "INVOICE": 0.021,
                    "OTHER": 0.014
                }
            }
        },
        "usage": {
            "input_tokens": 150,
            "output_tokens": 0
        }
    }
    
    with patch.object(client, "query_system_one", return_value=mock_resp_data):
        conf, backend = client.score_decision(
            text="Contract No: GEMC-511687709668754 Total Order Value: 350000",
            proposed_type="PURCHASE_ORDER"
        )
        assert conf == 0.965
        assert "Liquid AI (d1.2) System One" in backend


def test_liquid_decision_calibrated_scoring_purchase_order():
    text = (
        "Contract No: GEMC-511687709668754\n"
        "Organisation Name: Container Corporation of India Limited\n"
        "Total Order Value: INR 450,000.00\n"
        "Buyer Details: Dy Manager MIS\n"
    )
    # Target match
    conf_po, _ = score_document_decision(text, "PURCHASE_ORDER")
    assert conf_po >= 0.85, f"Expected high confidence for PO, got {conf_po}"
    
    # Mismatch check
    conf_tax, _ = score_document_decision(text, "TURNOVER_CERT")
    assert conf_tax < 0.50, f"Expected low confidence for mismatch, got {conf_tax}"


def test_liquid_decision_calibrated_scoring_udyam():
    text = (
        "GOVERNMENT OF INDIA\n"
        "MINISTRY OF MICRO, SMALL AND MEDIUM ENTERPRISES\n"
        "UDYAM REGISTRATION CERTIFICATE\n"
        "UDYAM REGISTRATION NUMBER: UDYAM-MH-12-0045678\n"
        "NAME OF ENTERPRISE: ABC TRADERS\n"
        "TYPE OF ENTERPRISE: MICRO ENTERPRISE\n"
    )
    conf_udyam, _ = score_document_decision(text, "UDYAM_MSME")
    assert conf_udyam >= 0.90, f"Expected >=0.90 for Udyam, got {conf_udyam}"


def test_liquid_decision_calibrated_scoring_gst():
    text = (
        "Form GST REG-06\n"
        "Registration Certificate\n"
        "Registration Number: 27AAACC1205A1ZN\n"
        "Legal Name: RAVECHI CARTRIDGE\n"
        "Date of Liability: 01/07/2017\n"
    )
    conf_gst, _ = score_document_decision(text, "GST_CERTIFICATE")
    assert conf_gst >= 0.90, f"Expected >=0.90 for GST, got {conf_gst}"


def test_liquid_decision_calibrated_scoring_turnover():
    text = (
        "TO WHOM IT MAY CONCERN\n"
        "ANNUAL TURNOVER CERTIFICATE\n"
        "This is to certify that M/s HEAMONS SYSTEMS has the following turnover:\n"
        "FY 2021-22: Rs. 45.2 Lakhs\n"
        "FY 2022-23: Rs. 52.8 Lakhs\n"
        "UDIN: 23123456AAAAAB1234\n"
        "Chartered Accountant Membership No. 123456\n"
    )
    conf_to, _ = score_document_decision(text, "TURNOVER_CERT")
    assert conf_to >= 0.90, f"Expected >=0.90 for Turnover, got {conf_to}"


def test_doc_classifier_uses_liquid_decision_scoring():
    # Mock LLM router to return a segment without any confidence key
    mock_llm_response = {
        "segmented_documents": [
            {
                "start_page": 1,
                "end_page": 1,
                "document_type": "PURCHASE_ORDER",
                "summary": "GeM Contract order for supplies",
                "reasoning": "Contains GeM contract agreement headings"
            }
        ]
    }
    
    snippets = {
        1: "Contract No: GEMC-511687709668754 Total Order Value: INR 500000"
    }
    
    with patch("pipeline.doc_classifier.query_llm_json", return_value=mock_llm_response):
        segments = segment_pdf_via_llm("sample_po.pdf", snippets)
        assert len(segments) == 1
        # Confidence must be populated by Liquid Decision model, NOT LLM token
        assert segments[0].confidence >= 0.85
        assert segments[0].document_type == DocumentType.PURCHASE_ORDER


def test_classify_document_populates_liquid_confidence():
    doc = DocumentMetadata(
        filename="test_gst.pdf",
        filepath="/tmp/test_gst.pdf",
        num_pages=1,
        pages=[
            PageData(
                page_number=1,
                text="Form GST REG-06 Registration Certificate 27AAACC1205A1ZN",
                ocr_text="Form GST REG-06 Registration Certificate 27AAACC1205A1ZN"
            )
        ]
    )
    
    mock_llm_response = {
        "segmented_documents": [
            {
                "start_page": 1,
                "end_page": 1,
                "document_type": "GST_CERTIFICATE",
                "summary": "GST Certificate",
                "reasoning": "Valid Form GST REG-06"
            }
        ]
    }
    
    with patch("pipeline.doc_classifier.query_llm_json", return_value=mock_llm_response):
        classified = classify_document(doc)
        assert classified.document_type == DocumentType.GST_CERTIFICATE
        assert classified.classification_confidence >= 0.85
        assert "Liquid AI Decision Model" in classified.summary
