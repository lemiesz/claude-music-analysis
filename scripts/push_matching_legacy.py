#!/usr/bin/env python
"""Push the FULL Matching set into a USB stick's LEGACY database
(exportExt.pdb, table type 0) — the file legacy players (XDJ-XZ) actually
read for Related Tracks > Matching.

Why: rekordbox's incremental device-sync only ever appends its own ledger
deltas to this table, so script-written djmdRecommendLike rows never fully
arrive (observed 2026-07-04: stick held an arbitrary 7,919-row subset of
52,636 master pairs — e.g. "neck" showed 1 of its 16 matches on the XZ).
push_matching_to_usb.py fixes exportLibrary.db (OneLibrary), but the XZ
ignores that file.

Format (reverse-engineered + validated 2026-07-04 against the live stick):
  - PDB file: 4KB pages; header @0: u4 zero, u4 page_len, u4 num_tables,
    u4 next_unused_page, u4 unknown, u4 sequence; table entries @28,
    16 bytes each: type, empty_candidate, first_page, last_page.
  - Matching rows (exportExt table type 0): 24 bytes =
    u4 createdMsLow, u4 createdMsHigh, u4 partner_id, u4 owner_id,
    u4 like_rate(0), u4 flags(3). One row per unordered pair; players read
    both directions. IDs are the legacy/OneLibrary content_id, mapped from
    master via exportLibrary.db content.masterContentId (identity verified
    against export.pdb track rows by file size).
  - Page: heap of rows from offset 40; row index grows from page end in
    groups of 16 (16 x u2 heap offsets stored backwards + u2 present flags);
    max 154 rows/page.

Safety gates (all must pass before any disk write):
  1. round-trip: rebuild an existing full page from its parsed rows ->
     must be byte-identical to the original
  2. ID mapping audit: every mapped legacy id must exist in export.pdb's
     track table with the same file size as master (>=99.9% required)
  3. post-build: independent re-parse of the new image — every desired pair
     present exactly once, every non-type-0 page byte-identical
Backs up exportExt.pdb next to itself first. Do NOT let rekordbox sync
while this runs.

Usage: ~/.rekordbox-venv/bin/python push_matching_legacy.py [/Volumes/STICK] [--apply]
"""
import sys, shutil, struct, datetime
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
EXT = VOL / "PIONEER/rekordbox/exportExt.pdb"
PDB = VOL / "PIONEER/rekordbox/export.pdb"
ONE = VOL / "PIONEER/rekordbox/exportLibrary.db"
for f in (EXT, PDB, ONE):
    assert f.exists(), f"{f} not found — is the stick mounted?"

PAGE = 4096
ROW = 24
MAXROWS = 154            # 40B header + 154*24 heap + 10*36 index == 4096

# ---------------- pdb helpers ----------------
def tables_of(data):
    n = struct.unpack("<I", data[8:12])[0]
    out = []
    for t in range(n):
        typ, ec, first, last = struct.unpack("<4I", data[28+t*16:28+t*16+16])
        out.append(dict(idx=t, type=typ, empty_candidate=ec, first=first, last=last))
    return out

def chain_pages(data, first, last):
    pages, pg, seen = [], first, set()
    while pg and pg not in seen:
        seen.add(pg); pages.append(pg)
        if pg == last: break
        pg = struct.unpack("<I", data[pg*PAGE+12:pg*PAGE+16])[0]
    return pages

