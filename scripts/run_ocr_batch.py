import os
import sys
import re
import base64
import logging
from pathlib import Path
import fitz  # PyMuPDF
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("gemini_ocr")

# Load environment variables
WORKSPACE = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=WORKSPACE / ".env")

# 1. Initialize API Clients
google_client = None
gemini_key = os.environ.get("GEMINI_API_KEY")
if gemini_key:
    try:
        from google import genai
        from google.genai import types
        google_client = genai.Client(api_key=gemini_key)
        logger.info("Successfully initialized Google GenAI Client.")
    except Exception as e:
        logger.error(f"Failed to initialize Google GenAI SDK: {e}")
else:
    logger.warning("GEMINI_API_KEY not set, Gemini OCR will be unavailable.")

openrouter_client = None
openrouter_key = os.environ.get("OPENROUTER_API_KEY")
if openrouter_key:
    try:
        from openai import OpenAI
        openrouter_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_key
        )
        logger.info("Successfully initialized OpenAI client for OpenRouter.")
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client for OpenRouter: {e}")
else:
    logger.warning("OPENROUTER_API_KEY not set, OpenRouter OCR will be unavailable.")

# Import local Ollama client wrapper for local Qwen fallback
sys.path.append(str(WORKSPACE))
try:
    from pipeline.ollama_client import ollama
except ImportError:
    logger.error("Could not import pipeline.ollama_client. Ensure workspace pipeline package is accessible.")
    sys.exit(1)

# Configuration
BIDS_DIR = WORKSPACE / "raw_bids/ARC Cartridges_Bids"
OUTPUT_ROOT = WORKSPACE / "ocr_cache"

# Load target bidders dynamically from config or bids folder
CONFIG_FILE = WORKSPACE / "config/tender_config.json"
TARGET_BIDDERS = []
if CONFIG_FILE.exists():
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
            TARGET_BIDDERS = cfg.get("target_bidders", [])
    except Exception:
        pass

