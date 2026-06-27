"""
OCR parser for image files (jpg, png) and scanned PDFs.

Handles auto-orientation correction (rotation AND true left-right mirroring)
per page, since scanners/fax-style scans can flip pages inconsistently
within the same document.

Uses Tesseract via pytesseract. OCR output is messy, so results from here
should always be treated as needing human review.

Requirements on the machine for OCR to work:
  - Tesseract installed (apt install tesseract-ocr, or see Windows wiki)
  - For scanned PDFs: poppler installed (pdf2image needs it: apt install poppler-utils)
"""
import logging
from .base import parse_text_lines

logger = logging.getLogger("statements.ocr")


def _configure_tesseract():
    """Point pytesseract at the binary if a path is set in settings."""
    import pytesseract
    from django.conf import settings
    cmd = getattr(settings, "TESSERACT_CMD", "")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def _confidence_score(pytesseract, img, psm=6) -> float:
    """Average word confidence from Tesseract for a given image/orientation."""
    try:
        data = pytesseract.image_to_data(
            img, output_type=pytesseract.Output.DICT, config=f"--psm {psm}"
        )
        confs = [int(c) for c in data["conf"] if c != "-1"]
        return sum(confs) / len(confs) if confs else 0.0
    except Exception:
        return 0.0

def correct_orientation(pytesseract, img, detect_scale: float = 0.3):
    """
    Detects page orientation (normal / rotated180 / mirrored / mirrored+rotated180)
    using a direct confidence comparison: run a fast low-res OCR pass on all 4
    possible orientations and keep whichever produces the highest average word
    confidence. This is the only approach that reliably caught real orientation
    issues on actual scanned statements during testing — Tesseract's built-in
    OSD (image_to_osd) was tried first but gave inconsistent/wrong results on
    these documents, so it has been removed rather than kept as a "fast path."
    """
    from PIL import Image

    small = img.resize((
        max(1, int(img.width * detect_scale)),
        max(1, int(img.height * detect_scale)),
    ))

    candidates = {
        "normal":              small,
        "flipped":             small.transpose(Image.FLIP_LEFT_RIGHT),
        "rotated180":          small.rotate(180),
        "flipped_rotated180":  small.transpose(Image.FLIP_LEFT_RIGHT).rotate(180),
    }
    scores = {name: _confidence_score(pytesseract, candidate) for name, candidate in candidates.items()}
    best = max(scores, key=scores.get)

    if best == "normal":
        return img, "normal", scores
    elif best == "flipped":
        return img.transpose(Image.FLIP_LEFT_RIGHT), "flipped", scores
    elif best == "rotated180":
        return img.rotate(180), "rotated180", scores
    else:
        return img.transpose(Image.FLIP_LEFT_RIGHT).rotate(180), "flipped_rotated180", scores    

def ocr_image(file_path, auto_orient: bool = True):
    """Run OCR on a single image, return raw text + orientation applied."""
    pytesseract = _configure_tesseract()
    from PIL import Image
    image = Image.open(file_path)

    orientation = "normal"
    if auto_orient:
        image, orientation, _scores = correct_orientation(pytesseract, image)

    text = pytesseract.image_to_string(image, config="--psm 6")
    return text, orientation


def ocr_pdf_pages(file_path, dpi: int = 200, max_pages: int | None = None):
    """
    Generator — yields (page_number, text, orientation) one page at a time,
    so callers can process/save incrementally instead of holding 168 pages
    of OCR text in memory and only finding out about a failure at the end.

    dpi=200 (not 300) trades a little OCR accuracy for significantly faster
    page rendering on large multi-page scans — adjust if rows are coming
    out garbled.
    """
    pytesseract = _configure_tesseract()
    from pdf2image import pdfinfo_from_path, convert_from_path

    info = pdfinfo_from_path(file_path)
    total_pages = info.get("Pages", 0)
    if max_pages:
        total_pages = min(total_pages, max_pages)

    logger.info(f"OCR starting: {total_pages} pages, dpi={dpi}, file={file_path}")

    for page_num in range(1, total_pages + 1):
        try:
            pages = convert_from_path(
                file_path, dpi=dpi, first_page=page_num, last_page=page_num
            )
            if not pages:
                continue
            image = pages[0]
            image, orientation, scores = correct_orientation(pytesseract, image)
            text = pytesseract.image_to_string(image, config="--psm 6")
            logger.info(f"Page {page_num}/{total_pages}: orientation={orientation}")
            yield page_num, text, orientation
        except Exception as exc:
            logger.error(f"Page {page_num} OCR failed: {exc}")
            yield page_num, "", f"error: {exc}"


def ocr_pdf(file_path, dpi: int = 200, max_pages: int | None = None) -> str:
    """Non-streaming convenience wrapper — combines all pages into one text blob.
    Prefer ocr_pdf_pages() directly for large files so you can save incrementally."""
    chunks = []
    for page_num, text, orientation in ocr_pdf_pages(file_path, dpi=dpi, max_pages=max_pages):
        chunks.append(text)
    return "\n".join(chunks)


def parse(file_path):
    text, orientation = ocr_image(file_path)
    rows = parse_text_lines(text)
    return rows, f"Image OCR (orientation: {orientation}, best-effort, review every row)."

# """
# OCR parser for image files (jpg, png) and scanned PDFs.

# Uses Tesseract via pytesseract. OCR output is messy, so results from here
# should always be treated as needing human review.

# Imports are done LAZILY (inside functions) so the rest of the app keeps
# working even if Tesseract / Pillow / pdf2image are not installed yet.

# Requirements on the machine for OCR to work:
#   - Tesseract installed (https://github.com/UB-Mannheim/tesseract/wiki on Windows)
#   - For scanned PDFs: poppler installed (pdf2image needs it)
# """
# from .base import parse_text_lines


# def _configure_tesseract():
#     """Point pytesseract at the binary if a path is set in settings."""
#     import pytesseract
#     from django.conf import settings
#     cmd = getattr(settings, "TESSERACT_CMD", "")
#     if cmd:
#         pytesseract.pytesseract.tesseract_cmd = cmd
#     return pytesseract


# def ocr_image(file_path):
#     """Run OCR on a single image, return raw text."""
#     pytesseract = _configure_tesseract()
#     from PIL import Image
#     image = Image.open(file_path)
#     return pytesseract.image_to_string(image)


# def ocr_pdf(file_path):
#     """Convert each PDF page to an image and OCR it. Returns combined text."""
#     pytesseract = _configure_tesseract()
#     from pdf2image import convert_from_path
#     pages = convert_from_path(file_path, dpi=300)
#     return "\n".join(pytesseract.image_to_string(page) for page in pages)


# def parse(file_path):
#     text = ocr_image(file_path)
#     rows = parse_text_lines(text)
#     return rows, "Image OCR (best-effort, review every row)."
