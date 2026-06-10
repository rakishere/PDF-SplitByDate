#!/usr/bin/env python3
"""
Split a PDF into one new PDF per page (written to the same folder), extracting
LABELED dates from each page and optionally naming each file by a chosen field.

TABLE-AWARE + OCR FALLBACK.
  * Pages WITH a text layer are read with pdfplumber (fast, exact).
  * Pages with NO text layer (scanned images) are rendered and OCR'd with
    Tesseract, then run through the SAME column-matching logic.
Either way, a label is matched to the date in *its own column* (value directly
below or to the right), so this handles:

    Period Start:    Period End:     Payment Date:
    30-MAR-2026      12-APR-2026      16-APR-2026

Dependencies:
    pip install pypdf pdfplumber pymupdf pytesseract pillow
    Plus the Tesseract engine:
      Windows: install "Tesseract at UB Mannheim", then either add it to PATH
               or pass --tesseract "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
      macOS:   brew install tesseract
      Linux:   apt-get install tesseract-ocr

Usage:
    python split_pdf.py <pdf> [--field LABEL ...] [--name-by FIELD]
        [--ocr {auto,always,never}] [--dpi N] [--tesseract PATH]
        [--debug] [--quiet]

Examples:
    python split_pdf.py statement.pdf --field "Payment Date" --name-by "Payment Date"
    python split_pdf.py statement.pdf --field "Payment Date" --debug
    python split_pdf.py statement.pdf --field "Payment Date" \
        --tesseract "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
"""

import argparse
import io
import re
from datetime import datetime
from pathlib import Path
from statistics import median

import pdfplumber
from pypdf import PdfReader, PdfWriter

# OCR libs are optional at import time; we only need them when a page is scanned.
try:
    import fitz  # PyMuPDF
    import pytesseract
    from PIL import Image
    _OCR_AVAILABLE = True
except Exception:
    _OCR_AVAILABLE = False


# --- Date grammar ------------------------------------------------------------

MONTHS = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
    "|january|february|march|april|may|june|july|august"
    "|september|october|november|december"
)

DATE_SPECS = [
    (rf"\d{{1,2}}[-/](?:{MONTHS})[-/]\d{{2,4}}",
     ["%d-%b-%Y", "%d/%b/%Y", "%d-%B-%Y", "%d/%B/%Y", "%d-%b-%y", "%d/%b/%y"]),
    (r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",
     ["%Y-%m-%d", "%Y/%m/%d"]),
    (rf"(?:{MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}}",
     ["%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"]),
    (rf"\d{{1,2}}\s+(?:{MONTHS})\.?\s+\d{{4}}",
     ["%d %B %Y", "%d %b %Y"]),
    (r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}",
     ["%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y", "%m/%d/%y", "%m-%d-%y", "%m.%d.%y"]),
]

ANY_DATE = re.compile("|".join(f"(?:{p})" for p, _ in DATE_SPECS), re.I)


def parse_date(token: str):
    token = token.strip().rstrip(".,;")
    cleaned = token.replace(".", "")
    for _, formats in DATE_SPECS:
        for fmt in formats:
            for candidate in (token, cleaned):
                try:
                    return datetime.strptime(candidate, fmt)
                except ValueError:
                    continue
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# --- Word sources ------------------------------------------------------------
# Both sources yield the same shape: {"text", "x0", "x1", "top", "bottom"}.

def _words_from_text(page) -> list[dict]:
    ws = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    return [{"text": w["text"], "x0": w["x0"], "x1": w["x1"],
             "top": w["top"], "bottom": w["bottom"]} for w in ws]


