"""
union_bank_parser.py  v5 FIXED
==============================
Parser for Union Bank of India scanned PDF statements.

SEMANTIC column extraction using AMOUNT PATTERNS instead of x-coordinates.
"""

import os
import re
import logging
from decimal import Decimal, InvalidOperation
from datetime import date as _date

try:
    import pytesseract
    from PIL import Image
    from pdf2image import convert_from_path, pdfinfo_from_path
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False

logger = logging.getLogger("statements.union_bank_parser")

_DPI = 200

# ---------------------------------------------------------------------------
# Date fixing
# ---------------------------------------------------------------------------

def _fix_date_parts(dd: str, mm: str, yyyy: str, trailing_ref: str = '') -> tuple:
    """Fix OCR errors in date parts."""
    d = int(dd)
    if d > 31:
        dd = dd.replace('9', '0').replace('8', '0')
    m = int(mm)
    if m > 12:
        mm = mm.replace('9', '0').replace('8', '0')
    # Year: trailing-ref contamination
    if trailing_ref and trailing_ref[0].isalpha() and yyyy.endswith('9'):
        cand = yyyy[:-1] + '8'
        try:
            if 2015 <= int(cand) <= 2026:
                yyyy = cand
        except ValueError:
            pass
    # If year still wrong, try single replacements
    y = int(yyyy)
    if not (2015 <= y <= 2026):
        candidates = []
        for pos in range(len(yyyy)):
            for digit in '0123456789':
                if digit == yyyy[pos]:
                    continue
                cand = yyyy[:pos] + digit + yyyy[pos + 1:]
                try:
                    cv = int(cand)
                    if 2015 <= cv <= 2026:
                        candidates.append(cv)
                except ValueError:
                    pass
        if candidates:
            yyyy = str(min(candidates))
    return dd, mm, yyyy


def _parse_date(dd: str, mm: str, yyyy: str):
    try:
        d, m, y = int(dd), int(mm), int(yyyy)
        if not (1 <= d <= 31 and 1 <= m <= 12 and 2000 <= y <= 2030):
            return None
        return _date(y, m, d)
    except (ValueError, TypeError):
        return None

# ---------------------------------------------------------------------------
# Amount helpers
# ---------------------------------------------------------------------------

def _fix_amount_str(s: str) -> str:
    if not s:
        return s
    s = s.strip()
    s = re.sub(r'(\d)-(\d{2})\s*$', r'\1.\2', s)
    s = s.replace('€', '6').replace('£', '6').replace('©', '0').replace('¢', '6')
    if re.sub(r'[\s,.]', '', s) in ('6116', '616', '6l6', '6I6'):
        return '6.16'
    m = re.match(r'^(\d{1,3}),(\d{2})$', s)
    if m:
        return m.group(1) + '.' + m.group(2)
    s = re.sub(r'0{4,}', '000', s)
    return s


def _parse_amount(s: str):
    if not s:
        return None
    s = _fix_amount_str(s)
    s = re.sub(r'(?i)(cr|dr)\s*$', '', s).strip().rstrip('.,')
    s = re.sub(r'[oO](?=[0-9])', '0', s)
    s = re.sub(r'(?<=[0-9])[oO]', '0', s)
    s = s.replace(',', '').replace(' ', '').replace('/', '').strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _balance_type(s: str) -> str:
    m = re.search(r'(?i)(cr|dr)', (s or '').strip())
    return m.group(1).upper() if m else ''


def _looks_like_amount(s: str) -> bool:
    """Check if token looks like an Indian-formatted amount (X,XX,XXX.XX or partial)."""
    return bool(re.match(r'^\d+,\d{2}', s) or re.match(r'^\d+\.\d{2}', s) or (re.match(r'^\d+,$', s) and len(s) <= 4))

# ---------------------------------------------------------------------------
# Mode & counterparty
# ---------------------------------------------------------------------------

