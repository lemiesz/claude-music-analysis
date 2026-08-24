"""Generate the SVG figures for directions-not-dimensions.md."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

SURF = "#fcfcfb"; INK = "#0b0b0b"; INK2 = "#52514e"; MUT = "#898781"
AXIS = "#c3c2b7"; GRID = "#e1e0d9"; BLUE = "#2a78d6"; ORANGE = "#eb6834"
RAMP = ["#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#1c5cab", "#104281",
        "#0d366b"]  # steps 100-700
CMAP = LinearSegmentedColormap.from_list("seqblue", RAMP)

plt.rcParams.update({
    "font.family": "sans-serif", "font.size": 10,
    "text.color": INK, "axes.edgecolor": AXIS, "axes.labelcolor": INK2,
    "xtick.color": MUT, "ytick.color": MUT, "axes.linewidth": 1.0,
    "svg.fonttype": "none",
})

def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)

# ---------- Figure 1: the mechanism ----------
rng = np.random.default_rng(7)
g = rng.uniform(10, 90, 42); e = rng.uniform(0, 4, 42)
x, y = g + e, g - e
fig, (a, b) = plt.subplots(1, 2, figsize=(9.6, 4.3), facecolor=SURF,
                           gridspec_kw={"width_ratios": [1.15, 1]})
for ax in (a, b):
    ax.set_facecolor(SURF); style(ax)

oa = np.argsort(e)
sc = a.scatter(x[oa], y[oa], c=e[oa], cmap=CMAP, s=42, edgecolors=SURF, linewidths=1.2,
               vmin=0, vmax=4, zorder=3)
a.set_xlabel("x = g + e"); a.set_ylabel("y = g − e")
a.set_title("A. Both axes show the music type g.\nThe color (energy e) looks random.",
            fontsize=10, color=INK, loc="left")
mid = np.array([60, 26])
a.annotate("", xy=mid + [16, 16], xytext=mid,
           arrowprops=dict(arrowstyle="-|>", color=MUT, lw=1.8))
a.text(*(mid + [17, 9]), "music-type\ndirection (1, 1)", color=INK2, fontsize=8.5)
a.annotate("", xy=mid + [13, -13], xytext=mid,
           arrowprops=dict(arrowstyle="-|>", color=ORANGE, lw=2.2))
a.text(*(mid + [-6, -19]), "energy direction\nw = (1, −1)", color=ORANGE,
       fontsize=8.5)

proj = (x - y) / 2.0
jit = rng.uniform(-0.8, 0.8, len(proj))
order = np.argsort(e)
b.scatter(proj[order], jit[order], c=e[order], cmap=CMAP, s=46,
          edgecolors=SURF, linewidths=1.2, vmin=0, vmax=4, zorder=3)
b.set_yticks([]); b.set_ylim(-2.6, 2.6)
b.spines["left"].set_visible(False)
b.set_xlabel("projection onto w:  (x − y) / 2")
b.set_title("B. The projection onto w recovers e exactly:\nposition = e, light on the left, dark on the right.",
            fontsize=10, color=INK, loc="left")
cb = fig.colorbar(sc, ax=b, shrink=0.8, aspect=18, pad=0.04)
cb.set_label("energy e", color=INK2); cb.outline.set_edgecolor(AXIS)
cb.ax.tick_params(color=MUT, labelcolor=MUT)
fig.tight_layout()
fig.savefig("fig1-mechanism.svg", facecolor=SURF, bbox_inches="tight")

# ---------- Figure 2: parts vs whole ----------
names = ["PC2", "PC3", "PC4", "PC1", "direction w\n(all 1280 dims)"]
vals = [0.212, 0.276, 0.335, 0.403, 0.843]
cols = [BLUE] * 4 + [ORANGE]
fig, ax = plt.subplots(figsize=(6.8, 3.0), facecolor=SURF)
ax.set_facecolor(SURF); style(ax); ax.spines["left"].set_visible(False)
bars = ax.barh(names, vals, height=0.62, color=cols, zorder=3)
for r, v in zip(bars, vals):
    ax.text(v + 0.012, r.get_y() + r.get_height() / 2, f"{v:.3f}",
            va="center", color=INK2, fontsize=9)
ax.set_xlim(0, 0.98); ax.set_xlabel("correlation with the energy (absolute)")
ax.xaxis.grid(True, color=GRID, lw=0.8, zorder=0)
ax.set_axisbelow(True); ax.tick_params(left=False)
ax.set_title("No principal component holds the energy. The full direction does.",
             fontsize=10.5, color=INK, loc="left")
fig.tight_layout()
fig.savefig("fig2-parts-vs-whole.svg", facecolor=SURF, bbox_inches="tight")

# ---------- Figure 3: projection destroys the direction ----------
names = ["UMAP 2D map", "UMAP 3D map", "full space\n(1280 dims)"]
vals = [0.468, 0.539, 0.843]
fig, ax = plt.subplots(figsize=(6.8, 2.6), facecolor=SURF)
ax.set_facecolor(SURF); style(ax); ax.spines["left"].set_visible(False)
bars = ax.barh(names, vals, height=0.58, color=[BLUE] * 3, zorder=3)
for r, v in zip(bars, vals):
    ax.text(v + 0.012, r.get_y() + r.get_height() / 2, f"ρ = {v:.3f}",
            va="center", color=INK2, fontsize=9)
ax.set_xlim(0, 0.98)
ax.set_xlabel("how well the energy direction reads (Spearman ρ)")
ax.xaxis.grid(True, color=GRID, lw=0.8, zorder=0)
ax.set_axisbelow(True); ax.tick_params(left=False)
ax.set_title("The 2D map keeps the large directions and drops the small one.",
             fontsize=10.5, color=INK, loc="left")
fig.tight_layout()
fig.savefig("fig3-map-loses-direction.svg", facecolor=SURF, bbox_inches="tight")

# ---------- Figure 4: what a subspace holds ----------
# r is the vector of the PC1..PC4 correlations with the energy (Section 4).
# The PCs are mutually uncorrelated, so the best direction in their span
# reaches |r|, and a random unit direction in that span reaches |r|/sqrt(4).
r = np.array([0.403, 0.212, -0.276, 0.335])
best_sub = float(np.linalg.norm(r))
rand_sub = best_sub / np.sqrt(len(r))
names = ["a random direction\nin the 4D subspace", "best single component\n(PC1)",
         "best direction in\nspan{PC1...PC4}", "full direction w\n(all 1280 dims)"]
vals = [rand_sub, 0.403, best_sub, 0.843]
cols = [MUT, BLUE, RAMP[4], ORANGE]
fig, ax = plt.subplots(figsize=(6.8, 3.2), facecolor=SURF)
ax.set_facecolor(SURF); style(ax); ax.spines["left"].set_visible(False)
bars = ax.barh(names, vals, height=0.6, color=cols, zorder=3)
for bar, v in zip(bars, vals):
    ax.text(v + 0.012, bar.get_y() + bar.get_height() / 2, f"{v:.3f}",
            va="center", color=INK2, fontsize=9)
ax.set_xlim(0, 0.98); ax.set_xlabel("correlation with the energy (absolute)")
ax.xaxis.grid(True, color=GRID, lw=0.8, zorder=0)
ax.set_axisbelow(True); ax.tick_params(left=False)
ax.set_title("A subspace beats its best axis. It does not match the full space.",
             fontsize=10.5, color=INK, loc="left")
fig.tight_layout()
fig.savefig("fig4-subspace-ladder.svg", facecolor=SURF, bbox_inches="tight")

print("done")
