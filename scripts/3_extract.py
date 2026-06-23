#!/usr/bin/env python3
"""
3_extract.py — extract a PDF to Markdown with Docling.

The first ingestion step, split out so you can open and inspect the Markdown
before chunking. Docling parses the PDF's structure (headings, paragraphs,
tables) and writes clean Markdown.

Usage:  python scripts/3_extract.py document.pdf [output.md]
        (default output: the PDF path with a .md extension)
"""

import os
import sys

from docling.document_converter import DocumentConverter


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit("Usage: python scripts/3_extract.py document.pdf [output.md]")
    pdf = sys.argv[1]
    if not os.path.exists(pdf):
        sys.exit("PDF not found: " + pdf)
    md_path = sys.argv[2] if len(sys.argv) == 3 else os.path.splitext(pdf)[0] + ".md"

    print(f"Extracting {pdf} -> {md_path}")
    document = DocumentConverter().convert(pdf).document
    markdown = document.export_to_markdown()
    with open(md_path, "w") as f:
        f.write(markdown)
    print(f"Wrote {len(markdown):,} characters to {md_path}")


if __name__ == "__main__":
    main()
