PROJECT_CONTEXT.md — v10 (master roadmap) — paste into any new chat

Keep this file in the repo. Start a FRESH chat per task, upload this (plus the
repo/zip if code work is needed), and say which roadmap item to work on.
Update "Current status" and check off roadmap items as they complete.

===============================================================================
CURRENT STATUS (2026-07-11) — *** B4 IS CLOSED AND LIVE. model_version has
MOVED for the first time: dab23f4fbac8 -> 43a43880e377. ***

B4 (role-aware expected_innings) was found broken, fixed, gated, replicated on
an independent season, and PROMOTED — all today. Two separate silent-failure
bugs had to be fixed before the gate could test anything at all; either one
alone would have produced a gate that "passed" while measuring nothing. Both
are described below because the FAILURE MODE matters more than the fix.

Also closed today: HTTP retry/backoff (the daily Action was dying on transient
timeouts), a self-healing outcome-grading backfill (three separate paths were
silently losing whole days of forward pairs), and a full training-set rebuild
(the A3 training set had the same appearances-as-starts bug baked into
14,816 rows on disk).

Six commits, in order:
  af2f496  B4: point-in-time bug + config threading; passes gate
  97f5618  HTTP: transport-level retries with exponential backoff
  f176a2a  PROMOTE B4: role-aware expected_innings LIVE (43a43880e377)
  7d68d22  Automation: self-healing backfill window for outcome grading
  a6a1880  Training manifests: rebuilt 2023-2026 with the fixed parser
  bab52dd  Commit orphaned prediction archives (2026-07-10, 2026-07-11)

THE NEXT REAL PIECE OF WORK IS THE HRR BUG (see OPEN ITEMS #1). It is the
single biggest accuracy lever left, it is structural (not a tuning problem),
it also corrupts fantasy, and it is now evidenced from three independent
directions. Do NOT enable bias corrections until it is fixed — a correction
layer fitted on top of a broken distribution just hides the bug. AFTER it is
fixed, add standalone `runs` and `rbi` categories (#1b) so hrr stops being a
black box — the ONLY reason the previous attempt patched a symptom-cap instead
of finding this bug is that nobody could see which component was broken.

-------------------------------------------------------------------------------
B4 — WHAT WAS ACTUALLY WRONG (both bugs, because the pattern will recur)

BUG 1 (the one this doc predicted): point_in_time._aggregate_pitching never
set `games` on the point-in-time PitchingStatsSnapshot, and set
games_started=len(rows) (total appearances, not real starts). Because
RoleAwareInningsEstimator returns role="unknown" whenever games <= 0, EVERY
reconstructed pitcher fell back to default_innings. Fixed by adding a
per-game games_started field to GameLogRow, populating it from the gameLog
split's `gamesStarted` key (0/1 per game — the SAME key mlb_api._parse_pitching
already reads, so it is the schema the live path trusts), and setting
games=len(rows) + games_started=sum(per-game starts) in the aggregator. BOTH
UNFLOORED: a reliever's true 0 starts is exactly the opener signal, and
flooring it would reintroduce the phantom start the whole fix exists to remove.

BUG 2 (NOT predicted, and fatal on its own): reconstruct_objects constructed
MLBStatsAPI and AsOfMLBAPI with `season=` ONLY, never passing `config`. So
RoleAwareInningsEstimator was built from {} -> enabled=False -> the legacy
heuristic, REGARDLESS of what --config pointed at. Fixing BUG 1 alone would
NOT have unblocked the gate. Four constructor sites, all now pass config=.
Threading config through is INERT when role_innings is absent/disabled
(verified byte-identical to legacy across the full snapshot grid), so it was
safe to ship during collection.

LESSON: run_b4_gate_verdict.py's exit-2 "K probabilities are IDENTICAL" guard
names BOTH causes in its error text. It was right. The doc only chased one.
When a tool tells you two things can cause a failure, check both.

-------------------------------------------------------------------------------
B4 — THE FIT, THE GATE, THE REPLICATION

Fit (2023, clean temporal holdout — gate runs on 2024-25):
  opener_innings  1.5 -> 2.6   FITTED from n=281 point-in-time-labelled rows
  bulk_innings    3.5 -> 3.5   KEPT (see B5 in OPEN ITEMS)
  everything else structural, unchanged.

Roles were labelled with the SAME 5-GAME TRAILING POINT-IN-TIME WINDOW the
gate's estimator actually reads (MLBStatsAPI.get_pitchers_for_date calls
estimate(recent), and AsOfMLBAPI's `recent` is get_pitching_stats_as_of ->
last 5 appearances strictly before the date). A season-level roster — which is
what A6's add_role_and_recompute produces, since it merges on player_id ALONE —
would have collapsed every pitcher to ONE label for the whole season and fitted
a different population than the constants get applied to. New tooling:
  scripts/build_pit_role_rows.py       (point-in-time role + actual_ip rows)
  scripts/fit_role_innings_pit.py      (reuses the REAL fit_constants; only the
                                        source of the (role, actual_ip) frame
                                        differs — one ruler, no reimplementation)

