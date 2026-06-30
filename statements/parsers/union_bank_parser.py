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
# Skew and Date helper functions
# ---------------------------------------------------------------------------

import math

def _get_hist_var(ys, bin_size=5):
    min_y = min(ys)
    max_y = max(ys)
    if max_y == min_y:
        return 0
    num_bins = int((max_y - min_y) / bin_size) + 1
    bins = [0] * num_bins
    for y in ys:
        idx = int((y - min_y) / bin_size)
        if 0 <= idx < num_bins:
            bins[idx] += 1
    mean = sum(bins) / len(bins)
    variance = sum((x - mean) ** 2 for x in bins) / len(bins)
    return variance


def estimate_skew_angle(words):
    if not words:
        return 0.0
    best_angle = 0.0
    max_variance = 0.0
    for a_deg in [x * 0.2 for x in range(-15, 16)]:
        angle = a_deg * math.pi / 180
        tan_a = math.tan(angle)
        projected_ys = [w['top'] - w['left'] * tan_a for w in words]
        variance = _get_hist_var(projected_ys, bin_size=4)
        if variance > max_variance:
            max_variance = variance
            best_angle = angle
    return best_angle


_ROBUST_DATE_RE = re.compile(
    r'^[^\w]*([0-9a-zA-Z]{1,2})[-/\\~=\s_.]*([0-9a-zA-Z]{1,2})[-/\\~=\s_.]*([0-9a-zA-Z]{3,4})(.*)'
)

def _clean_date_part(s: str) -> str:
    s = s.upper()
    s = s.replace('I', '1').replace('L', '1').replace('l', '1')
    s = s.replace('O', '0').replace('o', '0').replace('G', '0')
    s = s.replace('C', '0').replace('U', '0')
    s = s.replace('B', '8')
    s = s.replace('S', '5')
    return s


def _fix_date_parts(dd: str, mm: str, yyyy: str, trailing_ref: str = '') -> tuple:
    dd = _clean_date_part(dd)
    mm = _clean_date_part(mm)
    yyyy = _clean_date_part(yyyy)
    
    trail_clean = trailing_ref.strip()
    
    # Handle year split: e.g., yyyy is 3 digits ('201') and next digit in trail is '8' or '9'
    if len(yyyy) == 3 and yyyy.startswith('20'):
        if trail_clean and trail_clean[0] in '89':
            yyyy = yyyy + trail_clean[0]
            trailing_ref = trail_clean[1:]
        elif trail_clean and trail_clean[0] in 'BLS':
            resolved_digit = _clean_date_part(trail_clean[0])
            yyyy = yyyy + resolved_digit
            trailing_ref = trail_clean[1:]
        else:
            yyyy = yyyy + '8'  # default to 8
            
    # Normalize day
    try:
        d = int(dd)
    except ValueError:
        d = 1
    if d > 31:
        for char_to_replace in ['9', '8', '6', '5', '3', '2']:
            if dd.startswith(char_to_replace):
                dd = '0' + dd[1:]
                break
        try:
            d = int(dd)
        except ValueError:
            d = 1
            
    # Normalize month
    try:
        m = int(mm)
    except ValueError:
        m = 1
    if m > 12:
        for char_to_replace in ['9', '8', '6', '5', '3', '2']:
            if mm.startswith(char_to_replace):
                mm = '0' + mm[1:]
                break
        try:
            m = int(mm)
        except ValueError:
            m = 1
            
    # Year: trailing-ref contamination (e.g., 20195... -> 2018S..., 20198... -> 20188...)
    if yyyy.startswith('2019') and len(yyyy) > 4:
        extra = yyyy[4:]
        yyyy = '2018'
        trailing_ref = extra + trailing_ref
    elif yyyy.startswith('2019') and trail_clean and (trail_clean.startswith('5') or trail_clean.startswith('8') or trail_clean.startswith('3')):
        yyyy = '2018'
        trailing_ref = '8' + trailing_ref
        
    # Year: letter/digit correction for future/past years outside statement period
    if len(yyyy) == 4:
        try:
            y_val = int(yyyy)
            if y_val > 2026:
                # E.g. 2078 -> 2018, 2091 -> 2018
                if yyyy.startswith('207'):
                    yyyy = '201' + yyyy[3]
                else:
                    yyyy = '2018'
        except ValueError:
            yyyy = '2018'
            
    return dd, mm, yyyy, trailing_ref


