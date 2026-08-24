# How a Space Can Hold a Feature That No Axis Holds

*This document uses Simplified Technical English (ASD-STE100) where possible.
Some standard technical names are not STE words. The text defines them where
they occur.*

## 1. Purpose

The energy brief found the energy of a track as a direction in an embedding
(see `energy-scale-brief.md`, Section 4). This document explores the idea
behind that result.

The idea is this: a set of dimensions can hold a feature together, when no
single dimension holds it alone. The feature is not in a slot. The feature is
a pattern across the slots. This is what "abstract vector representation"
means in practice.

## 2. Two Views of a Feature

The common view says: a feature is a column in a table. The tempo is a
column. The year is a column. One number, one slot, one meaning.

The second view says: a feature is a direction in a space. To read the
feature of a point, you project the point onto the direction. The projection
is a weighted sum of all the coordinates. The weights are the direction.

The first view is a special case of the second view. A column is a direction
that has one weight equal to 1 and all other weights equal to 0. The space
does not care which directions get names. The names are our choice of axes,
not a property of the data.

## 3. A Small Example That You Can Calculate by Hand

Make a space with two dimensions, `x` and `y`. Each point is a track. Each
track has a music type value `g` and an energy value `e`. The music type
causes large differences. The energy causes small differences.

Let the two coordinates mix the two causes:

```
x = g + e
y = g - e
```

Here are five tracks:

| Track | g | e | x | y |
|---|---|---|---|---|
| A | 10 | 1 | 11 | 9 |
| B | 10 | 3 | 13 | 7 |
| C | 50 | 1 | 51 | 49 |
| D | 50 | 3 | 53 | 47 |
| E | 90 | 2 | 92 | 88 |

Read the column `x`. It sorts the tracks as A, B, C, D, E. That is the music
type order, not the energy order. The column `y` gives the same order. Each
single axis is almost blind to the energy. The correlation of `x` with `e` is
near 0. The correlation of `y` with `e` is also near 0.

Now project each point onto the direction `(+1, -1)`:

```
x - y = (g + e) - (g - e) = 2e
```

| Track | x - y | e |
|---|---|---|
| A | 2 | 1 |
| B | 6 | 3 |
| C | 2 | 1 |
| D | 6 | 3 |
| E | 4 | 2 |

The projection recovers the energy exactly. The large cause `g` cancels. The
small cause `e` remains. No axis held the energy. The pair of axes held it
together, as a difference.

This is the full mechanism in two dimensions. The embedding does the same
thing in 1280 dimensions. The directions are not clean like `(+1, -1)`, and
the causes do not cancel exactly. But the principle is identical: the feature
lives in a combination, and a projection reads it out.

## 4. The Real Case: 1280 Dimensions

The library embedding has 1280 dimensions. A neural network makes it. The
network learned to identify music types. Nobody trained it to measure energy.

But the energy is readable in its space. A ridge regression found a direction
`w`. The projection onto `w` agrees with the acoustic energy of Method 1 at a
held-out Spearman correlation of 0.843.

The direction is diagonal. The principal components show this. A principal
component is the axis with the largest spread, then the next largest, and so
on. If the energy were a clean axis, one component would hold it. Instead,
many components each hold a part:

| Component | Correlation with the energy | Part of the total variance |
|---|---|---|
| PC1 | +0.403 | 16.0% |
| PC4 | +0.335 | 5.4% |
| PC3 | -0.276 | 6.3% |
| PC2 | +0.212 | 8.9% |

Even the best component holds less than half of the signal. The full
direction across all 1280 dimensions holds 0.843. The whole is much more than
each part. That is the signature of a distributed feature.

## 5. Why a Network Stores Features This Way

A network must describe each track with a fixed number of dimensions. But the
world has more properties than the network has convenient axes: music type,
tone, tempo feel, voice, texture, era, energy, and more. The network cannot
give each property its own slot.

The solution is to share. Each dimension takes part in many properties. Each
property spreads across many dimensions. This works because a later stage can
always read a property back with a weighted sum. Storage as a pattern costs
nothing when the reader is a projection. The standard name for this is a
*distributed representation*.

There is also a standard name for the reader. A small linear model that reads
a property out of a space is a *linear probe*. The ridge regression of the
energy brief is a linear probe. When the probe succeeds, we say the space
"knows" the property. The space knew the energy, although nobody put it
there on purpose. It came in as a side effect of the music type task, because
energy helps to separate music types.

## 6. What Follows From the Idea

**Nearness is not sameness.** The distance between two points sums the
differences along all directions. The energy direction is one small part of
that sum. Thus two tracks can be near each other and have different energy.
The energy brief measured this: the energy is almost independent of the
distance in the embedding. A neighbor list answers "what sounds the same".
It does not answer "what has the same energy". Different questions read
different directions.

**A map can destroy a direction.** The 2D sound map keeps the large
directions and drops the small ones. The energy direction is small and
diagonal, so the map drops much of it. The measurement: the direction reads
at 0.843 in the full space, 0.539 after UMAP to 3D, and 0.468 after UMAP to
2D. The feature did not go away. The projection that could read it went away.

**One space, many features.** Nothing limits the space to one readable
direction. A different weight set `w2` could read a different property from
the same points. The embedding is not a table with hidden columns. It is a
medium. A feature is a question that you ask with a projection, not a thing
that sits in a slot.

**A subset also works.** The idea does not need all 1280 dimensions. Any
subspace that contains enough of the direction can answer the question. This
is why the four acoustic groups of Method 1 also work: drive, weight, and
fullness are each weak alone (no single value above +0.56), but their
weighted sum tracks the same energy. Method 1 and Method 2 are the same
mechanism at two scales. Both read a diagonal feature out of a space with a
weighted sum.

## 7. Summary

A feature does not need a dimension. A feature needs a direction. A single
column is only the simplest direction. When the causes of variation mix into
the coordinates, no column shows the small cause, but a projection can cancel
the large cause and recover the small one. The two-dimensional example shows
the mechanism exactly. The 1280-dimensional embedding shows it at scale. The
practical rules follow directly: do not trust one axis, do not trust a 2D
picture, and test what a space knows with a probe, not with your eyes.
