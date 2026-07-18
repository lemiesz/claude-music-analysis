#!/usr/bin/env python
"""Step 3: derived Genre My Tag (from top-level folder membership) + energy->Situation.

* Genre: for each top-level genre folder, recursively collect every track in its
  descendant playlists and tag it with that folder's name, under the existing
  `Genre` My Tag category. Reuses an existing Genre child tag on case-insensitive
  name match (so `techno` reuses the preset `Techno`), else creates it.
* Situation: map the energy playlists (lo/mid/hi, peak-time) to existing
  `Situation` tags (see SITUATION_MAP below).
* Mirrors both into the Comment field as #hashtags (preserve existing, idempotent).

Usage:  python genre_situation.py [--apply]      (rekordbox must be closed for --apply)
"""
import sys, uuid, shutil, datetime
from pathlib import Path
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables
from pyrekordbox.config import get_config
from pyrekordbox.utils import get_rekordbox_pid

APPLY = "--apply" in sys.argv

GENRE_FOLDERS = ["house", "techno", "trance", "dnb", "bass", "psy", "hip-hop", "disco"]

# energy playlist id -> existing Situation tag name
SITUATION_MAP = {
    "478894502":  "Peak Time",   # psy/energy-sets: hi
    "2606165362": "Main Floor",  # psy/energy-sets: mid
    "3047272017": "Build up",    # psy/energy-sets: lo
    "2264560123": "Peak Time",   # techno: peak-time
}

db = Rekordbox6Database()
now = datetime.datetime.now()

pls = {p.ID: {"name": p.Name, "parent": p.ParentID, "folder": p.Attribute == 1}
       for p in db.get_playlist()}
children = {}
for pid, v in pls.items():
    children.setdefault(v["parent"], []).append(pid)
comment_plan = {}   # content_id -> set of hashtags


def leaf_tracks_under(folder_id):
    """Union of ContentIDs in all leaf playlists descending from folder_id."""
    out, stack = set(), [folder_id]
    while stack:
        cur = stack.pop()
        for kid in children.get(cur, []):
            if pls[kid]["folder"]:
                stack.append(kid)
            else:
                out.update(s.ContentID for s in db.get_playlist_songs(PlaylistID=kid))
    return out


def category(name):
    return db.query(tables.DjmdMyTag).filter(
        tables.DjmdMyTag.ParentID == "root", tables.DjmdMyTag.Name == name).one()


def find_child_ci(parent_id, name):
    for r in db.query(tables.DjmdMyTag).filter(tables.DjmdMyTag.ParentID == str(parent_id)).all():
        if r.Name.lower() == name.lower():
            return r
    return None


def next_seq(parent_id):
    seqs = [int(r.Seq) for r in db.query(tables.DjmdMyTag).filter(
        tables.DjmdMyTag.ParentID == str(parent_id)).all()]
    return (max(seqs) + 1) if seqs else 1


def make_tag(name, parent_id):
    row = tables.DjmdMyTag(ID=str(db.generate_unused_id(tables.DjmdMyTag)),
                           Seq=next_seq(parent_id), Name=name, Attribute=0,
                           ParentID=str(parent_id), UUID=str(uuid.uuid4()),
                           created_at=now, updated_at=now)
    db.add(row)
    return row


def links_for(tag_id):
    return {r.ContentID for r in db.query(tables.DjmdSongMyTag).filter(
        tables.DjmdSongMyTag.MyTagID == str(tag_id)).all()}


def link(tag_id, cids):
    existing = links_for(tag_id)
    fresh = [c for c in cids if c not in existing]
    for n, c in enumerate(fresh, start=len(existing) + 1):
        db.add(tables.DjmdSongMyTag(ID=str(uuid.uuid4()), MyTagID=str(tag_id), ContentID=str(c),
                                    TrackNo=n, UUID=str(uuid.uuid4()), created_at=now, updated_at=now))
    return len(fresh)


print(f"{'APPLY' if APPLY else 'DRY-RUN'} — step 3: Genre + Situation\n")
total = 0

# --- Genre ---
gcat = category("Genre")
print(f"[Genre] category id {gcat.ID}")
roots = {v["name"]: pid for pid, v in pls.items() if v["parent"] in (None, "root") and v["folder"]}
for g in GENRE_FOLDERS:
    fid = roots.get(g)
    if not fid:
        print(f"    ! genre folder '{g}' not found"); continue
    cids = leaf_tracks_under(fid)
    for c in cids:
        comment_plan.setdefault(c, set()).add("#" + g)
    tag = find_child_ci(gcat.ID, g) or make_tag(g, gcat.ID)
    n = link(tag.ID, cids)
    total += n
    print(f"    {g:<10} -> tag '{tag.Name}'  tracks={len(cids):>5}  new_links={n:>5}")

# --- Situation ---
scat = category("Situation")
print(f"\n[Situation] category id {scat.ID}")
for pid, sit in SITUATION_MAP.items():
    p = db.get_playlist(ID=pid)
    if not p:
        print(f"    ! playlist {pid} missing"); continue
    cids = {s.ContentID for s in db.get_playlist_songs(PlaylistID=pid)}
    for c in cids:
        comment_plan.setdefault(c, set()).add("#" + sit.replace(" ", "-").lower())
    tag = find_child_ci(scat.ID, sit)
    if not tag:
        print(f"    ! Situation tag '{sit}' missing"); continue
    n = link(tag.ID, cids)
    total += n
    print(f"    {p.Name:<12} -> '{sit}'  tracks={len(cids):>4}  new_links={n:>4}")

# --- comment mirror ---
changed = 0
for cid, tags in comment_plan.items():
    c = db.get_content(ID=cid)
    if not c:
        continue
    cur = c.Commnt or ""
    missing = sorted(h for h in tags if h not in cur)
    if not missing:
        continue
    if APPLY:
        c.Commnt = (cur.rstrip() + " " + " ".join(missing)).strip() if cur.strip() else " ".join(missing)
    changed += 1
print(f"\n[comments] tracks whose comment gains hashtags: {changed}")
print(f"TOTAL new song-tag links: {total}")

lib = db.query(tables.DjmdContent).filter(tables.DjmdContent.rb_local_deleted == 0).count()
print(f"library tracks (unchanged): {lib}")

if APPLY:
    if get_rekordbox_pid():
        print("\nRefusing — rekordbox is running."); db.session.rollback(); sys.exit(1)
    src = Path(get_config("rekordbox6", "db_path"))
    backup = src.with_name(src.name + ".backup-" + now.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(src, backup); print(f"\nBacked up DB -> {backup}")
    db.commit(); print("Committed. Genre + Situation tags written.")
else:
    print("\nDRY RUN — no changes written."); db.session.rollback()
