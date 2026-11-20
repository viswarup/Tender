import json
from pathlib import Path

HEAMONS_PATH = Path("data/vendors/HEAMONS SYSTEMS.json")
if not HEAMONS_PATH.exists():
    print("HEAMONS SYSTEMS JSON not found.")
    exit(1)

with open(HEAMONS_PATH, "r") as f:
    data = json.load(f)

print(f"### Documents Submitted by {data['vendor_name']}\n")
for idx, doc in enumerate(data.get("documents", []), 1):
    print(f"#### {idx}. {doc['filename']}")
    print(f"* **Classified Type**: `{doc['document_type']}`")
    print(f"* **Classification Confidence**: {doc['classification_confidence']:.2f}")
    print(f"* **Reasoning/Heuristics**: {doc.get('summary', 'N/A')}")
    
    # Let's list segments if any
    segments = doc.get("segments", [])
    if segments:
        print("* **Logical Segments inside this file**:")
        for seg in segments:
            print(f"  - Pages {seg['start_page']}-{seg['end_page']}: `{seg['document_type']}` — *{seg.get('summary', '')}*")
    print()
