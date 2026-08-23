# Measuring "energy" two ways: DSP, and a diagonal through embedding space

A DJ library needs a **relative energy scale** — rank any track, or any playlist,
from 1 to 10 on one consistent axis. This brief describes two independent ways we
computed it, and why the fact that they agree is the most useful result.

## Why the obvious approaches fail

**Loudness is not energy.** The first attempt used raw RMS over a fixed 30–120s
window. RMS measures the *mastering engineer*: a loud modern master beats a quiet
older one regardless of the music. The failure is measurable — that feature places
**16.9% of duplicate pairs (the same song, two files) more than two deciles apart**.

**Mood classifiers are not energy.** The second attempt thresholded an Essentia
`mood_aggressive` model. That detects harsh *timbre*, so it inverted whole genres:
drum & bass came out **66% "warmup"**, disco 83%, while psytrance was 47% "peak".

**And there is no ground truth to learn from.** Two candidate label sources were
tested and both were dead ends. Hand-looking "curated" energy tags turned out to be
derived from genre-specific playlists, so a model trained on them learns *subgenre*,
not energy. And play-position within recorded DJ sets correlates only 0.02–0.05 with
every candidate feature, with the sign flipping between genres — real sets are not
strictly energy-ramped.

No ground truth means energy must be **defined**, not learned. Every component has to
be a named, signed, inspectable number.

## Method 1 — measure it directly (DSP)

Decode each track, then compute explicit acoustic quantities. Three rules do the heavy
lifting:

1. **Divide every track by its own RMS before measuring.** Master gain cancels out, so
   what remains is arrangement density and spectral balance rather than mastering level.
2. **Analyse the whole track**, not a fixed window that often lands on an intro.
3. **Summarise at percentiles, not means**, so a long ambient intro cannot drag down a
   banger.

The resulting numbers are pooled into four groups, each re-standardised before weighting:

| group | what it captures |
|---|---|
| drive | rhythmic density per beat, beat-locked envelope pulse ("pump") |
| weight | low-end share, presence/hats share |
| full | how much of the track runs at full tilt; *minus* crest factor and dynamic range |
| percept | Essentia mood models — deliberately the smallest weight |

Re-standardising each group before weighting is load-bearing. Without it the weights are
fiction: the Essentia features all move together, so at a nominal 0.25 weight that group
exerted **+0.727** influence on the final score. After tuning, the four groups land at
+0.68 / +0.65 / +0.60 / +0.59 — balanced, with no component above +0.56.

Result: duplicate pairs now differ by a mean of **0.041** in percentile (versus 0.112 for
raw RMS), and correlation with BPM stays at **−0.05** — energy is not a tempo proxy.

## Method 2 — find it as a direction in the embedding

Separately, every track already has a **1280-dimensional embedding** from a neural audio
model, used for similarity search. The question: is energy in there?

**It is — but not as a dimension. As a direction.**

The distinction is the crux. Think of a cloud of points in ordinary 3D space where each
point has a temperature. There may be no axis called "temperature" — but temperature might
still rise steadily as you move along the diagonal `0.3·x + 0.7·y − 0.2·z`. Nothing is
labelled; the information lives in a *combination* of coordinates.

Same here, with 1280 coordinates instead of 3. To find the direction we solve for a weight
vector **w** (one weight per dimension) such that the projection `w · x` best reproduces the
DSP energy scores — an ordinary ridge regression. Projecting a track onto **w** then yields
its energy.

That direction predicts the DSP score at **held-out Spearman 0.843**.

And it is genuinely diagonal. If energy were a dominant, tidy axis, one principal component
would carry it. Instead it is smeared:

| component | correlation with energy | share of embedding variance |
|---|---|---|
| PC1 | +0.403 | 16.0% |
| PC4 | +0.335 | 5.4% |
| PC3 | −0.276 | 6.3% |
| PC2 | +0.212 | 8.9% |

### Why the 2D map hides it

The browsable sound map projects those 1280 dimensions to 2D with UMAP. Measuring the same
energy direction *after* projection:

```
full 1280-dim   rho = 0.843
UMAP 3D         rho = 0.539
UMAP 2D         rho = 0.468
```

Flattening to 2D discards roughly **45%** of the recoverable energy ordering. This is not a
bug in UMAP — it optimises for preserving *local neighbourhoods*, and it spends its two
output dimensions on the highest-variance structure (broadly, genre and timbre). A
low-variance diagonal like energy is exactly what gets sacrificed. The information was never
in one of the axes you can see, so squashing the space destroys it.

A useful corollary: **embedding proximity does not imply similar energy.** Beyond
near-duplicates, the mean energy difference between a track and its nearest neighbours
flattens out and stops improving. Energy is largely *orthogonal* to embedding **distance**
while remaining linearly decodable from embedding **position**.

## Why two methods matter

The DSP composite is computed from raw audio with no knowledge of the embedding. The
embedding comes from an entirely separate neural model. They agree at 0.843.

That is **cross-validation, not circularity**. If the DSP composite were largely measurement
noise, no direction in the embedding could recover it. And notably, the old raw-RMS feature
would *fail* this test, because mastering loudness is not recoverable from a timbre
embedding — the embedding does not encode how loud someone mastered the record.

Three practical uses follow:

1. **Confidence.** Two independent measurements agreeing is stronger evidence than either alone.
2. **Coverage.** Tracks whose audio files are missing can never get DSP features, but many
   still have embeddings — projecting onto **w** gives them a defensible imputed score.
3. **A visible energy axis.** The sound map can project onto **w** directly, surfacing
   structure the 2D layout currently throws away.

## What is still unsolved

The composite systematically favours sustained four-on-floor material and ranks
breakbeat-driven material low — drum & bass lands well below where a DJ would put it. The
cause is identifiable: the `full` group rewards continuous, compressed, wall-of-sound
production and penalises tracks built around space and contrast. That is a *production-style*
axis leaking into what is supposed to be an energy axis — a subtler version of the same
mistake raw RMS makes.

Fixing it honestly requires either genre-relative normalisation, or a real ground truth
built by labelling a stratified sample across genres rather than reusing playlists that
happen to have energy-sounding names.