Gate (16 dates, seed 17, B=4000, 17,217 matched rows):
  PASS — frozen significantly better on ZERO of 3 K lines.
  All 3 K lines favor B4; Murphy skill roughly DOUBLES on each.
  Hitter negative controls all TIE.

  The hitter drift was verified against a MEASURED NOISE FLOOR, not assumed:
  a frozen-vs-frozen control run gave hitter drift 0.0055/0.0038/0.0062 —
  indistinguishable from B4-vs-frozen's 0.0058/0.0038/0.0058. Critically that
  control also gave K drift of EXACTLY 0.00000 (the K sim is analytic, not
  Monte Carlo), so the 0.0249 mean / 0.751 max K drift in the B4 run is 100%
  real signal with a zero noise floor underneath it. Do this control run for
  any future gate — a single-date smoke has n_dates=1, which makes the block
  bootstrap degenerate (ci_lo == ci_hi on every row) and "significance"
  meaningless.

Replication (2026, independent season, fitted separately):
                  2023      2026
  opener_innings   2.6       2.3     (fitted independently)
  opener n         281       167
  bulk start_ratio 0.527     0.527   (agrees to THREE decimals)
  starter ip       5.416     5.396
Both seasons land far below the legacy 5.5 default / 4.0 floor for openers.
The constant is a stable structural fact about bullpen usage, not a 2023
artifact. Promoted the GATED artifact (2.6), not a refit — the gate is what
earns promotion, and refitting would ship something the gate never saw.

-------------------------------------------------------------------------------
INFRASTRUCTURE CLOSED TODAY

HTTP RETRIES (97f5618). MLBStatsAPI built a BARE requests.Session() with no
retry adapter and timeout=20, so _get made exactly ONE attempt and turned any
blip into a fatal DataFetchError. That is what killed the scheduled
daily-predictions Action, and a gate smoke test mid-run. Retry logic DID
already exist — in DiskCachedGetMixin._network_get — but that mixin's own
docstring forbids it on live slates, so run_slate.py ran UNPROTECTED. The logic
existed; it was wired to the wrong path. Retries now live at the TRANSPORT
(mlb_api._build_session): urllib3 Retry over 429/5xx, backoff 0/2/4/8s, 404
fails fast, GET-only, timeout 30s. The mixin's retry loop was REMOVED in the
same change — keeping both would MULTIPLY them (3 mixin attempts x N session
retries = up to 3N requests per URL), hammering the API hardest exactly when a
429 asks us to back off. One retry layer, at the transport.
  NOTE: a bare requests.Session is not "no retry config" — its default adapter
  carries Retry(0), i.e. an EXPLICIT never-retry. That is why the bug was
  invisible.

BACKFILL (7d68d22). The record-outcomes job graded YESTERDAY and only
yesterday, and skipped "gracefully" when the archive was missing. Three
independent failure modes each lost a day PERMANENTLY and SILENTLY:
  1. predict failed -> no archive -> next day's record job said "nothing to
     grade" and exited CLEAN. Nobody went back.
  2. predict succeeded but its git push failed -> archive never reached repo.
  3. games not final at 10:00 ET -> the script exited 1 -> workflow RED ->
     pairs never recorded, nothing retried them.
run_record_outcomes.py now takes --backfill-days N (default 7 in the workflow).
Safe because OutcomeRecorder is idempotent BY CONSTRUCTION (_build_pair_rows
loads existing (player_id, game_date, category) keys and skips them), and
because "games not final" and "no archive" are SKIPS, not failures — failing on
those would turn the workflow red every time a West Coast game runs long, and a
workflow that cries wolf is one nobody reads. First real run RECOVERED 40
ungraded pairs from 2026-07-07. daily-predictions.yml's commit step now runs
with if: always(), so an archive written before a crash still reaches the repo
(the job still fails afterward — a real outage stays visible).
  NOT backfilled: PREDICTIONS. A slate regenerated days later would see
  lineups/weather that were not knowable at 4 PM ET, and PredictionArchive does
  not even round-trip the simulation block. Injecting reconstructions would
  contaminate the one dataset whose entire value is that it is FORWARD. A lost
  slate stays lost; a lost GRADE gets healed.

TRAINING-SET REBUILD (a6a1880). The A3 training set had the SAME
appearances-as-starts bug baked into its pit_gs / pit_recent_gs /
pit_recent_ip_per_gs columns — 14,816 rows of it. Verified on Tyler Holton
(663947, a reliever): pit_gs climbed 17 -> 28 -> 39 -> 41 while he threw 1-2 IP
per outing. Any future GBM/IP model trained on those columns would have learned
the conflation as if it were signal. Rebuilt all four seasons with --rebuild
(the MANIFEST is the resume gate, not the shards — deleting shard dirs does
nothing).
  PROOF: Holton max pit_gs, before -> after
    2023: 17 -> 0     2024: 63 -> 8     2025: 62 -> 5     2026: 1 -> 1
  Cost: ~5 minutes, misses=0. The http cache stores RAW API JSON, so cached
  responses + fixed parser = correct output with zero refetches.

