"""
PDF Processor — converts PDFs into page-level text and images.

Uses pypdf for digital text extraction and PyMuPDF (fitz) for
rendering pages as images when OCR is needed.
"""

import logging
from pathlib import Path
from typing import Optional
import pypdf
import fitz  # PyMuPDF

from pipeline.models import PageData, DocumentMetadata

logger = logging.getLogger(__name__)

# Minimum text length to consider a page as having extractable text
MIN_TEXT_LENGTH = 30


def process_pdf(
    filepath: str | Path,
    output_images_dir: str | Path,
    render_images: bool = True,
    dpi: int = 200,
) -> DocumentMetadata:
    """
    Process a single PDF file: extract text per page and optionally render page images.

    Args:
        filepath: Path to the PDF file.
        output_images_dir: Directory to save rendered page images.
        render_images: Whether to render pages as PNG images (needed for OCR/VLM).
        dpi: Resolution for page image rendering.

    Returns:
        DocumentMetadata with page-level data populated.
    """
    filepath = Path(filepath)
    output_images_dir = Path(output_images_dir)
    output_images_dir.mkdir(parents=True, exist_ok=True)

    if not filepath.exists():
        logger.error(f"PDF not found: {filepath}")
        raise FileNotFoundError(f"PDF not found: {filepath}")

    doc_meta = DocumentMetadata(
        filename=filepath.name,
        filepath=str(filepath),
        file_size_bytes=filepath.stat().st_size,
    )

    pages: list[PageData] = []
    needs_ocr_pages: list[int] = []

    # Phase 1: Extract text with pypdf
    try:
        reader = pypdf.PdfReader(str(filepath))
        doc_meta.num_pages = len(reader.pages)

        for i, page in enumerate(reader.pages):
            page_num = i + 1
            try:
                text = page.extract_text() or ""
            except Exception as e:
                logger.warning(f"pypdf text extraction failed on {filepath.name} page {page_num}: {e}")
                text = ""

            has_text = len(text.strip()) >= MIN_TEXT_LENGTH

            page_data = PageData(
                page_number=page_num,
                text=text.strip(),
                has_extractable_text=has_text,
            )

            if not has_text:
                needs_ocr_pages.append(page_num)

            pages.append(page_data)

    except pypdf.errors.PdfReadError as e:
        logger.warning(f"pypdf failed to read {filepath.name}: {e}. Trying PyMuPDF.")
        # Fall through to PyMuPDF-only processing
        try:
            fitz_doc = fitz.open(str(filepath))
            doc_meta.num_pages = len(fitz_doc)
            for i in range(len(fitz_doc)):
                page_num = i + 1
                fitz_page = fitz_doc[i]
                text = fitz_page.get_text() or ""
                has_text = len(text.strip()) >= MIN_TEXT_LENGTH

                page_data = PageData(
                    page_number=page_num,
                    text=text.strip(),
                    has_extractable_text=has_text,
                )
                if not has_text:
                    needs_ocr_pages.append(page_num)
                pages.append(page_data)
            fitz_doc.close()
        except Exception as e2:
            logger.error(f"Both pypdf and PyMuPDF failed on {filepath.name}: {e2}")
            doc_meta.pages = pages
            return doc_meta

    except Exception as e:
        logger.error(f"Unexpected error reading {filepath.name}: {e}")
        doc_meta.pages = pages
        return doc_meta

    # Phase 2: Render images ONLY for pages that need OCR (huge speed savings)
    if render_images and needs_ocr_pages:
        try:
            fitz_doc = fitz.open(str(filepath))
            for i in range(len(fitz_doc)):
                page_num = i + 1
                if page_num <= len(pages):
                    fitz_page = fitz_doc[i]

                    # First try PyMuPDF text for pages that pypdf missed
                    if not pages[page_num - 1].has_extractable_text:
                        fitz_text = fitz_page.get_text() or ""
                        if len(fitz_text.strip()) >= MIN_TEXT_LENGTH:
                            pages[page_num - 1].text = fitz_text.strip()
                            pages[page_num - 1].has_extractable_text = True
                            if page_num in needs_ocr_pages:
                                needs_ocr_pages.remove(page_num)
                            continue  # No need to render image for this page

                    # Only render image if page still needs OCR
                    if page_num in needs_ocr_pages:
                        zoom = dpi / 72.0
                        mat = fitz.Matrix(zoom, zoom)
                        pix = fitz_page.get_pixmap(matrix=mat, alpha=False)

                        img_filename = f"{filepath.stem}_page_{page_num}.png"
                        img_path = output_images_dir / img_filename
                        pix.save(str(img_path))

                        pages[page_num - 1].image_path = str(img_path)

            fitz_doc.close()
        except Exception as e:
            logger.warning(f"Image rendering failed for {filepath.name}: {e}")

    doc_meta.pages = pages

    if needs_ocr_pages:
        logger.info(
            f"{filepath.name}: {len(needs_ocr_pages)} of {doc_meta.num_pages} pages need OCR "
            f"(pages: {needs_ocr_pages})"
        )
    else:
        logger.info(f"{filepath.name}: All {doc_meta.num_pages} pages have extractable text")

    return doc_meta


