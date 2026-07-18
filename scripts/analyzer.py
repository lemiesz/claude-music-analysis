#!/usr/bin/env python
"""Hybrid metadata analyzer for a rekordbox library, backed by a LOCAL database.

Design: fetching (slow, API) is decoupled from applying (fast, local), and every
lookup is persisted in metadata.sqlite so runs are INCREMENTAL and reusable.

Sources:
  * Discogs  -> label, genre/style, year, catalog#  (search by artist+title)
  * Spotify  -> exact ISRC match -> release year + artist genres (+ audio
               features IF your app still has access; Spotify deprecated the
               audio-features endpoint for new apps in Nov 2024).

Commands:
  python analyzer.py fetch  [--source discogs,spotify] [--limit N] [--all] [--refetch]
  python analyzer.py apply  [--min-confidence 0.55] [--fields genre,label,year,catno]
  python analyzer.py report

Credentials (kept local):
  Discogs: ./.discogs_token   (or $DISCOGS_TOKEN)
  Spotify: ./.spotify_creds   as  client_id:client_secret   (or $SPOTIFY_CREDS)
"""
import os, sys, re, json, time, base64, sqlite3, csv, shutil, datetime, argparse, difflib
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

HERE = Path(__file__).resolve().parent
META_DB = HERE / "metadata.sqlite"
UA = "RekordboxAnalyzer/1.0 (+local personal use)"
JUNK_ARTISTS = {"loopmasters", "", "none"}
JUNK_TITLE = re.compile(r"^[A-Z0-9'_ -]{1,14}$")

# --------------------------------------------------------------------------- DB
mdb = sqlite3.connect(META_DB)
# --- schema ---------------------------------------------------------------
# `track`      : rekordbox identity snapshot (the join key)
# per-source   : one typed table per structured source (discogs, spotify, ...)
# `feature`    : GENERIC extensible store — any (source, name)->value, so new
#                sources (essentia audio features, reccobeats, ...) need NO
#                schema change. Add a fetcher + register it in SOURCES.
# `source_run` : bookkeeping per source run (for incremental/reporting)
mdb.executescript("""
CREATE TABLE IF NOT EXISTS track (
  rb_id TEXT PRIMARY KEY, artist TEXT, title TEXT, isrc TEXT, seen_at TEXT);
CREATE TABLE IF NOT EXISTS discogs (
  rb_id TEXT PRIMARY KEY, matched INT, confidence REAL, release_id INT,
  label TEXT, genre TEXT, style TEXT, year INT, catno TEXT, fetched_at TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS spotify (
  rb_id TEXT PRIMARY KEY, matched INT, track_id TEXT, isrc TEXT, artist_genres TEXT,
  release_date TEXT, energy REAL, danceability REAL, valence REAL, tempo REAL,
  key INT, mode INT, fetched_at TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS feature (
  rb_id TEXT, source TEXT, name TEXT, num REAL, txt TEXT, fetched_at TEXT,
  PRIMARY KEY (rb_id, source, name));
CREATE TABLE IF NOT EXISTS source_run (
  source TEXT, started_at TEXT, finished_at TEXT, fetched INT, matched INT);
-- provenance: what was actually written to rekordbox, from which source at what
-- confidence -> lets a later/better source corroborate and UPGRADE these.
CREATE TABLE IF NOT EXISTS applied (
  rb_id TEXT, field TEXT, value TEXT, source TEXT, confidence REAL, applied_at TEXT,
  PRIMARY KEY (rb_id, field));
CREATE TABLE IF NOT EXISTS http_cache (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS track_files (rb_id TEXT PRIMARY KEY, path TEXT);
""")
mdb.commit()

def put_feature(rb_id, source, name, value):
    """Generic feature sink — future sources use this instead of new columns."""
    num = value if isinstance(value, (int, float)) else None
    txt = None if num is not None else (json.dumps(value) if not isinstance(value, str) else value)
    mdb.execute("INSERT OR REPLACE INTO feature VALUES (?,?,?,?,?,?)",
                (rb_id, source, name, num, txt, datetime.datetime.now().isoformat()))

