# Direct batter PA foundation v4.1 integrity report

## Decision

`SOURCE_TRUTH_REPAIR_PASSED_2023_PANEL_READY_FOR_MODEL_DEVELOPMENT_ONLY`

This is not a predictive-improvement claim, model promotion, production change,
or betting authorization. It establishes a trustworthy 2023-only development
panel for one separately predeclared challenger.

## Root causes repaired

1. Raw outcome truth was joined on `(game_pk, player_id)` without requiring raw
   `game_date` and `season` parity. The repaired identity is
   `(season, game_date, game_pk, player_id)`.
2. A missing raw terminal group was silently converted to zero PA. Missing raw
   groups now fail unless an official final MLB receipt proves the exact
   player/date/game has zero values across all contracted batting statistics.
3. A terminal event was accepted without proving that it occurred on the final
   pitch of its plate appearance. The parser now verifies the maximum pitch
   number within the unique game/batter/at-bat identity.
4. Swing, whiff, chase, zone, moment, pitch-shape, and pitch-type features did
   not serialize complete count/denominator/missingness lineage. Every such
   feature now carries support that is independently revalidated.
5. The old `all prior` label overstated the available source history. v4.1 is
   named truthfully as `available_2023_regular_season_to_date`.
6. Physical target rows, fit-eligible rows, and explicit zero-PA rows are now
   distinct manifest populations.

## Verified artifact facts

- Raw 2023 Statcast files rehashed: 643.
- Physical 2023 player-game rows: 43,740.
- Fit-eligible positive-PA rows: 43,726.
- Official-receipt-proven zero-PA rows: 14.
- Non-target raw terminal groups retained only as history: 5,017.
- Feature/lineage columns: 90.
- Identity duplicates: 0.
- Chronology violations: 0.
- 2024 selection rows: 0.
- Panel SHA-256:
  `643a4c6533dbe59fc4e6b5e5932683c0877ae0a5d982945c799976d36af1ebf6`.
- A second immutable build produced the same panel SHA-256.

The complete binding is in
`config/direct_batter_pa_foundation_v4_1_artifact_registry.json`.

## Regression and mutation proof

- 35 focused source, identity, terminal-event, denominator, zero-PA receipt,
  and mutation tests passed.
- 14 global evidence-spend and future-evaluation boundary tests passed in the
  separate continuation worktree.
- Mutated barrel-rate serialization failed.
- Mutated swing-denominator serialization failed.
- Mutated panel content hash failed.
- A non-final terminal event failed.
- A missing raw target without a zero-PA receipt failed.
- A wrong raw date for the same player and game failed to join.
- A candidate claim on 2024, spent HR confirmation, development-used evidence,
  or the already-started forward window failed.

## Preserved boundaries

- Only 2023 was rebuilt or made eligible for model development.
- 2024 remains globally spent and was not reopened.
- The 2025 HR confirmation remains spent.
- May 2026 was not fetched, opened, parsed, reconstructed, or written.
- No missed prospective evidence was backfilled.
- Existing frozen baselines and rejected candidates were unchanged.
- Existing collectors, AWS runtime, operational smoke, probabilities, and
  policies were unchanged.

## Highest-value next action

Predeclare one true empirical-Bayes-offset, regularized shared-PA challenger;
run chronological out-of-fold development on this 2023 panel only; and compare
Hits, HR over 0.5, and Total Bases separately against league rate, a row-wise
time-safe empirical-Bayes player-rate baseline, and the frozen PA-core
comparator. Do not open 2024. A survivor must be frozen before a new untouched
future confirmation window begins.
