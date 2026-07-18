#!/usr/bin/env python
"""Rebuild no-cat-sorted/ with sensible clusters. Fixes: double/half-time BPM,
trance!=psytrance, gangsta-is-hiphop, and incoherent by-sound crates.

Structure (all ADD-only; originals stay in no-category-music, nothing else touched):
  no-cat-sorted/
    by-genre/   <family>              real DJ genre families (11)
    by-bpm/     <band>                on FEEL bpm (half/double-time folded)
    by-sound/   <sonic>/ <NNbpm>      embedding clusters, each split by feel-bpm

Usage: ~/.rekordbox-venv/bin/python recluster_nocategory.py [--apply] [--k 18]
"""
import sys, re, shutil, datetime, sqlite3
import numpy as np
from pathlib import Path
from collections import Counter, defaultdict
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables
from pyrekordbox.config import get_config
from pyrekordbox.utils import get_rekordbox_pid

APPLY = "--apply" in sys.argv
K = int(sys.argv[sys.argv.index("--k") + 1]) if "--k" in sys.argv else 18
HERE = Path(__file__).resolve().parent
SRC_PID = "59257379"     # no-category-music
UNSORTED = "712840821"   # _unsorted (parent of no-cat-sorted)

# ---------- genre taxonomy ----------
def family(full):
    parent, _, sub = str(full or "?").partition("---"); s = sub.lower()
    if parent == "Hip Hop":                                   return "hip-hop"
    if parent != "Electronic":                                return "other"
    if "halftime" in s:                                       return "halftime"
    if "psy" in s or "goa" in s:                              return "psytrance"
    if "trance" in s:                                         return "trance"
    if "drum n bass" in s or "jungle" in s or "juke" in s or "footwork" in s: return "dnb-juke"
    if "dubstep" in s or "bassline" in s or "grime" in s or "riddim" in s:    return "dubstep-bass"
    if "hardcore" in s or "gabber" in s:                      return "hardcore"
    if "techno" in s or "tribal" in s or "industrial" in s:   return "techno"
    if "house" in s or "electro" in s:                        return "house"
    if any(k in s for k in ("ambient","downtempo","experimental","chillwave",
            "vaporwave","glitch","idm","drone","trip hop","new age","leftfield")):
                                                              return "ambient-downtempo"
    if s == "hip hop":                                        return "hip-hop"
    return "other"

GEN_ORDER = ["hip-hop","halftime","house","techno","trance","psytrance",
             "dubstep-bass","dnb-juke","hardcore","ambient-downtempo","other"]

# ---------- feel-bpm (fold half/double-time using genre anchor) ----------
def feel_bpm(fam, b):
    if not b: return None
    if fam in ("hip-hop", "halftime"):
        while b >= 118: b /= 2            # double-time detection -> real feel
    elif fam in ("house","techno","trance","psytrance","hardcore","dubstep-bass"):
        while b < 95:  b *= 2             # slow-detected 4/4 -> real feel
    elif fam == "dnb-juke":
        while b < 120: b *= 2
    return b

BPM_EDGES = [(0,75,"<75"),(75,85,"75-85"),(85,95,"85-95"),(95,110,"95-110"),
             (110,122,"110-122"),(122,128,"122-128"),(128,135,"128-135"),
             (135,145,"135-145"),(145,160,"145-160"),(160,175,"160-175"),(175,999,"175+")]
def bpm_band(b):
    if not b: return "unknown-bpm"
    for lo, hi, nm in BPM_EDGES:
        if lo <= b < hi: return nm
    return "175+"

def slug(s): return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"

# ---------- load ----------
db = Rekordbox6Database()
md = sqlite3.connect(f"file:{HERE/'metadata.sqlite'}?mode=ro", uri=True)
emb = {}
for rb, vec in md.execute("SELECT rb_id,vec FROM embedding"):
    v = np.frombuffer(vec, dtype=np.float32); emb[rb] = v/(np.linalg.norm(v)+1e-9)
gmap = {rb: t for rb, t in md.execute(
    "SELECT rb_id,txt FROM feature WHERE source='essentia_ml' AND name='genre_discogs'")}
