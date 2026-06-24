#!/usr/bin/env python3
"""
ui/api.py — live backend for the demo (used only by `./ui/run.sh --live`).

A thin wrapper over the search engine. It returns the SAME JSON shape that
build_fixtures.py freezes into fixtures.json, so the frontend is identical
whether it reads frozen fixtures (offline, the default) or hits this API live.

The default demo path does NOT use this server at all — it serves the static
page + fixtures.json with python's stdlib http.server, so the talk runs fully
offline. This file exists for ad-hoc/typed queries during Q&A.
"""

import json
import os

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
import ibm_db

import hybrid_core as h
import build_fixtures as bf   # responses_for()

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "queries.json")) as f:
    DECK = json.load(f)
GOLD = {item["query"]: set(item["gold_chunk_ids"]) for item in DECK}

app = FastAPI(title="Db2 Hybrid Search Demo", docs_url="/docs")


@app.get("/api/queries")
def queries():
    """The curated demo deck (query, type, gold chunk IDs, note)."""
    return DECK


@app.get("/api/search")
def search(q: str = Query(..., description="search text"), k: int = bf.K):
    """All three strategy responses for a query (lexical, vector, hybrid).
    gold_chunk_ids come from the curated set when the query matches one."""
    gold = GOLD.get(q, set())
    conn = h.connect()
    try:
        modes = bf.responses_for(conn, q, gold)
    finally:
        ibm_db.close(conn)
    return {"query": q, "gold_chunk_ids": sorted(gold), **modes}


# Serve the same static UI; API routes above take precedence over this mount.
app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")
