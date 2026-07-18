#!/usr/bin/env python
"""Incremental My Tag filler for NEW tracks — targets the CONSOLIDATED 4 banks
(Genre / Energy / Vibe; Label+Source stay playlist-driven via tag_and_merge.py).

Supersedes apply_mood_tags.py + apply_vibe_tags.py for ongoing ingest: those
predate the 2026-07-04 bank consolidation and would recreate the old
Intensity/Mood banks. This script NEVER creates banks — it only fills tags into
existing ones, and only for tracks that have NO tag in that bank yet.

  Genre  (only if untagged): ML genre_discogs family -> Techno/house/trance/psy/
         dnb/bass/hip-hop/disco (ambient/other/unmappable -> left untagged)
  Energy (only if no intensity tag): mood_aggressive -> warmup/groovy/driving/peak
         (hi/mid/lo are left alone — separately curated)
  Vibe   (only if untagged): happy/chill/dark/listening/euphoric/hypnotic/
         melancholic/feelgood — same rules as apply_vibe_tags.py

New tags are mirrored into Comments as #hashtags. Idempotent; backs up
master.db; refuses if rekordbox is running.

Usage: ~/.rekordbox-venv/bin/python ingest_tags.py [--apply]
"""
import sys, uuid, shutil, datetime, sqlite3
from pathlib import Path
from collections import defaultdict, Counter
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables
from pyrekordbox.utils import get_rekordbox_pid

APPLY = "--apply" in sys.argv
HERE = Path(__file__).resolve().parent
now = datetime.datetime.now()

# ---------- features ----------
md = sqlite3.connect(f"file:{HERE/'metadata.sqlite'}?mode=ro", uri=True)
feat = defaultdict(dict)
for rb, name, num, txt in md.execute(
        "SELECT rb_id,name,num,txt FROM feature WHERE source='essentia_ml'"):
    feat[rb][name] = num if num is not None else txt
ef = sqlite3.connect(f"file:{HERE/'essentia_features.sqlite'}?mode=ro", uri=True)
mode = {rb: txt for rb, txt in ef.execute("SELECT rb_id,txt FROM feature WHERE name='mode'")}

def genre_tag(full):
    parent, _, sub = str(full or "?").partition("---"); s = sub.lower()
    if parent == "Hip Hop" or s == "hip hop":                 return "hip-hop"
    if parent != "Electronic":                                return None
    if any(k in s for k in ("disco", "funk", "soul", "boogie")): return "disco"
    if "psy" in s or "goa" in s:                              return "psy"
    if "trance" in s:                                         return "trance"
    if "drum n bass" in s or "jungle" in s or "juke" in s or "footwork" in s: return "dnb"
    if any(k in s for k in ("dubstep","bassline","grime","riddim","halftime","bass music")): return "bass"
    if "techno" in s or "tribal" in s or "industrial" in s:   return "Techno"
    if "house" in s or "garage" in s or "electro" in s:       return "house"
    return None

def intensity_tag(f):
    a = f.get("mood_aggressive") or 0
    return "peak" if a > 0.60 else "driving" if a > 0.35 else "groovy" if a > 0.15 else "warmup"

def vibe_tags(f, rb):
    out = set()
    h, r, s, a, p, e = ((f.get(k) or 0) for k in
        ("mood_happy", "mood_relaxed", "mood_sad", "mood_aggressive", "mood_party", "energy"))
    if h > 0.40: out.add("happy")
    if r > 0.60: out.add("chill")
    if s > 0.12 or (a > 0.55 and h < 0.10): out.add("dark")
    if p < 0.60: out.add("listening")
    if h > 0.50 and e > 0.45 and p > 0.90: out.add("euphoric")
    if 0.10 < a < 0.60 and h < 0.25 and r < 0.50 and s < 0.10 and p > 0.87: out.add("hypnotic")
    if s > 0.05 and r > 0.40 and h < 0.15: out.add("melancholic")
    elif mode.get(rb) == "minor" and r > 0.55 and h < 0.10 and a < 0.30: out.add("melancholic")
    if 0.15 < h <= 0.50 and p > 0.90 and a < 0.40 and "hypnotic" not in out: out.add("feelgood")
    return out

