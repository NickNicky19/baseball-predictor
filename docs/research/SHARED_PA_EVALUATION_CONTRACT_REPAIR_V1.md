# Shared PA evaluation contract repair v1

Status: verified integrity repair; no performance or market promotion claim.

The shared-PA fitting and adjudication utilities did not apply one common
validation boundary. Negative, fractional, or nonfinite outcome counts could
reach some score paths. Several score/calibration paths checked only row sums,
so negative or above-one probabilities could enter. Temperature scaling clipped
inputs before proving they were probabilities. Bootstrap draw counts and seeds
were coerced, empirical-Bayes prior strength could be negative/nonfinite, locked
CatBoost identity fields could be overridden through generic parameters, and
model probability output was checked only for column count.

The repair validates count and probability matrices before fitting, scoring,
calibration, uncertainty, or market derivation. Numerical epsilon clipping is
retained only after a valid probability distribution is proven and only to make
logarithms finite. Bootstrap controls and empirical-Bayes strength now have
exact type/range contracts. CatBoost loss, seed, and verbosity identity cannot
be replaced through the free parameter block, and every returned probability
matrix must be finite, bounded, unit-mass, and exact-shape.

Focused regression/mutation proof passes 68 tests; the full suite passes 383.
Valid scores, calibration, uncertainty, baselines, and probabilities are
unchanged. No outcome, price, May 2026, source, feature, prediction, AWS,
collector, or frozen-production artifact was opened or changed. This provides
no new independent evidence for Hits, HR, or Total Bases.
