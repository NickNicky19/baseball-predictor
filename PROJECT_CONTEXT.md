PROJECT_CONTEXT.md — v7 (master roadmap) — paste into any new chat

Keep this file in the repo. Start a FRESH chat per task, upload this (plus the
repo/zip if code work is needed), and say which roadmap item to work on.
Update "Current status" and check off roadmap items as they complete.

===============================================================================
CURRENT STATUS (2026-07-10) — B4 OFFLINE BUILD DONE (GATE OPEN); B3 CLOSED;
GATE #4(b) FULLY CLOSED (HITTERS + K)

Phase A machinery COMPLETE; the real 2023-2025 data run is DONE; B1 (model
pick), B2 (calibration), and B3 (distributional pitcher K) are ALL CLOSED;
gate #4(b) passes for hitters AND strikeouts — gate #4(b) is FULLY CLOSED.
Next front is B4 (role-aware expected_innings), which fixes the simulator's
short-outing K bias. B4 IS a live-model change (forks model_version) and
must be gated like the doc says — do not slip it in casually.

Since v6, one thing closed:

B4 (role-aware expected_innings) is BUILT and OFFLINE-VALIDATED (24/24 in
scripts/check_b4_offline.py), but its GATE IS OPEN — it is not promoted and
the live model is unchanged. The live model_version is STILL dab23f4fbac8
(confirmed against the real config: adding "role_innings" to the allowlist
is inert while no role_innings block exists in config.json). B4 is the first
live-model change since the freeze; it does NOT go live until it forks the
hash at the gate and clears gate #4 (walk-forward + calibration + shadow).

B2 CLOSED (2026-07-09/10). CatBoost point predictions -> calibrated P(over)
per category. DESIGN VERDICT: the count model IS the calibrator. lambda =
mean_scale * predicted_value, per-category family chosen from the
CONDITIONAL dispersion of the walk-forward pairs (not the marginal, which is
inflated by the spread of lambda):
hits       Poisson        (cond var/mean ~0.85-1.0)
home_runs  Poisson        (cond var/mean ~1.0; rare-event canonical)
hrr        neg-binomial   (a=0.80; cond var/mean ~2.2, stable across bins)
strikeouts neg-binomial   (a=0.01; ~Poisson on the 2024 fit)
Post-hoc isotonic/Platt could NOT beat the raw count model out-of-sample
(it is already well-calibrated); direct per-line logistic (approach b) was
uniformly worse. So deployed_stage2=identity for ALL categories.
HR (betting focus) carries mean_scale=0.907 correcting a ~9% hot point
estimate (holdout ECE 0.020 -> 0.013, better on Brier AND log-loss). K's
fitted scale did NOT transfer (thin 2024 fit) so K stays mean_scale=1.0 —
a guard in the runner made that decision from the holdout, not by hand.
Holdout ECE all cats < 0.028 (fit on 2024 pairs, tested on 2025 pairs);
production artifact refit on ALL 2024+ pairs with the validated config.

GATE #4(b) — calibration — FULLY CLOSED (2026-07-10; hitters first, K same
day right after B3). GBM calibrates equal-or-better than the simulator on
matched rows (17,137 matched rows [gbm=17,223, sim=18,429], 15 reconstructed
2024+ dates, seed 17; 1 thin date kept — 2024-03-21 at 80 rows — the block
bootstrap weights it naturally). Verdict uses a BLOCK BOOTSTRAP OVER DATES
(B=4000, 95% CI) on the per-row Brier difference — NOT a fixed epsilon (the
original 1e-4 epsilon wrongly failed the gate on sub-0.001 noise gaps; that
was itself a "headline vs evidence" trap the discipline caught). Result, all
four categories:
hits        0.5  n=3218  GBM  dBrier -0.0172 [-0.0214,-0.0135]  (significant)
hits        1.5  n=3218  GBM        -0.0047 [-0.0070,-0.0022]  (significant)
home_runs   0.5  n=3218  TIE        +0.0008 [-0.0002,+0.0016]  (n.s.; Murphy
skill ~0.003 BOTH models = rare-event noise floor; not a sim win)
hrr         1.5  n=3218  GBM        -0.0054 [-0.0101,-0.0007]  (significant)
hrr         2.5  n=3218  TIE        +0.0003 [-0.0021,+0.0032]  (n.s.)
strikeouts  4.5  n=349   TIE        -0.0081 [-0.0221,+0.0041]  (n.s. — CI
crosses zero; point favors GBM but is NOT significant, read the CI not the
point gap — same lesson as the fixed-epsilon trap)
strikeouts  5.5  n=349   GBM        -0.0174 [-0.0276,-0.0088]  (significant)
strikeouts  6.5  n=349   GBM        -0.0117 [-0.0193,-0.0045]  (significant)
Sim significantly better on ZERO lines (of eight) -> PASS. Verdict metric is
Brier (proper, bounded); log-loss reported as supporting evidence; Murphy
skill (UNC - Brier) printed per line so noise-floor categories read
honestly. NOTE: K's Murphy skill (~0.02-0.04) is notably HIGHER than the
hitter categories (~0.004-0.01) — real resolution above the base rate, not
another noise floor like HR/hrr-2.5. CAVEAT: K legs rest on n=349 rows / 14
dates — thin, as flagged below; one leg of evidence, not a reason to lean on
K before forward pairs accrue.

B3 CLOSED (2026-07-10). project_pitcher_strikeouts now ships an ANALYTIC K
distribution (Poisson, or NB via a dispersion knob) instead of
simulation=None — see the new [B3] key-decision entry below for the design
reasoning. The gate harness picked it up with zero changes to its lookup
logic; only STANDARD_LINES gained a strikeouts entry and the date-sampling
weight was pinned to the hitter subset so --seed 17 reproduces the same 15
dates as the original hitter-only run. Files: src/prediction/prop_engine.py,
scripts/check_b3_offline.py (13/13 offline), run_gate_reconstruct.py.
Commit e670fb6.

