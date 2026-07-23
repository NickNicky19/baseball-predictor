# Correction consumption contract repair v1 (predeclared)

## Incident

The optional daily correction path can change simulator parameters and then add
category offsets only to `projected_value`.  The stored Monte Carlo distribution
and its exact market-tail probabilities remain unchanged.  A second prediction
on the same `DailyPredictor` can also blend already-corrected parameters again.
When corrections are explicitly requested, a missing or malformed state is
silently treated as an ordinary uncorrected run.

Those behaviors make the source of a displayed value ambiguous, allow repeated
invocation to change a probability, and can separate rankings from the exact
probabilities consumed by market evaluation.  The correction state also has no
locked research-protocol or promotion identity.  It is therefore not an
admissible probability artifact under the active research contract.

## Locked repair

1. Explicitly requested corrections must fail closed when their state is
   missing, malformed, inactive, unversioned, or not independently promoted.
2. Persisted correction state must carry a canonical schema version, finite and
   bounded values, a point-in-time training cutoff, SHA-256 identities for its
   source, protocol, code, configuration, and tests, an exact market scope, and
   a promotion status.
3. Automated retraining may create only `RESEARCH_ONLY` state.  It cannot grant
   runtime promotion to itself.
4. Output-only category offsets are prohibited at the probability consumer.
   Any future promoted change must regenerate one coherent distribution from
   its model inputs; it may not edit the displayed mean after simulation.
5. Parameter corrections, if a future independently promoted artifact clears
   this contract, must always apply to immutable uncorrected parameters so
   repeated prediction calls are idempotent.
6. Mutation tests will prove rejection of the old legacy state, missing state,
   corrupt/nonfinite values, forged promotion, incomplete hashes, category
   offsets, and repeated-application drift while preserving a valid promoted
   parameter-only artifact.

This is an integrity repair, not a fitted challenger, performance claim,
production promotion, market authorization, or betting authorization.  It
opens no historical outcome, price, prospective, 2026, or May artifact.
