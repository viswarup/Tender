"""
PQC Evaluation Engine — Applies commercial experience criteria
from the tender configuration to evaluate each vendor's bid.

Replicates the evaluation logic from PQC1.pdf.

The engine:
1. Takes extracted vendor data (PurchaseOrders, MAFs, Udyam, etc.)
2. Applies hard-coded business rules from the config
3. Uses AI for "similar item" judgment calls
4. Produces a structured PQCEvaluation with detailed reasoning
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from pipeline.models import (
    VendorExtraction,
    PurchaseOrderData,
    PQCEvaluation,
    POEvaluation,
    PQCStatus,
    SimilarItemMatch,
    ExecutionProofStatus,
)
import functools
from pipeline.ollama_client import OllamaClient, ollama
from pipeline.llm_router import query_llm_json, parse_json_response


logger = logging.getLogger(__name__)

DEFAULT_EVALUATOR_MODEL = "gpt-oss:20b"


@functools.lru_cache(maxsize=1024)
def parse_date(date_str: str) -> Optional[datetime]:
    """Parse a date string in various formats."""
    date_str = date_str.strip()
    formats = [
        "%d-%b-%Y", "%d %b %Y", "%d %B %Y",  # Put common GeM formats first
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
        "%Y-%m-%d", "%Y/%m/%d",
        "%d-%m-%y", "%d/%m/%y",
        "%b %d, %Y", "%B %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    # Try to extract date components with regex
    patterns = [
        r'(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})',  # DD-MM-YYYY
        r'(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})',  # YYYY-MM-DD
    ]
    for pattern in patterns:
        match = re.search(pattern, date_str)
        if match:
            groups = match.groups()
            try:
                if len(groups[0]) == 4:  # YYYY-MM-DD
                    return datetime(int(groups[0]), int(groups[1]), int(groups[2]))
                else:  # DD-MM-YYYY
                    return datetime(int(groups[2]), int(groups[1]), int(groups[0]))
            except ValueError:
                continue
    return None


class PQCEvaluator:
    """
    Evaluates vendors against Pre-Qualification Criteria (PQC)
    defined in the tender configuration.
    """

    def __init__(self, config_path: str | Path, client: Optional[OllamaClient] = None):
        """
        Initialize evaluator with tender configuration.

        Args:
            config_path: Path to tender_config.json
            client: OllamaClient instance (uses singleton if None)
        """
        self.config_path = Path(config_path)
        with open(self.config_path) as f:
            self.config = json.load(f)

        self.client = client or ollama
        self.model = self.config.get("models", {}).get("evaluator", DEFAULT_EVALUATOR_MODEL)

        # Extract key parameters from config
        self.similar_item_keywords = [
            kw.lower() for kw in self.config.get("similar_item_keywords", [])
        ]
        self.similar_item_definition = self.config.get(
            "similar_item_definition", "Supply of toners or cartridges"
        )
        self.experience_window_years = self.config.get("experience_window_years", 5)
        self.criteria_options = self.config.get("criteria_options", [])
        self.bid_end_date = self.config.get("bid_end_date", "2025-10-27")
        self.mse_exemption = self.config.get("mse_exemption", True)

        self.financial_pqc = self.config.get("financial_pqc", "Not Applicable")
        if self.financial_pqc != "Not Applicable":
            raise NotImplementedError(
                f"Financial PQC rule '{self.financial_pqc}' is configured, but financial PQC enforcement "
                "is not yet implemented."
            )

        # Calculate the experience window
        self._compute_experience_window()

    def _compute_experience_window(self):
        """
        Compute the 5-year experience window.
        Per the tender: "last 5 years ending on the last day of the month
        immediately preceding the month in which the last date of bid submission falls"
        """
        try:
            bid_date = datetime.strptime(self.bid_end_date, "%Y-%m-%d")
            # Last day of the month preceding bid submission month
            first_of_bid_month = bid_date.replace(day=1)
            last_day_prev_month = first_of_bid_month - timedelta(days=1)
            self.window_end = last_day_prev_month
            self.window_start = self.window_end.replace(
                year=self.window_end.year - self.experience_window_years
            )
            logger.info(
                f"Experience window: {self.window_start.strftime('%Y-%m-%d')} "
                f"to {self.window_end.strftime('%Y-%m-%d')}"
            )
        except ValueError:
            # Fallback: approximate
            self.window_end = datetime(2025, 9, 30)
            self.window_start = datetime(2020, 10, 1)
            logger.warning("Could not parse bid_end_date, using approximate window")


    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse a date string in various formats."""
        return parse_date(date_str)

    def _check_date_in_window(self, date_str: str) -> tuple[bool, str]:
        """
        Check if a PO date falls within the experience window.

        Returns:
            Tuple of (is_within_window, explanation)
        """
        parsed = self._parse_date(date_str)
        if parsed is None:
            return False, f"Could not parse date '{date_str}'"

        if self.window_start <= parsed <= self.window_end:
            return True, (
                f"Date {parsed.strftime('%Y-%m-%d')} is within window "
                f"({self.window_start.strftime('%Y-%m-%d')} to {self.window_end.strftime('%Y-%m-%d')})"
            )
        else:
            return False, (
                f"Date {parsed.strftime('%Y-%m-%d')} is OUTSIDE window "
                f"({self.window_start.strftime('%Y-%m-%d')} to {self.window_end.strftime('%Y-%m-%d')})"
            )

    def _keyword_similar_item_check(self, items_desc: str) -> SimilarItemMatch:
        """
        Quick keyword-based check for similar item matching.
        Returns YES if keywords are found, NO if clearly unrelated.
        """
        desc_lower = items_desc.lower()

        # Positive keywords
        if any(kw in desc_lower for kw in self.similar_item_keywords):
            return SimilarItemMatch.YES

        # Strong negative signals
        negative_keywords = [
            "furniture", "air conditioner", "civil work", "construction",
            "stationery", "notebook", "pen", "pencil", "paper",
            "computer", "laptop", "desktop", "server", "networking",
            "hard disk", "ram", "motherboard", "monitor",
            "electrical", "plumbing", "painting",
        ]
        if any(neg in desc_lower for neg in negative_keywords):
            return SimilarItemMatch.NO

        # Ambiguous — needs AI judgment
        return SimilarItemMatch.PARTIAL

    def _ai_similar_item_check(self, po_data: PurchaseOrderData) -> tuple[SimilarItemMatch, str]:
        """
        Use AI to determine if a PO's items constitute "similar items"
        per the tender definition.

        Args:
            po_data: Purchase order data with items description.

        Returns:
            Tuple of (match_result, reasoning)
        """
        items_desc = ""
        if po_data.items_description:
            items_desc = po_data.items_description.value
        if not items_desc:
            items_desc = f"PO from {po_data.issuing_organization.value}"

        # First try keyword check
        keyword_result = self._keyword_similar_item_check(items_desc)
        if keyword_result == SimilarItemMatch.YES:
            return SimilarItemMatch.YES, f"Items description contains similar-item keywords: '{items_desc}'"
        elif keyword_result == SimilarItemMatch.NO:
            return SimilarItemMatch.NO, f"Items clearly unrelated to toners/cartridges: '{items_desc}'"

        # AI judgment for ambiguous cases
        prompt = f"""You are evaluating Purchase Orders for a government tender.

The tender is for: "{self.similar_item_definition}"
Similar items are defined as: "Supply of toners or cartridges"

The PO being evaluated has these details:
- PO Number: {po_data.po_number.value}
- Issuing Organization: {po_data.issuing_organization.value}
- Items/Description: {items_desc}
- PO Value: {po_data.po_value.value}

QUESTION: Does this PO qualify as "Supply of toners or cartridges" (similar item)?

Consider:
- Toner cartridges, ink cartridges, printer consumables = YES
- Printers/MFPs with cartridges = PARTIAL (only if cartridge value is separable)
- Computer peripherals, stationery, IT hardware = NO
- Office supplies that include cartridges among other items = PARTIAL

Respond with JSON:
{{"meets_similar_item": "YES" or "NO" or "PARTIAL", "reasoning": "..."}}"""

        try:
            result = query_llm_json(prompt, local_model=self.model)
            match = result.get("meets_similar_item", "NO").upper()
            reasoning = result.get("reasoning", "")

            if match == "YES":
                return SimilarItemMatch.YES, reasoning
            elif match == "PARTIAL":
                return SimilarItemMatch.PARTIAL, reasoning
            else:
                return SimilarItemMatch.NO, reasoning

        except Exception as e:
            logger.error(f"AI similar item check failed: {e}")
            return SimilarItemMatch.PARTIAL, f"AI evaluation failed: {e}. Manual review needed."

    def evaluate_purchase_order(self, po: PurchaseOrderData) -> POEvaluation:
        """
        Evaluate a single Purchase Order against PQC criteria.

        Args:
            po: Extracted purchase order data.

        Returns:
            POEvaluation result.
        """
        # 1. Check similar item match
        similar_match, similar_reasoning = self._ai_similar_item_check(po)
        meets_similar = similar_match in (SimilarItemMatch.YES, SimilarItemMatch.PARTIAL)

        # 2. Check value threshold (check against all options, record the lowest met)
        po_value = po.po_value_numeric or 0.0
        meets_value = False
        threshold_used = ""
        for option in self.criteria_options:
            if po_value >= option["min_value_per_order"]:
                meets_value = True
                threshold_used = f"{option['label']}: ≥ ₹{option['min_value_per_order']:,.0f}"
                break  # Take the highest threshold met

        # 3. Check date within window
        date_str = po.po_date.value if po.po_date else ""
        date_in_window, date_explanation = self._check_date_in_window(date_str)

        # 4. Check execution proof
        has_proof = po.execution_proof_status in (
            ExecutionProofStatus.SUBMITTED,
        )

        # 5. Determine if qualifying (requires execution proof)
        is_qualifying = meets_similar and meets_value and date_in_window and has_proof

        disqualification_reasons = []
        if not meets_similar:
            disqualification_reasons.append(f"Does not meet similar item criteria: {similar_reasoning}")
        if not meets_value:
            disqualification_reasons.append(
                f"PO value ₹{po_value:,.0f} below minimum thresholds"
            )
        if not date_in_window:
            disqualification_reasons.append(f"Date issue: {date_explanation}")
        if not has_proof:
            disqualification_reasons.append("Missing execution proof")

        return POEvaluation(
            po_number=po.po_number.value,
            issuing_org=po.issuing_organization.value,
            po_date=date_str,
            po_value=po_value,
            meets_similar_item=meets_similar,
            similar_item_reasoning=similar_reasoning,
            similar_item_match_type=similar_match,
            meets_value_threshold=meets_value,
            threshold_used=threshold_used,
            execution_proof_available=has_proof,
            execution_proof_type=(
                po.execution_proof_docs[0] if po.execution_proof_docs else ""
            ),
            date_within_window=date_in_window,
            is_qualifying=is_qualifying,
            disqualification_reason="; ".join(disqualification_reasons),
        )

    def evaluate_vendor(self, vendor: VendorExtraction) -> PQCEvaluation:
        """
        Run full PQC evaluation for a vendor.

        Args:
            vendor: Complete extracted data for a vendor.

        Returns:
            PQCEvaluation with status, reasoning, and detailed PO evaluations.
        """
        logger.info(f"Evaluating PQC for: {vendor.vendor_name}")

        # Check for MSE exemption (PROBLEM-05)
        mse_exempt = False
        if self.mse_exemption and vendor.udyam_registrations:
            udyam = vendor.udyam_registrations[0]
            udyam_num = udyam.udyam_number.value if udyam.udyam_number else ""
            ent_type = udyam.enterprise_type.value if udyam.enterprise_type else "Unknown"
            
            udyam_regex = re.compile(r'UDYAM-[A-Z]{2}-\d{2}-\d+', re.IGNORECASE)
            is_valid_format = bool(udyam_regex.search(udyam_num))
            
            if is_valid_format and any(micro_small in ent_type.upper() for micro_small in ["MICRO", "SMALL"]):
                mse_exempt = True
                logger.info(
                    f"{vendor.vendor_name}: Has valid Udyam registration ({udyam_num}) "
                    f"and enterprise type '{ent_type}' — MSE exemption applicable"
                )
            else:
                logger.warning(
                    f"{vendor.vendor_name}: Has Udyam registration but failed validation. "
                    f"Number: '{udyam_num}' (valid format={is_valid_format}), Type: '{ent_type}'"
                )

        # Check for Startup exemption (C5)
        startup_exempt = False
        if self.config.get("startup_exemption", True):
            for doc in vendor.documents:
                doc_text = ""
                if doc.pages:
                    doc_text = "\n".join([page.ocr_text or page.text or "" for page in doc.pages]).upper()
                if "DPIIT" in doc_text or ("STARTUP" in doc_text and "RECOGNITION" in doc_text):
                    startup_exempt = True
                    logger.info(
                        f"{vendor.vendor_name}: Has document with DPIIT/Startup Recognition text ({doc.filename}) "
                        "— Startup exemption applicable"
                    )
                    break

        exempt_applicable = mse_exempt or startup_exempt

        # Evaluate each PO
        po_evals = []
        for po in vendor.purchase_orders:
            po_eval = self.evaluate_purchase_order(po)
            po_evals.append(po_eval)

        # POs that meet similarity, value threshold, and date in window (regardless of execution proof)
        commercially_matching = [
            pe for pe in po_evals 
            if pe.meets_similar_item and pe.meets_value_threshold and pe.date_within_window
        ]

        # POs that are fully qualifying (meets commercial criteria AND has execution proof)
        qualifying = [pe for pe in po_evals if pe.is_qualifying]

        # Check which criteria option is met
        criteria_met = None
        status = PQCStatus.REJECTED
        recommendation = ""
        queries: list[str] = []

        if exempt_applicable:
            status = PQCStatus.ACCEPTED
            reasons = []
            if mse_exempt:
                reasons.append("MSE Exemption (Udyam Registration)")
            if startup_exempt:
                reasons.append("Startup Exemption (DPIIT Recognition)")
            criteria_met = " & ".join(reasons)
            recommendation = (
                f"{vendor.vendor_name} is exempted from commercial experience criteria "
                f"as a {' and '.join(reasons)}."
            )
        elif qualifying:
            # Check against criteria options (sorted by min_orders ascending = most lenient first)
            for option in sorted(self.criteria_options, key=lambda o: o["min_orders"]):
                # Count qualifying orders that meet THIS option's value threshold
                qualifying_for_option = [
                    q for q in qualifying
                    if q.po_value >= option["min_value_per_order"]
                ]

                if len(qualifying_for_option) >= option["min_orders"]:
                    # Check if any PO used in the qualifying subset is a partial similar-item match
                    subset = qualifying_for_option[:option["min_orders"]]
                    partial_matches = [q for q in subset if q.similar_item_match_type == SimilarItemMatch.PARTIAL]
                    
                    if partial_matches:
                        status = PQCStatus.QUERIED
                        criteria_met = f"{option['label']} (pending technical review)"
                        recommendation = (
                            f"{vendor.vendor_name}: {len(qualifying_for_option)} qualifying order(s) "
                            f"found for {option['label']} "
                            f"(min {option['min_orders']} × ≥ ₹{option['min_value_per_order']:,.0f}), "
                            f"but {len(partial_matches)} order(s) require technical review of similar-item similarity."
                        )
                        for q in partial_matches:
                            queries.append(
                                f"Technical review of similar-item similarity required for PO {q.po_number}: {q.similar_item_reasoning}"
                            )
                    else:
                        # Fully qualifies
                        status = PQCStatus.ACCEPTED
                        criteria_met = (
                            f"{option['label']}: {option['min_orders']} order(s) × "
                            f"≥ ₹{option['min_value_per_order']:,.0f}"
                        )
                        recommendation = (
                            f"{vendor.vendor_name}: Meets commercial experience criteria under "
                            f"{option['label']} with {len(qualifying_for_option)} qualifying order(s)."
                        )
                    break  # Stop at the first (most lenient) satisfied option

        if status == PQCStatus.REJECTED and not exempt_applicable and commercially_matching:
            # Check if they WOULD qualify if they had execution proof or completed technical review
            for option in sorted(self.criteria_options, key=lambda o: o["min_orders"]):
                matching_for_option = [
                    q for q in commercially_matching
                    if q.po_value >= option["min_value_per_order"]
                ]

                if len(matching_for_option) >= option["min_orders"]:
                    subset = matching_for_option[:option["min_orders"]]
                    without_proof = [q for q in subset if not q.execution_proof_available]
                    partial_matches = [q for q in subset if q.similar_item_match_type == SimilarItemMatch.PARTIAL]
                    
                    if without_proof or partial_matches:
                        status = PQCStatus.QUERIED
                        reasons = []
                        if without_proof:
                            reasons.append("pending execution proof")
                        if partial_matches:
                            reasons.append("pending technical review")
                        criteria_met = f"{option['label']} ({' & '.join(reasons)})"
                        
                        rec_lines = [
                            f"{vendor.vendor_name}: {len(matching_for_option)} potentially qualifying order(s) "
                            f"found for {option['label']} (min {option['min_orders']} × ≥ ₹{option['min_value_per_order']:,.0f})."
                        ]
                        if without_proof:
                            rec_lines.append(f"{len(without_proof)} order(s) missing execution proof.")
                            for q in without_proof:
                                queries.append(
                                    f"Execution proof required for PO {q.po_number} "
                                    f"(₹{q.po_value:,.0f} from {q.issuing_org})"
                                )
                        if partial_matches:
                            rec_lines.append(f"{len(partial_matches)} order(s) require technical review of similar-item similarity.")
                            for q in partial_matches:
                                queries.append(
                                    f"Technical review of similar-item similarity required for PO {q.po_number}: {q.similar_item_reasoning}"
                                )
                        recommendation = " ".join(rec_lines)
                        break

        if status == PQCStatus.REJECTED and not exempt_applicable:
            # Build rejection reasoning
            if not vendor.purchase_orders:
                recommendation = f"{vendor.vendor_name}: No purchase orders found in submitted documents."
            else:
                reasons = set()
                for pe in po_evals:
                    if not pe.meets_similar_item:
                        reasons.add("similar item criteria not met")
                    if not pe.meets_value_threshold:
                        reasons.add("PO values below minimum thresholds")
                    if not pe.date_within_window:
                        reasons.add("PO dates outside experience window")
                    if not pe.execution_proof_available:
                        reasons.add("missing execution proof")
                reasons_str = ", ".join(sorted(reasons))
                recommendation = (
                    f"{vendor.vendor_name}: {len(po_evals)} PO(s) evaluated, none qualify. "
                    f"Reasons: {reasons_str}."
                )

        # Build detailed reasoning
        detail_lines = [f"PQC Evaluation for {vendor.vendor_name}"]
        detail_lines.append(f"Financial PQC: Not Applicable")
        detail_lines.append(f"MSE Exemption: {'Yes' if mse_exempt else 'No'}")
        detail_lines.append(f"Startup Exemption: {'Yes' if startup_exempt else 'No'}")
        detail_lines.append(f"Purchase Orders submitted: {len(vendor.purchase_orders)}")
        detail_lines.append(f"Qualifying orders: {len(qualifying)}")
        detail_lines.append(f"Status: {status.value}")
        detail_lines.append(f"Criteria met: {criteria_met or 'None'}")
        detail_lines.append("")

        for pe in po_evals:
            detail_lines.append(f"  PO: {pe.po_number}")
            detail_lines.append(f"    Org: {pe.issuing_org}")
            detail_lines.append(f"    Date: {pe.po_date}")
            detail_lines.append(f"    Value: ₹{pe.po_value:,.0f}")
            detail_lines.append(f"    Similar Item: {'✓' if pe.meets_similar_item else '✗'} (Type: {pe.similar_item_match_type.value}) — {pe.similar_item_reasoning}")
            detail_lines.append(f"    Value Threshold: {'✓' if pe.meets_value_threshold else '✗'} — {pe.threshold_used or 'Below all thresholds'}")
            detail_lines.append(f"    Date in Window: {'✓' if pe.date_within_window else '✗'}")
            detail_lines.append(f"    Execution Proof: {'✓' if pe.execution_proof_available else '✗'}")
            detail_lines.append(f"    Qualifying: {'✓' if pe.is_qualifying else '✗'}")
            if pe.disqualification_reason:
                detail_lines.append(f"    Reason: {pe.disqualification_reason}")
            detail_lines.append("")

        return PQCEvaluation(
            vendor_name=vendor.vendor_name,
            financial_pqc="Not Applicable",
            po_evaluations=po_evals,
            qualifying_orders=qualifying,
            criteria_met=criteria_met,
            status=status,
            recommendation=recommendation,
            queries=queries,
            detailed_reasoning="\n".join(detail_lines),
            mse_exemption_applicable=exempt_applicable,
        )


def load_evaluator(config_path: str | Path = "config/tender_config.json") -> PQCEvaluator:
    """Convenience factory to create a PQCEvaluator from the default config."""
    return PQCEvaluator(config_path)
