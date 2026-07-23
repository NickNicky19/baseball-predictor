# Statcast source-loss repair v1

Status: predeclared integrity repair; research only; no model promotion and no
betting authorization.

## Measured defect

`SavantClient.fetch_statcast_range` converts an unavailable dependency, a
provider exception, and a `None` or empty provider response into the same empty
DataFrame. `StatcastFeatureEngine` then builds no player profiles and resolves
every active hitter to league averages. The resulting probabilities cannot
distinguish a real player-history gap from total source loss.

## Success condition

1. Dependency absence, provider exceptions, empty provider responses, missing
   CSVs, empty CSVs, and malformed pitch-level schemas fail before a probability
   can be produced.
2. Valid observations persist their source kind, source status, source window,
   source row count, and exact field-level fallback list through JSON and
   DataFrame feature serialization.
3. A genuinely missing player profile remains coverage-preserving but is
   explicitly labelled as a league fallback; it can no longer masquerade as
   observed player evidence.
4. Prediction-health records preserve the same lineage facts without assigning
   an unsupported confidence score.

## Failure condition

The repair fails if any source outage still returns an ordinary empty profile
pool, if an `observed_complete` profile can contain fallback fields, if lineage
is lost on serialization, or if valid observed and valid player-fallback inputs
cannot be represented separately.

## Protected invariants

- May 2026 is not fetched, read, parsed, or written.
- No outcome, price, prospective receipt, or settlement evidence is opened.
- The frozen production baseline and all rejected candidates are unchanged.
- No probability coefficient, threshold, model artifact, or market policy is
  changed.
- The July 22 operational collector and its runtime are untouched.

## Required mutations

- Replace a provider exception with the former empty-frame behavior: the test
  must fail.
- Remove `events` from a nonempty pitch-level pull: the schema test must fail.
- Mark a profile `observed_complete` while retaining fallback fields: lineage
  validation must fail.
- Delete source lineage during feature round trip: the round-trip test must
  fail.

Only the minimum permissible point-in-time feature artifacts affected by this
repair may later be reconstructed. This repair is not evidence of improved
Brier score, log loss, calibration, discrimination, ROI, or market behavior.
