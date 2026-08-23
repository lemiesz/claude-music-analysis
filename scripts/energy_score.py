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
import sqlite3, argparse, os, numpy as np
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--validate", action="store_true")
ap.add_argument("--all", action="store_true")
ap.add_argument("--playlist", default=None)
ap.add_argument("--top", type=int, default=10)
args = ap.parse_args()

# name -> (sign, group). Groups are averaged, then groups are weighted.
COMPONENTS = {
    # DRIVE: how busy / how much movement
    "onsets_per_beat": (+1, "drive"),   # rhythmic density, tempo-normalised
    "pump":            (+1, "drive"),   # beat-locked envelope modulation
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
    need = list(COMPONENTS)
    ids = [i for i in f if all(isinstance(f[i].get(k),(int,float)) and np.isfinite(f[i][k]) for k in need)]
    X = np.column_stack([[f[i][k] for i in ids] for k in need])
    return ids, need, X

def robust_z(v):
    """median/IQR z — heavy tails (pump, crest) shouldn't dominate."""
    med = np.median(v); iqr = np.percentile(v,75) - np.percentile(v,25)
    return np.clip((v - med) / (iqr + 1e-12), -4, 4)

def score_of(ids, need, X):
    Z = np.column_stack([robust_z(X[:,k]) * COMPONENTS[n][0] for k,n in enumerate(need)])
    gs = {}
    for g in GROUP_W:
        cols = [k for k,n in enumerate(need) if COMPONENTS[n][1]==g]
        # Re-standardise the group score BEFORE weighting. Without this, groups
        # whose members are mutually correlated (notably `percept` — the essentia
        # mood models all move together) carry far more variance than their
        # nominal weight, and quietly dominate. Measured: percept correlated
        # +0.756 with the final score at a nominal weight of 0.25.
        gs[g] = robust_z(Z[:,cols].mean(1))
    s = sum(GROUP_W[g]*gs[g] for g in GROUP_W)
    return s, gs, Z

def deciles(s):
    o = np.argsort(s, kind="mergesort"); r = np.empty(len(s)); r[o] = np.arange(len(s))
    pct = (r + 0.5)/len(s)
    return np.minimum((pct*10).astype(int)+1, 10), pct

if __name__ == "__main__":
    ids, need, X = load()
    s, gs, Z = score_of(ids, need, X)
    dec, pct = deciles(s)
    print(f"scored {len(ids)} tracks")
    if args.validate:
        print("\ncorrelation of each component with the FINAL score "
              "(no single one should dominate):")
        for k,n in sorted(enumerate(need), key=lambda t: -abs(np.corrcoef(Z[:,t[0]],s)[0,1])):
            print(f"   {n:<18}{np.corrcoef(Z[:,k],s)[0,1]:+.3f}")
        print("\ngroup correlations with final score:")
        for g in GROUP_W: print(f"   {g:<10}{np.corrcoef(gs[g],s)[0,1]:+.3f}")
