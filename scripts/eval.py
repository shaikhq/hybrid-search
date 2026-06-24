#!/usr/bin/env python3
"""
eval.py — measure retrieval quality on a small GOLDEN EVAL SET.

The golden eval set below is a set of relevance judgments (a.k.a. *qrels* in
information-retrieval terms): each query is paired with its known-relevant
chunk(s). Reports MRR, Recall@K and Hits@1 for each leg (lexical, vector,
hybrid), plus a per-query table of where the first relevant result landed. Run it
after any change to chunking, the embedding model, the fusion gates, or the
weights, and judge the change by the numbers instead of by eyeballing one query.

    ./scripts/eval.sh        # runs as the Db2 instance owner, local connection

The labels below are for the IBM Db2 12.1.5 LLM-integration reference doc; replace
them for your own corpus.
"""

import ibm_db
import hybrid_core as h

K = 5  # recall / top-k cutoff

# Golden eval set / relevance judgments (qrels): (query, {relevant chunk_id, ...}).
# A starting, hand-labeled set — extend it as you find more representative queries.
GOLDEN = [
    # exact codes / identifiers -> keyword should win
    ("42615", {27, 33, 36}),
    ("42613", {45}),
    ("42601", {45}),
    ("38555", {90}),
    ("REASONING_EFFORT", {34}),
    ("REPETITION_PENALTY", {22}),
    # distinctive term + natural language -> hybrid
    ("what privilege is needed to use TO_EMBEDDING", {55, 82}),
    ("how do I change the API key on an existing model", {30, 46}),
    ("how do I transfer ownership of a model to another user", {51, 52}),
    ("how do I register an external model", {4}),
    ("how do I drop an external model", {47}),
    # paraphrases whose words aren't in the answer -> vector
    ("how can I make the model stop generating at a certain phrase", {23, 35}),
    ("how do I turn text into vectors", {14, 80}),
    ("what controls the randomness of the output", {24}),
    ("limit the maximum length of the generated text", {19}),
    ("how long can text generation run before timing out", {25}),
]


def first_relevant_rank(ranked, relevant):
    for i, cid in enumerate(ranked, start=1):
        if cid in relevant:
            return i
    return None


def summarize(results):
    """results: [(ranked_ids, relevant_set)] -> (MRR, Recall@K, Hits@1)."""
    rr = recall = hit1 = 0.0
    for ranked, relevant in results:
        rank = first_relevant_rank(ranked, relevant)
        rr += 1.0 / rank if rank else 0.0
        recall += len(set(ranked[:K]) & relevant) / len(relevant)
        hit1 += 1.0 if ranked[:1] and ranked[0] in relevant else 0.0
    n = len(results) or 1
    return rr / n, recall / n, hit1 / n


def main():
    conn = h.connect()
    legs = {"lexical": h.lexical, "vector": h.vector, "hybrid": h.hybrid}
    collected = {name: [] for name in legs}
    per_query = []

    for query, relevant in GOLDEN:
        ranks = {}
        for name, fn in legs.items():
            ranked = [cid for cid, _ in fn(conn, query, 10)]
            collected[name].append((ranked, relevant))
            ranks[name] = first_relevant_rank(ranked, relevant)
        per_query.append((query, ranks))
    ibm_db.close(conn)

    def cell(r):
        return (str(r) if r else "-").rjust(3)

    print(f"\nGolden set: {len(GOLDEN)} queries — rank of first relevant result "
          f"(lower is better, '-' = not in top 10)\n")
    print(f"  {'query':52}  lex  vec  hyb")
    print(f"  {'-' * 52}  ---  ---  ---")
    for query, ranks in per_query:
        print(f"  {query[:52]:52}  {cell(ranks['lexical'])}  "
              f"{cell(ranks['vector'])}  {cell(ranks['hybrid'])}")

    print(f"\n  {'leg':8}  {'MRR':>6}  {('Recall@' + str(K)):>9}  {'Hits@1':>7}")
    print(f"  {'-' * 8}  {'-' * 6}  {'-' * 9}  {'-' * 7}")
    for name in legs:
        mrr, recall, hit1 = summarize(collected[name])
        print(f"  {name:8}  {mrr:6.3f}  {recall:9.3f}  {hit1:7.3f}")
    print()


if __name__ == "__main__":
    main()
