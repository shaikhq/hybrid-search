#!/usr/bin/env python3
"""
hybrid_search.py — a small, representative hybrid-search demo on IBM Db2.

All the search work happens IN Db2 — there is no BM25 or embedding math in
Python here. For each demo query, against the corpus built by
../scripts/ingest.py, the script runs three rankings and shows them together:

  • Lexical  — Db2 Text Search (OpenSearch-backed): CONTAINS + SCORE (keywords).
  • Vector   — Db2 native vectors: VECTOR_DISTANCE + in-database TO_EMBEDDING.
  • Hybrid   — the two above fused with Reciprocal Rank Fusion (RRF), computed
               in a single SQL query.

Seeing them side by side is the point: keyword search nails exact terms, vector
search catches paraphrases, and the fusion gets both.

Prerequisite: run ../scripts/ingest.py first (creates myschema.chunks with a
text index and a vector column).

Usage:  python hybrid_search.py
Config: read from ../.env (same Db2 + watsonx settings as the pipeline).
"""

import os
import time

import ibm_db

# --- Read settings from .env (repo root) -------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for env_file in (os.path.join(ROOT, ".env"), ".env"):
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

def setting(name, default=None):
    return os.environ.get(name, default)

DATABASE = setting("DB2_DATABASE", "sample")
HOST     = setting("DB2_HOST", "localhost")
PORT     = setting("DB2_PORT", "50000")
USER     = setting("DB2_USER", "db2inst1")
PASSWORD = setting("DB2_PASSWORD")
SCHEMA   = setting("DB2_SCHEMA", "myschema")
TABLE    = setting("DB2_TABLE", "chunks")
T        = f"{SCHEMA}.{TABLE}"
MODEL    = f"{SCHEMA}.{TABLE}_embed"

N     = 3    # results to show per ranking
RRF_K = 60   # RRF constant

# Two contrasting demo queries:
#   1. shares vocabulary with the docs  -> both legs do well
#   2. a paraphrase ("vectors" vs "embeddings") -> keywords struggle, vectors win
DEMO_QUERIES = [
    "vector embeddings",
    "how do I turn text into vectors",
]

# Lexical leg: keyword match, ranked by Db2 Text Search SCORE.
LEXICAL_SQL = f"""
    SELECT chunk_id FROM {T}
    WHERE CONTAINS(chunk_text, CAST(? AS VARCHAR(4000))) = 1
    ORDER BY SCORE(chunk_text, CAST(? AS VARCHAR(4000))) DESC
    FETCH FIRST {N} ROWS ONLY
"""

# Vector leg: semantic match. Db2 embeds the query (TO_EMBEDDING) and compares.
VECTOR_SQL = f"""
    SELECT chunk_id FROM {T}
    ORDER BY VECTOR_DISTANCE(embedding, TO_EMBEDDING(CAST(? AS VARCHAR(4000)) USING {MODEL}), COSINE) ASC
    FETCH FIRST {N} ROWS ONLY
"""

# Hybrid leg: rank each side, keep each side's top-10, FULL OUTER JOIN, and add
# 1/(k+rank) across the two — Reciprocal Rank Fusion, entirely in SQL.
HYBRID_SQL = f"""
    WITH
    q (qv) AS (VALUES TO_EMBEDDING(CAST(? AS VARCHAR(4000)) USING {MODEL})),
    lex AS (SELECT chunk_id, rnk FROM (
        SELECT chunk_id, ROW_NUMBER() OVER (ORDER BY SCORE(chunk_text, CAST(? AS VARCHAR(4000))) DESC) AS rnk
        FROM {T} WHERE CONTAINS(chunk_text, CAST(? AS VARCHAR(4000))) = 1) WHERE rnk <= 10),
    vec AS (SELECT chunk_id, rnk FROM (
        SELECT c.chunk_id, ROW_NUMBER() OVER (ORDER BY VECTOR_DISTANCE(c.embedding, q.qv, COSINE) ASC) AS rnk
        FROM {T} c, q) WHERE rnk <= 10)
    SELECT COALESCE(lex.chunk_id, vec.chunk_id) AS chunk_id
    FROM lex FULL OUTER JOIN vec ON lex.chunk_id = vec.chunk_id
    ORDER BY COALESCE(1.0/({RRF_K} + lex.rnk), 0) + COALESCE(1.0/({RRF_K} + vec.rnk), 0) DESC
    FETCH FIRST {N} ROWS ONLY
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


def ids(conn, sql, query, n_params):
    """Run a query (binding `query` n_params times) and return the chunk ids."""
    stmt = ibm_db.prepare(conn, sql)
    for i in range(1, n_params + 1):
        ibm_db.bind_param(stmt, i, query)
    ibm_db.execute(stmt)
    out, row = [], ibm_db.fetch_assoc(stmt)
    while row:
        out.append(int(row["CHUNK_ID"]))
        row = ibm_db.fetch_assoc(stmt)
    return out


def snippet(conn, chunk_id):
    stmt = ibm_db.prepare(conn, f"SELECT CAST(SUBSTR(chunk_text,1,90) AS VARCHAR(90)) AS S FROM {T} WHERE chunk_id = ?")
    ibm_db.bind_param(stmt, 1, chunk_id)
    ibm_db.execute(stmt)
    return ibm_db.fetch_assoc(stmt)["S"].strip()


def main():
    conn = connect()
    for query in DEMO_QUERIES:
        lexical = ids(conn, LEXICAL_SQL, query, 2)
        vector  = ids(conn, VECTOR_SQL,  query, 1)
        hybrid  = ids(conn, HYBRID_SQL,  query, 3)
        print("\n" + "=" * 70)
        print(f'QUERY: "{query}"')
        print("-" * 70)
        print(f"  Lexical (keyword) : {lexical or '(no keyword matches)'}")
        print(f"  Vector  (semantic): {vector}")
        print(f"  Hybrid  (RRF/SQL) : {hybrid}")
        if hybrid:
            print(f"  Top hybrid chunk {hybrid[0]}: {snippet(conn, hybrid[0])}")
    ibm_db.close(conn)


if __name__ == "__main__":
    main()
