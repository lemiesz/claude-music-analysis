# How to Measure the Energy of a Track: Two Methods

*This document uses Simplified Technical English (ASD-STE100).*

## 1. Purpose

A DJ library needs an energy value for each track. The value must be relative. You
must be able to put the full library in order from 1 to 10. You must also be able to
put one playlist in order in the same way.

This document gives two methods. The first method measures the energy from the audio
signal. The second method finds the energy as a direction in an embedding. The two
methods agree with each other. This agreement is the most important result.

## 2. Why the Simple Methods Are Not Correct

### 2.1 Loudness Is Not Energy

The first method calculated the RMS value of the audio signal. It used only one part
of each track, from 30 seconds to 120 seconds.

The RMS value shows the master level of the audio file. A loud master gives a high
value. A quiet master gives a low value. The music has only a small effect.

A test shows this fault. Some tracks are in the library two times, as two different
files. The two files must get the same energy value. But this method puts 16.9 percent
of these pairs more than two deciles apart.

### 2.2 A Mood Model Is Not Energy

The second method used a value from a mood model. The name of the value is
`mood_aggressive`. The model finds a hard tone. It does not find energy.

Thus the result was not correct for full music types. The model put 66 percent of the
drum-and-bass tracks in the lowest group. It put 83 percent of the disco tracks in the
lowest group. But it put 47 percent of the psytrance tracks in the highest group.

### 2.3 There Is No Correct Data to Learn From

We tested two sets of labels. Neither set was usable.

The first set came from playlists with energy names. But these playlists contain only
one music type. A model that learns from them learns the music type. It does not learn
the energy.

The second set came from the play order in recorded DJ sets. The correlation with each
feature is between 0.02 and 0.05. The sign also changes between music types. Real DJ
sets do not increase in energy at a constant rate.

Thus you cannot learn the energy from data. You must define the energy. Each part of
the definition must have a name and a sign. You must be able to examine each part.

## 3. Method 1: Measure the Audio Signal

Decode each audio file. Then calculate the acoustic values. Three rules are important:

1. Divide each track by its own RMS value before you measure it. This removes the
   master level. The result then shows the density and the spectral balance.
2. Analyze the full track. Do not analyze only one part of it.
3. Use percentiles. Do not use mean values. A quiet start must not decrease the value
   of a loud track.

Put the values into four groups. The table shows the groups.

| Group | What it measures |
|---|---|
| drive | The number of onsets in each beat, and the pulse at the beat rate |
| weight | The low-frequency part and the high-frequency part of the signal |
| full | How much of the track stays at a high level, minus the crest factor and the dynamic range |
| percept | The values from the mood models. This group has the smallest weight. |

You must standardize each group before you apply the weight of the group. If you do
not do this, the weights are not correct. The mood values increase and decrease
together. Thus that group controlled +0.727 of the result at a weight of only 0.25.

After the correction, the four groups give +0.68, +0.65, +0.60 and +0.59. No single
value gives more than +0.56.

The test results are good. The duplicate pairs now differ by 0.041 (mean, in
percentile). The RMS method gives 0.112. The correlation with the tempo is -0.05. Thus
the energy value is not a tempo value.

## 4. Method 2: Find a Direction in the Embedding

Each track also has an embedding. The embedding has 1280 dimensions. A neural network
makes it. The system uses it to find tracks that sound the same.

The energy is in the embedding. But the energy is not one dimension. The energy is a
direction.

### 4.1 A Dimension and a Direction Are Different

Look at a group of points in a usual space of three dimensions. Each point has a
temperature. No axis shows the temperature. But the temperature can increase when you
move along the direction `0.3x + 0.7y - 0.2z`. The information is in a combination of
the axes. No single axis holds it.

The embedding is the same. But it has 1280 axes and not three.

### 4.2 How to Find the Direction

Find a set of weights. There is one weight for each dimension. Call this set `w`.
Multiply each track by `w`. The result must agree with the energy value from Method 1.
A ridge regression finds `w`.

This direction gives a held-out Spearman correlation of 0.843.

