#!/usr/bin/env python
"""Loudness-invariant, full-track energy features. ffmpeg -> numpy (no librosa).

Why this exists: the old `energy` features were unusable as a relative scale —
audio_features.py used raw RMS (measures the MASTERING, not the music) over a
fixed 30-120s window (often an intro/breakdown, not the drop), and the My Tag
Energy bank was thresholded off `mood_aggressive`, a mood classifier that reads
harsh timbre as "peak" (dnb came out 66% "warmup").

Design rules:
  1. Each track is normalised by its OWN rms before measurement -> master gain
     cancels out. Energy here means arrangement density + spectral weight.
  2. Whole track (capped at CAP seconds), not a fixed window.
  3. Summarised at percentiles, not means: p90 = what the track does at peak.
  4. Every component is a named, inspectable number - no black-box classifier.

Writes source='dsp2' into energy_features.sqlite. Incremental: skips done ids.
Usage: python3.12 energy_analyze.py [--limit N] [--workers 6] [--ids-from FILE] [--refetch]
"""
import os, sys, sqlite3, subprocess, argparse, datetime, math
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "metadata.sqlite")
OUT = os.path.join(HERE, "energy_features.sqlite")
SR, NFFT, HOP, CAP = 22050, 2048, 512, 720.0
SOURCE = "dsp2"

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--workers", type=int, default=6)
ap.add_argument("--refetch", action="store_true")
ap.add_argument("--ids-from", default=None)
args = ap.parse_args()

EDGES = [(20,60,'sub'), (60,120,'bass'), (120,500,'lowmid'),
         (500,2000,'mid'), (2000,6000,'hi'), (6000,11025,'air')]

def decode(path):
    p = subprocess.run(
        ["ffmpeg","-v","quiet","-nostdin","-t",str(CAP),"-i",path,
         "-f","f32le","-acodec","pcm_f32le","-ac","1","-ar",str(SR),"-"],
        capture_output=True)
    if p.returncode != 0 or not p.stdout: return None
    return np.frombuffer(p.stdout, dtype=np.float32)

def analyse(path, bpm):
    y = decode(path)
    if y is None or len(y) < SR * 20: return None
    y = y[np.isfinite(y)]
    rms_global = float(np.sqrt(np.mean(y**2)) + 1e-12)
    y = y / rms_global                      # <-- loudness invariance

    n = 1 + (len(y) - NFFT) // HOP
    if n < 40: return None
    idx = np.arange(NFFT)[None,:] + HOP*np.arange(n)[:,None]
    win = np.hanning(NFFT).astype(np.float32)
    S = np.abs(np.fft.rfft(y[idx] * win, axis=1))          # (frames, bins)
    freqs = np.fft.rfftfreq(NFFT, 1.0/SR)
    P = S**2
    tot = P.sum(1) + 1e-12

    bands = {nm: P[:, (freqs>=lo)&(freqs<hi)].sum(1) for lo,hi,nm in EDGES}
    fe = tot                                                # frame energy
    p95 = np.percentile(fe, 95)
    flux = np.maximum(np.diff(S, axis=0), 0).sum(1)
    flux = flux / (np.median(flux) + 1e-12)

    out = {}
    out["sustain"]   = float((fe > 0.5*p95).mean())          # how much runs at full tilt
    out["crest"]     = float(np.percentile(fe,99) / (np.percentile(fe,50)+1e-12))
    out["dyn_range"] = float(np.log10(np.percentile(fe,90)/(np.percentile(fe,10)+1e-12)+1e-12))
    for nm in bands: out[f"r_{nm}"] = float(np.median(bands[nm]/tot))
    out["flux_p50"]  = float(np.percentile(flux,50))
    out["flux_p90"]  = float(np.percentile(flux,90))
    cen = (freqs[None,:]*P).sum(1)/tot
    out["centroid"]  = float(np.median(cen))

    thr = np.percentile(flux, 75)
    peaks = (flux[1:-1] > thr) & (flux[1:-1] >= flux[:-2]) & (flux[1:-1] > flux[2:])
    dur = len(y)/SR
    out["onset_rate"] = float(peaks.sum()/dur)
    if bpm and bpm > 40:
        out["onsets_per_beat"] = float(peaks.sum()/(dur*bpm/60.0))
        # beat-rate periodicity of the energy envelope (4-on-floor / sidechain pump)
        env = fe - fe.mean(); fps = SR/HOP
        F = np.abs(np.fft.rfft(env * np.hanning(len(env))))
        ff = np.fft.rfftfreq(len(env), 1.0/fps)
        bf = bpm/60.0
        band = (ff > bf*0.94) & (ff < bf*1.06)
        out["pump"] = float(F[band].max()/(np.median(F[(ff>0.1)&(ff<20)])+1e-12)) if band.any() else 0.0
    out["dur"] = float(dur)
    return out