def _extract_mode(text: str) -> str:
    patterns = [
        (r'NEFT[A-Z]?|NEETO|NEVTO|NESTO|NRETO|NEPIO|NEET[^A-Z]', 'NEFT'),
        (r'RTGS[A-Z]?|RIGS|RUGS|RYGS|ATGS', 'RTGS'),
        (r'\bIMPS\b', 'IMPS'),
        (r'\bUPI\b', 'UPI'),
        (r'\bCASH\b', 'CASH'),
        (r'\bCHQ\b|\bCHEQUE\b', 'CHQ'),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return ''


def _extract_counterparty(particulars: str) -> str:
    cp = re.sub(r'^(?:NEFT[O-Z]?|RTGS[O-Z]?|IMPS|UPI)[:\s\-]*', '', particulars, flags=re.IGNORECASE).strip()
    if re.search(r'charges\s+for|customer|^$', cp, re.IGNORECASE):
        return ''
    return cp

# ---------------------------------------------------------------------------
# Balance-sequence corrector
# ---------------------------------------------------------------------------

_DIGIT_SWAPS = {'4': '1', '8': '0', '9': '0', '6': '0', '1': '4', '0': '8', '7': '1', '2': '1'}


def _try_fix_debit(debit_val: Decimal, expected: Decimal, tolerance: Decimal = Decimal('50')) -> Decimal:
    if debit_val is None or expected <= 0:
        return debit_val
    if abs(debit_val - expected) <= tolerance:
        return debit_val
    s = str(int(debit_val))
    candidates = []
    if len(s) > 2:
        try:
            candidates.append(Decimal(s[1:]))
        except InvalidOperation:
            pass
    for pos in range(len(s)):
        c = s[pos]
        if c in _DIGIT_SWAPS:
            fixed = s[:pos] + _DIGIT_SWAPS[c] + s[pos + 1:]
            try:
                candidates.append(Decimal(fixed))
            except InvalidOperation:
                pass
    best, best_diff = debit_val, abs(debit_val - expected)
    for cand in candidates:
        if cand <= 0:
            continue
        diff = abs(cand - expected)
        if diff < best_diff:
            best_diff = diff
            best = cand
    if best != debit_val and best_diff <= tolerance:
        return best
    return debit_val


def _apply_balance_corrections(rows: list) -> list:
    for i in range(1, len(rows)):
        prev_bal = rows[i - 1].get('balance')
        cur_bal  = rows[i].get('balance')
        cur_deb  = rows[i].get('debit')
        if prev_bal is None or cur_bal is None or cur_deb is None:
            continue
        expected = prev_bal - cur_bal
        if expected <= 0:
            continue
        fixed = _try_fix_debit(cur_deb, expected)
        if fixed != cur_deb:
            rows[i] = dict(rows[i])
            rows[i]['debit'] = fixed
            bd = dict(rows[i].get('bank_json_data') or {})
            bd['debit_balance_corrected'] = True
            bd['debit_original'] = str(cur_deb)
            rows[i]['bank_json_data'] = bd
    return rows

# ---------------------------------------------------------------------------
# Single-page parser
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r'^(\d{2})[-/](\d{2})[-/](\d{4})(.*)')
_SKIP_RE = re.compile(
    r'report\s+to|service\s+outlet|account\s+num|report\s+for\s+the\s+period'
    r'|brought\s+forward|opening\s+balance|union\s+bank\s+of\s+india'
    r'|transaction\s+details|page\s+\d+\s+of|^\s*page\s+\d+'
    r'|debit\s+amt|credit\s+amt|balance\s+amt|particulars|\bcontra\b'
    r'|nirwal\s+lifestyle|account\s+opening',
    re.IGNORECASE,
)


