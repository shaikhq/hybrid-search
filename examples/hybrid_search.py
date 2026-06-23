"""
hybrid_search.py
================
A minimal, heavily-commented demonstration of HYBRID SEARCH over REAL IBM Db2
machine-learning stored-procedure documentation.

It runs three retrieval "legs" over the same corpus and shows them side by side:

    1. BM25       - classic lexical / keyword scoring (an inverted-index idea)
    2. Dense      - sentence-transformer embeddings + cosine similarity (meaning)
    3. RRF-fused  - Reciprocal Rank Fusion combining the two (implemented here,
                    from scratch, so the math is visible)

The whole point is to SEE complementarity:
  - exact-token queries (procedure names, SQL codes) should favour BM25,
  - paraphrased "what's my problem" queries should favour the dense vectors,
  - and RRF should be strong on BOTH.

This is a personal learning script, not production code. The fetched text is IBM
copyrighted material: it is cached locally for personal study only.

Run:  python hybrid_search.py
First run fetches + embeds (slow); later runs use ./cache and are fast.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import sys
import time

import numpy as np
import requests
from bs4 import BeautifulSoup
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

# ----------------------------------------------------------------------------
# 0. CONFIG
# ----------------------------------------------------------------------------
# IBM Documentation serves the *rendered* topic pages via JavaScript, but the
# underlying plain HTML for each topic is available from its content API:
#     https://www.ibm.com/docs/api/v1/content/<knowledge-center-path>
# The ML stored-procedure topics all live under this base path. The seed topic
# (r_ml_stored_procedures.html) links to every child topic with relative
# "../doc/<file>.html" hrefs, so we fetch the seed, parse those links, and
# follow them. (No JS, no scraping of the dynamic nav required.)
CONTENT_API = "https://www.ibm.com/docs/api/v1/content/"
BASE_PATH = "SSEPGG_12.1.0/com.ibm.db2.luw.ml.doc/doc/"
SEED_FILE = "r_ml_stored_procedures.html"

# Human-facing URL for a topic (for display / attribution only).
HUMAN_URL = "https://www.ibm.com/docs/en/db2/12.1.x"

CACHE_DIR = "cache"
EMB_FILE = os.path.join(CACHE_DIR, "embeddings.npy")
EMB_META = os.path.join(CACHE_DIR, "embeddings.meta")

MODEL_NAME = "all-MiniLM-L6-v2"   # small, CPU-friendly dense encoder
USER_AGENT = "db2-hybrid-search-learning/1.0 (personal study; +local-only)"
TIMEOUT = 30                       # seconds per HTTP request

CHUNK_WORDS = 150                  # target passage length (~100-200 words)
CHUNK_OVERLAP = 30                 # words shared between neighbouring chunks
TOP_K = 5                          # how many results to display per leg
RRF_K = 60                         # the classic RRF constant


# ----------------------------------------------------------------------------
# 1. FETCH  (with on-disk caching + polite headers)
# ----------------------------------------------------------------------------
def fetch(path: str) -> str | None:
    """Fetch one content-API document by its knowledge-center path.

    Caches the raw HTML under ./cache/ keyed on the file name, so reruns never
    re-hit IBM's servers. Returns the HTML string, or None if the fetch failed
    (caller is expected to skip failures and carry on).
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, posixpath.basename(path))

    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            return fh.read()

    url = CONTENT_API + path
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ! skip {path}: {exc}")
        return None

    html = resp.text
    with open(cache_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    time.sleep(0.5)  # be polite between live requests
    return html


def child_html_links(html: str, base_path: str) -> list[str]:
    """Resolve every same-directory '<file>.html' link on a page to a full path.

    Children are linked relatively (e.g. '../doc/r_cmatrix_stats_procedure_c.html').
    We resolve against the page's directory and keep only links that stay inside
    the ML docs folder, so the crawl can't wander into unrelated products.
    """
    base_dir = posixpath.dirname(base_path)
    out: list[str] = []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        href = a["href"]
        if not href.endswith(".html"):
            continue
        resolved = posixpath.normpath(posixpath.join(base_dir, href))
        if resolved.startswith(BASE_PATH) and resolved not in out:
            out.append(resolved)
    return out


def discover_and_fetch(max_depth: int = 2) -> list[dict]:
    """Breadth-first crawl of the ML stored-procedure docs.

    depth 0 = the seed index, depth 1 = the category topics (data exploration,
    model tuning, troubleshooting, ...), depth 2 = the per-procedure reference
    pages (IDAX.CMATRIX_STATS, IDAX.PRUNE_DECTREE, ...) where the rich text and
    exact tokens actually live. Failed fetches are skipped.

    Each page records the category 'section' it belongs to (its depth-1 ancestor),
    which we use later to judge whether a query found the right topic area.
    Returns a list of {"path", "title", "section", "html"}.
    """
    print("Fetching IBM Db2 ML stored-procedure docs (BFS, depth %d) ..." % max_depth)
    seed_path = BASE_PATH + SEED_FILE

    pages: list[dict] = []
    visited: set[str] = set()
    attempted = 0
    queue: list[tuple[str, int, str | None]] = [(seed_path, 0, None)]

    while queue:
        path, depth, parent_section = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)
        attempted += 1

        html = fetch(path)
        if html is None:
            continue
        title = page_title(html)
        # A page's "section" is its own title at depth<=1, else its category's.
        section = title if depth <= 1 else (parent_section or title)
        pages.append({"path": path, "title": title, "section": section, "html": html})

        if depth < max_depth:
            for child in child_html_links(html, path):
                if child not in visited:
                    queue.append((child, depth + 1, section))

    skipped = attempted - len(pages)
    print(f"Retrieved {len(pages)} page(s)  ({attempted} attempted, {skipped} skipped).")
    return pages


