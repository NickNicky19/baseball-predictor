# Direct batter PA foundation v3 adjudication

Status: **rejected; frozen production baseline retained; no betting
authorization**.

Candidate: `direct_batter_all_prior_regular_season_v3_source_truth_repair`.
This is a source-integrity repair and fitted batter-only PA challenger, not a
production promotion. May 2026 was not fetched, opened, parsed, or written.
The spent 2025 HR confirmation was not opened or reused. No pitcher, lineup,
price, execution, settlement, or reconstructed prospective evidence entered
the candidate.

## Integrity result

- Built 87,462 player-game rows: 43,740 in 2023 and 43,722 in 2024.
- Independently rehashed 1,291 raw 2023/2024 batter files.
- Certified zero identity duplicates and zero source-date chronology violations.
- Certified exact raw terminal-PA truth parity at the probability consumer.
- Certified shared batted-ball denominator/count/rate lineage after serialization.
- Excluded 31 zero-PA rows before fitting/scoring, leaving 43,726 fit rows and
  43,705 selection rows; coverage loss among eligible rows was zero.
- Full repository suite: 148 passed.

The repaired consumer also fails closed on partial feature-pipeline output,
duplicate rich-feature ownership, corrupt fitted K/BB rates, impossible
batted-ball lineage, and unknown pitcher-HR direction modes. The corrected
pitcher HR/9 direction is mutation-tested, but this candidate excludes the
entire pitcher block because no receipt-proven starter identity is used.

## Locked 2024 selection result

The scored components are PA-outcome foundation diagnostics. They are not
game-level Hits, HR-over-0.5, or Total-Bases market probabilities.

| PA foundation | Brier | Log loss | AUC | Calibration intercept/slope | Decision |
|---|---:|---:|---:|---:|---|
| Hit event | 0.34236272 | 0.52600504 | 0.521903 | -0.5875 / 0.5240 | Reject |
| HR event | 0.05838584 | 0.13375740 | 0.596440 | -0.3543 / 0.9079 | Reject |
| Total-base class | 0.36715998 | 0.73091820 | 0.543224 | class-specific; double slope 0.2418 | Reject |

HR improved proper scores against each internal comparator, but not by the
predeclared material amount:

| HR comparator | Brier delta (candidate - comparator), 95% date-block CI | Required below | Log-loss delta, 95% CI | Required below | AUC result |
|---|---:|---:|---:|---:|---|
| 2023 league rate | -0.00020478 [-0.00024311, -0.00016539] | -0.00058591 | -0.00175460 [-0.00206789, -0.00144819] | -0.00135512 | Better |
| Time-safe EB player rate, 200-PA prior | -0.00008670 [-0.00011031, -0.00006302] | -0.00058473 | -0.00073563 [-0.00093080, -0.00054087] | -0.00134493 | Better |
| All-prior fitted core | -0.00002836 [-0.00004603, -0.00001125] | -0.00058414 | -0.00017921 [-0.00032072, -0.00003655] | -0.00133937 | Worse: 0.596440 vs 0.599223 |

Only HR log loss versus the league-rate comparator cleared its individual 1%
gate. Every required comparator/gate combination had to pass; therefore HR
failed. Hits was worse than the fitted core on both proper scores and AUC.
Total Bases was worse than the fitted core on both proper scores and AUC and
did not materially beat either simple comparator. No market can rescue another.

The descriptive top-decile HR/PA audit found candidate predicted 0.05319 versus
0.04893 observed (gap -0.00426), compared with the fitted core's 0.05626 versus
0.04795 (gap -0.00831). This audit has no gate effect because its exact tail
threshold was not locked in the candidate protocol; it cannot rescue the
rejected selection.

## Market blockers

- The frozen production simulator is not identity-aligned to this panel, so
  the required HR comparison is missing rather than fabricated.
- No verified point-in-time executable market probabilities are available for
  these rows.
- A full-game probability requires a separately locked point-in-time PA-volume
  distribution. Realized game PA was not used as a prediction input.
- Fresh untouched confirmation/prospective replication is still required.

Accordingly, the repair fixes integrity defects but earns no model or market
promotion. Production probabilities and policy remain unchanged.

## Bound hashes

### Evidence

- protocol: `efda4db5e21abfaa3d702418667709e4c1d53256838fa9abaedc610dd8e75e61`
- panel: `1638ee336833ae6cb3ba580eafe2800a8f4a6c609719fe88df7481c71e0f2fb1`
- panel manifest: `673ece8b9a26733ad4c8bf0b95fb15f6b32849e7a37c9b2fcb7f0734dcb4030f`
- panel certificate: `a4fe34a0ad58bec046c0f00fe8e7029535bd2f9aa02eff391f03304b867cbaaf`
- selection report: `dcd1079f3728387b1993db4d05ce450a948356b110558e4268304364c670dfba`
- selection predictions: `29353af5b6765072eb86467e8fedd8b06f8167c80c64fbc37b7ff2a456b613d2`
- descriptive tail audit: `d9e611be4eb615758ae3a6e4d95ace2d7cde34d40539a0fef0e288487aa1713e`

### Candidate code recorded at selection

- panel builder: `978c12c7a82c52f926039ebd963751f082011e390ad5101069b8a0b8e8842453`
- panel validator: `c7f58b61714fece13288f049a8c5ccc79f78461e3858823ca436b0f40d92675e`
- selector: `e671ecc79de6dee44d270613581e8ee24369fe8768a86b2204b47d62400f3b80`
- direct history: `783b8c6dbbec9875ad4937ba530bcae347b29b3e03f681fac0d0b4507a163d82`
- Statcast integrity: `fad152c8abc92892949344421a3fba26067bf95e8fe1ea9367af259586786b15`
- feature pipeline: `6ef40f19cec9a9031901d919bdd775331efd6eee332943bff612aeca9f342a69`
- PA simulator: `c8832f17dbc5328ac52be8fc6fb37b12b0664ba0e782baabc1eec6986d4e8a4f`

The selection report contains the individual candidate test hashes. The tail
audit and its tests are separately bound as
`9bd519e48457c2025d4218ecf9f236d62e61e495bfa96d088d41a2b3c1e87004`
and `21872a029d14efb82620249235840eea534079fb06b645d4b7c9107dd1ca3e22`.

## Single highest-value next action

Use only 2023 internal chronological folds to measure which truthful,
count-bearing batter-history block adds HR discrimination beyond the all-prior
core, without reopening 2024, 2025, or May 2026. Predeclare one fitted
challenger from that development work and lock it for prospective forward
replication alongside a point-in-time PA-volume receipt. Do not rerun 2024
selection, tune the rejected v3 candidate, or touch the July 22 pitcher
collector.