def job(t):
    rb, path, bpm = t
    try:
        r = analyse(path, bpm)
        return (rb, r)
    except Exception as e:
        return (rb, None)

def main():
    con = sqlite3.connect(OUT)
    con.execute("""CREATE TABLE IF NOT EXISTS feature (
      rb_id TEXT, source TEXT, name TEXT, num REAL, txt TEXT, fetched_at TEXT,
      PRIMARY KEY (rb_id, source, name))""")
    done = set() if args.refetch else {r[0] for r in con.execute(
        "SELECT DISTINCT rb_id FROM feature WHERE source=?", (SOURCE,))}

    rdb = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    # BPM from rekordbox itself (full coverage), falling back to the essentia subset
    bpm = {r[0]: r[1] for r in rdb.execute(
        "SELECT rb_id, num FROM feature WHERE source='essentia' AND name='bpm'")}
    try:
        from pyrekordbox import Rekordbox6Database
        from pyrekordbox.db6 import tables as _t
        _db = Rekordbox6Database()
        for c in _db.query(_t.DjmdContent).filter(_t.DjmdContent.rb_local_deleted == 0).all():
            if c.BPM: bpm[str(c.ID)] = c.BPM / 100.0
    except Exception as e:
        print("rekordbox BPM unavailable, using essentia subset:", e, flush=True)
    todo = []
    only = None
    if args.ids_from:
        only = {l.strip() for l in open(args.ids_from) if l.strip()}
    for rb, path in rdb.execute("SELECT rb_id, path FROM track_files"):
        if rb in done or (only is not None and rb not in only): continue
        if not os.path.exists(path): continue
        todo.append((rb, path, bpm.get(rb)))
        if args.limit and len(todo) >= args.limit: break
    print(f"to analyse: {len(todo)}  (workers {args.workers})", flush=True)

    t0 = datetime.datetime.now(); ok = fail = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(job, t) for t in todo]
        for k, f in enumerate(as_completed(futs), 1):
            rb, r = f.result()
            if r is None: fail += 1
            else:
                ok += 1
                now = datetime.datetime.now().isoformat()
                con.executemany(
                    "INSERT OR REPLACE INTO feature (rb_id,source,name,num,txt,fetched_at) VALUES (?,?,?,?,NULL,?)",
                    [(rb, SOURCE, nm, v, now) for nm, v in r.items()])
            if k % 100 == 0:
                con.commit()
                el = (datetime.datetime.now()-t0).total_seconds()
                # remaining, NOT total-run time — the old hardcoded-total form
                # read misleadingly high near the end of a run.
                print(f"  {k}/{len(todo)}  ok={ok} fail={fail}  {el/k:.2f}s/track  "
                      f"remaining={((len(todo)-k)*el/k)/60:.0f}min", flush=True)
    con.commit()
    el = (datetime.datetime.now()-t0).total_seconds()
    print(f"done: ok={ok} fail={fail} in {el/60:.1f}min ({el/max(len(todo),1):.2f}s/track)")

if __name__ == "__main__":
    main()
