# Tuesday Market-Authorization Research Goal

## Objective

By Tuesday, July 21, 2026 at 8:00 AM America/Chicago, produce the strongest
verified market-specific evidence package possible toward eventual betting
authorization. Success means trustworthy evidence and a clear next decision,
not forcing a candidate to pass.

## Global invariants

- Never weaken, bypass, delete, reinterpret, or silently relax an existing
  identity, chronology, settlement, provenance, mutation, coverage,
  uncertainty, or authorization guard.
- Never inspect or score May. If a market becomes fully May-ready, create a
  hash-bound `READY_TO_OPEN_MAY` record and stop for explicit user approval.
- Never authorize betting, expose actionable GUI plays, or treat research
  output as executable.
- Never pool markets, dates, sides, books, products, or model versions to
  rescue a weak result.
- Never tune against June confirmation results, sealed evidence, or repeated
  holdout observations.
- Never fabricate or infer missing prices, sides, dates, identities,
  settlement, outcomes, lineups, or executability.
- Heuristic confidence cannot select bets, size wagers, or satisfy uncertainty
  requirements.
- Preserve unrelated behavior and user work. Every changed line must trace to
  a measured defect, predeclared experiment, or required invariant.
- Predeclare success, failure, protected invariants, and distinguishing
  mutations before every intervention.
- Run the cheapest discriminating test first: mutation/offline test, one-date
  smoke, then full reconstruction only when justified.
- Record exact source, configuration, protocol, data, feature, probability,
  outcome, market, and report hashes.
- Reject improvements whose economic value does not justify added complexity.
- Do not consume usage merely to remain active. Stop when further progress
  requires new data, authority, or an irreversible user decision.

## Stage 0 — Protect the active run

Do not modify source, model, configuration, protocol, feature, reconstruction,
validator, or evaluation files until both active 56-date HR reconstruction
processes terminate and publish complete artifacts. Monitor only. On any
material failure, report the exact failure and do not infer success or patch
around it mid-run.

## Stage 1 — Adjudicate HR over 0.5

Validate frozen and batted-ball candidate artifacts against the exact 56-date
March–April plus June contract:

- exact dates, model keys, official outcomes, market coverage, feature
  manifests, source snapshots, configurations, random seed, and pre-2026 PA
  artifact;
- candidate-only feature differences and no unrelated drift;
- no May dates;
- all probability, identity, chronology, settlement, and provenance
  invariants.

Apply the locked HR candidate gate unchanged. Require better Brier score and
log loss in both open blocks, confirmation paired date-block 95% upper bounds
below zero for both, nonnegative market-movement evidence in both blocks, and
no coverage, fallback, identity, settlement, or data-health regression.

If HR fails, reject it and preserve frozen behavior. Produce a hash-bound
failure diagnosis and identify one highest-value measured next experiment; do
not immediately implement a chain of speculative alternatives.

If HR passes, freeze the challenger and produce a hash-bound
`READY_TO_OPEN_MAY` package. Do not open May.

## Stage 2 — Consolidate Hits

Reconcile the complete time-safe Hits evidence into one immutable status
report:

- accepted and rejected candidates;
- current absolute capture and ROI uncertainty;
- remaining policy, settlement, executability, calibration, and forward-shadow
  blockers;
- one highest-value measured next experiment.

Implement a new Hits candidate only if existing open-period evidence identifies
a specific defect and the experiment can be predeclared, mutation-tested,
smoked, and evaluated without opening May. Otherwise stop Hits at a documented
next-action decision.

## Stage 3 — Establish other market contracts

For Total Bases, RBI, and Hits+Runs+RBI separately, perform outcome-blind
readiness audits before changing prediction logic:

- actual historical availability by book, product, line, and side;
- one-sided versus two-sided price contract;
- posted-price and closing-price availability;
- canonical MLB identity and official outcome definition;
- book-specific settlement, void, DNP, and participation rules;
- duplicate and fragment behavior;
- executable-price evidence;
- current model coverage and required simulator outputs;
- leakage and fallback risks.

Do not assume these markets share the Hits or HR contract. Do not manufacture
an under price. Do not begin full reconstruction for a market whose identity,
price, settlement, outcome, or executability contract fails.

## Stage 4 — Rank and advance one market

Rank HR, Hits, Total Bases, RBI, and Hits+Runs+RBI by:

1. contract completeness;
2. clean historical sample size;
3. official grading reliability;
4. executable-price evidence;
5. model coverage and data health;
6. open-period calibration and discrimination;
7. economic signal and uncertainty;
8. implementation complexity and risk.

Advance at most one additional candidate after HR: the highest-ranked candidate
supported by measured evidence. Predeclare its protocol, mutations, smoke gate,
full open-period gate, and rejection rule. Do not stack it with another
unproven change.

## Terminal conditions

Stop successfully when all four stages that remain feasible are complete and
the Tuesday evidence report is produced.

Stop safely and report `BLOCKED` when progress requires:

- opening May;
- paid or unavailable external data;
- a new sportsbook or product decision;
- an irreversible architecture choice not established by evidence;
- missing authority;
- materially incomplete artifacts;
- or a repeated external or system failure.

## Tuesday evidence report

Produce one hash-bound report containing:

- completed and failed gates;
- exact artifact and protocol hashes;
- accepted, rejected, and still-research-only candidates;
- per-market readiness ranking;
- current capture, ROI, calibration, coverage, and uncertainty evidence;
- sealed evidence still untouched;
- forward-shadow readiness;
- remaining authorization blockers;
- `READY_TO_OPEN_MAY` records, if any;
- the single highest-value next action.

No result may be called profitable, bettable, approved, or authorized unless a
separate immutable market-specific authorization record eventually satisfies
the locked historical, held-out, executable-price, settlement, and prospective
forward-shadow requirements.
