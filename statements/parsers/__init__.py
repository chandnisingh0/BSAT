"""
Dispatcher: looks at the file extension and calls the right parser.
Returns (rows, file_type, notes).

Parsers are imported lazily (inside the function) so that a missing optional
dependency (e.g. OCR libs) never breaks parsing of other file types.
"""
import os

def parse_file(file_path, stream: bool = False):
    ext = file_path.lower().rsplit(".", 1)[-1] if "." in file_path else ""

    if ext in ("csv", "xlsx", "xls"):
        from . import csv_parser
        rows = csv_parser.parse(file_path)
        if stream:
            return iter(rows), "csv", "Parsed CSV/XLSX."
        return rows, "csv", "Parsed CSV/XLSX."

    elif ext == "pdf":
        from . import pdf_parser
        if stream:
            rows_iter, file_type, notes = pdf_parser.parse(file_path, stream=True)
            return rows_iter, file_type, notes
        rows, notes = pdf_parser.parse(file_path)
        return rows, "pdf", notes

    elif ext == "rpt":
        from . import rpt_parser
        rows, notes = rpt_parser.parse(file_path)
        if stream:
            return iter(rows), "rpt", notes
        return rows, "rpt", notes

    elif ext in ("jpg", "jpeg", "png"):
        from . import image_parser
        rows, notes = image_parser.parse(file_path)
        if stream:
            return iter(rows), "image", notes
        return rows, "image", notes

    else:
        if stream:
            return iter([]), "unknown", "Unrecognized file type."
        return [], "unknown", "Unrecognized file type."

# def parse_file(file_path):
#     ext = os.path.splitext(file_path)[1].lower()

#     if ext in (".csv", ".xlsx", ".xls"):
#         from . import csv_parser
#         rows = csv_parser.parse(file_path)
#         return rows, "csv", "Structured file parsed directly."

#     if ext == ".pdf":
#         from . import pdf_parser
#         rows, notes = pdf_parser.parse(file_path)
#         ftype = "pdf_scan" if "OCR" in notes else "pdf_text"
#         return rows, ftype, notes

#     if ext in (".png", ".jpg", ".jpeg", ".tiff", ".bmp"):
#         from . import image_parser
#         rows, notes = image_parser.parse(file_path)
#         return rows, "image", notes

#     if ext == ".rpt":
#         from . import rpt_parser
#         rows, notes = rpt_parser.parse(file_path)
#         return rows, "rpt", notes

#     return [], "unknown", f"Unsupported file type: {ext}"
