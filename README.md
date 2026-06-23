# Hybrid Search on IBM Db2 12.1

Build a **hybrid search** corpus inside Db2 — keyword search *and* semantic
(vector) search over the same data — straight from a PDF.

## What this does / why it exists

Most "AI search" demos do only half the job. Real-world retrieval works best
when you combine **two** ways of finding text:

- **Lexical** (keyword / BM25) — great at exact terms: API names, error codes, identifiers.
- **Semantic** (vector embeddings) — great at meaning: paraphrases and synonyms.

This project ingests a PDF and stores each chunk in Db2 with **both**
representations, then searches with both and fuses the results. It uses
Db2 12.1's built-in features end to end:

- **Db2 Text Search** (OpenSearch-backed) for the lexical/BM25 index, and
- **native `VECTOR` columns + in-database `TO_EMBEDDING`** (via a registered
  watsonx.ai model) for the semantic index.

No external vector database, no separate search service to keep in sync — one
Db2 table is the source of truth.

## Architecture: one chunk, two representations

```
PDF ──Docling──▶ Markdown ──HybridChunker──▶ chunks
                                               │
                                               ▼
                         Db2 table  (chunk_id, chunk_text, embedding)
                                ├── chunk_text → Db2 Text Search index (OpenSearch)   → BM25 / CONTAINS · SCORE
                                └── embedding  → native VECTOR column (watsonx.ai)      → cosine · VECTOR_DISTANCE
                                               │
                                               ▼
                       search.py: run both legs, fuse with Reciprocal Rank Fusion (RRF)
```

Each chunk is **one row** holding its text, a stable `chunk_id`, a text-search
index entry, and its dense vector. Search runs the keyword leg and the vector
leg, then **RRF** merges the two rankings into one — so each leg covers the
other's blind spot.

## Prerequisites

- **IBM Db2 12.1** with the native `VECTOR` type and in-database embedding
  (model registration + `TO_EMBEDDING`). See [docs/db2-setup.md](docs/db2-setup.md).
- **OpenSearch**, installed and registered with Db2 Text Search.
  See [docs/opensearch-setup.md](docs/opensearch-setup.md).
- **Python 3.12** and the packages in [requirements.txt](requirements.txt).
- **watsonx.ai** access: an API key, a project id, and an embedding model
  (default: `sentence-transformers/all-minilm-l6-v2`, 384-dim).
- A system library for Docling's OpenCV dependency (`libGL.so.1`):
  - RHEL/Fedora: `sudo dnf install -y libglvnd-glx`
  - Debian/Ubuntu: `sudo apt-get install -y libgl1`

> Run the pipeline **as the Db2 instance owner** (e.g. `db2inst1`). The cleanup
> and text-index steps use the `db2ts` command-line tool, which must run locally
> as the instance owner.

## Setup

```bash
# 1. Clone
git clone <your-repo-url> hybrid-search
cd hybrid-search

# 2. Create a virtual environment and install dependencies.
python3 -m venv .venv
source .venv/bin/activate

# CPU-only PyTorch first (avoids large CUDA downloads):
pip install --index-url https://download.pytorch.org/whl/cpu torch==2.12.1 torchvision==0.27.1
pip install -r requirements.txt

# 3. Configure credentials. Copy the template and fill in real values.
cp .env.example .env
$EDITOR .env          # Db2 connection + watsonx.ai API key / project id
```

`.env` is git-ignored — your real credentials are never committed.

**One-time:** register OpenSearch with Db2 Text Search (creates the backend the
lexical index uses):

```bash
./scripts/setup_text_search.sh
```

## Usage — run the three scripts in order

The pipeline is three steps; **order matters**.

### 1. `cleanup` — start from a clean slate

```bash
./scripts/cleanup.sh
```

Drops the text-search index, then the chunks table (in that dependency order).
Idempotent — safe to run even if nothing exists yet.
**Leaves behind:** an empty schema, ready for a fresh ingest.

### 2. `ingest` — PDF → searchable corpus

```bash
python scripts/ingest.py --pdf path/to/your-document.pdf
```

Runs end to end: extract the PDF to Markdown (Docling) → chunk it
(HybridChunker, capped to the embedding model's token limit) → create the table
and load the chunks → build the Db2 Text Search lexical index → register the
watsonx.ai embedding model → add a `VECTOR` column and populate it with
in-database embeddings.
**Leaves behind:** one table (default `myschema.chunks`) where every row has
`chunk_id`, `chunk_text`, a text-search index entry, and an `embedding` vector.

> Lexical-only (skip watsonx): add `--skip-embedding`. Run `python scripts/ingest.py --help`
> for all options (schema/table names, token cap, vector dimension, etc.).

### 3. `search` — hybrid retrieval

```bash
python scripts/search.py "how do I turn text into vectors"
```

Runs the **lexical** leg (`CONTAINS` + `SCORE`) and the **vector** leg
(`VECTOR_DISTANCE` over a freshly embedded query), then fuses them with
**Reciprocal Rank Fusion**. Prints each leg's top hits and the fused ranking,
showing which legs found each chunk.
**Leaves behind:** nothing — it's read-only.

## Configuration

Everything is configurable via `.env` or CLI flags (CLI overrides `.env`):
Db2 connection, watsonx.ai credentials, schema/table names, chunk token cap,
and vector dimension. See [.env.example](.env.example) and each script's
`--help`.

## Repository layout

```
scripts/   cleanup.sh · ingest.py · search.py  (+ setup_text_search.sh prereq)
docs/      Db2 and OpenSearch setup notes, images
examples/  optional in-memory RRF demo (no Db2)
```

## Docs & examples

- [docs/db2-setup.md](docs/db2-setup.md) — install and prepare Db2 12.1.
- [docs/opensearch-setup.md](docs/opensearch-setup.md) — install OpenSearch and wire it to Db2 Text Search.
- [examples/](examples/) — an optional standalone in-memory hybrid-search demo
  (BM25 + dense + RRF, no Db2) for understanding the fusion idea.

## License

[Apache-2.0](LICENSE).
