#!/usr/bin/env python3
"""
6_search.py — hybrid search over the corpus built by 5_ingest.py.

All the search work happens IN Db2 — there is no BM25 or embedding math in
Python here. For each query it runs three rankings and shows them together:

  • Lexical  — Db2 Text Search (OpenSearch-backed): CONTAINS + SCORE (keywords).
  • Vector   — Db2 native vectors: VECTOR_DISTANCE + in-database TO_EMBEDDING.
  • Hybrid   — the two above fused with Reciprocal Rank Fusion (RRF), computed
               in a single SQL query.

Seeing them side by side is the point: keyword search nails exact terms, vector
search catches paraphrases, and the fusion gets both.

Prerequisite: run 5_ingest.py first (creates myschema.chunks with a text index
and a vector column).

Usage:
    python scripts/6_search.py                 # preset demo queries
    python scripts/6_search.py "your question" # search your own query
Config: read from .env at the repo root (same Db2 + watsonx settings as the pipeline).
"""

import os
import sys
import time

import ibm_db

# --- Read settings from .env (repo root) -------------------------------------
# Best-effort: if .env is missing or unreadable (e.g. running as the instance
# owner in local mode), just fall back to defaults / real env vars.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for env_file in (os.path.join(ROOT, ".env"), ".env"):
    try:
        if os.path.exists(env_file):
            for line in open(env_file):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    except OSError:
        pass

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
    """Connect to Db2.

    LOCAL mode (DB2_HOST empty or 'local'): a fast local connection — run this
    as the Db2 instance owner; no host/port/password needed. Use this to avoid
    the slow ibm_db TCP connect.

    Otherwise: a TCP connection using the .env credentials (with a short retry).
    """
    if not HOST or HOST.lower() == "local":
        return ibm_db.connect(DATABASE, "", "")
    dsn = (f"DATABASE={DATABASE};HOSTNAME={HOST};PORT={PORT};"
           f"PROTOCOL=TCPIP;UID={USER};PWD={PASSWORD};ConnectTimeout=10;")
    for attempt in range(2):
        try:
            return ibm_db.connect(dsn, "", "")
        except Exception:
            if attempt == 1:
                raise
            time.sleep(2)


def ids(conn, sql, params):
    """Run a query, binding `params` in order; return the chunk ids."""
    stmt = ibm_db.prepare(conn, sql)
    for i, value in enumerate(params, start=1):
        ibm_db.bind_param(stmt, i, value)
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
    # No argument -> run the preset demo queries. One argument -> search it.
    queries = [sys.argv[1]] if len(sys.argv) > 1 else DEMO_QUERIES
    conn = connect()
    for query in queries:
        # Db2 Text Search CONTAINS requires ALL words to be present (implicit
        # AND), so a natural-language query like "how to use TEXT_GENERATION"
        # would match nothing. We OR the words for the keyword leg so it matches
        # on any of them, ranked by SCORE — normal keyword-search behavior. The
        # vector leg always uses the raw query (it's embedded as-is).
        keywords = " OR ".join(query.split())

        # We run each leg on its own only so we can show the three rankings side
        # by side. The hybrid query already fuses lexical + vector with RRF in SQL.
        lexical = ids(conn, LEXICAL_SQL, [keywords, keywords])
        vector  = ids(conn, VECTOR_SQL,  [query])
        hybrid  = ids(conn, HYBRID_SQL,  [query, keywords, keywords])

        print("\n" + "=" * 70)
        print(f'QUERY: "{query}"')
        print("-" * 70)
        print(f"  Lexical (keyword)  : {lexical or '(no keyword matches)'}")
        print(f"  Vector  (semantic) : {vector}")
        print(f"  Hybrid  (RRF in SQL): {hybrid}")
        print("\n  Hybrid results:")
        for cid in hybrid:
            print(f"    #{cid}: {snippet(conn, cid)}")
    ibm_db.close(conn)


if __name__ == "__main__":
    main()
