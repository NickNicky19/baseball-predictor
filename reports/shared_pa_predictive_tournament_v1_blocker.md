# Shared-PA predictive tournament v1 — evidenced source blocker

## Decision

`BLOCKED_BEFORE_POINT_IN_TIME_PANEL_AND_FIRST_OUTCOME_SCORE`

The existing `shared_pa_candidate_v1` architecture, exact market-PMF machinery,
three opportunity arms, and frozen comparator remain available. The requested
outcome-model tournament cannot be fitted truthfully from the currently
qualified 2023 source release.

This is not a losing model result. No model result was produced.

## Primary evidence

The read-only capability check inspected all 2,430 retained 2023 coded-final
feeds in the qualified opportunity release.

| Evidence | Result |
|---|---:|
| Distinct gamePk values | 2,430 |
| Official-date range | 2023-03-30 through 2023-10-01 |
| Qualified opportunity projection rows | 43,740 |
| Batting-order player records inspected | 50,750 |
| Outcome-complete player records | 0 |
| Records missing `atBats`, `hits`, `doubles`, `triples`, `homeRuns`, `baseOnBalls`, and `strikeOuts` | 50,750 for every field |

The retained `stats.batting` object contains only `plateAppearances`. Its bytes
can prove PA opportunity, but they cannot establish the eight-class per-PA
outcome target or a time-safe batter outcome history.

Machine-readable evidence:

- `reports/shared_pa_predictive_tournament_v1_source_readiness.json`
- SHA-256: `9a6de63679ab34452a0d3f0a690c5dee4eb30171f1443277f10c7453850e8c57`
- Expected validator exit: `2` (fail closed)

## Why the existing outcome-rich panel was not substituted

An older 43,740-row 2023 panel exists at an isolated worktree path and has
stable local bytes:

- SHA-256: `643a4c6533dbe59fc4e6b5e5932683c0877ae0a5d982945c799976d36af1ebf6`
- bytes: 14,364,814

Its delivered source-binding decision is nevertheless
`BLOCKED_INCOMPLETE_SOURCE_AND_DEPENDENCY_AUTHORITY`. The documented blockers
include missing raw Statcast transport receipts, missing independent official
transport receipts, a mixed 2023–2025 legacy official source hash, no exact
reproducible dependency lock, and no external expected release digest.

Primary tracked evidence:

- `reports/direct_batter_pa_source_binding_v1.json` — SHA-256
  `ac9b2f4dcef677238747147f511b5d4f443e3bdbb5d6de19f2bccd8cd7d797b3`
- `reports/direct_batter_pa_source_binding_v1.md` — SHA-256
  `5f852ca3a3135ade9a2dc5fc1f91a32cdb9e17bf50b021a7ce6f5f76b869429f`

Using that panel would violate the rule that source provenance and exact
dependency authority must pass before performance competition.

## Completed unblocked work

- Locked one tournament and early-2023 warm-up policy before any new outcome
  score was observed.
- Preserved `shared_pa_candidate_v1`; no third candidate architecture was
  created.
- Implemented a reusable positive-schema eligibility check that distinguishes
  an opportunity-qualified release from an outcome-qualified release.
- Classified C0 and every registered feature block without repeatedly testing
  rejected or ineligible inputs.
- Added focused negative tests for PA-only input, an outcome-complete fixture,
  and a non-2023/May-2026 mutation.
- Preserved the frozen baseline and all existing opportunity artifacts.

## Blocked requested deliverables

The following require outcome-complete, receipt-qualified historical bytes and
therefore were not fabricated:

- point-in-time 2023 outcome feature panel;
- fitted C0 shared-PA artifact;
- standalone, combination, and delete-one tournament scores;
- exact historical Hits, HR, Total Bases, batter-K, and batter-BB predictions;
- candidate-versus-baseline market metrics and clustered uncertainty;
- opportunity-arm market comparison;
- immutable prospective candidate release.

## Smallest lawful unblock

Capture a new, immutable **outcome-complete 2023 source release** for the same
schedule-derived 2,430 gamePk identities. At minimum, each retained official
player-game row must include:

`plateAppearances`, `atBats`, `hits`, `doubles`, `triples`, `homeRuns`,
`baseOnBalls`, and `strikeOuts`.

The release must retain request/response receipts, raw bytes, parser identity,
observation timestamps, exact dependency/runtime identity, an external expected
digest, and the existing 2023-only and no-May-2026 guards. Historical capture
does not authorize feature construction, fitting, predictions, deployment, or
betting unless those actions are separately authorized.

Optional Statcast, plate-discipline, park, and sprint sources should not delay
C0. Blocks without receipt-complete point-in-time inputs must remain
`INELIGIBLE`; the first lawful tournament can run C0 and recency from official
outcomes, then add only independently qualified blocks.