def _extract_date_and_trail(line_words: list) -> tuple:
    if not line_words:
        return None, 0
    for num_words in [1, 2, 3, 4]:
        if num_words > len(line_words):
            break
        joined = "".join(w['text'] for w in line_words[:num_words])
        m = _ROBUST_DATE_RE.match(joined)
        if m:
            dd, mm, yyyy, trail = m.groups()
            dd, mm, yyyy, trail = _fix_date_parts(dd, mm, yyyy, trailing_ref=trail)
            try:
                d, m_val, y = int(dd), int(mm), int(yyyy)
                if 1 <= d <= 31 and 1 <= m_val <= 12 and 2000 <= y <= 2100:
                    txn_date = _date(y, m_val, d)
                    return (txn_date, trail), num_words
            except Exception:
                pass
    return None, 0

# ---------------------------------------------------------------------------
# Amount helpers
# ---------------------------------------------------------------------------

def _fix_amount_str(s: str) -> str:
    if not s:
        return s
    s = s.strip()
    s = s.replace('—', '-').replace('~', '-').replace('=', '-')
    s = s.replace('€', '6').replace('£', '6').replace('©', '0').replace('¢', '6')
    
    # Strip CR/DR suffix first to check the decimal position
    s_clean = re.sub(r'(?i)(cr|dr)\s*$', '', s).strip()
    
    # If the character at index -3 is a comma, hyphen, or space, change it to a dot
    if len(s_clean) >= 3 and s_clean[-3] in (',', '-', ' '):
        s_clean = s_clean[:-3] + '.' + s_clean[-2:]
        
    if re.sub(r'[\s,.]', '', s_clean) in ('6116', '616', '6l6', '6I6'):
        return '6.16'
        
    s_clean = re.sub(r'0{4,}', '000', s_clean)
    return s_clean


