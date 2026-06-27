"""
RPT parser — handles PNB Customer Account Ledger Report (fixed-width text).
Also handles generic delimited/multi-space RPT files as fallback.
"""
import re
from .base import (
    parse_date, parse_amount, parse_balance_type,
    parse_text_lines, extract_mode, extract_counterparty,
)


# ── PNB fixed-width column positions (0-indexed, exclusive end) ───────────────
PNB_LAYOUT = {
    "gl_date":     (1,   11),
    "value_date":  (13,  23),
    "instrument":  (23,  39),
    "particulars": (39,  99),
    "debit":       (105, 118),
    "credit":      (118, 131),
    "balance":     (131, 155),
    "entry_user":  (156, 166),
}

# Lines that look like PNB headers / separators — skip them
_PNB_SKIP = re.compile(
    r"GL\.\s*Date|Page Total|B/F Balance|Opening Balance"
    r"|Service Out|Account No|Peg Review|------"
    r"|PUNJAB NATIONAL BANK|Customer Account Ledger"
    r"|Report To|SolId|Set Id|Gl Sub Head|Acct Range"
    r"|Currency Code|Account Label|Open/Closed|Period|Limit Details"
    r"|Order by|REP31|Page\s+\d+",
    re.IGNORECASE,
)


def _is_pnb_format(lines: list[str]) -> bool:
    hits = 0
    for line in lines:
        if len(line) > 140:
            bal_chunk = line[131:155]   # CORRECTED from 139:162
            if re.search(r'\d.*?(Cr|Dr)', bal_chunk):
                hits += 1
                if hits >= 2:
                    return True
    return False

def _parse_pnb(lines: list[str]) -> tuple[list[dict], str]:
    rows = []
    row_counter = 0

    for raw_line in lines:
        line = raw_line.rstrip()

        # Skip headers, separators, empty lines
        if not line.strip() or _PNB_SKIP.search(line):
            continue
        if len(line) < 100:
            continue  # too short to be a data line

        def col(name):
            s, e = PNB_LAYOUT[name]
            return line[s:e].strip() if len(line) >= e else ""

        gl_date = parse_date(col("gl_date"))
        if gl_date is None:
            continue

        row_counter += 1
        narration = col("particulars")
        debit_raw  = col("debit")
        credit_raw = col("credit")
        balance_raw = col("balance")

        # Balance in PNB looks like: "10,00,000.00Cr"
        balance_type = parse_balance_type(balance_raw)

        # Extra fields for bank_json_data
        extra = {
            "value_date": col("value_date"),
            "instrument_number": col("instrument"),
            "entry_user_id": col("entry_user"),
            "raw_line": raw_line,
        }

        rows.append({
            "txn_date":          gl_date,
            "value_date":        parse_date(col("value_date")),
            "narration_raw":     narration,
            "debit":             parse_amount(debit_raw),
            "credit":            parse_amount(credit_raw),
            "balance":           parse_amount(balance_raw),
            "balance_type":      balance_type,
            "reference":         col("instrument"),
            "txn_mode":          extract_mode(narration),
            "counterparty_name": extract_counterparty(narration),
            "source_row":        row_counter,
            "bank_json_data":    extra,
        })

    return rows, "RPT parsed as PNB fixed-width Customer Account Ledger."


# ── Generic delimited fallback ────────────────────────────────────────────────

def _detect_delimiter(sample_lines):
    for delim in ["|", "\t"]:
        if all(delim in line for line in sample_lines if line.strip()):
            return delim
    if all(re.search(r"\s{2,}", line) for line in sample_lines if line.strip()):
        return "MULTISPACE"
    return None


def _split(line, delim):
    if delim == "MULTISPACE":
        return re.split(r"\s{2,}", line.strip())
    return [c.strip() for c in line.split(delim)]


def parse(file_path: str) -> tuple[list[dict], str]:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [ln.rstrip("\n") for ln in f]

    # ── PNB path ──────────────────────────────────────────────────────────────
    if _is_pnb_format(lines):
        return _parse_pnb(lines)

    # ── Delimited path ────────────────────────────────────────────────────────
    data_lines = [ln for ln in lines if ln.strip()]
    delim = _detect_delimiter(data_lines[:10]) if data_lines else None
    if delim:
        rows = []
        for i, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            cells = _split(line, delim)
            date = parse_date(cells[0]) if cells else None
            if not date:
                continue
            narration = cells[1] if len(cells) > 1 else ""
            bal_raw = cells[4] if len(cells) > 4 else ""
            rows.append({
                "txn_date":          date,
                "narration_raw":     narration,
                "debit":             parse_amount(cells[2]) if len(cells) > 2 else None,
                "credit":            parse_amount(cells[3]) if len(cells) > 3 else None,
                "balance":           parse_amount(bal_raw),
                "balance_type":      parse_balance_type(bal_raw),
                "reference":         cells[5] if len(cells) > 5 else "",
                "txn_mode":          extract_mode(narration),
                "counterparty_name": extract_counterparty(narration),
                "source_row":        i,
                "bank_json_data":    {"raw_line": line},
            })
        return rows, f"RPT parsed as delimited ('{delim}')."

    # ── Last resort ───────────────────────────────────────────────────────────
    return parse_text_lines("\n".join(lines)), "RPT parsed via generic line heuristic."