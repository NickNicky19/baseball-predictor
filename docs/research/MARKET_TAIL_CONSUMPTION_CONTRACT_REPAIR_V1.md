# Market-tail probability consumption contract repair v1 (predeclared)

## Incident

The edge consumer returns a matching exact tail without validating the Monte
Carlo result that contains it.  NaN/out-of-range probabilities, malformed or
ambiguous threshold keys, a category mismatch, an invalid draw count, or a
non-monotone tail can therefore reach market arithmetic.  Total Bases checks
its allowed line grid only when the exact tail is missing, so an injected exact
tail can bypass the candidate's declared market interface.  Direct edge calls
also do not bind player/category identity between projection and quote.

The simulator has a related silent fallback: an unknown category is scored as
HRR, and a nonpositive draw count can construct an empty invalid distribution.

## Locked repair

1. Validate the complete simulation result before any exact tail is consumed:
   positive integral draw count, exact category identity, finite ordered
   summaries, positive integral unique thresholds, finite probabilities in
   `[0,1]`, and nonincreasing survival probabilities.
2. Validate Total Bases line support before looking up an exact tail.
3. Bind quote player and category to the projection for direct and indexed
   consumption.
4. Reject unknown simulation categories and nonpositive/nonintegral draw
   counts at generation; do not substitute HRR or emit an empty distribution.
5. Add regression and mutation tests for every old failure while preserving
   valid exact-tail and valid market arithmetic unchanged.

No probability is clipped, substituted, fitted, or tuned.  This is an
integrity repair only and opens no historical outcome, price, prospective,
2026, or May artifact.