title = {str(i): t for i, t in db.query(tables.DjmdContent.ID, tables.DjmdContent.Title).all()}
artist = {a.ID: (a.Name or "") for a in db.query(tables.DjmdArtist).all()}
aid = {str(i): artist.get(a, "") for i, a in db.query(tables.DjmdContent.ID, tables.DjmdContent.ArtistID).all()}

allcids = [s.ContentID for s in db.get_playlist_songs(PlaylistID=SRC_PID)]
rawbpm = {}
for c in allcids:
    ct = db.get_content(ID=c); rawbpm[c] = (ct.BPM/100.0) if (ct and ct.BPM) else None
fam = {c: family(gmap.get(c)) for c in allcids}
fbpm = {c: feel_bpm(fam[c], rawbpm[c]) for c in allcids}

def lbl(c): return f"{aid.get(c,'')} - {title.get(c,'?')}".strip(" -")[:46]

# ---------- by-genre ----------
by_genre = defaultdict(list)
for c in allcids: by_genre[fam[c]].append(c)

# ---------- by-bpm (feel) ----------
by_bpm = defaultdict(list)
for c in allcids: by_bpm[bpm_band(fbpm[c])].append(c)

# ---------- by-sound: global embedding clusters, each split by feel-bpm ----------
scids = [c for c in allcids if c in emb]
missing = [c for c in allcids if c not in emb]
X = np.stack([emb[c] for c in scids])
def kmeans(X, K, iters=40, seed=0):
    rng = np.random.default_rng(seed); C = X[rng.choice(len(X), K, replace=False)].copy()
    lab = np.zeros(len(X), int)
    for _ in range(iters):
        lab = (X @ C.T).argmax(1)
        for k in range(K):
            m = X[lab == k]
            if len(m): C[k] = m.mean(0); C[k] /= np.linalg.norm(C[k])+1e-9
    return lab
lab = kmeans(X, K)

# --- name each cluster by its dominant DISTINCTIVE sub-genre; generic clusters fall
#     back to their base family so they merge instead of becoming hip-hop-2/3/4.
GENERIC_SLUGS = {"house", "techno", "trance", "hip-hop", "instrumental", "gangsta",
                 "conscious", "electronic", "pop", "rock", "synth-pop",
                 "alternative-rock", "psychedelic-rock", "hard-rock", "blues-rock",
                 "hands-up", "hip-hop-1"}
def sub_slug(full): return slug(str(full or "").split("---")[-1])

raw_clusters = []
for k in range(K):
    mem = [scids[i] for i in range(len(scids)) if lab[i] == k]
    if not mem: continue
    n = len(mem)
    subcnt = Counter(sub_slug(gmap.get(c)) for c in mem)
    best, bestc = None, 0
    for sl, c in subcnt.items():
        if sl and sl not in GENERIC_SLUGS and c > bestc: best, bestc = sl, c
    if best and bestc >= max(12, 0.30 * n):        # distinctive enough -> own folder
        name = best
    else:                                          # generic -> merge into base family
        name = Counter(fam[c] for c in mem).most_common(1)[0][0]
    raw_clusters.append((name, mem))

merged = defaultdict(list)                          # same name across clusters -> one folder
for name, mem in raw_clusters: merged[name] += mem

def leafify(mem):
    """split a folder's members into tempo-contiguous <NNbpm> leaves (median feel-bpm)."""
    bands = defaultdict(list)
    for c in mem: bands[bpm_band(fbpm[c])].append(c)
    ordered = [bands[nm] for _,_,nm in BPM_EDGES if bands.get(nm)]
    MIN, groups, cur = 8, [], []
    for g in ordered:
        cur += g
        if len(cur) >= MIN: groups.append(cur); cur = []
    if cur: (groups[-1].extend(cur) if groups else groups.append(cur))
    leaves, seen = {}, Counter()
    for g in groups:
        med = int(round(np.median([fbpm[c] for c in g if fbpm[c]] or [0])))
        nm = f"{med}bpm"; seen[nm] += 1
        if seen[nm] > 1: nm = f"{nm}-{seen[nm]}"
        leaves[nm] = g
    if bands.get("unknown-bpm"): leaves["unknown-bpm"] = bands["unknown-bpm"]
    return leaves