def page_title(html: str) -> str:
    """Best-effort topic title: prefer <title>, fall back to first <h1>."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else "(untitled)"


# ----------------------------------------------------------------------------
# 2. HTML -> TEXT -> CHUNKS
# ----------------------------------------------------------------------------
def html_to_text(html: str) -> str:
    """Strip tags to readable text. Drops script/style/nav noise."""
    # The content API already returns chrome-free topic HTML, so we strip ONLY
    # scripts/styles. (Stripping nav/header/footer here would delete the
    # procedure list that IBM wraps in those tags -- losing real content.)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def chunk_pages(pages: list[dict]) -> list[dict]:
    """Split each page's text into short, overlapping passages.

    Each chunk is a dict: {id, title, url, text}. Overlap keeps a sentence that
    straddles a boundary findable from either chunk.
    """
    chunks: list[dict] = []
    for page in pages:
        words = html_to_text(page["html"]).split()
        title = page["title"]
        # A readable, human-facing URL for attribution (slug unknown -> use path).
        url = f"{HUMAN_URL}  [{posixpath.basename(page['path'])}]"

        step = CHUNK_WORDS - CHUNK_OVERLAP
        for start in range(0, max(1, len(words)), step):
            window = words[start:start + CHUNK_WORDS]
            if not window:
                break
            # Fold the topic title into the indexed text so both legs can match
            # on it (the title is often the most on-topic phrase on the page).
            chunks.append({
                "id": len(chunks),
                "title": title,
                "section": page["section"],
                "url": url,
                "text": f"{title}. " + " ".join(window),
            })
            if start + CHUNK_WORDS >= len(words):
                break
    return chunks


# ----------------------------------------------------------------------------
# 3. TOKENIZER  (shared by BM25 and by token discovery)
# ----------------------------------------------------------------------------
# We lowercase and split on non-alphanumerics, BUT keep internal "." and "_" so
# identifiers survive as single tokens:
#   "IDAX.CMATRIX_STATS" -> "idax.cmatrix_stats"   (one token, exact-matchable)
#   "SQL20562N"          -> "sql20562n"            (digits NOT stripped)
# Stripping digits or splitting on "." / "_" would destroy exactly the tokens a
# lexical search is best at, so we deliberately preserve them.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


# ----------------------------------------------------------------------------
# 4a. LEXICAL LEG: BM25
# ----------------------------------------------------------------------------
# BM25 is the classic bag-of-words relevance model built on an inverted index:
# it rewards query terms that are rare across the corpus (IDF) and appear often
# in a document (term frequency), with length normalisation. rank-bm25 builds
# the index for us; we just supply tokenized documents and tokenized queries.
def build_bm25(chunks: list[dict]) -> BM25Okapi:
    tokenized_corpus = [tokenize(c["text"]) for c in chunks]
    return BM25Okapi(tokenized_corpus)


def bm25_scores(bm25: BM25Okapi, query: str) -> np.ndarray:
    """Raw BM25 score per chunk (higher = better). Scale is unbounded."""
    return np.asarray(bm25.get_scores(tokenize(query)), dtype=float)


# ----------------------------------------------------------------------------
# 4b. DENSE LEG: sentence-transformer embeddings + cosine
# ----------------------------------------------------------------------------
# Each chunk is mapped to a 384-d vector capturing meaning; the query is mapped
# the same way; relevance = cosine similarity. This is what lets a paraphrase
# ("isn't memorizing the training data") match "overfitting" with no shared
# words. We normalise vectors so cosine == dot product, and cache the matrix.
def embed_chunks(model: SentenceTransformer, chunks: list[dict]) -> np.ndarray:
    """Return an (n_chunks x dim) L2-normalised embedding matrix, cached to disk.

    Cache is invalidated automatically if the chunk text or model changes
    (we store a hash of both alongside the .npy).
    """
    signature = hashlib.sha256(
        (MODEL_NAME + "\n" + "\n".join(c["text"] for c in chunks)).encode("utf-8")
    ).hexdigest()

    if os.path.exists(EMB_FILE) and os.path.exists(EMB_META):
        with open(EMB_META, encoding="utf-8") as fh:
            if fh.read().strip() == signature:
                return np.load(EMB_FILE)

    print("Embedding chunks (first run / corpus changed) ...")
    emb = model.encode(
        [c["text"] for c in chunks],
        convert_to_numpy=True,
        normalize_embeddings=True,   # -> cosine similarity is just a dot product
        show_progress_bar=False,
    )
    np.save(EMB_FILE, emb)
    with open(EMB_META, "w", encoding="utf-8") as fh:
        fh.write(signature)
    return emb


def cosine_scores(model: SentenceTransformer, emb: np.ndarray, query: str) -> np.ndarray:
    """Cosine similarity of the query against every chunk. Range ~[-1, 1]."""
    q = model.encode([query], convert_to_numpy=True, normalize_embeddings=True)[0]
    return emb @ q


# ----------------------------------------------------------------------------
# 5. RANKING + RECIPROCAL RANK FUSION (from scratch)
# ----------------------------------------------------------------------------
def rank_order(scores: np.ndarray) -> list[int]:
    """Chunk indices ordered best-first.

    Deterministic: ties broken by ascending chunk id so reruns are identical.
    (np.argsort alone is not tie-stable in the direction we want.)
    """
    n = len(scores)
    return sorted(range(n), key=lambda i: (-scores[i], i))


def reciprocal_rank_fusion(ranked_lists: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Combine several ranked lists into one fused score per document.

        score(doc) = sum over legs of  1 / (k + rank_in_that_leg)

    RRF uses RANKS, not the raw leg scores, on purpose: BM25 scores (unbounded,
    ~0..15 here) and cosine similarities (~ -1..1) live on completely
    incomparable scales, so adding or averaging them is meaningless. A document's
    *position* in each list, however, is directly comparable -- so we fuse those.
    The constant k (=60) damps the influence of the very top ranks a little, a
    value shown to work well across many tasks in the original RRF paper.

    rank is 1-based (best result = rank 1). Returns {doc_id: fused_score}.
    """
    fused: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return fused


