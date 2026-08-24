#!/usr/bin/env python
"""Turn raw dsp2 + essentia_ml features into ONE relative energy score.

The score is defined, not learned — there is no trustworthy ground truth for
"energy" in this library (the old hi/mid/lo tags came from psy/energy-sets/*
playlists, i.e. psytrance only, so they can't be extrapolated). Every component
below is a named, inspectable number with a stated sign and rationale.

Ranking is deliberately SEPARATE from scoring: the score is continuous, so you
can rank the whole library (--all) or any subset (--playlist NAME) with the same
numbers. Deciles are computed over whatever set you ask for.

Usage:
  energy_score.py --validate            # consistency checks, no writes
  energy_score.py --all                 # library-wide deciles
  energy_score.py --playlist "psy/energy-sets/hi"
"""
import sqlite3, argparse, os, json, numpy as np
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--validate", action="store_true")
ap.add_argument("--recalibrate", action="store_true", help="refreeze energy_calibration.json from the current library")
ap.add_argument("--all", action="store_true")
ap.add_argument("--playlist", default=None)
ap.add_argument("--top", type=int, default=10)
args = ap.parse_args()

# name -> (sign, group). Groups are averaged, then groups are weighted.
COMPONENTS = {
    # DRIVE: how busy / how much movement
    "onsets_per_beat": (+1, "drive"),   # rhythmic density, tempo-normalised
    # pump REMOVED 2026-08-23. It measured the strength of the energy envelope AT
    # THE BEAT FREQUENCY, which is metronomic REGULARITY, not energy. Minimal and
    # hypnotic techno is perfectly regular and scored enormously (one such track
    # hit 1440 against a library median of 128, and took rank 1 of the whole
    # library on that alone). Hard techno, hardstyle and riddim syncopate and
    # distort, which smears the envelope, so they scored in the 1st-21st
    # percentile despite being far harder. Dropping it puts Billx and
    # Klangkuenstler above the hypnotic track, which matches listening.
    # flux_p90 REMOVED 2026-08-23: it measures peakiness, not intensity —
    # correlates +0.528 with dyn_range and +0.408 with crest, both of which carry
    # a NEGATIVE sign here, so at +1 it actively fought the blend (it ended up
    # correlating -0.159 with the final score).
    # WEIGHT: spectral force
    "r_bass":          (+1, "weight"),  # kick/bass band share
    "r_sub":           (+1, "weight"),
    "r_hi":            (+1, "weight"),  # hats/presence -> perceived intensity
    # centroid REMOVED 2026-08-23: inert, contributed +0.068 to the final score.
    # FULLNESS: how much of the track runs at full tilt
    "sustain":         (+1, "full"),
    "crest":           (-1, "full"),    # peaky/sparse = less sustained energy
    "dyn_range":       (-1, "full"),
    # PERCEPTUAL: essentia ML models (full coverage, modest weight by design —
    # these are what the OLD tags relied on exclusively, and they are genre-biased)
    "ml_aggressive":   (+1, "percept"),
    "ml_party":        (+1, "percept"),
    "ml_energy":       (+1, "percept"),
    "ml_relaxed":      (-1, "percept"),
}
# Tuned 2026-08-23 on the full run. percept cut 0.25 -> 0.15 because the essentia
# models still dominated (+0.727) even after group re-standardisation; drive raised
# to compensate. Measured effect: duplicate-pair |dpct| 0.0429 -> 0.0411, |BPM corr|
# 0.085 -> 0.053, max group dominance 0.727 -> 0.661.
GROUP_W = {"drive": 0.40, "weight": 0.25, "full": 0.20, "percept": 0.15}

