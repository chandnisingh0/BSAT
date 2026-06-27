import re
from datetime import date, datetime, time as dt_time
from decimal import Decimal, InvalidOperation

import re as _re
from datetime import date as _date


DATE_FORMATS = [
    "%d-%m-%Y",   # 26-03-2019  ← PNB format
    "%d/%m/%Y",
    "%d-%b-%Y",   # 26-Mar-2019
    "%d/%b/%Y",
    "%Y-%m-%d",
    "%d-%b-%y",
    "%d/%m/%y",
    "%d-%m-%y",
]


def parse_date(raw: str) -> date | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(raw: str) -> dt_time | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def parse_amount(raw: str) -> Decimal | None:
    if not raw:
        return None
    cleaned = re.sub(r"[,\s]", "", str(raw).strip())
    cleaned = re.sub(r"(Cr|Dr)$", "", cleaned, flags=re.IGNORECASE).strip()
    # if not cleaned or cleaned in ("-", "0", "0.00"):
    #     return None
    if not cleaned or cleaned in ("-",):
        return None

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_balance_type(raw: str) -> str:
    if not raw:
        return ""
    m = re.search(r"(Cr|Dr)\s*$", raw.strip(), re.IGNORECASE)
    return m.group(1).capitalize() if m else ""


# ── Transaction mode & counterparty extraction ────────────────────────────────

_MODE_PATTERNS = [
    (r"\bRTGS\b|^RTGS", "RTGS"),
    (r"\bNEFT\b", "NEFT"),
    (r"\bIMPS\b|^IMPS", "IMPS"),    
    (r"\bUPI\b",  "UPI"),
    (r"\bCASH\s+DEPOSIT\b", "CASH"),
    (r"\bCASH\b", "CASH"),
    (r"\bCHQ\b|\bCHEQUE\b", "CHQ"),
    (r"\bDD\b", "DD"),
]


def extract_mode(narration: str) -> str:
    for pattern, mode in _MODE_PATTERNS:
        if re.search(pattern, narration, re.IGNORECASE):
            return mode
    return ""


