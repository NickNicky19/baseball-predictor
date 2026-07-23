# Rich-feature lineage repair v1

Status: predeclared integrity repair; research only; no model promotion and no
betting authorization.

## Measured defect

`RichFeatureEnricher` constructs source-bound Statcast values and then calls
`input_data.update(rolling)`. A rolling provider can therefore replace
`barrel_rate`, `hard_hit_rate`, `xwoba`, `xba`, `xslg`, contact rates, sample
size, or target-date context. Those replacements reach the PA simulator, while
the stored feature dictionary carries no per-field origin, cutoff, counts, or
denominator. The existing barrel/hard-hit pair check catches only impossible
ordering; it cannot distinguish an unauthorized but plausible override.

## Success condition

1. Rolling providers are restricted to their declared rolling namespace and
   cannot replace source-bound Statcast or game-context fields.
2. Rich Statcast pass-through fields persist exact source lineage. Barrel and
   hard-hit lineage includes their common denominator and counts.
3. Serialization and PA probability consumption reject a changed value,
   missing lineage, or lineage that disagrees with the source profile.
4. Existing valid source-bound rich features remain numerically identical.

## Failure condition

The repair fails if a rolling dictionary can still replace a source-bound
field, if a plausible barrel/hard-hit mutation reaches probabilities, if
lineage disappears on round trip, or if adding truthful lineage changes a valid
probability.

## Protected invariants

No data, outcomes, prices, prospective evidence, or May 2026 artifacts are
opened. No probability coefficient, model artifact, threshold, market policy,
frozen baseline, rejection record, or operational collector is changed.

## Required mutations

- A rolling payload containing `barrel_rate` must fail before enrichment.
- A source-bound rich `xwoba` changed after enrichment must fail at probability
  consumption even when all values are individually plausible.
- A source-bound rich payload with deleted lineage must fail serialization.
- Count/denominator lineage that differs from the source profile must fail.
