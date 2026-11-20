import sys
import json
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
DATA_DIR = WORKSPACE / "data"


def test_heamons_ground_truth():
    gt_file = DATA_DIR / "ground_truth" / "pqc1_criteria_summary.json"
    eval_file = DATA_DIR / "evaluations" / "HEAMONS SYSTEMS.json"
    vendor_file = DATA_DIR / "vendors" / "HEAMONS SYSTEMS.json"

    if not gt_file.exists():
        print(f"FAIL: Ground truth file not found at: {gt_file}")
        sys.exit(1)
    if not eval_file.exists() or not vendor_file.exists():
        print("FAIL: HEAMONS evaluation results not found. Run the pipeline first.")
        sys.exit(1)

    with open(gt_file) as f:
        gt = json.load(f)
    with open(eval_file) as f:
        evaluation = json.load(f)
    with open(vendor_file) as f:
        vendor = json.load(f)

    heamons_gt = gt.get("heamons_ground_truth", {})
    expected_po = heamons_gt.get("qualifying_po", {})
    extracted_pos = vendor.get("purchase_orders", [])
    
    po_match = next(
        (
            po for po in extracted_pos
            if po.get("po_number", {}).get("value") == expected_po.get("po_number")
        ),
        None,
    )
    
    actual_status = evaluation.get("status")
    expected_status = heamons_gt.get("status")
    actual_qualifying = [
        po.get("po_number") for po in evaluation.get("qualifying_orders", [])
    ]

    checks = [
        {
            "label": "Final status",
            "expected": expected_status,
            "actual": actual_status,
            "passed": actual_status == expected_status,
        },
        {
            "label": "Ground-truth PO extracted",
            "expected": expected_po.get("po_number"),
            "actual": po_match.get("po_number", {}).get("value") if po_match else None,
            "passed": po_match is not None,
        },
        {
            "label": "Ground-truth PO value",
            "expected": expected_po.get("po_value_inr"),
            "actual": po_match.get("po_value_numeric") if po_match else None,
            "passed": bool(po_match) and po_match.get("po_value_numeric") == expected_po.get("po_value_inr"),
        },
        {
            "label": "Ground-truth PO qualifies",
            "expected": expected_po.get("po_number"),
            "actual": ", ".join(actual_qualifying),
            "passed": expected_po.get("po_number") in actual_qualifying,
        },
    ]

    print("\n" + "=" * 50)
    print("HEAMONS GROUND TRUTH COMPARISON")
    print("=" * 50)
    all_passed = True
    for check in checks:
        status_str = "PASS" if check["passed"] else "FAIL"
        if not check["passed"]:
            all_passed = False
        print(f"[{status_str}] {check['label']}")
        print(f"      Expected: {check['expected']}")
        print(f"      Actual:   {check['actual']}")
        print("-" * 50)

    if all_passed:
        print("\nSUCCESS: All ground truth checks passed!")
        sys.exit(0)
    else:
        print("\nFAILURE: One or more ground truth checks failed.")
        sys.exit(1)


if __name__ == "__main__":
    test_heamons_ground_truth()