if not TARGET_BIDDERS:
    if BIDS_DIR.exists():
        TARGET_BIDDERS = sorted([d.name for d in BIDS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")])
    else:
        TARGET_BIDDERS = [
            "RAVECHI CARTRIDGE",
            "ALANKAR DISTRIBUTORS",
            "ARMS PERIPHERALS",
            "HEAMONS SYSTEMS",
            "LUXMI ENTERPRISES"
        ]

OCR_PROMPT = (
    "Extract all text and layout data from this image exactly as it appears. "
    "Use clean Markdown format. If tables are present, format them as markdown tables. "
    "Do NOT add any conversational headers, explanations, or chatter."
)
LOCAL_VLM_MODEL = "qwen2.5vl:3b"

def encode_image(image_path):
    """Converts a local image into a Base64 string for OpenRouter."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

def run_hybrid_ocr(image_path):
    """
    OCR pipeline with 3 stages of fallbacks:
      1. Primary: Google Gemini 2.5 Flash
      2. Secondary Fallback: OpenRouter nex-agi/nex-n2-pro:free
      3. Tertiary Fallback: Local Ollama (qwen2.5vl:3b)
    """
    # --- STAGE 1: Primary Processing via Google Gemini ---
    if google_client:
        try:
            logger.info(f"    [OCR Stage 1] Querying Google Gemini (gemini-2.5-flash)...")
            from google.genai import types
            with open(image_path, "rb") as img_file:
                image_bytes = img_file.read()
                
            response = google_client.models.generate_content(
                model='gemini-2.5-flash', 
                contents=[
                    OCR_PROMPT, 
                    types.Part.from_bytes(data=image_bytes, mime_type='image/png')
                ]
            )
            if response.text:
                return response.text, "Google Gemini (gemini-2.5-flash)"
            raise ValueError("Empty response received from Gemini.")
            
        except Exception as google_error:
            logger.warning(f"    ⚠️ [OCR Stage 1] Gemini failed or hit rate limits: {google_error}")
            logger.info("    [OCR Stage 2] Redirecting request to OpenRouter Fallback...")
    else:
        logger.info("    [OCR Stage 1] Gemini client not initialized, skipping to OpenRouter...")
        
    # --- STAGE 2: Secondary Fallback via OpenRouter ---
    if openrouter_client:
        try:
            base64_image = encode_image(image_path)
            
            completion = openrouter_client.chat.completions.create(
                model="nex-agi/nex-n2-pro:free", 
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": OCR_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                            }
                        ]
                    }
                ]
            )
            content = completion.choices[0].message.content
            if content:
                return content, "OpenRouter (nex-agi/nex-n2-pro:free)"
            raise ValueError("Empty response received from OpenRouter.")
            
        except Exception as or_error:
            logger.warning(f"    ⚠️ [OCR Stage 2] OpenRouter failed: {or_error}")
            logger.info(f"    [OCR Stage 3] Redirecting request to Local Ollama fallback ({LOCAL_VLM_MODEL})...")
    else:
        logger.info("    [OCR Stage 2] OpenRouter client not initialized, skipping to Local VLM...")

    # --- STAGE 3: Fallback Processing via Local VLM ---
    try:
        img_b64 = ollama.image_to_base64(image_path)
        response = ollama.generate(
            model=LOCAL_VLM_MODEL,
            prompt=OCR_PROMPT,
            images=[img_b64],
            temperature=0.0,
            num_ctx=4096
        )
        if response.strip():
            return response.strip(), f"Local Ollama ({LOCAL_VLM_MODEL})"
        raise ValueError("Empty response received from local Ollama VLM.")
    except Exception as local_error:
        error_msg = f"[OCR Failed: Gemini, OpenRouter, and Local VLM all failed. Local Error: {local_error}]"
        logger.error(f"    ❌ [OCR Stage 3] Local VLM fallback also failed: {local_error}")
        return error_msg, "None (Failed)"

def clean_and_tag_text(text: str, page_num: int) -> list[str]:
    """Split text into lines, clean, and prepend line-by-line tags [P{page}-L{line}]."""
    raw_lines = text.split("\n")
    tagged_lines = []
    line_idx = 1
    for line in raw_lines:
        line_stripped = line.strip()
        if not line_stripped:
            tagged_lines.append("")
            continue
        tagged_lines.append(f"[P{page_num}-L{line_idx}] {line_stripped}")
        line_idx += 1
    return tagged_lines

def convert_md_to_html(md_content: str, title: str) -> str:
    """Simple converter from markdown to clean HTML for visual inspection."""
    html_lines = []
    in_table = False
    
    lines = md_content.split("\n")
    for line in lines:
        line_stripped = line.strip()
        
        # Check for tags at the start: e.g. [P1-L1]
        tag_match = re.match(r"^(\[P\d+-L\d+\])(.*)", line_stripped)
        tag_prefix = ""
        line_body = line_stripped
        if tag_match:
            tag_prefix = f'<span class="tag">{tag_match.group(1)}</span> '
            line_body = tag_match.group(2).strip()
            
        # Parse table markdown: e.g. | col1 | col2 |
        if line_body.startswith("|") and line_body.endswith("|"):
            cells = [c.strip() for c in line_body.split("|")[1:-1]]
            # Check if it's a separator line like |---|---|
            if all(re.match(r"^:?-+:?$", c) for c in cells):
                continue
            
            if not in_table:
                in_table = True
                html_lines.append("<table>")
                html_lines.append("<thead><tr>")
                for cell in cells:
                    html_lines.append(f"<th>{cell}</th>")
                html_lines.append("</tr></thead><tbody>")
            else:
                html_lines.append("<tr>")
                for cell in cells:
                    html_lines.append(f"<td>{cell}</td>")
                html_lines.append("</tr>")
            continue
        else:
            if in_table:
                in_table = False
                html_lines.append("</tbody></table>")
        
        # Headings
        if line_body.startswith("#"):
            h_level = len(line_body) - len(line_body.lstrip("#"))
            h_text = line_body.lstrip("#").strip()
            html_lines.append(f"<h{h_level}>{tag_prefix}{h_text}</h{h_level}>")
        elif not line_stripped:
            html_lines.append("<br/>")
        else:
            # Regular line
            html_lines.append(f"<p>{tag_prefix}{line_body}</p>")
            
    if in_table:
        html_lines.append("</tbody></table>")
        
    html_body = "\n".join(html_lines)
    
    html_template = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 900px;
            margin: 40px auto;
            padding: 0 20px;
            background-color: #f8f9fa;
        }}
        h1, h2, h3, h4, h5, h6 {{
            color: #111;
            margin-top: 24px;
            margin-bottom: 16px;
            font-weight: 600;
        }}
        p {{
            margin-top: 0;
            margin-bottom: 8px;
            font-size: 14px;
        }}
        .tag {{
            font-weight: bold;
            color: #0066cc;
            background-color: #e6f2ff;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 12px;
            margin-right: 6px;
            font-family: monospace;
            display: inline-block;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 20px 0;
            background: #fff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 10px 12px;
            text-align: left;
            font-size: 13px;
        }}
        th {{
            background-color: #f1f3f5;
            font-weight: bold;
        }}
        tr:nth-child(even) {{
            background-color: #f8f9fa;
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    {html_body}
</body>
</html>
"""
    return html_template

def process_pdf_file(pdf_path: Path, bidder_name: str):
    logger.info(f"Processing PDF: {pdf_path.name} for bidder {bidder_name}")
    
    bidder_output_dir = OUTPUT_ROOT / bidder_name
    bidder_output_dir.mkdir(parents=True, exist_ok=True)
    
    md_output_path = bidder_output_dir / f"{pdf_path.stem}.md"
    html_output_path = bidder_output_dir / f"{pdf_path.stem}.html"
    
    try:
        fitz_doc = fitz.open(str(pdf_path))
    except Exception as e:
        logger.error(f"Failed to open PDF {pdf_path.name}: {e}")
        return
        
    extracted_doc_lines = []
    
    for page_idx in range(len(fitz_doc)):
        page_num = page_idx + 1
        fitz_page = fitz_doc[page_idx]
        
        # Step 1: Try Native Digital Text extraction
        text = fitz_page.get_text() or ""
        text_stripped = text.strip()
        
        if len(text_stripped) >= 30:
            logger.info(f"  Page {page_num}: Digital Text Extracted ({len(text_stripped)} chars)")
            tagged_lines = clean_and_tag_text(text, page_num)
            extracted_doc_lines.append(f"[P{page_num}-L0] [OCR_MODEL: PyMuPDF (Native Digital Text)]")
            extracted_doc_lines.extend(tagged_lines)
            extracted_doc_lines.append("")
        else:
            # Step 2: Fallback to Hybrid Vision OCR
            logger.info(f"  Page {page_num}: Scanned/Sparse page. Running Hybrid OCR pipeline...")
            
            temp_img_dir = WORKSPACE / "data/temp_ocr_images"
            temp_img_dir.mkdir(parents=True, exist_ok=True)
            img_path = temp_img_dir / f"{pdf_path.stem}_p{page_num}.png"
            
            zoom = 2.0
            mat = fitz.Matrix(zoom, zoom)
            pix = fitz_page.get_pixmap(matrix=mat, alpha=False)
            pix.save(str(img_path))
            
            # Execute the OCR pipeline
            ocr_text, ocr_model = run_hybrid_ocr(img_path)
            
            # Clean up temp image
            if img_path.exists():
                img_path.unlink()
                
            tagged_lines = clean_and_tag_text(ocr_text, page_num)
            extracted_doc_lines.append(f"[P{page_num}-L0] [OCR_MODEL: {ocr_model}]")
            extracted_doc_lines.extend(tagged_lines)
            extracted_doc_lines.append("")

    fitz_doc.close()
    
    # Save output files
    final_md_content = "\n".join(extracted_doc_lines)
    
    with open(md_output_path, "w", encoding="utf-8") as f:
        f.write(final_md_content)
    logger.info(f"Saved Markdown: {md_output_path}")
    
    html_title = f"{bidder_name} - {pdf_path.name}"
    final_html_content = convert_md_to_html(final_md_content, html_title)
    
    with open(html_output_path, "w", encoding="utf-8") as f:
        f.write(final_html_content)
    logger.info(f"Saved HTML: {html_output_path}")

def main():
    # Verify local Ollama server connection (as tertiary fallback)
    if not ollama.is_available():
         logger.warning("Local Ollama server is offline or unreachable. If both remote API routes fail, fallback OCR will fail.")
         
    for bidder in TARGET_BIDDERS:
        bidder_dir = BIDS_DIR / bidder
        if not bidder_dir.exists():
            logger.warning(f"Bidder directory not found: {bidder_dir}")
            continue
            
        logger.info(f"Starting OCR run for Bidder: {bidder}")
        for file in sorted(bidder_dir.iterdir()):
            if file.is_file() and file.suffix.lower() == ".pdf":
                # Check if this PDF has already been successfully processed
                bidder_output_dir = OUTPUT_ROOT / bidder
                md_path = bidder_output_dir / f"{file.stem}.md"
                html_path = bidder_output_dir / f"{file.stem}.html"
                
                if md_path.exists() and html_path.exists() and md_path.stat().st_size > 0:
                    logger.info(f"  Skipping already processed PDF: {file.name}")
                    continue
                    
                process_pdf_file(file, bidder)
                
    logger.info("OCR batch run successfully completed!")

if __name__ == "__main__":
    main()
