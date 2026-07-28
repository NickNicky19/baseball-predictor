# Projected lineup empirical-joint candidate v1

## Decision

`ENGINEERING_READY_FOR_FUTURE_PROSPECTIVE_EVALUATION_ONLY`

This is an input-layer research candidate, not a verified probability
improvement, production promotion, betting signal, or betting authorization.
It does not change the frozen predictor.

## Predictive hypothesis

The Hits, HR over 0.5, and Total Bases models need a truthful distribution over
whether a batter starts and, conditional on starting, where the batter appears
in the order. At T−4, the final lineup is normally unavailable. The candidate
therefore uses:

1. the official active roster captured by the existing T−4 receipt boundary;
2. complete lineups from strictly earlier games for the same team; and
3. no target-game outcomes, final lineup, guessed injury state, or actual
   postgame starter.

Every earlier complete lineup whose nine players remain on the target receipt's
active roster is one observation. Identical joint lineups are combined and each
eligible prior team-game receives equal weight. There is no recency coefficient,
league-average substitution, last-lineup fallback, partial-lineup imputation,
or hand-tuned baseball effect.

## Why joint scenarios matter

Independent player start probabilities can assign probability to impossible
lineups containing more or fewer than nine starters or duplicate batting slots.
This candidate preserves the nine-player and nine-slot constraints in every
scenario. Player start and slot probabilities are derived only after the joint
distribution passes the existing projected-lineup contract.

## Fail-closed behavior

- Same-day and future historical lineups are rejected.
- May 2026 is rejected before construction.
- The active roster, team, target date, completed-lineup bytes, and feature
  store hashes must agree.
- The downstream candidate independently rebuilds the historical feature store
  and requires exact semantic equality; a self-consistent forged feature-store
  hash is insufficient.
- The projection records the SHA-256 of the code bytes actually executing,
  rather than accepting a caller-supplied code identity.
- A late projection is terminally unavailable.
- No eligible historical joint lineup produces an explicit terminal record
  without probabilities.
- Any malformed identity, incomplete lineup, duplicate player/slot, receipt
  mismatch, or probability-mass defect is rejected.

## Required evidence before downstream use

The candidate must be run only on new receipt-paired, non-May targets. It must
then be evaluated chronologically for start-probability Brier score and
calibration, slot log loss, joint-lineup log loss, coverage, and quarantine
rate against predeclared time-safe baselines. Only after that input-layer gate
passes may a locked downstream challenger consume its marginals.

Hits, HR over 0.5, and Total Bases must still be adjudicated separately. A
market cannot rescue another. HR must still beat the league-rate baseline, the
time-safe empirical-Bayes player-rate baseline, the frozen simulator, and valid
market-implied probability where available. Untouched prospective evidence,
executable prices, verified settlement, positive ROI uncertainty bounds, and
forward replication remain mandatory before any betting authorization.

## Preserved evidence boundaries

- No 2024 selection outcomes were opened.
- The spent 2025 HR confirmation was not used.
- May 2026 was not read, fetched, parsed, reconstructed, or written.
- No missed prospective receipt or prediction was backfilled.
- The reserved 2026 forward window was not retrofitted.
- Existing baselines, rejected candidates, collectors, AWS runtime, and
  production probabilities were unchanged.

## Highest-value next action

Independently review these exact bytes, pass the Linux regression/mutation gate,
then—under a separate deployment authorization—begin future-only collection of
candidate lineup projections alongside final lineup labels. Do not connect the
candidate to player-prop probabilities until the prospective input-layer gates
pass.
