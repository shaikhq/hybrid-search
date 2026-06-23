#!/usr/bin/env python3
"""
search.py — Hybrid search over the corpus built by ingest.py
=============================================================================
Runs BOTH retrieval legs over myschema.chunks and fuses them:

  • Lexical  — Db2 Text Search:  CONTAINS() filters, SCORE() ranks (BM25-style).
  • Vector   — native Db2 vector: VECTOR_DISTANCE(embedding, TO_EMBEDDING(query))
               cosine similarity, with the query embedded in-database by the
               same watsonx.ai model used at ingest time.
  • Fusion   — Reciprocal Rank Fusion (RRF): score(d) = Σ 1 / (k + rank_leg(d)).

This is the payoff of the one-chunk-two-representations table: each leg covers
the other's blind spot (lexical nails exact tokens; vector catches paraphrases),
and RRF merges their rankings without needing comparable score scales.

PARAMETERIZED SQL: the user query is always bound as a parameter (never built
into SQL text) — for both CONTAINS/SCORE and TO_EMBEDDING.

USAGE:
    python3 search.py "how do I turn text into vectors"
    python3 search.py --top-k 10 --final 5 "register an external model"

Config (CLI or env, same .env as ingest.py): Db2 connection, DB2_SCHEMA,
DB2_TABLE, DB2_MODEL_NAME, DB2_VECTOR_COLUMN.
=============================================================================
"""

import argparse
import os
import re
import sys
import textwrap
import time

import ibm_db