def process_vendor_folder(
    vendor_folder: str | Path,
    output_images_dir: str | Path,
    skip_extensions: Optional[set[str]] = None,
) -> list[DocumentMetadata]:
    """
    Process all PDFs in a vendor's bid folder.

    Args:
        vendor_folder: Path to the vendor's folder.
        output_images_dir: Base directory for page images.
        skip_extensions: File extensions to skip (default: {'.xlsx', '.xls'}).

    Returns:
        List of DocumentMetadata for each processed file.
    """
    vendor_folder = Path(vendor_folder)
    output_images_dir = Path(output_images_dir)

    if skip_extensions is None:
        skip_extensions = {".xlsx", ".xls", ".csv"}

    documents = []

    for file_path in sorted(vendor_folder.iterdir()):
        if file_path.is_file() and file_path.suffix.lower() not in skip_extensions:
            if file_path.suffix.lower() == ".pdf":
                vendor_img_dir = output_images_dir
                try:
                    doc = process_pdf(file_path, vendor_img_dir)
                    documents.append(doc)
                except Exception as e:
                    logger.error(f"Failed to process {file_path.name}: {e}")
                    # Create a minimal metadata entry
                    documents.append(DocumentMetadata(
                        filename=file_path.name,
                        filepath=str(file_path),
                        file_size_bytes=file_path.stat().st_size if file_path.exists() else 0,
                    ))

    logger.info(f"Processed {len(documents)} documents from {vendor_folder.name}")
    return documents


def get_full_text(doc: DocumentMetadata) -> str:
    """Get the concatenated text of all pages in a document."""
    texts = []
    for page in doc.pages:
        text = page.ocr_text or page.text
        if text:
            texts.append(f"[Page {page.page_number}]\n{text}")
    return "\n\n".join(texts)


def get_pages_needing_ocr(doc: DocumentMetadata) -> list[PageData]:
    """Get list of pages that need OCR (no extractable text)."""
    return [p for p in doc.pages if not p.has_extractable_text and p.image_path]


def render_single_page(filepath: str | Path, page_num: int, output_images_dir: str | Path, dpi: int = 160) -> Optional[str]:
    """Render a single page of a PDF as a PNG image and return its path."""
    filepath = Path(filepath)
    output_images_dir = Path(output_images_dir)
    output_images_dir.mkdir(parents=True, exist_ok=True)
    img_path = output_images_dir / f"{filepath.stem}_page_{page_num}.png"
    if img_path.exists():
        return str(img_path)
    try:
        pdf = fitz.open(str(filepath))
        page = pdf[page_num - 1]
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(str(img_path))
        pdf.close()
        return str(img_path)
    except Exception as e:
        logger.warning(f"Could not render page {page_num} of {filepath.name}: {e}")
        return None

