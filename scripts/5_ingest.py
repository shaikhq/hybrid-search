#!/usr/bin/env python3
"""
5_ingest.py — load chunks from a CSV into Db2 and build both search indexes.

Reads a CSV of (chunk_id, chunk_text) produced by 4_chunk.py, then:
  1. Load     the chunks into a Db2 table.
  2. Index    the text for keyword search (Db2 Text Search, OpenSearch-backed).
  3. Register a watsonx.ai embedding model in Db2.
  4. Embed    each chunk into a VECTOR column (in-database TO_EMBEDDING).
  5. Index    the VECTOR column for fast approximate nearest-neighbour search.

End state: one row per chunk holding its text, a stable chunk_id, a text-search
index entry, its dense vector, and a vector (ANN) index over that vector.

Usage:  python scripts/5_ingest.py chunks.csv
Config: read from .env (Db2 connection + watsonx.ai credentials).
Run as the Db2 instance owner — step 2 uses the db2ts command.
Run ./1_cleanup.sh first for a clean slate, and ./2_setup.sh once beforehand.
"""

import csv
import getpass
import os
import subprocess
import sys
import time

import ibm_db

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
VEC_INDEX  = TABLE + "_vec_idx"
MODEL      = SCHEMA + "." + TABLE + "_embed"
TABLE_FULL = SCHEMA + "." + TABLE

DIM        = int(setting("VECTOR_DIM", "384"))
# Distance metric the vector index is built with. Must match the metric used in
# the search queries (hybrid_core.py uses COSINE). COSINE requires FLOAT32.
VEC_DISTANCE = setting("VECTOR_DISTANCE", "COSINE")
OS_PORT    = int(setting("OPENSEARCH_PORT", "9200"))
OWNER      = setting("DB2_INSTANCE_OWNER", "db2inst1")

WX_URL     = setting("WATSONX_URL")
WX_KEY     = setting("WATSONX_APIKEY")
WX_PROJECT = setting("WATSONX_PROJECT_ID")
WX_MODEL   = setting("WATSONX_MODEL_ID", "sentence-transformers/all-minilm-l6-v2")
SKIP_EMBED = setting("SKIP_EMBEDDING", "").lower() in ("1", "true", "yes")

csv.field_size_limit(10_000_000)   # chunk_text can be a few thousand characters


def as_owner(shell):
    """Run a shell command as the Db2 instance owner — directly if we already are
    that user, otherwise via sudo."""
    argv = ["bash", "-lc", shell] if getpass.getuser() == OWNER \
        else ["sudo", "-niu", OWNER, "bash", "-lc", shell]
    subprocess.run(argv, check=False)


