"""
Shared utilities for the tender evaluation pipeline.
"""

import re
import logging
from pathlib import Path
from typing import Optional, Union, Tuple
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

def clean_tags(text: str) -> str:
    """Remove line and page tags (e.g., [P1-L5]) from OCR/Markdown text."""
    if not text:
        return ""
    # Remove things like [P1-L5]
    text = re.sub(r"\[P\d+-L\d+\]", "", text)
    # Remove [START] and [END] tags if present
    text = text.replace("[START]", "").replace("[END]", "")
    return text.strip()

def _extract_first(pattern: str, text: str, default: str = "Unknown") -> str:
    """Extract the first regex match from text or return default."""
    match = re.search(pattern, text)
    if match:
        return match.group(1).strip()
    return default

# Alias for _extract_regex used in scripts/run_pipeline.py
_extract_regex = _extract_first

def parse_monetary_value(value_str: str) -> Optional[float]:
    """
    Parse a monetary value string into a float.
    Handles: ₹, Rs., INR, commas, lakh/crore notation, etc.
    """
    if not value_str:
        return None

    # Remove common prefixes and whitespace
    cleaned = value_str.strip()
    cleaned = re.sub(r'^(INR|Rs\.?|₹)\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()

    # Handle lakh/crore notation
    lakh_match = re.search(r'([\d,.]+)\s*(?:lakh|lac|L)\b', cleaned, re.IGNORECASE)
    if lakh_match:
        num_str = lakh_match.group(1).replace(',', '')
        try:
            return float(num_str) * 100000
        except ValueError:
            pass

    crore_match = re.search(r'([\d,.]+)\s*(?:crore|cr)\b', cleaned, re.IGNORECASE)
    if crore_match:
        num_str = crore_match.group(1).replace(',', '')
        try:
            return float(num_str) * 10000000
        except ValueError:
            pass

    # Standard numeric parsing: remove commas, handle /- suffix
    cleaned = re.sub(r'[/\-]+$', '', cleaned)
    cleaned = cleaned.replace(',', '')
    cleaned = re.sub(r'[^\d.]', '', cleaned)

    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        logger.warning(f"Could not parse monetary value: '{value_str}'")
        return None

def find_text_in_pdf(
    filepath: Union[str, Path],
    value: str,
    page_num: Optional[int] = None,
    fitz_doc: Optional[fitz.Document] = None
) -> Tuple[int, Optional[dict], str]:
    """
    Search for a text value in a PDF file.
    If page_num is provided, searches only that page (1-indexed).
    Otherwise, searches all pages.
    Returns (page_number, bbox_dict, context_text).
    """
    value = (value or "").strip()
    if not value or value.lower() == "unknown":
        return 1, None, ""

    # Clean up value: strip leading/trailing punctuation and whitespace
    clean_val = value.strip(";:,.- \t\n")
    if not clean_val:
        return 1, None, ""

    search_terms = []

    # If it contains semicolons, split and add parts
    if ";" in clean_val:
        parts = [p.strip(";:,.- \t\n") for p in clean_val.split(";")]
        for p in parts:
            if p and len(p) >= 3:
                search_terms.append(p)
    
    # Add clean value itself
    search_terms.append(clean_val)

    # Handle numeric/monetary variations
    digits_only = re.sub(r"\D", "", clean_val)
    if digits_only and len(digits_only) >= 4:
        num = int(digits_only)
        # Format millions style
        search_terms.append(f"{num:,}")
        search_terms.append(f"{num:,}.00")
        
        # Format Indian lakhs style (e.g. 814515 -> 8,14,515)
        s = str(num)
        if len(s) >= 4:
            last_three = s[-3:]
            other_parts = s[:-3]
            chunks = []
            while other_parts:
                chunks.append(other_parts[-2:])
                other_parts = other_parts[:-2]
            chunks.reverse()
            indian_formatted = ",".join(chunks) + "," + last_three
            search_terms.append(indian_formatted)
            search_terms.append(indian_formatted + ".00")
            
        search_terms.append(digits_only)

    # Handle common business abbreviations
    if "private limited" in clean_val.lower():
        search_terms.append(re.sub(r"(?i)private limited", "Pvt. Ltd.", clean_val))
        search_terms.append(re.sub(r"(?i)private limited", "Pvt Ltd", clean_val))
    if "pvt. ltd." in clean_val.lower() or "pvt ltd" in clean_val.lower():
        search_terms.append(re.sub(r"(?i)pvt\.?\s*ltd\.?", "Private Limited", clean_val))

    # Remove duplicates but preserve order
    seen = set()
    search_terms = [x for x in search_terms if not (x in seen or seen.add(x))]

    doc = fitz_doc
    opened_locally = False
    if doc is None:
        try:
            doc = fitz.open(str(filepath))
            opened_locally = True
        except Exception as e:
            logger.debug(f"Could not open PDF file {filepath}: {e}")
            return 1, None, ""

    try:
        pages_to_search = [page_num] if page_num is not None else list(range(1, len(doc) + 1))
        
        for p_idx in pages_to_search:
            if p_idx < 1 or p_idx > len(doc):
                continue
            page = doc[p_idx - 1]
            page_text = page.get_text() or ""
            for term in search_terms:
                if not term or len(term) < 3:
                    continue
                rects = page.search_for(term)
                if rects:
                    rect = rects[0]
                    page_rect = page.rect
                    bbox = {
                        "x_min": round(rect.x0 / page_rect.width * 100, 2),
                        "y_min": round(rect.y0 / page_rect.height * 100, 2),
                        "x_max": round(rect.x1 / page_rect.width * 100, 2),
                        "y_max": round(rect.y1 / page_rect.height * 100, 2),
                    }
                    start = max(0, page_text.lower().find(term.lower()) - 160)
                    end = min(len(page_text), start + 420)
                    context = re.sub(r"\s+", " ", page_text[start:end]).strip()
                    if opened_locally:
                        doc.close()
                    return p_idx, bbox, context
                    
        if opened_locally:
            doc.close()
    except Exception as e:
        logger.debug(f"Search failed for {filepath}: {e}")
        if opened_locally and doc:
            try:
                doc.close()
            except Exception:
                pass

    return (page_num if page_num is not None else 1), None, ""
