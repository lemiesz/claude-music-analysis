#!/usr/bin/env python
"""Halve the stored rekordbox BPM for no-category tracks that confidently have a
half-time feel: rb BPM >= 118 AND essentia tempo == rb/2 (within 5%), gated by
genre family. Backs up master.db; refuses if rekordbox is running.

Note: updates DjmdContent.BPM (display/sort value) only — beatgrids in ANLZ
files are not rewritten.

Usage: ~/.rekordbox-venv/bin/python bpm_halftime_fix.py [--apply]
       [--families hip-hop,halftime,...]   (default: hip-hop,halftime)
       [--scope nocat|all]                 (default: nocat = no-category-music only)

Idempotent: once halved, a track's rb BPM ~= essentia BPM, so it never
re-triggers. Safe to re-run after ingesting new tracks.
"""
import sys, shutil, datetime, sqlite3
from pathlib import Path
from collections import defaultdict
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables
from pyrekordbox.utils import get_rekordbox_pid

APPLY = "--apply" in sys.argv
FAMS = (sys.argv[sys.argv.index("--families") + 1].split(",")
        if "--families" in sys.argv else ["hip-hop", "halftime"])
SCOPE = sys.argv[sys.argv.index("--scope") + 1] if "--scope" in sys.argv else "nocat"
HERE = Path(__file__).resolve().parent
SRC_PID = "59257379"  # no-category-music

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

db = Rekordbox6Database()
if SCOPE == "all":
    cids = [str(i) for (i,) in db.query(tables.DjmdContent.ID)
            .filter(tables.DjmdContent.rb_local_deleted == 0).all()]
else:
    cids = [s.ContentID for s in db.get_playlist_songs(PlaylistID=SRC_PID)]
md = sqlite3.connect(f"file:{HERE/'metadata.sqlite'}?mode=ro", uri=True)
gmap = {rb: t for rb, t in md.execute(
    "SELECT rb_id,txt FROM feature WHERE source='essentia_ml' AND name='genre_discogs'")}
ef = sqlite3.connect(f"file:{HERE/'essentia_features.sqlite'}?mode=ro", uri=True)
ebpm = {rb: n for rb, n in ef.execute("SELECT rb_id,num FROM feature WHERE name='bpm' AND num>0")}

title = {str(i): t for i, t in db.query(tables.DjmdContent.ID, tables.DjmdContent.Title).all()}
artist = {a.ID: (a.Name or "") for a in db.query(tables.DjmdArtist).all()}
aid = {str(i): artist.get(a, "") for i, a in db.query(tables.DjmdContent.ID, tables.DjmdContent.ArtistID).all()}
def lbl(c): return f"{aid.get(c,'')} - {title.get(c,'?')}".strip(" -")[:58]

cand = defaultdict(list)   # family -> [(cid, rb_bpm, new_bpm, ess_bpm)]
for c in cids:
    ct = db.get_content(ID=c)
    rb = (ct.BPM / 100.0) if (ct and ct.BPM) else None
    e = ebpm.get(c)
    if not (rb and e) or rb < 118: continue
    if abs(rb - 2 * e) / (2 * e) < 0.05:            # essentia says half-time
        cand[family(gmap.get(c))].append((c, rb, rb / 2, e))

sel = [(f, r) for f in FAMS for r in cand.get(f, [])]
print(f"{'APPLY' if APPLY else 'DRY-RUN'} — halftime BPM fix, families={','.join(FAMS)}, scope={SCOPE}\n")
print(f"── WILL HALVE ({len(sel)}) ──")
for f in FAMS:
    for c, rb, nb, e in sorted(cand.get(f, []), key=lambda r: -r[1]):
        print(f"   [{f:<9}] {rb:6.1f} -> {nb:5.1f}  (ess {e:5.1f})  {lbl(c)}")
excl = {f: v for f, v in cand.items() if f not in FAMS}
if excl:
    print(f"\n── excluded families (not touched) ──")
    for f, v in sorted(excl.items(), key=lambda kv: -len(kv[1])):
        print(f"   {f:<20} {len(v)}")

if not APPLY:
    print("\nDRY RUN — no writes. Re-run with --apply (rekordbox closed).")
    sys.exit(0)
if get_rekordbox_pid():
    sys.exit("Refusing — rekordbox is running. Quit it first.")

src = Path.home() / "Library/Pioneer/rekordbox/master.db"
bk = src.with_name(src.name + ".backup-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy2(src, bk); print(f"\nbackup -> {bk}")
for f, (c, rb, nb, e) in sel:
    ct = db.get_content(ID=c)
    ct.BPM = int(round(nb * 100))
db.commit()
print(f"Committed. Halved BPM on {len(sel)} tracks.")
