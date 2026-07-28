# Pitcher Joint Opportunity v1 — Predeclared Scaffold

## Decision

This release is a **predeclared research scaffold only**. It freezes the input,
chronology, model, evaluation, and abstention contracts before any 2024 access,
outcome inspection, fitting, or selection. It delivers no fitted artifact and
therefore produces no usable prediction. It changes no frozen production
probability and authorizes neither shadow use nor betting.

## Candidate application boundary

The application boundary accepts no caller-supplied evidence root, target, plan
hash, typed receipt, or `forward-pitcher-context-v2` mapping. An independently
fixed evidence-authority contract must bind the approved immutable archive era,
collector release and runtime, service-owned root/archive, exact game/team/
pitcher target, plan-source bytes, ledger manifest/terminal record/context/raw,
workload raw-receipt manifest, observation/T−4 cutoffs, parser/schema identities,
and protocol. No such external authority exists in this release: every binding
is explicitly null and the authority state is
`UNBOUND_NO_APPROVED_ARCHIVE_ERA`. Application therefore abstains with
`EVIDENCE_AUTHORITY_NOT_EXTERNALLY_BOUND` before it can inspect workload, model,
or runtime inputs.

The probability application also accepts no caller-supplied workload record or
raw workload-receipt sequence. A future bound implementation must derive the
service-owned workload archive, raw-receipt manifest, feature artifact and hash,
target identity, assembly/source chronology, parser/schema/feature-code hashes,
and protocol binding exclusively from that external authority contract.

The raw receipt-tree replay remains available only through a named synthetic
test boundary. Typed workload replay likewise remains only in a named offline
validation function. Those boundaries can prove parser and ledger behavior, but
their typed returns are not application inputs and cannot reach probability
consumption.
Copied, rehashed, or jointly forged internally consistent trees therefore do
not acquire authority. A later eligible implementation would need a separately
reviewed release that derives the evidence location and every expected semantic
identity from an externally authorized contract; a caller-selected filesystem
path can never supply that authority. The code does not look up, guess, or
substitute the actual eventual starter.

A second positive schema requires retained official live-feed bytes for every
prior appearance. The exact parser replays game/pitcher/team identity, starter
role, official BF and pitch count, every completed PA event, and its outs
transition. Each row is re-derived and bound to its transport receipt, source
schema, parser/feature bytes, protocol, and row hash. Missing values, duplicate
games, shorter rolling history, unclassified batters faced, impossible HR outs,
or any hash/identity contradiction abstain. No league average, clamp, row
deletion, or shorter-window fallback exists.

## Exact mathematical scaffold

For state `s=(b,o,x)`, where `b` is exact batters faced before the next PA, `o`
is exact outs before the next PA, and `x` is the validated strictly-prior
workload vector, a fitted artifact supplies standardized linear predictors.

Removal hazard:

`h(s) = logistic(β₀ + Σ βⱼ (xⱼ-μⱼ)/σⱼ)`

Removal mass at a state is the arriving active mass times `h(s)`; continuing
mass is the arriving mass times `1-h(s)`. Twenty-seven exact outs is the only
structural absorbing game-complete state. The engine does not multiply a fixed
innings estimate by a constant PA/inning value.

Conditional on continuation, exactly one seven-class softmax supplies the PA
outcome probabilities:

`P(c|s) = exp(η_c(s)) / Σ_d exp(η_d(s))`

for `c ∈ {K, BB_HBP, 1B, 2B, 3B, HR, OTHER_OUT}`. A second learned conditional
softmax supplies the exact-out transition given state and PA outcome. Its
denominator contains only the predeclared structural support for the current
inning: HR is exactly zero outs and `OTHER_OUT` cannot receive zero. Every
category's support is mutation-tested. These restrictions are part of the
probability definition, not post-hoc clipping or renormalization.

The market PMF recurrence is applied separately for each statistic but always
uses the same hazard, PA-outcome, and outs-transition kernel:

`A_{b+1,o+Δ,k+g_m(c)} += A_{b,o,k}(1-h(s))P(c|s)P(Δ|c,s)`

`T_k += A_{b,o,k}h(s)`

where `g_m(c)` is 1 for the market event, or official total-base weight
`{1,2,3,4}` for hit types. Thus K, BB/HBP, Hits Allowed, HR Allowed, and Total
Bases Allowed come from one multinomial; outs are the absorbing opportunity
state and innings are `outs/3`. Any unresolved probability mass above the
hash-bound numerical tolerance fails closed. Remaining numerical tail is
reported and never silently normalized away.

