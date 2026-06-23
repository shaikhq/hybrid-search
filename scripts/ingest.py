#!/usr/bin/env python3
"""
ingest.py — turn one PDF into a hybrid-search corpus in IBM Db2.

What it does, in order:
  1. Extract  the PDF to Markdown with Docling.
  2. Chunk    the Markdown with Docling's HybridChunker (token-aware).
  3. Load     the chunks into a Db2 table (chunk_id, chunk_text).
  4. Index    the text for keyword search (Db2 Text Search, OpenSearch-backed).
  5. Register a watsonx.ai embedding model in Db2.
  6. Embed    each chunk into a VECTOR column (in-database TO_EMBEDDING).

Usage:  python ingest.py path/to/document.pdf
Config: read from .env (Db2 connection + watsonx.ai credentials).
Run as the Db2 instance owner — step 4 uses the db2ts command.
Run ./cleanup.sh first for a clean slate.
"""

import getpass
import os
import subprocess
import sys
import time

import ibm_db
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from transformers import AutoTokenizer

# --- Read settings from .env (repo root or current directory) ----------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _env in (os.path.join(_ROOT, ".env"), ".env"):
    if os.path.exists(_env):
        for _line in open(_env):
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

def setting(name, default=None):
    return os.environ.get(name, default)

DATABASE   = setting("DB2_DATABASE", "sample")
HOST       = setting("DB2_HOST", "localhost")
PORT       = setting("DB2_PORT", "50000")
USER       = setting("DB2_USER", "db2inst1")
PASSWORD   = setting("DB2_PASSWORD")
SCHEMA     = setting("DB2_SCHEMA", "myschema")
TABLE      = setting("DB2_TABLE", "chunks")
INDEX      = TABLE + "_text_idx"
MODEL      = SCHEMA + "." + TABLE + "_embed"
TABLE_FULL = SCHEMA + "." + TABLE

