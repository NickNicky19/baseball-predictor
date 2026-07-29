# Pitcher Source-Truth Repair v1

## Decision

This is a versioned **repair-only** release. It corrects source parsing,
denominators, start-count identity, and zero-versus-missing consumption. It is
not a fitted model, predictive promotion, production activation, market-value
claim, or betting authorization.

## Repaired defects

- MLB innings notation is converted to exact outs by one live/PIT parser;
  `"5.2"` means 17 outs and a fractional float is rejected.
- Live and PIT start counts preserve a legitimate zero. Missing, malformed,
  out-of-range, or season starts greater than appearances fail closed.
- Counts, exact outs, and supplied per-nine rates are cross-validated. Missing
  rates may be derived from validated counts/outs; supplied contradictory rates
  are rejected.
- Missing rates are `None`, while legitimate zero rates remain zero through
  candidate-ready consumption. Frozen probability paths retain their prior
  truthiness fallback behind named compatibility functions.
- Candidate-ready live and PIT retrieval rejects missing people, missing/empty
  stat blocks, and snapshots without exact outs/start/appearance identity.
- A new opt-in `a3.3-pitcher-source-truth-v1` training schema records exact
  season, recent, and outcome outs and validates their innings round trip. The
  frozen `a3.2` schema and its serialized fallback behavior remain unchanged.

## Compatibility boundaries and limitations

The repair deliberately preserves frozen behavior where changing it would be a
new probability candidate:

- A legacy/manual snapshot with missing start or appearance counts can still
  enter the frozen innings compatibility path, which uses the pre-existing
  default innings value. This fallback is not source evidence.
- Missing K/BB/HR rates can still reach explicit league/default fallbacks in
  frozen probability and profile-building paths. A legitimate observed zero no
  longer loses its identity in source-truth records, but frozen output still
  treats zero as it did before the repair. These defaults are compatibility
  behavior, not a claim that the missing player rate was observed.
- Candidate-ready consumption is a separate strict boundary. It rejects source
  gaps rather than using any frozen compatibility snapshot or league fallback.
- The repair validates semantics after an MLB pitching stat payload reaches the
  parser. It does not prove receipt timestamp, source authenticity, probable-
  starter identity, or decision-horizon eligibility.
- ERA and WHIP remain on their legacy permissive parsing path and are outside
  this tranche. Hitter parsing is also outside scope.
- The supplied-rate consistency tolerance admits normal two-decimal display
  rounding; it establishes arithmetic coherence, not upstream source trust.
- No data or feature artifact was created or rebuilt. No outcome, May 2026,
  price, execution, settlement, AWS, or operational collector state was read or
  changed.

## Artifact identities

- Configuration: **NOT_APPLICABLE** — no configuration file or coefficient was
  changed or required by the parser repair.
- Data: **NOT_APPLICABLE** — no raw, historical, prospective, or outcome data
  was read, created, rebuilt, or mutated.
- Feature artifacts: **NOT_APPLICABLE** — feature-construction code was repaired,
  and a new candidate-only schema was defined, but no feature artifact was
  produced or rebuilt.
- Model: **NOT_APPLICABLE** — no model was fit, scored, calibrated, or promoted.
- Market policy: **NOT_APPLICABLE** — no policy, price, ROI, or authorization
  boundary was changed.

## Verification

Final focused and impacted test commands/results are recorded in the bound JSON
manifest. The release verifier recomputes every changed source/test hash,
verifies this report hash, checks the exact source/test delta from the base
commit, and fails on stale-hash mutations.

The focused proof includes frozen-projection equivalence, candidate-ready
missing-source mutations, every supplied per-nine contradiction, exact-out
aggregation, candidate-schema round trips, schema downgrades, downstream outs
mutations, and stale release/report hashes.

The manifest/report binding is internally content-addressed. It proves byte
consistency relative to the included base commit; it is not an independent
external authorization signature or rollback-resistant trust anchor.
