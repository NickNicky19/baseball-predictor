PROJECT_CONTEXT.md — v4 (master roadmap) — paste into any new chat

Keep this file in the repo. Start a FRESH chat per task, upload this (plus the
repo/zip if code work is needed), and say which roadmap item to work on.
Update "Current status" and check off roadmap items as they complete.

===============================================================================
CURRENT STATUS (2026-07-09, evening)
===============================================================================
Phase A machinery COMPLETE and the real 2023-2025 data run is DONE:
- A3 build: 133,344 hitter rows + 14,816 pitcher rows across 2023/24/25
  (per-season ~44.5k hitters / ~4.9k pitchers, 205 dates each).
- A4 enrich: all three seasons enriched, roller_schema=a4.1 stamped, 26
  roll15/roll30 cols. Spot-checked on real data (2024-08-25): ~99% fill on
  mid-season dates, means match MLB baselines (xwoba 0.366, EV 87.9, barrel
  7.6%, hardhit 38%, whiff 23.8%), blank-by-design confirmed (bip<5 -> NaN).
- Assembled enriched set (NEW step, run_assemble_enriched.py):
    data/training/training_hitters_2023_2025_statcast.csv.gz  (133,344 rows)
    data/training/training_pitchers_2023_2025.csv.gz          (14,816 rows)

B1 TRAINED + BENCHMARKED (first real GBM result):
- 4 gated CatBoost models trained (hits/hrr/home_runs from hitters,
  strikeouts from pitchers). Temporal split at 2025-06-07 (train 104,451 /
  holdout 26,764 hitters; pitchers 11,092 / 2,945). Models in data/models/gbm/.
- Simulator benchmark via A2 reconstruct_objects + compare_reports, 20
  held-out dates (Jun-Sep 2025), 14,834 outcome rows:
      category     sim MAE   gbm MAE    delta   winner
      hits          0.7059    0.6819   -0.0240  GBM
      home_runs     0.2032    0.2110   +0.0078  sim (noise; HR mean ~0.12)
      hrr           1.5111    1.4803   -0.0308  GBM
      strikeouts    1.9269    1.8682   -0.0587  GBM
      combined      1.2357    1.2052   -0.0305  GBM
  Read: GBM beats simulator on the 3 signal categories, strongest on
  strikeouts (matches the predicted short-outing K bias). HR delta is noise.
  Edges are SMALL and this is ONE holdout window scored on MAE only — not
  calibration, not the promotion gate.
- Walk-forward smoke test (1 fold, 3-day window, 2024) PASSED: catboost
  combined 1.1849 vs sim 1.2406 (K delta -0.139); lightgbm 1.2120 (K delta
  +0.008 — its whole deficit is strikeouts, where CatBoost's ordered target
  encoding on the pitcher categoricals appears to matter). Both models' fold
  predictions saved to wf_predictions_<model>.csv for B2.

WALK-FORWARD FULL RUN (5 folds x 7-day windows): LAUNCHED, ~90 min. Result
pending — PASTE IT HERE when it lands (per-fold consistency + aggregate +
winner). B2 calibrates the winner on wf_predictions_<winner>.csv.

NEXT ACTIONS:
1. (main line) Walk-forward result -> record here -> B2 calibration fit on the
   saved fold predictions. Do NOT promote on MAE alone (gate #4).
2. (parallel, fresh chat) A5 odds sourcing + read-only line logger — see the
   A5 KICKOFF section below. Blocks nothing; starts the CLV clock.
3. (later, deliberate) 2026 data: keep 2026 as a HELD-OUT validation season
   for the picked+calibrated model first (cleanest generalization test);
   only consider folding partial-2026 into training as a separate experiment
   afterward. Requires adding a 2026 window to SEASON_WINDOWS in
   training_set_builder.py. For "predict today," that's run_slate.py (live
   path), not the training builder.

===============================================================================
What this project is
===============================================================================
MLB player-prop prediction system (hits, HR, HRR, pitcher strikeouts).
Goals: accuracy, backtestability, self-improvement, modularity, minimal
hand-picked coefficients, real Statcast features, calibrated probabilities.
Endgame: learned per-PA models (CatBoost/LightGBM) trained on multi-season
point-in-time data, calibrated P(over) vs live market lines, fully automated.
Repo: C:\Projects\baseball_predictor. GUI via python main.py; CLI via
run_*.py; automation via GitHub Actions.