HARNESS FIXES. check_bug2_offline.py had a broken import (bare
`from model_version import ...`) and had been silently failing. More important:
check_reconstruct_offline.py was carrying BUG 2 INSIDE ITSELF — it constructed
FixtureAPI without config=, so it was testing the pre-B4 legacy path while the
live model runs B4. Its pitching fixture also never set gamesStarted, so an
"Ace" (6 IP / 7 K per outing) parsed as 0 starts and classified as an OPENER.
Both fixed; the harness now also pins games/games_started explicitly so BUG 1
cannot silently return. 26/26 (was 21/2).

-------------------------------------------------------------------------------
FULL OFFLINE HARNESS BOARD (2026-07-11, all green except as noted)

  check_reconstruct_offline      26/26   (A2)
  check_training_builder_offline 29/1    (A3) — see OPEN ITEMS #2
  check_statcast_roller_offline  19/19   (A4)
  check_a6_offline               50/50   (A6)
  check_b3_offline               13/13   (B3)
  check_gbm_offline              18/18   (B1)
  check_bug2_offline              9/9
  check_b4_offline               26/26   (B4)
  check_pit_pitching_offline     44/44   (B4 point-in-time — NEW)
  check_pit_role_rows_offline    33/33   (B4 historical fit — NEW)
  check_http_retry_offline       31/31   (NEW)
  check_backfill_offline         26/26   (NEW)
  check_b4_gate_tools_offline    24/24
  check_shadow_compare_offline   27/27
  check_calibration_offline      needs run_calibrate_gbm.py first (not a bug)

===============================================================================
OPEN ITEMS (2026-07-11) — in priority order

#1  HRR IS STRUCTURALLY BROKEN. *** BIGGEST LEVER. NEXT REAL WORK. ***

    src/simulation/game_simulator.py builds a FRESH BaseState on EVERY plate
    appearance, reads one field off it, and throws it away:

        def _rbi_for_outcome(self, kind):
            b1, b2, b3 = self._sample_base_state()   # fresh random draw
            state = BaseState(bases=[b1, b2, b3])    # NEW object every PA
            state.advance_single()                   # ...
            return state.rbi                         # read once, DISCARDED

    Three consequences, worst first:
      (a) runs and RBI come from UNRELATED draws within the same PA.
          _rbi_for_outcome samples a base state; _run_for_batter IGNORES it
          entirely and flips an independent coin against p_score_from_base.
      (b) the batter's own run has no causal link to him being on base. You
          single, and a coin comes up heads 28% of the time — rather than you
          standing on first and scoring if the next guys drive you in.
      (c) base state does not persist across PAs, so nothing correlates within
          a game. Real games CLUSTER; this model's PAs are independent islands.

    hrr = hits + runs + rbi — three variables that are strongly positively
    correlated in reality, modeled as (near-)independent. The MEAN survives;
    the DISTRIBUTION is wrong. For a Brier-scored over/under, the shape IS the
    product.

    EVIDENCE (three independent directions):
      1. Code: BaseState is a full state machine (it correctly advances
         runners, tracks outs, credits RBI) being used as a stateless lookup.
         state.runs is computed correctly and NEVER READ.
      2. Validation, 5 consecutive days: hrr MAE 1.516 / 1.527 / 1.527 /
         1.529 / 1.501 — remarkably CONSISTENT, i.e. systematic, not noise.
         Meanwhile hrr mean_error is ~0 (-0.026 / -0.048 / +0.060 / +0.260 /
         -0.014). Point estimate fine, distribution wrong. And median_error
         (+0.53..+0.81) diverges hugely from mean_error (~0), so the error
         distribution is SKEWED. Compare the components: hits MAE ~0.66-0.71,
         home_runs ~0.21-0.24 — both fine. ONLY THE COMPOSITE IS BROKEN.
      3. Somebody already hit this and patched the SYMPTOM: scripts/
         diagnose_hrr_breakdown.py computes an "overshoot" CAP
         (new_cap = hrr_rate * pa * 1.9). A fat upper tail is exactly what
         independent-sampling-of-correlated-quantities produces. The cap is
         treating the fever.

    THE FIX: give GameSimulator ONE persistent BaseState per simulated game;
    advance it stochastically between the batter's own PAs to represent the ~8
    intervening batters (that is what DEFAULT_BASE_STATE_DIST is actually FOR —
    a transition target, not a per-PA resample); and read runs AND rbi off the
    SAME state machine, deleting _run_for_batter's independent coin.

    THIS ALSO FIXES FANTASY. monte_carlo._fantasy_points sums result.rbi and
    result.runs, so hitter fantasy inherits the identical bug. Two categories,
    one root cause.

    *** CAVEAT THAT WILL BITE: p_score_from_base (0.27/0.28/0.40/0.56) was
    almost certainly FITTED AGAINST THE BROKEN SAMPLING — the retrain layer
    tuned it to make a wrong model produce roughly-right means. Fixing the
    structure may initially look WORSE until those constants are re-fit. Plan
    for it: fix structure -> re-fit constants -> gate the COMBINATION. Do not
    revert a correct change because its first measurement looks bad. ***

    This is a gated change (it moves hitter output -> forks model_version ->
    needs its own gate, exactly like B4). Also noticed while reading:
    _fantasy_points OMITS hit_by_pitch and stolen_base — they are in the config
    and the dataclass but never summed, and the simulator never generates them.
    PrizePicks scores both (2 pts / 5 pts). Minor, but it is a silent gap.

