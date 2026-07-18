#!/usr/bin/env python
"""Rebuild the BPM views of no-cat-sorted using ACTUAL stored rekordbox BPM
(run bpm_halftime_fix.py first so half-time tracks are already corrected):

  by-bpm/    strict bands — every track's stored BPM lies inside its band
  by-sound/  keeps existing cluster folders/membership; re-splits each cluster's
             leaves so every leaf's tracks are within +/-8% of the leaf's mean BPM
  NEW no-cat tracks not yet in any by-sound cluster are assigned to the nearest
  cluster centroid (1280-dim cosine, floor 0.55) — progressive, no re-clustering
  also deletes any stray empty playlist directly under by-sound

by-genre and cluster folder names/membership are untouched.
Backs up master.db; refuses if rekordbox is running.

Usage: ~/.rekordbox-venv/bin/python rebuild_nocat_bpm.py [--apply]
"""
import sys, shutil, datetime
import numpy as np
from pathlib import Path
from collections import defaultdict
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables
from pyrekordbox.utils import get_rekordbox_pid

APPLY = "--apply" in sys.argv
SRC_PID = "59257379"  # no-category-music

BPM_EDGES = [(0,75,"<75"),(75,85,"75-85"),(85,95,"85-95"),(95,110,"95-110"),
             (110,122,"110-122"),(122,128,"122-128"),(128,135,"128-135"),
             (135,145,"135-145"),(145,160,"145-160"),(160,175,"160-175"),(175,999,"175+")]
def bpm_band(b):
    if not b: return "unknown-bpm"
    for lo, hi, nm in BPM_EDGES:
        if lo <= b < hi: return nm
    return "175+"

db = Rekordbox6Database()

# ---------- locate live structure ----------
def child(pid, name, folder=None):
    q = db.query(tables.DjmdPlaylist).filter(
        tables.DjmdPlaylist.ParentID == pid, tables.DjmdPlaylist.Name == name,
        tables.DjmdPlaylist.rb_local_deleted == 0)
    return q.one_or_none()

root = db.query(tables.DjmdPlaylist).filter(
    tables.DjmdPlaylist.Name == "no-cat-sorted", tables.DjmdPlaylist.ParentID == "root",
    tables.DjmdPlaylist.rb_local_deleted == 0).one_or_none() or \
    db.query(tables.DjmdPlaylist).filter(
    tables.DjmdPlaylist.Name == "no-cat-sorted",
    tables.DjmdPlaylist.rb_local_deleted == 0).one()
f_bpm = child(root.ID, "by-bpm"); f_snd = child(root.ID, "by-sound")
assert f_bpm and f_snd, "by-bpm / by-sound not found under no-cat-sorted"

# ---------- stored BPM for all no-category tracks ----------
allcids = [s.ContentID for s in db.get_playlist_songs(PlaylistID=SRC_PID)]
bpm = {}
for c in allcids:
    ct = db.get_content(ID=c)
    bpm[c] = (ct.BPM / 100.0) if (ct and ct.BPM) else None

# ---------- by-bpm: strict bands on stored BPM ----------
by_bpm = defaultdict(list)
for c in allcids: by_bpm[bpm_band(bpm[c])].append(c)

# ---------- by-sound: read existing clusters, re-split leaves at +/-8% ----------
clusters = []   # (folder_row, [cids])
stray_empty = []
for ch in db.query(tables.DjmdPlaylist).filter(
        tables.DjmdPlaylist.ParentID == f_snd.ID,
        tables.DjmdPlaylist.rb_local_deleted == 0).all():
    if ch.Attribute == 1:   # folder = a sound cluster
        mem = []
        for leaf in db.query(tables.DjmdPlaylist).filter(
                tables.DjmdPlaylist.ParentID == ch.ID,
                tables.DjmdPlaylist.rb_local_deleted == 0).all():
            mem += [s.ContentID for s in db.get_playlist_songs(PlaylistID=leaf.ID)]
        clusters.append((ch, sorted(set(mem), key=mem.index)))
    else:                   # plain playlist directly under by-sound (e.g. no-audio-data)
        songs = [s.ContentID for s in db.get_playlist_songs(PlaylistID=ch.ID)]
        if not songs: stray_empty.append(ch)
        else: clusters.append((ch, songs))