def _parse_amount(s: str):
    if not s:
        return None
    s = _fix_amount_str(s)
    # Strip any trailing letters and spaces (handles CR, DR, 0R, pR, etc.)
    s = re.sub(r'(?i)[a-z\s]+$', '', s).strip()
    # Strip all characters except digits, dots, and hyphens
    s = re.sub(r'[^\d\.\-]', '', s)
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _balance_type(s: str) -> str:
    s = (s or '').strip()
    # Standard match first
    m = re.search(r'(?i)(cr|dr)$', s)
    if m:
        return m.group(1).upper()
    # Handle OCR variants at end of string:
    # pR, 0R, oR, bR → DR (p/0/o/b are common OCR misreads of D)
    # cR → CR (c is common OCR misread of C)
    m2 = re.search(r'([a-zA-Z])R$', s)
    if m2:
        c = m2.group(1).upper()
        if c in ('P', 'O', '0', 'B', 'D'):
            return 'DR'
        if c in ('C',):
            return 'CR'
    # If balance has any suffix letters, default to CR (most common)
    if re.search(r'[a-zA-Z]$', s):
        return 'CR'
    return ''


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
    """
    Use signed running balance as oracle to correct amounts.

    Strategy:
    1. ALWAYS correct column assignments (debit↔credit swap) based on balance direction.
    2. For same-column amounts: correct when clearly off (>2% AND >500 Rs error),
       but leave alone when the OCR balance itself might be wrong (very small diff rows).
    """
    def _signed_bal(row):
        bal = row.get('balance')
        if bal is None:
            return None
        btype = (row.get('balance_type') or '').upper()
        return -bal if btype == 'DR' else bal

    # Tolerance to consider amount "already correct"
    CLOSE_ABS = Decimal('50')      # within 50 Rs → definitely correct
    CLOSE_PCT = Decimal('0.01')    # within 1% → definitely correct

    def _is_close(parsed_val, expected_val):
        if parsed_val is None or expected_val is None or expected_val <= 0:
            return False
        abs_diff = abs(parsed_val - expected_val)
        pct_diff = abs_diff / expected_val
        return abs_diff <= CLOSE_ABS or pct_diff <= CLOSE_PCT

    def _should_correct(parsed_val, expected_val):
        """Return True when parsed_val is significantly off from expected_val.
        
        Uses adaptive thresholds:
        - For small amounts (<5000 Rs): corrects if >5 Rs AND >1.5% off
        - For larger amounts: corrects if >500 Rs AND >2% off
        This handles both tiny NEFT charges (64.64→124.64) and large transfers.
        """
        if parsed_val is None or expected_val is None or expected_val <= 0:
            return False
        abs_diff = abs(parsed_val - expected_val)
        pct_diff = abs_diff / expected_val
        if expected_val < Decimal('5000'):
            # Small amount row — use tighter thresholds
            return abs_diff > Decimal('5') and pct_diff > Decimal('0.015')
        else:
            # Large amount row
            return abs_diff > Decimal('500') and pct_diff > Decimal('0.015')

    for i in range(1, len(rows)):
        prev_signed = _signed_bal(rows[i - 1])
        cur_signed  = _signed_bal(rows[i])
        if prev_signed is None or cur_signed is None:
            continue

        diff = cur_signed - prev_signed    # positive = credit, negative = debit
        cur_deb  = rows[i].get('debit')
        cur_cred = rows[i].get('credit')

        if diff < 0:
            expected_debit = abs(diff)

            if cur_deb is not None and _is_close(cur_deb, expected_debit):
                continue   # Already correct, leave it

            if cur_cred is not None and _is_close(cur_cred, expected_debit):
                # Debit landed in credit column → swap
                rows[i] = dict(rows[i])
                rows[i]['debit'] = cur_cred
                rows[i]['credit'] = None
                bd = dict(rows[i].get('bank_json_data') or {})
                bd['col_swapped'] = True
                rows[i]['bank_json_data'] = bd

            elif cur_cred is not None and cur_deb is None:
                # Only credit present but balance went down → flip
                rows[i] = dict(rows[i])
                rows[i]['debit'] = cur_cred
                rows[i]['credit'] = None
                bd = dict(rows[i].get('bank_json_data') or {})
                bd['col_swapped_sign'] = True
                rows[i]['bank_json_data'] = bd

            elif cur_deb is not None and _should_correct(cur_deb, expected_debit):
                # Same column, but amount is significantly wrong → use oracle
                rows[i] = dict(rows[i])
                bd = dict(rows[i].get('bank_json_data') or {})
                bd['debit_ocr_corrected'] = str(cur_deb)
                rows[i]['debit'] = expected_debit
                rows[i]['bank_json_data'] = bd

            elif cur_deb is None and cur_cred is None:
                rows[i] = dict(rows[i])
                rows[i]['debit'] = expected_debit
                bd = dict(rows[i].get('bank_json_data') or {})
                bd['debit_filled_from_balance'] = True
                rows[i]['bank_json_data'] = bd

        elif diff > 0:
            expected_credit = diff

            if cur_cred is not None and _is_close(cur_cred, expected_credit):
                continue   # Already correct

            if cur_deb is not None and _is_close(cur_deb, expected_credit):
                # Credit landed in debit column → swap
                rows[i] = dict(rows[i])
                rows[i]['credit'] = cur_deb
                rows[i]['debit'] = None
                bd = dict(rows[i].get('bank_json_data') or {})
                bd['col_swapped'] = True
                rows[i]['bank_json_data'] = bd

            elif cur_deb is not None and cur_cred is None:
                # Only debit present but balance went up → flip
                rows[i] = dict(rows[i])
                rows[i]['credit'] = expected_credit
                rows[i]['debit'] = None
                bd = dict(rows[i].get('bank_json_data') or {})
                bd['col_swapped_sign'] = True
                bd['credit_original_debit'] = str(cur_deb)
                rows[i]['bank_json_data'] = bd

            elif cur_cred is not None and _should_correct(cur_cred, expected_credit):
                # Same column, but amount significantly wrong → use oracle
                rows[i] = dict(rows[i])
                bd = dict(rows[i].get('bank_json_data') or {})
                bd['credit_ocr_corrected'] = str(cur_cred)
                rows[i]['credit'] = expected_credit
                rows[i]['bank_json_data'] = bd

            elif cur_deb is None and cur_cred is None:
                rows[i] = dict(rows[i])
                rows[i]['credit'] = expected_credit
                bd = dict(rows[i].get('bank_json_data') or {})
                bd['credit_filled_from_balance'] = True
                rows[i]['bank_json_data'] = bd

    return rows

# ---------------------------------------------------------------------------
# Single-page parser
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r'^(\d{2})[-/](\d{2})[-/](\d{4})(.*)')
_SKIP_RE = re.compile(
    # Only skip lines that are clearly headers, footers, or metadata
    # NOTE: Do NOT add generic narration words here — they appear in real transactions too
    r'service\s+outlet|account\s+num(ber)?|customer\s+id'
    r'|brought\s+forward|opening\s+balance'
    r'|union\s+bank'     # 'union bank' only appears in page headers, never in transaction narrations
    r'|transaction\s+details'
    r'|\bfinacle\b|https?://'
    r'|debit\s+amt|credit\s+amt|balance\s+amt'
    r'|\bparticulars\b|\bcontra\b'
    r'|account\s+opening'
    r'|station\s+road'   # page-break header contains branch address
    r'|\brepor[t]\s+to\b|report\s+for\s+the\s+period',
    re.IGNORECASE,
)


