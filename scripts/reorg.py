#!/usr/bin/env python
"""Deterministic rekordbox folder reorganization (genre-first blueprint).

Operations: create folders, rename folders, move playlists/folders (reparent),
delete the verified duplicate + empty playlists and the emptied label folders.

move_playlist / rename only change ParentID/Name/Seq -- they NEVER touch the
DjmdSongPlaylist track rows (verified). So the ONLY thing that can change track
membership is a deletion. This script proves no track is lost by simulating the
plan and comparing before/after invariants.

Usage:
    python reorg.py            # DRY RUN: validate + simulate + report (no writes)
    python reorg.py --apply    # back up master.db, then execute + commit

Rekordbox must be CLOSED for --apply.
"""
import sys
import shutil
import datetime
from pathlib import Path
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables
from pyrekordbox.config import get_config

APPLY = "--apply" in sys.argv

# --- existing folder IDs ---------------------------------------------------
HOUSE, TECHNO, TRANCE, DNB, BASS, HIPHOP = ("3869279475","3244867696","1848977732","1295599011","1879955514","1696839446")
PSYCOLL, PSYDANCE, PSYTRANCE, PSYTECH = ("4045171934","2727782168","1859367982","2972331610")
BASSHOUSE, LABELS, SEMPA = ("195006818","354903408","2508775755")
TECHHOUSEF, DIGGIN, DRCOMP, RANDOM = ("2480105043","2534527575","385612513","712840821")

# --- new folders: key -> (display name, parent [existing id or new key]) ---
NEW_FOLDERS = {
    "psy":             ("psy", None),
    "disco":           ("disco", None),
    "_system":         ("_system", None),
    "disco-funky":     ("disco-funky", HOUSE),
    "organic-melodic": ("organic-melodic", HOUSE),
    "melodic":         ("melodic", TECHNO),
    "peak-driving":    ("peak-driving", TECHNO),
    "tech-trance":     ("tech-trance", TRANCE),
    "progressive":     ("progressive", TRANCE),
}

RENAMES = {
    RANDOM: "_unsorted", DRCOMP: "sets",
    TECHHOUSEF: "tech-house", DIGGIN: "deep",
    PSYDANCE: "energy-sets", PSYCOLL: "downtempo-organic",
}

# --- moves: (playlist/folder id, target [existing id or new key]) ----------
MOVES = [
    # assemble psy
    (PSYTRANCE, "psy"), (PSYTECH, "psy"), (PSYDANCE, "psy"), (PSYCOLL, "psy"),
    ("619884394", "psy"),           # ektoplazm -> psy (loose)
    ("4209870182", PSYCOLL),        # african-tribal-orchestra -> downtempo-organic
    # assemble bass
    (BASSHOUSE, BASS),
    # house/tech-house
    ("2304839636", TECHHOUSEF), ("887706024", TECHHOUSEF),      # dirtybird (+2022)
    # house/deep
    ("3987028666", DIGGIN), ("113129585", DIGGIN), ("142262089", DIGGIN), ("2828169554", DIGGIN),
    # house/disco-funky
    ("3607273423","disco-funky"), ("4206972200","disco-funky"), ("2519602047","disco-funky"), ("2926435886","disco-funky"),
    # house/organic-melodic
    ("2292595423","organic-melodic"), ("2932793367","organic-melodic"), ("2085324472","organic-melodic"),
    ("4058489127","organic-melodic"), ("1113562163","organic-melodic"),   # stil-vor-talent, adid
    # techno/melodic
    ("214761921","melodic"), ("2275452001","melodic"), ("1127318913","melodic"),
    # techno/peak-driving
    ("2618263792","peak-driving"), ("1394954197","peak-driving"), ("2264560123","peak-driving"),
    ("2983956104","peak-driving"), ("1202567920","peak-driving"),         # fckng-serious
    # trance/tech-trance
    ("1753387471","tech-trance"), ("3151138516","tech-trance"),
    # trance/progressive
    ("1194356974","progressive"), ("2926618485","progressive"), ("816230423","progressive"), ("694520905","progressive"),
    ("3870012562", TRANCE),         # whos-afraid-138 -> trance loose
    # hip-hop consolidation
    ("185842949", HIPHOP), ("713941477", HIPHOP), ("913741948", HIPHOP),
    # disco / sets / _system
    ("3209808178", "disco"),
    ("944296333", DRCOMP),          # sempa -> sets
    ("2683753618", "_system"),      # MIK Cue Points -> _system
]

