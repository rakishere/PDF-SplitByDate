#!/usr/bin/env python3
"""Split a PDF into one new PDF per page, written to the same folder."""

import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def split_pdf(pdf_path: str | Path) -> list[Path]:
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"No such file: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    stem = pdf_path.stem            # filename without extension
    folder = pdf_path.parent        # same folder as the source
    pad = len(str(len(reader.pages)))  # zero-pad page numbers, e.g. 01, 02

    outputs: list[Path] = []
    for i, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)

        out_path = folder / f"{stem}_page_{i:0{pad}d}.pdf"
        with open(out_path, "wb") as f:
            writer.write(f)

        outputs.append(out_path)
        print(f"Wrote {out_path}")

    return outputs


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python split_pdf.py <path-to-pdf>")
        sys.exit(1)

    pages = split_pdf(sys.argv[1])
    print(f"\nDone. {len(pages)} page(s) written.")