def cache_get(k):
    r = mdb.execute("SELECT v FROM http_cache WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else None
def cache_put(k, v):
    mdb.execute("INSERT OR REPLACE INTO http_cache VALUES (?,?)", (k, json.dumps(v))); mdb.commit()
def has_row(table, rb_id):
    return mdb.execute(f"SELECT 1 FROM {table} WHERE rb_id=?", (rb_id,)).fetchone() is not None

def http_json(url, headers, key=None, post=None):
    if key:
        c = cache_get(key)
        if c is not None:
            return c
    for attempt in range(4):
        try:
            req = Request(url, headers=headers, data=post, method="POST" if post else "GET")
            with urlopen(req, timeout=30) as r:
                data = json.load(r)
            break
        except HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", 3 * (attempt + 1)))); continue
            data = {"_error": f"HTTP {e.code}", "_body": e.read(300).decode("utf8", "ignore")}; break
        except URLError as e:
            data = {"_error": str(e.reason)}; break
    else:
        data = {"_error": "retries-exhausted"}
    if key:
        cache_put(key, data)
    return data

def norm(s): return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

# --------------------------------------------------------------------------- Discogs
def discogs_token():
    t = os.environ.get("DISCOGS_TOKEN") or (HERE / ".discogs_token").read_text().strip() \
        if (HERE / ".discogs_token").exists() or os.environ.get("DISCOGS_TOKEN") else None
    return t

NOISE_RE = re.compile(r"\s*[\(\[][^)\]]*\b(mix|edit|remix|rmx|version|dub|instrumental|"
                      r"bootleg|rework|vip|extended|original|radio|beatless|acapella)\b[^)\]]*[\)\]]", re.I)
FEAT_RE  = re.compile(r"\s*\b(feat\.?|ft\.?|featuring)\b.*$", re.I)
CATNO_RE = re.compile(r"[\[\(]\s*([A-Za-z]{2,}[A-Za-z0-9]*\d{2,}[A-Za-z0-9]*)\s*[\]\)]")

def clean_title(title):
    s = NOISE_RE.sub("", title or "")
    s = FEAT_RE.sub("", s)
    s = re.sub(r"[\[\(][^)\]]*[\)\]]", "", s)       # drop any remaining brackets
    return re.sub(r"\s+", " ", s).strip(" -")

def extract_catno(title):
    m = CATNO_RE.search(title or "")
    return m.group(1) if m else None

def _dsearch(params, token):
    key = "discogs:" + urlencode(sorted(params.items()))
    live = cache_get(key) is None
    data = http_json("https://api.discogs.com/database/search?" + urlencode({**params, "token": token}),
                     {"User-Agent": UA}, key=key)
    if live:
        time.sleep(1.1)                              # throttle only on real API calls
    return (data or {}).get("results") or []

def discogs_lookup(artist, title, token):
    catno = extract_catno(title)
    clean = clean_title(title)
    results = []
    if catno:                                        # catalog number = strongest signal
        results = _dsearch({"catno": catno, "type": "release", "per_page": 5}, token)
    if not results:
        results = _dsearch({"artist": artist, "track": clean, "type": "release", "per_page": 5}, token)
    if not results:                                  # free-text fallback
        results = _dsearch({"q": f"{artist} {clean}", "type": "release", "per_page": 5}, token)

    q = norm(f"{artist} {clean}"); best, bs = None, 0.0
    for r in results[:5]:
        sc = difflib.SequenceMatcher(None, q, norm(r.get("title", ""))).ratio()
        if catno and norm(catno) == norm(r.get("catno", "")): sc = max(sc, 0.95)
        if norm(artist) and norm(artist) in norm(r.get("title", "")): sc = min(1.0, sc + 0.15)
        if sc > bs: best, bs = r, sc
    if not best:
        return dict(matched=0, confidence=0, release_id=None, label=None, genre=None,
                    style=None, year=None, catno=None, raw="[]")
    lab = best.get("label") or []; sty = best.get("style") or []; gen = best.get("genre") or []
    return dict(matched=1, confidence=round(bs, 2), release_id=best.get("id"),
                label=(lab[0] if lab else None), style=(sty[0] if sty else None),
                genre=(gen[0] if gen else None), year=best.get("year"), catno=best.get("catno"),
                raw=json.dumps(best))

# --------------------------------------------------------------------------- Spotify
_sp_tok = {"tok": None, "exp": 0}
_af_dead = [False]
def spotify_token():
    creds = os.environ.get("SPOTIFY_CREDS")
    f = HERE / ".spotify_creds"
    if not creds and f.exists(): creds = f.read_text().strip()
    if not creds: return None
    if _sp_tok["tok"] and time.time() < _sp_tok["exp"]: return _sp_tok["tok"]
    cid, sec = creds.split(":", 1)
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    d = http_json("https://accounts.spotify.com/api/token",
                  {"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"},
                  post=b"grant_type=client_credentials")
    _sp_tok["tok"] = d.get("access_token"); _sp_tok["exp"] = time.time() + d.get("expires_in", 3600) - 60
    return _sp_tok["tok"]

def spotify_lookup(isrc, tok):
    key = "spotify:isrc:" + isrc
    d = http_json(f"https://api.spotify.com/v1/search?q=isrc:{quote(isrc)}&type=track&limit=1",
                  {"Authorization": "Bearer " + tok}, key=key)
    items = (((d or {}).get("tracks") or {}).get("items")) or []
    if not items:
        return dict(matched=0, track_id=None, isrc=isrc, artist_genres=None, release_date=None,
                    energy=None, danceability=None, valence=None, tempo=None, key=None, mode=None, raw="{}")
    t = items[0]; tid = t["id"]
    rel = (t.get("album") or {}).get("release_date")
    aid = (t.get("artists") or [{}])[0].get("id")
    genres = None
    if aid:
        a = http_json(f"https://api.spotify.com/v1/artists/{aid}",
                      {"Authorization": "Bearer " + tok}, key="spotify:artist:" + aid)
        genres = json.dumps(a.get("genres") or [])
    af = {}
    if not _af_dead[0]:                              # audio-features deprecated for new apps
        af = http_json(f"https://api.spotify.com/v1/audio-features/{tid}",
                       {"Authorization": "Bearer " + tok}, key="spotify:af:" + tid)
        if str((af or {}).get("_error", "")).startswith("HTTP 40"):
            _af_dead[0] = True; af = {}              # stop trying after first rejection
    time.sleep(0.3)
    return dict(matched=1, track_id=tid, isrc=isrc, artist_genres=genres, release_date=rel,
                energy=af.get("energy"), danceability=af.get("danceability"), valence=af.get("valence"),
                tempo=af.get("tempo"), key=af.get("key"), mode=af.get("mode"), raw=json.dumps(t)[:2000])

# --------------------------------------------------------------------------- rekordbox
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables
from pyrekordbox.config import get_config
from pyrekordbox.utils import get_rekordbox_pid
C = tables.DjmdContent

def get_or_create_genre(db, name):
    g = db.query(tables.DjmdGenre).filter(tables.DjmdGenre.Name == name).first()
    return g if g else db.add_genre(name)

def get_or_create_label(db, name):
    l = db.query(tables.DjmdLabel).filter(tables.DjmdLabel.Name == name).first()
    return l if l else db.add_label(name)

def is_junk(t):
    if (getattr(t, "Length", 0) or 0) >= 60:   # real song-length -> never junk (even if Artist is empty)
        return False
    a = norm(t.Artist.Name if t.Artist else "")
    if a in JUNK_ARTISTS: return True
    if t.Title and JUNK_TITLE.match(t.Title) and " " not in (t.Title or ""): return True
    return not a                                 # short + no artist -> one-shot sample

def candidates(db, only, limit, want_all):
    out = []
    for t in db.query(C).filter(C.rb_local_deleted == 0).order_by(C.ID):
        if is_junk(t): continue
        miss = ((("genre" in only) and not (t.GenreID and t.GenreID != "")) or
                (("label" in only) and not (t.LabelID and t.LabelID != "")) or
                (("year" in only) and not t.ReleaseYear))
        if only and not miss: continue
        out.append(t)
        if not want_all and len(out) >= limit: break
    return out

# --------------------------------------------------------------------------- commands
# --- pluggable source registry --------------------------------------------
# A source fetcher has signature (db, track, ctx) -> bool (True = live fetch made).
# TO ADD A SOURCE LATER (e.g. Essentia audio features, ReccoBeats):
#   1) write  def src_<name>(db, t, ctx): ...  persisting into its own table
#      and/or put_feature(t.ID, "<name>", key, value)
#   2) add it to SOURCES below. No other code changes needed.
def src_discogs(db, t, ctx):
    if not ctx["dtok"] or (not ctx["refetch"] and has_row("discogs", t.ID)):
        return False
    artist = t.Artist.Name if t.Artist else ""
    r = discogs_lookup(artist, t.Title, ctx["dtok"])
    mdb.execute("INSERT OR REPLACE INTO discogs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (t.ID, r["matched"], r["confidence"], r["release_id"], r["label"], r["genre"],
                 r["style"], r["year"], r["catno"], datetime.datetime.now().isoformat(), r["raw"]))
    ctx["log"](f"D {artist} - {str(t.Title)[:30]:<30} [{r['confidence']}] "
               f"style={r['style']} label={r['label']} yr={r['year']}")
    return True