# --- deletions: leaves/dups/empties first, then emptied folders ------------
DELETE_LEAVES = ["1219220828", "2524832694", "200000", "1293403395"]  # labels/anjuna dup, Untitled, CUE Analysis, classics
DELETE_FOLDERS = [LABELS, SEMPA]  # after their children are moved/deleted

# ---------------------------------------------------------------------------
db = Rekordbox6Database()

pls = {p.ID: {"name": p.Name, "parent": p.ParentID, "folder": p.Attribute == 1}
       for p in db.get_playlist()}
song_rows = {}
for r in db.query(tables.DjmdSongPlaylist).all():
    song_rows.setdefault(r.PlaylistID, []).append(r.ContentID)

errors = []

def expect(pid, want_folder=None, label=""):
    if pid not in pls:
        errors.append(f"MISSING id {pid} ({label})")
    elif want_folder is not None and pls[pid]["folder"] != want_folder:
        errors.append(f"id {pid} ({pls[pid]['name']}) folder={pls[pid]['folder']} expected {want_folder}")

# validate references
for pid, tgt in MOVES:
    expect(pid, None, "move src")
    if tgt not in NEW_FOLDERS:
        expect(tgt, True, "move target")
for pid in list(RENAMES):
    expect(pid, True, "rename")
for pid in DELETE_LEAVES:
    expect(pid, False, "delete leaf")
for pid in DELETE_FOLDERS:
    expect(pid, True, "delete folder")

# ---- simulate on an in-memory tree ----------------------------------------
sim = {pid: dict(v) for pid, v in pls.items()}
for key, (name, parent) in NEW_FOLDERS.items():
    sim[key] = {"name": name, "parent": parent, "folder": True, "new": True}
for pid, newname in RENAMES.items():
    if pid in sim: sim[pid]["name"] = newname
for pid, tgt in MOVES:
    if pid in sim: sim[pid]["parent"] = tgt
deleted = set(DELETE_LEAVES) | set(DELETE_FOLDERS)
for pid in deleted:
    sim.pop(pid, None)

# tree integrity: every surviving node reaches a root without cycles/dangling
def reaches_root(pid):
    seen = set()
    cur = pls.get(pid, sim.get(pid, {})).get("parent") if pid in pls else sim[pid]["parent"]
    cur = sim[pid]["parent"]
    while cur not in (None, "root"):
        if cur in seen: return False, "cycle"
        if cur not in sim: return False, f"dangling parent {cur}"
        seen.add(cur); cur = sim[cur]["parent"]
    return True, ""
for pid in sim:
    ok, why = reaches_root(pid)
    if not ok: errors.append(f"tree: {pid} ({sim[pid]['name']}) -> {why}")

# check folders being deleted are empty of surviving children
for fid in DELETE_FOLDERS:
    kids = [c for c in sim if sim[c]["parent"] == fid]
    if kids:
        errors.append(f"delete folder {fid} ({pls[fid]['name']}) still has children: {[sim[c]['name'] for c in kids]}")

# ---- TRACK PRESERVATION ----------------------------------------------------
before_rows = sum(len(v) for v in song_rows.values())
removed_rows = sum(len(song_rows.get(pid, [])) for pid in deleted)
after_rows = before_rows - removed_rows

before_tracks = set().union(*song_rows.values()) if song_rows else set()
surviving_tracks = set()
for pid, cids in song_rows.items():
    if pid not in deleted:
        surviving_tracks.update(cids)
