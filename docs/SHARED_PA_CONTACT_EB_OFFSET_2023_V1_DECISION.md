# Shared PA contact-augmented 2023 decision

Decision: **REJECTED — no candidate, promotion, production change, or betting
authorization.**

The exact scoring code was committed at
`da788f01043b8f35548a7afa67fb59f349193225` before the accepted run. The
experiment used only the certified 2023 base panel and the certified 2023
strict-prior contact-quality panel. It evaluated 34,673 out-of-fold
player-games in four expanding chronological folds; the first 9,053 rows were
training only and coverage loss was zero.

The residual used 116 fixed features: the exact 73-feature rejected-control
surface plus 43 numeric contact features. The contact panel's maximum prior
source date remained a chronology certificate and was explicitly excluded from
the model. CatBoost parameters, empirical-Bayes construction, PA-opportunity
screen, comparators, bootstrap, and gates were locked before scoring. There was
no feature, parameter, threshold, or model selection.

## Independent market results

| Market/component | Candidate result | Decisive evidence |
|---|---:|---|
| Hits over 0.5 | Brier 0.471262; log loss 0.664328; AUC 0.53442 | Worse than the rejected residual control by 0.0856% Brier and 0.0668% log loss, with worse AUC and ECE. It was also worse than fitted empirical Bayes by 0.3644% Brier and 0.3046% log loss. |
| HR over 0.5 | Brier 0.214917; log loss 0.368569; AUC 0.60227 | Versus the rejected control, Brier was 0.0011% worse and log loss only 0.0233% better; uncertainty crossed zero, calibration ECE worsened, and fold discrimination failed. It did not beat the frozen core or fitted empirical Bayes. |
| Per-PA Total Bases | Brier 0.373824; log loss 0.746890; AUC 0.54653 | Worse than fitted empirical Bayes by 0.1802% Brier and 0.2601% log loss and worse than the frozen core on both proper scores. |

All remaining Hits and Total Bases threshold components also failed their
separate gates. No market rescued another.

The HR candidate-defined top decile had sufficient support—3,468 player-games
and 699 home runs—but failed. Its Brier score was 0.323308, log loss 0.504448,
and absolute calibration gap 0.03029. It was worse than the rejected control,
fitted empirical-Bayes arms, and frozen core on tail proper scoring and
calibration.

## What this means

The contact input repair is valid, but it did not improve the model under the
predeclared architecture. This cleanly separates data integrity from predictive
value: truthful barrels, hard-hit counts, launch-angle bands, batted-ball types,
and EV50 do not automatically create a better probability model.

The fitted player empirical-Bayes history remains the strongest dependable
batter-only foundation. The result does not justify trying a different depth,
feature subset, prior, calibration map, or threshold after seeing these
outcomes.

The measured missing information is now conditional context rather than
another marginal batter summary: pregame batting opportunity and opposing
pitcher context. Historical completed-game lineup positions and actual starters
cannot be substituted because they lack decision-time receipts. The next
candidate must therefore wait for a hash-bound projected-lineup opportunity
contract and receipt-proven probable-starter identity, then be locked before a
genuinely future target date.

## Immutable evidence

- `decision.json`: `aaa853d7c0f7eaa617a1523f81a96164a344bb3a2f0538fb98ced4661626f4ad`
- `manifest.json`: `cd62059cf10ec44eb6e09256d052c4d21f0d5af7d46db215f093193017532b43`
- `oof_predictions.csv.gz`: `2b41094b4fd1806f22fda83561b3966eca93c202d620d448c46bcaa777e603c5`
- `report.json`: `0e48986244eb3754b774fc21a0916afc576d283405f99718a862386223cacab1`

The exact artifact validator passed, as did 43 focused regression and mutation
tests. No 2024 or 2025 evidence was opened, May 2026 remained sealed, no missed
prospective record was backfilled, and no AWS collector, production
probability, frozen baseline, or previous rejection was changed.
