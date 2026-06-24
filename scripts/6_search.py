#!/usr/bin/env python3
"""
6_search.py — run the three search legs for a query and print them side by side.

    python scripts/6_search.py                 # preset demo queries
    python scripts/6_search.py "your query"

On this setup run it via ./scripts/search.sh (fast local connection). The engine
and the fusion live in hybrid_core.py.
"""

import sys
import ibm_db
import hybrid_core as h

N = 3  # results to show per leg

# 1. an exact error code -> keyword wins, vectors scatter (and get gated out)
# 2. a paraphrase        -> keywords struggle, vectors carry it
DEMO_QUERIES = ["42615", "how do I turn text into vectors"]


def show(conn, label, rows):
    print(f"\n  {label}: {[cid for cid, _ in rows] or '(no matches)'}")
    for cid, score in rows:
        print(f"      #{cid} ({score:.3f}): {h.snippet(conn, cid)}")


def main():
    queries = sys.argv[1:] or DEMO_QUERIES
    conn = h.connect()
    for query in queries:
        print("\n" + "=" * 70)
        print(f'QUERY: "{query}"')
        print("-" * 70)
        show(conn, "Lexical (keyword, BM25)  ", h.lexical(conn, query, N))
        show(conn, "Vector  (semantic, cosine)", h.vector(conn, query, N))
        show(conn, "Hybrid  (gated fusion)   ", h.hybrid(conn, query, N))
    ibm_db.close(conn)


if __name__ == "__main__":
    main()
