"use strict";

// Reads the frozen fixtures by default (offline). For queries not in the frozen
// set, it falls back to the live API (/api/search) when running ./ui/run.sh --live.

const state = { qid: null, mode: "lexical", showScores: false, record: null, query: "" };
let DATA = null;       // fixtures.json (by_query results + meta)
let DECK = [];         // featured queries shown in the rail
let DECK_ALL = [];     // full curated set (so typed queries still resolve)

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const TYPE_LABEL = { keyword: "Keyword", semantic: "Semantic", mixed: "Mixed" };
const legChip = (leg) => leg === "bm25"
  ? '<span class="chip chip-bm25">BM25</span>'
  : '<span class="chip chip-vector">Vector</span>';

/* ---------- boot ---------- */
async function boot() {
  DATA = await (await fetch("fixtures.json", { cache: "no-store" })).json();
  DECK_ALL = await (await fetch("queries.json", { cache: "no-store" })).json();
  DECK = DECK_ALL.filter((q) => q.featured);
  renderDeck();
  renderAgg();
  setMode("lexical");
  wire();
}

function renderDeck() {
  $("#deck-list").innerHTML = DECK.map((q) => `
    <li class="qrow" data-id="${q.id}">
      <div class="qtop">
        <span class="qtext">${esc(q.query)}</span>
        <span class="type type-${q.query_type}">${TYPE_LABEL[q.query_type] || q.query_type}</span>
      </div>
      <div class="gold-id">gold: <b>${q.gold_chunk_ids.map((c) => "#" + c).join(" ")}</b></div>
    </li>`).join("");
}

function renderAgg() {
  const k = DATA.meta.k, modes = ["lexical", "vector", "hybrid"];
  const label = { lexical: "BM25", vector: "Vector", hybrid: "Hybrid" };
  const cls = { lexical: "chip-bm25", vector: "chip-vector", hybrid: "chip-both" };
  const hit = { lexical: 0, vector: 0, hybrid: 0 };
  const mrr = { lexical: 0, vector: 0, hybrid: 0 };
  DECK.forEach((q) => {
    const rec = DATA.by_query[q.id];
    modes.forEach((m) => { const gr = rec[m].gold_rank; if (gr) { hit[m]++; mrr[m] += 1 / gr; } });
  });
  const n = DECK.length;
  $("#agg").innerHTML =
    `<span class="a-title">Gold answer in top ${k}, across ${n} eval queries</span>` +
    modes.map((m) => `<span class="a-item"><span class="chip ${cls[m]}">${label[m]}</span> <b>${hit[m]}/${n}</b></span>`).join("") +
    `<span class="a-note">MRR (illustrative): ${modes.map((m) => `${label[m]} ${(mrr[m] / n).toFixed(2)}`).join(" · ")}</span>`;
}

/* ---------- controls ---------- */
function wire() {
  $("#deck-list").addEventListener("click", (e) => {
    const li = e.target.closest(".qrow"); if (!li) return;
    const item = DECK_ALL.find((q) => String(q.id) === li.dataset.id);
    document.querySelectorAll(".qrow").forEach((r) => r.classList.toggle("active", r === li));
    state.qid = item.id; state.query = item.query;
    $("#searchbox").value = item.query;
    run();
  });
  $("#modes").addEventListener("click", (e) => {
    const b = e.target.closest(".mode"); if (!b) return;
    setMode(b.dataset.mode);
    if (state.record) render();        // re-render same query in the new strategy
  });
  $("#run").addEventListener("click", run);
  $("#searchbox").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  $("#t-scores").addEventListener("change", (e) => { state.showScores = e.target.checked; if (state.record) render(); });
  $("#t-agg").addEventListener("change", (e) => { $("#agg").hidden = !e.target.checked; });
  $("#output").addEventListener("click", (e) => {
    const row = e.target.closest(".row"); if (row) row.classList.toggle("open");
  });
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode").forEach((b) =>
    b.setAttribute("aria-checked", String(b.dataset.mode === mode)));
  $("#modes").classList.toggle("hybrid", mode === "hybrid");
  $("#fused-hint").hidden = mode !== "hybrid";
}

