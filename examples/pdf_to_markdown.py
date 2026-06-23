#!/usr/bin/env python3
"""
pdf_to_markdown.py
===========================================================================
Convert a PDF to Markdown with Docling. That's all this script does -
chunking, the Db2 table, and ingestion happen in the notebook
(chunk_and_ingest.ipynb).

Requirements:  docling

How to run:
    python3 pdf_to_markdown.py
===========================================================================
"""

import os

from docling.document_converter import DocumentConverter

PDF_PATH = "LLM_Integration_Doc.pdf"
MD_PATH = os.path.splitext(PDF_PATH)[0] + ".md"   # -> LLM_Integration_Doc.md


def main():
    if not os.path.exists(PDF_PATH):
        raise SystemExit(f"PDF not found: {PDF_PATH}")

    print(f"Converting '{PDF_PATH}' to Markdown with Docling ...")
    document = DocumentConverter().convert(PDF_PATH).document
    markdown = document.export_to_markdown()

    with open(MD_PATH, "w") as f:
        f.write(markdown)

    print(f"  -> wrote '{MD_PATH}' ({len(markdown):,} characters)")


if __name__ == "__main__":
    main()