def extract_counterparty(narration: str) -> str:
    # NEFT-OW/ref/NAME or RTGS-OW/ref/NAME
    m = re.match(r"(?:NEFT|RTGS)-OW/[^/]+/(.+)", narration, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # NEFT-NAME (plain, no slash)
    m = re.match(r"NEFT-(.+)", narration, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # RTGS followed by name
    m = re.match(r"RTGS(.+)", narration, re.IGNORECASE)
    if m:
        return m.group(1).strip().lstrip("-/ ")
    return ""


# ── Generic line-based heuristic (fallback) ───────────────────────────────────

def parse_text_lines(text: str) -> list[dict]:
    rows = []
    row_counter = 0
    for i, line in enumerate(text.splitlines(), start=1):
        parts = re.split(r"\s{2,}", line.strip())
        if not parts:
            continue
        txn_date = parse_date(parts[0])
        if txn_date is None:
            continue
        row_counter += 1
        narration   = parts[1] if len(parts) > 1 else ""
        debit_raw   = parts[2] if len(parts) > 2 else ""
        credit_raw  = parts[3] if len(parts) > 3 else ""
        balance_raw = parts[4] if len(parts) > 4 else ""
        rows.append({
            "txn_date":          txn_date,
            "narration_raw":     narration,
            "debit":             parse_amount(debit_raw),
            "credit":            parse_amount(credit_raw),
            "balance":           parse_amount(balance_raw),
            "balance_type":      parse_balance_type(balance_raw),
            "reference":         "",
            "txn_mode":          extract_mode(narration),
            "counterparty_name": extract_counterparty(narration),
            "source_row":        row_counter,
            "bank_json_data":    {"raw_line": line},
        })
    return rows

# ── OCR-specific line parser (tolerant of scan noise, digit confusion) ────────

_OCR_DATE_DIGIT_CLASS = r'[0-9OoQDS]'
_OCR_DATE_PATTERN = _re.compile(
    rf'({_OCR_DATE_DIGIT_CLASS}{{1,2}})\s*[-~]\s*({_OCR_DATE_DIGIT_CLASS}{{1,2}})\s*[-~]\s*({_OCR_DATE_DIGIT_CLASS}{{2,4}})'
)
_OCR_DIGIT_FIX = {'O': '0', 'o': '0', 'Q': '0', 'D': '0', 'S': '5'}

_OCR_AMOUNT_PATTERN = _re.compile(
    r'(\d{1,3}(?:[,\s]\d{2,3})*)\s*[.\-]\s*(\d{2})\s*([a-zA-Z]{0,2})'
)
_OCR_DR_MARKERS = {"dr", "pr", "or"}
_OCR_CR_MARKERS = {"cr", "ck", "0r"}


def _ocr_clean_digits(s: str) -> str:
    return "".join(_OCR_DIGIT_FIX.get(ch, ch) for ch in s)


def _ocr_try_parse_date(d_raw, m_raw, y_raw, year_min=2015, year_max=2030):
    d, m, y = _ocr_clean_digits(d_raw), _ocr_clean_digits(m_raw), _ocr_clean_digits(y_raw)
    try:
        day, month = int(d), int(m)
        year = int(y)
        if year < 100:
            year += 2000
        if not (year_min <= year <= year_max):
            return None
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return None
        return _date(year, month, day)
    except (ValueError, TypeError):
        return None


def extract_ocr_date(line: str, search_window: int = 35):
    """Find a date anywhere near the start of an OCR'd line, tolerating
    common digit-confusion (O/0, S/5) and OCR-mangled separators (~ instead of -)."""
    m = _OCR_DATE_PATTERN.search(line[:search_window])
    if not m:
        return None
    return _ocr_try_parse_date(*m.groups())


def _ocr_classify_balance_suffix(suffix: str) -> str:
    s = suffix.lower()
    if s in _OCR_DR_MARKERS:
        return "Dr"
    if s in _OCR_CR_MARKERS:
        return "Cr"
    return ""


def extract_ocr_amounts(line: str):
    """
    Returns (balance, balance_type, txn_amount) extracted from the rightmost
    amount-like tokens on the line. The balance is the LAST amount on the
    line (always carries a Cr/Dr suffix in this statement format) — this is
    the most reliable field. The transaction amount is the second-to-last
    amount, which is usually correct but can occasionally absorb a stray
    reference-number fragment when OCR drops whitespace; treat it as
    best-effort.
    """
    matches = _OCR_AMOUNT_PATTERN.findall(line)
    amounts = []
    for int_part, dec_part, suffix in matches:
        cleaned_int = _re.sub(r"[,\s]", "", int_part)
        try:
            val = float(f"{cleaned_int}.{dec_part}")
        except ValueError:
            continue
        amounts.append((val, _ocr_classify_balance_suffix(suffix)))

    if not amounts:
        return None, "", None

    balance, balance_type = amounts[-1]
    txn_amount = amounts[-2][0] if len(amounts) >= 2 else None
    return balance, balance_type, txn_amount

def parse_ocr_lines(text: str) -> list[dict]:
    """
    Parser specifically for noisy OCR'd scanned bank statements. Unlike
    parse_text_lines() (which assumes clean multi-space-delimited columns),
    this tolerates merged columns, OCR digit confusion, and missing
    whitespace — common on degraded scans. Every row is marked with a
    quality_flag so it surfaces for mandatory human review (per DV-05 /
    OCR accuracy rules), since transaction-amount extraction on this kind
    of input is best-effort, not guaranteed.

    Carries the last successfully-parsed date forward onto rows where the
    date itself couldn't be read (bank statements are date-ordered, so this
    is a reasonable and auditable assumption — the carried-forward fact is
    recorded in bank_json_data for traceability).
    """
    rows = []
    row_counter = 0
    last_good_date = None

    skip_pattern = _re.compile(
        r"page \d+ of|account:|opening balance|brought forward|report for the period"
        r"|report to|service outlet|account number|union bank|punjab national"
        r"|finacle|^\s*date\s|particulars.*debit.*credit|^\s*[|;.\]]\s*$",
        _re.IGNORECASE,
    )

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) < 8:
            continue
        if skip_pattern.search(stripped):
            continue

        extracted_date = extract_ocr_date(stripped)
        date_was_carried = False
        if extracted_date is None:
            if last_good_date is None:
                continue
            extracted_date = last_good_date
            date_was_carried = True
        else:
            last_good_date = extracted_date

        balance, balance_type, txn_amount = extract_ocr_amounts(stripped)
        if balance is None:
            continue

        row_counter += 1
        narration = stripped

        balance_suspect = False
        expected_diff = None
        if rows and txn_amount is not None:
            prev_balance = rows[-1]["balance"]
            if prev_balance is not None:
                expected_diff = abs(balance - prev_balance)
                if expected_diff > 0 and txn_amount > 0:
                    ratio = expected_diff / txn_amount
                    if not (0.85 <= ratio <= 1.15):
                        balance_suspect = True

        flag = "OCR_NEEDS_REVIEW"
        if date_was_carried:
            flag += "_DATE_CARRIED"
        if balance_suspect:
            flag += "_BALANCE_SUSPECT"

        rows.append({
            "txn_date":          extracted_date,
            "narration_raw":     narration,
            "debit":             None,
            "credit":             None,
            "balance":           balance,
            "balance_type":      balance_type,
            "reference":         "",
            "txn_mode":          extract_mode(narration),
            "counterparty_name": extract_counterparty(narration),
            "source_row":        row_counter,
            "quality_flag":      flag,
            "bank_json_data":    {
                "raw_line": line,
                "ocr_txn_amount_guess": txn_amount,
                "date_carried_forward": date_was_carried,
                "balance_suspect": balance_suspect,
                "balance_diff_from_prev": expected_diff,
            },
        })

    return rows