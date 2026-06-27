#!/usr/bin/env python3
"""Quick speed test: single-pass OCR, low DPI, minimal memory footprint."""
import fitz
import pytesseract
from PIL import Image
import time
import sys

pdf_path = sys.argv[1] if len(sys.argv) > 1 else "media/uploads/UNION_BANK_OF_INDIA_3185010100243264_ZkZGpvR.pdf"
num_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3

doc = fitz.open(pdf_path)
t0 = time.time()

for i in range(num_pages):
    page_t0 = time.time()
    pix = doc[i].get_pixmap(dpi=150, colorspace=fitz.csGRAY)  # low DPI, grayscale only
    img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
    img = img.rotate(180)
    text = pytesseract.image_to_string(img, config="--psm 6")
    print(f"Page {i+1}: {time.time()-page_t0:.1f}s, {len(text)} chars extracted")

print(f"\nTotal: {time.time()-t0:.1f}s for {num_pages} pages")
print(f"Estimated for 168 pages (sequential): {(time.time()-t0)/num_pages*168/60:.1f} min")
doc.close()