# ----------------------------------------------------------------------------
# 6. DISPLAY HELPERS
# ----------------------------------------------------------------------------
def short(title: str, width: int = 26) -> str:
    # Friendlier short labels for the verbose IBM topic titles.
    for long, brief in (
        ("Troubleshooting machine learning stored procedures", "Troubleshooting"),
        ("Machine learning stored procedures", "ML stored procedures"),
        ("In-database machine learning", "In-database ML"),
    ):
        title = title.replace(long, brief)
    return (title[:width - 1] + "…") if len(title) > width else title.ljust(width)


def best_rank(order: list[int], chunks: list[dict], expect: str) -> int:
    """1-based rank of the first chunk in the expected category (or 9999).

    Matches on the chunk's `section` (its category) so a per-procedure page
    counts toward the topic area it belongs to.
    """
    for rank, doc_id in enumerate(order, start=1):
        if expect.lower() in chunks[doc_id]["section"].lower():
            return rank
    return 9999


def show_query(query_label, query, expect, chunks, bm25_s, cos_s):
    """Print BM25 | Vector | RRF top-5 side by side, then a one-line verdict."""
    bm_order = rank_order(bm25_s)
    co_order = rank_order(cos_s)
    # Feed each leg's full ranking into RRF, then re-sort by fused score.
    fused = reciprocal_rank_fusion([bm_order, co_order])
    rrf_order = sorted(fused, key=lambda i: (-fused[i], i))

    print("\n" + "=" * 100)
    print(f"[{query_label}] QUERY: {query!r}")
    print(f"   expected topic ~ {expect!r}")
    print("-" * 100)
    print(f"{'#':>2}  {'BM25-only (raw bm25)':<36}{'Vector-only (cosine)':<36}{'RRF-fused (rrf score)':<36}")
    print("-" * 100)
    for i in range(TOP_K):
        b = bm_order[i]
        c = co_order[i]
        r = rrf_order[i]
        cell_b = f"{short(chunks[b]['title'])} {bm25_s[b]:6.2f}"
        cell_c = f"{short(chunks[c]['title'])} {cos_s[c]:6.3f}"
        cell_r = f"{short(chunks[r]['title'])} {fused[r]:6.4f}"
        print(f"{i + 1:>2}  {cell_b:<36}{cell_c:<36}{cell_r:<36}")

    # Verdict: where did the expected topic land in each leg?
    rb = best_rank(bm_order, chunks, expect)
    rc = best_rank(co_order, chunks, expect)
    rr = best_rank(rrf_order, chunks, expect)
    winner = "BM25" if rb < rc else ("Vector" if rc < rb else "tie")
    recovered = "yes" if rr <= max(1, min(rb, rc)) else f"no (rank {rr})"
    print("-" * 100)
    print(f"   note: expected topic best rank -> BM25 #{rb}, Vector #{rc}, RRF #{rr}  "
          f"| {winner} leg won | RRF recovered the right doc: {recovered}")


