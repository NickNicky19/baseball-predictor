# 2023 batted-ball composition development adjudication

Status: **rejected; no development survivor; frozen production baseline retained;
no betting authorization**.

This was the single predeclared 2023-only internal development experiment from
`direct_batter_pa_development_2023_v1`. It tested whether replacing overlapping
barrel and hard-hit rates with mutually exclusive, count-bearing batted-ball
composition materially improved the shared batter PA foundation. It did not
select, confirm, promote, or evaluate a game-level betting market.

The candidate represented measured batted-ball evidence as barrels, hard-hit
non-barrels, and other measured BBE over one denominator, plus measured BBE per
prior PA. The source constructor now excludes non-PA and missing terminal events
from BBE and fails closed if counts are partial, negative, out of order, or the
measured-BBE denominator exceeds prior PA exposure. Regression and mutation
tests cover those boundaries.

## Sealed chronology and coverage

- Read exactly the certified 43,740-row 2023 prefix; parsed no 2024 rows.
- Excluded 14 zero-PA rows before fitting, leaving 43,726 eligible rows.
- Produced 34,673 out-of-fold predictions across 144 dates and four expanding
  chronological folds with zero eligible-row coverage loss.
- Did not open 2025 confirmation, May 2026, outcomes from any 2026 workflow,
  pitcher receipts, lineups, prices, settlement, execution, or market evidence.
- Production probabilities, policy, and the July 22 collector were unchanged.
- Focused regression/mutation tests passed 14/14; the full repository suite
  passed 158/158.

## Locked result

| PA component | Candidate Brier | Legacy Brier | Candidate log loss | Legacy log loss | Candidate AUC | Legacy AUC | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| Hit event | 0.34697461 | 0.34702292 | 0.53122489 | 0.53129865 | 0.519516 | 0.518578 | Reject |
| HR event | 0.06308505 | 0.06308220 | 0.14248513 | 0.14246899 | 0.590255 | 0.590365 | Reject |

The Hits proper-score changes versus the legacy representation were very small
and their paired date-block 95% intervals crossed zero: Brier delta
`-0.00004831 [-0.00013216, 0.00003718]` and log-loss delta
`-0.00007376 [-0.00018483, 0.00004023]`. They were also far below the locked 1%
materiality requirements, and two of four folds worsened on both proper scores.
The candidate was worse than the time-safe empirical-Bayes player comparator.

HR was fractionally worse than the legacy representation on Brier, log loss,
and AUC. It improved proper scores versus the empirical-Bayes and league-rate
comparators, but did not clear the locked 1% materiality gates except for log
loss versus the league rate alone. HR also failed calibration-intercept,
calibration-slope, AUC-noninferiority-versus-legacy, and fold-consistency gates.
Only one of four HR folds passed all three fold gates.

No component passed every predeclared materiality, calibration,
discrimination, uncertainty, and chronology requirement. Shared inputs cannot
let one market rescue another; no versioned model candidate is created from
this rejected representation.

## Bound hashes

- development protocol: `0133cc6979aa09f441c2230f04c7c0891ed9f18a844eed0798661f43c4985552`
- calibration addendum: `6af309ff53d94a48b953278f09415f4bd42112e7c73f8fef5618dda35a4cf1cd`
- extracted panel: `5e47ee69416d22bfe7eb0b7745abf7ac60cd9fa585c2f0d54aff266fb59ec4f8`
- extraction manifest: `ca6246eeb80ca8e2d11baad1ccffc738778719979efd23dde0d33aaac873d0a3`
- development report: `53dcae63adec1e4b0c23b5656256b02ff9debd684be64d237734c5bf11299cfb`
- OOF predictions: `f1c41f491cebc8fff343ddbc40f2e5ff0fccc29da42b93a8dd477cfcf854fb5e`
- direct-history implementation: `3ce4e2084d3d60a4f57b847f5f41011e9a34a37883830df40dd376575176b451`
- extractor: `b803a17cf609e65afb69f953f911d7a4805d3f900ed100932209dd304c6d3fcc`
- evaluator: `e38d719b48c45710424e4be342dd59ca3516cf830c42fb78c6dabc27f9732b07`
- direct-history tests: `c1741f1ccc829ef5b3e7c937850db38fcef8b138acbb0979fa6958f3543d5000`
- extractor tests: `2cf2163390913317b6e06961d2d24db63f60d60f5ab88ef353d50071efd89dbd`
- evaluator tests: `ffa2defc9ca7f33bad9357913745499cc5a2d4b3e347532933bf174c781b5ccc`

## Single highest-value next action

Lock a forward-only, batter-only shadow data contract that captures truthful
counts, denominators, source hashes, PA-volume inputs, and decision-time
predictions before each game. Use 2023 only as acknowledged development data
for any future regularized/nested design work; do not reuse these revealed
folds or the spent 2024/2025 outcomes as independent proof. Fresh prospective
replication is now the only honest path to promotion evidence.
