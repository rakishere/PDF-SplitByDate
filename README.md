# PDF Splitter with Labeled-Date Extraction

Split a multi-page PDF into one PDF per page, and name each output file by a
date read off that page — even when the dates live in a table or the page is a
scanned image.

The tool is **table-aware** (it matches a label like `Payment Date:` to the
value in *its own column*, not just the nearest date in reading order) and has
an **automatic OCR fallback** for scanned/image-only pages.

---

## What it does

Given `statement.pdf`, it writes one file per page into the same folder. With
`--field` / `--name-by`, files are named by the chosen date, e.g.:

```
statement_payment_date_2026-04-16.pdf
statement_payment_date_2026-01-20.pdf
```

Pages where the chosen field can't be found fall back to a page-number name:

```
statement_page_06.pdf
```

It handles three layouts automatically:

- **Inline** — `Payment Date: 16-APR-2026` on one line.
- **Tables** — labels in a header row, values aligned in columns below.
- **Scans** — image-only pages with no text layer, read via OCR.

---

## Requirements

### Python packages

```bash
pip install pypdf pdfplumber pymupdf pytesseract pillow
```

| Package      | Used for                                         |
|--------------|--------------------------------------------------|
| `pypdf`      | Writing the split single-page PDFs               |
| `pdfplumber` | Reading text **with x/y coordinates** (columns)  |
| `pymupdf`    | Rendering scanned pages to images for OCR        |
| `pytesseract`| Wrapper that calls the Tesseract OCR engine      |
| `pillow`     | Image handling for OCR                            |

### Tesseract OCR engine (only needed for scanned pages)

`pytesseract` is just a wrapper — it shells out to the actual `tesseract`
program, which is installed separately.

- **Windows:** install from the official UB Mannheim build —
  <https://github.com/UB-Mannheim/tesseract/wiki>
  or `winget install -e --id UB-Mannheim.TesseractOCR`
  (default location: `C:\Program Files\Tesseract-OCR`)
- **macOS:** `brew install tesseract`
- **Linux:** `apt-get install tesseract-ocr`

Verify it's installed and visible:

```bash
tesseract --version
```

If that prints a version, you're set. If not, see **Troubleshooting** below.

> If your PDFs all have a real text layer (not scans), you can skip Tesseract
> and the OCR packages entirely — the tool only invokes OCR when a page has no
> extractable text.

---

## Usage

```bash
python split_pdf.py <pdf> [options]
```

### Common examples

Split every page and name files by the Payment Date:

```bash
python split_pdf.py statement.pdf --field "Payment Date" --name-by "Payment Date"
```

Extract several labeled dates, name by one of them:

```bash
python split_pdf.py statement.pdf \
    --field "Period Start" --field "Period End" --field "Payment Date" \
    --name-by "Payment Date"
```

Inspect how the PDF's text is laid out (use this first when a field won't match):

```bash
python split_pdf.py statement.pdf --field "Payment Date" --debug
```

Point at Tesseract explicitly (Windows, if it's not on your PATH):

```bash
python split_pdf.py statement.pdf --field "Payment Date" \
    --tesseract "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

Plain split with no date logic (just one file per page):

```bash
python split_pdf.py statement.pdf
```

---

## Options

| Flag           | Default | Description                                                                 |
|----------------|---------|-----------------------------------------------------------------------------|
| `pdf`          | —       | Path to the source PDF (required).                                          |
| `--field`      | —       | A date label to extract. Repeatable; pass once per label.                  |
| `--name-by`    | —       | Name each file by this field's date. Must match one of the `--field` labels. |
| `--ocr`        | `auto`  | `auto` = OCR only pages with no text; `always` = OCR every page; `never` = disable OCR. |
| `--dpi`        | `300`   | Render resolution for OCR. Try `400` if OCR misreads characters.           |
| `--tesseract`  | —       | Full path to `tesseract.exe` (Windows, if not on PATH).                    |
| `--debug`      | off     | Print each page's positioned words and how each field matched.             |
| `--quiet`      | off     | Suppress the per-page log.                                                 |

---

## How it works

1. **Read each page with coordinates.** `pdfplumber` returns every word with its
   x/y position. If a page has no text (a scan), the page is rendered to an
   image with `pymupdf` and OCR'd with Tesseract, producing the same
   word-with-position data.
2. **Find the label.** Consecutive words are matched to your `--field` label
   (whitespace-flexible, so `Payment Date` also matches `Payment  Date`).
3. **Find the dates.** Every value that parses as a date is located, with its
   column position.
4. **Match by column.** Each label is paired with the date in its own
   column — the value directly below it, or to its right on the same row —
   scored by horizontal alignment. This is what makes `Payment Date` pick its
   own value instead of a neighboring column's.
5. **Split and name.** Each page is written as a standalone PDF, named by the
   chosen field's date.

### Supported date formats

```
30-MAR-2026     16/APR/2026          (day, named month, year)
2026-04-16      2026/04/16           (ISO)
April 16, 2026  Apr 16 2026          (month name first)
16 April 2026   16 Apr 2026          (day first)
04/16/2026      4-16-26   04.16.26   (numeric US month/day/year)
```

> **Ambiguity note:** purely numeric `04/16/2026` is read as US month/day.
> The named-month formats (`16-APR-2026`) are unambiguous and preferred.

### Output naming

```
<source-stem>_<field>_<YYYY-MM-DD>.pdf     when the field is found
<source-stem>_page_<NN>.pdf                fallback when it isn't
```

Collisions (two pages sharing a date) get a numeric suffix: `_2`, `_3`, …

---

## Troubleshooting

**`FileNotFoundError: [WinError 2]` from `get_tesseract_version`**
The Tesseract engine isn't installed or isn't on your PATH. Run
`tesseract --version`; if it fails, install Tesseract (see Requirements) and
either open a **new** terminal (PATH changes don't apply to already-open
terminals) or pass `--tesseract "C:\Program Files\Tesseract-OCR\tesseract.exe"`.
The path must point to the `.exe` file, not the folder.

**A field reports `NOT FOUND` on a text page**
Run with `--debug` and look at the printed words. Common causes: the label
text differs from what you passed (extra spaces, a different colon, wrapped
across lines), or the value sits too far from the label. The debug view shows
exactly how the PDF renders the label so you can adjust.

**A field reports `NOT FOUND` and the page shows `source=ocr` with no words**
The scan is too low-quality for OCR to read. Try a higher `--dpi` (e.g. 400),
and make sure the source scan is reasonably sharp and straight.

**Dates come out wrong on scanned pages**
OCR can misread characters on imperfect scans (e.g. a `1` read as a `7`).
Raise `--dpi`, and spot-check output against the source before trusting it for
large batches. Text-layer pages are exact; only OCR'd pages carry this risk.

**Output is slow**
OCR rasterizes and recognizes each scanned page, which is much slower than
reading a text layer. Pages that already have text are fast; only scans incur
the OCR cost.

---

## Notes & limitations

- Text-layer extraction is exact. OCR is best-effort and depends on scan
  quality — verify before relying on it for high-stakes naming.
- The row-grouping tolerance adapts to font size automatically, but very dense
  or unusually spaced tables may need tuning (see `_row_tol` / `_cluster_rows`
  in the source).
- Encrypted/password-protected PDFs must be unlocked before processing.