orphaned = before_tracks - surviving_tracks          # tracks that lose ALL playlist membership

lib_count = db.query(tables.DjmdContent).filter(tables.DjmdContent.rb_local_deleted == 0).count()

# ---- report ---------------------------------------------------------------
print(f"{'APPLY' if APPLY else 'DRY-RUN'} — folder reorganization\n")
print(f"validation errors: {len(errors)}")
for e in errors: print("   !", e)

folders_before = sum(1 for v in pls.values() if v["folder"])
folders_after = sum(1 for v in sim.values() if v["folder"])
leaves_before = sum(1 for v in pls.values() if not v["folder"])
leaves_after = sum(1 for v in sim.values() if not v["folder"])
print(f"\nfolders:  {folders_before} -> {folders_after}   (+{len(NEW_FOLDERS)} new, -{len(DELETE_FOLDERS)} deleted)")
print(f"playlists:{leaves_before} -> {leaves_after}   (-{len(DELETE_LEAVES)} deleted)")
print(f"moves:    {len(MOVES)}   renames: {len(RENAMES)}")

print("\n--- TRACK PRESERVATION ---")
print(f"library tracks (DjmdContent): {lib_count}  [unchanged — deletes never remove content]")
print(f"playlist track-rows:  {before_rows} -> {after_rows}   (removed {removed_rows} rows from deleted playlists)")
print(f"unique tracks in >=1 playlist: {len(before_tracks)} -> {len(surviving_tracks)}")
print(f"tracks orphaned (lose ALL playlists): {len(orphaned)}")
if orphaned:
    for cid in list(orphaned)[:20]:
        c = db.get_content(ID=cid); print("   ORPHAN:", cid, getattr(c,'Title',None))

deleted_detail = ", ".join(f"{pls[p]['name']}({len(song_rows.get(p,[]))})" for p in DELETE_LEAVES)
print(f"\ndeleted leaves: {deleted_detail}")
print("labels/anjuna's tracks preserved in anjuna-hq:",
      set(song_rows.get("1219220828", [])) <= surviving_tracks)

verdict = (len(errors) == 0 and len(orphaned) == 0 and lib_count == 21054)
print(f"\n{'PASS ✅  no tracks lost, tree valid' if verdict else 'FAIL ❌  review errors above'}")

if not APPLY:
    db.session.rollback()
    sys.exit(0)

# ---- APPLY ----------------------------------------------------------------
from pyrekordbox.utils import get_rekordbox_pid
if not verdict:
    print("\nRefusing to apply — validation failed."); db.session.rollback(); sys.exit(1)
if get_rekordbox_pid():
    print("\nRefusing to apply — rekordbox is running. Quit it first."); db.session.rollback(); sys.exit(1)

src = Path(get_config("rekordbox6", "db_path"))
backup = src.with_name(src.name + ".backup-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy2(src, backup)
print(f"\nBacked up DB -> {backup}")

newid = {}
for key, (name, parent) in NEW_FOLDERS.items():          # 1. create folders
    newid[key] = db.create_playlist_folder(name, parent=parent).ID
resolve = lambda tgt: newid.get(tgt, tgt)
for pid, newname in RENAMES.items():                     # 2. rename
    db.rename_playlist(pid, newname)
for pid, tgt in MOVES:                                    # 3. reparent
    db.move_playlist(pid, parent=resolve(tgt))
for pid in DELETE_LEAVES + DELETE_FOLDERS:               # 4. delete (tolerant)
    if db.get_playlist(ID=pid) is not None:
        db.delete_playlist(pid)

db.commit()                                              # sets USN; refuses if rekordbox open
print(f"Committed. {len(NEW_FOLDERS)} folders created, {len(RENAMES)} renamed, "
      f"{len(MOVES)} moved, {len(DELETE_LEAVES) + len(DELETE_FOLDERS)} deleted.")
print(f"Restore with: cp '{backup}' '{src}'")
