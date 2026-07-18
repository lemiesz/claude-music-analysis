#!/usr/bin/env python
"""Deterministic rekordbox tagging + duplicate merge.

Scope:
  * Create My Tag categories `Label` and `Source`, tag every track in the
    mapped playlists.
  * Mirror each track's assigned tags into its Comment field as #hashtags
    (CDJ-visible), preserving any existing comment. Idempotent.
  * Delete the confirmed duplicate playlist `labels/anjuna`.

Does NOT touch the native Label field (deferred to a future API-based pass).

Usage:
    python tag_and_merge.py            # DRY RUN: report only, no writes
    python tag_and_merge.py --apply    # back up master.db, then write + commit

Rekordbox MUST be closed for --apply (pyrekordbox.commit() enforces this).
Idempotent: re-running skips categories/tags/links/hashtags that already exist.
"""
import sys
import uuid
import shutil
import datetime
from pathlib import Path

from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables
from pyrekordbox.config import get_config

APPLY = "--apply" in sys.argv

# ---------------------------------------------------------------------------
# Tag map:  category -> { tag_name: [playlist_id, ...] }
# ---------------------------------------------------------------------------
LABEL = {
    "adid":            ["1113562163"],
    "anjuna":          ["1504717863"],                # anjuna-hq (labels/anjuna is an exact dup -> deleted)
    "dirtybird":       ["2304839636", "887706024"],
    "stil-vor-talent": ["4058489127"],
    "fckng-serious":   ["1202567920"],
    "whos-afraid-138": ["3870012562"],
    "wakaan":          ["1562333594"],
    "iboga":           ["1682698814", "4074863434"],
    "defected":        ["3194469167", "1517580875"],
    "toolroom":        ["1373366818", "3429518904"],
    "kompakt":         ["1340947428"],
    "crosstown-rebels":["2400781866"],
    "desous":          ["125552494", "3441756417"],
    "ektoplazm":       ["619884394"],
    "bouq":            ["1356828559"],
    "basement-discos": ["3757910629"],
}
SOURCE = {
    "spotify":  ["2684760280", "442378032"],
    "beatport": ["1416639744"],
}
CATEGORIES = {"Label": LABEL, "Source": SOURCE}

DUP_TO_DELETE = ("1219220828", "labels/anjuna")   # verified exact dup of anjuna-hq (287/287)

# ---------------------------------------------------------------------------
db = Rekordbox6Database()
now = datetime.datetime.now()

# content_id -> set of hashtag strings to ensure in its comment
comment_plan = {}


def playlist_content_ids(pid):
    seen, out = set(), []
    for s in db.get_playlist_songs(PlaylistID=str(pid)):
        if s.ContentID not in seen:
            seen.add(s.ContentID)
            out.append(s.ContentID)
    return out


def find_root_category(name):
    return (db.query(tables.DjmdMyTag)
              .filter(tables.DjmdMyTag.ParentID == "root", tables.DjmdMyTag.Name == name)
              .one_or_none())


def find_child_tag(parent_id, name):
    return (db.query(tables.DjmdMyTag)
              .filter(tables.DjmdMyTag.ParentID == str(parent_id), tables.DjmdMyTag.Name == name)
              .one_or_none())


def next_root_seq():
    seqs = [int(r.Seq) for r in db.query(tables.DjmdMyTag)
            .filter(tables.DjmdMyTag.ParentID == "root").all()]
    return (max(seqs) + 1) if seqs else 1


def make_mytag(name, attribute, parent_id, seq):
    row = tables.DjmdMyTag(
        ID=str(db.generate_unused_id(tables.DjmdMyTag)),
        Seq=seq, Name=name, Attribute=attribute, ParentID=str(parent_id),
        UUID=str(uuid.uuid4()), created_at=now, updated_at=now,
    )
    db.add(row)
    return row


