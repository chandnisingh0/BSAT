"""
PDF parser — handles both standard 5-column and Saraswat-style 7-column layouts.
Uses pdfplumber; falls back to OCR for scanned PDFs.
"""
import re
import pdfplumber
from .base import (
    parse_date, parse_time, parse_amount, parse_balance_type,
    parse_text_lines, extract_mode, extract_counterparty,
)

def _looks_like_header(cells):
    # Safeguard: If the first cell or any early cell looks like a valid date, it's a transaction row, NOT a header!
    if any(parse_date(str(c)) for c in cells[:2] if c):
        return False

    joined = " ".join(str(c or "").lower() for c in cells)
    return any(w in joined for w in (
        "date", "narration", "description", 
        "particulars", "debit", "credit", "transaction",
        "sr.no", "sr no", "opening balance"
    ))

# def _clean(cells):
#     return [str(c).strip().replace("\n", " ") if c else "" for c in cells]
def _clean(cells):
    out = []
    for c in cells:
        s = (str(c) or "").replace("\x0c", " ").replace("\u2011", "-")
        s = re.sub(r'[\r\n]+', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        out.append(s)
    # merge tiny fragments into previous cell
    for i in range(len(out)-1, 0, -1):
        if out[i] and len(out[i]) <= 3 and out[i-1]:
            out[i-1] = (out[i-1] + " " + out[i]).strip()
            out[i] = ""
    return [s for s in out]

def _map_row(cells: list[str], source_row: int, layout_type: str) -> dict | None:
    date_col = None
    for i, cell in enumerate(cells[:4]):
        if parse_date(cell):
            date_col = i
            break

    if date_col is None:
        return None

    txn_date = parse_date(cells[date_col])
    n = len(cells)
    txn_ref = ""

    # 7-column Saraswat-style
    if date_col == 0 and layout_type == "7col":
        value_date  = parse_date(cells[1]) if (n > 1 and cells[1]) else None
        txn_time    = parse_time(cells[2]) if (n > 2 and cells[2]) else None
        narration   = cells[3] if n > 3 else ""
        debit_raw   = cells[4] if n > 4 else ""
        credit_raw  = cells[5] if n > 5 else ""
        balance_raw = cells[6] if n > 6 else ""
        chq_ref     = cells[7] if n > 7 else ""
        extra = {"value_date_raw": cells[1] if n > 1 else "", "txn_time_raw": cells[2] if n > 2 else "", "raw_cells": cells}

    # 6-column: Date | Ref | Particulars | Debit | Credit | Balance
    elif date_col == 0 and layout_type == "6col":
        value_date  = None
        txn_time    = None
        txn_ref     = cells[1] if n > 1 else ""
        narration   = cells[2] if n > 2 else ""
        debit_raw   = cells[3] if n > 3 else ""
        credit_raw  = cells[4] if n > 4 else ""
        balance_raw = cells[5] if n > 5 else ""
        chq_ref     = ""
        extra = {"txn_ref_raw": txn_ref, "raw_cells": cells}

    # 5-column layout: Date | Narration | Debit | Credit | Balance
    elif date_col == 0:
        value_date  = None
        txn_time    = None
        narration   = cells[1] if n > 1 else ""
        debit_raw   = cells[2] if n > 2 else ""
        credit_raw  = cells[3] if n > 3 else ""
        balance_raw = cells[4] if n > 4 else ""
        chq_ref     = ""
        extra = {"raw_cells": cells}

    elif date_col == 1:
        value_date  = None
        txn_time    = None
        narration   = cells[2] if n > 2 else ""
        chq_ref     = cells[3] if n > 3 else ""
        debit_raw   = cells[4] if n > 4 else ""
        credit_raw  = cells[5] if n > 5 else ""
        balance_raw = cells[6] if n > 6 else ""
        extra = {"sr_no": cells[0], "chq_ref_number": chq_ref, "raw_cells": cells}

    else:
        return None

    if "opening balance" in narration.lower():
        return None

    balance_type = parse_balance_type(balance_raw)
    return {
        "txn_date":          txn_date,
        "value_date":        value_date,
        "txn_time":          txn_time,
        "narration_raw":     narration,
        "debit":             parse_amount(debit_raw),
        "credit":            parse_amount(credit_raw),
        "balance":           parse_amount(balance_raw),
        "balance_type":      balance_type,
        "reference":         extra.get("txn_ref_raw", chq_ref),
        "txn_mode":          extract_mode(narration),
        "counterparty_name": extract_counterparty(narration),
        "source_row":        source_row,
        "bank_json_data":    extra,
    }

# def _detect_layout(cells: list[str]) -> str:      
#     """
#     Returns layout name based on column count and content.
#       '7col'  — Transaction Date | Value Date | Time | Particulars | Debit | Credit | Balance
#       '5col'  — Date | Narration | Debit | Credit | Balance
#     """
#     n = len(cells)
#     if n >= 7:
#         # if re.match(r"\d{1,2}:\d{2}", cells[2]):
#         return "7col"
#     return "5col"
def _detect_layout(cells: list[str]) -> str:
    n = len(cells)
    if n >= 7:
        return "7col"
    if n == 6:
        # detect amounts in last 3 columns -> 6col layout
        if any(re.search(r'\d[\d,]*\.\d{1,2}', str(c or "")) for c in (cells[3], cells[4], cells[5])):
            return "6col"
    return "5col"

def parse(file_path: str, stream: bool = False):
    """
    stream=False (default): returns (rows: list, file_type_notes: str) — unchanged behavior.
    stream=True: returns a generator yielding row dicts one at a time as they're
                 extracted, plus the caller must separately know file_type via
                 inspecting whether OCR was used. Used by the Celery task for
                 live progress on large scanned PDFs.
    """

    # ── Union Bank scanned PDF (rotated 180°, no table lines) ────────────────
    # Must be checked FIRST before pdfplumber runs, because pdfplumber returns
    # zero tables for these image-only pages and the code would fall through
    # to the wrong generic OCR path.
    from .union_bank_parser import is_union_bank_scanned, parse as parse_union_bank
    if is_union_bank_scanned(file_path):
        # Change max_pages=2 to max_pages=None once you're happy with the output
        rows, notes = parse_union_bank(file_path, max_pages=2)
        if stream:
            def _gen():
                for r in rows:
                    yield r
            return _gen(), "pdf_scan", notes
        return rows, notes
    # ── existing logic below — completely untouched ───────────────────────────

    rows = []
    total_chars = 0
    row_counter = 0

    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            total_chars += len(page.extract_text() or "")
            table_settings = {
                "vertical_strategy": "lines",
                "horizontal_strategy": "text",
                "intersection_tolerance": 5,
                "snap_tolerance": 3,
                "join_tolerance": 3,
            }
            for table in page.extract_tables(table_settings=table_settings) or []:

                # Check layout globally for this table based on the longest row found
                max_cols = max(len(r) for r in table) if table else 0
                if max_cols >= 7:
                    layout_type = "7col"
                elif max_cols == 6:
                    layout_type = "6col"
                else:
                    layout_type = "5col"

                for raw_cells in table:
                    if not raw_cells:
                        continue

                    # Fix 1: Clean first
                    cells = _clean(raw_cells)

                    # Fix 2: Header guard check
                    if _looks_like_header(cells):
                        continue

                    row_counter += 1
                    # Fix 3: Pass the pre-determined layout_type down
                    row = _map_row(cells, row_counter, layout_type)
                    if row:
                        rows.append(row)
                    else:
                        print("UNMAPPED ROW:", cells)

    if rows:
        if stream:
            def _gen():
                for r in rows:
                    yield r
            return _gen(), "pdf_text", "Parsed as text PDF via pdfplumber."
        return rows, "Parsed as text PDF via pdfplumber."

    if total_chars < 50:
        from .image_parser import ocr_pdf_pages
        from .base import parse_ocr_lines

        if stream:
            def _gen():
                running_count = 0
                for page_num, text, orientation in ocr_pdf_pages(file_path, dpi=300):
                    page_rows = parse_ocr_lines(text)
                    for r in page_rows:
                        running_count += 1
                        r["source_row"] = running_count
                        yield r
            return _gen(), "pdf_scan", "Scanned PDF: OCR extraction in progress (rows appear live)."

        all_rows = []
        page_count = 0
        for page_num, text, orientation in ocr_pdf_pages(file_path, dpi=300):
            page_count += 1
            page_rows = parse_ocr_lines(text)
            for r in page_rows:
                r["source_row"] = len(all_rows) + 1
                all_rows.append(r)
        return all_rows, (
            f"Scanned PDF: OCR across {page_count} pages. "
            f"All {len(all_rows)} extracted rows are flagged OCR_NEEDS_REVIEW — "
            f"balance figures are reliable, debit/credit split requires manual confirmation."
        )

    with pdfplumber.open(file_path) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    final_rows = parse_text_lines(full_text)
    if stream:
        def _gen():
            for r in final_rows:
                yield r
        return _gen(), "pdf_text", "Text PDF without clean tables: line-based parse."

    print("---------", final_rows, "---------")
    return final_rows, "Text PDF without clean tables: line-based parse."


# def parse(file_path: str, stream: bool = False):
#     """
#     stream=False (default): returns (rows: list, file_type_notes: str) — unchanged behavior.
#     stream=True: returns a generator yielding row dicts one at a time as they're
#                  extracted, plus the caller must separately know file_type via
#                  inspecting whether OCR was used. Used by the Celery task for
#                  live progress on large scanned PDFs.
#     """

#     # ── Union Bank scanned PDF (rotated 180°, no table lines) ────────────────
#     # Must be checked FIRST before pdfplumber runs, because pdfplumber returns
#     # zero tables for these image-only pages and the code would fall through
#     # to the wrong generic OCR path.
#     from .union_bank_parser import is_union_bank_scanned, parse as parse_union_bank
#     if is_union_bank_scanned(file_path):
#         # Change max_pages=2 to max_pages=None once you're happy with the output
#         rows, notes = parse_union_bank(file_path, max_pages=2)
#         if stream:
#             def _gen():
#                 for r in rows:
#                     yield r
#             return _gen(), "pdf_scan", notes
#         return rows, notes
#     # ── existing logic below — completely untouched ───────────────────────────

#     rows = []
#     total_chars = 0
#     row_counter = 0

#     with pdfplumber.open(file_path) as pdf:
#         for page in pdf.pages:
#             total_chars += len(page.extract_text() or "")
#             table_settings = {
#                 "vertical_strategy": "lines",
#                 "horizontal_strategy": "text",
#                 "intersection_tolerance": 5,
#                 "snap_tolerance": 3,
#                 "join_tolerance": 3,
#             }
#             for table in page.extract_tables(table_settings=table_settings) or []:

#                 # Check layout globally for this table based on the longest row found
#                 max_cols = max(len(r) for r in table) if table else 0
#                 if max_cols >= 7:
#                     layout_type = "7col"
#                 elif max_cols == 6:
#                     layout_type = "6col"
#                 else:
#                     layout_type = "5col"

#                 for raw_cells in table:
#                     if not raw_cells:
#                         continue

#                     # Fix 1: Clean first
#                     cells = _clean(raw_cells)

#                     # Fix 2: Header guard check
#                     if _looks_like_header(cells):
#                         continue

#                     row_counter += 1
#                     # Fix 3: Pass the pre-determined layout_type down
#                     row = _map_row(cells, row_counter, layout_type)
#                     if row:
#                         rows.append(row)
#                     else:
#                         print("UNMAPPED ROW:", cells)

#     if rows:
#         if stream:
#             def _gen():
#                 for r in rows:
#                     yield r
#             return _gen(), "pdf_text", "Parsed as text PDF via pdfplumber."
#         return rows, "Parsed as text PDF via pdfplumber."

#     if total_chars < 50:
#         from .image_parser import ocr_pdf_pages
#         from .base import parse_ocr_lines

#         if stream:
#             def _gen():
#                 running_count = 0
#                 for page_num, text, orientation in ocr_pdf_pages(file_path, dpi=300):
#                     page_rows = parse_ocr_lines(text)
#                     for r in page_rows:
#                         running_count += 1
#                         r["source_row"] = running_count
#                         yield r
#             return _gen(), "pdf_scan", "Scanned PDF: OCR extraction in progress (rows appear live)."

#         all_rows = []
#         page_count = 0
#         for page_num, text, orientation in ocr_pdf_pages(file_path, dpi=300):
#             page_count += 1
#             page_rows = parse_ocr_lines(text)
#             for r in page_rows:
#                 r["source_row"] = len(all_rows) + 1
#                 all_rows.append(r)
#         return all_rows, (
#             f"Scanned PDF: OCR across {page_count} pages. "
#             f"All {len(all_rows)} extracted rows are flagged OCR_NEEDS_REVIEW — "
#             f"balance figures are reliable, debit/credit split requires manual confirmation."
#         )

#     with pdfplumber.open(file_path) as pdf:
#         full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
#     final_rows = parse_text_lines(full_text)
#     if stream:
#         def _gen():
#             for r in final_rows:
#                 yield r
#         return _gen(), "pdf_text", "Text PDF without clean tables: line-based parse."

#     print("---------", final_rows, "---------")
#     return final_rows, "Text PDF without clean tables: line-based parse."





































# def parse(file_path: str, stream: bool = False):
#     rows = []
#     total_chars = 0
#     row_counter = 0
    
#     with pdfplumber.open(file_path) as pdf:
#         for page in pdf.pages:
#             total_chars += len(page.extract_text() or "")
#             table_settings = {
#                 "vertical_strategy": "lines",
#                 "horizontal_strategy": "text",
#                 "intersection_tolerance": 5,
#                 "snap_tolerance": 3,
#                 "join_tolerance": 3,
#             }
#             for table in page.extract_tables(table_settings=table_settings) or []:

#                 # Check layout globally for this table based on the longest row found
#                 max_cols = max(len(r) for r in table) if table else 0
#                 if max_cols >= 7:
#                     layout_type = "7col"
#                 elif max_cols == 6:
#                     layout_type = "6col"
#                 else:
#                     layout_type = "5col"

#                 for raw_cells in table:
#                     if not raw_cells:
#                         continue
                    
#                     # Fix 1: Clean first
#                     cells = _clean(raw_cells)
                    
#                     # Fix 2: Header guard check
#                     if _looks_like_header(cells):
#                         continue
                        
#                     row_counter += 1
#                     # Fix 3: Pass the pre-determined layout_type down
#                     row = _map_row(cells, row_counter, layout_type)
#                     if row:
#                         rows.append(row)
#                     else:
#                         print("UNMAPPED ROW:", cells)

#     if rows:
#         if stream:
#             def _gen():
#                 for r in rows:
#                     yield r
#             return _gen(), "pdf_text", "Parsed as text PDF via pdfplumber."
#         return rows, "Parsed as text PDF via pdfplumber."

#     if total_chars < 50:
#         from .image_parser import ocr_pdf_pages
#         from .base import parse_ocr_lines

#         if stream:
#             def _gen():
#                 running_count = 0
#                 for page_num, text, orientation in ocr_pdf_pages(file_path, dpi=300):
#                     page_rows = parse_ocr_lines(text)
#                     for r in page_rows:
#                         running_count += 1
#                         r["source_row"] = running_count
#                         yield r
#             return _gen(), "pdf_scan", "Scanned PDF: OCR extraction in progress (rows appear live)."

#         all_rows = []
#         page_count = 0
#         for page_num, text, orientation in ocr_pdf_pages(file_path, dpi=300):
#             page_count += 1
#             page_rows = parse_ocr_lines(text)
#             for r in page_rows:
#                 r["source_row"] = len(all_rows) + 1
#                 all_rows.append(r)
#         return all_rows, (
#             f"Scanned PDF: OCR across {page_count} pages. "
#             f"All {len(all_rows)} extracted rows are flagged OCR_NEEDS_REVIEW — "
#             f"balance figures are reliable, debit/credit split requires manual confirmation."
#         )

#     with pdfplumber.open(file_path) as pdf:
#         full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
#     final_rows = parse_text_lines(full_text)
#     if stream:
#         def _gen():
#             for r in final_rows:
#                 yield r
#         return _gen(), "pdf_text", "Text PDF without clean tables: line-based parse."
    
#     print("---------", final_rows, "---------")
#     return final_rows, "Text PDF without clean tables: line-based parse."