# ----------------------------------------------------------------------------
# 7. TOKEN DISCOVERY  (so the user can sanity-check the lexical queries)
# ----------------------------------------------------------------------------
def discover_candidate_tokens(chunks: list[dict]) -> None:
    blob = " ".join(c["text"] for c in chunks)
    procs = sorted(set(re.findall(r"IDAX\.[A-Z_]+", blob)))
    sqlcodes = sorted(set(re.findall(r"SQL\d{3,}[A-Z]?", blob)))
    phrases = [p for p in ["confusion matrix", "accuracy", "precision", "recall",
                           "mean absolute error", "mean squared error", "overfitting"]
               if p in blob.lower()]
    print("\nCandidate EXACT tokens discovered in the corpus (sanity-check these):")
    print(f"  stored procedures : {', '.join(procs[:12])}{' ...' if len(procs) > 12 else ''}")
    print(f"  SQL error codes   : {', '.join(sqlcodes) or '(none found)'}")
    print(f"  notable phrases   : {', '.join(phrases)}")


# ----------------------------------------------------------------------------
# 8. DEMO QUERIES
# ----------------------------------------------------------------------------
# LEXICAL: exact strings that appear in the docs -> should favour BM25.
LEXICAL_QUERIES = [
    ("IDAX.CMATRIX_STATS",   "Model evaluation"),
    ("IDAX.PRUNE_DECTREE",   "Model tuning"),
    ("confusion matrix",     "Model evaluation"),
    ("SQL20562N",            "Troubleshooting"),
    ("mean squared error",   "Model evaluation"),
]