def load():
    d = sqlite3.connect(f"file:{HERE}/energy_features.sqlite?mode=ro", uri=True)
    f = defaultdict(dict)
    for rb, n, v in d.execute("SELECT rb_id,name,num FROM feature WHERE source='dsp2'"):
        f[rb][n] = v
    md = sqlite3.connect(f"file:{HERE}/metadata.sqlite?mode=ro", uri=True)
    for rb, n, v in md.execute(
            "SELECT rb_id,name,num FROM feature WHERE source='essentia_ml'"):
        if n in ("mood_aggressive","mood_party","energy","mood_relaxed"):
            f[rb]["ml_" + n.replace("mood_","")] = v
    # onsets_per_beat as stored is CORRUPTED by the BPM labelling convention: a
    # track filed at 87 instead of 174 gets double the apparent density. Measured
    # 2026-08-23 within dnb: halftime-filed tracks averaged decile 7.39 vs 3.12 for
    # full-tempo ones, an onsets_per_beat ratio of 1.82x (~the 2x of a pure
    # artefact). Recompute it here from onset_rate against a CANONICAL tempo
    # octave, folded into [90, 180). No re-extraction needed.
    try:
        from pyrekordbox import Rekordbox6Database
        from pyrekordbox.db6 import tables as _t
        _db = Rekordbox6Database()
        for _c in _db.query(_t.DjmdContent).filter(_t.DjmdContent.rb_local_deleted == 0).all():
            rb = str(_c.ID)
            if rb in f and _c.BPM and f[rb].get("onset_rate"):
                b = _c.BPM / 100.0
                while b < 90:  b *= 2
                while b >= 180: b /= 2
                f[rb]["onsets_per_beat"] = f[rb]["onset_rate"] * 60.0 / b
    except Exception as e:
        print("WARNING: canonical-BPM recompute unavailable, using stored value:", e)
    need = list(COMPONENTS)
    ids = [i for i in f if all(isinstance(f[i].get(k),(int,float)) and np.isfinite(f[i][k]) for k in need)]
    X = np.column_stack([[f[i][k] for i in ids] for k in need])
    return ids, need, X

CAL_PATH = os.path.join(HERE, "energy_calibration.json")

def robust_z(v, med=None, iqr=None):
    """median/IQR z — heavy tails (crest, band shares) shouldn't dominate.
    Pass frozen med/iqr to keep scores stable when the library grows."""
    if med is None: med = np.median(v)
    if iqr is None: iqr = np.percentile(v,75) - np.percentile(v,25)
    return np.clip((v - med) / (iqr + 1e-12), -4, 4)

def load_cal():
    """Frozen calibration, or None. Stale files (component set changed) are refused."""
    if not os.path.exists(CAL_PATH): return None
    cal = json.load(open(CAL_PATH))
    if set(cal.get("features", {})) != set(COMPONENTS):
        print(f"WARNING: {os.path.basename(CAL_PATH)} is STALE "
              f"(calibrated on {sorted(set(cal.get('features',{})) ^ set(COMPONENTS))} mismatch). "
              f"Ignoring it — re-run with --recalibrate.")
        return None
    return cal

def score_of(ids, need, X, cal=None):
    if cal:
        Z = np.column_stack([robust_z(X[:,k], cal["features"][n]["median"],
                                      cal["features"][n]["iqr"]) * COMPONENTS[n][0]
                             for k,n in enumerate(need)])
    else:
        Z = np.column_stack([robust_z(X[:,k]) * COMPONENTS[n][0] for k,n in enumerate(need)])
    gs = {}
    for g in GROUP_W:
        cols = [k for k,n in enumerate(need) if COMPONENTS[n][1]==g]
        # Re-standardise the group score BEFORE weighting. Without this, groups
        # whose members are mutually correlated (notably `percept` — the essentia
        # mood models all move together) carry far more variance than their
        # nominal weight, and quietly dominate. Measured: percept correlated
        # +0.756 with the final score at a nominal weight of 0.25.
        gm = Z[:,cols].mean(1)
        if cal: gs[g] = robust_z(gm, cal["group_norm"][g]["median"], cal["group_norm"][g]["iqr"])
        else:   gs[g] = robust_z(gm)
    s = sum(GROUP_W[g]*gs[g] for g in GROUP_W)
    return s, gs, Z

def deciles(s, cal=None):
    o = np.argsort(s, kind="mergesort"); r = np.empty(len(s)); r[o] = np.arange(len(s))
    pct = (r + 0.5)/len(s)
    if cal:   # fixed thresholds: today's library spans 1-8, 9-10 kept for harder music
        return np.digitize(s, cal["thresholds"]) + 1, pct
    return np.minimum((pct*10).astype(int)+1, 10), pct

