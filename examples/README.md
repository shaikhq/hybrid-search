# Hybrid Search demo (BM25 + dense vectors + RRF) over real IBM Db2 ML docs

A single-file, heavily-commented learning script that shows three retrieval
"legs" running over the **same** corpus of real IBM Db2 12.1 *machine-learning
stored-procedure* documentation, side by side:

| leg | how it scores | good at |
|-----|---------------|---------|
| **BM25** | lexical / keyword (inverted-index relevance) | exact tokens: procedure names, SQL codes |
| **Dense** | `all-MiniLM-L6-v2` embeddings + cosine | meaning / paraphrases |
| **RRF-fused** | Reciprocal Rank Fusion of the two (written from scratch) | strong on both |

The goal is to **see complementarity**: lexical queries visibly favour BM25,
paraphrased "what's my problem" queries favour the vectors, and RRF holds up on
both.

## What it does
1. Fetches the seed topic (`r_ml_stored_procedures.html`) from IBM's content API,
   parses its child links, and fetches the sub-topics (data exploration,
   transformation, model building/tuning/evaluation/inferencing/management, and
   troubleshooting). Failed fetches are skipped; it prints how many pages it got.
2. Strips HTML to text, chunks into ~150-word overlapping passages
   (`id / title / url / text`), prints the chunk count, and prints a few **exact
   candidate tokens** it discovered so you can sanity-check the lexical queries.
3. Runs ~10 demo queries (LEXICAL + SEMANTIC groups) and prints
   **BM25 | Vector | RRF** top-5 lists side by side with raw scores, plus a
   one-line verdict (which leg won, did RRF recover the right doc).

## Caching (so the second run is fast)
- Raw HTML pages → `./cache/<file>.html`
- Embedding matrix → `./cache/embeddings.npy` (auto-invalidated if the corpus or
  model changes, via a hash in `./cache/embeddings.meta`)

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate

# CPU-only PyTorch (smaller, no CUDA):
pip install torch==2.12.1 --index-url https://download.pytorch.org/whl/cpu

pip install -r requirements.txt

python hybrid_search.py            # full 10-query, three-column comparison
python hybrid_search.py --talk     # minimal 3-beat story for a live audience
```

`--talk` is the version to present to people who know vector search and keyword
search but have never seen them combined. For each of two queries it ranks the
**distinct topics** each engine returns and marks the one correct topic with ✓ —
so you watch *where* the ✓ lands:

1. an exact-token query (`x86`, a literal platform token) → BM25 ranks the right
   page **#1**, vectors scatter it to the **bottom** of the list;
2. a paraphrased question (`get a first look at what is inside my dataset`) →
   vectors rank it **#1**, BM25 ranks it **lower** (no shared words).

The ✓'s form a diagonal: each engine surfaces the right answer high on the query
that suits it and low on the other. The **Hybrid (RRF)** column keeps the correct
topic at **#1 for both** — it covers each engine's blind spot.

First run downloads the `all-MiniLM-L6-v2` model (~80 MB) from Hugging Face and
fetches the docs. Later runs read from `./cache` and are fast.

## Installing Db2

The demo above needs no Db2 instance. If you want a local Db2 12.1 to experiment
with, [docs/db2-setup.md](../docs/db2-setup.md) is a minimal, beginner-friendly install and
setup guide (create the instance, start it, load a sample database, run a test
query).

## Notes
- No Db2 instance, no external vector DB, no web framework — everything is
  in-memory + NumPy.
- RRF is implemented in `reciprocal_rank_fusion()` so the math is visible:
  `score(doc) = Σ 1/(k + rank)`, `k=60`. It fuses **ranks** precisely because
  BM25 scores (unbounded) and cosine scores (~ -1..1) are on incomparable scales.
- The fetched text is **IBM copyrighted material**, cached locally for personal
  study only.