TOKENIZER  = setting("TOKENIZER_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
MAX_TOKENS = int(setting("MAX_TOKENS", "256"))
DIM        = int(setting("VECTOR_DIM", "384"))
OS_PORT    = int(setting("OPENSEARCH_PORT", "9200"))
OWNER      = setting("DB2_INSTANCE_OWNER", "db2inst1")

WX_URL     = setting("WATSONX_URL")
WX_KEY     = setting("WATSONX_APIKEY")
WX_PROJECT = setting("WATSONX_PROJECT_ID")
WX_MODEL   = setting("WATSONX_MODEL_ID", "sentence-transformers/all-minilm-l6-v2")
SKIP_EMBED = setting("SKIP_EMBEDDING", "").lower() in ("1", "true", "yes")


def db2ts(command):
    """Run one db2ts text-search command as the Db2 instance owner."""
    shell = f'export DB2DBDFT={DATABASE}; db2ts "{command}"'
    argv = ["bash", "-lc", shell] if getpass.getuser() == OWNER \
        else ["sudo", "-niu", OWNER, "bash", "-lc", shell]
    subprocess.run(argv, check=False)


def quote(value):
    """Quote a string for inline SQL (escapes single quotes). Used in DDL,
    which can't take parameter markers."""
    return "'" + (value or "").replace("'", "''") + "'"


def connect():
    """Connect to Db2, retrying a few times to ride out transient stalls."""
    dsn = (f"DATABASE={DATABASE};HOSTNAME={HOST};PORT={PORT};"
           f"PROTOCOL=TCPIP;UID={USER};PWD={PASSWORD};ConnectTimeout=10;")
    for attempt in range(5):
        try:
            return ibm_db.connect(dsn, "", "")
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2)


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python ingest.py path/to/document.pdf")
    pdf = sys.argv[1]
    if not os.path.exists(pdf):
        sys.exit("PDF not found: " + pdf)
    if not PASSWORD:
        sys.exit("Set DB2_PASSWORD in .env")

    markdown_path = os.path.splitext(pdf)[0] + ".md"

    # 1. Extract the PDF to Markdown.
    print(f"1. Extracting {pdf} -> {markdown_path}")
    document = DocumentConverter().convert(pdf).document
    open(markdown_path, "w").write(document.export_to_markdown())

    # 2. Chunk it (capped to the embedding model's token limit).
    print(f"2. Chunking (max {MAX_TOKENS} tokens)")
    chunker = HybridChunker(tokenizer=HuggingFaceTokenizer(
        tokenizer=AutoTokenizer.from_pretrained(TOKENIZER), max_tokens=MAX_TOKENS))
    chunks = [chunker.contextualize(chunk=c) for c in chunker.chunk(dl_doc=document)]
    print(f"   {len(chunks)} chunks")

    conn = connect()

    # 3. Create the table and load the chunks (parameterized insert).
    print(f"3. Loading {len(chunks)} chunks into {TABLE_FULL}")
    try:
        ibm_db.exec_immediate(conn,
            f"CREATE TABLE {TABLE_FULL} (chunk_id INTEGER NOT NULL PRIMARY KEY, chunk_text CLOB(1M))")
    except Exception:
        sys.exit(f"Table {TABLE_FULL} already exists — run ./cleanup.sh first.")
    insert = ibm_db.prepare(conn, f"INSERT INTO {TABLE_FULL} (chunk_id, chunk_text) VALUES (?, ?)")
    for chunk_id, text in enumerate(chunks, start=1):
        ibm_db.bind_param(insert, 1, chunk_id)
        ibm_db.bind_param(insert, 2, text)
        ibm_db.execute(insert)

    # 4. Build the keyword (text-search) index via db2ts.
    print(f"4. Building text-search index {SCHEMA}.{INDEX}")
    found = ibm_db.exec_immediate(conn,
        f"SELECT SERVERID FROM SYSIBMTS.SYSTSSERVERS "
        f"WHERE SERVERPORT={OS_PORT} AND ENGINETYPE='OPENSEARCH' FETCH FIRST 1 ROW ONLY")
    row = ibm_db.fetch_assoc(found)
    if not row:
        sys.exit("No OpenSearch server registered. Run ./setup_text_search.sh first.")
    server_id = row["SERVERID"]
    db2ts(f"ENABLE DATABASE FOR TEXT CONNECT TO {DATABASE}")
    db2ts(f"CREATE INDEX {SCHEMA}.{INDEX} FOR TEXT ON {TABLE_FULL}(chunk_text) SERVERID {server_id} INACTIVE")
    db2ts(f"ALTER INDEX {SCHEMA}.{INDEX} FOR TEXT SET ACTIVE")
    db2ts(f"UPDATE INDEX {SCHEMA}.{INDEX} FOR TEXT")

    if SKIP_EMBED:
        print("Done (lexical-only; SKIP_EMBEDDING set).")
        ibm_db.close(conn)
        return

    # 5. Register the watsonx.ai embedding model in Db2.
    print(f"5. Registering watsonx model {MODEL}")
    if not (WX_URL and WX_KEY and WX_PROJECT):
        sys.exit("Set WATSONX_URL/APIKEY/PROJECT_ID in .env (or set SKIP_EMBEDDING=1).")
    try:
        ibm_db.exec_immediate(conn, f"DROP EXTERNAL MODEL {MODEL}")  # so re-runs pick up new config
    except Exception:
        pass
    ibm_db.exec_immediate(conn,
        f"CREATE EXTERNAL MODEL {MODEL} PROVIDER WATSONX "
        f"ID {quote(WX_MODEL)} URL {quote(WX_URL)} "
        f"TYPE TEXT_EMBEDDING RETURNING VECTOR({DIM}, FLOAT32) "
        f"KEY {quote(WX_KEY)} PROJECT_ID {quote(WX_PROJECT)}")

    # 6. Add a VECTOR column and fill it with in-database embeddings.
    print(f"6. Embedding chunks into a VECTOR({DIM}) column")
    try:
        ibm_db.exec_immediate(conn, f"ALTER TABLE {TABLE_FULL} ADD COLUMN embedding VECTOR({DIM}, FLOAT32)")
    except Exception:
        pass  # column already exists
    ibm_db.exec_immediate(conn, f"UPDATE {TABLE_FULL} SET embedding = TO_EMBEDDING(chunk_text USING {MODEL})")

    ibm_db.close(conn)
    print(f"Done: {len(chunks)} chunks with text + vector in {TABLE_FULL}")


if __name__ == "__main__":
    main()