sound = [{"name": nm, "n": len(mem), "bands": leafify(mem)} for nm, mem in merged.items()]
sound.sort(key=lambda s: (-s["n"]))

# ---------- report ----------
print(f"{'APPLY' if APPLY else 'DRY-RUN'} — recluster {len(allcids)} no-category tracks "
      f"(emb {len(scids)}, missing {len(missing)}), K={K}\n")
print("── by-genre ──")
for g in GEN_ORDER:
    if by_genre.get(g): print(f"   {g:<20} {len(by_genre[g]):>4}")
print("\n── by-bpm (feel) ──")
for _,_,nm in BPM_EDGES:
    if by_bpm.get(nm): print(f"   {nm:<12} {len(by_bpm[nm]):>4}")
if by_bpm.get("unknown-bpm"): print(f"   {'unknown-bpm':<12} {len(by_bpm['unknown-bpm']):>4}")
print("\n── by-sound (cluster / feel-bpm leaves) ──")
for s in sound:
    leaves = " ".join(f"{b}:{len(g)}" for b, g in sorted(s["bands"].items()))
    print(f"   📁 {s['name']:<26} {s['n']:>4}   [{leaves}]")
    ex = [lbl(c) for c in next(iter(s['bands'].values()))[:2]]
    print(f"        e.g. {ex[0]}" + (f"  /  {ex[1]}" if len(ex) > 1 else ""))
if missing: print(f"\n   (no-audio-data) {len(missing)}")

if not APPLY:
    print("\nDRY RUN — no writes. Re-run with --apply (rekordbox closed).")
    sys.exit(0)
if get_rekordbox_pid():
    sys.exit("Refusing — rekordbox is running. Quit it first.")

# ---------- apply ----------
# wipe EVERY existing no-cat-sorted (root or nested) so we never leave a duplicate
def rm(pid):
    for ch in db.query(tables.DjmdPlaylist).filter(tables.DjmdPlaylist.ParentID == pid).all():
        if ch.Attribute == 1: rm(ch.ID)
        db.delete_playlist(ch.ID)
for old in db.query(tables.DjmdPlaylist).filter(
        tables.DjmdPlaylist.Name == "no-cat-sorted",
        tables.DjmdPlaylist.rb_local_deleted == 0).all():
    rm(old.ID); db.delete_playlist(old.ID)
db.flush()
parent_id = db.create_playlist_folder("no-cat-sorted").ID   # parent omitted -> root

added = 0
fg = db.create_playlist_folder("by-genre", parent=parent_id).ID
for g in GEN_ORDER:
    if not by_genre.get(g): continue
    pl = db.create_playlist(g, parent=fg)
    for c in by_genre[g]: db.add_to_playlist(pl.ID, c); added += 1
fb = db.create_playlist_folder("by-bpm", parent=parent_id).ID
for _,_,nm in BPM_EDGES + [(0,0,"unknown-bpm")]:
    if not by_bpm.get(nm): continue
    pl = db.create_playlist(nm, parent=fb)
    for c in by_bpm[nm]: db.add_to_playlist(pl.ID, c); added += 1
fs = db.create_playlist_folder("by-sound", parent=parent_id).ID
for s in sound:
    folder = db.create_playlist_folder(s["name"], parent=fs).ID
    for band, g in sorted(s["bands"].items()):
        pl = db.create_playlist(band, parent=folder)
        for c in g: db.add_to_playlist(pl.ID, c); added += 1
if missing:
    pl = db.create_playlist("no-audio-data", parent=fs)
    for c in missing: db.add_to_playlist(pl.ID, c); added += 1

src = Path.home() / "Library/Pioneer/rekordbox/master.db"
bk = src.with_name(src.name + ".backup-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy2(src, bk); print(f"\nbackup -> {bk}")
db.commit()
print(f"Committed. Rebuilt no-cat-sorted ({added} entries). Originals untouched.")
