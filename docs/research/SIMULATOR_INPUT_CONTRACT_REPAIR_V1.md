# Simulator input contract repair v1

Status: verified integrity repair; not a model promotion.

## Incident

The shared game-probability path could consume malformed state without one
fail-closed boundary. Out-of-contract recent-form and BvP values were clipped
into plausible values. `GameSimulatorInput` accepted nonfinite, impossible, or
unit-inconsistent numeric state. A configured missing PA-distribution artifact
silently selected the legacy two-point PA model, and negative distribution
weights were silently changed to zero. Invalid non-null lineup slots also
selected that fallback. Finally, an output-safeguard failure was logged but the
projection was still returned, and an explicit empty category request expanded
to every hitter category.

## Repair contract

- Preserve valid in-contract probability behavior exactly.
- Reject, never clip, out-of-contract matchup inputs at the shared explicit and
  sampled boundary.
- Validate all game-simulator numeric inputs for type, finiteness, mathematical
  range, and cross-field K/BB coherence before simulation.
- Treat an explicitly configured PA artifact as required. Validate its complete
  lineup-slot identity, PA support, weights, and finiteness before sampling.
- Validate base-state and run-scoring configuration without converting negative
  weights or impossible probabilities into plausible state.
- Reject invalid non-null lineup slots rather than selecting the legacy path.
- Do not publish a projection that fails the declared output safeguard.
- Respect an explicit empty category request.

## Verification boundary

The focused regression/mutation harness passes 52 tests and the full suite
passes 315 tests. Mutations cover NaN/infinity, booleans, invalid rate sums,
nonpositive factors, invalid lineup slots, missing/incomplete/nonfinite/negative
PA distributions, invalid base states, impossible run probabilities, silent
category expansion, silent lineup fallback, and safeguard-only logging.

No source, feature, prediction, outcome, price, May 2026, AWS, or collector
artifact was rebuilt or changed. No valid probability changed, so chronological
performance evaluation would manufacture no new candidate evidence. Hits, HR,
and Total Bases remain unpromoted and betting remains unauthorized.