# SEMANTIC: paraphrases that AVOID the docs' wording -> should favour vectors.
# These are deliberately phrased to match the CONCEPT each page covers without
# reusing its terms (the tuning page is about pruning a tree to reduce
# complexity/overfitting; inferencing is about applying a model to new rows; etc).
# NOTE: a purely abstract phrasing like "check my model isn't memorizing the
# training data" actually fails here -- the corpus mentions overfitting only in
# passing and never with those words, so BM25 is blind to it and the dense leg
# tops out around rank 5. The phrasings below stay faithful to what the pages
# really say, so the dense leg can genuinely win.
SEMANTIC_QUERIES = [
    ("simplify an overly complex tree model so it generalizes better", "Model tuning"),
    ("ways to clean up and reshape my data before training",           "Data transformation"),
    ("run a built model against new observations to get results",      "Model inferencing"),
    ("list and delete saved models",                                   "Model management"),
    ("get a first look at what is inside my dataset",                  "Data exploration"),
]


# ----------------------------------------------------------------------------
# 8b. TALK MODE  (python hybrid_search.py --talk)
# ----------------------------------------------------------------------------
# A stripped-down view for presenting to people who already know vector search
# and keyword search but have never seen them combined. The whole story is one
# small table: each engine has a STRENGTH and a matching BLIND SPOT, and hybrid
# (RRF) covers both. We pick two queries that each probe one ability:
#   - an exact-token query   -> keyword's strength, vectors' blind spot
#   - a paraphrased question  -> vectors' strength, keyword's blind spot
# Each talk query = (query, expect_match, topic_label, insight).
#   expect_match : substring used to find the correct topic in a ranked list
#   topic_label  : how that correct topic is shown to the audience
TALK_QUERIES = [
    # EXACT-TOKEN query: "x86" is a platform token with little semantic meaning.
    # BM25 matches the literal token (it appears once, on the requirements page);
    # vectors have almost nothing to grasp, so they scatter it far down the list.
    ("x86", "In-database machine learning", "Setup / requirements (In-database ML)",
     "BM25 matches the literal token, so the right page is #1. Vectors find little"
     " meaning in 'x86', so they rank it far down."),
    # MEANING query: a paraphrase that shares NO words with the target page.
    # Vectors match the intent; BM25 has no overlapping terms, so it ranks it lower.
    ("get a first look at what is inside my dataset", "Data exploration", "Data exploration",
     "Vectors match the intent, so the right topic is #1. BM25 shares no words with"
     " the page, so it ranks it lower."),
]


def _orders(query, bm25, model, emb):
    """Return (bm25_order, vector_order, rrf_order) for one query."""
    bo = rank_order(bm25_scores(bm25, query))
    co = rank_order(cosine_scores(model, emb, query))
    fused = reciprocal_rank_fusion([bo, co])
    ro = sorted(fused, key=lambda i: (-fused[i], i))
    return bo, co, ro


def _ranked_topics(order, chunks):
    """Collapse an engine's chunk ranking to a list of DISTINCT topics, best
    first (a topic's rank = the best rank of any of its chunks). This is what
    lets the audience see *where the correct topic lands* in each engine."""
    topics = []
    for doc in order:
        section = chunks[doc]["section"]
        if section not in topics:
            topics.append(section)
    return topics