def _parse_page_image(img: "Image.Image", page_num: int) -> list:
    """
    SEMANTIC extraction:
    1. Find all amount tokens
    2. Rightmost amount with CR/DR = BALANCE
    3. Amount before balance = DEBIT
    4. Short alphanumeric tokens after date = REFERENCE
    5. Rest = NARRATION
    """
    data = pytesseract.image_to_data(
        img, output_type=pytesseract.Output.DICT, config='--psm 6 --oem 1'
    )

    lines = {}
    for i in range(len(data['text'])):
        word = data['text'][i].strip()
        if not word:
            continue
        key = data['block_num'][i] * 10000 + data['par_num'][i] * 1000 + data['line_num'][i]
        lines.setdefault(key, []).append(word)

    rows = []
    row_counter = 0

    for key in sorted(lines):
        tokens = lines[key]
        if not tokens:
            continue

        dm = _DATE_RE.match(tokens[0])
        if not dm:
            continue

        dd, mm, yyyy, trail = dm.group(1), dm.group(2), dm.group(3), dm.group(4).strip()
        dd, mm, yyyy = _fix_date_parts(dd, mm, yyyy, trailing_ref=trail)
        txn_date = _parse_date(dd, mm, yyyy)
        if txn_date is None:
            continue

        line_text = ' '.join(tokens)
        if _SKIP_RE.search(line_text):
            continue

        # ===== SEMANTIC EXTRACTION =====
        # Find ALL amount-like tokens (skip date token)
        amount_indices = []
        for i, tok in enumerate(tokens[1:], start=1):
            if _looks_like_amount(tok):
                amount_indices.append(i)

        if not amount_indices:
            continue

        # BALANCE: rightmost amount with CR/DR suffix
        balance_raw = None
        balance_idx = None
        for i in reversed(amount_indices):
            tok = tokens[i]
            if re.search(r'(?i)(cr|dr)', tok):
                balance_raw = tok
                balance_idx = i
                break

        if balance_raw is None:
            continue

        bal_val = _parse_amount(balance_raw)
        if bal_val is None:
            continue

        # DEBIT: rightmost amount BEFORE balance (excluding balance)
        debit_raw = None
        debit_idx = None
        for i in reversed(amount_indices):
            if i >= balance_idx:
                continue
            debit_raw = tokens[i]
            debit_idx = i
            break

        deb_val = _parse_amount(debit_raw) if debit_raw else None

        # REFERENCE: tokens after date until first "long text" or "Charges"
        ref_tokens = []
        if trail:
            ref_tokens.append(trail)

        for i in range(1, len(tokens)):
            tok = tokens[i]
            # Stop at first amount
            if _looks_like_amount(tok):
                break
            # Stop at "Charges" (marks narration start)
            if tok.lower() == 'charges':
                break
            # Stop at long words (company names, > 15 chars)
            if len(tok) > 15:
                break
            # Include short, alphanumeric tokens as ref
            if len(tok) <= 15 and (tok.isalnum() or tok.replace('-', '').isalnum()):
                ref_tokens.append(tok)
            else:
                break

        ref = ' '.join(ref_tokens).strip()

        # NARRATION: everything after ref, excluding amounts
        part_tokens = []
        # Skip tokens up to end of ref tokens
        start_idx = len(ref_tokens) + 1  # +1 for date token

        for i in range(start_idx, len(tokens)):
            tok = tokens[i]
            # Skip amount tokens
            if i == debit_idx or i == balance_idx:
                continue
            # Skip pure amounts without context
            if _looks_like_amount(tok):
                continue
            part_tokens.append(tok)

        parts = ' '.join(part_tokens).strip()

        bal_type = _balance_type(balance_raw)
        row_counter += 1

        rows.append({
            'txn_date':          txn_date,
            'value_date':        None,
            'txn_time':          None,
            'narration_raw':     parts,
            'debit':             deb_val,
            'credit':            None,
            'balance':           bal_val,
            'balance_type':      bal_type,
            'reference':         ref,
            'txn_mode':          _extract_mode(ref + ' ' + parts),
            'counterparty_name': _extract_counterparty(parts),
            'source_row':        row_counter,
            'quality_flag':      'OCR_UNION_BANK',
            'bank_json_data': {
                'page_num':    page_num,
                'raw_line':    line_text,
                'debit_raw':   debit_raw or '',
                'balance_raw': balance_raw,
            },
        })

    rows = _apply_balance_corrections(rows)
    return rows

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def is_union_bank_scanned(file_path: str) -> bool:
    fname = os.path.basename(file_path).upper()
    if re.search(r'UNION[_\s\-]BANK', fname):
        return True
    if not _OCR_AVAILABLE:
        return False
    try:
        pages = convert_from_path(file_path, dpi=150, first_page=1, last_page=1)
        if not pages:
            return False
        img = pages[0].rotate(180)
        w, h = img.size
        crop = img.crop((0, 0, w, int(h * 0.10)))
        text = pytesseract.image_to_string(crop, config='--psm 6')
        return bool(re.search(r'UNION\s+BANK\s+OF\s+INDIA', text, re.IGNORECASE))
    except Exception as exc:
        logger.warning(f"Detection failed: {exc}")
        return False

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(file_path: str, max_pages: int = None) -> tuple:
    if not _OCR_AVAILABLE:
        return [], 'union_bank_parser: missing OCR deps. pip install pytesseract Pillow pdf2image'

    info = pdfinfo_from_path(file_path)
    total_pages = info.get('Pages', 0)
    if max_pages:
        total_pages = min(total_pages, max_pages)

    logger.info(f'Union Bank parser: {total_pages} page(s)')

    all_rows = []

    for page_num in range(1, total_pages + 1):
        try:
            pages = convert_from_path(file_path, dpi=_DPI, first_page=page_num, last_page=page_num)
            if not pages:
                continue
            img = pages[0].rotate(180)
            page_rows = _parse_page_image(img, page_num)
            offset = len(all_rows)
            for r in page_rows:
                r['source_row'] += offset
            all_rows.extend(page_rows)
            logger.info(f'  Page {page_num}: {len(page_rows)} rows')
        except Exception as exc:
            logger.error(f'  Page {page_num}: {exc}')

    return all_rows, (
        f'Union Bank scanned PDF: {_DPI} dpi OCR, {total_pages} page(s), '
        f'{len(all_rows)} rows. Semantic (amount-pattern) column detection. '
        f'Balance reliable, debit best-effort.'
    )


