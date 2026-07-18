#!/usr/bin/env python
"""Populate rekordbox's Matching feature (djmdRecommendLike) from the audio
embedding index that powers the visualizer.

Pair rules:
  - full 1280-dim cosine similarity >= 0.90 (this space's "really similar" band;
    0.85 is near the random-pair 99th pctile and would produce millions of pairs)
  - BPM compatible: within 8% at 1:1, 2:1 or 1:2 (post-halftime-fix values)
  - ranked by sim + 0.02 bonus for Camelot-compatible key (same / +-1 / relative)
  - top 6 partners per track, deduped to one row per unordered pair

Backs up master.db; refuses if rekordbox is running.

DIFF-BASED (2026-07-04): instead of wipe-and-reinsert, this now diffs the
desired pair set against existing script rows (UUID==ID): new pairs are
INSERTed with fresh USNs, dropped pairs are SOFT-deleted (rb_local_deleted=1
+ fresh USN — rekordbox's own delete mechanism, so incremental device-sync
should propagate the removal), unchanged rows are left untouched (no USN
churn -> sync only carries the delta). --rebuild forces the old full
wipe-and-reinsert.

Usage: ~/.rekordbox-venv/bin/python build_matching.py [--apply] [--rebuild] [--top 6] [--sim 0.90]
"""
import sys, uuid, shutil, datetime, sqlite3
import numpy as np
from pathlib import Path
from collections import defaultdict
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables
from pyrekordbox.utils import get_rekordbox_pid
from sqlalchemy import text

APPLY = "--apply" in sys.argv
TOP = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 6
SIM = float(sys.argv[sys.argv.index("--sim") + 1]) if "--sim" in sys.argv else 0.90
HERE = Path(__file__).resolve().parent

CAMELOT = {"Abm":(1,"A"),"B":(1,"B"),"Ebm":(2,"A"),"F#":(2,"B"),"Bbm":(3,"A"),"Db":(3,"B"),
           "Fm":(4,"A"),"Ab":(4,"B"),"Cm":(5,"A"),"Eb":(5,"B"),"Gm":(6,"A"),"Bb":(6,"B"),
           "Dm":(7,"A"),"F":(7,"B"),"Am":(8,"A"),"C":(8,"B"),"Em":(9,"A"),"G":(9,"B"),
           "Bm":(10,"A"),"D":(10,"B"),"F#m":(11,"A"),"A":(11,"B"),"Dbm":(12,"A"),"E":(12,"B")}
def key_ok(k1, k2):
    a, b = CAMELOT.get(k1), CAMELOT.get(k2)
    if not a or not b: return False
    (n1, l1), (n2, l2) = a, b
    if n1 == n2: return True                      # same or relative
    return l1 == l2 and min((n1-n2) % 12, (n2-n1) % 12) == 1

def bpm_ok(b1, b2):
    if not b1 or not b2: return False
    for r in (1.0, 2.0, 0.5):
        if abs(b1 - b2 * r) / (b2 * r) <= 0.08: return True
    return False

# ---------- load ----------
db = Rekordbox6Database()
live = {}   # cid -> (bpm, key, label)
kname = {k.ID: k.ScaleName for k in db.query(tables.DjmdKey).all()}
artist = {a.ID: (a.Name or "") for a in db.query(tables.DjmdArtist).all()}
for c in db.query(tables.DjmdContent).filter(tables.DjmdContent.rb_local_deleted == 0).all():
    live[str(c.ID)] = ((c.BPM / 100.0) if c.BPM else None, kname.get(c.KeyID),
                       f"{artist.get(c.ArtistID,'')} - {c.Title or '?'}".strip(" -")[:44])

md = sqlite3.connect(f"file:{HERE/'metadata.sqlite'}?mode=ro", uri=True)
ids, vecs = [], []
for rb, vec in md.execute("SELECT rb_id, vec FROM embedding"):
    if rb in live:
        v = np.frombuffer(vec, dtype=np.float32)
        ids.append(rb); vecs.append(v / (np.linalg.norm(v) + 1e-9))
X = np.stack(vecs); N = len(ids)
bpm = np.array([live[i][0] or 0.0 for i in ids])
keys = [live[i][1] for i in ids]
import re
norm_lbl = [re.sub(r"[^a-z0-9]+", "", live[i][2].lower()) for i in ids]

# ---------- pairwise top-K (chunked) ----------
pairs = {}   # (i,j) i<j -> sim
CH = 512
for s in range(0, N, CH):
    S = X[s:s+CH] @ X.T
    for r in range(S.shape[0]):
        i = s + r
        row = S[r]; row[i] = -1
        cand = np.where(row >= SIM)[0]
        if not len(cand): continue
        ok = [j for j in cand if bpm_ok(bpm[i], bpm[j])]
        if not ok: continue
        ok = [j for j in ok if row[j] < 0.997 and norm_lbl[i] != norm_lbl[j]]  # skip dup files
        score = [(row[j] + (0.02 if key_ok(keys[i], keys[j]) else 0.0), j) for j in ok]
        seen_lbl, picked = set(), 0
        for _, j in sorted(score, reverse=True):
            if picked >= TOP: break
            if norm_lbl[j] in seen_lbl: continue   # dup copies of one partner take one slot
            seen_lbl.add(norm_lbl[j]); picked += 1
            a, b = (i, j) if i < j else (j, i)
            pairs[(a, b)] = max(pairs.get((a, b), 0.0), float(row[j]))

