# Prospective batter schedule denominator receipts v1

Status: `PREDECLARED_RESEARCH_ONLY_SOURCE_PRODUCER`

This isolated producer may capture only the official MLB prior-schedule
denominator needed by shared-PA receipt surfaces 10-12. It does not project a
lineup, generate a probability, inspect a target-game outcome, access prices,
or authorize betting.

## Success and failure

Success requires an immutable non-May T-4 target, a fixed-runtime-bound
evidence scope, replayed roster and history ledgers, a response received within
the final 120 seconds before T-4, exact official request identity, retained raw
bytes, semantic replay through `parse_prior_schedule_denominator`, complete
coverage of every permitted calendar date from the collection epoch through
the day before the target, and an exact captured/terminal-missing partition
derived only from replayed history terminals. A caller cannot provide or
override that partition.

Missing, late, stale, malformed, ambiguous, overlapping, mutated, or
incompletely partitioned input is terminal failure. No missing game, response,
team, date, or byte may be imputed or backfilled.

## Protected invariants

- May 2026 is rejected before source path or fetch construction.
- Fetching is injected and can run only inside the locked T-4 window.
- Raw bytes are publish-once and content addressed.
- The target binds exact roster/history release snapshots and the evidence
  scope; the terminal retains the exact history authority for downstream replay.
- Each target is staged outside the authoritative target tree and exposed by
  one atomic directory rename; concurrent losers verify the winner.
- Release identity is recomputed from a fixed transitive dependency closure.
- Semantic receipts and coverage are rebuilt from retained bytes before
  publication and again during verification.
- A late tick records only permanent missedness and performs no fetch.
- Source errors record explicit terminal missingness and retain no fabricated
  raw receipt.
- Probability generation, production consumption, outcomes, prices,
  settlement, promotion, and betting are absent from this module.

## Required mutations

The focused gate must reject post-horizon and stale receipts, May-bearing
targets/ranges before body access, request or team swaps, returned-date gaps,
duplicate games, forged targets, authority changes, noncanonical raw aliases,
linked/reparse inputs, dependency changes, history denominator gaps,
immutable-name replacement, orphan raw objects, concurrent ticks, and retries
after terminal failure. It must also prove restart idempotence, a zero-game
range, multi-segment first/min/max chronology, and a segmented range that skips
May without opening it.