# ---------- current tag state ----------
db = Rekordbox6Database()
rows = db.query(tables.DjmdMyTag).filter(tables.DjmdMyTag.rb_local_deleted == 0).all()
banks = {r.Name: r for r in rows if r.ParentID == "root"}
for need in ("Genre", "Energy", "Vibe"):
    assert need in banks, f"bank {need} missing — was the 2026-07-04 consolidation undone?"
tagrow = {}   # (bank, tag) -> row
for r in rows:
    if r.ParentID != "root":
        bank = next((b for b, c in banks.items() if c.ID == r.ID or c.ID == r.ParentID), None)
        if bank: tagrow[(bank, r.Name)] = r

INTENSITY = {"warmup", "groovy", "driving", "peak"}
tagged = defaultdict(set)   # bank -> cids that already have a (relevant) tag there
linked = defaultdict(set)   # tag ID -> cids
for l in db.query(tables.DjmdSongMyTag).filter(tables.DjmdSongMyTag.rb_local_deleted == 0).all():
    linked[l.MyTagID].add(l.ContentID)
for (bank, name), r in tagrow.items():
    if bank == "Energy" and name not in INTENSITY: continue   # hi/mid/lo don't block intensity fill
    for cid in linked[r.ID]: tagged[bank].add(cid)

live = {str(c.ID) for c in db.query(tables.DjmdContent.ID).filter(tables.DjmdContent.rb_local_deleted == 0).all()}

# ---------- plan (fill-only) ----------
plan = defaultdict(set)     # (bank, tag) -> new cids
for rb, f in feat.items():
    if rb not in live: continue
    if rb not in tagged["Genre"]:
        g = genre_tag(f.get("genre_discogs"))
        if g: plan[("Genre", g)].add(rb)
    if rb not in tagged["Energy"] and f.get("mood_aggressive") is not None:
        plan[("Energy", intensity_tag(f))].add(rb)
    if rb not in tagged["Vibe"]:
        for v in vibe_tags(f, rb): plan[("Vibe", v)].add(rb)

print(f"{'APPLY' if APPLY else 'DRY-RUN'} — incremental tag fill (untagged tracks only)\n")
per_bank = Counter()
for (bank, tag), cids in sorted(plan.items()):
    print(f"   {bank:<7} {tag:<12} +{len(cids)}"); per_bank[bank] += len(cids)
print(f"\nnew links: {dict(per_bank) or 'none — everything already tagged'}")

if not APPLY:
    print("\nDRY RUN — no writes. Re-run with --apply (rekordbox closed).")
    sys.exit(0)
if not plan:
    sys.exit(0)
if get_rekordbox_pid():
    sys.exit("Refusing — rekordbox is running. Quit it first.")

src = Path.home() / "Library/Pioneer/rekordbox/master.db"
bk = src.with_name(src.name + ".backup-" + now.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(src, bk); print(f"\nbackup -> {bk}")

comment_plan = defaultdict(set)
for (bank, tname), cids in plan.items():
    tag = tagrow.get((bank, tname))
    if tag is None:   # tag missing inside an existing bank -> create it there
        seq = 1 + max([int(r.Seq or 0) for (b, _), r in tagrow.items() if b == bank] or [0])
        tag = tables.DjmdMyTag(ID=str(db.generate_unused_id(tables.DjmdMyTag)), Seq=seq,
                               Name=tname, Attribute=0, ParentID=str(banks[bank].ID),
                               UUID=str(uuid.uuid4()), created_at=now, updated_at=now)
        db.add(tag); db.flush(); tagrow[(bank, tname)] = tag
    n = len(linked[tag.ID])
    for cid in cids:
        if cid in linked[tag.ID]: continue
        n += 1
        db.add(tables.DjmdSongMyTag(ID=str(uuid.uuid4()), MyTagID=str(tag.ID), ContentID=str(cid),
                                    TrackNo=n, UUID=str(uuid.uuid4()), created_at=now, updated_at=now))
        comment_plan[cid].add("#" + tname)

changed = 0
for cid, tags in comment_plan.items():
    t = db.get_content(ID=cid)
    if not t: continue
    cur = t.Commnt or ""
    missing = sorted(h for h in tags if h not in cur)
    if missing:
        t.Commnt = (cur.rstrip() + " " + " ".join(missing)).strip() if cur.strip() else " ".join(missing)
        changed += 1
db.commit()
print(f"Committed. Filled tags on new tracks; {changed} comments updated.")
