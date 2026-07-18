#!/usr/bin/env python
"""Audio features via ESSENTIA (source-built for arm64). Better danceability/BPM/
key than the librosa heuristic. Runs in ~/.essentia-venv (py3.11).

Reads file paths from metadata.sqlite (read-only), writes to its own
essentia_features.sqlite `feature` table (source='essentia'); merge later with
`analyzer.py merge-audio` (point it at this db) or the merge helper.

Usage:  ~/.essentia-venv/bin/python essentia_features.py [--limit N] [--refetch]
"""
import os, sys, sqlite3, datetime, argparse, warnings
from urllib.parse import unquote
import numpy as np
warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DB = os.path.join(HERE, "metadata.sqlite")
OUT_DB = os.path.join(HERE, "essentia_features.sqlite")
SR = 44100

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--refetch", action="store_true")
args = ap.parse_args()

import essentia
essentia.log.infoActive = False
essentia.log.warningActive = False
import essentia.standard as es

odb = sqlite3.connect(OUT_DB)
odb.execute("""CREATE TABLE IF NOT EXISTS feature (
  rb_id TEXT, source TEXT, name TEXT, num REAL, txt TEXT, fetched_at TEXT,
  PRIMARY KEY (rb_id, source, name))""")
rdb = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True, timeout=60)
rdb.execute("PRAGMA busy_timeout=60000")

def put(rb, name, value):
    num = float(value) if isinstance(value, (int, float, np.floating)) else None
    txt = None if num is not None else str(value)
    odb.execute("INSERT OR REPLACE INTO feature VALUES (?,?,?,?,?,?)",
                (rb, "essentia", name, num, txt, datetime.datetime.now().isoformat()))

def done(rb):
    return odb.execute("SELECT 1 FROM feature WHERE rb_id=? AND source='essentia' LIMIT 1", (rb,)).fetchone()

dance_algo = es.Danceability(sampleRate=SR)
rhythm_algo = es.RhythmExtractor2013(method="degara")
key_algo = es.KeyExtractor()

rows = rdb.execute("SELECT rb_id, path FROM track_files").fetchall()
n_ok = n_skip = n_err = 0
for rb, path in rows:
    if not args.refetch and done(rb):
        continue
    p = unquote(path[7:]) if path.startswith("file://") else path
    if not os.path.exists(p):
        n_skip += 1; continue
    try:
        audio = es.MonoLoader(filename=p, sampleRate=SR)()
        if audio.size < SR:
            n_skip += 1; continue
        mid = audio[30 * SR: 120 * SR] if audio.size > 130 * SR else audio   # ~90s slice
        dance, _ = dance_algo(mid)                    # ~0..3, higher = more danceable
        bpm, _, beat_conf, _, _ = rhythm_algo(mid)
        key, scale, key_strength = key_algo(mid)
        energy = float(np.sqrt(np.mean(mid ** 2)))
        put(rb, "danceability", round(min(dance / 3.0, 1.0), 3))   # normalized 0..1
        put(rb, "danceability_raw", round(float(dance), 3))
        put(rb, "bpm", round(float(bpm), 1)); put(rb, "beat_confidence", round(float(beat_conf), 3))
        put(rb, "energy", round(energy, 5))
        put(rb, "key", key); put(rb, "mode", scale); put(rb, "key_strength", round(float(key_strength), 3))
        odb.commit(); n_ok += 1
        print(f"  ok {os.path.basename(p)[:38]:<38} bpm={bpm:5.0f} dance={dance:.2f} "
              f"key={key}{'m' if scale=='minor' else ''} energy={energy:.3f}", flush=True)
    except Exception as e:
        n_err += 1
        print(f"  ERR {os.path.basename(p)[:38]}: {type(e).__name__}: {str(e)[:45]}", flush=True)
    if args.limit and (n_ok + n_err) >= args.limit:
        break

print(f"\ndone: analyzed={n_ok} skipped={n_skip} errors={n_err}", flush=True)
