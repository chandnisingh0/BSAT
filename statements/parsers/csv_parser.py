"""
CSV / XLSX parser.

Bank exports name columns differently, so we match column names loosely
against a set of known aliases. If your bank uses a name not listed here,
just add it to the alias lists below.
"""
import pandas as pd
from .base import parse_date, parse_amount

# Map standard field -> possible column names (lowercased, no spaces)
COLUMN_ALIASES = {
    "txn_date":  ["date", "txndate", "transactiondate", "valuedate", "postingdate"],
    "narration": ["narration", "description", "particulars", "details", "remarks"],
    "debit":     ["debit", "withdrawal", "withdrawalamt", "debitamount", "dr", "paid"],
    "credit":    ["credit", "deposit", "depositamt", "creditamount", "cr", "received"],
    "balance":   ["balance", "closingbalance", "runningbalance", "availablebalance"],
    "reference": ["reference", "chequeno", "refno", "utr", "transactionref", "chqno"],
}


def _norm(name):
    return str(name).lower().replace(" ", "").replace(".", "").replace("_", "")


def _find_column(columns, aliases):
    normalized = {_norm(c): c for c in columns}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def parse(file_path):
    if str(file_path).lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(file_path)
    else:
        df = pd.read_csv(file_path)

    # locate the actual column for each standard field
    colmap = {field: _find_column(df.columns, aliases)
              for field, aliases in COLUMN_ALIASES.items()}

    rows = []
    for idx, record in df.iterrows():
        def get(field):
            col = colmap[field]
            return record[col] if col and pd.notna(record[col]) else None

        rows.append({
            "txn_date": parse_date(get("txn_date")),
            "narration_raw": str(get("narration") or "").strip(),
            "debit": parse_amount(get("debit")),
            "credit": parse_amount(get("credit")),
            "balance": parse_amount(get("balance")),
            "reference": str(get("reference") or "").strip(),
            "source_row": int(idx) + 2,  # +2: header row + 1-based
        })
    return rows
