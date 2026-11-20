"""
Accuracy check against IOCL's committee evaluation minutes (PQC1.pdf).

Compares pipeline output in data/vendors + data/evaluations with the
hand-transcribed ground truth in data/ground_truth/pqc1_committee_minutes.json
and reports, per stage:

  1. PO discovery      - did we find the POs the committee evaluated?
  2. Field extraction  - on matched POs: date, value, issuing organisation
  3. Similar item      - YES/NO judgement vs committee
  4. Execution proof   - SUBMITTED vs committee's CRAC / not-submitted finding
  5. Final verdict     - ACCEPTED / QUERIED / REJECTED per bidder

Usage:
    python scripts/accuracy_check.py                 # print report
    python scripts/accuracy_check.py --markdown out.md
"""

import argparse
import json
import re
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

from pipeline.pqc_evaluator import parse_date

GT_PATH = WORKSPACE / "data" / "ground_truth" / "pqc1_committee_minutes.json"
VENDORS = WORKSPACE / "data" / "vendors"
EVALS = WORKSPACE / "data" / "evaluations"

STOPWORDS = {"of", "the", "and", "for", "india", "ltd", "limited", "&", "-"}


def norm_po(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return f"GEMC-{digits}" if digits else ""


def org_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def org_match(predicted: str, expected: str) -> bool:
    """Match if the smaller token set is mostly contained in the larger (handles abbreviations in brackets)."""
    p, e = org_tokens(predicted), org_tokens(expected)
    if not p or not e:
        return False
    overlap = len(p & e) / min(len(p), len(e))
    return overlap >= 0.6


def pct(num: int, den: int) -> str:
    return f"{num}/{den} ({num / den * 100:.0f}%)" if den else "n/a"


def run() -> dict:
    gt = json.loads(GT_PATH.read_text())["bidders"]

    po_rows, verdict_rows = [], []
    totals = {k: [0, 0] for k in ("found", "date", "value", "org", "similar", "proof")}
    extracted_total = extra_total = 0

    for bidder, truth in gt.items():
        vendor = json.loads((VENDORS / f"{bidder}.json").read_text())
        evaluation = json.loads((EVALS / f"{bidder}.json").read_text())

        extracted = {}
        for po in vendor.get("purchase_orders", []):
            key = norm_po(po["po_number"]["value"])
            if key and key not in extracted:
                extracted[key] = po
        similar_by_po = {
            norm_po(pe["po_number"]): pe["meets_similar_item"]
            for pe in evaluation.get("po_evaluations", [])
        }

        gt_keys = {norm_po(p["po_number"]) for p in truth["purchase_orders"]}
        extracted_total += len(extracted)
        extra_total += len(set(extracted) - gt_keys)

        for gpo in truth["purchase_orders"]:
            key = norm_po(gpo["po_number"])
            row = {"bidder": bidder, "sno": gpo["sno"], "po": gpo["po_number"]}
            po = extracted.get(key)
            totals["found"][1] += 1
            if po is None:
                row.update(found=False)
                po_rows.append(row)
                continue
            totals["found"][0] += 1
            row["found"] = True

            got_date = parse_date(po["po_date"]["value"] or "")
            want_date = parse_date(gpo["date"])
            row["date_ok"] = bool(got_date and want_date and got_date.date() == want_date.date())
            row["date_got"] = po["po_date"]["value"]

            got_value = po.get("po_value_numeric") or 0
            row["value_ok"] = abs(got_value - gpo["value"]) <= 1
            row["value_got"] = got_value
            row["value_want"] = gpo["value"]

            row["org_ok"] = org_match(po["issuing_organization"]["value"], gpo["issuer"])
            row["org_got"] = po["issuing_organization"]["value"]

            for field in ("date", "value", "org"):
                totals[field][1] += 1
                totals[field][0] += row[f"{field}_ok"]

            if gpo["similar_item"] in ("YES", "NO"):
                totals["similar"][1] += 1
                row["similar_ok"] = similar_by_po.get(key) == (gpo["similar_item"] == "YES")
                totals["similar"][0] += row["similar_ok"]

            if gpo["proof"] in ("CRAC_SUBMITTED", "NOT_SUBMITTED"):
                totals["proof"][1] += 1
                got_proof = po.get("execution_proof_status") == "SUBMITTED"
                row["proof_ok"] = got_proof == (gpo["proof"] == "CRAC_SUBMITTED")
                row["proof_got"] = "SUBMITTED" if got_proof else "NOT"
                row["proof_want"] = gpo["proof"]
                totals["proof"][0] += row["proof_ok"]

            po_rows.append(row)

        got_v, want_v = evaluation.get("status"), truth["verdict"]
        verdict_rows.append({
            "bidder": bidder, "system": got_v, "committee": want_v,
            "ok": got_v == want_v,
            "false_accept": got_v == "ACCEPTED" and want_v != "ACCEPTED",
            "basis": evaluation.get("criteria_met"),
        })

    return {
        "po_rows": po_rows, "verdict_rows": verdict_rows, "totals": totals,
        "extracted_total": extracted_total, "extra_total": extra_total,
    }


def render(r: dict) -> str:
    t = r["totals"]
    v = r["verdict_rows"]
    ok_v = sum(x["ok"] for x in v)
    fa = sum(x["false_accept"] for x in v)
    lines = []
    add = lines.append

    add("# Accuracy vs IOCL committee minutes (PQC1)\n")
    add(f"Scope: {len(v)} bidders, {t['found'][1]} purchase orders evaluated by the committee.\n")
    add("## Headline\n")
    add("| Stage | Metric | Result |")
    add("|---|---|---|")
    add(f"| Discovery | Committee POs found by pipeline (recall) | **{pct(*t['found'])}** |")
    add(f"| Extraction | PO date exact | **{pct(*t['date'])}** |")
    add(f"| Extraction | PO value exact (±₹1) | **{pct(*t['value'])}** |")
    add(f"| Extraction | Issuing organisation | **{pct(*t['org'])}** |")
    add(f"| Judgement | Similar-item decision | **{pct(*t['similar'])}** |")
    add(f"| Judgement | Execution-proof decision | **{pct(*t['proof'])}** |")
    add(f"| Verdict | Bidder status matches committee | **{pct(ok_v, len(v))}** |")
    add(f"| Verdict | False accepts (unqualified bidder passed) | **{fa}** |")
    add("")
    precision_note = r["extracted_total"] - r["extra_total"]
    add(f"Pipeline extracted {r['extracted_total']} unique POs; {r['extra_total']} were not in the committee's evaluation "
        f"(precision {pct(precision_note, r['extracted_total'])}).\n")

    add("## Verdicts\n")
    add("| Bidder | Committee | System | Match | System basis |")
    add("|---|---|---|---|---|")
    for x in v:
        mark = "✅" if x["ok"] else ("❌ false accept" if x["false_accept"] else "❌")
        add(f"| {x['bidder']} | {x['committee']} | {x['system']} | {mark} | {x['basis'] or ''} |")
    add("")

    add("## Field errors\n")
    errs = []
    for row in r["po_rows"]:
        if not row["found"]:
            errs.append(f"| {row['bidder']} | {row['po']} | not extracted | | |")
            continue
        if not row["value_ok"]:
            errs.append(f"| {row['bidder']} | {row['po']} | value | ₹{row['value_want']:,.0f} | ₹{row['value_got']:,.0f} |")
        if not row["date_ok"]:
            errs.append(f"| {row['bidder']} | {row['po']} | date | | {row['date_got']} |")
        if not row["org_ok"]:
            errs.append(f"| {row['bidder']} | {row['po']} | organisation | | {row['org_got']} |")
        if row.get("proof_ok") is False:
            errs.append(f"| {row['bidder']} | {row['po']} | execution proof | {row['proof_want']} | {row['proof_got']} |")
    if errs:
        add("| Bidder | PO | Field | Committee | System |")
        add("|---|---|---|---|---|")
        lines.extend(errs)
    else:
        add("None.")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Score pipeline output against PQC1 committee minutes")
    parser.add_argument("--markdown", help="also write the report to this file")
    args = parser.parse_args()
    report = render(run())
    print(report)
    if args.markdown:
        Path(args.markdown).write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