#1b ADD STANDALONE `runs` AND `rbi` CATEGORIES — *** AFTER #1, NOT INSTEAD ***

    ORDERING IS THE WHOLE POINT. Adding an rbi category does NOT fix hrr. It
    would create a NEW prop that reads the SAME broken result.rbi — the bug
    made more visible, not fixed. Doing this BEFORE #1 bakes the correlation
    bug into two more props.

    But AFTER #1 it is genuinely worth doing, for a reason that has nothing to
    do with betting those lines: DECOMPOSITION IS DIAGNOSIS.

    Right now hrr is a BLACK BOX. When its MAE is 1.52, you cannot tell which
    component is responsible — hits, runs, or RBI. That is exactly why the
    previous attempt reached for a CAP (diagnose_hrr_breakdown.py) instead of
    finding the real bug: there was no way to see inside the composite. If
    hits / runs / rbi were each graded separately, component-level error would
    be visible immediately and this class of bug could not hide again.

    Currently graded: hits, home_runs, hrr, strikeouts. There is no standalone
    runs or rbi category anywhere — RBI is only ever seen INSIDE the composite.

    HONEST EXPECTATION: RBI is the HARDEST of these to predict, because it
    depends on things outside the batter's control (whether the guys ahead of
    him got on base). A standalone RBI line will have worse MAE than hits no
    matter how good the model gets. That is IRREDUCIBLE, not failure. Know it
    going in so nobody chases it.

    PrizePicks scores both (Run 2 pts, RBI 2 pts), so they are also real
    props, not just diagnostics.

    Sequence: fix #1 (the correlation) -> re-fit p_score_from_base -> gate the
    combination -> THEN add runs/rbi as graded categories.

#2  pit_recent_ip_per_gs IS ILL-DEFINED FOR RELIEVERS. (Phase 2 blocker.)

    Post-fix, recent_gs is REAL starts. A reliever with zero starts in the
    recent window divides by zero -> the feature falls back to 0.0. That is the
    fix working (no more phantom starts) but 0.0 is the WRONG SENTINEL: to a
    GBM it reads as "throws zero innings per start" (an unbelievably terrible
    starter) rather than "does not start". Those are completely different
    things and the model cannot tell them apart. It should be NaN/blank —
    check_gbm_offline.py already has a check literally titled "A4 blank became
    NaN (not '')", so the codebase already knows this pattern matters.
    This is why check_training_builder_offline.py is 29/1. Not urgent (no GBM
    is deployed) but FIX BEFORE TRAINING ON THIS DATA.

#3  B5 — BULK. bulk_innings is still at its 3.5 placeholder, in BOTH seasons,
    for the same structural reason: the fitted value (~4.35 in 2023, ~4.41 in
    2026) is ABOVE starter_ip_floor (4.0), so fit_constants clamps it to the
    floor — which would make a bulk arm project identically to the shortest
    possible starter, i.e. B4 would fork model_version while doing NOTHING for
    that bucket.

    The bucket boundaries are wrong, not the data. Diagnosed and confirmed:
    within bulk, actual_ip rises MONOTONICALLY with start_ratio (3.67 -> 3.88
    -> 4.60 -> 4.88 across ratio bins), with NO bimodality — so bulk is a
    genuine role continuum, not contaminated with misclassified starters (an
    earlier hypothesis, tested and REJECTED). The fix is to lower
    starter_min_ratio (from 0.80) so high-ratio swing arms classify as
    STARTERS, where the per-start clamp handles them properly, freeing bulk to
    be a genuinely short bucket. That is a STRUCTURAL CUT change, which
    fit_role_innings' own docstring forbids re-fitting from the innings it
    produces — so it needs its own gated experiment with its own evidence.

#4  NO MARKET DATA -> CANNOT MEASURE EDGE. run_shadow_compare.py works (27/27)
    but has nothing to eat: data/lines/lines_<date>.csv is not being populated.
    Calibration against OUTCOMES (run_validate.py) tells you the model is
    well-behaved. It says NOTHING about whether your probabilities beat the
    PRICE. Those are different questions and only the second one makes money.
    The A5 daily logging habit still has not started. Every un-logged day is
    permanently lost — closing lines are non-reconstructable.

