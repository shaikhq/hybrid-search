# Evaluation Results — Hybrid Search

Search-quality results produced by the evaluation harness.

- **Reproduce:** `./scripts/eval.sh`
- **Golden eval set** (relevance judgments / *qrels*): [scripts/eval.py](../scripts/eval.py)
  — 16 queries, each paired with its known-relevant chunk(s)
- **Corpus:** IBM Db2 12.1.5 LLM-integration reference (101 chunks)
- **Date:** 2026-06-23
- **Verdict:** ✅ PASS

Each number is the **position of the correct answer** in that mode's results
(1 = top, lower is better). **—** = not found in the top 10.

## Scores by mode

| mode | MRR | Recall@5 | Hits@1 |
|---|---|---|---|
| lexical (keyword) | 0.511 | 0.688 | 0.375 |
| vector (semantic) | 0.677 | 0.812 | 0.562 |
| **hybrid (fusion)** | **0.887** | **0.969** | **0.812** |

Plain-English (hybrid mode): correct answer at **#1 in 13/16 (81%)**, in the
**top 5 in ~16/16 (97%)**, and **never missed** (0/16). Keyword-only got #1 in
6/16; vector-only in 9/16 — so the fusion is clearly best.

## Per-query results

| # | Question | Best suited to | Lex | Vec | Hyb | Analysis |
|---|---|---|---|---|---|---|
| 1 | `42615` | keyword (code) | 1 | — | 1 | Vector can't embed a bare code → correctly gated out; keyword wins. ✅ |
| 2 | `42613` | keyword (code) | 1 | — | 1 | Keyword nails it, vector ignored. ✅ |
| 3 | `42601` | keyword (code) | 5 | — | 5 | Only a weak keyword match exists; hybrid keeps it but low. ⚠️ weakest case |
| 4 | `38555` | keyword (code) | 1 | 1 | 1 | Code sits in a code-table chunk, so both modes find it. ✅ |
| 5 | `REASONING_EFFORT` | identifier | — | 1 | 1 | Keyword fails (text splits the underscore); vector rescues. ✅ |
| 6 | `REPETITION_PENALTY` | identifier | — | 1 | 1 | Vector saves it. ✅ |
| 7 | what privilege is needed to use TO_EMBEDDING | hybrid | 4 | 2 | 2 | Answer spread over 2 chunks; ranks the sibling first. ⚠️ top-2 |
| 8 | how do I change the API key on an existing model | hybrid | 1 | 1 | 1 | Both modes strong. ✅ |
| 9 | how do I transfer ownership of a model to another user | hybrid | 1 | 1 | 1 | Both strong. ✅ |
| 10 | how do I register an external model | hybrid | 7 | 2 | 1 | Fusion beats both legs (7 & 2 → 1). ✅ |
| 11 | how do I drop an external model | hybrid | 1 | 1 | 1 | Both strong. ✅ |
| 12 | how can I make the model stop generating at a certain phrase | vector | 4 | 2 | 1 | Fusion beats both legs (4 & 2 → 1). ✅ |
| 13 | how do I turn text into vectors | vector | 2 | 1 | 1 | Vector leads, hybrid keeps #1. ✅ |
| 14 | what controls the randomness of the output | vector | 3 | 1 | 1 | Vector finds TEMPERATURE. ✅ |
| 15 | limit the maximum length of the generated text | vector | 2 | 3 | 2 | MAX_NEW_TOKENS vs TIME_LIMIT overlap confuses it. ⚠️ top-2 |
| 16 | how long can text generation run before timing out | vector | — | 1 | 1 | Keyword fails; vector rescues. ✅ |

## Summary

- ✅ correct answer at #1: **13 / 16**
- ⚠️ correct answer in top 5 (not #1): **3 / 16** (#3, #7, #15)
- ❌ missed entirely: **0 / 16**

## Observations

- **The gate works** — on bare codes (#1–3) the noisy vector mode is dropped, so
  keyword wins cleanly.
- **The rescue works** — when keyword fails on identifiers/paraphrases (#5, #6,
  #16), vector carries the result.
- **Fusion adds real value** — in #10 and #12 the combined result is better than
  either mode alone.
- The 3 imperfect cases all trace to the same root cause: **fragmented chunks +
  a small (384-d) embedding model.**

## Next levers (re-run `./scripts/eval.sh` after each to confirm a gain)

1. **Better chunking** — merge heading fragments and prepend section context.
2. **Stronger embedding model** — swap the 384-d model for a 768-d+ one, re-embed.
3. **Cross-encoder reranker** — rerank the fused top-k.

> Notes: these results depend on the corpus, the embedding model, and the fusion
> knobs in `.env` (`HYBRID_*`). Re-run after any change to refresh this report.