===============================================================================
Architecture (layers)
===============================================================================
Data (src/data/: mlb_api, savant, point_in_time, http_cache[A3],
statcast_roller[A4]) -> Features (src/features/) -> Simulation
(src/simulation/: pa_simulator -> game_simulator -> monte_carlo) ->
Prediction (src/prediction/: prop_engine, edge_calculator, daily_predictor,
shadow_logger) -> Evaluation (src/evaluation/: calibration_report,
backtest_engine, walk_forward_validator, output_safeguards) -> Learning
(src/learning/: outcome_recorder, retrain_runner, training_set_builder[A3],
gbm_dataset[B1], gbm_trainer[B1], gbm_benchmark[B1]).

run_*.py entry points:
  run_slate.py               A1  all-category predict (CI + manual)
  run_reconstruct_date.py    A2  one historical date -> resolution report;
                                 + reconstruct_objects() (B1 benchmark seam)
  run_build_training_set.py  A3  2023-2025 training-set builder (resumable)
  run_audit_player.py        A3  as-of snapshot vs raw game log (vs Bball-Ref)
  run_build_statcast_features.py  A4  join rolling Statcast onto A3 rows
  run_assemble_enriched.py   B1  assemble _statcast shards -> training gz
  run_train_gbm.py           B1  train per-category GBMs (temporal split)
  run_benchmark_gbm.py       B1  GBM-vs-simulator on sampled holdout dates
  run_walkforward_gbm.py     B1  5-fold walk-forward, both models, saves
                                 out-of-sample predictions for B2

===============================================================================
How the model works (one paragraph) — UNCHANGED, still frozen
===============================================================================
Per PA: logits for K and BB, then HR-on-contact, then hit type. Hit rate on
balls in play is driven by the hitter's xBA (shrunk to a contact-conditional
league baseline); power/speed shape only the extra-base mix. Opposing pitcher
enters as a modifier: K coef 1.10 (strong), BB 0.90, HR 0.20 + HR/9 skill
term; pitcher never touches hit-on-contact rate (deliberate). Game totals via
Monte Carlo over PAs. Runs/RBI via base-state model. Pitcher K = per-PA rate
x (expected_innings x 4.2) — a POINT ESTIMATE (simulation=null), no
distribution. Model math was NOT touched by any A1-A4 or B1 work.

===============================================================================
Key decisions & why (do not relitigate)
===============================================================================
- Statcast xBA/xSLG/xwOBA centered on CONTACT-CONDITIONAL baselines (season
  baselines gave phantom power). Baselines self-calibrate from live pulls.
- xBA drives hit rate directly (old latent-weight arithmetic triple-counted
  contact; deleted ~5 coefficients).
- Feature scales range-normalized so latents sit near +/-1.
- Runs/RBI decoupled from hits; HRR cap derives from league HRR rate.
- Edge engine de-vigs both sides, uses true MC distribution, quarter-Kelly
  capped 5%, ranks by Kelly.
- GUI (2026-07-08): ONE "Run all" = ONE predict() call for
  hits/hrr/home_runs + pitcher K -> tabbed display -> one archive/day.
- outcome_recorder: pairs CSV records actual_pa/hits/hr/runs/rbi/bb/k
  (hitters) and actual_ip/k/bb_allowed/hr_allowed (pitchers); auto-migrates
  legacy CSVs. actual_ip is the training signal for the future IP model.
- [A1] Automation now mirrors the GUI: run_slate.py = one predict() for all
  categories; the rich JSON archive (with simulation blocks incl.
  p_ge_threshold) is committed and later graded. model_version (config hash)
  tags every pair. CURRENT model_version = dab23f4fbac8.
- [A3] Training rows are RAW as-of stats, not the engineered FeatureVector,
  on purpose: GBMs learn from signals, the simulator stays the baseline.
- [A4] Rolling windows are GAME-based (last 15/30 games), contact-conditional
  aggregation matching SavantClient so features and live profiles share one
  ruler. Barrel/hard-hit derived from raw feed (launch_speed_angle==6; EV>=95)
  because statcast_batter has no precomputed columns.
