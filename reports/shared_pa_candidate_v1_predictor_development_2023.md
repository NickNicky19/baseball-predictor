# Shared-PA candidate v1 predictor development

Status: **integrated research candidate; predictive superiority not established**

## Source foundation

The independently verified official-MLB 2023 source release contains 2,430
regular-season games and 43,740 original starters, including 14 zero-PA
starters.  Its qualified PA-volume artifact is:

- authority manifest: `a7f46262a8ffe508d036d7c89b822dc40f666edc159bf2d8174fe1549069bd5e`
- PA artifact: `be525ee7c30641f869f39f960712c878a85968cbadf6cf40772e85402790a533`
- external authority attestation: `ba0575f06d9b525d69973a9559d9f7f5dcb10d7c37d148b20872420cf78840c0`
- runtime authority receipt file: `16b83893b56d04861055b3ff9ae9dcc5fe991a61761d49f1d0715fa8d871f25a`

The exact authority was replayed through the active candidate adapter.  The
offline wiring proof emitted all five intended research markets: Hits, home
runs, Total Bases, batter strikeouts, and batter walks.  The fixture used for
that wiring proof was synthetic and was not retained or represented as a
prediction.

## Candidate definition

`shared_pa_candidate_v1` preserves the frozen predictor and the surviving
EB-200 batter-only PA control.  Its only new numerical component is a
source-bound game PA opportunity mixture:

1. receipt-derived probability of starting;
2. unconditional `P(start and slot)` masses from legal joint-lineup scenarios;
3. conditional `P(slot | start)` for display and later lineup evaluation;
4. qualified official-2023 `P(PA | original starting slot)`;
5. nonstart PA mass fixed at zero until a lawful pinch-hit model exists.

The candidate excludes pitcher context, BvP, and the mutable Savant aggregate
override.  A missing or contradictory required receipt causes abstention.

## Predeclared 2023 component result

Four expanding chronological folds evaluated only the slot-conditional PA
component.  This is conditional-on-known-start-slot calibration, not a
historical reconstruction of projected lineups.

Across 28,620 validation rows, slot conditioning versus the fold-local pooled
PA distribution changed the predeclared metrics by:

| Metric | Slot minus pooled | Interpretation |
|---|---:|---|
| Multiclass Brier | -0.03836248 | improved |
| Multiclass log loss | -0.09352215 | improved |
| Expected-PA MAE | +0.02286254 | worsened |

The component therefore did **not** improve every predeclared metric.  It is
not promoted and no market-level superiority is claimed.  The result still
shows that batting slot contains real distributional information: the proper
probability scores improved in every fold, while the mean-only error worsened
in every fold.  Untouched forward testing must determine whether integrating
start and slot uncertainty improves Hits, HR, and Total Bases probabilities.

## Why no historical start model was fitted

The authorized release contains original starters and their realized slots;
it does not contain the full point-in-time eligible roster for every team-day.
It therefore lacks the lawful negative denominator required to fit
`P(start | eligible at T-4)`.  Treating season rosters or final starters as that
denominator would fabricate historical pregame evidence.  The candidate uses
the existing receipt-bound empirical joint-lineup model and requires its
start/slot accuracy to be measured prospectively against later confirmed
lineups.

## Why no new batter PA model was fitted

The regularized batter-only shared-PA families already evaluated on the
permitted historical evidence were rejected.  Re-running them would add
selection bias, not information.  EB-200 remains the strongest surviving
control.  This candidate isolates the genuinely new hypothesis—better
opportunity—before another feature family is registered.

## What remains before forward predictions

1. Publish an exact clean release and runtime receipt for these candidate bytes.
2. Wire the existing genuine T-4 roster/history producer to the v3 source-bound
   record builder.
3. Retain projected and later confirmed-lineup lanes separately; never overwrite
   the T-4 record.
4. Generate explicit abstentions for every unsupported player/team.
5. Compare Hits, HR, and Total Bases separately on identical frozen/candidate
   rows after settlement.  Batter K and walks remain candidate-only research
   until their separate baseline/evaluation contracts are locked.

No May 2026 data, 2024 selection evidence, spent 2025 HR confirmation, prices,
prospective outcomes, or settlement data were accessed for this work.