# """
# union_bank_parser.py  v3
# ========================
# Parser for Union Bank of India scanned PDF statements (Canon scanner, 180° rotated).

# What this does differently from the generic image_parser:
# - Renders at 200 dpi (sharper text than the default 150)
# - Uses word-level bounding boxes to separate columns instead of hoping
#   text columns are cleanly whitespace-delimited
# - Recalibrated column boundaries for this specific statement layout
# - Date fixes: impossible day/month (93->03), trailing-ref contamination
#   in year (2019S -> year=2018, ref=S...), impossible year (2028->2018)
# - Amount fixes: dash-as-decimal, special chars, split debit tokens joined,
#   "6116"->6.16 (standard UBI NEFT charge)
# - Balance-sequence correction: uses prev_balance - cur_balance as oracle
#   to fix spurious leading digits in debit (e.g. 431262->131262)
# """

# import os
# import re
# import logging
# from decimal import Decimal, InvalidOperation
# from datetime import date as _date

# try:
#     import pytesseract
#     from PIL import Image
#     from pdf2image import convert_from_path, pdfinfo_from_path
#     _OCR_AVAILABLE = True
# except ImportError:
#     _OCR_AVAILABLE = False

# logger = logging.getLogger("statements.union_bank_parser")

# # ---------------------------------------------------------------------------
# # Column fractions — calibrated for 1654 px wide (200 dpi)
# # ---------------------------------------------------------------------------
# _REF_ZONE_END_FRAC  = 370  / 1654
# _DEBIT_START_FRAC   = 750  / 1654
# _BALANCE_START_FRAC = 1150 / 1654
# _DPI = 200

# # ---------------------------------------------------------------------------
# # Patterns
# # ---------------------------------------------------------------------------
# _DATE_RE = re.compile(r'^(\d{2})[-/](\d{2})[-/](\d{4})(.*)')