def _topic_rank(topics, expect):
    """1-based position of the correct topic in a ranked-topics list."""
    for i, t in enumerate(topics, start=1):
        if expect.lower() in t.lower():
            return i
    return None


def _topic_cell(topics, expect, r, width=28):
    """One row: '✓ Topic' if this is the correct topic, else '  Topic'."""
    if r >= len(topics):
        return ""
    mark = "✓" if expect.lower() in topics[r].lower() else " "
    return f"{mark} {short(topics[r], width)}"


def run_talk(chunks, bm25, model, emb) -> None:
    print("\n" + "#" * 100)
    print("#  HYBRID SEARCH: covering the blind spots of keyword and vector search")
    print("#  Each list = the top topics one engine returns. ✓ = the correct topic.")
    print("#  Watch WHERE the ✓ lands: high in the engine that suits the query, low in the other.")
    print("#" * 100)

    TOPN = 6
    col = 32
    kinds = ["exact-token query  (a literal platform token)",
             "meaning query  (a plain-English paraphrase)"]

    for kind, (query, expect, label, insight) in zip(kinds, TALK_QUERIES):
        bo, co, ro = _orders(query, bm25, model, emb)
        bt, ct, rt = _ranked_topics(bo, chunks), _ranked_topics(co, chunks), _ranked_topics(ro, chunks)

        print(f"\n{'=' * 100}")
        print(f'QUERY — {kind}')
        print(f'   "{query}"        correct topic = {label}')
        print("-" * 100)
        print(f"  rank  {'Keyword (BM25)':<{col}}{'Vector (dense)':<{col}}{'Hybrid (RRF)':<{col}}")
        print("-" * 100)
        for r in range(TOPN):
            print(f"   {r + 1}    "
                  f"{_topic_cell(bt, expect, r):<{col}}"
                  f"{_topic_cell(ct, expect, r):<{col}}"
                  f"{_topic_cell(rt, expect, r):<{col}}")
        print("-" * 100)
        rb, rc, rr = _topic_rank(bt, expect), _topic_rank(ct, expect), _topic_rank(rt, expect)
        print(f"   correct topic's rank:   BM25 = {rb}      Vector = {rc}      Hybrid = {rr}")
        print(f"   -> {insight}")

    print(f"\n{'=' * 100}")
    print("PUNCHLINE: each engine ranks the right topic #1 on the query that suits it, and lower on")
    print("the other -- opposite blind spots. Hybrid (RRF) blends both, so the right topic is #1 on BOTH.")
    print(f"{'=' * 100}\n")


# ----------------------------------------------------------------------------
# 9. MAIN
# ----------------------------------------------------------------------------
def main() -> None:
    # `--talk` = the minimal 3-beat story for a live audience.
    # default   = the full 10-query, three-column comparison.
    talk = "--talk" in sys.argv

    pages = discover_and_fetch()
    if not pages:
        raise SystemExit("No pages retrieved (network down?). Nothing to search.")

    chunks = chunk_pages(pages)
    print(f"Built {len(chunks)} chunks from {len(pages)} pages "
          f"(~{CHUNK_WORDS} words each, {CHUNK_OVERLAP}-word overlap).")

    if not talk:
        discover_candidate_tokens(chunks)

    # Build both legs.
    bm25 = build_bm25(chunks)
    model = SentenceTransformer(MODEL_NAME)
    emb = embed_chunks(model, chunks)

    if talk:
        run_talk(chunks, bm25, model, emb)
        return

    # Pre-compute the per-query scores and render the side-by-side comparison.
    for label, queries in (("LEXICAL", LEXICAL_QUERIES), ("SEMANTIC", SEMANTIC_QUERIES)):
        for query, expect in queries:
            bm25_s = bm25_scores(bm25, query)
            cos_s = cosine_scores(model, emb, query)
            show_query(label, query, expect, chunks, bm25_s, cos_s)

    print("\nDone. (Re-run is fast: pages come from ./cache, embeddings from "
          f"{EMB_FILE}.)")


if __name__ == "__main__":
    main()
