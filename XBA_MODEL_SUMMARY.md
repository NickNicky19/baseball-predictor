# xBA-Driven Hit Model: Structural Rewrite

## What was wrong with the draft you reviewed

The draft had the right idea and four problems that would have made things worse:

1. **`_get_effective_xba()` was a stub returning 0.28 for everyone** — the hard part (threading the player's actual xBA into the computation) was skipped, so all hit discrimination would have vanished.
2. **It crashed**: `league.xba_on_contact` didn't exist (`AttributeError`).
3. **The math broke the league anchor**: multiplying the legacy hit weights by `bip_hit_prob` and then normalizing does not produce that hit probability — a league hitter would have dropped to ~0.15 hit-on-BIP (~0.5 hits/game).
4. **Double shrinkage** (toward league, then blended with a hardcoded 0.28 again).

## What was built instead

### 1. Hit rate comes directly from measured xBA — by construction, not by weight arithmetic
`_bip_outcome_distribution()` sets total hits-on-contact equal to the player's xBA (Statcast `estimated_ba_using_speedangle`), with:
- **Sample-size-aware shrinkage** toward the league contact-conditional baseline: weight = `sample_pa / (sample_pa + 120)` — one empirical-Bayes parameter instead of a fixed blend fraction.
- Park/BvP context scaling and the calibration lever applied, then bounded.
- HR backed out (xBA counts HR as hits), and power/speed shaping **only the mix** of singles/doubles/triples — never the total.

This **deletes** the contact→hit and power→hit couplings (five coefficients) rather than tuning them. The hit rate is a measured quantity. The `hit_prob_cap` remains only as a safety net; the bounded construction can't reach it.

### 2. Sampling and explicit probabilities unified — and a real pre-existing bug fixed
While reviewing the draft I found that `_calculate_hr_prob` returns **P(HR | ball in play)** (its intercept is `logit(hr_on_bip)`), which the sampling path used correctly — but `expected_outcome_probabilities()` reported that conditional as an **absolute per-PA probability**. The explicit path (which feeds `ProbabilityEngine` → your HR prop probabilities) was overstating HR by ~45% versus what the Monte Carlo actually simulated (0.042/PA reported vs 0.029/PA simulated for a league hitter).

`simulate()` now samples from the exact distribution `expected_outcome_probabilities()` reports; the two agree by construction and a regression test verifies it empirically (30k samples, HR within ±0.006). Safeguard HR bounds updated to the per-PA basis.

### 3. The last of the "0.08 divisor disease" cleaned out
The Statcast feature scales (`xwoba_scale=39`, `xslg_scale=30`, `hard_hit_scale=32`) were producing quality/power latents of magnitude **8–9** for elite hitters, feeding logit coefficients designed for magnitude ~1. Consequences: every quality hitter's K pinned at `k_max` (Judge simulated at **41.6% K** vs his real ~27%) and every decent power hitter pinned at `hr_max` (no discrimination among elite power). Scales now map the realistic cross-player spread to ~±1, and `hr_hitter_power`/`k_quality` were brought to per-unit magnitudes. Verified against real-world targets: Judge K 30.9%, Hoerner K 12.4%, Ramos HR-on-BIP 0.068 (un-pinned, ~28-HR pace).

### 4. Self-improvement loop redirected to one clean lever
`calibrate_hit_type_weights()` used to tune four interacting weight fields — all inert under the new model, which would have silently broken the learning loop. It now tunes a single bounded knob, `hit_rate_scale` (multiplier on target hit-on-contact), from hits-category bias.

### 5. Self-calibrating league baseline
`xba_on_contact` added to `LeagueBaselines` and config (cold-start 0.320); the Statcast engine now derives it from every live pull alongside the xwOBA/xSLG baselines. Watch for it in the calibration log line.

## Verification (all with your real calibrated baselines: 0.315/0.530)

League anchor exact: hit/PA 0.221, K 0.225, per-PA HR 0.029 (now the *correct* number), probabilities sum to 1.

| Hitter | K/PA | hits/g | HR/g | HRR |
|---|---|---|---|---|
| Bench bat | 0.273 | 0.71 | 0.063 | 1.32 |
| Hoerner (contact) | 0.124 | 1.05 | 0.090 | 1.84 |
| League average | 0.224 | 0.91 | 0.107 | 1.70 |
| Ramos | 0.259 | 0.93 | 0.182 | 1.92 |
| Lindor | 0.181 | 1.10 | 0.182 | 2.18 |
| Judge (elite) | 0.309 | 0.93 | 0.247 | 2.11 |

Everything under the ~3.0–3.3 cap, correctly ordered, hit/PA varies by player, K rates match real-world values, HR paces are realistic (Judge ~40 HR, Ramos ~29).

Tests: **14/14 simulation tests, 55 pass / 0 fail overall.** New guards: xBA drives hit rate; sampling matches explicit probabilities; elite K not pinned; plus all prior guards (neutrality, no cap pinning, base-state correctness).

## Files changed (7)

- `src/simulation/pa_simulator.py` — the rewrite
- `src/models/dataclasses.py` — `xba_on_contact` baseline
- `src/features/legacy_statcast_features.py` — self-calibrates xBA baseline from each pull
- `src/evaluation/calibration.py` — hits calibration → single `hit_rate_scale` lever
- `src/evaluation/output_safeguards.py` — HR bounds on per-PA basis
- `config/config.json` — `xba_on_contact`
- `tests/test_simulation.py` — 4 new regression guards

## After merging

1. Run `python main.py` — expect few or zero HRR warnings, and the calibration log should now print three baselines including `xba_on_contact=...`.
2. **Clear `data/learning/bias_corrections.json` one more time.** The HR consistency fix shifts explicit HR probabilities down ~45% to their true values, so anything learned against the old numbers is invalid. This is the last structural change planned before pairs data should be allowed to accumulate.
3. Run `scripts/diagnose_hrr_breakdown.py` — `hit/PA` should vary by player and `HR/PA` should be visibly lower than before (it's now a true per-PA number).

## Honest scorecard against the earlier assessment

- "Make hit weights less sensitive structurally, not just by lowering numbers" — done: the weights are gone; hit rate is constructed from a measurement.
- "Move toward learned per-PA probabilities" — still the endpoint (Phase 2, on `point_in_time.py` data). This rewrite reduces what that model has to learn.
- Hand-tuned coefficient count: net reduction (five couplings deleted, four calibration fields → one, two new principled parameters: shrinkage PA and the XBH tilts). The remaining logit coefficients are still hand-set and should ultimately be fit — but they now operate on sanely-scaled inputs, verified against real player benchmarks, and each has a single job.