# _SKIP_RE = re.compile(
#     r'report\s+to|service\s+outlet|account\s+num|account\s+mum'
#     r'|report\s+for\s+the\s+period|brought\s+forward|opening\s+balance'
#     r'|union\s+bank\s+of\s+india|transaction\s+details'
#     r'|page\s+\d+\s+of\s+\d+|^\s*page\s+\d+'
#     r'|debit\s+amt|credit\s+amt|balance\s+amt|particulars'
#     r'|\bcontra\b|account\s+opening|nirwal\s+lifestyle',
#     re.IGNORECASE,
# )

# _MODE_PATTERNS = [
#     (re.compile(r'NEFT[A-Z]?|NEETO|NEVTO|NESTO|NRETO|NEPIO|NEET[^A-Z]', re.I), 'NEFT'),
#     (re.compile(r'RTGS[A-Z]?|RIGS|RUGS|RYGS|ATGS', re.I), 'RTGS'),
#     (re.compile(r'\bIMPS\b', re.I), 'IMPS'),
#     (re.compile(r'\bUPI\b',  re.I), 'UPI'),
#     (re.compile(r'\bCASH\b', re.I), 'CASH'),
#     (re.compile(r'\bCHQ\b|\bCHEQUE\b', re.I), 'CHQ'),
# ]

# _STRIP_MODE_PREFIX = re.compile(
#     r'^(?:NEFT[O-Z]?|RTGS[O-Z]?|IMPS|UPI)[:\s\-]*', re.IGNORECASE
# )
# _SKIP_COUNTERPARTY = re.compile(
#     r'charges\s+for|customer|^$', re.IGNORECASE
# )

# # ---------------------------------------------------------------------------
# # Date helpers
# # ---------------------------------------------------------------------------

# def _fix_date_parts(dd: str, mm: str, yyyy: str, trailing_ref: str = '') -> tuple:
#     """
#     Correct OCR errors in DD, MM, YYYY strings.

#     Known failure modes:
#     - Day/month > valid range: digit 9 or 8 misread from 0 (93->03, 94->04)
#     - Year contaminated by first char of ref token bleeding in:
#         "2018S..." OCR'd as "2019" + "S..." (8+S blend -> 9S)
#         Fix: if trailing starts with a letter and year ends in 9, try 9->8
#     - Year completely garbled (2076, 2028):
#         Try every single-digit replacement, take smallest valid year
#     """
#     # --- Day ---
#     d = int(dd)
#     if d > 31:
#         dd2 = dd.replace('9', '0').replace('8', '0')
#         try:
#             if 1 <= int(dd2) <= 31:
#                 dd = dd2
#         except ValueError:
#             pass

#     # --- Month ---
#     m = int(mm)
#     if m > 12:
#         mm2 = mm.replace('9', '0').replace('8', '0')
#         try:
#             if 1 <= int(mm2) <= 12:
#                 mm = mm2
#         except ValueError:
#             pass

#     # --- Year ---
#     # Step 1: trailing-ref contamination check (must come before range check)
#     # e.g. "03-04-2018S38676346" OCR'd as "03-04-2019S" + "38676346"
#     if trailing_ref and trailing_ref[0].isalpha() and yyyy.endswith('9'):
#         cand = yyyy[:-1] + '8'
#         try:
#             if 2015 <= int(cand) <= 2026:
#                 yyyy = cand
#         except ValueError:
#             pass

#     # Step 2: if still outside valid range, try single-digit replacements
#     y = int(yyyy)
#     if not (2015 <= y <= 2026):
#         candidates = []
#         for pos in range(len(yyyy)):
#             for digit in '0123456789':
#                 if digit == yyyy[pos]:
#                     continue
#                 cand = yyyy[:pos] + digit + yyyy[pos + 1:]
#                 try:
#                     cv = int(cand)
#                     if 2015 <= cv <= 2026:
#                         candidates.append(cv)
#                 except ValueError:
#                     pass
#         if candidates:
#             yyyy = str(min(candidates))

#     return dd, mm, yyyy


# def _parse_date(dd: str, mm: str, yyyy: str):
#     try:
#         d, m, y = int(dd), int(mm), int(yyyy)
#         if not (1 <= d <= 31 and 1 <= m <= 12 and 2000 <= y <= 2030):
#             return None
#         return _date(y, m, d)
#     except (ValueError, TypeError):
#         return None

