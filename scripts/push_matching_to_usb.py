#!/usr/bin/env python
"""Push the FULL Matching set from master.db directly into a USB stick's
exportLibrary.db (OneLibrary / Device Library Plus).

Why: rekordbox's incremental device-sync does not refresh the recommendedLike
table when rows were written outside the rekordbox UI (its change ledger never
sees them) — observed 2026-07-04: three syncs in a row kept a stale 11k subset.
Writing the export DB directly sidesteps that, and since sync leaves this table
alone, the injection survives subsequent syncs.

The export DB is SQLCipher-encrypted with a PUBLIC static key (the same for all rekordbox installs;
not shipped with this repo — supply via RB_DLP_KEY or a .dlp_key file). Its content table carries
masterContentId -> master.db ContentID, giving an exact ID mapping. Pairs whose
partner track is not on the stick are skipped (the player couldn't load them).

Safety: backs up exportLibrary.db next to itself (.bak-<ts>) before writing.
Close rekordbox (or don't sync) while this runs.

Usage: ~/.rekordbox-venv/bin/python push_matching_to_usb.py [/Volumes/STICK] [--apply]
       (default volume: /Volumes/DR_COMPUTER)
"""
import sys, shutil, datetime
from pathlib import Path
import sqlcipher3
from pyrekordbox import Rekordbox6Database
from sqlalchemy import text

import os
DLP_KEY = os.environ.get("RB_DLP_KEY", "").strip()
if not DLP_KEY:
    _kf = Path(__file__).with_name(".dlp_key")
    if _kf.exists():
        DLP_KEY = _kf.read_text().strip()
if not DLP_KEY:
    sys.exit("Device Library Plus key required: set RB_DLP_KEY or create scripts/.dlp_key.\n"
             "The key is a static SQLCipher key shared by all rekordbox installs; it is not\n"
             "distributed with this repo — extract it from your own rekordbox installation.")
APPLY = "--apply" in sys.argv
vols = [a for a in sys.argv[1:] if a.startswith("/")]
VOL = Path(vols[0] if vols else "/Volumes/DR_COMPUTER")
EXPORT = VOL / "PIONEER/rekordbox/exportLibrary.db"
assert EXPORT.exists(), f"{EXPORT} not found — is the stick mounted?"

mdb = Rekordbox6Database()
pairs = mdb.session.execute(text(
    "SELECT ContentID1, ContentID2 FROM djmdRecommendLike WHERE rb_local_deleted=0")).fetchall()

db = sqlcipher3.connect(str(EXPORT))
db.execute(f"PRAGMA key = '{DLP_KEY}'")
idmap = {str(m): c for c, m in db.execute(
    "SELECT content_id, masterContentId FROM content WHERE masterContentId IS NOT NULL")}
cur = db.execute("SELECT COUNT(*) FROM recommendedLike").fetchone()[0]

ms = int(datetime.datetime.now().timestamp() * 1000)
rows, skipped = [], 0
for a, b in pairs:
    ca, cb = idmap.get(str(a)), idmap.get(str(b))
    if ca is None or cb is None: skipped += 1; continue
    rows.append((ca, cb, None, ms))

print(f"{'APPLY' if APPLY else 'DRY-RUN'} — push matching to {VOL.name}")
print(f"   master pairs {len(pairs)} | stick content {len(idmap)} | currently on stick {cur}")
print(f"   will write {len(rows)} pairs (skipping {skipped} with off-stick partners)")

if not APPLY:
    print("\nDRY RUN — no writes. Re-run with --apply (rekordbox closed / not syncing).")
    sys.exit(0)

bk = EXPORT.with_name(f"exportLibrary.db.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}")
shutil.copy2(EXPORT, bk); print(f"backup -> {bk}")
db.execute("DELETE FROM recommendedLike")
db.executemany("INSERT INTO recommendedLike (content_id_1, content_id_2, rating, createdDate) VALUES (?,?,?,?)", rows)
db.commit()
db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
n = db.execute("SELECT COUNT(*) FROM recommendedLike").fetchone()[0]
db.close()
print(f"Committed. Stick now has {n} matching pairs. Eject cleanly, then test on the XZ.")