def src_spotify(db, t, ctx):
    isrc = (t.ISRC or "").strip()
    if not ctx["stok"] or not isrc or (not ctx["refetch"] and has_row("spotify", t.ID)):
        return False
    r = spotify_lookup(isrc, ctx["stok"])
    mdb.execute("INSERT OR REPLACE INTO spotify VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (t.ID, r["matched"], r["track_id"], r["isrc"], r["artist_genres"], r["release_date"],
                 r["energy"], r["danceability"], r["valence"], r["tempo"], r["key"], r["mode"],
                 datetime.datetime.now().isoformat(), r["raw"]))
    for k in ("energy", "danceability", "valence", "tempo"):
        if r.get(k) is not None:
            put_feature(t.ID, "spotify", k, r[k])
    return True

SOURCES = {"discogs": src_discogs, "spotify": src_spotify}
# future: SOURCES["essentia"] = src_essentia    # local audio features via put_feature()

def cmd_fetch(a):
    db = Rekordbox6Database()
    want = [s for s in a.source.split(",") if s in SOURCES]
    ctx = {"dtok": discogs_token() if "discogs" in want else None,
           "stok": spotify_token() if "spotify" in want else None,
           "refetch": a.refetch, "log": lambda m: None}
    if "spotify" in want and not ctx["stok"]:
        print("(!) spotify requested but no ./.spotify_creds — skipping."); want.remove("spotify")
    only = set(x for x in a.only_missing.split(",") if x)
    cands = candidates(db, only, a.limit, a.all)
    print(f"FETCH — {len(cands)} candidates | sources={want} | "
          f"incremental={'no(refetch)' if a.refetch else 'yes'}\n", flush=True)
    counts = {s: 0 for s in want}
    for i, t in enumerate(cands, 1):
        ctx["log"] = lambda m, i=i: print(f"  [{i}] {m}", flush=True)
        mdb.execute("INSERT OR REPLACE INTO track VALUES (?,?,?,?,?)",
                    (t.ID, t.Artist.Name if t.Artist else "", t.Title,
                     (t.ISRC or "").strip(), datetime.datetime.now().isoformat()))
        for s in want:
            if SOURCES[s](db, t, ctx):
                counts[s] += 1
        mdb.commit()
        if i % 200 == 0:
            print(f"  ... {i}/{len(cands)} processed", flush=True)
    mdb.execute("INSERT INTO source_run VALUES (?,?,?,?,?)",
                (",".join(want), "", datetime.datetime.now().isoformat(), sum(counts.values()), 0))
    mdb.commit()
    print(f"\nfetched: {counts}  (stored in {META_DB.name})", flush=True)