# # ---------------------------------------------------------------------------
# # Amount helpers
# # ---------------------------------------------------------------------------

# def _fix_amount_str(s: str) -> str:
#     """Fix OCR noise in amount strings before numeric parsing."""
#     if not s:
#         return s
#     s = s.strip()

#     # Dash as decimal point: "13,30,000-00" -> "13,30,000.00"
#     s = re.sub(r'(\d)-(\d{2})\s*$', r'\1.\2', s)

#     # Special character substitutions
#     s = (s.replace('€', '6').replace('£', '6')
#           .replace('©', '0').replace('¢', '6'))

#     # Known garbled NEFT charge: 6.16
#     if re.sub(r'[\s,.]', '', s) in ('6116', '616', '6l6', '6I6'):
#         return '6.16'

#     # "NN,NN" (comma where dot should be): "17,44" -> "17.44"
#     m = re.match(r'^(\d{1,3}),(\d{2})$', s)
#     if m:
#         return m.group(1) + '.' + m.group(2)

#     # Collapse 4+ zero runs (inserted OCR digit): "0900" -> "000"
#     s = re.sub(r'0{4,}', '000', s)

#     return s


# def _parse_amount(s: str):
#     """Parse Indian-format amount string with OCR fixes. Returns Decimal or None."""
#     if not s:
#         return None
#     s = _fix_amount_str(s)
#     s = re.sub(r'(?i)(cr|dr)\s*$', '', s).strip().rstrip('.,')
#     # o/O -> 0 inside numbers
#     s = re.sub(r'[oO](?=[0-9])', '0', s)
#     s = re.sub(r'(?<=[0-9])[oO]', '0', s)
#     s = s.replace(',', '').replace(' ', '').replace('/', '').strip()
#     if not s:
#         return None
#     try:
#         return Decimal(s)
#     except InvalidOperation:
#         return None


# def _balance_type(s: str) -> str:
#     m = re.search(r'(?i)(cr|dr)', (s or '').strip())
#     return m.group(1).upper() if m else ''

# # ---------------------------------------------------------------------------
# # Mode & counterparty
# # ---------------------------------------------------------------------------

# def _extract_mode(text: str) -> str:
#     for pattern, label in _MODE_PATTERNS:
#         if pattern.search(text):
#             return label
#     return ''


# def _extract_counterparty(particulars: str) -> str:
#     cp = _STRIP_MODE_PREFIX.sub('', particulars).strip()
#     if _SKIP_COUNTERPARTY.search(cp):
#         return ''
#     return cp

# # ---------------------------------------------------------------------------
# # Balance-sequence debit corrector
# # ---------------------------------------------------------------------------

# _DIGIT_SWAPS = {
#     '4': '1', '8': '0', '9': '0', '6': '0',
#     '1': '4', '0': '8', '7': '1', '2': '1',
# }


# def _try_fix_debit(debit_val: Decimal, expected: Decimal,
#                    tolerance: Decimal = Decimal('50')) -> Decimal:
#     """
#     If debit_val is far from expected (prev_bal - cur_bal), attempt common
#     single-digit OCR substitutions to find a closer value.
#     Only applies the fix if it brings us within tolerance rupees.
#     """
#     if debit_val is None or expected <= 0:
#         return debit_val
#     if abs(debit_val - expected) <= tolerance:
#         return debit_val

#     s = str(int(debit_val))
#     candidates = []

#     # Drop leading digit
#     if len(s) > 2:
#         try:
#             candidates.append(Decimal(s[1:]))
#         except InvalidOperation:
#             pass

#     # Replace each digit using OCR confusion map
#     for pos in range(len(s)):
#         c = s[pos]
#         if c in _DIGIT_SWAPS:
#             fixed = s[:pos] + _DIGIT_SWAPS[c] + s[pos + 1:]
#             try:
#                 candidates.append(Decimal(fixed))
#             except InvalidOperation:
#                 pass