#5  xwoba_scale = 7.81 IS A RED FLAG. bias_corrections.json needs to multiply
    xwOBA by 7.8x (and hard_hit by 5.1x, barrel by 3.4x) to make the power
    model work, while hit_rate_scale sits at ~1.0. A healthy calibration
    multiplier is near 1.0. The correction layer is BRUTE-FORCING a
    mis-specified power model rather than correcting a small bias. Worth
    investigating pa_simulator + legacy_statcast_features for a units or
    double-counting problem.

#6  DO NOT ENABLE BIAS CORRECTIONS YET. The dry-run says it would fit at 0.85
    confidence on 3,624 pairs (weighted MAE 0.906). Do not. A correction layer
    fitted on top of a structurally broken hrr distribution would make the
    model look right ON AVERAGE while staying wrong on DISTRIBUTION — which is
    exactly what loses money on over/unders. Fix #1, then re-fit.

#7  WEATHER RESOLVES IN AN OFFLINE HARNESS AND NOBODY KNOWS WHY.
    check_reconstruct_offline.py used to assert weather DEFAULTED (no network);
    it now comes back {'resolved': 18}. That is arguably better, but the reason
    was never established. The assertion was changed to "accounted for on all
    18 bundles (resolved or defaulted)" rather than flipped to match whatever
    was observed (which would make the check vacuous). Worth a look.

#8  THE ENRICHED STATCAST HITTER SET MAY BE STALE.
    training_hitters_2023_2025_statcast.csv.gz is built by
    run_build_statcast_features.py / run_assemble_enriched.py, NOT by the
    training builder — so today's --rebuild did NOT regenerate it. Hitter rows
    should not carry the pit_gs bug (it is a pitcher column), but if the
    enriched set embedded any OPPOSING-PITCHER features derived from pit_gs /
    pit_recent_gs, it would have inherited the conflation. UNVERIFIED. Check
    its columns before training on it.

-------------------------------------------------------------------------------
STANDING NOTES THAT KEEP EARNING THEIR KEEP

  * The cheap check pays for itself. Three times today: the offline harness
    caught BUG 2 before an overnight run; the frozen-vs-frozen control proved
    the hitter drift was noise rather than a leak; and `git status --short`
    caught an UNINTENDED promotion (the role_innings block had been added to
    the live config.json before the gate was even run — it was never staged,
    but it would have silently mixed two model_versions in the pairs file).

  * A harness that passes on both the broken AND the fixed code guards
    NOTHING. Every new harness this session was MUTATION-TESTED: reverted the
    fix, confirmed the harness fails, confirmed it names the right invariant.
    check_pit_pitching_offline.py fails 28/36 against the pre-fix code and
    prints "fixed=5.5 old=5.5" — the exact gate-tests-nothing signature.

  * Stub at the RIGHT layer or you measure nothing. The first retry harness
    stubbed HTTPAdapter.send() — but send() is WHERE urllib3's retry loop
    lives (it calls conn.urlopen(retries=...)), so stubbing it BYPASSES the
    machinery under test. It reported "1 attempt" every time and would have
    "proven" retries work while measuring nothing. Drive the real Retry state
    machine (is_retry / increment / get_backoff_time) instead.

  * Fixture arithmetic must be done POST-cutoff. An "opener" fixture with 2
    starts in 12 games looks like ratio 0.167 (opener) — but the as-of cutoff
    trims the window to 9 games, making it 2/9 = 0.222 (bulk). Got this wrong
    once; the boundary is now pinned explicitly in check_pit_pitching_offline.

===============================================================================
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


reconstruct_objects() (B1/gate seam)
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
run_analyze_k_error.py     A6  read-only K-error-vs-actual_ip analysis
(actual_ip buckets; role before/after via the real RoleAwareInningsEstimator)
scripts/check_a6_offline.py     A6  offline harness (50/50)
scripts/diagnose_k_pairs.py     A6  read-only pairs-CSV drop attribution


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
an UNFLOORED games (total appearances) field to PitchingStatsSnapshot,
populated from the same gamesPlayed the hitter parser already reads (no new
API call). start_ratio = games_started / games. The pre-B4 heuristic floored
games_started to >=1, which MANUFACTURED a phantom start for pure relievers/
openers and reported starter-length outings — a direct source of the
short-outing K over-projection. games_started stays floored (no existing
read changes); games is the new honest denominator. Thin samples
(games < min_games_for_role) are FLAGGED role="unknown" and fall back to the
starter path, not assigned an invented role.