def load_dotenv():
    """Load .env from the current dir or the repo root (so it works whether you
    run from the repo root or from scripts/). Real env vars take precedence."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in (".env", os.path.join(repo_root, ".env")):
        if not os.path.exists(path):
            continue
        for line in open(path):
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

def ident(name, what):
    if not _IDENT.match(name or ""):
        sys.exit(f"ERROR: invalid {what} {name!r}")
    return name


def build_config(argv=None):
    load_dotenv()
    p = argparse.ArgumentParser(description="Hybrid search (lexical + vector + RRF) over Db2")
    p.add_argument("query", help="the search query")
    p.add_argument("--top-k", type=int, default=int(os.environ.get("TOP_K", 10)),
                   help="results retrieved per leg before fusion (default 10)")
    p.add_argument("--final", type=int, default=int(os.environ.get("FINAL_K", 5)),
                   help="fused results to display (default 5)")
    p.add_argument("--rrf-k", type=int, default=int(os.environ.get("RRF_K", 60)),
                   help="RRF damping constant k (default 60)")
    p.add_argument("--db-host", default=os.environ.get("DB2_HOST", "localhost"))
    p.add_argument("--db-port", default=os.environ.get("DB2_PORT", "50000"))
    p.add_argument("--db-name", default=os.environ.get("DB2_DATABASE", "sample"))
    p.add_argument("--db-user", default=os.environ.get("DB2_USER", "db2inst1"))
    p.add_argument("--db-password", default=os.environ.get("DB2_PASSWORD"))
    p.add_argument("--schema", default=os.environ.get("DB2_SCHEMA", "myschema"))
    p.add_argument("--table", default=os.environ.get("DB2_TABLE", "chunks"))
    p.add_argument("--vector-column", default=os.environ.get("DB2_VECTOR_COLUMN", "embedding"))
    p.add_argument("--model-name", default=os.environ.get("DB2_MODEL_NAME"))
    a = p.parse_args(argv)
    if not a.db_password:
        p.error("Db2 password required (--db-password or DB2_PASSWORD)")
    a.model_name = a.model_name or f"{a.schema}.{a.table}_embed"
    ident(a.schema, "schema"); ident(a.table, "table")
    ident(a.vector_column, "vector column"); ident(a.model_name, "model name")
    return a


def connect(a, attempts=4):
    # ConnectTimeout makes a stalled TCP connect fail fast (instead of hanging)
    # so the retry loop can recover from transient connection-manager stalls.
    dsn = (f"DATABASE={a.db_name};HOSTNAME={a.db_host};PORT={a.db_port};"
           f"PROTOCOL=TCPIP;UID={a.db_user};PWD={a.db_password};ConnectTimeout=15;")
    last = None
    for i in range(attempts):
        try:
            return ibm_db.connect(dsn, "", "")
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep(2)
    raise SystemExit(f"ERROR: could not connect to Db2 after {attempts} attempts: {last}")


def fetch_all(stmt):
    rows, r = [], ibm_db.fetch_assoc(stmt)
    while r:
        rows.append(r); r = ibm_db.fetch_assoc(stmt)
    return rows


def lexical_search(conn, a):
    """BM25-style lexical ranking via Db2 Text Search. Query is parameterized."""
    tbl = f"{a.schema}.{a.table}"
    sql = (f"SELECT chunk_id, SCORE(chunk_text, CAST(? AS VARCHAR(4000))) AS REL "
           f"FROM {tbl} WHERE CONTAINS(chunk_text, CAST(? AS VARCHAR(4000))) = 1 "
           f"ORDER BY REL DESC FETCH FIRST {int(a.top_k)} ROWS ONLY")
    stmt = ibm_db.prepare(conn, sql)
    ibm_db.bind_param(stmt, 1, a.query)
    ibm_db.bind_param(stmt, 2, a.query)
    ibm_db.execute(stmt)
    return [(int(r["CHUNK_ID"]), float(r["REL"])) for r in fetch_all(stmt)]


def vector_search(conn, a):
    """Semantic ranking via cosine distance on watsonx embeddings. Parameterized."""
    tbl = f"{a.schema}.{a.table}"
    sql = (f"SELECT chunk_id, "
           f"VECTOR_DISTANCE({a.vector_column}, "
           f"TO_EMBEDDING(CAST(? AS VARCHAR(4000)) USING {a.model_name}), COSINE) AS DIST "
           f"FROM {tbl} ORDER BY DIST ASC FETCH FIRST {int(a.top_k)} ROWS ONLY")
    stmt = ibm_db.prepare(conn, sql)
    ibm_db.bind_param(stmt, 1, a.query)
    ibm_db.execute(stmt)
    return [(int(r["CHUNK_ID"]), float(r["DIST"])) for r in fetch_all(stmt)]


def rrf_fuse(lexical, vector, k):
    """Reciprocal Rank Fusion. Returns [(chunk_id, rrf, lex_rank, vec_rank)] sorted."""
    lex_rank = {cid: i for i, (cid, _) in enumerate(lexical, start=1)}
    vec_rank = {cid: i for i, (cid, _) in enumerate(vector, start=1)}
    fused = {}
    for cid, r in lex_rank.items():
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + r)
    for cid, r in vec_rank.items():
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + r)
    out = [(cid, score, lex_rank.get(cid), vec_rank.get(cid)) for cid, score in fused.items()]
    out.sort(key=lambda x: -x[1])
    return out


def fetch_texts(conn, a, ids):
    if not ids:
        return {}
    tbl = f"{a.schema}.{a.table}"
    marks = ",".join("?" * len(ids))
    stmt = ibm_db.prepare(conn, f"SELECT chunk_id, chunk_text FROM {tbl} WHERE chunk_id IN ({marks})")
    for i, cid in enumerate(ids, start=1):
        ibm_db.bind_param(stmt, i, cid)
    ibm_db.execute(stmt)
    return {int(r["CHUNK_ID"]): str(r["CHUNK_TEXT"]) for r in fetch_all(stmt)}


def main():
    a = build_config()
    conn = connect(a)
    try:
        lexical = lexical_search(conn, a)
        vector = vector_search(conn, a)
        fused = rrf_fuse(lexical, vector, a.rrf_k)

        print(f'\nQuery: "{a.query}"')

        print(f"\n── Lexical (Db2 Text Search · SCORE) ── top {len(lexical)}")
        for rank, (cid, rel) in enumerate(lexical, start=1):
            print(f"  {rank:>2}. chunk {cid:<4} score {rel:.3f}")

        print(f"\n── Vector (watsonx embeddings · cosine) ── top {len(vector)}")
        for rank, (cid, dist) in enumerate(vector, start=1):
            print(f"  {rank:>2}. chunk {cid:<4} distance {dist:.3f}")

        print(f"\n── Hybrid (RRF, k={a.rrf_k}) ── top {a.final}")
        texts = fetch_texts(conn, a, [cid for cid, *_ in fused[:a.final]])
        for rank, (cid, score, lr, vr) in enumerate(fused[:a.final], start=1):
            legs = []
            if lr: legs.append(f"lex#{lr}")
            if vr: legs.append(f"vec#{vr}")
            print(f"\n  {rank}. chunk {cid}   rrf {score:.4f}   [{', '.join(legs)}]")
            print(textwrap.fill(texts.get(cid, "").strip(), width=96,
                                initial_indent="      ", subsequent_indent="      ")[:360])
    finally:
        ibm_db.close(conn)


if __name__ == "__main__":
    main()