#     best, best_diff = debit_val, abs(debit_val - expected)
#     for cand in candidates:
#         if cand <= 0:
#             continue
#         diff = abs(cand - expected)
#         if diff < best_diff:
#             best_diff = diff
#             best = cand

#     if best != debit_val and best_diff <= tolerance:
#         logger.debug(f"  balance-corrected debit {debit_val} -> {best} (expected ~{expected})")
#         return best
#     return debit_val


# def _apply_balance_corrections(rows: list) -> list:
#     """Use prev_balance - cur_balance as oracle to fix obvious debit OCR errors."""
#     for i in range(1, len(rows)):
#         prev_bal = rows[i - 1].get('balance')
#         cur_bal  = rows[i].get('balance')
#         cur_deb  = rows[i].get('debit')

#         if prev_bal is None or cur_bal is None or cur_deb is None:
#             continue

#         expected = prev_bal - cur_bal
#         if expected <= 0:
#             continue

#         fixed = _try_fix_debit(cur_deb, expected)
#         if fixed != cur_deb:
#             rows[i] = dict(rows[i])
#             rows[i]['debit'] = fixed
#             bd = dict(rows[i].get('bank_json_data') or {})
#             bd['debit_balance_corrected'] = True
#             bd['debit_original'] = str(cur_deb)
#             rows[i]['bank_json_data'] = bd

#     return rows

# # ---------------------------------------------------------------------------
# # Single-page parser
# # ---------------------------------------------------------------------------

# def _parse_page_image(img: "Image.Image", page_num: int) -> list:
#     """Extract transaction rows from one corrected (upright) PIL Image."""
#     img_w, _ = img.size

#     ref_end       = int(_REF_ZONE_END_FRAC  * img_w)
#     debit_start   = int(_DEBIT_START_FRAC   * img_w)
#     balance_start = int(_BALANCE_START_FRAC * img_w)

#     data = pytesseract.image_to_data(
#         img,
#         output_type=pytesseract.Output.DICT,
#         config='--psm 6 --oem 1',
#     )

#     # Group words by OCR line key
#     lines = {}
#     for i in range(len(data['text'])):
#         word = data['text'][i].strip()
#         if not word:
#             continue
#         key = (
#             data['block_num'][i] * 10000
#             + data['par_num'][i]  * 1000
#             + data['line_num'][i]
#         )
#         lines.setdefault(key, []).append({
#             'text': word,
#             'x':    data['left'][i],
#         })

#     rows = []
#     row_counter = 0

#     for key in sorted(lines):
#         words = lines[key]
#         if not words:
#             continue

#         dm = _DATE_RE.match(words[0]['text'])
#         if not dm:
#             continue

#         dd   = dm.group(1)
#         mm   = dm.group(2)
#         yyyy = dm.group(3)
#         trail = dm.group(4).strip()

#         dd, mm, yyyy = _fix_date_parts(dd, mm, yyyy, trailing_ref=trail)
#         txn_date = _parse_date(dd, mm, yyyy)
#         if txn_date is None:
#             continue

#         line_text = ' '.join(w['text'] for w in words)
#         if _SKIP_RE.search(line_text):
#             continue

#         # Reference: trailing on date token, or next word(s) in ref zone
#         ref_zone = [w for w in words if w['x'] < ref_end]
#         ref = trail
#         if not ref and len(ref_zone) > 1:
#             ref = ' '.join(w['text'] for w in ref_zone[1:]).strip()

#         # Particulars: between ref zone and debit zone
#         parts = ' '.join(
#             w['text'] for w in words
#             if ref_end <= w['x'] < debit_start
#         ).strip()

#         # Debit: join all tokens in zone (handles split amounts like "19, 60,000.00")
#         deb_text = ''.join(
#             w['text'] for w in words
#             if debit_start <= w['x'] < balance_start
#         ).strip()
#         deb_val = _parse_amount(deb_text)

#         # Balance
#         bal_text = ''.join(
#             w['text'] for w in words if w['x'] >= balance_start
#         ).strip()
#         bal_val  = _parse_amount(bal_text)
#         bal_type = _balance_type(bal_text)

