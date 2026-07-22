# PA-volume chronology integrity v1

Status: certified integrity repair and rejected single 2024 selection. Research
only. The frozen production baseline remains in force. No betting authorization.

## Defects repaired

The preserved prior artifact fitted its PA distribution on 2023 and the 2024
selection year together. Its fitter also required `out_pa > 0`, silently
deleting 14 valid zero-PA original starters in 2023 and 18 in 2024. At runtime,
the simulator could load an unhashed path, silently fall back when it was
missing, and condition on an official lineup slot without a decision-time
receipt.

The repaired artifact fits 2023 only and retains all 43,740 original starters
from 2,430 games, including zero PA as valid support. The strict runtime
requires its exact SHA-256. A lineup slot can affect PA volume only with a
T-minus-4-or-earlier authorization; otherwise the 2023 pooled distribution is
used and lineup-slot/expected-PA values are absent from the effective feature
vector. Contradictory evidence terminates.

## Locked 2024 adjudication

Historical 2024 has no receipt-proven T-minus-4 lineup state. Therefore the
single selection run used the 2023 pooled PA distribution for every one of
43,722 player-games (2,429 games), never the official 2024 lineup slot or
realized target-game PA. Per-PA probabilities used a 200-PA time-safe
multinomial empirical-Bayes player history, updated only after all games on a
date were predicted. The reader stopped before projecting 2025 outcomes. May
2026 was not read.

| Market | Candidate Brier | Legacy-volume player Brier | Brier delta | Candidate log loss | Legacy-volume player log loss | Log-loss delta | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| Hits over 0.5 | 0.236466 | 0.236800 | -0.000334 | 0.665738 | 0.666459 | -0.000721 | Reject |
| HR over 0.5 | 0.099777 | 0.099782 | -0.000005 | 0.348843 | 0.348863 | -0.000020 | Reject |
| Total Bases over 0.5 | 0.236466 | 0.236800 | -0.000334 | 0.665738 | 0.666459 | -0.000721 | Reject |
| Total Bases over 1.5 | 0.227257 | 0.227317 | -0.000060 | 0.646824 | 0.646953 | -0.000129 | Reject |

Every point proper score improved slightly, but none cleared the predeclared
1% materiality requirement against the time-safe player-rate arm. Hits and
Total Bases over 0.5 also had tiny AUC regressions. HR independently cleared
the league-rate comparison, including calibration and high-tail gates, but did
not materially beat the player historical-rate arm. No market rescues another.

The identity-aligned frozen production simulator, timestamp-certified valid
market-implied probabilities, receipt-proven lineups, and fresh untouched
confirmation/prospective replication remain unavailable. Accordingly every
promotion gate fails and the frozen baseline is retained.

## Bound artifacts

- Protocol SHA-256: `d6b1035d924f61ea3a98e8f08fbb82e795d40b2e3cda442dacbd1ebb860c9733`
- Repaired 2023 artifact SHA-256: `7ffd6a8fecb1c4f8aed1966c234a61499050346f87f884f731c440daca793c90`
- 2024 selection report SHA-256: `5439c0e3418d1b50e1c3153e5668b9721fdd01b60986b9358814523a6df9ce3d`
- Permissible source SHA-256: `3fc38325007845d6a7a99102274f7e520ed6f4310cc2a3248ebc441afeb814c5`
- 2023 fit projection SHA-256: `9fa2e04373440f069bb9e0d3642bf01ff56c777152f122e9bfed65a2392d0753`
- 2023/2024 adjudication projection SHA-256: `99a38a3dced0cc85f2a3bf59c9e1e03973128c0474421b856c419862af1e0019`

Single highest-value next action: deploy and independently verify the
future-only AWS T-minus-4 receipt collector, then extend its immutable target
evidence to the already-preregistered lineup/PA-volume boundary. Missing
receipts remain permanently missing and must never be backfilled.