def cmd_apply(a):
    db = Rekordbox6Database()
    if not a.dry_run and get_rekordbox_pid():
        sys.exit("Refusing — rekordbox is running. Quit it first.")
    fields = set(a.fields.split(","))
    n = {f: 0 for f in ("genre", "label", "year", "catno")}
    rowsD = {r[0]: r for r in mdb.execute(
        "SELECT rb_id,matched,confidence,label,genre,style,year,catno FROM discogs")}
    rowsS = {r[0]: r for r in mdb.execute("SELECT rb_id,release_date,artist_genres FROM spotify WHERE matched=1")}
    ids = set(rowsD) | set(rowsS)

    def prov(rb, field, value, source, conf):
        if not a.dry_run:
            mdb.execute("INSERT OR REPLACE INTO applied VALUES (?,?,?,?,?,?)",
                        (rb, field, str(value), source, conf, datetime.datetime.now().isoformat()))

    for rb in ids:
        t = db.get_content(ID=rb)
        if not t: continue
        d = rowsD.get(rb); s = rowsS.get(rb)
        conf = d[2] if d else 0
        d_ok = bool(d and d[1] and conf >= a.min_confidence)
        genre, gsrc, gconf = (None, None, None)
        if d_ok and (d[5] or d[4]):
            genre, gsrc, gconf = (d[5] or d[4]), "discogs", conf
        elif s and s[2]:
            g = json.loads(s[2] or "[]"); genre = g[0].title() if g else None
            gsrc, gconf = ("spotify", None)
        label = d[3] if d_ok else None
        year = (d[6] if d_ok else None) or ((s[1] or "")[:4] if s and s[1] else None)
        ysrc = "discogs" if (d_ok and d[6]) else ("spotify" if (s and s[1]) else None)
        catno = d[7] if d_ok else None

        if "genre" in fields and genre and not (t.GenreID and t.GenreID != ""):
            if not a.dry_run: t.GenreID = get_or_create_genre(db, genre).ID
            n["genre"] += 1; prov(rb, "genre", genre, gsrc, gconf)
        if "label" in fields and label and label.lower() != "none" and not (t.LabelID and t.LabelID != ""):
            if not a.dry_run: t.LabelID = get_or_create_label(db, label).ID
            n["label"] += 1; prov(rb, "label", label, "discogs", conf)
        if "year" in fields and year and not t.ReleaseYear:
            try:
                yv = int(str(year)[:4])
                if not a.dry_run: t.ReleaseYear = yv
                n["year"] += 1; prov(rb, "year", yv, ysrc, conf)
            except (TypeError, ValueError): pass
        if "catno" in fields and catno and str(catno).lower() != "none" and not (t.Subtitle and t.Subtitle.strip()):
            if not a.dry_run: t.Subtitle = str(catno)
            n["catno"] += 1; prov(rb, "catno", catno, "discogs", conf)

    if a.dry_run:
        print(f"DRY-RUN apply @>={a.min_confidence} — WOULD fill: {n}  (no writes)")
        db.session.rollback(); return
    mdb.commit()
    src = Path(get_config("rekordbox6", "db_path"))
    bk = src.with_name(src.name + ".backup-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(src, bk); print(f"backup -> {bk}")
    db.commit()
    print(f"Committed. filled: {n}  (provenance logged in 'applied' table for future upgrades)")

def cmd_paths(a):
    """Export rekordbox file paths into metadata.sqlite for the audio-feature env."""
    db = Rekordbox6Database()
    n = 0
    for t in db.query(C).filter(C.rb_local_deleted == 0):
        fp = t.FolderPath
        if fp and not is_junk(t):                 # skip sample-pack/one-shot junk
            mdb.execute("INSERT OR REPLACE INTO track_files VALUES (?,?)", (t.ID, fp)); n += 1
    mdb.commit()
    print(f"exported {n} file paths to track_files (junk skipped)")

def cmd_mergeaudio(a):
    """Fold essentia_features.sqlite into the main metadata.sqlite feature table."""
    aud = str(HERE / "essentia_features.sqlite")
    mdb.execute("ATTACH DATABASE ? AS aud", (aud,))
    n = mdb.execute("SELECT COUNT(*) FROM aud.feature").fetchone()[0]
    mdb.execute("INSERT OR REPLACE INTO feature SELECT * FROM aud.feature")
    mdb.commit(); mdb.execute("DETACH DATABASE aud")
    print(f"merged {n} audio feature rows into metadata.sqlite")

def cmd_report(a):
    tot = mdb.execute("SELECT COUNT(*) FROM track").fetchone()[0]
    dm = mdb.execute("SELECT COUNT(*) FROM discogs WHERE matched=1").fetchone()[0]
    dn = mdb.execute("SELECT COUNT(*) FROM discogs").fetchone()[0]
    sm = mdb.execute("SELECT COUNT(*) FROM spotify WHERE matched=1").fetchone()[0]
    print(f"local metadata DB: {META_DB}")
    print(f"  tracks seen:        {tot}")
    print(f"  discogs: {dm}/{dn} matched")
    print(f"  spotify: {sm} matched")
    out = HERE / "enrichment_report.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["rb_id","artist","title","d_conf","d_label","d_style","d_year","d_catno"])
        for r in mdb.execute("""SELECT t.rb_id,t.artist,t.title,d.confidence,d.label,d.style,d.year,d.catno
                                FROM track t LEFT JOIN discogs d ON d.rb_id=t.rb_id ORDER BY d.confidence DESC"""):
            w.writerow(r)
    print(f"  csv -> {out}")

ap = argparse.ArgumentParser()
sub = ap.add_subparsers(dest="cmd", required=True)
f = sub.add_parser("fetch"); f.add_argument("--source", default="discogs,spotify")
f.add_argument("--only-missing", default="genre,label"); f.add_argument("--limit", type=int, default=25)
f.add_argument("--all", action="store_true"); f.add_argument("--refetch", action="store_true")
f.set_defaults(fn=cmd_fetch)
p = sub.add_parser("apply"); p.add_argument("--min-confidence", type=float, default=0.6)
p.add_argument("--fields", default="genre,label,year,catno")
p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_apply)
r = sub.add_parser("report"); r.set_defaults(fn=cmd_report)
pa = sub.add_parser("paths"); pa.set_defaults(fn=cmd_paths)
ma = sub.add_parser("merge-audio"); ma.set_defaults(fn=cmd_mergeaudio)
def cmd_mergemood(a):
    aud = str(HERE / "mood_features.sqlite")
    mdb.execute("ATTACH DATABASE ? AS mood", (aud,))
    n = mdb.execute("SELECT COUNT(*) FROM mood.feature").fetchone()[0]
    mdb.execute("INSERT OR REPLACE INTO feature SELECT * FROM mood.feature")
    mdb.commit(); mdb.execute("DETACH DATABASE mood")
    print(f"merged {n} mood feature rows into metadata.sqlite")
mm = sub.add_parser("merge-mood"); mm.set_defaults(fn=cmd_mergemood)
def cmd_mergev2(a):
    v2 = str(HERE / "features_v2.sqlite")
    mdb.execute("CREATE TABLE IF NOT EXISTS embedding (rb_id TEXT PRIMARY KEY, model TEXT, dim INT, vec BLOB, fetched_at TEXT)")
    mdb.execute("ATTACH DATABASE ? AS v2", (v2,))
    nf = mdb.execute("SELECT COUNT(*) FROM v2.feature").fetchone()[0]
    ne = mdb.execute("SELECT COUNT(*) FROM v2.embedding").fetchone()[0]
    mdb.execute("INSERT OR REPLACE INTO feature SELECT * FROM v2.feature")
    mdb.execute("INSERT OR REPLACE INTO embedding SELECT * FROM v2.embedding")
    mdb.commit(); mdb.execute("DETACH DATABASE v2")
    print(f"merged {nf} feature rows + {ne} embeddings into metadata.sqlite")
mv = sub.add_parser("merge-v2"); mv.set_defaults(fn=cmd_mergev2)
args = ap.parse_args()
args.fn(args)