def write_cal(need, X, s, gs):
    cal = {"features": {}, "group_norm": {}, "groups": GROUP_W,
           "signs": {n: COMPONENTS[n][0] for n in need}}
    for k, n in enumerate(need):
        v = X[:,k]
        cal["features"][n] = {"median": float(np.median(v)),
                              "iqr": float(np.percentile(v,75)-np.percentile(v,25))}
    for g in GROUP_W:
        cal["group_norm"][g] = {"median": float(np.median(gs[g])),
                                "iqr": float(np.percentile(gs[g],75)-np.percentile(gs[g],25))}
    th = [float(np.percentile(s, 100*i/8)) for i in range(1,8)]   # library spans deciles 1-8
    band = th[-1]-th[-2]
    th += [float(th[-1]+band), float(th[-1]+2*band)]              # 9 and 10 above the library
    cal["thresholds"] = th
    cal["note"] = ("Frozen calibration. Scores use these fixed medians/IQRs, so importing "
                   "tracks does NOT reshuffle existing scores. Today's library spans deciles "
                   "1-8; 9 and 10 are headroom for harder material. Regenerate with "
                   "--recalibrate after ANY change to COMPONENTS or GROUP_W.")
    json.dump(cal, open(CAL_PATH,"w"), indent=1)
    return cal


# ---------------------------------------------------------------------------
# ABSOLUTE scale (--absolute). The percentile scale above is purely RELATIVE:
# the top track sits at 1.0000 by construction, whatever it sounds like, and
# importing new music reshuffles every existing decile. That gives no headroom
# for harder material than the library happens to contain.
#
# These anchors map each feature to [0,1] against FIXED reference points, not
# library statistics. Where a feature has a physical bound, the anchor uses it:
#   sustain   fraction of frames at full level -> <= 1 by definition
#   crest     p99/p50                          -> >= 1 by definition
#   dyn_range log10(p90/p10)                   -> >= 0 by definition
#   r_*       band shares                      -> <= 1 by definition
#   ml_*      model outputs                    -> already [0,1]
# Consequence: scores are stable across imports, and the library's hardest track
# lands wherever it lands rather than being forced to the ceiling.
# (lo, hi) — hi is the "maximum energy" end, so lo>hi means lower is harder.
ANCHORS = {
    "onsets_per_beat": (0.5, 6.0),
    "pump":            (0.0, 3000.0),
    "r_bass":          (0.0, 0.40),
    "r_sub":           (0.0, 0.50),
    "r_hi":            (0.0, 0.15),
    "sustain":         (0.0, 1.0),
    "crest":           (8.0, 1.0),     # inverted: 1.0 = perfectly constant
    "dyn_range":       (3.0, 0.0),     # inverted: 0 = no dynamic range
    "ml_aggressive":   (0.0, 1.0),
    "ml_party":        (0.0, 1.0),
    "ml_energy":       (0.0, 1.0),
    "ml_relaxed":      (1.0, 0.0),     # inverted
}

def absolute_score(ids, need, X):
    """Energy on a fixed 0-10 scale with headroom. 10 = every component maxed."""
    gsum, gcnt = {}, {}
    for k, n in enumerate(need):
        lo, hi = ANCHORS[n]
        u = np.clip((X[:, k] - lo) / (hi - lo), 0.0, 1.0)
        g = COMPONENTS[n][1]
        gsum[g] = gsum.get(g, 0) + u
        gcnt[g] = gcnt.get(g, 0) + 1
    gav = {g: gsum[g] / gcnt[g] for g in gsum}
    return 10.0 * sum(GROUP_W[g] * gav[g] for g in GROUP_W), gav

if __name__ == "__main__":
    ids, need, X = load()
    cal = None if args.recalibrate else load_cal()
    s, gs, Z = score_of(ids, need, X, cal)
    if args.recalibrate:
        cal = write_cal(need, X, s, gs)
        print(f"recalibrated -> {os.path.basename(CAL_PATH)} ({len(need)} components)")
    dec, pct = deciles(s, cal)
    print(f"scored {len(ids)} tracks   calibration: {'frozen' if cal else 'NONE (relative)'}")
    if args.validate:
        print("\ncorrelation of each component with the FINAL score "
              "(no single one should dominate):")
        for k,n in sorted(enumerate(need), key=lambda t: -abs(np.corrcoef(Z[:,t[0]],s)[0,1])):
            print(f"   {n:<18}{np.corrcoef(Z[:,k],s)[0,1]:+.3f}")
        print("\ngroup correlations with final score:")
        for g in GROUP_W: print(f"   {g:<10}{np.corrcoef(gs[g],s)[0,1]:+.3f}")