deg = defaultdict(int)
for a, b in pairs: deg[a] += 1; deg[b] += 1
dv = np.array([deg.get(i, 0) for i in range(N)])
print(f"{'APPLY' if APPLY else 'DRY-RUN'} — Matching pairs from embeddings (sim>={SIM}, top {TOP}/track)\n")
print(f"tracks with embeddings in collection: {N}")
print(f"pairs: {len(pairs)}   tracks with >=1 match: {(dv>0).sum()} ({(dv>0).mean():.0%})   "
      f"matches/track median {int(np.median(dv))}, p90 {int(np.percentile(dv,90))}, max {dv.max()}")
print("\nsample pairs (across similarity range):")
sp = sorted(pairs.items(), key=lambda kv: -kv[1])
for (a, b), sim in sp[:5] + sp[len(sp)//2:len(sp)//2+5] + sp[-5:]:
    ka, kb = keys[a] or "?", keys[b] or "?"
    print(f"   {sim:.3f}  [{bpm[a]:5.1f} {ka:>3}] {live[ids[a]][2]:<44} <-> [{bpm[b]:5.1f} {kb:>3}] {live[ids[b]][2]}")

# ---------- diff vs existing script rows (UUID==ID marks ours; UI rows differ) ----------
REBUILD = "--rebuild" in sys.argv
desired = {}   # (cidA, cidB) sorted -> best sim
for (a, b), sim in pairs.items():
    k = tuple(sorted((ids[a], ids[b])))
    if desired.get(k, -1.0) < sim: desired[k] = sim

ses = db.session
existing_live, existing_dead = {}, {}   # pair-key -> row ID
for rid, c1, c2, dele in ses.execute(text(
        "SELECT ID, ContentID1, ContentID2, rb_local_deleted FROM djmdRecommendLike WHERE UUID = ID")):
    (existing_dead if dele else existing_live)[tuple(sorted((str(c1), str(c2))))] = rid

fresh = [k for k in desired if k not in existing_live]
resurrect = [existing_dead[k] for k in fresh if k in existing_dead]
inserts = [k for k in fresh if k not in existing_dead]
removes = [rid for k, rid in existing_live.items() if k not in desired]
print(f"\ndiff vs {len(existing_live)} existing script rows: +{len(inserts)} new, "
      f"{len(resurrect)} resurrected, -{len(removes)} soft-deleted, "
      f"{len(existing_live) - len(removes)} unchanged (untouched — no USN churn)")
if REBUILD:
    print("--rebuild: will wipe ALL script rows and reinsert everything")

if not APPLY:
    print("\nDRY RUN — no writes. Re-run with --apply (rekordbox closed).")
    sys.exit(0)
if get_rekordbox_pid():
    sys.exit("Refusing — rekordbox is running. Quit it first.")
if not REBUILD and not (inserts or resurrect or removes):
    print("\nNo changes — djmdRecommendLike already matches the desired set. Nothing written.")
    sys.exit(0)

src = Path.home() / "Library/Pioneer/rekordbox/master.db"
bk = src.with_name(src.name + ".backup-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy2(src, bk); print(f"\nbackup -> {bk}")

# Native-format rows (verified against a UI-created match 2026-07-04):
#   LikeRate NULL, DataCreated = ms epoch split into hi/lo 32 bits, and
#   rb_local_usn allocated from agentRegistry.localUpdateCount — the export
#   ONLY includes rows with usn <= that counter, so we bump it afterwards.
nowdt = datetime.datetime.now()
now = nowdt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " +00:00"
ms = int(nowdt.timestamp() * 1000)
dch, dcl = ms >> 32, ms & 0xFFFFFFFF

if REBUILD:
    ses.execute(text("DELETE FROM djmdRecommendLike WHERE UUID = ID"))
    inserts, resurrect, removes = list(desired), [], []

counter = ses.execute(text(
    "SELECT int_1 FROM agentRegistry WHERE registry_id='localUpdateCount'")).scalar()
ops = 0
if inserts:
    rows = []
    for k in sorted(inserts, key=lambda k: -desired[k]):
        rid = str(uuid.uuid4()); ops += 1
        rows.append(dict(ID=rid, c1=k[0], c2=k[1], UUID=rid, usn=counter + ops, ts=now))
    ses.execute(text(
        "INSERT INTO djmdRecommendLike (ID, ContentID1, ContentID2, LikeRate, DataCreatedH, DataCreatedL,"
        " UUID, rb_data_status, rb_local_data_status, rb_local_deleted, rb_local_synced,"
        " usn, rb_local_usn, created_at, updated_at)"
        f" VALUES (:ID, :c1, :c2, NULL, {dch}, {dcl}, :UUID, 0, 0, 0, 0, NULL, :usn, :ts, :ts)"), rows)
if resurrect:
    rows = []
    for rid in resurrect:
        ops += 1; rows.append(dict(id=rid, usn=counter + ops, ts=now))
    ses.execute(text("UPDATE djmdRecommendLike SET rb_local_deleted = 0, rb_local_usn = :usn,"
                     " updated_at = :ts WHERE ID = :id"), rows)
if removes:
    rows = []
    for rid in removes:
        ops += 1; rows.append(dict(id=rid, usn=counter + ops, ts=now))
    ses.execute(text("UPDATE djmdRecommendLike SET rb_local_deleted = 1, rb_local_usn = :usn,"
                     " updated_at = :ts WHERE ID = :id"), rows)
ses.execute(text("UPDATE agentRegistry SET int_1 = :v WHERE registry_id='localUpdateCount'"),
            {"v": counter + ops})
db.commit()
print(f"Committed. +{len(inserts)} inserted, {len(resurrect)} resurrected, -{len(removes)} soft-deleted "
      f"(usn {counter+1}..{counter+ops}, counter bumped). Incremental USB sync should carry the delta.")