def _words_from_ocr(pdf_path, page_index, dpi=300, min_conf=30) -> list[dict]:
    if not _OCR_AVAILABLE:
        raise RuntimeError(
            "OCR needed but libraries missing. Install: "
            "pip install pymupdf pytesseract pillow, plus the Tesseract engine."
        )
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    finally:
        doc.close()

    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    words = []
    for i, txt in enumerate(data["text"]):
        if not txt or not txt.strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1
        if conf < min_conf:
            continue
        left, top = data["left"][i], data["top"][i]
        words.append({
            "text": txt, "x0": float(left), "x1": float(left + data["width"][i]),
            "top": float(top), "bottom": float(top + data["height"][i]),
        })
    return words


# --- Positional (table-aware) matching ---------------------------------------

def _row_tol(words) -> float:
    heights = [w["bottom"] - w["top"] for w in words if w["bottom"] > w["top"]]
    h = median(heights) if heights else 6.0
    return max(2.0, 0.5 * h)


def _cluster_rows(words, tol):
    rows = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        for row in rows:
            if abs(row["top"] - w["top"]) <= tol:
                row["words"].append(w)
                break
        else:
            rows.append({"top": w["top"], "words": [w]})
    for row in rows:
        row["words"].sort(key=lambda w: w["x0"])
    rows.sort(key=lambda r: r["top"])
    return rows


def _find_label_cell(rows, label):
    target = _norm(label)
    for row in rows:
        ws = row["words"]
        for i in range(len(ws)):
            acc = ""
            for j in range(i, len(ws)):
                acc += _norm(ws[j]["text"])
                if acc == target:
                    span = ws[i:j + 1]
                    return {"x0": min(w["x0"] for w in span),
                            "x1": max(w["x1"] for w in span),
                            "top": row["top"]}
                if len(acc) > len(target):
                    break
    return None


def _find_date_cells(rows):
    cells = []
    for row in rows:
        ws = row["words"]
        i = 0
        while i < len(ws):
            matched = False
            for size in (3, 2, 1):
                if i + size <= len(ws):
                    chunk = ws[i:i + size]
                    dt = parse_date(" ".join(w["text"] for w in chunk))
                    if dt:
                        cells.append({
                            "dt": dt, "text": " ".join(w["text"] for w in chunk),
                            "x0": min(w["x0"] for w in chunk),
                            "x1": max(w["x1"] for w in chunk),
                            "top": row["top"]})
                        i += size
                        matched = True
                        break
            if not matched:
                i += 1
    return cells


def _match_field(label_cell, date_cells, tol):
    if not label_cell or not date_cells:
        return None
    lcx = (label_cell["x0"] + label_cell["x1"]) / 2
    best, best_score = None, float("inf")
    for c in date_cells:
        ccx = (c["x0"] + c["x1"]) / 2
        below = c["top"] > label_cell["top"] + tol
        same_row_right = (abs(c["top"] - label_cell["top"]) <= tol
                          and c["x0"] >= label_cell["x0"] - 2)
        if not (below or same_row_right):
            continue
        score = abs(lcx - ccx) + 0.5 * abs(c["top"] - label_cell["top"])
        if score < best_score:
            best, best_score = c, score
    return best


def fields_from_words(words, fields):
    tol = _row_tol(words)
    rows = _cluster_rows(words, tol)
    date_cells = _find_date_cells(rows)
    out = {}
    for f in fields:
        lc = _find_label_cell(rows, f)
        out[f] = _match_field(lc, date_cells, tol) if lc else None
    return out


# --- Helpers -----------------------------------------------------------------

def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").lower()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


# --- Splitting ---------------------------------------------------------------

