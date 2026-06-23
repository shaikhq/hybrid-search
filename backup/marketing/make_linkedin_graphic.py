import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

W, H = 10.8, 13.5  # 1080 x 1350 @100dpi (LinkedIn 4:5 portrait)
fig, ax = plt.subplots(figsize=(W, H), dpi=100)
ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
fig.patch.set_facecolor("#f8fafc")

INK, MUTE = "#0f172a", "#475569"
BLUE  = dict(fc="#dbeafe", ec="#2563eb")
ORNG  = dict(fc="#ffedd5", ec="#ea580c")
GREY  = dict(fc="#eef2f7", ec="#64748b")
GREEN = dict(fc="#dcfce7", ec="#16a34a")
H_BOX, W_BOX = 1.12, 8.8

def box(y, title, sub, style, num):
    x = (W - W_BOX) / 2
    ax.add_patch(FancyBboxPatch((x, y), W_BOX, H_BOX,
        boxstyle="round,pad=0.02,rounding_size=0.18",
        fc=style["fc"], ec=style["ec"], lw=2.3, zorder=2))
    ax.text(W/2, y + H_BOX*0.62, title, ha="center", va="center",
            fontsize=18.5, fontweight="bold", color=INK, zorder=3)
    ax.text(W/2, y + H_BOX*0.24, sub, ha="center", va="center",
            fontsize=12.5, color=MUTE, zorder=3)
    ax.add_patch(plt.Circle((x + 0.52, y + H_BOX - 0.30), 0.27,
        fc=style["ec"], ec="white", lw=1.5, zorder=4))
    ax.text(x + 0.52, y + H_BOX - 0.30, str(num), ha="center", va="center",
            fontsize=13, fontweight="bold", color="white", zorder=5)

def arrow(y_top, y_bot, label):
    ax.add_patch(FancyArrowPatch((W/2, y_top), (W/2, y_bot),
        arrowstyle="-|>", mutation_scale=22, lw=2.4, color="#94a3b8", zorder=1))
    ax.text(W/2 + 0.3, (y_top + y_bot)/2, label, ha="left", va="center",
            fontsize=11.5, style="italic", color=MUTE, zorder=3)

# Title
ax.text(W/2, 12.95, "From PDF to Searchable Knowledge",
        ha="center", fontsize=27, fontweight="bold", color=INK)
ax.text(W/2, 12.42, "Document ingestion for hybrid search — the lexical leg",
        ha="center", fontsize=14.5, color=MUTE)
ax.add_patch(plt.Rectangle((1.1, 12.18), W-2.2, 0.04, color="#2563eb"))

steps = [
    ("PDF document", "the raw source", GREY),
    ("Markdown", "Docling extracts clean, structured text", GREY),
    ("Chunks", "HybridChunker — structure- & token-aware (256 tok)", GREY),
    ("Db2 table  ·  pdf_chunks", "the single source of truth", BLUE),
    ("Db2 Text Search  +  OpenSearch", "Db2 builds & queries the lexical index for you", ORNG),
    ("SQL search  ·  CONTAINS + SCORE", "ranked, relevance-scored results — just SQL", GREEN),
]
labels = [None, "extract", "chunk", "store", "index", "query"]
top, pitch = 11.05, 1.60
ys = [top - i*pitch for i in range(len(steps))]
for i, (t, s, st) in enumerate(steps):
    box(ys[i], t, s, st, num=i+1)
    if i > 0:
        arrow(ys[i-1], ys[i] + H_BOX, labels[i])

# Footer
ax.add_patch(FancyBboxPatch((1.1, 0.50), W-2.2, 1.05,
    boxstyle="round,pad=0.02,rounding_size=0.15", fc="#0f172a", ec="none", zorder=2))
ax.text(W/2, 1.18, "Part 1 of building hybrid search",
        ha="center", va="center", fontsize=15, fontweight="bold", color="white", zorder=3)
ax.text(W/2, 0.80, "Next:  embeddings  →  vector search  →  RRF fusion  =  hybrid search",
        ha="center", va="center", fontsize=12, color="#cbd5e1", zorder=3)

fig.savefig("/home/shaikhq/hybrid-search/linkedin_workflow.png",
            facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.25)
print("wrote /home/shaikhq/hybrid-search/linkedin_workflow.png")
