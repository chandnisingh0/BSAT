#!/usr/bin/env python3
"""
Fast, accurate extractor for scanned Union Bank of India statement PDFs.

WHY THIS IS NEEDED
  The PDF has NO text layer. Every page is a 600-DPI fax-compressed
  (CCITT G4) image, scanned UPSIDE-DOWN (rotated 180 deg). So the only
  way to get the data out is OCR.

SPEED (the only real levers for OCR throughput)
  1. PARALLELISM - one page per CPU core (multiprocessing). Runtime
                   scales ~linearly with cores; this dominates everything.
  2. 300 DPI     - sweet spot for these dense Indian-format numbers.
  3. IN-MEMORY   - PyMuPDF renders straight to a pixmap, no temp files.

ACCURACY (financial data must be right)
  Plain OCR confuses digits in long numbers (S<->5, O<->0, B<->8) and
  only ~57% of rows reconcile. So each page gets TWO OCR passes:
    * text pass     - normal OCR for Date / Ref / Particulars
    * numeric pass  - OCR with a DIGIT WHITELIST for the money columns
                      (->~92% of rows reconcile automatically)
  Debit vs Credit is derived from the change in running balance, and
  every row is CROSS-CHECKED: |balance change| must equal the booked
  amount. Rows that don't reconcile are flagged 'CHECK' so you can
  eyeball them against the raw-text dump. Nothing is silently guessed.

OUTPUT
  <out>.txt  full OCR text, page-ordered (audit trail)
  <out>.csv  transactions: date, ref, particulars, debit, credit,
             balance, dr_cr, ocr_amount, check, confidence

USAGE
  pip install pymupdf pytesseract pillow      # plus system 'tesseract'
  python extract_bank_statement.py statement.pdf
  python extract_bank_statement.py statement.pdf -o out -j 8 --pages 1-20
"""
import argparse, csv, multiprocessing as mp, os, re, sys, time
from decimal import Decimal, InvalidOperation
import fitz                       # PyMuPDF
import pytesseract
from pytesseract import Output
from PIL import Image

# ---- per-worker globals (configured once per process) -------------------
_DOC = None
_DPI = 300
_ROT = 180
_CFG_TEXT = "--psm 6"
_CFG_NUM  = "--psm 6 -c tessedit_char_whitelist=0123456789,.CRD"

DATE_RE = re.compile(r"\d{2}-\d{2}-\d{4}")
NUM_RE  = re.compile(r"^[\d,]+\.\d{2}(CR|DR)?$")
AMT_RE  = re.compile(r"^[\d,]+\.\d{2}$")
# balance/amount token that OCR mangled with stray letters, e.g. 2,85,73,496.i6CR
DIRTY_NUM_RE = re.compile(r"^[\d,]+\.\w{2}[A-Za-z]{0,3}$")

# fuzzy date: OCR turns 0->O/C, 1->l/I, 5->S, 8->B, '-'->'~'; recover safely
_DCONF = str.maketrans("OoCcQlIiSsBbgG", "00000111155886")
_FUZZY_DATE_RE = re.compile(r"[\dOoCcQlIiSsBbgG]{2}[-~][\dOoCcQlIiSsBbgG]{2}[-~][\dOoCcQlIiSsBbgG]{4}")


def fuzzy_date(token):
    """Return a clean dd-mm-yyyy if token is a plausibly-OCR'd date, else ''."""
    m = _FUZZY_DATE_RE.match(token)
    if not m:
        return ""
    s = m.group(0).replace("~", "-").translate(_DCONF)
    mm = re.match(r"(\d{2})-(\d{2})-(\d{4})", s)
    if not mm:
        return ""
    d, mo, y = int(mm[1]), int(mm[2]), int(mm[3])
    if 1 <= d <= 31 and 1 <= mo <= 12 and 2010 <= y <= 2030:
        return s
    return ""


def _init(pdf, dpi, rot):
    global _DOC, _DPI, _ROT
    _DOC = fitz.open(pdf)
    _DPI, _ROT = dpi, rot


