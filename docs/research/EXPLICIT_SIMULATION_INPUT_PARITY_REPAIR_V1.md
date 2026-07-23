# Explicit/simulation input-parity repair v1

Status: predeclared integrity repair of the frozen comparator; no model
promotion or betting authorization.

## Measured incident

`PropEngine.project_hitter` produces two probability representations from one
bundle: `ProbabilityEngine.from_bundle` creates the archived explicit PA
distribution, while `_bundle_to_sim_input` creates the Monte Carlo input. The
simulation path bounded BvP OPS, BvP HR, and recent-form multipliers, but the
explicit path consumed their unbounded values. A projection could therefore
archive PA probabilities that were not the probabilities sampled for its
market distribution.

This is a static consumption defect visible without outcome access. It affects
Hits, HR, and Total Bases consistency; it does not establish that either set of
legacy hand-specified effects is predictive.

## Locked repair

- One effective bundle-to-PA context function must own pitcher rates, park and
  weather combination, umpire bias, handedness, and the frozen comparator's
  existing matchup bounds.
- Both explicit PA probabilities and game-simulation input must consume that
  exact context.
- The existing frozen bounds and coefficients are preserved byte-for-byte as
  comparator behavior; no new baseball effect or threshold is introduced.
- Invalid/nonfinite context values must fail closed instead of entering either
  representation.
- The repaired fitted batter-only candidate remains separate and does not
  inherit these hand-specified matchup effects.

## Required proof

- A mutation outside each existing BvP/form bound must produce identical
  effective inputs in both paths.
- Invalid numeric context must fail before explicit or sampled probability
  production.
- On already-valid in-bound inputs, explicit probabilities remain numerically
  unchanged.
- The full regression suite and seeded explicit-versus-sampling consistency
  checks pass.
