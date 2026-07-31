# Focused 2023 raw-byte outcome determination

## Decision

`RAW_BYTES_OUTCOME_INCOMPLETE`

Raw source objects were retained, but MLB was asked to return a narrow
server-side field projection. The retained bytes therefore cannot support the
outcome labels needed by the shared-PA model.

## Direct evidence

- Retained raw response files: 2,430.
- Distinct gamePk values: 2,430.
- Official-date range: 2023-03-30 through 2023-10-01.
- Batting-order player structures inspected: 50,750.
- Structures containing the complete required batting outcomes: 0.
- Feeds containing `allPlays`: 0.
- Retained play events: 0.
- `liveData` key set in all 2,430 feeds: exactly `boxscore`.
- The request receipt in all 2,430 feeds binds a `/feed/live?fields=...`
  request whose batting projection ends at `stats,batting,plateAppearances`.
- Representative away row: gamePk 716352, player ID 518934, batting order
  100, retained batting field `plateAppearances` only.
- Representative home row: gamePk 716352, player ID 521692, batting order
  300, retained batting field `plateAppearances` only.

Across all 50,750 inspected player batting structures, `atBats`, `hits`,
`doubles`, `triples`, `homeRuns`, `baseOnBalls`, `intentionalWalks`,
`strikeOuts`, `hitByPitch`, `sacFlies`, `sacBunts`, `totalBases`, `runs`,
`rbi`, and `catchersInterference` were absent. The exact machine-readable scan
is `reports/shared_pa_predictive_tournament_v1_raw_bytes_decision.json`.

The original opportunity source release remains unchanged. No external
request, label construction, fitting, scoring, prediction, or protected-data
access occurred during this determination.

## Smallest lawful replacement capture

Use the existing certified schedule index and request only:

`GET https://statsapi.mlb.com/api/v1/game/{gamePk}/boxscore`

There is no query string, schedule recapture, play-by-play request, Statcast
request, or model operation. The immutable request plan contains exactly the
2,430 certified gamePk values. The pacing floor is 1.10 seconds between actual
request-start timestamps, with at most four lifetime attempts per exact
request. Every response retains raw bytes, a hash-bound receipt, durable
attempt journal, runtime identity, source-access identity, request-plan
identity, and atomic publication record.

The future offline outcome-label release is governed by
`docs/research/SHARED_PA_OUTCOME_LABEL_RELEASE_CONTRACT_V1.md`. If complete
official boxscore fields cannot pass its PA and team/game reconciliation gates,
the raw release fails qualification; play-by-play is not silently added to this
authorization.