# ---------- assign NEW tracks to nearest existing cluster (progressive) ----------
import sqlite3
HERE = Path(__file__).resolve().parent
in_cluster = set()
for _, mem in clusters: in_cluster.update(mem)
unassigned = [c for c in allcids if c not in in_cluster]
assigned_report = {}
if unassigned:
    emb = {}
    need = set(unassigned) | in_cluster
    for rb, vec in sqlite3.connect(f"file:{HERE/'metadata.sqlite'}?mode=ro", uri=True)\
            .execute("SELECT rb_id, vec FROM embedding"):
        if rb in need:
            v = np.frombuffer(vec, dtype=np.float32); emb[rb] = v / (np.linalg.norm(v) + 1e-9)
    cents = []
    for row, mem in clusters:
        vs = [emb[c] for c in mem if c in emb]
        if vs:
            m = np.mean(vs, axis=0); cents.append((row, mem, m / (np.linalg.norm(m) + 1e-9)))
    n_new = 0
    for c in unassigned:
        if c not in emb: continue
        sims = [(float(emb[c] @ cent), row, mem) for row, mem, cent in cents]
        best, row, mem = max(sims, key=lambda t: t[0])
        if best >= 0.55:
            mem.append(c); n_new += 1
            assigned_report[row.Name] = assigned_report.get(row.Name, 0) + 1
    no_emb = sum(1 for c in unassigned if c not in emb)
    print(f"new-track assignment: {n_new} joined nearest cluster, "
          f"{len(unassigned)-n_new-no_emb} below 0.55 floor (by-bpm only), {no_emb} no embedding")

def split_8pct(mem):
    """Greedy split of sorted-by-BPM members into leaves where every track is
    within 8% of the leaf's mean BPM. Unknown-BPM tracks -> own leaf."""
    known = sorted((c for c in mem if bpm.get(c)), key=lambda c: bpm[c])
    unknown = [c for c in mem if not bpm.get(c)]
    leaves, cur = [], []
    for c in known:
        trial = cur + [c]
        m = float(np.mean([bpm[x] for x in trial]))
        if cur and not all(abs(bpm[x] - m) / m <= 0.08 for x in trial):
            leaves.append(cur); cur = [c]
        else:
            cur = trial
    if cur: leaves.append(cur)
    named, seen = [], defaultdict(int)
    for g in leaves:
        nm = f"{int(round(np.mean([bpm[x] for x in g])))}bpm"
        seen[nm] += 1
        if seen[nm] > 1: nm = f"{nm}-{seen[nm]}"
        named.append((nm, g))
    if unknown: named.append(("unknown-bpm", unknown))
    return named

plan = [(row, mem, split_8pct(mem)) for row, mem in clusters]

# ---------- report ----------
print(f"{'APPLY' if APPLY else 'DRY-RUN'} — rebuild by-bpm (strict) + by-sound leaves (±8%)\n")
print("── by-bpm (strict stored BPM) ──")
for _,_,nm in BPM_EDGES:
    if by_bpm.get(nm): print(f"   {nm:<12} {len(by_bpm[nm]):>4}")
if by_bpm.get("unknown-bpm"): print(f"   {'unknown-bpm':<12} {len(by_bpm['unknown-bpm']):>4}")
print("\n── by-sound leaves ──")
if assigned_report:
    print("   (new joiners: " + ", ".join(f"{k}+{v}" for k, v in sorted(assigned_report.items())) + ")")
for row, mem, leaves in sorted(plan, key=lambda p: -len(p[1])):
    spans = []
    for nm, g in leaves:
        bs = [bpm[c] for c in g if bpm.get(c)]
        spans.append(f"{nm}:{len(g)}" + (f"[{min(bs):.0f}-{max(bs):.0f}]" if bs else ""))
    print(f"   📁 {row.Name:<20} {len(mem):>4}   " + "  ".join(spans))
if stray_empty:
    print("\nwill delete empty:", ", ".join(p.Name for p in stray_empty))

if not APPLY:
    print("\nDRY RUN — no writes. Re-run with --apply (rekordbox closed).")
    sys.exit(0)
if get_rekordbox_pid():
    sys.exit("Refusing — rekordbox is running. Quit it first.")

src = Path.home() / "Library/Pioneer/rekordbox/master.db"
bk = src.with_name(src.name + ".backup-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy2(src, bk); print(f"\nbackup -> {bk}")

added = 0
# rebuild by-bpm playlists
for pl in db.query(tables.DjmdPlaylist).filter(
        tables.DjmdPlaylist.ParentID == f_bpm.ID,
        tables.DjmdPlaylist.rb_local_deleted == 0).all():
    db.delete_playlist(pl.ID)
db.flush()
for _,_,nm in BPM_EDGES + [(0,0,"unknown-bpm")]:
    if not by_bpm.get(nm): continue
    pl = db.create_playlist(nm, parent=f_bpm.ID)
    for c in by_bpm[nm]: db.add_to_playlist(pl.ID, c); added += 1
# rebuild each cluster's leaves in place
for row, mem, leaves in plan:
    if row.Attribute != 1: continue   # skip plain playlists (kept as-is)
    for leaf in db.query(tables.DjmdPlaylist).filter(
            tables.DjmdPlaylist.ParentID == row.ID,
            tables.DjmdPlaylist.rb_local_deleted == 0).all():
        db.delete_playlist(leaf.ID)
    db.flush()
    for nm, g in leaves:
        pl = db.create_playlist(nm, parent=row.ID)
        for c in g: db.add_to_playlist(pl.ID, c); added += 1
for p in stray_empty:
    db.delete_playlist(p.ID)
db.commit()
print(f"Committed. {added} track entries; deleted {len(stray_empty)} empty playlist(s).")