- [B1] CatBoost is the code-DEFAULT (native NaN + ordered target encoding for
  the real categoricals: opp SP context, venue, umpire, team — ordered
  encoding is leakage-resistant, matching project discipline). LightGBM behind
  --model lightgbm. WHICH model gets promoted is decided by walk-forward +
  calibration (gate #4), NOT by the code default. Both emit PropProjection
  with the exact simulator PropCategory labels.
- [B1] Fantasy is TRAINABLE but UNGATED: A2's HITTER_CATEGORIES has no fantasy
  baseline, so a fantasy GBM has nothing to beat -> excluded from the gate to
  avoid a phantom win. --include-fantasy trains it for later use.
- [B1] compare_reports has a phantom-win hole: a category the candidate never
  predicts still shows candidate_mae=0.0 (looks like its best result). The
  benchmark's assert_comparable() requires the compared set == the expected
  gated set AND non-zero samples on BOTH sides before trusting
  overall_improved.
- [B1] Targets derived to EXACTLY match outcome_recorder.compute_actual_value
  (fantasy weights read from config, not hardcoded) so train == grade.
- [B1] Split is TEMPORAL (train earlier dates, hold out most-recent ~20%),
  never random — random splits leak point-in-time structure and flatter the
  GBM. Walk-forward folds replicate WalkForwardValidator's boundary math
  exactly so all models score on identical windows.
- [B1] Walk-forward reconstructs each fold's simulator baseline ONCE and
  disk-caches it (data/cache/wf_simulator/) — the sim side is the cost
  (~2.5 min/date); both GBMs score against the cached baseline for free, and
  a killed run resumes from cache.
- [B1] Each fold's OUT-OF-SAMPLE GBM predictions are saved to
  data/models/gbm/wf_predictions_<model>.csv — B2 fits calibration on these
  (out-of-sample by construction), with zero re-reconstruction.

===============================================================================
Provenance / versioning (three stamps — filter by these, never mix)
===============================================================================
- model_version : config hash on every prediction->outcome pair (A1). Current
  = dab23f4fbac8. Pre-2026-07-09 CI pairs are hrr-only + version-blank (old
  broken workflow) — EXCLUDE them in calibration/C1.
- builder_schema="a3.1" : stamped on every A3 training row.
- roller_schema="a4.1"  : stamped on every A4-enriched row (hitters only;
  A4 is a hitter batted-ball signal, pitchers are never enriched).
B1's loader asserts builder_schema on both frames and roller_schema on the
HITTER frame only, and refuses a stamped-but-columnless frame.

===============================================================================
Confirmed diagnoses (still valid; updated by A2/A3/B1)
===============================================================================
- expected_innings = _estimate_expected_ip(recent): recent IP/GS clamped
  [4.0, 7.5], fallback 5.5. Role-blind; 4.0 floor inflates openers -> likely
  cause of short-outing K over-projection. Fix in B4, confirm size in C1.
  B1 evidence: GBM's largest MAE edge over the simulator is strikeouts
  (-0.059 on the 20-date benchmark), consistent with the GBM sidestepping
  this bias.
- PointInTimeStats: WAS unwired; NOW WIRED by A2/A3 (run_reconstruct_date.py
  and training_set_builder.py both use it, leakage-safe, canary-tested).
- The end-to-end historical pipeline WAS "does not exist yet"; NOW EXISTS
  (A2 reconstructs one date; A3 builds the multi-season set; A4 enriches;
  B1 trains/benchmarks against the simulator on identical outcomes).
- Pitcher IP precision would NOT materially help hitter projections (hitter
  PAs are slot-driven; no bullpen-transition modeling — that's D2).
- Downstream CSV consumers read columns by name — extra columns are safe
  (confirmed: model_version + detail columns added without breakage).

===============================================================================
The discipline (most important; promotion gates)
===============================================================================
1. Live model FROZEN during collection. Additive/read-only work is fine.
   (All A1-A4 and B1 work was additive; model math untouched.)
2. Corrections OFF during collection (apply_corrections=false).
3. No betting until calibrated. A projection alone is never a play — bet the
   GAP vs a de-vigged market line only.
4. MODEL PROMOTION GATE (GBMs, IP model, any candidate): never replaces live
   unless it (a) beats current in walk-forward compare_reports on held-out
   dates, (b) equal/better calibration, (c) >=1 week shadow with logged
   predictions. Evidence, then promotion. No exceptions.
5. DATA PROVENANCE: model_version tag shipped in A1 (see above). Keep tagging.

WHEN TO GO LIVE (settled; don't relitigate): shadow-live as early as C2
(predicting + logging, zero money); real money ONLY after C4 shows positive
CLV over a meaningful sample. Never at Phase B, no matter how good the model
looks — beating the simulator != beating the market, and a B-stage model has
never been tested against real closing lines. If C4 is flat, the answer is
"stay in shadow, hunt softer markets," not "go live anyway."

===============================================================================
MASTER ROADMAP
===============================================================================

Phase A — collection-safe / additive
[x] A1 DONE. All-category automation (run_slate.py) + real outcome recording
    (OutcomeRecorder) + model_version tag, live in CI. Fixed en route: daily
    workflow was hrr-only; origin's run_record_outcomes.py wrote no real
    outcomes; unanchored gitignore blocked archive commits; corrupt-state
    crash in corrections path; migration only firing on non-empty appends.
[x] A2 DONE. run_reconstruct_date.py: PointInTimeStats -> bundles ->
    evaluate_bundles for one historical date + per-feature resolution table.
    Additive change 2026-07-09: reconstruct_objects() returns the raw
    (simulator PropProjections, OutcomeRecords) for one date — same
    AsOfMLBAPI+FeatureFactory+PropEngine path; it's the seam B1's benchmark
    scores the GBM against. check_reconstruct_offline: 22/23 on a networked
    box (the 1 "fail" is the weather-defaults canary firing because weather
    actually RESOLVED with live network — benign, not a code fault).
[x] A3 DONE. training_set_builder.py + http_cache.py: resumable 2023-2025
    builder, raw as-of rows + outcomes, disk cache + manifest, canary-tested.
    run_audit_player.py cross-checks as-of vs raw log. Real run complete
    (counts in CURRENT STATUS).
[x] A4 DONE. statcast_roller.py + run_build_statcast_features.py: rolling
    xwOBA/EV/barrel/hard-hit/whiff (roll15/roll30), disk-cached, joined onto
    A3 rows. pybaseball in requirements. Real 2023-2025 enrich complete +
    spot-checked (details in CURRENT STATUS).
[ ] A5. Odds: test free-tier keys (The Odds API, SharpAPI) for MLB props. If
    props come through, build a READ-ONLY daily line logger (open/close lines
    + prices next to predictions). Closing lines required for CLV later.
    SEE "A5 KICKOFF" SECTION BELOW — start this in a fresh chat; it's the
    designated parallel task while B-phase runs.
[ ] A6. Analysis tooling (read-only): calibration plots, K-error vs actual_ip
    split (short-outing diagnosis), confidence-vs-accuracy on enriched CSV.

Phase B — offline; zero live-model contact
[~] B1 IN PROGRESS. Train CatBoost/LightGBM per-category models on A3+A4.
    DONE: CatBoost models trained (4 gated cats); beats simulator on 20-date
    MAE benchmark (numbers in CURRENT STATUS); walk-forward smoke test passed.
    RUNNING: 5-fold walk-forward with LightGBM as 2nd model — gate #4 picks
    the winner; folds also produce B2's calibration input. Simulator stays
    the baseline. Loader asserts a3.1 (both) + a4.1 (hitters).
    New files:
      src/learning/gbm_dataset.py     loader + targets(==compute_actual_value)
                                      + temporal split + NaN coercion
      src/learning/gbm_trainer.py     CatBoost primary / LightGBM via flag
      src/learning/gbm_benchmark.py   compare_reports + phantom-category guard
      run_train_gbm.py                train CLI
      run_assemble_enriched.py        assemble enriched set (completeness guard)
      run_benchmark_gbm.py            GBM-vs-simulator on sampled holdout dates
      run_walkforward_gbm.py          5-fold walk-forward, both models, saves
                                      wf_predictions_<model>.csv for B2
      scripts/check_gbm_offline.py    offline harness (18/18, no GBM libs)
[ ] B2. Calibration layer (isotonic/Platt) -> calibrated P(over) per category.
    FIT ON THE WALK-FORWARD FOLD PREDICTIONS (wf_predictions_<winner>.csv),
    not a single window, so calibration is measured out-of-sample.
[ ] B3. Distributional pitcher K (negative-binomial/simulated) for P(K>=line).
[ ] B4. Role-aware expected_innings (starter/opener/bulk, outing trend,
    pitch-count/IL flags) trained on historical outings + live actual_ip.
    Highest single-category lever; fixes short-outing K bias in the SIMULATOR
    (B1 evidence suggests the GBM already sidesteps it).

Phase C — post-collection, evidence-based live changes (gated by #4)
[ ] C1. calibration_report on forward-collected pairs; confirm/deny
    short-outing K bias + confidence flatness with numbers. FILTER OUT
    pre-2026-07-09 hrr-only/version-blank CI pairs.
[ ] C2. Promote winning Phase-B model(s) through the gate; enable corrections.
[ ] C3. Wire live odds -> edge engine; GUI color-coding off EDGE vs de-vigged
    line, never raw projection. (Needs B2's calibrated P(over) — deliberately
    after B2, even though A5's line logging starts much earlier.)
[ ] C4. Shadow betting log graded vs CLOSING lines (CLV).

Phase D — hardening / full automation
[ ] D1. Actions end-to-end: daily predict (all cats) -> record outcomes ->
    line logger -> weekly calibration -> monthly walk-forward -> failure
    alerts (email/issue on job failure or 0-pairs day).
[ ] D2. Bullpen-transition modeling for hitters — only after C proves core.

===============================================================================
A5 KICKOFF — odds sourcing + read-only line logger (fresh chat, parallel task)
===============================================================================
Goal: (1) test whether free-tier APIs actually return MLB PLAYER-PROP lines,
and (2) if props come through, build a READ-ONLY daily line logger that records
open/close lines + prices next to predictions. Additive / collection-safe:
touches no model, no archives, no pairs. It only writes a lines log.

START FROM WHAT ALREADY EXISTS — don't build config from scratch. config.json
already has an `odds` block with scaffolding the logger should reuse:
  odds.odds_api.enabled             (currently false)
  odds.odds_api.api_key_env         = "ODDS_API_KEY"   (env var, not in file)
  odds.odds_api.sport_key           = "baseball_mlb"
  odds.odds_api.regions             = "us"
  odds.odds_api.bookmaker           = "draftkings"
  odds.odds_api.markets             = ["batter_hits","batter_home_runs",
                                       "pitcher_strikeouts"]
  odds.odds_api.market_category_map = {batter_hits->hits,
                                       batter_home_runs->home_runs,
                                       pitcher_strikeouts->strikeouts}
  odds.file.*                       = CSV/JSON fallback + date_pattern
So the category mapping to the model's PropCategory labels is ALREADY defined.
The logger's job is to fill lines against these, not to invent the mapping.

EXPLORE FIRST, BUILD SECOND (roadmap wording: "test... IF props come through"):
free tiers are rate-limited and player-prop coverage is spotty. Confirm props
actually return on a free key BEFORE building the logger. The Odds API and
SharpAPI are the named candidates.

CLV BACKLOG — READ THIS BEFORE TRYING TO BACKFILL:
- CLV (closing-line value) is FORWARD-ONLY in practice. Player-prop CLOSING
  lines are barely archived anywhere free; most APIs serve only current or
  upcoming markets. You cannot reliably reconstruct historical prop CLOSING
  lines, so CLV history starts the day the logger starts — every un-logged
  day is lost. This is the reason to stand up the logger EARLY.
- What you CAN sometimes backfill is historical prop lines (opening or a
  mid-day snapshot). That is a MODEL-VALIDATION signal ("would my projections
  have found edges vs the market historically?"), NOT CLV. Label and store it
  separately; NEVER feed it into CLV math — a non-closing line used as a
  closing line gives a confidently wrong CLV number. Two tracks:
    (a) forward CLOSING-line logging           -> real CLV (C4), starts now
    (b) optional historical prop-line backfill -> model validation only
- Closing lines specifically (not opening) are what C4 grades against, so the
  logger must capture a CLOSE snapshot near first pitch, not just an open.

A5 stays OFF the critical path: B1 (walk-forward + model pick) -> B2
(calibration) is the main line. A5 runs in parallel and blocks nothing.

===============================================================================
Realistic expectations (read before getting excited)
===============================================================================
~500k rows makes GBMs viable; it does not manufacture signal. A4's rolling
Statcast features are where the signal is (A3 alone is volume). Confirmed so
far: the GBM beats the simulator on MAE — a real, provable step — but the
edges are SMALL (combined -0.03), and "beats my simulator" != "beats the
market." Books price with sharp flow; the path is calibrated P(over) +
selective betting on soft spots (props, alt lines, small markets), measured
by CLV. The evaluation discipline (gates, shadow, CLV) is the actual moat.

===============================================================================
Known issues / weaknesses (confirm with data, then fix)
===============================================================================
- Short-outing K over-projection in the SIMULATOR (IP floor + role-blind
  estimate). B4 fix. B1's K edge is consistent with this diagnosis.
- Confidence flat tiers (~0.62/0.72), unvalidated — do not sort/size by it.
  GBM projections currently carry confidence=0.0 on purpose; B2 owns
  calibrated confidence.
- Pitcher K point estimate (no distribution) until B3.
- HR props: most nights no HR over is bettable (rare event, heavy juice).
  Neither model meaningfully "wins" HR on MAE (deltas ~0.008 = noise).
- Verify HRR actual (hits+runs+rbi) matches the market's HRR definition.
- A4 rolling features BLANK for early-season / low-BiP (< min_bip=5) rows by
  design; roll15_games/bip counts let a GBM know history is thin. Not a bug.
  B1's loader coerces these ""s to NaN (CatBoost/LightGBM handle natively).

===============================================================================
Operational gotchas (learned the hard way — save yourself the pain)
===============================================================================
- GIT: repo has heavy CRLF churn and had tracked __pycache__ .pyc files (now
  untracked). Stage EXPLICIT file lists; NEVER `git add -A`. Live Actions push
  to origin/main regularly, so ALWAYS `git pull --rebase origin main` before
  `git push`. PowerShell `>` writes UTF-16-w/-BOM — never use it to write git
  control files (it corrupted MERGE_HEAD once). During a rebase, `--theirs`
  means YOUR replayed commit (flipped from intuition).
- DATA IS GITIGNORED: data/training/<season>/ shards, training_*.csv.gz, and
  data/cache/ are ignored; only manifest_*.json is tracked. Verified working.
- WINDOWS FILE LOCKS: close CSVs in Excel before re-enriching; the A4 CLI
  warns cleanly instead of crashing on a locked file.
- pybaseball MUST be installed for A4 AND the live model's Statcast path
  (else it falls back to league baselines).
- ASSEMBLY: after A4, enriched data lives in per-date *_statcast.csv shards;
  the old assembled training_hitters_*.csv is UN-enriched. Use
  run_assemble_enriched.py (NOT TrainingSetBuilder.assemble, which would
  double-count raw+_statcast). Output is *_statcast.csv.gz. Row counts must
  equal the A3 per-season sums (133,344 / 14,816) — mismatch = double-count
  or missing dates. The assembler's completeness guard refuses (exit 2) if
  any raw hitter date lacks an enriched twin.
- BENCHMARK / WALK-FORWARD COST: each reconstructed date ~2.5 min (bundle
  build; does NOT cache away) + ~3s Statcast pull (cached). 20 dates ~= 55
  min; 5-fold x 7-day walk-forward ~= 90 min. Walk-forward disk-caches sim
  reconstructions (data/cache/wf_simulator/), so re-runs resume free.
- PowerShell env: `$env:PYTHONPATH="."` per session (not `PYTHONPATH=.` —
  that's bash). Config path is config/config.json (not repo root). --config
  is OPTIONAL for training the 4 gated cats (only fantasy needs the weights).
- Python 3.14 + catboost 1.2.10 works (cp314 wheel exists). No runtime
  issues in training or inference.
- run_benchmark_gbm / run_walkforward_gbm import run_reconstruct_date as a
  TOP-LEVEL module (import run_reconstruct_date), not from src — the
  run_*.py entry points live in repo root.
- Offline harnesses only need fixtures; the REAL bugs surface on real
  data/Windows. Spot-check real output before trusting a big run — the
  1-date/1-fold smoke tests before the 20-date and 5-fold runs each caught
  a real issue cheaply (an import path; nothing logic-level).

===============================================================================
How to work cost-efficiently with Claude
===============================================================================
- FRESH chat per roadmap item; upload this file (+ repo zip if coding).
- Name the item: "Build A5" / "B2 calibration" — the file gives context.
- Heavier model for hard builds/debugging; lighter for small questions.
- Paste only the last ~15 lines of errors. Batch related questions.
- Update this file after each session (status, checkboxes, new decisions).
- Validate offline harness FIRST, then real smoke test (1 date / 1 fold),
  then the full run, then commit. Commit each roadmap item separately.















--------------------------------------------------------------------------------------
when to start using live
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
