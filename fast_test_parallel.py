#!/usr/bin/env python3
"""Parallel speed test v2 — avoids loading full PDF doc per worker."""
import fitz
import pytesseract
from PIL import Image
import time
import sys
import multiprocessing as mp

def _ocr_page(args):
    pdf_path, page_index = args
    doc = fitz.open(pdf_path)
    pix = doc[page_index].get_pixmap(dpi=150, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
    img = img.rotate(180)
    text = pytesseract.image_to_string(img, config="--psm 6")
    doc.close()
    return page_index, len(text)

if __name__ == "__main__":
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "media/uploads/UNION_BANK_OF_INDIA_3185010100243264_ZkZGpvR.pdf"
    num_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 2

    tasks = [(pdf_path, i) for i in range(num_pages)]

    t0 = time.time()
    with mp.Pool(workers) as pool:
        for i, char_count in pool.imap_unordered(_ocr_page, tasks):
            print(f"Page {i+1}: done, {char_count} chars  (elapsed: {time.time()-t0:.1f}s)")

    total = time.time() - t0
    print(f"\nTotal: {total:.1f}s for {num_pages} pages with {workers} workers")
    print(f"Per-page average: {total/num_pages:.2f}s")
    print(f"Estimated for 168 pages: {total/num_pages*168/60:.1f} min")