The direction is diagonal. If the energy were one clear axis, one principal component
would show it. But many components hold a part of it:

| Component | Correlation with the energy | Part of the total variance |
|---|---|---|
| PC1 | +0.403 | 16.0% |
| PC4 | +0.335 | 5.4% |
| PC3 | -0.276 | 6.3% |
| PC2 | +0.212 | 8.9% |

### 4.3 The Two-Dimensional Map Removes the Energy

The sound map shows the 1280 dimensions in two dimensions. UMAP makes this map. Measure
the same energy direction after UMAP:

```
1280 dimensions   rho = 0.843
UMAP 3D           rho = 0.539
UMAP 2D           rho = 0.468
```

The two-dimensional map loses approximately 45 percent of the energy order. This is not
a fault in UMAP. UMAP keeps the local groups of points. It uses its two output
dimensions for the largest differences, which are the music type and the tone. The
energy direction is diagonal and small. Thus UMAP removes it.

Note this result: two tracks that are near each other do not always have the same
energy. The energy is almost independent of the distance in the embedding. But you can
still calculate the energy from the position in the embedding.

## 5. Why Two Methods Are Better Than One

Method 1 uses only the audio signal. It does not use the embedding. A different neural
network makes the embedding. The two methods agree at 0.843.

Thus the agreement is a true test. It is not a circular argument. If Method 1 gave only
noise, no direction in the embedding could find it. The old RMS method would fail this
test. The embedding does not hold the master level of an audio file.

There are three uses:

1. **Confidence.** Two independent methods that agree give better evidence than one
   method.
2. **Full coverage.** Some audio files are not available. You cannot calculate Method 1
   for these tracks. But many of them have an embedding. Use `w` to calculate an energy
   value for them.
3. **A visible energy axis.** The sound map can show the direction `w`. The map then
   shows the energy, which the two-dimensional layout usually removes.

## 6. A Fault That We Found and Corrected

The value `onsets_per_beat` divides the number of onsets by the number of beats. Thus
it uses the tempo. But a library can hold the same tempo in two ways. It can hold a
fast track at its full tempo. It can also hold the same track at one half of that
tempo.

This gave incorrect results. A track at one half of the tempo got two times too many
onsets in each beat. Then it got too much energy.

The measurement shows the size of the fault. In one music type, the tracks at one half
of the tempo got a mean decile of 7.39. The same music at the full tempo got 3.41. The
ratio of `onsets_per_beat` was 1.82. A ratio of 2.00 is the ratio of a pure fault.

The correction is easy. Change the tempo to a standard range before you divide. Use the
range from 90 to 180. Multiply or divide the tempo by 2 until it is in this range. The
difference then decreases from 4.27 deciles to 1.28 deciles.

You do not need to analyze the audio files again. The system keeps `onset_rate` and the
tempo as two different values. Thus you can calculate `onsets_per_beat` again at any
time.

## 7. How to Test a Music Type Without a Circular Argument

Method 2 learns the direction from the values of Method 1. Thus the two methods must
agree. You cannot use this agreement to test one music type.

There is a better test. Remove one music type from the data. Then find the direction
again. The new direction does not know that music type. Then use the new direction to
calculate a value for it.

We did this test for each music type. The largest difference was 0.041 in percentile.
For one music type, the mean value from Method 1 was 0.752. The new direction gave
0.776. The direction did not see this music type in its data. But it gave almost the
same result.

This test shows that Method 1 does not give too much energy to one music type. Use this
test when you must examine a group of tracks.

## 8. What Is Still Open

The order of the tracks in each music type is correct. In drum-and-bass, the quiet
tracks get a low value and the loud tracks get a high value. In disco, the result is
the same.

But one question stays open. A fast but quiet track can get a lower value than a slow
but loud track. This is correct for the audio signal. But a DJ can have a different
opinion.

You have two possible answers:

- Keep one scale for the full library. Accept that some music types stay low.
- Calculate the percentiles in each music type. Each type then uses the full range from
  1 to 10.

This is a decision about the use of the data. It is not a fault in the measurement.