A5 CODE DONE (2026-07-10, own chat). Odds sourcing confirmed on The Odds API
free tier; read-only line logger built + offline-tested. See A5 STATUS
section. A5 blocks nothing and stays off the critical path.

GIT RECOVERY (2026-07-10). B1's entire codebase had never been committed
(it existed only on the local box). While cleaning that up, a git reset   --hard deleted the 9 staged-but-uncommitted B1 files from disk; all were
recovered byte-for-byte from dangling blobs via git fsck + git cat-file,
verified by hash-match + 18/18 offline harness, then committed. See the new
operational gotchas at the bottom — this is the most important lesson added
this session.

model_version UNCHANGED = dab23f4fbac8. CONFIRMED 2026-07-10 via git grep:
src/utils/model_version.py:47 shows model_version(config, length=12) -> str
— a pure function of the config DICT only (no source file, no commit SHA),
matching this doc's own definition ("config hash"). B3 never touched
config.json (pitcher_k_dispersion is read via a .get(..., 0.0) fallback,
not a required key), so the hash's input is unchanged -> the value is
unchanged by construction, not just by inference. (Also independently true:
projected_value == round(sim.mean, 2), harness check 10 — the live-path
OUTPUT didn't move either, belt and suspenders.) The simulator remains the
LIVE production model until C2's gate. All B2 / gate-4b / B3 work was
ADDITIVE (no live model change), consistent with the collection freeze.

NEXT ACTIONS:
(main line) B4 GATE — own chat. Offline build is DONE + committed. At the
gate: fit role->innings constants vs K-error/actual_ip (A6), add enabled
role_innings block to config.json (forks model_version), run
run_gate_reconstruct.py + block-bootstrap verdict that role-aware K
calibrates equal-or-better than the frozen sim. Then shadow (C2). Do NOT
run casually during collection.

===============================================================================
What this project is

MLB player-prop prediction system (hits, HR, HRR, pitcher strikeouts).
Goals: accuracy, backtestability, self-improvement, modularity, minimal
hand-picked coefficients, real Statcast features, calibrated probabilities.
Endgame: learned per-PA models (CatBoost) trained on multi-season
point-in-time data, calibrated P(over) vs live market lines, fully automated.
Repo: C:\Projects\baseball_predictor. GUI via python main.py; CLI via
run_*.py; automation via GitHub Actions.

===============================================================================
Architecture (layers)

Data (src/data/: mlb_api, savant, point_in_time, http_cache[A3],
statcast_roller[A4]) -> Features (src/features/) -> Simulation
(src/simulation/: pa_simulator -> game_simulator -> monte_carlo) ->
Prediction (src/prediction/: prop_engine, edge_calculator, daily_predictor,
shadow_logger) -> Evaluation (src/evaluation/: calibration_report,
backtest_engine, walk_forward_validator, output_safeguards) -> Learning
(src/learning/: outcome_recorder, retrain_runner, training_set_builder[A3],
gbm_dataset[B1], gbm_trainer[B1], gbm_benchmark[B1], gbm_calibrator[B2]).

run_*.py entry points (ALL live at repo root — see gotchas):
run_slate.py               A1  all-category predict (CI + manual)
run_reconstruct_date.py    A2  one historical date -> resolution report;
+ reconstruct_objects() (B1/gate seam)
run_build_training_set.py  A3  2023-2025 training-set builder (resumable)
run_audit_player.py        A3  as-of snapshot vs raw game log
run_build_statcast_features.py  A4  join rolling Statcast onto A3 rows
run_assemble_enriched.py   B1  assemble statcast shards -> training gz
run_train_gbm.py           B1  train per-category GBMs (temporal split)
run_benchmark_gbm.py       B1  GBM-vs-simulator on sampled holdout dates
run_walkforward_gbm.py     B1  5-fold walk-forward, both models, saves pairs
run_wf_reanalyze.py        B1  matched-outcome reanalysis (authoritative WF)
run_calibrate_gbm.py       B2  fit/validate calibration; writes calibrators.json
run_calibration_gate.py    B2/gate  emit GBM P(over); compare vs sim
(block-bootstrap verdict + Murphy skill)
run_gate_reconstruct.py    gate  sim-side P(over) emit from reconstruction
(additive/leakage-safe; hitters only until B3)
run_log_lines.py           A5  read-only daily prop-line logger
probe_odds_apis.py         A5  one-off feasibility probe (disposable)

===============================================================================
How the model works (one paragraph) — UNCHANGED, still frozen

Per PA: logits for K and BB, then HR-on-contact, then hit type. Hit rate on
balls in play is driven by the hitter's xBA (shrunk to a contact-conditional
league baseline); power/speed shape only the extra-base mix. Opposing pitcher
enters as a modifier: K coef 1.10 (strong), BB 0.90, HR 0.20 + HR/9 skill
term; pitcher never touches hit-on-contact rate (deliberate). Game totals via
Monte Carlo over PAs. Runs/RBI via base-state model. Pitcher K = per-PA rate
x (expected_innings x 4.2) — this POINT ESTIMATE FORMULA is UNCHANGED; B3
(2026-07-10) wrapped it in an analytic Poisson/NB distribution
(simulation != None) WITHOUT touching the formula itself (additive, by
construction). Model math was NOT touched by any A1-A4, B1, B2, or B3 work.
The simulator remains the LIVE production model until C2's gate.

===============================================================================
Key decisions & why (do not relitigate)