def existing_links_for_tag(mytag_id):
    return {r.ContentID for r in db.query(tables.DjmdSongMyTag)
            .filter(tables.DjmdSongMyTag.MyTagID == str(mytag_id)).all()}


def link_song(mytag_id, content_id, track_no):
    db.add(tables.DjmdSongMyTag(
        ID=str(uuid.uuid4()), MyTagID=str(mytag_id), ContentID=str(content_id),
        TrackNo=track_no, UUID=str(uuid.uuid4()), created_at=now, updated_at=now,
    ))


# ---------------------------------------------------------------------------
# 1) My Tag categories + tags + song links
# ---------------------------------------------------------------------------
print(f"{'APPLY' if APPLY else 'DRY-RUN'} — rekordbox tag + comment mirror + merge\n")
total_links = 0
root_seq = next_root_seq()

for cat_name, tagmap in CATEGORIES.items():
    cat = find_root_category(cat_name)
    if cat is None:
        cat = make_mytag(cat_name, attribute=1, parent_id="root", seq=root_seq)
        root_seq += 1
        print(f"[+category] {cat_name}  (id {cat.ID})")
    else:
        print(f"[=category] {cat_name}  (exists, id {cat.ID})")

    for i, (tag_name, pids) in enumerate(tagmap.items(), start=1):
        cids, seen = [], set()
        for pid in pids:
            for cid in playlist_content_ids(pid):
                if cid not in seen:
                    seen.add(cid)
                    cids.append(cid)
        # record hashtag intent for comment mirroring
        for cid in cids:
            comment_plan.setdefault(cid, set()).add("#" + tag_name)

        tag = find_child_tag(cat.ID, tag_name)
        if tag is None:
            tag = make_mytag(tag_name, attribute=0, parent_id=cat.ID, seq=i)
            already = set()
            verb = "+tag"
        else:
            already = existing_links_for_tag(tag.ID)
            verb = "=tag"

        new_cids = [c for c in cids if c not in already]
        for n, cid in enumerate(new_cids, start=len(already) + 1):
            link_song(tag.ID, cid, n)
        total_links += len(new_cids)
        print(f"    [{verb}] {tag_name:<18} tracks={len(cids):>4}  new_links={len(new_cids):>4}")

# ---------------------------------------------------------------------------
# 2) Mirror tags into the Comment field (preserve existing, idempotent)
# ---------------------------------------------------------------------------
comments_changed = 0
for cid, hashtags in comment_plan.items():
    c = db.get_content(ID=cid)
    if c is None:
        continue
    current = c.Commnt or ""
    missing = sorted(h for h in hashtags if h not in current)
    if not missing:
        continue
    new_comment = (current.rstrip() + " " + " ".join(missing)).strip() if current.strip() else " ".join(missing)
    if APPLY:
        c.Commnt = new_comment
    comments_changed += 1
print(f"\n[comments] tracks whose comment gains hashtags: {comments_changed}")

# ---------------------------------------------------------------------------
# 3) Merge: delete verified duplicate playlist
# ---------------------------------------------------------------------------
dup_id, dup_name = DUP_TO_DELETE
dup = db.get_playlist(ID=dup_id)
if dup is not None:
    print(f"[merge]  delete duplicate playlist '{dup_name}' (id {dup_id})")
    if APPLY:
        db.delete_playlist(dup_id)
else:
    print(f"[merge]  duplicate '{dup_name}' already gone")

print(f"\nTOTAL new song-tag links: {total_links}")

# ---------------------------------------------------------------------------
if APPLY:
    src = Path(get_config("rekordbox6", "db_path"))
    backup = src.with_name(src.name + ".backup-" + now.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(src, backup)
    print(f"\nBacked up DB -> {backup}")
    db.commit()          # sets USN counters; refuses if rekordbox is running
    print("Committed. Tags + comments written.")
else:
    print("\nDRY RUN — no changes written. Re-run with --apply (rekordbox closed).")
    db.session.rollback()
