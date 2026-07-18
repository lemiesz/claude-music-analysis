#!/usr/bin/env python
"""Consolidate 9 My Tag banks into the 4 standard categories so the XDJ-XZ
Track Filter can see them (hardware/export only supports 4):

  1 Genre      keep used tags, drop 6 never-used defaults
  2 Components -> rename "Energy" : hi/mid/lo + driving/groovy/peak/warmup
  3 Situation  -> rename "Mood"   : chill/dark/happy/listening
                  (Peak Time's 79 links re-pointed to Intensity's "peak")
  4 Untitled   -> rename "Label"  : the 16 label tags
  Source bank left in place (desktop-only, harmless). Emptied banks soft-deleted.

Only re-parents/renames existing DjmdMyTag rows — track assignments follow the
tag IDs. Unused tags & emptied banks get rb_local_deleted=1 (soft delete).
Backs up master.db; refuses if rekordbox is running.

Usage: ~/.rekordbox-venv/bin/python mytag_consolidate.py [--apply]
"""
import sys, shutil, datetime
from pathlib import Path
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables
from pyrekordbox.utils import get_rekordbox_pid

APPLY = "--apply" in sys.argv

db = Rekordbox6Database()
rows = db.query(tables.DjmdMyTag).filter(tables.DjmdMyTag.rb_local_deleted == 0).all()
cats = {r.Name: r for r in rows if r.ParentID == "root"}
tags = {}  # (bank_name, tag_name) -> row
for r in rows:
    if r.ParentID != "root":
        bank = next((c.Name for c in cats.values() if c.ID == r.ParentID), "?")
        tags[(bank, r.Name)] = r

links = db.query(tables.DjmdSongMyTag).filter(tables.DjmdSongMyTag.rb_local_deleted == 0).all()
from collections import defaultdict
use = defaultdict(int); by_tag = defaultdict(list)
for l in links:
    use[l.MyTagID] += 1; by_tag[l.MyTagID].append(l)

# ---------- plan ----------
GENRE_DROP = ["Acid House", "Deep House", "Nu Disco", "Electro House", "Bass Music", "Trap"]
PLAN = [
    # (target bank row, new bank name, ordered list of (src_bank, tag), drop list of (src_bank, tag))
    (cats["Genre"], "Genre",
     [("Genre", n) for n in ("Techno", "house", "trance", "dnb", "bass", "psy", "hip-hop", "disco")],
     [("Genre", n) for n in GENRE_DROP]),
    (cats["Components"], "Energy",
     [("Energy", "hi"), ("Energy", "mid"), ("Energy", "lo"),
      ("Intensity", "driving"), ("Intensity", "groovy"), ("Intensity", "peak"), ("Intensity", "warmup")],
     [("Components", n) for n in ("Synth", "Vocal", "Beat", "Sub Bass", "Percussion", "Piano", "Dark", "Upper")]),
    (cats["Situation"], "Mood",
     [("Mood", n) for n in ("chill", "dark", "happy", "listening")],
     [("Situation", n) for n in ("Main Floor", "Second Floor", "Lounge", "Mid Night", "Morning", "Build up", "Build down")]),
    (cats["Untitled Column"], "Label",
     [("Label", n) for n in ("adid", "anjuna", "dirtybird", "stil-vor-talent", "fckng-serious",
                             "whos-afraid-138", "wakaan", "iboga", "defected", "toolroom",
                             "kompakt", "crosstown-rebels", "desous", "ektoplazm", "bouq", "basement-discos")],
     [("Untitled Column", "My Comment")]),
]
MERGE = ("Situation", "Peak Time")          # -> Intensity/peak
MERGE_INTO = ("Intensity", "peak")
EMPTY_BANKS = ["Label", "Energy", "Intensity", "Mood"]   # soft-delete after moves

missing = [k for _, _, mv, dr in PLAN for k in mv + dr if k not in tags]
assert not missing, f"tags not found: {missing}"

print(f"{'APPLY' if APPLY else 'DRY-RUN'} — My Tag consolidation to 4 standard banks\n")
for bank, new_name, moves, drops in PLAN:
    arrow = "" if bank.Name == new_name else f'  (rename "{bank.Name}")'
    print(f"[bank {bank.Seq}] {new_name}{arrow}")
    for i, key in enumerate(moves, 1):
        t = tags[key]; src = "" if key[0] == new_name or key[0] == bank.Name else f"  <- {key[0]}"
        print(f"    {i:>2}. {t.Name:<20} {use[t.ID]:>5} tracks{src}")
    for key in drops:
        print(f"     x  drop unused: {key[1]}")
pt, pk = tags[MERGE], tags[MERGE_INTO]
pk_cids = {l.ContentID for l in by_tag[pk.ID]}
dupes = sum(1 for l in by_tag[pt.ID] if l.ContentID in pk_cids)
print(f'\nmerge "Peak Time" ({use[pt.ID]} tracks) into "peak": '
      f"{use[pt.ID]-dupes} re-pointed, {dupes} already tagged peak (dropped)")
print(f"soft-delete emptied banks: {', '.join(EMPTY_BANKS)}  (Source kept, desktop-only)")

if not APPLY:
    print("\nDRY RUN — no writes. Re-run with --apply (rekordbox closed).")
    sys.exit(0)
if get_rekordbox_pid():
    sys.exit("Refusing — rekordbox is running. Quit it first.")

src = Path.home() / "Library/Pioneer/rekordbox/master.db"
bk = src.with_name(src.name + ".backup-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy2(src, bk); print(f"\nbackup -> {bk}")

now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " +00:00"
# merge Peak Time links first
for l in by_tag[pt.ID]:
    if l.ContentID in pk_cids: l.rb_local_deleted = 1
    else: l.MyTagID = pk.ID
pt.rb_local_deleted = 1
# renames, moves, seq, drops
for bank, new_name, moves, drops in PLAN:
    bank.Name = new_name
    for i, key in enumerate(moves, 1):
        t = tags[key]; t.ParentID = bank.ID; t.Seq = i
    for key in drops:
        tags[key].rb_local_deleted = 1
# soft-delete emptied banks
for nm in EMPTY_BANKS:
    cats[nm].rb_local_deleted = 1
db.commit()
print("Committed. My Tags consolidated to 4 banks — re-export USB for the XZ.")
