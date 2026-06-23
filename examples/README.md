# Example — hybrid search, three ways

`hybrid_search.py` is a small demo that shows the value of hybrid search on a
few representative queries. **All the search runs in Db2** — no BM25 or
embedding math in Python. For each query it prints three rankings side by side:

- **Lexical** — Db2 Text Search (OpenSearch-backed): `CONTAINS` + `SCORE`.
- **Vector** — Db2 native vectors: `VECTOR_DISTANCE` + in-database `TO_EMBEDDING`.
- **Hybrid** — the two fused with Reciprocal Rank Fusion (RRF), computed in one
  SQL query.

The point is the contrast: keyword search nails exact terms, vector search
catches paraphrases, and the fusion gets both. (The demo uses a shared-vocabulary
query and a paraphrase query to make this visible.)

## Prerequisite

Build the corpus first (one table with text + a text index + vectors):

```bash
python ../scripts/ingest.py path/to/your-document.pdf
```

## Run

```bash
pip install -r requirements.txt        # just ibm_db
python hybrid_search.py
```

Config (Db2 connection + watsonx model) comes from the project's `../.env`,
exactly like the main pipeline. To search interactively with your own query,
use the main tool instead: `python ../scripts/search.py "your question"`.
