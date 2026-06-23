#!/usr/bin/env python3
"""
search.py — hybrid search over the corpus built by ingest.py.

One Db2 SQL query does everything:
  • Lexical — Db2 Text Search keyword match (CONTAINS, ranked by SCORE).
  • Vector  — semantic match on watsonx embeddings (VECTOR_DISTANCE, cosine).
  • Fusion  — Reciprocal Rank Fusion (RRF) combines the two rankings, computed
              in SQL with ROW_NUMBER() + a FULL OUTER JOIN + SUM 1/(k+rank).

Usage:  python search.py "your question"
Config: read from .env (same Db2 + watsonx settings as ingest.py).
"""

import os
import sys
import textwrap
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
MODEL      = SCHEMA + "." + TABLE + "_embed"
TABLE_FULL = SCHEMA + "." + TABLE

TOP_K = 10   # results taken from each leg before fusing
FINAL = 5    # fused results to show
RRF_K = 60   # RRF constant (a standard default)

# Hybrid search in one query. The query text is bound three times (?, ?, ?):
# once to embed it for the vector leg, twice for the lexical leg (SCORE + CONTAINS).
# RRF lives entirely in SQL: rank each leg with ROW_NUMBER(), keep each leg's
# top-K, FULL OUTER JOIN on chunk_id, and sum 1 / (RRF_K + rank) across legs.
HYBRID_SQL = f"""
WITH
q (qv) AS (
    VALUES TO_EMBEDDING(CAST(? AS VARCHAR(4000)) USING {MODEL})
),
lex AS (
    SELECT chunk_id, rnk FROM (
        SELECT chunk_id,
               ROW_NUMBER() OVER (ORDER BY SCORE(chunk_text, CAST(? AS VARCHAR(4000))) DESC) AS rnk
        FROM {TABLE_FULL}
        WHERE CONTAINS(chunk_text, CAST(? AS VARCHAR(4000))) = 1
    ) WHERE rnk <= {TOP_K}
),
vec AS (
    SELECT chunk_id, rnk FROM (
        SELECT c.chunk_id,
               ROW_NUMBER() OVER (ORDER BY VECTOR_DISTANCE(c.embedding, q.qv, COSINE) ASC) AS rnk
        FROM {TABLE_FULL} c, q
    ) WHERE rnk <= {TOP_K}
),
fused AS (
    SELECT COALESCE(lex.chunk_id, vec.chunk_id) AS chunk_id,
           COALESCE(1.0/({RRF_K} + lex.rnk), 0) + COALESCE(1.0/({RRF_K} + vec.rnk), 0) AS rrf,
           lex.rnk AS lex_rnk, vec.rnk AS vec_rnk
    FROM lex FULL OUTER JOIN vec ON lex.chunk_id = vec.chunk_id
)
SELECT f.chunk_id, f.rrf, f.lex_rnk, f.vec_rnk, c.chunk_text
FROM fused f JOIN {TABLE_FULL} c ON c.chunk_id = f.chunk_id
ORDER BY f.rrf DESC
FETCH FIRST {FINAL} ROWS ONLY
"""


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
        sys.exit('Usage: python search.py "your question"')
    query = sys.argv[1]

    conn = connect()

    stmt = ibm_db.prepare(conn, HYBRID_SQL)
    for i in (1, 2, 3):
        ibm_db.bind_param(stmt, i, query)
    ibm_db.execute(stmt)

    print(f'\nQuery: {query}\nHybrid (RRF in SQL) results:')
    row = ibm_db.fetch_assoc(stmt)
    if not row:
        print("  (no matches)")
    while row:
        found = ", ".join(([f"lex#{row['LEX_RNK']}"] if row["LEX_RNK"] else []) +
                          ([f"vec#{row['VEC_RNK']}"] if row["VEC_RNK"] else []))
        print(f"\n  chunk {row['CHUNK_ID']}   rrf {float(row['RRF']):.5f}   [{found}]")
        print(textwrap.fill(str(row["CHUNK_TEXT"]).strip(), width=96,
                            initial_indent="    ", subsequent_indent="    ")[:320])
        row = ibm_db.fetch_assoc(stmt)

    ibm_db.close(conn)


if __name__ == "__main__":
    main()
