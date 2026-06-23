# Examples

Supplementary material. None of this is required to run the main pipeline
(`scripts/cleanup.sh` → `scripts/ingest.py` → `scripts/search.py`).

## `inmemory-rrf/` — hybrid search from scratch, no database

A single, heavily-commented Python script that demonstrates the **core idea**
of hybrid search — BM25 + dense embeddings + Reciprocal Rank Fusion — entirely
in memory, with no Db2 and no OpenSearch. Great for understanding *why* fusion
works before adding the database machinery.

See [inmemory-rrf/README.md](inmemory-rrf/README.md) to run it.

## `lexical-ingest-demo.ipynb` — notebook walk-through of the lexical leg

A narrated, cell-by-cell version of the ingestion + lexical-search path, useful
for demos and learning.

> **Heads up — local demo, not turnkey.** This notebook was built for a specific
> machine: it shells out via `sudo` to helper scripts at hardcoded
> `/home/db2inst1/...` paths (deployed copies of the text-search scripts now in
> `backup/`). It won't run as-is elsewhere. Treat it as a readable reference for
> the steps; for an actual run, use `scripts/ingest.py`, which does the same work
> portably.

## `pdf_to_markdown.py` — standalone PDF → Markdown

The Docling extraction step on its own (the notebook's companion converter).
`scripts/ingest.py` already does this internally; this is here if you want just
the Markdown.

```bash
python pdf_to_markdown.py    # edit PDF_PATH inside, or adapt as needed
```
