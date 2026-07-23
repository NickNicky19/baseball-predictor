# Statcast pitch-denominator repair v1

Status: predeclared integrity repair; no model promotion or betting authorization.

## Incident

`SavantClient.build_hitter_profiles_from_statcast` discarded every non-terminal
pitch before building a hitter profile.  `_aggregate_hitter_group` then used
that terminal-only frame to calculate swing, contact, whiff, chase, and zone
rates even though those metrics require all eligible pitches.  A valid source
response could therefore reach the probability path with truthful-looking but
wrong denominators.  The same path also copied Statcast `player_name`, which is
the pitcher identity on pitch-level Statcast rows, into a batter profile.

## Locked repair

- Qualify and count batter PA from terminal `events` rows.
- Retain every pitch for qualified batters when calculating pitch-denominator
  rates and batted-ball summaries.
- Keep `sample_pa` equal to terminal PA count and `source_row_count` equal to
  the number of source pitch rows consumed.
- Do not assign pitch-level Statcast `player_name` to a batter.  Numeric batter
  ID is authoritative until the separately sourced slate identity replaces
  the blank name.
- Fail closed when a grouped batter has no terminal PA, or when the resulting
  counts contradict the qualification boundary.

## Required proof

- Regression fixture with a multi-pitch PA must recover the full-pitch swing,
  contact, whiff, chase, and zone denominators.
- Mutation fixture must prove that adding non-terminal pitches changes the
  relevant rates without changing PA count.
- Identity fixture must prove pitcher `player_name` cannot enter the batter
  profile.
- Existing source-loss, rich-lineage, rolling-lineage, serialization, and
  simulator-consumption tests must remain green.

## Evaluation boundary

This repair changes valid inputs and is therefore a new research candidate,
not a correction to the frozen baseline.  Any rebuilt artifacts must use only
permissible pre-outcome sources, must skip May 2026 before fetch or parse, and
must be hash-bound.  Fit is 2023 and selection may be opened once on 2024.
Hits, HR over 0.5, and Total Bases must be adjudicated separately.  No result
from the spent 2025 HR confirmation may be reused as independent proof, and no
promotion is possible without fresh untouched confirmation/prospective
evidence and every locked market gate.
