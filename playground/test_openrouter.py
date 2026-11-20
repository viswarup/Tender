import os
import sys
import re
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

WORKSPACE = Path("/Users/viswarup/Downloads/tender evaluation")
load_dotenv(dotenv_path=WORKSPACE / ".env")

api_key = os.environ.get("OPENROUTER_API_KEY")
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
    timeout=60.0
)

# Load the file 17622499231073.md
file_path = WORKSPACE / "gemini_ocr/HEAMONS SYSTEMS/17622499231073.md"
with open(file_path, "r", encoding="utf-8") as f:
    text_content = f.read()

# Aggregation and snippets extraction
pages = {}
lines = text_content.split("\n")
for line in lines:
    match = re.match(r"^\[P(\d+)-L\d+\](.*)", line.strip())
    if match:
        p_num = int(match.group(1))
        body = match.group(2).strip()
        if p_num not in pages:
            pages[p_num] = []
        pages[p_num].append(body)
        
snippets = {}
head_len = 400
tail_len = 300
for p_num, text_lines in pages.items():
    page_text = " ".join(text_lines)
    if len(page_text) <= (head_len + tail_len + 50):
        snippets[p_num] = page_text
    else:
        head = page_text[:head_len]
        tail = page_text[-tail_len:]
        snippets[p_num] = f"[START] {head} ... [END] {tail}"

# Construct snippets text
snippets_text = ""
for p_num, snippet in sorted(snippets.items()):
    snippets_text += f"Page {p_num} Snippet:\n---\n{snippet}\n---\n\n"
    
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

print("Querying OpenRouter...")
try:
    response = client.chat.completions.create(
        model="openrouter/free",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    print("STATUS: SUCCESS")
    print("MODEL USED:", response.model)
    print("RESPONSE TEXT:")
    print(response.choices[0].message.content)
except Exception as e:
    print("STATUS: FAILED")
    print("ERROR:", e)