Statcast xBA/xSLG/xwOBA centered on CONTACT-CONDITIONAL baselines (season
baselines gave phantom power). Baselines self-calibrate from live pulls.
xBA drives hit rate directly (old latent-weight arithmetic triple-counted
contact; deleted ~5 coefficients).
Feature scales range-normalized so latents sit near +/-1.
Runs/RBI decoupled from hits; HRR cap derives from league HRR rate.
Edge engine de-vigs both sides, uses true MC distribution, quarter-Kelly
capped 5%, ranks by Kelly.
GUI (2026-07-08): ONE "Run all" = ONE predict() call for all categories ->
tabbed display -> one archive/day.
outcome_recorder: pairs CSV records actual_pa/hits/hr/runs/rbi/bb/k
(hitters) and actual_ip/k/bb_allowed/hr_allowed (pitchers); auto-migrates
legacy CSVs. actual_ip is the training signal for the future IP model.
[A1] run_slate.py = one predict() for all categories; rich JSON archive is
committed and later graded. model_version tags every pair. CURRENT
model_version = dab23f4fbac8.
[A3] Training rows are RAW as-of stats, not the engineered FeatureVector,
on purpose: GBMs learn from signals, the simulator stays the baseline.
[A4] Rolling windows are GAME-based (last 15/30), contact-conditional
aggregation matching SavantClient (one ruler). Barrel/hard-hit derived from
raw feed (launch_speed_angle==6; EV>=95).
[B1] MODEL PICK: CatBoost, all categories (via matched walk-forward
reanalysis). LightGBM eliminated: no K edge. The raw walk-forward aggregate
that said "sim wins" was confounded (mismatched outcomes + thin-training
2023 fold); superseded by run_wf_reanalyze.py's matched result.
[B1] Walk-forward comparisons MUST be on matched outcomes: inner-join sim
and candidate on (player_id, game_date, category) before scoring.
[B1] Fold adequacy matters: a fold trained on < 1 season is a floor-check,
not a deployment estimate. Tag/exclude thin dates and thin-trained folds.
[B1] Fantasy is TRAINABLE but UNGATED (no simulator baseline) -> phantom
win risk. --include-fantasy for later.
[B1] Out-of-sample fold predictions live in
data/models/gbm/wf_predictions_<model>.csv — B2's calibration input.
[B2] The count model IS the calibrator (deployed_stage2=identity). Family
chosen from CONDITIONAL dispersion, not marginal. lambda=mean_scale*pred.
Post-hoc isotonic/Platt did not beat raw out-of-sample; approach (b) worse.
[B2] mean_scale is a guarded, per-category bias correction: fit c =
sum(actual)/sum(pred) on train (Poisson MLE), KEEP only if it improves
holdout ECE without worsening Brier/log-loss. HR kept (0.907); K rejected
(thin-train scale did not transfer). hits/hrr ~1.0.
[B2] Validation split = fit on 2024 pairs, test on 2025 pairs (a real
next-season generalization test). Production artifact refit on ALL 2024+.
[gate] The gate VERDICT uses a block bootstrap OVER DATES (rows within a
slate are correlated; dates are the resampling unit). "Equal or better"
treats a statistical tie as equal — a sub-0.001 Brier gap is not evidence
the sim is better. Murphy skill (UNC - Brier) flags noise-floor categories.
[gate] The sim's P(over) is MonteCarloResult.p_ge_threshold, present on
freshly-reconstructed projections for ALL FOUR categories as of B3 (hitters
via project_hitter's mc_result; pitcher K via project_pitcher_strikeouts'
analytic wrap). Emit is leakage-safe (reuses reconstruct_objects()).
[B3] K's distribution is ANALYTIC (Poisson, dispersion knob -> NB), not
simulated. The live model's own math implies K ~ Binomial(batters_faced,
k_prob) with batters_faced FIXED (= expected_innings * 4.2, a constant) —
so there is no overdispersion to capture without also modeling
batters-faced VARIANCE, which is B4's job (role-aware expected_innings), not
B3's. Mean is pinned to the existing point estimate by construction
(additive; verified in check_b3_offline.py #10), not fit or corrected.
pitcher_k_dispersion config knob defaults to 0.0 (Poisson limit); reserved
for B4, leave at 0.0 during collection.
[B3] p_ge_threshold is keyed by the INTEGER count threshold as a float —
float(ceil(line)): {5.0, 6.0, 7.0} for lines 4.5/5.5/6.5 — matching the
hitter convention, NOT by the half-integer betting line. Decided explicitly
(considered keying by the line itself for "market-language" readability, but
one lookup convention across all categories won for the C3 edge engine,
which will need ceil(line) for hitters regardless).
[B4] model_version forking is a TWO-PART change, not one. model_version
hashes an ALLOWLIST (MODEL_CONFIG_KEYS in src/utils/model_version.py), NOT
the whole config dict — correct the earlier "pure function of the config
DICT only" phrasing to "pure function of the ALLOWLISTED SUBSET of config."
For enabling B4 to fork the hash, BOTH must be true: (a) "role_innings" is
in MODEL_CONFIG_KEYS (done, this session, shipped in the offline commit —
inert until a block exists), and (b) a top-level "role_innings" block exists
in config.json. Neither alone changes the version the way B4 needs. This is
the deliberate INVERSE of B3, which used a .get(...,0.0) fallback to AVOID
moving the hash. The offline harness's check 2a fails loudly if the
allowlist entry is ever dropped.

[B4] The role_innings block stays OUT of config.json until the gated flip.
Adding the block forks the hash even with enabled:false (the whole block is
hashed), which would create a new model_version whose BEHAVIOR is identical
to dab23f4fbac8 — exactly the "silently mixing behavior across a change"
that the version stamp exists to prevent, inverted (two tags, one behavior).
That fragments forward-pair provenance (C1 filters on model_version) for no
behavioral reason, and decouples the fork from the event it is meant to mark
("B4 went live" vs "someone edited config"). So: code ships now, config.json
is edited only at the gate, where the fork coincides with the real behavior
change, exactly once. Rejected option: conditionally excluding the block
from the hash until enabled — that re-creates B3's fallback pattern inside
the versioning layer and decouples hash from config state, worse than the
problem it solves.

[B4] Role is detected from a MEASURED signal, not a hand-picked guess. Added
an UNFLOORED `games` (total appearances) field to PitchingStatsSnapshot,
populated from the same gamesPlayed the hitter parser already reads (no new
API call). start_ratio = games_started / games. The pre-B4 heuristic floored
games_started to >=1, which MANUFACTURED a phantom start for pure relievers/
openers and reported starter-length outings — a direct source of the
short-outing K over-projection. games_started stays floored (no existing
read changes); `games` is the new honest denominator. Thin samples
(games < min_games_for_role) are FLAGGED role="unknown" and fall back to the
starter path, not assigned an invented role.

===============================================================================
Provenance / versioning (three stamps — filter by these, never mix)

model_version : config hash on every prediction->outcome pair (A1). Current
= dab23f4fbac8. Pre-2026-07-09 CI pairs are hrr-only + version-blank —
EXCLUDE them in calibration/C1.
builder_schema="a3.1" : stamped on every A3 training row.
roller_schema="a4.1"  : stamped on every A4-enriched row (hitters only).
B1's loader asserts builder_schema on both frames and roller_schema on the
HITTER frame only, and refuses a stamped-but-columnless frame.

===============================================================================
The discipline (most important; promotion gates)

Live model FROZEN during collection. Additive/read-only work is fine.
(All A1-A4, B1, B2, gate-4b, and B3 work was additive; the simulator is
live. B4 will NOT be additive — it's the first live-model change since the
freeze and must be gated on its own evidence, not slipped in.)
Corrections OFF during collection (apply_corrections=false).
No betting until calibrated. A projection alone is never a play — bet the
GAP vs a de-vigged market line only.
MODEL PROMOTION GATE (#4): never replaces live unless it
(a) beats current in walk-forward compare_reports on held-out dates
[CatBoost: DONE, via matched reanalysis];
(b) equal/better calibration
[DONE, hitters + K, via gate #4(b), 2026-07-10 (K same day, right after B3)];
(c) >=1 week shadow with logged predictions [C2's job].
Evidence, then promotion. Gate #4 is NOT fully closed: shadow (C2) remains.
A #4(b) PASS (now all four categories) is one leg, not clearance on its own.
DATA PROVENANCE: keep tagging model_version on every pair.

WHEN TO GO LIVE (settled; don't relitigate): shadow-live as early as C2
(predicting + logging, zero money); real money ONLY after C4 shows positive
CLV over a meaningful sample. Never at Phase B. If C4 is flat: stay in
shadow, hunt softer markets.

===============================================================================
MASTER ROADMAP

Phase A — collection-safe / additive
[x] A1 DONE. All-category automation (run_slate.py) + real outcome recording
+ model_version tag, live in CI.
[x] A2 DONE. run_reconstruct_date.py + reconstruct_objects() (B1/gate seam).
[x] A3 DONE. Resumable 2023-2025 builder; real run complete.
[x] A4 DONE. Rolling Statcast enrichment; real 2023-2025 enrich complete.
[~] A5 CODE DONE (own chat). Odds confirmed + read-only line logger built and
offline-tested. LEFT: start daily logging habit + one live --dry-run check
that HR/K populate on DK; rotate API key; gitignore data/lines/. See A5
STATUS section. Blocks nothing.
[ ] A6. Analysis tooling (read-only): calibration plots, K-error vs actual_ip
split, confidence-vs-accuracy.

Phase B — offline; zero live-model contact
[x] B1 DONE. CatBoost per-category models beat the simulator on matched
out-of-sample walk-forward rows. LightGBM eliminated. Files:
src/learning/gbm_dataset.py, gbm_trainer.py, gbm_benchmark.py;
run_train_gbm.py, run_assemble_enriched.py, run_benchmark_gbm.py,
run_walkforward_gbm.py, run_wf_reanalyze.py; scripts/check_gbm_offline.py
(18/18). Models: data/models/gbm/gbm_<cat>_catboost.cbm.
B2 input: data/models/gbm/wf_predictions_catboost.csv.
[x] B2 DONE (2026-07-09/10). Count-model calibration; deployed_stage2=identity
all cats; HR mean_scale=0.907; holdout ECE < 0.028. Files:
src/learning/gbm_calibrator.py, run_calibrate_gbm.py,
run_calibration_gate.py, scripts/check_calibration_offline.py (26/26).
Artifact: data/models/gbm/calibration/calibrators.json (gitignored,
regenerable).
[x] GATE #4(b) — calibration — FULLY CLOSED (2026-07-10; hitters, then K
same day right after B3). GBM equal-or-better than sim on matched rows
(17,137 matched rows [gbm=17,223, sim=18,429], 15 dates, seed 17, 1 thin
date kept). Block-bootstrap CI over dates: hits 0.5/1.5, hrr 1.5,
strikeouts 5.5/6.5 = GBM (significant); home_runs 0.5, hrr 2.5, strikeouts
4.5 = TIE (n.s. — CIs cross zero; HR/hrr-2.5 at the Murphy noise floor, but
strikeouts is NOT — K's Murphy skill ~0.02-0.04 shows real resolution). Sim
significantly better on ZERO of eight lines -> PASS. Files:
run_gate_reconstruct.py, run_calibration_gate.py. Artifacts (gitignored,
regenerable): data/models/gbm/calibration/{sim_probs.csv, gate_metrics.csv,
gate_reliability.png}. CAVEAT: K legs are n=349/line, 14 dates — thin; see
B3 entry below and Known Issues.

[x] B3 DONE (2026-07-10). Distributional pitcher K: analytic Poisson wrap
(dispersion knob -> NB, default 0.0 = Poisson limit), mean pinned to the
EXISTING point estimate by construction — ADDITIVE, projected_value
unchanged, model_version not forked. p_ge_threshold keyed by integer count
threshold float(ceil(line)) = {5.0, 6.0, 7.0} for lines 4.5/5.5/6.5 —
matches the hitter/gate convention, so run_gate_reconstruct.py needed only a
STANDARD_LINES entry, zero lookup-logic changes. See the two new [B3] key
decisions above for the reasoning (why analytic not simulated; why
integer-keyed not line-keyed). Files: src/prediction/prop_engine.py,
scripts/check_b3_offline.py (13/13 offline), run_gate_reconstruct.py
(STANDARD_LINES + strikeouts). Commit e670fb6. Closes the K half of gate
#4(b) above. Genuine batters-faced variance (short-outing bias) is
deliberately NOT modeled here — that's B4's role-aware expected_innings,
a gated live-model change, not this additive wrap.

[~] B4 OFFLINE BUILD DONE (2026-07-10); GATE OPEN. Role-aware
expected_innings. Fixes short-outing K over-projection in the SIMULATOR (B1
evidence; the GBM already sidesteps it). LIVE-MODEL CHANGE -> forks
model_version (two-part: allowlist entry [shipped] + config block [at gate];
see [B4] key decisions). Built + offline-validated only; NOT promoted, live
model unchanged. Files: src/prediction/role_innings.py (new,
RoleAwareInningsEstimator), scripts/check_b4_offline.py (new, 24/24),
config/role_innings.example.json (new, reference — enabled:false shape),
src/data/mlb_api.py (games field + estimator wiring + _estimate_expected_ip
delegates), src/prediction/daily_predictor.py (one line: passes config to
MLBStatsAPI, inert while disabled), src/utils/model_version.py
(role_innings added to MODEL_CONFIG_KEYS). Commit <fill in>.
LEFT (its own chat, at the gate): fit the placeholder role->innings numbers
(opener 1.5 / bulk 3.5 / ratio cuts 0.20,0.80 / clamp 4.0-7.0 are
STRUCTURALLY sound but UNVALIDATED) against the simulator's K error vs
actual_ip (A6 tooling); add enabled:true role_innings block to config.json
(forks the hash here); run_gate_reconstruct.py -> block-bootstrap-over-dates
that role-aware K calibrates equal-or-better than the frozen sim without
breaking normal starters.

Phase C — post-collection, evidence-based live changes (gated by #4)
[ ] C1. calibration_report on forward-collected pairs. FILTER OUT
pre-2026-07-09 hrr-only/version-blank CI pairs.
[ ] C2. Promote CatBoost through the gate (walk-forward: done; calibration:
hitters done, K pending B3; shadow >=1 week: here). Enable corrections.
[ ] C3. Wire live odds -> edge engine (needs B2's calibrated P(over) + A5's
line log). Markets: hits, home_runs, strikeouts. HRR has NO market
(validation-only; see A5 STATUS).
[ ] C4. Shadow betting log graded vs CLOSING lines (CLV). Filter lines log to
clv_eligible=1.

Phase D — hardening / full automation
[ ] D1. Actions end-to-end: daily predict -> record outcomes -> line logger
-> weekly calibration -> monthly walk-forward -> failure alerts.
[ ] D2. Bullpen-transition modeling for hitters — only after C proves core.

===============================================================================
B3 KICKOFF — distributional pitcher K [RESOLVED 2026-07-10 — see CURRENT
STATUS, the [B3] key decisions, and the roadmap checkbox above for the
outcome. Original design-question text kept below for the "why," per this
doc's own "do not relitigate" philosophy — the DESIGN QUESTION was settled
as (a) PARAMETRIC/analytic, and p_ge_threshold ended up integer-keyed, not
line-keyed; see [B3] above for both calls.]

GOAL: give the SIMULATOR a real strikeout distribution so it can emit
P(K >= line), which (1) unblocks the K column of gate #4(b) and (2) removes
the "K point estimate, no distribution" live-path weakness.

CURRENT STATE (prop_engine.project_pitcher_strikeouts): returns
PropProjection(..., simulation=None). The point estimate is
projected_k = rates["k_prob"] * (expected_innings * 4.2)
i.e. per-PA K prob x batters faced. No distribution is attached.

DESIGN QUESTION TO SETTLE FIRST:
(a) PARAMETRIC: wrap the point estimate in a negative-binomial (K counts are
overdispersed vs Poisson; B2 found K NB a~0.01 on GBM residuals but the
SIM's own dispersion must be measured, not assumed). P(K>=line) from the
NB survival function. Cheap, closed-form, no new simulation.
(b) SIMULATED: actually simulate batters-faced x per-PA K outcome (mirrors
the hitter Monte Carlo path that already fills MonteCarloResult.
p_ge_threshold), producing an empirical p_ge_threshold for K. More
consistent with the hitter path; heavier.
Mirror how project_hitter builds MonteCarloResult (monte_carlo.py) so the K
distribution lands in the SAME p_ge_threshold shape the gate harness reads.

SCOPE / DISCIPLINE (decide explicitly):


If B3 ONLY adds a distribution and leaves the shipped point value
unchanged, it is ADDITIVE and safe during collection.
If B3 changes the K point value, it is a LIVE-MODEL change -> forks
model_version, gate it like B4, do not slip it in mid-collection.
Default recommendation: additive first (attach distribution around the
existing point estimate), measure, and only later consider changing the
point value under B4's role-aware expected_innings.


VALIDATION: once simulation != None for pitchers, re-run
python run_calibration_gate.py compare 
--gbm data/models/gbm/calibration/gbm_deployed_probs.csv 
--sim data/models/gbm/calibration/sim_probs.csv
after regenerating sim_probs.csv with a reconstruction that now includes K
rows (run_gate_reconstruct.py currently emits hitters only; add K once the
projection carries p_ge_threshold). K lines: 4.5/5.5/6.5.

FILES LIKELY NEEDED IN THE B3 CHAT: src/prediction/prop_engine.py,
src/simulation/monte_carlo.py (how p_ge_threshold is built for hitters),
src/simulation/pa_simulator.py (per-PA K prob), src/models/dataclasses.py
(MonteCarloResult / PropProjection — already known: p_ge_threshold is
dict[float,float]).

===============================================================================
DATA COLLECTION NOTES (honest guidance — read before spinning up big runs)

Two tempting-but-premature ideas came up; both are "no, not yet," with reasons:


"Run run_gate_reconstruct.py with --n-dates 100+ for a season-long gate."
NOT worth it right now. The gate already PASSED with a clear, significant
margin on 15 dates / 17,137 matched rows (all four categories, since B3
closed) — the hits/hrr/strikeouts-5.5/6.5 CIs are nowhere near zero, and the
ties (HR, hrr 2.5, strikeouts 4.5) either sit at the Murphy NOISE FLOOR (HR,
hrr 2.5) or just have a CI crossing zero on a thin K sample (strikeouts
4.5) — neither case is fixed by more dates alone. More dates would tighten
CIs we don't need tightened and would NOT change any verdict. Cost is real
(~2.5 min/date => 100 dates ~4+ hours, plus MLB API load and the
overnight-dropout risk that already bit one date). Rule: collect data to
CHANGE a decision, not to decorate one already made. IF you ever want a
season-long gate for the record, B3 being done means one reconstruction
pass already covers all four categories — still not worth it now per the
reasoning above; nothing currently pending needs it. (The K sample thinness
is a real reason to want MORE K-specific data eventually — but that comes
from the forward-collection habit below, not a synthetic backward
season-long gate re-run.)
"Collect 2026 regular + Statcast now, just to have it."
HALF right — worth doing, but sequence it, and NOT as a background job
competing with the current main line (B4). The roadmap already earmarks
2026 as a HELD-OUT validation season for the calibrated CatBoost (cleanest
generalization test) BEFORE folding any 2026 into training. But:

2026 is IN PROGRESS. A mid-season pull is a moving target; the clean
held-out test wants a defined window, so pin a 2026 window in
SEASON_WINDOWS (training_set_builder.py) rather than "everything so far".
It is a full A3+A4-style build (resumable; offline harness -> smoke ->
full -> commit), which deserves its own chat and attention, not a
fire-and-forget background run.
It changes NO current result and unblocks nothing on the critical path
(B4). So it is genuinely optional-parallel, best done in a dedicated
session, not ahead of the main line.
The ONE 2026 data thing that IS time-sensitive and should start ASAP is
FORWARD collection you cannot backfill: run_slate.py daily (forward pairs
for C1) and A5's line logger daily (CLV is forward-only; every un-logged
day is lost). Those accrue the data that genuinely can't be reconstructed
later. Historical 2026 game/Statcast rows CAN be pulled later; closing
lines and forward prediction pairs CANNOT.





Bottom line on data: prioritize the data that is (a) decision-changing and
(b) non-reconstructable. Right now that is forward pairs + closing lines
(daily habit), not more gate reconstruction and not a speculative 2026 bulk
pull. Trust the discipline: the gate is closed on evidence; don't re-open a
settled question just because more data is collectable.

===============================================================================
A5 STATUS  (code DONE; daily logging habit + one live check left)

[~] A5 IN PROGRESS (code DONE, daily logging + one live validation left).
Odds sourcing CONFIRMED + read-only line logger BUILT and offline-tested.

PROBE RESULT (probe_odds_apis.py, 2026-07-10): The Odds API free tier
(500 req/mo) returns MLB player props on DraftKings (batter_hits confirmed
live). The /events call is free; 3 per-event odds calls (3 markets, 1 region,
1 book) cost ~1 credit total — one full-slate pull is cheap; 500/mo ~= 2
months runway. SharpAPI not probed (no stable free MLB prop endpoint).
$30/mo 20k tier would end cadence worries; DECISION: do NOT upgrade until the
logger is proven accurate on the free tier (evidence, then spend).

MARKET REALITY (settled): three markets map to model categories —
batter_hits->hits, batter_home_runs->home_runs, pitcher_strikeouts->
strikeouts. HRR HAS NO MARKET (books sell hits / runs / RBIs separately) ->
HRR is VALIDATION-ONLY / model-internal, nothing clean to bet or grade
against. RBIs/runs not pulled (add batter_rbis / batter_runs_scored to
odds.odds_api.markets only if wanted).

LINE LOGGER (run_log_lines.py) — standalone, read-only/additive (GET only;
sole write is data/lines/lines_YYYY-MM-DD.csv), reuses config odds block.
TWO-TRACK CLV baked into the data: every row carries snapshot_type
(open|midday|close|backfill) + clv_eligible (1 ONLY for close). Staggered MLB
start times => a blanket "close" pass mislabels early games; --commence-within
MIN selects near-first-pitch games, and the tool WARNS on --snapshot close
without a window. Default --snapshot midday. Offline harness passed.

New files (repo root): probe_odds_apis.py (disposable probe), run_log_lines.py.

LEFT TO DO (habit, not code):


First live game-day run: --dry-run, confirm batter_home_runs and
pitcher_strikeouts populate on DK (probe only printed hits; books post
HR/K closer to first pitch). Then a real write; spot-check the CSV.
gitignore data/lines/; stage scripts with an EXPLICIT file list.
ROTATE the ODDS_API_KEY (it was pasted in chat during setup).
START DAILY LOGGING when ready — running it daily starts the CLV clock;
the logger merely existing does not. Every un-logged day is lost.


NOTE FOR C4: CLV grades CLOSING lines only. Filter to clv_eligible=1.

===============================================================================
Realistic expectations (read before getting excited)

~130k rows made the GBM viable; the A4 rolling Statcast features are where
the signal is. CONFIRMED: CatBoost beats the simulator on matched
out-of-sample MAE and calibrates equal-or-better on hitter P(over) — real,
provable steps — but the edges are SMALL (weighted -0.018) and "beats my
simulator" != "beats the market." Books price with sharp flow; the path is
calibrated P(over) + selective betting on soft spots, measured by CLV. The
evaluation discipline (gates, shadow, CLV) is the actual moat — and it proved
its worth THREE times now: (1) the raw walk-forward aggregate said "sim wins"
and matched reanalysis showed why that was wrong; (2) the gate's first
fixed-epsilon verdict said "FAIL" on sub-0.001 noise and the block-bootstrap
showed it was actually a PASS; (3) HR shows as a TIE at the noise floor rather
than a fake win for either side. Trust the discipline, not headline numbers.

===============================================================================
Known issues / weaknesses (confirm with data, then fix)

Short-outing K over-projection in the SIMULATOR. B4 fix is BUILT + offline-
validated (24/24) but UNGATED — not live yet; the bias is still present in
the live model until B4 clears its gate. B1 corroborates. B3's Poisson/NB
wrap INHERITS this bias by design (mean pinned to the existing, biased point
estimate) — expected and correct for an additive wrap; B4 is where this
actually gets fixed. The B4 role->innings CONSTANTS are unvalidated
placeholders pending the K-error-vs-actual_ip fit at the gate.
Confidence flat tiers (~0.62/0.72), unvalidated. GBM projections carry
confidence=0.0 on purpose; B2 owns calibrated confidence.
HR props: rare event, heavy juice; neither model wins HR (MAE ~+0.008;
gate Brier tie at the Murphy noise floor). Do not expect an HR edge.
A4 rolling features blank for low-BiP rows by design; loader coerces ""->NaN.
Strikeouts out-of-sample sample is thin (~349-541 rows) -> K calibrator
rests on 351 rows; gate #4(b) K legs are the same ~349 rows / 14 dates —
re-fit/re-verify as forward K pairs accrue before leaning on K for real
money (C4/CLV is where that actually gets earned).
run_calibration_gate.py's footer string still prints "K deferred to B3" —
hardcoded from the hitter-only era, now STALE (K rows emit above it and the
gate is fully closed). Cosmetic; fix the string next time that file is
touched.
Gate verdict column ranks by POINT dBrier, not by significance — a line
whose CI crosses zero still prints its point-winner (e.g. strikeouts 4.5
shows "GBM" in v7's table above but is a TIE, n.s.). Always read the CI, not
the verdict column alone — the same "headline vs evidence" lesson the
fixed-epsilon trap taught, in a new spot.
DOUBLE CI RUN: 2026-07-09 shows TWO CI prediction commits (14:10 and 21:19
UTC) for the same date. Understand why (duplicate cron? manual dispatch?)
before it becomes a pattern.

===============================================================================
Operational gotchas (learned the hard way — save yourself the pain)

GIT: stage EXPLICIT file lists; NEVER git add -A. ALWAYS
git pull --rebase origin main before push. PowerShell > writes
UTF-16-w/-BOM — never write git control files with it. During a REBASE,
--theirs means YOUR replayed commit; during a STASH POP conflict, --ours is
HEAD and --theirs is the stash (opposite) — check git diff --staged
before committing a conflict resolution.
COMMIT CODE THE SAME SESSION IT IS VALIDATED. B1 closed with a full
evidence chain while its entire codebase sat UNTRACKED on one machine — one
disk failure from gone. The discipline that catches modeling confounds
applies to git too.
NEVER git reset --hard WITH STAGED-BUT-UNCOMMITTED NEW FILES. reset
--hard deletes new files that were git added but never committed (it forces
the tree to match HEAD, which never had them). If a commit fails mid-conflict
with new files staged, resolve in place or git stash first (stash carries
new files along safely). RECOVERY if it happens: git fsck --full lists
dangling blobs; git cat-file -p <sha> previews each; restore with
cmd /c "git cat-file -p <sha> > path" (NOT PowerShell > — it re-encodes and
can corrupt non-ASCII); verify with git hash-object <file> == blob sha.
Dangling blobs survive ~2 weeks; do not git gc before recovering.
LOCAL + CI SAME-DAY predictions collide on data/learning/predictions/
predictions_<date>.json. Both runs regenerate the whole slate -> conflict on
every player key. Resolve by keeping ONE run wholesale (checkout --ours/
--theirs), not by merging fragments (that leaves duplicate (player,cat,date)
keys). Better: block local run_slate on CI days, or namespace output by run
source.
DATA IS GITIGNORED: data/training/<season>/ shards, training_.csv.gz,
data/cache/, data/models/gbm/.cbm, data/models/gbm/calibration/, and
(add) data/lines/ are ignored; only manifest_.json is tracked. Calibrator


gate artifacts are all regenerable from the pairs CSV + reconstruction.
pycache/, .pyc, catboost_info/ are gitignored (a tracked .pyc caused
phantom "modified" noise and tripped a rebase). RECURRED 2026-07-10 on
src/prediction/__pycache__/*.pyc — those .pyc files were tracked BEFORE the
gitignore rule existed, and gitignore only stops NEW files from being
tracked, it does NOT retroactively untrack existing ones. Fixed with
git rm -r --cached src/prediction/__pycache__ (removes from tracking,
keeps the file on disk). If a rebase ever complains about an unstaged .pyc
again, this is almost certainly why — check for other already-tracked
bytecode with git ls-files | Select-String ".pyc" before assuming it's
something new.
WINDOWS FILE LOCKS: close CSVs in Excel before re-enriching.
pybaseball MUST be installed for A4 AND the live model's Statcast path.
ASSEMBLY: use run_assemble_enriched.py (NOT TrainingSetBuilder.assemble —
double-counts raw+statcast). Row counts must equal 133,344 / 14,816.
RECONSTRUCTION COST: ~2.5 min/date (bundle build, not cached away) + ~3s
Statcast (cached). Overnight runs risk a transient MLB API dropout (one gate
date failed this way and was flagged/dropped, not fatal — the wrapper
continues per-date). Walk-forward disk-caches sim reconstructions
(data/cache/wf_simulator/) — re-runs and reanalysis are free.
MATCHED SCORING: any model-vs-model comparison must inner-join on
(player_id, game_date, category[, line]) first. The reconstruction path
includes spring-training/international-opener games A3 excludes — unmatched
scoring silently compares different games.
GATE VERDICTS: judge significance with a block bootstrap OVER DATES, never a
fixed epsilon (rows within a slate are correlated; a sub-0.001 Brier gap is
noise). Report Murphy skill so noise-floor categories (HR) read as ties.
SEASON-BLIND FOLDS / SAMPLED DATES: evenly-spaced or weighted sampling can
land on Seoul/Tokyo openers (near-empty as-of features). Tag/keep-but-flag
thin dates (min-rows); the block bootstrap weights them naturally.
PowerShell env: $env:PYTHONPATH="." per session (bash syntax fails).
Config is config/config.json. --config OPTIONAL for the 4 gated cats.
Python 3.14 + catboost 1.2.10 works (cp314 wheel).
run.py entry points live at REPO ROOT (they import run_reconstruct_date /
src.* as top-level modules). Library code (gbm_calibrator) lives in
src/learning/; the runners use a try/except import shim so they work whether
co-located or split.
Smoke-test discipline pays: 1-date/1-fold smoke runs before every big run
caught real issues cheaply. Offline harness -> smoke -> full run -> commit.

MODEL_VERSION IS AN ALLOWLIST, NOT THE WHOLE CONFIG. model_version()
hashes only MODEL_CONFIG_KEYS (src/utils/model_version.py). A new
coefficient block added to config.json is INVISIBLE to the version until its
key is added to that allowlist — a live-model change could otherwise ship
with an UNCHANGED provenance stamp (silent corruption). When adding any
projection-shaping config block: add its key to MODEL_CONFIG_KEYS in the
SAME change, keep the block TOP-LEVEL (the allowlist reads config[k] at top
level; nested = invisible), and remember the block forks the hash the moment
it exists, enabled or not. B4's offline harness check 2a guards the allowlist
entry; nothing guards top-level placement except this note.


===============================================================================
How to work cost-efficiently with Claude

FRESH chat per roadmap item and recommend which level of claude to use if code or how is fit; upload this file (+ needed source files).
Name the item: "Build B3" / "A5 live check" — the file gives context.
Heavier model for hard builds/debugging; lighter for small questions.
Paste only the last ~15 lines of errors. Batch related questions.
Update this file after each session (status, checkboxes, new decisions).
Validate offline harness FIRST, then real smoke test (1 date / 1 fold),
then the full run, then commit. Commit each roadmap item separately.















--------------------------------------------------------------------------------------
-----------------------IGNORE----FOR ME----WHEN TO START USING LIVE----
--------------------------------------------------------------------------------------
Good question to ask now, before you're tempted by an early model. The clear answer from your own roadmap: not until Phase C, and specifically not until C4 shows positive CLV. Let me explain why each earlier stopping point is a trap.
Not after Phase B. B gives you a trained, calibrated model that beats your simulator on held-out data. That feels like "done," but remember the core truth: beating your simulator ≠ beating the market. A B-stage model has never once been tested against real closing lines. Turning it live here means betting real money on a model whose market edge is completely unmeasured. This is the most dangerous moment precisely because the model looks impressive.
The actual sequence to "live," gated by your own discipline #4:
Phase B builds the model. Then Phase C is where it earns the right to touch money, in order:

C1 — confirm on real forward-collected pairs that the model calibrates well (and that known biases like the short-outing K issue are actually fixed).
C2 — promote the model through the gate: it must beat the simulator in walk-forward, calibrate equal-or-better, and run in shadow mode ≥1 week with logged predictions. Shadow = it predicts alongside live but bets nothing.
C3 — wire in real odds so the edge engine compares your P(over) against de-vigged market lines. Now you can see edges, still betting nothing.
C4 — log shadow bets graded against closing lines (CLV). This is the real test. If your picks consistently beat the closing line, you have genuine edge. If they don't, you don't — and you've lost zero dollars finding out.

So the honest answer: you go live only after C4 shows positive CLV over a meaningful sample. That's not me being conservative — it's your own roadmap ("CLV — the real test of market-beating before any money moves") and discipline #3 ("No betting until calibrated. A projection alone is never a play").
Two things worth saying plainly:
There's a real distinction between "live" meanings. Running the model in shadow (predicting, logging, measuring CLV, zero money) can and should happen as early as C2–C4 — that's how you gather the evidence. Turning on real money is the thing that waits for C4 to prove out. Don't conflate them; the whole point of shadow is to be "live" in every way except the financial risk.
And a gut-check on expectations, since your context file is right about this: it's entirely possible C4 shows your model doesn't beat closing lines, even with a great GBM. That's not failure — that's the system working. The evaluation discipline exists so you find that out through logged CLV, not through a drained bankroll. If C4 is flat, the answer is "keep it in shadow, hunt softer markets, don't bet," not "go live anyway because the model looked good in B."
Also — and this is separate from model-goes-live — A5 matters for timing. You need to start logging closing lines now-ish, because every day without stored closing lines is market data you can never recover for CLV measurement later. So even though betting waits for C4, the line-logging infrastructure (A5) should go in early. That's why it's in Phase A, not Phase C.
Short version: shadow-live as early as C2, real-money-live only after C4 proves positive CLV. Never at Phase B, no matter how good the model looks.

