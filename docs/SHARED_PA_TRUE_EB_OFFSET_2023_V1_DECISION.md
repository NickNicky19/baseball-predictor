# Shared PA true-EB-offset 2023 decision

Decision: **REJECTED — no candidate, promotion, production change, or betting authorization.**

The exact pre-score candidate was committed at
`36d11b1fabee6d13d6530c23afd5d7cb510bf0ad`. Its execution lock has SHA-256
`a6fea5c2c274893e04fcd195f12677d3c3bac8f79614536d3ca8ed9971c6911b`.
The run used only the certified 2023 v4.1 panel, four expanding chronological
folds, and 34,673 out-of-fold player-games. The first 9,053 rows were training
only. Coverage loss inside the four validation windows was zero.

## What was tested

The candidate used a fitted strict-prior player Dirichlet posterior as a real
eight-class CatBoost baseline, then learned a residual from the repaired
batter-only process features. It was compared separately with:

- the outer-training league PA rate;
- the same time-safe player empirical-Bayes posterior without a residual;
- a frozen 200-PA empirical-Bayes control;
- the frozen all-prior CatBoost core family refitted within each training fold.

The historical game-market screen used the same pooled outer-training PA-count
distribution for every arm. It did not use the target game's lineup slot,
because the historical slot was obtained from a completed-game feed and lacked
a receipt proving pregame availability. These game-market results are research
screens, not deployable market proof.

## Result

All three markets failed their independent gates.

| Market/component | Central result | Why it failed |
|---|---|---|
| Hits over 0.5 | Candidate Brier 0.470859; log loss 0.663885 | It was 0.2786% worse in Brier and 0.2377% worse in log loss than fitted EB, with worse calibration and AUC. |
| HR over 0.5 | Candidate Brier 0.214915; log loss 0.368655; AUC 0.60180 | It beat league by 1.297% Brier and 1.821% log loss at the point estimate, but the Brier upper bound did not clear the required 1% margin. It was worse-calibrated than fitted EB and did not beat the frozen core. |
| Per-PA Total Bases | Candidate Brier 0.373804; log loss 0.746925 | It was worse than league, fitted EB, and the frozen core on both proper scores. |

The HR candidate-defined top decile had ample support—3,468 player-games and
661 home runs—and beat the league-rate tail. It nevertheless lost to fitted EB
and the frozen core on tail proper scores and calibration, so the tail gate
failed.

The fitted Dirichlet concentration was stable across folds: 149.09, 145.02,
148.66, and 150.36 PA. That stability supports the empirical-Bayes denominator
repair, but it does not rescue the residual candidate.

## Measured limiter

The time-safe player-rate baseline carries real signal. The current marginal
process summaries sometimes improve discrimination, but the residual generally
degrades calibration and proper scores. The result does not support selecting a
different tree depth, coefficient, prior, or threshold after seeing outcomes.

The strongest next input repair is one predeclared, strictly-prior batter
contact-quality block using source-truth counts and denominators:

- joint exit-velocity and launch-angle categories;
- official hard-hit, launch-angle sweet-spot, and barrel intersections;
- batted-ball-type counts;
- robust EV50-style upper-contact summaries rather than maxima;
- spray/pull counts only if the raw coordinate and handedness contract passes;
- explicit sample sizes and missingness for every field.

This direction is grounded in Statcast's own construction: MLB's
[Statcast glossary](https://www.mlb.com/glossary/statcast) and
[expected-statistics documentation](https://baseballsavant.mlb.com/expected_statistics)
describe xBA and expected outcome probabilities as functions of comparable
batted balls' exit velocity and launch angle. MLB also defines
[launch-angle contact bands](https://www.mlb.com/glossary/statcast/launch-angle)
and the [barrel](https://www.mlb.com/glossary/statcast/barrel) boundary, while
the Savant glossary defines hard-hit, sweet-spot, and EV50 concepts. The
project must reproduce any historical feature from strictly prior raw
observations; it must not ingest a retroactively recomputed expected-stat field
whose point-in-time version is unproven.

## Immutable evidence

- `decision.json`: `23970f4330973d34bce40dcb5719e5e03fd840a92ad61f1643cc54748bc8d393`
- `manifest.json`: `c60ccd977829d00c61365d3275e78da206e56b96dd8cb7a2e2dd6f5830575069`
- `oof_predictions.csv.gz`: `db336e7610c3f6d13642040ff6670814d9a53c0d6fce314a7a31306a3373a130`
- `report.json`: `da4cccc173645930d29644356bb48ab7133ead3ba228b382124bf77be07bda20`

The validator and 25 focused regression/mutation tests passed. No 2024 or 2025
evidence was opened, May 2026 remained sealed, no prospective record was
backfilled, and no AWS collector, production probability, frozen baseline, or
rejected candidate was changed.