The engine also publishes the shared removal PMF over exact `(batters faced,
outs)` states. Expected batters faced and outs are taken directly from that
PMF, and expected innings is exactly expected outs divided by three; no fixed
innings multiplier is present.

Every intercept, coefficient, standardizer, random seed, rolling-history
window, and training-data identity must come from one hash-bound 2023-only
fitted artifact. A separate externally fixed authorization must bind its exact
training bytes, fit code, fit tests, protocol, qualification report, and
artifact bytes. The exact runtime release needs its own external digest. Both
authorized digests are null in this scaffold, so a self-hashed synthetic object
cannot activate probability output. The repository contains no fitted artifact.

## Frozen protocol

- Develop one regularized candidate using 2023 regular-season rolling-origin
  folds and inputs strictly prior to every target.
- Freeze these protocol bytes before opening 2024.
- Select once on 2024, with one simultaneous run and separate market decisions.
- Do not tune after 2024 or reuse the spent 2025 HR confirmation.
- Require fresh untouched confirmation or prospective evidence.
- Adjudicate every market independently against its declared league-rate,
  time-safe pitcher empirical-Bayes, frozen-model where exactly comparable, and
  valid receipt-bound market baselines. One market cannot rescue another.
- Require proper-score, calibration, discrimination, coverage, chronology,
  identity, fallback, clustered-uncertainty, and valid-market gates. Economic
  claims additionally require verified executable prices, capture lower bound
  above +0.10, and ROI lower bound above zero.

## Truthful blockers before fitting

The current repository does **not** yet supply all required training inputs:

1. `PointInTimeStats.GameLogRow` still lacks BF, pitch count, complete PA events,
   outs transitions, and raw-receipt/parser/feature lineage. This release adds a
   fail-closed live-feed replay contract, but it is synthetic-test-only and has
   no independently validated historical receipt archive.
2. The historical pitcher builder identifies the actual box-score starter. It
   does not carry receipt-proven probable-starter identity from the historical
   decision horizon. That is outcome-time identity and is ineligible for this
   candidate.
3. The prospective v2 pitcher receipt can prove future T−4 starter identity,
   but it cannot be reconstructed for missed or historical games and no such
   evidence was backfilled here.
4. Aggregate game logs remain ineligible because they do not expose exact
   PA-to-outs transitions or intervening outs. Only the new retained complete
   event-sequence contract could supply those rows, and no qualifying 2023
   archive is delivered.
5. The mandated seven PA classes are not automatically exhaustive of official
   batters faced: reach-on-error, catcher interference, and other nonstandard
   events require an explicit truthful mapping or an expanded predeclared state
   space. This scaffold rejects any unclassified BF rather than hiding it in
   `OTHER_OUT` or deleting the appearance.
6. No exact historical sportsbook/product/line/price/settlement contract is
   delivered for these pitcher outcomes. Model probability scoring and market
   value remain different, separately blocked claims.
7. No 2023 model artifact, 2024 selection result, untouched confirmation,
   prospective replication, or economic evidence exists for this candidate.
8. No independently approved archive era, service-owned receipt root, collector
   release/runtime digest, or target-specific evidence-authorization receipt
   exists. Self-consistent hashes establish internal integrity only; they do not
   authorize which evidence tree may feed a probability.

The single highest-value next action is to establish an independently approved
future-only immutable evidence archive era binding the service-owned root,
collector release/runtime, target identity, retained plan/ledger/context/
workload bytes, T−4 cutoffs, parser/schema identities, and protocol. Historical
target-starter T−4 identity remains permanently unavailable unless an authentic
contemporaneous receipt was retained. Without external evidence authority,
application and fitting remain blocked; the correct response is not a copied or
self-hashed tree, actual-starter substitution, or imputation.

## Frozen production preservation

The release verifier binds the exact pre-release bytes of the frozen pitcher-K
probability engine, role/innings compatibility layer, and both current runtime
configs. The new scaffold is isolated under `src/evaluation` and has no import
from a production module. Existing pitcher-K behavior is unchanged by
construction and by content hash.

May 2026, 2024 outcomes, prices, AWS runtime, prospective ledgers, and production
archives were not read or modified for this release.