/* ---------- run + render ---------- */
async function run() {
  const text = $("#searchbox").value.trim();
  if (!text) return;
  const item = DECK_ALL.find((q) => q.query === text);
  let record = item ? DATA.by_query[item.id] : null;

  if (!record) {                       // not in fixtures → try live (--live only)
    try {
      const r = await fetch("/api/search?q=" + encodeURIComponent(text));
      if (r.ok) record = await r.json();
    } catch (_) { /* offline */ }
  }
  if (!record) {
    $("#output").innerHTML = `<p class="placeholder">This query isn't in the frozen demo set.
      Pick one on the left, or run <code>./ui/run.sh --live</code> for ad-hoc search.</p>`;
    state.record = null; return;
  }
  state.record = record;
  render();
}

function render() {
  const rec = state.record;
  $("#output").innerHTML = state.mode === "hybrid"
    ? comparisonHtml(rec)
    : heroBadge(rec[state.mode]) + rowsHtml(rec[state.mode].results, false) + scoreNote();
}

function heroBadge(resp) {
  const k = resp.k, gold = resp.gold_chunk_ids.map((c) => "#" + c).join(" ");
  if (resp.gold_rank) {
    return `<div class="hero found">✓ Gold answer at <span class="rank-num">#${resp.gold_rank}</span>
      <small>(${gold})</small></div>`;
  }
  return `<div class="hero missed">✕ Gold answer — not in top ${k} <small>(${gold})</small></div>`;
}

function rowsHtml(results, showProvenance) {
  return `<div class="rows">${results.map((r) => rowHtml(r, showProvenance)).join("")}</div>`;
}

function rowHtml(r, showProvenance) {
  const tags = showProvenance && r.found_by && r.found_by.length
    ? `<span class="tags">${r.found_by.map(legChip).join("")}</span>` : "";
  const goldFlag = r.is_gold ? `<span class="gold-flag">★ gold</span>` : "";
  return `<div class="row ${r.is_gold ? "gold" : ""}">
    <div class="rline">
      <span class="rank">${r.rank}</span>
      <span class="cid">#${r.chunk_id}</span>
      <span class="snip">${esc(r.snippet)}</span>
      ${tags}${goldFlag}
    </div>
    ${scoresHtml(r)}
    <div class="full">${esc(r.text)}</div>
  </div>`;
}

function scoresHtml(r) {
  if (!state.showScores) return "";
  const parts = [];
  if (r.score_type === "bm25") parts.push(`BM25 <b>${r.score}</b>`);
  else if (r.score_type === "cosine") parts.push(`cosine <b>${r.score}</b>`);
  else {
    parts.push(`fused <b>${r.score}</b>`);
    if (r.per_leg) {
      const b = r.per_leg.bm25, v = r.per_leg.vector;
      parts.push(`BM25${b.gated ? " (gated)" : ""} raw ${b.score ?? "—"} · norm ${b.norm} → +${r.contribution.bm25}`);
      parts.push(`Vector${v.gated ? " (gated)" : ""} raw ${v.score ?? "—"} · norm ${v.norm} → +${r.contribution.vector}`);
    }
  }
  return `<div class="scores">${parts.map((p) => `<span>${p}</span>`).join("")}</div>`;
}

function scoreNote() {
  if (!state.showScores) return "";
  return `<p class="score-note">BM25 and cosine are different scales, so each leg is normalized
    (score ÷ its best) and a low-confidence leg is gated out before the weighted sum.
    The fusion ranks by normalized score, not raw score.</p>`;
}

function comparisonHtml(rec) {
  const col = (title, dot, cls, resp, prov) => `
    <div class="col ${cls}">
      <h3><span class="dot ${dot}"></span>${title}</h3>
      ${heroBadge(resp)}
      ${rowsHtml(resp.results, prov)}
    </div>`;
  return `<div class="compare">
    ${col("Lexical", "bm25", "col-lexical", rec.lexical, false)}
    ${col("Vector", "vec", "col-vector", rec.vector, false)}
    ${col("Hybrid", "hyb", "col-hybrid", rec.hybrid, true)}
  </div>${scoreNote()}`;
}

boot();