def _render(page_index):
    pix = _DOC[page_index].get_pixmap(dpi=_DPI, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
    return img.rotate(_ROT) if _ROT else img


def _lines_from_data(img, cfg):
    """OCR -> list of visual lines. Each line: dict(top, words=[(x,txt,conf)])."""
    d = pytesseract.image_to_data(img, config=cfg, output_type=Output.DICT)
    lines = {}
    for i, txt in enumerate(d["text"]):
        if not txt.strip():
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        lines.setdefault(key, {"tops": [], "words": []})
        lines[key]["tops"].append(d["top"][i])
        try:
            conf = float(d["conf"][i])
        except ValueError:
            conf = -1.0
        lines[key]["words"].append((d["left"][i], txt.strip(), conf))
    out = []
    for v in lines.values():
        v["words"].sort()
        out.append({"top": sorted(v["tops"])[len(v["tops"]) // 2],  # median top
                    "words": v["words"]})
    out.sort(key=lambda l: l["top"])
    return out


SKIP_KW = ("Brought", "Forward", "Opening", "Tran", "Ref", "Balance",
           "Report", "UNION", "BANK", "Page", "Details", "Particulars",
           "REP", "NIRMAL", "Account", "Service", "Manager", "Signature",
           "finacle", "https", "Date")


def _ocr_page(page_index):
    """Numeric pass is the backbone; text fields are attached by line-y."""
    img = _render(page_index)
    text_lines = _lines_from_data(img, _CFG_TEXT)
    num_lines  = _lines_from_data(img, _CFG_NUM)

    raw = "\n".join(" ".join(w for _, w, _ in ln["words"]) for ln in text_lines)

    # tops of header / brought-forward / footer lines we must NOT treat as txns
    skip_tops = [tl["top"] for tl in text_lines
                 if any(kw in (w for _, w, _ in tl["words"]) for kw in SKIP_KW)]

    def near_skip(top):
        return any(abs(top - s) <= 18 for s in skip_tops)

    rows = []
    for nl in num_lines:
        if near_skip(nl["top"]):
            continue
        nums  = [w for _, w, _ in nl["words"] if NUM_RE.match(w) and "." in w]
        confs = [c for _, w, c in nl["words"]
                 if NUM_RE.match(w) and "." in w and c >= 0]
        if len(nums) != 2:           # a real txn line shows exactly amount+balance
            continue
        bal = nums[-1]
        drcr = "CR" if bal.endswith("CR") else ("DR" if bal.endswith("DR") else None)
        if drcr is None:             # balance must carry CR/DR; else it's not a txn row
            continue
        amount, balance = nums[-2], bal[:-2]
        conf = sum(confs) / len(confs) if confs else 0.0

        # attach date / ref / particulars from the nearest text line
        date = ref = particulars = ""
        tl = min(text_lines, key=lambda t: abs(t["top"] - nl["top"]),
                 default=None)
        if tl and abs(tl["top"] - nl["top"]) <= 30:
            toks = [w.strip(";|.,") for w in (t for _, t, _ in tl["words"])]
            toks = [t for t in toks if t]
            # find the date anywhere in the first few tokens (with OCR recovery)
            di = dval = None
            for k, t in enumerate(toks[:3]):
                fd = fuzzy_date(t)
                if fd:
                    di, dval = k, fd
                    break
            if di is not None:
                date = dval
                # strip the (possibly fuzzy) date prefix off the token
                rest0 = _FUZZY_DATE_RE.sub("", toks[di], count=1)
                seq = ([rest0] if rest0 else []) + toks[di + 1:]
                if seq:
                    ref = seq[0]
                    body = seq[1:]
                else:
                    body = []
            else:
                body = toks
            # particulars = words that are not numeric / balance-like (even w/ OCR noise)
            particulars = " ".join(
                t for t in body
                if not NUM_RE.match(t) and not DIRTY_NUM_RE.match(t))
        rows.append({
            "page": page_index + 1, "top": nl["top"], "date": date,
            "ref_num": ref, "particulars": particulars,
            "amount": amount, "balance": balance, "dr_cr": drcr,
            "confidence": round(conf, 1),
        })
    return page_index, raw, rows


def detect_rotation(pdf):
    doc = fitz.open(pdf)
    pix = doc[0].get_pixmap(dpi=200, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
    try:
        m = re.search(r"Rotate:\s*(\d+)", pytesseract.image_to_osd(img))
        return int(m.group(1)) if m else 180
    except Exception:
        return 180


def _dec(s):
    try:
        return Decimal(s.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def resolve_debit_credit(rows):
    """rows already in (page, top) order. Fill debit/credit + check."""
    out = []
    prev = None
    for r in rows:
        amt = _dec(r["amount"]); bal = _dec(r["balance"])
        debit = credit = ""
        check = "no_amount" if (amt is None or bal is None or not r["dr_cr"]) else ""
        signed = None
        if bal is not None and r["dr_cr"]:
            signed = bal if r["dr_cr"] == "CR" else -bal
        if signed is not None and amt is not None:
            if prev is None:
                check = "first"
            else:
                delta = signed - prev
                booked = None
                if delta > 0:
                    credit = f"{delta:.2f}"; booked = delta
                elif delta < 0:
                    debit = f"{-delta:.2f}"; booked = -delta
                if booked is not None:
                    check = "ok" if abs(booked - amt) <= Decimal("0.05") else "CHECK"
                elif delta == 0:
                    check = "zero_delta"
            prev = signed
        out.append({
            "page": r["page"], "date": r["date"], "ref_num": r["ref_num"],
            "particulars": r["particulars"], "debit": debit, "credit": credit,
            "balance": f"{bal:.2f}" if bal is not None else "",
            "dr_cr": r["dr_cr"] or "", "ocr_amount": f"{amt:.2f}" if amt is not None else "",
            "check": check, "confidence": r["confidence"],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--pages", default=None, help="1-based range, e.g. 1-20")
    args = ap.parse_args()

    base = args.out or os.path.splitext(os.path.basename(args.pdf))[0]
    txt_path, csv_path = base + ".txt", base + ".csv"

    doc = fitz.open(args.pdf); n = doc.page_count; doc.close()
    if args.pages:
        a, b = (int(x) for x in args.pages.split("-"))
        idxs = list(range(a - 1, b))
    else:
        idxs = list(range(n))

    rot = detect_rotation(args.pdf)
    print(f"[i] {n} pages | OCR {len(idxs)} | {args.jobs} workers | "
          f"{args.dpi} DPI | rotate {rot} | 2 passes/page", file=sys.stderr)

    t0 = time.time()
    raw_by_page, rows_by_page = {}, {}
    with mp.Pool(args.jobs, initializer=_init, initargs=(args.pdf, args.dpi, rot)) as pool:
        for done, (idx, raw, rows) in enumerate(
                pool.imap_unordered(_ocr_page, idxs, chunksize=1), 1):
            raw_by_page[idx] = raw
            rows_by_page[idx] = rows
            if done % 5 == 0 or done == len(idxs):
                el = time.time() - t0
                print(f"[i] {done}/{len(idxs)}  {el:.0f}s ({el/done:.1f}s/pg)", file=sys.stderr)

    with open(txt_path, "w", encoding="utf-8") as f:
        for i in sorted(raw_by_page):
            f.write(f"\n----- PAGE {i+1} -----\n{raw_by_page[i]}\n")

    ordered = []
    for i in sorted(rows_by_page):
        ordered.extend(sorted(rows_by_page[i], key=lambda r: r["top"]))
    final = resolve_debit_credit(ordered)

    cols = ["page", "date", "ref_num", "particulars", "debit", "credit",
            "balance", "dr_cr", "ocr_amount", "check", "confidence"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(final)

    el = time.time() - t0
    ok = sum(1 for r in final if r["check"] == "ok")
    flagged = sum(1 for r in final if r["check"] == "CHECK")
    print(f"[done] {len(final)} txns in {el:.0f}s ({el/max(len(idxs),1):.1f}s/pg). "
          f"{ok} reconciled, {flagged} flagged CHECK.", file=sys.stderr)
    print(f"[out] {txt_path}\n[out] {csv_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