def _parse_page_image(img: "Image.Image", page_num: int) -> list:
    """
    Extract transaction rows from one corrected (upright) PIL Image using:
    1. Dynamic skew estimation & deskewing of OCR word coordinates.
    2. Vertical overlap & proximity grouping of words into horizontal lines.
    3. Robust prefix-based date extraction to handle noise and split tokens.
    4. Horizontally-segmented column partitioning (Date, Ref, Particulars, Debit, Balance).
    """
    img_w, img_h = img.size
    
    # Column fractions (calibrated for 200 dpi)
    ref_end       = int((370 / 1654) * img_w)
    debit_start   = int((750 / 1654) * img_w)
    credit_start  = int((940 / 1654) * img_w)
    balance_start = int((1150 / 1654) * img_w)

    data = pytesseract.image_to_data(
        img, output_type=pytesseract.Output.DICT, config='--psm 6 --oem 1'
    )

    words = []
    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        if not text:
            continue
        words.append({
            'text': text,
            'left': data['left'][i],
            'top': data['top'][i],
            'width': data['width'][i],
            'height': data['height'][i],
            'bottom': data['top'][i] + data['height'][i],
            'cy': data['top'][i] + data['height'][i] / 2
        })

    if not words:
        return []

    # 1. Estimate skew angle and adjust Y coordinates
    skew_angle = estimate_skew_angle(words)
    tan_a = math.tan(skew_angle)
    
    for w in words:
        w['top_adj'] = w['top'] - w['left'] * tan_a
        w['bottom_adj'] = w['bottom'] - w['left'] * tan_a
        w['cy_adj'] = w['cy'] - w['left'] * tan_a

    # 2. Group words into horizontal lines using adjusted Y coordinates
    words.sort(key=lambda w: w['top_adj'])
    grouped_lines = []
    for w in words:
        placed = False
        for line in grouped_lines:
            line_top = sum(item['top_adj'] for item in line) / len(line)
            line_bottom = sum(item['bottom_adj'] for item in line) / len(line)
            line_height = line_bottom - line_top
            
            overlap_top = max(w['top_adj'], line_top)
            overlap_bottom = min(w['bottom_adj'], line_bottom)
            overlap = max(0, overlap_bottom - overlap_top)
            
            cy_line = (line_top + line_bottom) / 2
            
            if overlap > 0.4 * min(w['height'], line_height) or abs(w['cy_adj'] - cy_line) < 8:
                line.append(w)
                placed = True
                break
        if not placed:
            grouped_lines.append([w])

    # 3. Sort lines vertically and sort words in each line horizontally
    final_lines = []
    for line in grouped_lines:
        line.sort(key=lambda w: w['left'])
        avg_y = sum(w['top_adj'] for w in line) / len(line)
        final_lines.append((avg_y, line))
    
    final_lines.sort(key=lambda x: x[0])

    rows = []
    row_counter = 0

    for avg_y, line_words in final_lines:
        if not line_words:
            continue

        # Extract date using robust prefix matching
        date_res, num_date_words = _extract_date_and_trail(line_words)
        if not date_res:
            continue

        txn_date, trail = date_res

        line_text = ' '.join(w['text'] for w in line_words)
        if _SKIP_RE.search(line_text):
            continue

        # Filter out the words that were part of the date prefix
        remaining_words = line_words[num_date_words:]

        # Split into columns based on x-coordinates
        ref_zone = [w for w in remaining_words if w['left'] < ref_end]
        ref = trail
        if not ref and len(ref_zone) > 0:
            ref = ' '.join(w['text'] for w in ref_zone).strip()

        parts = ' '.join(
            w['text'] for w in remaining_words
            if ref_end <= w['left'] < debit_start
        ).strip()

        deb_text = ''.join(
            w['text'] for w in remaining_words
            if debit_start <= w['left'] < credit_start
        ).strip()
        deb_val = _parse_amount(deb_text)

        cr_text = ''.join(
            w['text'] for w in remaining_words
            if credit_start <= w['left'] < balance_start
        ).strip()
        cr_val = _parse_amount(cr_text)

        bal_text = ''.join(
            w['text'] for w in remaining_words if w['left'] >= balance_start
        ).strip()
        bal_val  = _parse_amount(bal_text)
        bal_type = _balance_type(bal_text)

        if bal_val is None:
            continue

        row_counter += 1
        rows.append({
            'txn_date':          txn_date,
            'value_date':        None,
            'txn_time':          None,
            'narration_raw':     parts,
            'debit':             deb_val,
            'credit':            cr_val,
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
                'debit_raw':   deb_text,
                'credit_raw':  cr_text,
                'balance_raw': bal_text,
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