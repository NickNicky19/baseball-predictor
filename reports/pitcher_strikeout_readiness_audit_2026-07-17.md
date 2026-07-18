# Pitcher strikeout readiness audit - 2026-07-17

## Scope and evidence boundary

This is a secondary, read-only readiness audit performed while the primary Hits
lane waits for the credential rotation and permanently excluded operational
smoke. It did not modify prediction, simulation, feature, policy, protocol,
reconstruction, or validator code.

- Markets: `player strikeouts` only.
- Historical months inspected: March, April, and June 2026 only.
- May 2026 was not read and remains sealed.
- Market queries did not select or use vendor `result` or `won`.
- No official outcome, model residual, payout, ROI, or prospective economic
  result was inspected.
- Historical vendor prices prove recorded price inventory only. They do not
  prove that a price was visible, executable, filled, or settled for the user.

## Disposition

**Historical price inventory is sufficient to justify a future hard-keyed
pitcher-strikeout contract, but the market and model are not ready for economic
evaluation or betting authorization.**

The current analytic probability implementation is mechanically coherent and
the role-aware innings path is wired into the live runtime. However, the active
pitcher-K formula still contains unfitted/structural parameters, reports pitcher
input health as `not_assessed_for_pitcher`, and was previously evaluated by a
legacy gate that deliberately drops unmatched keys. That prior gate is not an
acceptable certificate under the current exact-universe rules.

## Outcome-blind historical market inventory

All counts below require an explicit two-sided T-4h entry and an explicit
two-sided pregame closing observation for the same vendor identity. Products
remain separate.

| Book/product label | Pregame market selections | Complete T-4h + close paths | Start dates | Multi-fragment vendor keys | Maximum fragments |
|---|---:|---:|---:|---:|---:|
| DraftKings | 12,166 | 1,948 | 56 | 1,808 | 6 |
| Novig | 5,201 | 2,820 | 56 | 1,100 | 3 |
| Onyx | 14,614 | 2,305 | 53 | 5,193 | 3 |
| PrizePicks historical vendor label | 2,090 | 1,878 | 56 | 379 | 4 |
| Pinnacle | 1,877 | 1,500 | 56 | 227 | 5 |

The historical PrizePicks label is not account-visible execution truth and is
not interchangeable with a PrizePicks lineup or payout contract.

DraftKings complete paths by month:

| Month | Complete paths | Start dates |
|---|---:|---:|
| 2026-03 | 178 | 6 |
| 2026-04 | 957 | 30 |
| 2026-06 | 813 | 20 |

## DraftKings line and quote-age profile

| Line | Complete paths | Dates | Median age at T-4h (min) | P95 age (min) | Median overround |
|---:|---:|---:|---:|---:|---:|
| 2.5 | 32 | 24 | 87.0 | 837.55 | 0.0660 |
| 3.5 | 363 | 56 | 96.0 | 846.30 | 0.0603 |
| 4.5 | 683 | 56 | 82.0 | 903.50 | 0.0603 |
| 5.5 | 523 | 56 | 88.0 | 889.80 | 0.0603 |
| 6.5 | 259 | 53 | 95.0 | 939.00 | 0.0601 |
| 7.5 | 80 | 38 | 100.5 | 849.70 | 0.0598 |
| 8.5 | 8 | 6 | 83.0 | 513.35 | 0.0590 |

Across all 1,948 DraftKings paths, the median paired quote age at T-4h is
89 minutes, P95 is 889.65 minutes, and the maximum is 2,828 minutes. Exactly
987 paths are at most 90 minutes old and 961 are older. This does not validate
a 90-minute freshness rule; it demonstrates that freshness materially changes
the universe and must be fitted or predeclared without outcome-driven tuning.

## Runtime findings

1. `DailyPredictor` reaches `PropEngine.project_pitcher_strikeouts()` through
   the active MLB probable-pitcher path.
2. The point estimate blends season and recent K/9 using `0.35 / 0.65`, then
   regresses that result toward league K/9 using `0.75 / 0.25`. No fitted
   provenance artifact for those four weights was found.
3. `role_innings.enabled` is `true` in both `config/config.json` and
   `config/config.kbb.json`; it is not inactive.
4. The active role block matches
   `data/analysis/b4/candidate_role_innings.json`: opener innings `2.6` was
   fitted from 281 point-in-time 2023 rows, but bulk innings `3.5` was retained
   as a placeholder because 269 rows missed the predeclared `min_n=275`.
   Starter thresholds/clamps and the `5.5` default remain structural values.
5. The probability distribution is analytic. With no
   `pitcher_k_dispersion` value in the active configuration, it uses the
   Poisson limit (`0.0`). This is not itself a defect, but dispersion has not
   been economically validated for this market.
6. Pitcher projections emit the factual health label
   `not_assessed_for_pitcher`; therefore fallback/data-health stratification is
   not yet adequate for a pitcher-K authorization gate.
7. A trained CatBoost strikeout model exists on disk, but no active daily
   runtime loader or invocation was found. Its mere existence is not evidence
   that it improves or even reaches live predictions.

## Existing test and evidence assessment

The five existing offline harnesses completed **140/140** checks:

- B3 analytic distribution: 13/13.
- B4 role estimator: 26/26.
- B4 legacy gate tools: 24/24.
- Point-in-time role rows: 33/33.
- Point-in-time pitching/runtime wiring: 44/44.

Those checks establish useful mechanics, but they do not certify the old gate.
The load-bearing defect is explicit in the harness itself:

> `V4 unmatched keys are dropped (inner-join)`

`run_b4_gate_verdict.py` inner-joins frozen, candidate, and outcomes and prints
that unmatched rows are dropped. This violates the project's current exact
MODEL_KEY equality rule and can silently change the evaluated universe. The
legacy gate also passes when the candidate is merely not significantly worse;
it does not require the +0.10 capture lower bound, positive net-ROI lower bound,
product-specific settlement, or executable price evidence.

The old B4 metrics covered only 349 strikeout projections over 14 dates. The
candidate Brier point estimate improved on lines 4.5, 5.5, and 6.5, but every
confidence interval crossed zero. These results are exploratory mechanics
evidence, not independent proof of market value.

## Missing prerequisites

Before any pitcher-K model intervention or economic gate:

1. Build and hash a hard event crosswalk to official `mlb_game_pk`.
2. Build and hash a hard pitcher identity crosswalk to official `player_id`.
3. Construct a unique final MARKET_KEY and exclude every conflicting duplicate
   pair by predeclared rule.
4. Obtain official-final pitcher strikeout outcomes and starting/role evidence;
   never grade with vendor results.
5. Establish product-specific settlement, void, postponement, and correction
   contracts.
6. Establish exact model coverage and fail on unequal model-key sets rather
   than shrinking through joins.
7. Replace or explicitly quarantine every unfitted parameter; fit only on a
   chronologically earlier, hash-bound period.
8. Add factual pitcher input-health/fallback labels.
9. Predeclare a market-specific evaluation contract including calibration,
   discrimination, capture, posted-price payout, uncertainty, and coverage.
10. Require untouched holdout and future executable shadow replication. No
    product may rescue another product and no market may rescue another market.

## Highest-value next action for this lane

After the primary Hits operational smoke is complete, the first pitcher-K step
should be an **outcome-blind hard-identity and duplicate-funnel build**, not a
model rewrite. That determines the real scoreable market universe and whether
the apparent 1,948 DraftKings price paths survive official event/pitcher
identity and uniqueness. Only then can residual evidence determine whether
innings variance, K-rate inputs, dispersion, or a fitted challenger is the
actual limiting component.

## Evidence hashes

### Runtime and legacy evidence

- `config/config.json`: `77c029297b514e3df652bb447d90e58906e954c64dcd039d78537b9e59870436`
- `src/prediction/prop_engine.py`: `a67a48d1f50be42e6cc488ce74ced36855f3032e33663f0b80863888dc1401ed`
- `src/prediction/role_innings.py`: `025928e65fbe478043503481c940555729f558239ac35c8f2df0e6bc419ce771`
- `src/data/mlb_api.py`: `89d89232e7c1932ea913652bc4f5ec0e288ede1be4bd045547a88ec6edaf2270`
- `run_b4_gate_verdict.py`: `0108ba2cabbf7e98ba7cacbe1ddc3c757b7e3bfa2b96998b436d2c0d60049a7c`
- 2023 PIT role artifact: `6ae2a981960d17b2171a34e29de6e8f9927f33d392a966e33a5f294028f15ce3`
- Legacy B4 metrics: `1842a3148a66ffcb3471cb0943a5c2bf4e81e79618e1539f5288abf78fd45514`
- Dormant CatBoost strikeout model: `239a1c67588b30801f06b797aa2024d5a2c73cdd1ba1048677e769e9fcd37285`

### Outcome-blind SmartStake inputs

- March part 000: `c1058237807fbd39306a3b5091460cf4c02e63948e457e3c0a2a3b1aa4e0fba3`
- April part 000: `d3eb5e15fa0309ea5b1e698daf40d32d9f6376b0600eb60a07451a12668ceebd`
- April part 001: `af9938e78c3bf9ee5cda96cd646da7671e547bfbf1763f3c14c8ef53bdd97aad`
- April part 002: `aab964eb77b573a3f1552ae89b0e9447fd9bb8b342370d2578f51c3ebabfa2b4`
- April part 003: `e96441885d057603d4b88ede94e676a3d4cbe56e720b370a3d094e59327f4b92`
- April part 004: `69b645f171c5824ae8f2f5cc6a24b652a7436d7d6a805b0b0b70310080c33188`
- June part 000: `616dccd4e06c3138942236eed2531ade8db7d13c93f5f6babca504be40aba7d6`
- June part 001: `dc9c09351caf0976bbc46990713a8c37c62ed14a42fdea71202fb9ada827bb74`
- June part 002: `1b5db24a71e7a237f2f76bab0bbb619602b0693cac801766b13e2ac90562c37c`
- June part 003: `c9e1138e9b4776fbe635b4bde94fa6bf01ea09e3a35f553fa706b2e3d545ebd1`

## Authorization status

`RESEARCH_ONLY`. No pitcher-strikeout product or market is authorized for
wagering by this audit.