[A6] The role-aware before/after imports the REAL RoleAwareInningsEstimator
(from src.prediction.role_innings), NOT a reimplementation — role labels and
expected_innings can never drift from what B4 ships. For each pitcher row it
reproduces the LEGACY expected_innings (RoleAwareInningsEstimator.
_legacy_expected_ip, byte-identical to the pre-B4 heuristic) and the CANDIDATE
role-aware value, then rescales predicted_value by candidate/legacy. This
rescale is EXACT, not approximate: k_prob in prop_engine does NOT depend on
expected_innings (only the batters_faced multiplier does), so predicted =
k_prob * expected_innings * PA_PER_INNING and the ratio cancels k_prob. A6 is
descriptive evidence only — it NEVER edits config.json; the gated config edit
happens once, at the gate, by a person. --roster needs real innings_pitched
(the estimator requires ip>0 to assign any role but "unknown"; without it,
rows fall back to default_innings on both sides -> ratio ~1.0, no change,
which is correct-not-a-bug). A6 reads SEASON-level role signal for the
descriptive fit; the actual B4 gate reconstruction must use point-in-time
snapshots like everywhere else.

[BUG1] (FIXED 2026-07-10) B4's mlb_api.py top-level import of
RoleAwareInningsEstimator created a circular import (mlb_api -> prediction
pkg -> correction_manager -> learning pkg -> outcome_recorder -> mlb_api) that
made import src.data.mlb_api raise. Fix = LAZY import inside
MLBStatsAPI.init and the _estimate_expected_ip helper. No behaviour
change, does not fork model_version (imports aren't hashed). Lesson baked into
the gotchas: an offline harness that imports a module DIRECTLY can pass while
that module is broken THROUGH the package graph — always add a plain
import <the_real_module> check.

[BUG2] (FIXED 2026-07-10) model_version was blank on ALL 2758 pairs. Root
cause: an OFF-BY-ONE SLICE in outcome_recorder._actual_detail_fields, NOT the
migration and NOT the entrypoint. model_version is PAIR_COLUMNS[7]; the blank
detail dict was built from PAIR_COLUMNS[7:], which INCLUDES model_version, so
row.update(detail) in _build_pair_rows overwrote the just-set stamp with "" on
every row — from day one, all categories. Fixed by anchoring the slice to
PAIR_COLUMNS.index("actual_pa") (index 8) so it never includes the stamp and
can't regress on column reorder. The v7/v8 "cause localized by elimination"
claim was WRONG — both stated mechanisms were disproven: (a) the migration
PRESERVES existing stamps (reproduced), and (b) config passing was fine —
load_config(None) and explicit --config both yield dab23f4fbac8. Backfill of
the 2758 rows to dab23f4fbac8 done and valid (collection freeze). Added a
write-path guard (raises RetrainError on any blank stamp — it is what surfaced
this bug) + a no-config constructor warning. test_outcome_recorder_appends_pairs
now guards that the stamp survives .update(). LESSON: don't trust a
"localized by elimination" handoff that never ran the failing path — the guard
that actually executed the write path found it in one test.

===============================================================================
Provenance / versioning (three stamps — filter by these, never mix)

model_version : config hash on every prediction->outcome pair (A1). Current
= dab23f4fbac8. *** UPDATE (2026-07-10): BUG 2 (all pairs version-blank) is
FIXED — was an off-by-one slice that blanked the stamp on write; see the
[BUG2] entry. All 2758 rows backfilled to dab23f4fbac8 and the forward path
verified (0 blank rows, single version in the CSV). Filtering on model_version
for calibration/C1/B4 is now live and correct. Historical note: pre-2026-07-09
CI pairs were ALSO hrr-only; that part still holds independent of the version
fix.
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


model_version tag, live in CI.
[x] A2 DONE. run_reconstruct_date.py + reconstruct_objects() (B1/gate seam).
[x] A3 DONE. Resumable 2023-2025 builder; real run complete.
[x] A4 DONE. Rolling Statcast enrichment; real 2023-2025 enrich complete.
[~] A5 CODE DONE + COMMITTED (2026-07-10, commit ed3d0bc). Odds confirmed +
read-only line logger built, offline-tested, and (as of today) actually
tracked in git — it sat untracked since the original build, same near-miss
pattern as B1's git recovery incident. data/lines/ was already gitignored;
no gitignore change was needed. LEFT: start daily logging habit + one live
--dry-run check that HR/K populate on DK; rotate API key (it was pasted in
chat during setup). See A5 STATUS section. Blocks nothing.
[x] A6 DONE (2026-07-10, own chat). K-error-vs-actual_ip analysis tooling,
read-only/additive, offline-validated (50/50) + run on real pairs. Files:
run_analyze_k_error.py (actual_ip bucketing + real-estimator role before/
after), scripts/check_a6_offline.py, scripts/diagnose_k_pairs.py (drop
attribution). See [A6] key decision. Committed + pushed. Surfaced BUG 1 (B4
circular import, FIXED) and BUG 2 (model_version blank on all pairs, since
FIXED — off-by-one slice, see [BUG2]). Calibration plots / confidence-vs-accuracy were the nominal A6 scope
too; the K-error split was the priority for B4 and is what shipped — the
other two views can be added later if wanted, they're not on the critical
path. NOTE: on the CURRENT real pairs A6 is not yet decision-useful (2 K
dates, ~4 short-outing rows); it needs more forward K pairs. Those pairs are
now model_version-filterable (BUG 2 fixed), so forward K collection is clean.


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

[~] B4 OFFLINE BUILD DONE (2026-07-10); GATE KIT BUILT + COMMITTED
(2026-07-10, later same day, commit 0bbfa21); GATE STILL OPEN. Role-aware
expected_innings. Fixes short-outing K over-projection in the SIMULATOR (B1
evidence, C1 evidence; the GBM already sidesteps it). LIVE-MODEL CHANGE ->
forks model_version (two-part: allowlist entry [shipped] + config block [at
gate]; see [B4] key decisions). Built + offline-validated only; NOT
promoted, live model unchanged. Files: src/prediction/role_innings.py (new,
RoleAwareInningsEstimator), scripts/check_b4_offline.py (24/24, now
including the standing-note package-graph smoke check — see below),
config/role_innings.example.json (new, reference — enabled:false shape),
src/data/mlb_api.py (games field + estimator wiring + _estimate_expected_ip
delegates), src/prediction/daily_predictor.py (one line: passes config to
MLBStatsAPI, inert while disabled), src/utils/model_version.py
(role_innings added to MODEL_CONFIG_KEYS).
GATE KIT (2026-07-10, own chat, commit 0bbfa21): standing-note package-graph
smoke check added to check_b4_offline.py (now 26/26 — check 0a plain
`import src.data.mlb_api` through the real package graph, the line that
would have caught BUG 1; 0b delegation check). Three new tools, each behind
scripts/check_b4_gate_tools_offline.py (24/24): scripts/build_pitcher_roster.py
(builds A6's --roster CSV from real season pitching lines),
scripts/fit_role_innings.py (fits opener_innings/bulk_innings from real
actual_ip per role; min-n=15 guard with a DO-NOT-GATE-YET flag; ordering
guards; validates through the real RoleAwareInningsEstimator; previews the
model_version fork; keeps ratio cuts/clamp/min_games/default structural, not
fitted), run_b4_gate_verdict.py at repo root (frozen-vs-candidate sim block
bootstrap over dates, B=4000, Murphy skill, hitter negative-control check,
exits 2 rather than emitting a verdict if K probabilities are identical
between the two runs — the reconstruction-plumbing tell). Runbook:
B4_GATE_RUNBOOK.md (not yet committed as of this session — save to repo
root if you want it version-controlled).
RAN ON REAL DATA (not yet a gate result): roster builder -> 88/88 pitchers,
0 fetch failures, 0 games=0. Fitter filtered to model_version=dab23f4fbac8
-> 56 usable K rows (32 dropped, missing predicted_value/
actual_strikeouts/actual_ip>0) -> opener n=3, bulk n=6, BOTH under
min-n=15 -> DO-NOT-GATE-YET, placeholders kept unchanged (opener 1.5 /
bulk 3.5); fork preview dab23f4fbac8 -> 6a71e023582c is the PLACEHOLDER
hash, not a fitted result.
LEFT (its own chat, at the gate, once forward K data clears min-n=15 for
both opener and bulk): fit the real role->innings numbers via
fit_role_innings.py; A6 before/after acceptance; build config/config.b4.json
(NOT config.json directly — avoids running the daily slate B4-enabled
pre-verdict); run_gate_reconstruct.py --config config/config.b4.json ->
run_b4_gate_verdict.py block-bootstrap-over-dates that role-aware K
calibrates equal-or-better than the frozen sim without breaking normal
starters, with hitters as negative controls; only then flip config.json at
promotion, so the model_version fork coincides with go-live. Full sequence
in B4_GATE_RUNBOOK.md. The ONLY thing that advances this is calendar time on
real forward-collected K pairs (run_slate.py daily) — no reconstruction
shortcut; the project's own data-collection philosophy (see DATA COLLECTION
NOTES below) says this explicitly for K thinness.

Phase C — post-collection, evidence-based live changes (gated by #4)
[x] C1 DONE (2026-07-10). calibration_report run on 2758 forward pairs
(2026-07-05 -> 07-09). The anticipated pre-07-09 hrr-only/version-blank
filter turned out to be MOOT for this data — all rows already post-fix and
stamped dab23f4fbac8, nothing to exclude. Results: hits/home_runs/hrr
well-centered; strikeouts +0.480 bias (over-projecting, n=88) — corroborates
B4's short-outing premise without contradicting any earlier gate. Confidence
informative (0.80-0.90 bucket) but not monotonic — sort-worthy, not yet
size-worthy. See NEXT ACTIONS above for the run command and full numbers.
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

model_version BLANK ON ALL PAIRS (BUG 2, FOUND + FIXED 2026-07-10). Was: all
2758 pairs version-blank, every category, incl. recent rows. Root cause: an
off-by-one slice in outcome_recorder._actual_detail_fields (PAIR_COLUMNS[7:]
included model_version and blanked it via row.update) — NOT the migration and
NOT the entrypoint config passing (both handoff hypotheses disproven). FIXED
by anchoring the slice to index("actual_pa"); 2758 rows backfilled to
dab23f4fbac8; forward path verified clean. C1/B4 model_version filtering now
LIVE. Full detail in the [BUG2] key-decision entry. No longer a blocker.

B4 circular import (BUG 1) — FIXED 2026-07-10 (mlb_api.py lazy import). Kept
here as a pointer only: if anything re-hoists that import to module top level,
import src.data.mlb_api will break again. See [BUG1] key decision + the new
gotcha about direct-import-vs-package-graph harness blind spots.

Short-outing K over-projection in the SIMULATOR. B4 fix's GATE KIT is BUILT,
offline-validated, and committed (26/26 + 24/24) but the GATE ITSELF DID NOT
RUN — not live yet; the bias is still present in the live model until B4
clears its gate. Three independent signals now corroborate the same bias:
B1, A6 (K-error strongly negative at low actual_ip, fading to ~0 around 5-6
IP — the SHAPE is confirmed, but short-outing buckets were n=2-7 / 2 dates,
so magnitude wasn't fittable), and now C1 (calibration_report on 2758
forward pairs: strikeouts +0.480 bias, proj 5.46 vs actual 4.98, n=88 —
same direction, still thin). B3's Poisson/NB wrap INHERITS this bias by
design (mean pinned to the existing, biased point estimate) — expected and
correct for an additive wrap; B4 is where this actually gets fixed.
RAN THE FIT ON REAL DATA (2026-07-10, gate-kit session): fit_role_innings.py
filtered to model_version=dab23f4fbac8 -> 56 usable rows -> opener n=3,
bulk n=6, both under min-n=15 -> DO-NOT-GATE-YET, correctly refused to fork
the hash on unfittable magnitudes. The B4 role->innings CONSTANTS remain
unvalidated placeholders (opener 1.5 / bulk 3.5 / cuts 0.20,0.80 / clamp
4.0-7.0). NEXT: keep collecting forward K pairs via run_slate.py (the only
lever — no reconstruction shortcut, see DATA COLLECTION NOTES); re-run
fit_role_innings.py periodically; once both roles clear min-n=15, proceed
per B4_GATE_RUNBOOK.md.
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
STRAY PARTIAL EXPORT (found 2026-07-10, C1 session): prediction_outcomes2.csv
(135 rows, missing model_version and all actual_* detail columns) surfaced
as an upload during C1 work. Not present in git status as of that session,
so likely an ad-hoc local export rather than a repo file — verify before
acting, and delete/move it out of data/ if a copy exists, to avoid a future
"which CSV is authoritative" mistake.

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
src/prediction/pycache/.pyc — those .pyc files were tracked BEFORE the
gitignore rule existed, and gitignore only stops NEW files from being
tracked, it does NOT retroactively untrack existing ones. Fixed with
git rm -r --cached src/prediction/pycache (removes from tracking,
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
src. as top-level modules). Library code (gbm_calibrator) lives in
src/learning/; the runners use a try/except import shim so they work whether
co-located or split.
Smoke-test discipline pays: 1-date/1-fold smoke runs before every big run
caught real issues cheaply. Offline harness -> smoke -> full run -> commit.

OFFLINE HARNESS IMPORT-PATH BLIND SPOT (learned 2026-07-10, B4 circular
import). A check_*_offline.py that imports the thing under test DIRECTLY
(e.g. from src.prediction.role_innings import ...) can pass 24/24 while the
module is BROKEN when imported THROUGH the real package graph (e.g.
import src.data.mlb_api, which pulls the prediction+learning package
init chains). B4's harness did exactly this and missed a top-level
circular import that made import src.data.mlb_api raise. RULE: every offline
harness for a module that lives inside a package should also do a plain
import <the_actual_production_module> (the one real callers import), not
just import the class in isolation. One line; would have caught it.

VERIFY PROVENANCE STAMPS LAND, don't assume. model_version() returning the
right hash in isolation does NOT mean pairs get stamped — the stamp can be
lost at the WRITE path. The ACTUAL BUG 2 cause was an off-by-one column slice
in _actual_detail_fields that included model_version in a blank dict and
row.update()'d over the stamp (fixed; see [BUG2]). Other plausible write-path
losses to watch for: a schema migration with DictWriter(restval="") that
rewrites rows without re-stamping, or a recorder built without config. A
write-path guard now raises on any blank stamp, so a silent recurrence is not
possible — but still spot-check the CSV periodically:
python -c "import pandas as pd; d=pd.read_csv('data/learning/ prediction_outcomes.csv',dtype=str,keep_default_na=False); print(d.groupby(['category',d['model_version'].replace('','<blank>')]). size())". Any <blank> = the stamp isn't landing. scripts/
diagnose_k_pairs.py does a fuller read-only drop-attribution + version audit.

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


-----------------------IGNORE----FOR ME----WHEN TO START USING LIVE----

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