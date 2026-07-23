# Savant player-summary contract repair v1

Status: predeclared integrity repair; no model promotion or betting
authorization.

## Measured incident

Nine preserved daily production/research configurations declare
`data/savant/stats.csv`, but that artifact is absent in this checkout. Both
configuration resolvers converted that declared source loss to `None`, and the
daily path logged an ordinary fallback instead of failing.

If such a file is present, the player-summary parser has three additional
source-truth defects:

- `row.get("barrel_rate") or row.get("barrel_batted_rate")` makes alias
  selection depend on Python truthiness, so NaN can suppress a valid canonical
  value;
- Savant leaderboard percentage-point fields are normalized conditionally,
  making a valid displayed value such as `0.5` become probability `0.5` instead
  of `0.005`;
- aggregate barrel/hard-hit rates can enter without their common BBE
  denominator/counts, and a summary has no enforced strict-prior cutoff.

The active configured artifact is absent, so no historical outcome or May file
is needed to establish these defects. Baseball Savant's official custom
leaderboard labels these fields as percentages and separately exposes Barrels
and Batted Balls.

## Locked repair

- A nonempty configured CSV path must resolve to a regular file or fail closed.
- Pregame pitch-level CSV input must carry parseable `game_date`; only rows
  strictly before the target date may be consumed.
- Pregame player-summary input must carry one unambiguous
  `source_window_end`, strictly before the target date.
- Player IDs must be positive integral and unique; malformed nonmissing values
  cannot become missing/fallback values.
- Official `*_percent` and `barrel_batted_rate` columns are percentage points
  and are always divided by 100.
- `barrel_rate` is rejected as an ambiguous player-summary alias.
- Barrel/hard-hit rates may be consumed only with exact common BBE, barrel, and
  hard-hit counts. Counts determine the probabilities; provided rates must
  agree within their one-decimal display precision.
- No coefficient, player exclusion, probability cap, or league value changes.

## Required proof

- Missing configured files, missing/late cutoffs, duplicate/bad identities,
  ambiguous aliases, partial counts, malformed values, and contradictory rates
  fail closed.
- A valid `0.5` percent summary value becomes `0.005`, never `0.5`.
- A valid count-bound, strict-prior summary and a strict-prior pitch CSV retain
  their truthful inputs.
- The full regression suite must pass and frozen artifacts remain unchanged.