def split_pdf(pdf_path, fields=None, name_by=None, ocr="auto", dpi=300,
              debug=False, verbose=True):
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"No such file: {pdf_path}")

    fields = fields or []
    if name_by and not any(name_by.lower() == f.lower() for f in fields):
        raise ValueError(f"--name-by {name_by!r} must match one of --field {fields!r}")

    reader = PdfReader(str(pdf_path))
    stem = pdf_path.stem
    folder = pdf_path.parent
    pad = len(str(len(reader.pages)))

    results, used_names = [], {}

    with pdfplumber.open(str(pdf_path)) as plumber:
        for i, (page_w, page_r) in enumerate(zip(plumber.pages, reader.pages), start=1):
            source = "text"
            words = _words_from_text(page_w) if fields else []

            if fields and ocr == "always":
                words, source = _words_from_ocr(pdf_path, i - 1, dpi), "ocr"
            elif fields and not words and ocr != "never":
                words, source = _words_from_ocr(pdf_path, i - 1, dpi), "ocr"

            field_cells = fields_from_words(words, fields) if fields else {}

            if debug:
                print(f"\n----- PAGE {i}: source={source}, {len(words)} words -----")
                if words:
                    print("  ".join(f"{w['text']}@{w['x0']:.0f}" for w in words)[:1200])
                else:
                    print("(no words found)")
                print("----- end -----")

            chosen = None
            if name_by:
                for f, cell in field_cells.items():
                    if f.lower() == name_by.lower() and cell:
                        chosen = cell["dt"]
                        break

            base = (f"{stem}_{_slug(name_by)}_{_iso(chosen)}" if chosen
                    else f"{stem}_page_{i:0{pad}d}")
            n = used_names.get(base, 0)
            used_names[base] = n + 1
            out_name = base if n == 0 else f"{base}_{n + 1}"
            out_path = folder / f"{out_name}.pdf"

            writer = PdfWriter()
            writer.add_page(page_r)
            with open(out_path, "wb") as fo:
                writer.write(fo)

            info = {"page": i, "path": out_path, "source": source,
                    "fields": {f: (_iso(c["dt"]) if c else None)
                               for f, c in field_cells.items()}}
            results.append(info)

            if verbose:
                if fields:
                    parts = [f"{f}={info['fields'][f] or 'NOT FOUND'}" for f in fields]
                    summary = "  ".join(parts)
                else:
                    summary = "(no --field given)"
                print(f"Page {i} [{source}]: {summary}  ->  {out_path.name}")
                if debug:
                    for f, cell in field_cells.items():
                        if cell:
                            print(f"        {f!r} -> {cell['text']!r} @x0={cell['x0']:.0f}")

    return results


def main():
    p = argparse.ArgumentParser(description="Table-aware PDF splitter with labeled-date extraction and OCR fallback.")
    p.add_argument("pdf", help="Path to the source PDF.")
    p.add_argument("--field", action="append", default=[], metavar="LABEL",
                   help="A date label to extract (repeatable).")
    p.add_argument("--name-by", metavar="FIELD",
                   help="Name each file by this field's date (must be one of --field).")
    p.add_argument("--ocr", choices=["auto", "always", "never"], default="auto",
                   help="auto = OCR only pages with no text layer (default); "
                        "always = OCR every page; never = disable OCR.")
    p.add_argument("--dpi", type=int, default=300, help="Render DPI for OCR (default 300).")
    p.add_argument("--tesseract", metavar="PATH",
                   help="Path to tesseract.exe (Windows, if not on PATH).")
    p.add_argument("--debug", action="store_true", help="Print positioned words + matches.")
    p.add_argument("--quiet", action="store_true", help="Suppress per-page logging.")
    args = p.parse_args()

    if args.tesseract and _OCR_AVAILABLE:
        pytesseract.pytesseract.tesseract_cmd = args.tesseract

    results = split_pdf(args.pdf, fields=args.field, name_by=args.name_by,
                        ocr=args.ocr, dpi=args.dpi, debug=args.debug,
                        verbose=not args.quiet)

    if args.field:
        found = sum(1 for r in results if any(r["fields"].values()))
        ocr_pages = sum(1 for r in results if r["source"] == "ocr")
        print(f"\nDone. {len(results)} page(s) written, {found} matched at least "
              f"one field ({ocr_pages} via OCR).")
    else:
        print(f"\nDone. {len(results)} page(s) written.")


if __name__ == "__main__":
    main()
