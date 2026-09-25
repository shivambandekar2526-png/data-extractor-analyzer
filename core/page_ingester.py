"""
PDF Page Ingester
=================
Extracts raw text tokens, line groupings, and structure from each page of an academic PDF.
Provides multi-tiered fallback: native text (pypdf/pdfplumber) -> high-res OCR (PyMuPDF/RapidOCR).
"""

import os
from typing import List, Optional

import pdfplumber
import pypdf

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None

try:
    from ..models import PageContent
except Exception:
    try:
        from data_extractor_AI.models import PageContent
    except Exception:
        from models import PageContent

_ocr_engine = None


def get_ocr():
    """Lazy initialize RapidOCR engine singleton."""
    global _ocr_engine
    if _ocr_engine is None and RapidOCR is not None:
        try:
            _ocr_engine = RapidOCR()
        except Exception:
            _ocr_engine = None
    return _ocr_engine


def ocr_page(page, dpi: int = 140) -> str:
    """Render a PyMuPDF page pixmap and cluster OCR bounding boxes into horizontal lines."""
    ocr = get_ocr()
    if not ocr or not page:
        return ""
    try:
        pix = page.get_pixmap(dpi=dpi)
        res, _ = ocr(pix.tobytes("png"))
        if not res:
            return ""

        boxes = []
        for item in res:
            text = item[1].strip() if len(item) > 1 and item[1] else ""
            if text:
                boxes.append({"text": text, "x": item[0][0][0], "y": item[0][0][1]})
        boxes.sort(key=lambda b: (b["y"], b["x"]))

        lines = []
        curr_line = []
        curr_y = None
        for b in boxes:
            if curr_y is None:
                curr_y = b["y"]
                curr_line.append(b)
            elif abs(b["y"] - curr_y) <= 8:
                curr_line.append(b)
            else:
                curr_line.sort(key=lambda item: item["x"])
                lines.append(" ".join(item["text"] for item in curr_line))
                curr_line = [b]
                curr_y = b["y"]
        if curr_line:
            curr_line.sort(key=lambda item: item["x"])
            lines.append(" ".join(item["text"] for item in curr_line))
        return "\n".join(lines)
    except Exception:
        return ""


def ingest_pdf(pdf_path: str) -> List[PageContent]:
    """
    Extract text from every page of a PDF document.
    Returns an ordered list of PageContent objects.
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    pages: List[PageContent] = []

    reader = None
    try:
        reader = pypdf.PdfReader(pdf_path)
    except Exception:
        pass

    plumber_pdf = None
    try:
        plumber_pdf = pdfplumber.open(pdf_path)
    except Exception:
        pass

    mupdf_doc = None
    if pymupdf:
        try:
            mupdf_doc = pymupdf.open(pdf_path)
        except Exception:
            pass

    page_count = 0
    if reader and len(reader.pages) > 0:
        page_count = len(reader.pages)
    elif plumber_pdf and len(plumber_pdf.pages) > 0:
        page_count = len(plumber_pdf.pages)
    elif mupdf_doc and len(mupdf_doc) > 0:
        page_count = len(mupdf_doc)

    if page_count == 0:
        raise ValueError(f"Unable to read or open PDF document: {pdf_path}")

    for idx in range(page_count):
        text = ""
        is_ocr = False

        # Tier 1: pypdf native text
        if reader and idx < len(reader.pages):
            try:
                text = (reader.pages[idx].extract_text() or "").strip()
            except Exception:
                text = ""

        # Tier 2: pdfplumber if pypdf was empty or low-density
        if len(text) < 50 and plumber_pdf and idx < len(plumber_pdf.pages):
            try:
                plumb_text = (plumber_pdf.pages[idx].extract_text() or "").strip()
                if len(plumb_text) > len(text):
                    text = plumb_text
            except Exception:
                pass

        # Tier 3: OCR fallback if native text layer is missing or corrupted
        if len(text) < 50 and mupdf_doc and idx < len(mupdf_doc):
            ocr_text = ocr_page(mupdf_doc[idx]).strip()
            if len(ocr_text) > len(text):
                text = ocr_text
                is_ocr = True

        lines = [l for l in text.split("\n") if l.strip()]
        char_count = len(text)
        digit_count = sum(1 for c in text if c.isdigit())
        num_density = (digit_count / max(char_count, 1))

        pages.append(PageContent(
            page_num=idx,
            text=text,
            line_count=len(lines),
            char_count=char_count,
            is_ocr=is_ocr,
            numeric_density=round(num_density, 3)
        ))

    if plumber_pdf:
        try:
            plumber_pdf.close()
        except Exception:
            pass

    return pages