def db2ts(command):
    """Run one db2ts text-search command as the Db2 instance owner."""
    as_owner(f'export DB2DBDFT={DATABASE}; db2ts "{command}"')


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
        sys.exit("Usage: python scripts/5_ingest.py chunks.csv")
    csv_path = sys.argv[1]
    if not os.path.exists(csv_path):
        sys.exit("CSV not found: " + csv_path)
    if not PASSWORD:
        sys.exit("Set DB2_PASSWORD in .env")

    # Read the chunks from CSV (columns: chunk_id, chunk_text).
    with open(csv_path, newline="") as f:
        chunks = [(int(r["chunk_id"]), r["chunk_text"]) for r in csv.DictReader(f)]
    print(f"Read {len(chunks)} chunks from {csv_path}")

    # Vector indexes are gated behind a registry variable. Set it (effective
    # immediately, no instance restart) before connecting, so this session can
    # CREATE the vector index in step 5. Skipped in lexical-only runs.
    if not SKIP_EMBED:
        print("Enabling vector indexing (db2set DB2_VECTOR_INDEXING=YES)")
        as_owner("db2set DB2_VECTOR_INDEXING=YES -immediate")

    conn = connect()

    # 1. Create the table and load the chunks (parameterized insert).
    print(f"1. Loading chunks into {TABLE_FULL}")
    try:
        ibm_db.exec_immediate(conn,
            f"CREATE TABLE {TABLE_FULL} (chunk_id INTEGER NOT NULL PRIMARY KEY, chunk_text CLOB(1M))")
    except Exception:
        sys.exit(f"Table {TABLE_FULL} already exists — run ./1_cleanup.sh first.")
    insert = ibm_db.prepare(conn, f"INSERT INTO {TABLE_FULL} (chunk_id, chunk_text) VALUES (?, ?)")
    for chunk_id, text in chunks:
        ibm_db.bind_param(insert, 1, chunk_id)
        ibm_db.bind_param(insert, 2, text)
        ibm_db.execute(insert)

    # 2. Build the keyword (text-search) index via db2ts.
    print(f"2. Building text-search index {SCHEMA}.{INDEX}")
    # Change, read it from view SYSIBMTS.TSSERVERS
    found = ibm_db.exec_immediate(conn,
        f"SELECT SERVERID FROM SYSIBMTS.SYSTSSERVERS "
        f"WHERE SERVERPORT={OS_PORT} AND ENGINETYPE='OPENSEARCH' FETCH FIRST 1 ROW ONLY")
    row = ibm_db.fetch_assoc(found)
    if not row:
        sys.exit("No OpenSearch server registered. Run ./2_setup.sh first.")
    server_id = row["SERVERID"]
    # (Text search was enabled once by 2_setup.sh.) Create the index, turn it
    # on, then fill it — db2ts prints "CIE00001 ... successfully" for each step.
    # Chnage to SPs
    # Add DROP Index: https://www.ibm.com/docs/en/db2/12.1.x?topic=routines-systs-drop-procedure-drop-text-search-index
    # Chnage to SP: https://www.ibm.com/docs/en/db2/12.1.x?topic=routines-systs-create-procedure-create-text-search-index
    db2ts(f"CREATE INDEX {SCHEMA}.{INDEX} FOR TEXT ON {TABLE_FULL}(chunk_text) SERVERID {server_id}")
    # Change to SP: https://www.ibm.com/docs/en/db2/12.1.x?topic=routines-systs-update-procedure-update-text-search-index
    db2ts(f"UPDATE INDEX {SCHEMA}.{INDEX} FOR TEXT")

    if SKIP_EMBED:
        print("Done (lexical-only; SKIP_EMBEDDING set).")
        ibm_db.close(conn)
        return

    # 3. Register the watsonx.ai embedding model in Db2.
    print(f"3. Registering watsonx model {MODEL}")
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

    # 4. Add a VECTOR column and fill it with in-database embeddings.
    print(f"4. Embedding chunks into a VECTOR({DIM}) column")
    try:
        ibm_db.exec_immediate(conn, f"ALTER TABLE {TABLE_FULL} ADD COLUMN embedding VECTOR({DIM}, FLOAT32)")
    except Exception:
        pass  # column already exists
    ibm_db.exec_immediate(conn, f"UPDATE {TABLE_FULL} SET embedding = TO_EMBEDDING(chunk_text USING {MODEL})")

    # 5. Build the vector (ANN) index over the embeddings. This accelerates the
    #    cosine-similarity leg from a brute-force scan to a graph traversal.
    #    Notes from the Db2 docs that drive the choices below:
    #      - EXCLUDE NULL KEYS is required because `embedding` is nullable.
    #      - Creating the index makes the table READ-ONLY, so it must be the
    #        last write — all rows are already loaded and embedded by now.
    #      - The optimizer only chooses the index once statistics exist; without
    #        RUNSTATS an APPROX search silently falls back to a brute-force scan.
    print(f"5. Building vector index {SCHEMA}.{VEC_INDEX} (DISTANCE {VEC_DISTANCE})")
    try:
        ibm_db.exec_immediate(conn, f"DROP INDEX {SCHEMA}.{VEC_INDEX}")  # tidy partial re-runs
    except Exception:
        pass
    ibm_db.exec_immediate(conn,
        f"CREATE VECTOR INDEX {SCHEMA}.{VEC_INDEX} ON {TABLE_FULL}(embedding) "
        f"WITH DISTANCE {VEC_DISTANCE} EXCLUDE NULL KEYS")
    ibm_db.exec_immediate(conn,
        f"CALL SYSPROC.ADMIN_CMD('RUNSTATS ON TABLE {TABLE_FULL} AND INDEXES ALL')")

    ibm_db.close(conn)
    print(f"Done: {len(chunks)} chunks with text + vector + vector index in {TABLE_FULL}")


if __name__ == "__main__":
    main()