#         if bal_val is None:
#             continue

#         row_counter += 1
#         rows.append({
#             'txn_date':          txn_date,
#             'value_date':        None,
#             'txn_time':          None,
#             'narration_raw':     parts,
#             'debit':             deb_val,
#             'credit':            None,
#             'balance':           bal_val,
#             'balance_type':      bal_type,
#             'reference':         ref,
#             'txn_mode':          _extract_mode(ref + ' ' + parts),
#             'counterparty_name': _extract_counterparty(parts),
#             'source_row':        row_counter,
#             'quality_flag':      'OCR_UNION_BANK',
#             'bank_json_data': {
#                 'page_num':    page_num,
#                 'raw_line':    line_text,
#                 'debit_raw':   deb_text,
#                 'balance_raw': bal_text,
#             },
#         })

#     rows = _apply_balance_corrections(rows)
#     return rows

# # ---------------------------------------------------------------------------
# # Detection
# # ---------------------------------------------------------------------------

# def is_union_bank_scanned(file_path: str) -> bool:
#     """
#     Returns True if this PDF is a Union Bank scanned statement.
#     Checks filename first (free), then OCR at 150 dpi if needed.
#     """
#     fname = os.path.basename(file_path).upper()
#     if re.search(r'UNION[_\s\-]BANK', fname):
#         logger.info('is_union_bank_scanned: matched by filename')
#         return True

#     if not _OCR_AVAILABLE:
#         return False
#     try:
#         pages = convert_from_path(file_path, dpi=150, first_page=1, last_page=1)
#         if not pages:
#             return False
#         img = pages[0].rotate(180)
#         w, h = img.size
#         crop = img.crop((0, 0, w, int(h * 0.10)))
#         text = pytesseract.image_to_string(crop, config='--psm 6')
#         result = bool(re.search(r'UNION\s+BANK\s+OF\s+INDIA', text, re.IGNORECASE))
#         logger.info(f'is_union_bank_scanned: OCR result = {result}')
#         return result
#     except Exception as exc:
#         logger.warning(f'is_union_bank_scanned check failed: {exc}')
#         return False

# # ---------------------------------------------------------------------------
# # Public API
# # ---------------------------------------------------------------------------

# def parse(file_path: str, max_pages: int = None) -> tuple:
#     """
#     Parse a Union Bank scanned PDF statement.

#     Args:
#         file_path:  Path to the PDF.
#         max_pages:  Limit pages (pass 2 while testing, None for full run).

#     Returns:
#         (rows: list[dict], notes: str)
#     """
#     if not _OCR_AVAILABLE:
#         return [], (
#             'union_bank_parser: missing OCR deps. '
#             'Run: pip install pytesseract Pillow pdf2image'
#         )

#     info = pdfinfo_from_path(file_path)
#     total_pages = info.get('Pages', 0)
#     if max_pages:
#         total_pages = min(total_pages, max_pages)

#     logger.info(f'Union Bank parser: {total_pages} page(s) — {file_path}')

#     all_rows = []

#     for page_num in range(1, total_pages + 1):
#         try:
#             pages = convert_from_path(
#                 file_path, dpi=_DPI,
#                 first_page=page_num, last_page=page_num,
#             )
#             if not pages:
#                 continue

#             img = pages[0].rotate(180)
#             page_rows = _parse_page_image(img, page_num)

#             offset = len(all_rows)
#             for r in page_rows:
#                 r['source_row'] += offset
#             all_rows.extend(page_rows)

#             logger.info(f'  Page {page_num}/{total_pages}: {len(page_rows)} rows')

#         except Exception as exc:
#             logger.error(f'  Page {page_num} failed: {exc}')

#     notes = (
#         f'Union Bank scanned PDF: OCR ({_DPI} dpi) across {total_pages} page(s). '
#         f'{len(all_rows)} rows extracted. '
#         f'Flagged OCR_UNION_BANK — balance reliable, debits best-effort '
#         f'with balance-sequence correction. No credit column in this format.'
#     )
#     return all_rows, notes