#!/usr/bin/env python
"""Precompute everything the visualization app needs from the stored embeddings:
  - UMAP 2D projection (the map layout)
  - HDBSCAN clusters (sonic families)
  - L2-normalized embeddings (for fast cosine nearest-neighbors / constellation)
  - per-track metadata (label, genre, intensity, moods) for hover + coloring

Saves: viz_arrays.npz  (xy, emb_norm, cluster)  and  viz_meta.csv
Run:  ~/.rbx-viz/bin/python viz_precompute.py
"""
import os, sqlite3
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "metadata.sqlite")
d = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

# --- embeddings ---
rows = d.execute("SELECT rb_id, vec FROM embedding").fetchall()
ids = [r[0] for r in rows]
emb = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
print(f"loaded {emb.shape[0]} embeddings ({emb.shape[1]}-dim)")

# --- per-track features ---
feat = {}
for rb, name, num, txt in d.execute(
        "SELECT rb_id,name,num,txt FROM feature WHERE source='essentia_ml'"):
    feat.setdefault(rb, {})[name] = num if num is not None else txt

# --- labels: prefer track table (artist/title), fall back to filename ---
label = {}
for rb, ar, ti in d.execute("SELECT rb_id,artist,title FROM track"):
    if ar or ti:
        label[rb] = f"{ar or '?'} - {ti or '?'}"
for rb, p in d.execute("SELECT rb_id,path FROM track_files"):
    if rb not in label:
        label[rb] = os.path.splitext(os.path.basename(p))[0]

def intensity(f):
    a = f.get("mood_aggressive", 0) or 0
    return ("peak" if a > 0.60 else "driving" if a > 0.35 else "groovy" if a > 0.15 else "warmup")
def moodstr(f):
    m = []
    if (f.get("mood_happy", 0) or 0) > 0.40: m.append("happy")
    if (f.get("mood_relaxed", 0) or 0) > 0.60: m.append("chill")
    if (f.get("mood_sad", 0) or 0) > 0.12 or ((f.get("mood_aggressive", 0) or 0) > 0.55 and (f.get("mood_happy", 1) or 1) < 0.10): m.append("dark")
    if (f.get("mood_party", 1) or 1) < 0.60: m.append("listening")
    return ",".join(m) or "—"

meta = pd.DataFrame({
    "rb_id": ids,
    "label": [label.get(i, i)[:60] for i in ids],
    "genre": [str((feat.get(i, {}).get("genre_discogs") or "?")).replace("Electronic---", "") for i in ids],
    "intensity": [intensity(feat.get(i, {})) for i in ids],
    "mood": [moodstr(feat.get(i, {})) for i in ids],
    "aggressive": [round(feat.get(i, {}).get("mood_aggressive", 0) or 0, 2) for i in ids],
    "relaxed": [round(feat.get(i, {}).get("mood_relaxed", 0) or 0, 2) for i in ids],
    "happy": [round(feat.get(i, {}).get("mood_happy", 0) or 0, 2) for i in ids],
})

# --- UMAP 2D + 3D ---
import umap
print("running UMAP 2D (cosine)...")
xy = umap.UMAP(n_components=2, n_neighbors=15, min_dist=0.12, metric="cosine", random_state=42).fit_transform(emb)
print("running UMAP 3D (cosine)...")
xyz = umap.UMAP(n_components=3, n_neighbors=15, min_dist=0.12, metric="cosine", random_state=42).fit_transform(emb)

# --- clusters: HDBSCAN in EMBEDDING space (10-dim UMAP -> leaf + kNN) ---
# Clustering on the 2D layout inherits projection distortion. Swept 2026-07:
# eom merges families (purity .52); leaf is pure (.84) but marks ~55% noise;
# leaf + kNN-assigning noise to the nearest cluster wins on both axes
# (purity .750 vs old .641, zero noise, 48 clusters).
from sklearn.cluster import HDBSCAN
from sklearn.neighbors import KNeighborsClassifier
print("running UMAP 10D reduction for clustering (cosine)...")
red = umap.UMAP(n_components=10, n_neighbors=30, min_dist=0.0,
                metric="cosine", random_state=42).fit_transform(emb)
print("clustering (HDBSCAN leaf on 10-dim + kNN noise assignment)...")
cl = HDBSCAN(min_cluster_size=60, min_samples=10,
             cluster_selection_method="leaf").fit_predict(red)
_m = cl >= 0
if 0 < _m.sum() < len(cl):
    cl[~_m] = KNeighborsClassifier(n_neighbors=10).fit(red[_m], cl[_m]).predict(red[~_m])
meta["cluster"] = ["noise" if c < 0 else f"c{c}" for c in cl]
print(f"clusters found: {len(set(cl)) - (1 if -1 in cl else 0)}  (noise: {(cl<0).sum()})")

# --- normalized embeddings for cosine NN / constellation ---
emb_norm = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)

np.savez_compressed(os.path.join(HERE, "viz_arrays.npz"),
                    xy=xy.astype(np.float32), xyz=xyz.astype(np.float32),
                    emb_norm=emb_norm.astype(np.float32), cluster=cl.astype(np.int32))
meta.to_csv(os.path.join(HERE, "viz_meta.csv"), index=False)
print(f"saved viz_arrays.npz + viz_meta.csv  ({len(meta)} tracks)")