def page_rows(data, pg):
    off = pg * PAGE
    flags, nrs = data[off+27], data[off+24]
    nrl = struct.unpack("<H", data[off+34:off+36])[0]
    n = nrl if (nrl > nrs and nrl != 0x1FFF) else nrs
    rows = []
    if (flags & 0x40) or not n:
        return rows
    for g in range((n + 15) // 16):
        base = off + PAGE - g*36
        pf = struct.unpack("<H", data[base-4:base-2])[0]
        for i in range(min(16, n - g*16)):
            if (pf >> i) & 1:
                ro = struct.unpack("<H", data[base-6-2*i:base-4-2*i])[0]
                rows.append(struct.unpack("<6I", data[off+40+ro:off+40+ro+ROW]))
    return rows

def build_page(template_hdr, page_index, next_page, rows, u16):
    """Build one 4KB type-0 data page holding `rows` (list of 6-tuples)."""
    n = len(rows)
    assert 0 < n <= MAXROWS
    buf = bytearray(PAGE)
    buf[0:40] = template_hdr                       # start from a real header
    struct.pack_into("<I", buf, 4, page_index)
    struct.pack_into("<I", buf, 8, 0)              # table type 0
    struct.pack_into("<I", buf, 12, next_page)
    struct.pack_into("<I", buf, 16, u16)           # transaction stamp
    buf[24] = n                                    # num_rows_small
    ngroups = (n + 15) // 16
    used = n * ROW
    # exact rekordbox accounting (verified on all 54 live pages): full groups
    # cost 36 bytes, a partial group of k rows costs 4 + 2k
    fullg, k = n // 16, n % 16
    free = PAGE - 40 - used - (36 * fullg + (4 + 2 * k if k else 0))
    struct.pack_into("<H", buf, 28, free)          # free_size
    struct.pack_into("<H", buf, 30, used)          # used_size
    struct.pack_into("<H", buf, 34, n)             # num_rows_large (inert: nrs wins)
    for i, r in enumerate(rows):
        struct.pack_into("<6I", buf, 40 + i*ROW, *r)
    for g in range(ngroups):
        base = PAGE - g*36
        pf = 0
        for i in range(min(16, n - g*16)):
            struct.pack_into("<H", buf, base-6-2*i, (g*16 + i) * ROW)
            pf |= 1 << i
        struct.pack_into("<H", buf, base-4, pf)
    return bytes(buf)

# ---------------- load inputs ----------------
mdb = Rekordbox6Database()
pairs = mdb.session.execute(text(
    "SELECT ContentID1, ContentID2 FROM djmdRecommendLike WHERE rb_local_deleted=0")).fetchall()
msize = {str(i): fs for i, fs in mdb.session.execute(text(
    "SELECT ID, FileSize FROM djmdContent WHERE rb_local_deleted=0"))}

odb = sqlcipher3.connect(str(ONE))
odb.execute(f"PRAGMA key = '{DLP_KEY}'")
m2l = {str(m): c for c, m in odb.execute(
    "SELECT content_id, masterContentId FROM content WHERE masterContentId IS NOT NULL")}
odb.close()

ext = bytearray(EXT.read_bytes())
pdb = PDB.read_bytes()

# ---------------- gate 2: ID mapping audit vs export.pdb track table ----------------
ptables = tables_of(pdb)
ttab = next(t for t in ptables if t["type"] == 0)
legacy_size = {}
for pg in chain_pages(pdb, ttab["first"], ttab["last"]):
    off = pg * PAGE
    flags, nrs = pdb[off+27], pdb[off+24]
    nrl = struct.unpack("<H", pdb[off+34:off+36])[0]
    n = nrl if (nrl > nrs and nrl != 0x1FFF) else nrs
    if (flags & 0x40) or not n: continue
    for g in range((n + 15) // 16):
        base = off + PAGE - g*36
        pf = struct.unpack("<H", pdb[base-4:base-2])[0]
        for i in range(min(16, n - g*16)):
            if not (pf >> i) & 1: continue
            ro = struct.unpack("<H", pdb[base-6-2*i:base-4-2*i])[0]
            r = off + 40 + ro
            if struct.unpack("<H", pdb[r:r+2])[0] != 0x24: continue
            legacy_size[struct.unpack("<I", pdb[r+72:r+76])[0]] = struct.unpack("<I", pdb[r+16:r+20])[0]
ok = bad = missing = 0
for m, l in m2l.items():
    if m not in msize: continue
    if l not in legacy_size: missing += 1
    elif legacy_size[l] == msize[m]: ok += 1
    else: bad += 1
total = ok + bad + missing
print(f"ID mapping audit: {ok}/{total} verified by file size ({bad} mismatched, {missing} not in export.pdb)")
assert total and ok / total >= 0.999, "ID mapping audit failed — aborting"

# ---------------- build desired row set ----------------
seen, rows_by_owner, skipped = set(), {}, 0
for c1, c2 in pairs:
    l1, l2 = m2l.get(str(c1)), m2l.get(str(c2))
    if l1 is None or l2 is None: skipped += 1; continue
    key = (min(l1, l2), max(l1, l2))
    if key in seen: continue
    seen.add(key)
    rows_by_owner.setdefault(l1, []).append(l2)
ms = int(datetime.datetime.now().timestamp() * 1000)
lo32, hi32 = ms & 0xFFFFFFFF, ms >> 32
new_rows = []
for owner in sorted(rows_by_owner):
    for p in sorted(rows_by_owner[owner]):
        new_rows.append((lo32, hi32, p, owner, 0, 3))

# ---------------- current legacy state ----------------
etables = tables_of(ext)
etab = next(t for t in etables if t["type"] == 0)
epages = chain_pages(ext, etab["first"], etab["last"])
# the chain starts with a "strange" index page (flags & 0x40) that players
# expect at the head of every table — preserve it verbatim, NEVER rewrite it
# (rewriting it as a data page broke the XZ on the first attempt 2026-07-04)
head_pages = []
for pg in epages:
    if ext[pg*PAGE+27] & 0x40: head_pages.append(pg)
    else: break
data_pages = epages[len(head_pages):]
assert head_pages, \
    "chain does not start with a strange index page — restore a clean backup first"
assert all(not (ext[pg*PAGE+27] & 0x40) for pg in data_pages), \
    "strange page in mid-chain — layout not understood, aborting"
cur_rows = [r for pg in data_pages for r in page_rows(ext, pg)]
template = None
for pg in data_pages:
    if ext[pg*PAGE+24] == MAXROWS:
        template = pg; break
assert template is not None, "no full data page found to use as template"
tmpl_hdr = bytes(ext[template*PAGE:template*PAGE+40])

# ---------------- gate 1: byte-identical round-trip of the template page ----------------
t_rows = page_rows(ext, template)
t_next = struct.unpack("<I", ext[template*PAGE+12:template*PAGE+16])[0]
t_u16 = struct.unpack("<I", ext[template*PAGE+16:template*PAGE+20])[0]
rebuilt = build_page(tmpl_hdr, template, t_next, t_rows, t_u16)
orig = bytes(ext[template*PAGE:(template+1)*PAGE])
# don't-care bytes (verified stale garbage on the live stick, ignored by
# readers): num_rows_large @34-35, and the 2 gap bytes between index groups
mask = {34, 35}
for g in range((len(t_rows) + 15) // 16):
    base = PAGE - g * 36
    mask.update((base - 2, base - 1))
diffs = [i for i in range(PAGE) if rebuilt[i] != orig[i] and i not in mask]
masked = [i for i in range(PAGE) if rebuilt[i] != orig[i] and i in mask]
if diffs:
    print(f"ROUND-TRIP FAILED: page {template}, {len(diffs)} differing bytes at {diffs[:20]}")
    sys.exit(1)
print(f"round-trip OK: page {template} identical outside don't-care bytes "
      f"({len(t_rows)} rows, {len(masked)} masked stale bytes)")

# ---------------- plan ----------------
npages_needed = (len(new_rows) + MAXROWS - 1) // MAXROWS
old_total_pages = len(ext) // PAGE
extra = max(0, npages_needed - len(data_pages))
new_total = old_total_pages + extra
new_seq = struct.unpack("<I", ext[20:24])[0] + 1
print(f"\n{'APPLY' if APPLY else 'DRY-RUN'} — push matching to LEGACY exportExt.pdb on {VOL.name}")
print(f"   master pairs {len(pairs)} -> legacy rows {len(new_rows)} (skipped {skipped} off-stick)")
print(f"   current table: {len(cur_rows)} rows on {len(data_pages)} data pages (+{len(head_pages)} index head); "
      f"new: {len(new_rows)} rows on {npages_needed} pages ({extra} appended, file +{extra*4}KB)")

# ---------------- build new image in memory ----------------
img = bytearray(ext)
page_ids = data_pages[:npages_needed] + list(range(old_total_pages, old_total_pages + extra))
img += bytes(PAGE * extra)
chunks = [new_rows[i*MAXROWS:(i+1)*MAXROWS] for i in range(npages_needed)]
for k, pg in enumerate(page_ids):
    # rekordbox convention: the last chain page's next points one-past-EOF
    nxt = page_ids[k+1] if k+1 < len(page_ids) else new_total
    img[pg*PAGE:(pg+1)*PAGE] = build_page(tmpl_hdr, pg, nxt, chunks[k], new_seq)
# orphaned old chain pages (if table shrank) are left in place — unreachable, harmless
# file header + table entry, mimicking observed conventions exactly:
#   empty_candidate = first past-EOF page index, next_unused_page = pages + 1
struct.pack_into("<I", img, 28 + etab["idx"]*16 + 4, new_total)
struct.pack_into("<I", img, 28 + etab["idx"]*16 + 12, page_ids[-1])
struct.pack_into("<I", img, 12, new_total + 1)
struct.pack_into("<I", img, 20, new_seq)

# ---------------- gate 3: independent re-parse of the new image ----------------
img = bytes(img)
vt = next(t for t in tables_of(img) if t["type"] == 0)
vpages = chain_pages(img, vt["first"], vt["last"])
vrows = [r for pg in vpages for r in page_rows(img, pg)]
want = {(min(r[2], r[3]), max(r[2], r[3])) for r in new_rows}
got = [(min(r[2], r[3]), max(r[2], r[3])) for r in vrows]
assert len(vrows) == len(new_rows), f"row count {len(vrows)} != {len(new_rows)}"
assert len(set(got)) == len(got) == len(want) and set(got) == want, "pair set mismatch after rebuild"
touched = set(page_ids) | {0}
same = sum(1 for pg in range(old_total_pages) if pg not in touched
           and img[pg*PAGE:(pg+1)*PAGE] == ext[pg*PAGE:(pg+1)*PAGE])
assert same == old_total_pages - len(touched & set(range(old_total_pages))), "untouched pages changed!"
print(f"validation OK: {len(vrows)} rows re-parsed, pair set exact, "
      f"{same} untouched pages byte-identical")

if not APPLY:
    print("\nDRY RUN — no writes. Re-run with --apply (rekordbox must NOT sync during write).")
    sys.exit(0)

bk = EXT.with_name(f"exportExt.pdb.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}")
shutil.copy2(EXT, bk)
print(f"backup -> {bk}")
EXT.write_bytes(img)
print(f"Committed: exportExt.pdb now {len(img)//PAGE} pages, {len(vrows)} matching rows.")
print("Move the backup off the stick, dot_clean, eject cleanly, then test on the XZ.")
