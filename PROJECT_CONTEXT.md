PROJECT_CONTEXT.md — v17 (master roadmap) — paste into any new chat

===============================================================================
SHARED PA AWS COMPARATOR AUTOMATION (2026-07-22) — CODE READY, NOT DEPLOYED

The separate T-4 comparator layer now has isolated AWS preparation, market,
finalization, and health services. It reads the existing pitcher-receipt and
shared-PA ledgers only; neither collector nor protected local process 20872 was
restarted, altered, or retimed. Prediction generation is allowed only from
T-45m through T-5m, the single three-market provider response only from T-3m
through T-4, and finalization only after a five-minute receipt grace. A missed
window becomes an immutable terminal record and can never trigger a backfill.

Frozen Hits/HR and separate structural Total Bases archives are published as
atomic immutable trees. Provider events, exact game identity, and all three
market keys are also one atomic tree. Final records require receipt-proven
pitcher identity and bind the exact automation manifest and a fully hash-locked
Python dependency environment. Missing provider evidence remains an explicit
non-probability record and cannot erase valid frozen model evidence. Outcome-
free health reports expose missing credentials and overdue terminal coverage.

  readiness report:
    reports/shared_pa_comparator_aws_automation_readiness_v1.json
  report sha256:
    a6e3a6a5daf00fee26b289a6c63d169fbfee7eea967b888f1cfd4de8be1fc03a
  release manifest:
    config/shared_pa_comparator_release_manifest_v1.json
  runtime sha256:
    4e48f563f0817bcbd5a81510cd399a7d264b46fd4556e31cf79adb19258ffde9
  dependency lock sha256:
    4de9e599070a7cfb96eaa9e742d7fe577d7ae011113a664a747361b0bb543e20

Each market line has its own terminal state, so a missing 1.5 line cannot
erase a valid 0.5 line or be rescued by it. Simultaneously open horizons reuse
one full-slate generation rather than risking deadline loss through duplicate
model work. Verification is 45/45 focused and 261/261 full repository tests, plus the
offline release and 32-file hash-manifest gates. No real provider request,
outcome read, May access, backfill, model fit, coefficient change, promotion,
or betting authorization occurred. Proven probability improvement remains 0%.
The code is committed and pushed in draft PR #14, but is not yet reviewed,
landed, or deployed; AWS provider-key presence is also unverified. The next
action is prerequisite PR #13 landing, then PR #14 review and exact-commit
installation followed by a future-only non-economic smoke.

===============================================================================
SHARED PA COMPARATOR CAPTURE READINESS (2026-07-22) — NOT DEPLOYED

The separate shared-PA comparator layer now has a locked, fail-closed T-4
contract for Hits, HR over 0.5, and Total Bases. Frozen batter archives now
carry the exact opposing-pitcher MLB ID consumed by the feature bundle, and a
comparator probability is usable only when that ID equals the immutable
receipt-proven probable starter for the opposing side. Missing or conflicting
identity remains unassessable; no name match, eventual starter, deletion,
clipping, or league-average substitution is permitted.

The supported production `run_slate.py` path remains unchanged. Total Bases is
generated only by a separate unpromoted structural runner and separate archive.
Regression proves that appending this output leaves the existing Hits, HRR, and
HR projections exactly unchanged. The market layer requests the three locked
provider keys but accepts a probability only from a unique DraftKings Over and
Under at the same event, player, line, and timestamp. One-sided HR, cross-book
pairing, late evidence, duplicate SIDs, unmatched players, and identity drift
receive explicit non-probability dispositions. Publication is write-once and
hash-bound.

  readiness report:
    reports/shared_pa_comparator_capture_readiness_v1.json
  report sha256:
    3dfcb5ec8109accb9c1654a743c3cc6b7687c9a9a172b69962591c243869de36

Verification is 31/31 focused and 241/241 full repository tests. No real
provider request, outcome read, May access, backfill, model fit, probability
coefficient change, production promotion, or betting authorization occurred.
This is code readiness only and is not yet deployed. The highest-value next
action is an exact reviewed release and a separate AWS non-economic T-4 smoke;
the existing pitcher and shared-PA collectors must remain untouched.

===============================================================================
SHARED PA ADJUDICATION READINESS (2026-07-22) — NO MODEL PROMOTION

The shared batter PA forward control is deployed separately on AWS from the
pitcher-receipt collector. Its purpose is fresh point-in-time research evidence;
it is not a production promotion and does not authorize betting.

An outcome-blind prospective adjudication boundary is implemented for Hits,
HR over 0.5, and Total Bases. It requires an exact 56-date population from
2026-07-23 through 2026-09-16 and forbids opening outcomes before
2026-09-17T12:00:00Z. Failed or incomplete dates cannot be skipped, replaced,
or backfilled. May 2026 remains fully sealed.

The adjudicator derives each scored target from a hash-bound official final-feed
projection; recomputes game/team/side/player identity, count algebra, and Total
Bases; binds the exact consumed probabilities to the T-4 horizon; uses the
candidate-fixed top-decile cohort for every HR comparator; and requires binary
Brier/log-loss materiality at every declared line. All 56 calendar dates require
an explicit disposition. Synthetic regression/mutation coverage is 37/37 and
the full repository suite is 211/211.

  readiness report:
    reports/shared_pa_forward_adjudication_readiness_v1.json
  report sha256:
    69f1bad3692cb6d1f2a0b53bc37f5d2f02872a48e708e42362562d656f24b128

This is an evaluation-integrity improvement, not a measured probability
improvement. No new batter-only challenger has earned selection, production is
unchanged, and no market is promotion-eligible. The shared AWS lane currently
captures the empirical-Bayes control but not every required exact T-4 frozen
production or receipt-verified market comparator. Missing comparator evidence
must remain missing; it cannot be reconstructed after the horizon.

===============================================================================
AUTHORITATIVE AWS RECEIPT HARDENING (2026-07-22) - READY, NOT DEPLOYED

The fresh-evidence boundary remains unchanged: all admissible batter-only
families have spent their 2024 selection and remain rejected, 2025 HR
confirmation is spent, May 2026 remains sealed, and no missed T-minus-4 receipt
may be backfilled. A new model candidate is still forbidden until genuinely
fresh receipt-bound evidence exists.

The AWS T-minus-4 pitcher-receipt release was re-audited before deployment and
six operational integrity defects were repaired at source. Official no-game
responses now produce a verified zero-target receipt instead of crashing. A
plan fetch that completes after T-minus-4 is rejected. The systemd cadence now
matches the locked 15-second runtime. Source-error and missed terminal states
are alerts rather than false health successes. An hourly read-only GitHub
workflow independently replays retained raw schedule bytes through the exact
plan, source receipt, ledger, and health chain without fetching replacements.
Deployment is isolated from the full model/odds collector and accepts only an
exact authorized commit with a pinned GitHub host key and clean-release checks.

Validation passed: AWS regression/mutations 16/16; context 10/10; ledger 7/7;
collector 6/6; runtime 7/7; combined full pytest 125/125; read-only verifier
4/4; combined forward-shadow readiness 225/225; Python compile, workflow YAML,
installer shell syntax, and every certificate-bound SHA-256 passed. No model,
price, lineup, outcome, settlement, or May artifact was accessed. Local process
20872 and operational smoke were observed only and remained untouched.

  certified hardening report:
    reports/aws_pitcher_receipt_automation_hardening_v2.json
  report sha256:
    f81e97d4fb3e37b095d3b046b6e92abb8580b8e5a1346e7c1def3181aa86b652

Status: `CERTIFIED_CODE_READY_NOT_DEPLOYED`. Betting remains unauthorized. The
single highest-value next action is exact-head landing followed by exact-commit
AWS installation and verification of the first future-only plan/tick/health
cycle. Do not deploy a moving branch and do not backfill a missed date.

===============================================================================
CURRENT HANDOFF ADDENDUM (2026-07-21) — SUPERSEDES OLDER HEADLINES ON CONFLICT

Absolute rules: research only; no betting authorization; May 2026 sealed;
never lower the +0.10 capture lower-bound gate; never treat historical prices
as executable; never use postgame facts as pregame features; never pool markets
or products; never fabricate a fallback.

Current main checkout: C:\Projects\baseball_predictor on branch
codex/hits-forward-evidence-release at commit 0406acf. Its supported daily
command is `python run_slate.py --date YYYY-MM-DD --config config/config.kbb.json`.
This runs the frozen K/BB baseline for Hits, HR-related hitter projections, HRR,
and pitcher strikeouts. It deliberately excludes exploratory/rejected HR work.
Never use `--apply-corrections` in the frozen evidence period; manual runs must
use a separate `--archive-dir`.

2026-07-21 HR research is isolated in
C:\Projects\baseball_predictor\.codex-provenance-foundation on branch
codex/pregame-provenance-foundation (commits 074ffdb, 17d331f, af14620,
5f921b1). It is not production code. The strictly-prior 30/120-day Statcast HR
challenger showed only small 2025 simple-baseline gains (Brier about -0.000230,
log loss about -0.001475), below its predeclared materiality gate; it is
rejected and its 2025 confirmation cannot be reused as independent proof.

Current readiness: about 5/10 overall; Hits about 5/10 and still below the
locked +0.10 capture lower-bound gate (candidate lower bound about +0.0503);
HR over 0.5 about 3/10. No policy qualifies and no market is authorized.

The current highest-value direction is a shared fitted batter PA-outcome
foundation for Hits, HR over 0.5, and Total Bases, adjudicated separately.
Batter-only features may proceed only when their timing contract passes.
Pitcher-matchup features require receipt-proven probable-starter identity; if
unavailable, exclude that block rather than using a guessed or actual postgame
starter. RBI/HRR require a separate point-in-time run-context layer.

Fit on 2023 and select on 2024. Do not reuse spent confirmation data as new
independent proof. Fresh untouched confirmation or forward evidence is required
for a new candidate. No hand-tuned baseball coefficient, multiplier, or
performance threshold may be introduced.

The local 2023-2025 Statcast cache has 2,145,072 regular-season pitch rows,
710,217 EV/LA batted-ball rows, 371,987 spray-coordinate-ready rows (52.38% of
BBE), and 1,302 distinct pitchers with complete pitch context. This is enough
for research foundations, not proof of a blanket spray feature or a pregame
pitcher-matchup model.

Operational smoke/collector evidence is separate and permanently non-economic
unless a clean authorized economic era is created. Do not backfill missed
prospective observations. Missed days may be reconstructed only as labelled
historical research with predictions hash-locked before official outcomes; they
cannot count as executable-price, forward ROI, or authorization evidence.

Formal goal status in the app is ACTIVE under the strengthened shared batter
PA-outcome objective. Do not falsely mark it complete; the batter-only and
probable-starter fail-closed rules remain mandatory.

===============================================================================

Keep this file in the repo. Start a FRESH chat per task, upload this (plus the
repo/zip if code work is needed), and say which roadmap item to work on.
Update "Current status" and check off roadmap items as they complete.

===============================================================================
CANONICAL STATUS (2026-07-17) — THIS SECTION SUPERSEDES OLDER STATUS HEADLINES

ABSOLUTE END GOAL

Produce a market-specific betting authorization supported by verified settlement
rules, executable prices, a locked model and policy, an untouched held-out test,
positive ROI uncertainty bounds, and prospective forward-shadow replication.

The project remains RESEARCH_ONLY. No current artifact authorizes wagering.
Heuristic confidence, point estimates, pooled markets, historical vendor result
values, or an attractive backtest cannot authorize a bet.

PROGRESS SCALE

  10 = the first legitimate, market-specific betting authorization. This is
       evidence-backed permission to act under a locked contract; it is not a
       guarantee of profit or an assertion of zero risk.
  12–15 = post-authorization excellence: replicated profitable markets,
          operational uptime, real fill/void tracking, drift detection,
          independent audits, and proven capital/risk controls. These levels
          cannot be claimed before level 10 exists.

Honest current assessment: approximately 5/10 overall. Hits is the most mature
market at roughly 5/10. HR over 0.5 is roughly 3/10. This reflects evidence
readiness, not subjective model confidence.

-------------------------------------------------------------------------------
TUESDAY EVIDENCE MILESTONE — UPDATED AFTER a3.2, NOT AN AUTHORIZATION

  goal declaration:
    GOAL_TUESDAY_MARKET_AUTHORIZATION.md
  canonical updated report:
    data/analysis/tuesday_market_authorization_2026-07-21_v2/report.json
    sha256 8670a3a9353f022e8b013d450ef614681cb5384e12509b94d694903d7025abca
  human-readable report sha256:
    9242649701e967f9bd9c38addee30e9bed5f92311418f601baa8b2bf24202153
  evidence binding sha256:
    e10d8c4ec705e21dea42b6d996bd003a4f992d26c9ac4845576ca27b0924e0d5

The updated package preserves the original Tuesday report by hash, adds the
completed corrected a3.2 HR branch, and refuses overwrite. The milestone
maximized honest evidence without weakening a gate. Its result is still
research-only.

-------------------------------------------------------------------------------
HITS — CURRENT RESEARCH BASELINE

  * The K/BB candidate remains the preserved research baseline.
  * Open-period candidate capture: approximately +0.0480416.
  * Open-period flat-stake ROI: approximately -0.0341635.
  * The locked policy fitter returned NO_POLICY_QUALIFIES.
  * The inherited +0.10 lower-bound gate was not lowered.
  * No Hits betting authorization exists.

The next irreplaceable evidence is prospective: executable T−4h prices, locked
model output, closing prices, official outcomes/voids, and real operational
capture. Historical work remains useful for diagnosis, but cannot substitute for
forward shadow evidence.

-------------------------------------------------------------------------------
HR OVER 0.5 — CORRECTED a3.2 BRANCH COMPLETE AND REJECTED

The retired a3.1 source remains preserved. The corrected a3.2 branch is
isolated and uses original batting-order sequence-zero starters rather than
final lineup occupants. It rebuilt 615 regular-season dates spanning 2023–2025:
7,289 games, 131,202 hitters, and 14,578 pitchers. Statcast enrichment and exact
assembly preserved all 131,202 hitter rows.

  migration protocol sha256:
    3476d59538f1027150e44fd8721cafae497cbabc96f3052dec18c8f423df8bc0
  assembled hitter artifact sha256:
    3fc38325007845d6a7a99102274f7e520ed6f4310cc2a3248ebc441afeb814c5
  corrected PA artifact sha256:
    d61818e0fd4cfb70e40fdb240dc372ee5872b43263157ca1b23ee30cb3983570
  signal-screen report sha256:
    6aa505e5d31b9a574b742c30563762669bafe65a1003f8777cbb7f58c6d75970

The PA model was refit on corrected 2023–2024 starters. The separate
2023-fit / 2024-select / 2025-confirm signal screen passed only the right to
reconstruct; it did not authorize installation or betting.

The locked 24-date 2025 reconstruction then completed and validated exactly:
30,525 total simulation rows and 5,724 HR-over-0.5 rows/features. The full
validation report is research-only and did not inspect performance.

  reconstruction-date artifact sha256:
    7612e62a930f73c70a8af2836e049b6a367fd8318ae5ecb5e5c3b6cfced58afb
  reconstruction protocol sha256:
    413a645cb7bc56c4a1b760eb305f9a4061e9c61f2045e06a8607dbc8b91baf25
  full validation report sha256:
    a6bcafba62db3819ec9a074d47d61147e82f8dea1dcbbf6e6fcbcbff9ab0d96a

The mapping method was locked and mutation-tested before confirmation. It fit
12 calibration dates, then opened the remaining 12 confirmation dates exactly
once. Confirmation contained 2,826 rows. Point estimates improved slightly,
but the candidate failed the locked proof requirements:

                           raw       control   candidate
    Brier score          0.106662   0.106322   0.106248
    log loss             0.369836   0.367443   0.367092
    AUC                  0.573726   0.573726   0.576488
    absolute bias        0.011460   0.005034   0.006632

Candidate bias was worse than the calibration-only control. The paired-date
Brier and log-loss upper uncertainty bounds crossed zero versus both raw and
control. Therefore the candidate was rejected. No subgroup, threshold change,
2026 open-period experiment, or installation may rescue it.

  implementation contract sha256:
    9cf3b965d1763e805a46ff87b58d1524fe85aa7bb52e8cd0a642893253f94272
  calibration lock sha256:
    22bdc22515fa94c7ff415d22dd0b79341fc0bf16110c69a480f65a3b7994c8d9
  confirmation report sha256:
    580f53339ea561618c50be9b0cf004ddd795c6515989ffcb3bc7e106b19bca6b
  rejection record sha256:
    003396bf92364ba4b26cb47b6f53f5ee3edcdf10bd561310b63a5c21a9c92697
  confirmation validation sha256:
    4d08a1c8f3909e24087dd3cce8a548e2be4a063d1cde2ddc4edcc8df9c31e4d3

May 2026 remained sealed. Betting remains unauthorized.

-------------------------------------------------------------------------------
FORWARD-SHADOW FOUNDATION — DEPLOYMENT-READY LOCALLY, NOT DEPLOYED

The DraftKings Hits entry lane now has a real The Odds API v4 adapter and an
unattended one-minute service state machine. Local contracts enforce:

  * future-only schedule planning with exact MLB gamePk/team/start identity;
  * automatic pre-target plan supersession when official start identity moves;
  * hard failure when schedule drift occurs after evidence exists or T-4h is due;
  * fresh target-specific K/BB Hits prediction provenance, never a reused archive;
  * capture begins only inside the locked 120-second pre-T-4h operational window
    and completes no later than T-4h;
  * real DraftKings batter_hits Over/Under at the same line only;
  * exact provider event/outcome ids, hard MLB identities, and no fuzzy player join;
  * exhaustive candidate funnel accounting, including missing model rows;
  * retained raw bytes, quota receipts, portable target evidence, credential
    redaction, atomic publication, restart idempotence, and no backfill;
  * a deterministic selected/no-selection funnel and append-only, hash-chained
    research ledger committed before the decision horizon;
  * a separate exact prestart reference for every captured MARKET_KEY, explicitly
    not represented as a guaranteed last price or actual fill;
  * official MLB final-feed retention and modeled DraftKings reference
    graded/void/unscored dispositions, with vendor result values disqualified;
  * separate fail-closed Onyx, Novig, Chalkboard, and PrizePicks execution
    contracts; DraftKings remains reference-only in the declared Texas context;
  * an hourly GitHub verifier/90-day artifact backup that fetches evidence through
    a separate read-only SSH identity, verifies the whole lifecycle and ledger,
    and never fetches replacement odds.

Relevant implementation:

  run_shadow_collector_tick.py
  run_shadow_primary_collector.py
  src/evaluation/shadow_live_provider.py
  src/evaluation/shadow_capture_plan.py
  src/evaluation/shadow_provider_adapter.py
  src/evaluation/shadow_target_capture.py
  src/evaluation/shadow_lifecycle.py
  src/evaluation/shadow_ledger.py
  src/evaluation/shadow_official_hits.py
  src/evaluation/execution_product_contracts.py
  run_shadow_close_collector.py
  run_shadow_official_settlement.py
  scripts/verify_shadow_capture_tree.py
  scripts/verify_shadow_lifecycle_tree.py
  scripts/check_shadow_lifecycle_tree_offline.py
  scripts/check_tracked_secrets.py
  .github/workflows/shadow-evidence-verifier.yml
  deploy/shadow_collector/

Offline readiness is 223/223. The refreshed readiness artifact is:

  reports/forward_shadow_readiness_v27.json
  sha256 b7b7681bebc540210231b37d821f002bc2217e81e8945f58594ed0a41516b82f

The durable collector no longer writes its working daily prediction archive to
the Git-tracked `data/learning/predictions/` path. `run_slate.py` now accepts an
output-only `--archive-dir`, and the collector binds it to
`<service-root>/generated_predictions/`. The model call, configuration,
decision-time provenance, feature persistence, categories, and T-4h checks are
unchanged. Mutations prove both the CLI handoff and the absence of a write to
the tracked archive path. This prevents a clean forward-evidence checkout from
invalidating itself on its next tick.

Release preflight found and closed one additional fail-open seam: the economic
evidence clean-tree check had ignored nonignored untracked files. It now treats
such source/configuration as dirty, and the mutation is covered by the 195
guards. An untracked runtime override can no longer enter a supposedly clean
evidence era. Six additional era guards now bind a secret-free exact runtime
manifest: Python executable bytes/version, platform, OpenSSL, and all installed
distribution versions. Package or interpreter drift is rejected before a
credential prompt or provider call. Scope validation also independently compares
the running Git HEAD and full clean-tree state against the frozen source commit;
recorded provenance fields are no longer trusted without observing the same
release checkout.

The release also binds `.gitattributes`, with portable LF text normalization
for source, configuration, contracts, and reports. Exact readiness hashes must
survive a clean Windows or Linux checkout instead of depending on one
worktree's line-ending settings.

The first prospective economic look is also predeclared and mutation-tested in
`config/forward_shadow_evidence_boundary.json`. It requires 56 complete future
official-date blocks under one frozen evidence era, derived only from the 37
open March-April and 19 open June Hits dates. Operational smoke, incomplete
dates, and May 2026 cannot count. Before that boundary, official outcomes may be
retained and hash-verified but capture, ROI, calibration, subgroup, and other
economic summaries must not be produced or inspected.

The operational smoke and the later economic era now require separate immutable
`evidence_scope.json` artifacts. The collector refuses to start without one.
The smoke scope is permanently `economic_evidence_eligible=false`; the economic
scope requires a completed smoke certificate, a clean release checkout, and an
exact match to every bound readiness hash. Any material drift requires a new
era rather than continuing the old denominator.

This is code readiness, not operational deployment. The previously exposed key
has been rotated and the replacement passed provider authentication without
being retained. Before durable deployment: explicitly select/authorize the host
and provider plan, configure the host secret plus GitHub's separate read-only
SSH credentials, and complete one future real successor shadow smoke that is
excluded from economic evidence. The complete T−4h-to-prestart-to-official-
settlement-to-ledger lifecycle exists and is mutation-tested locally; operational
deployment, exact execution-product evidence, and accumulated prospective
replication remain downstream work. No forward-shadow authorization exists yet.

A no-cost local Windows v11 smoke scope was prepared and independently validated:

  data/learning/shadow/operational_smoke_v11/evidence_scope.json
  scope_sha256 4088a7bda12738371e3a9a2a14b22886a824cf765fd068d0217a016113485b11
  file_sha256 0d5fefe301ef0c8353a1d40f3b7e22fc74c509ec02a8ec426f8e51337f9b136e

  data/learning/shadow/operational_smoke_v11/runtime_manifest.json
  fingerprint_sha256 55747f459e86b04740ca74f75750b5d9ab140582f5b3f54619a0f314357c5b94
  file_sha256 76cf3169b89d4796380e6707df6ec1789506b39063d0de9848f553f8a7bd6585

The user rotated the previously exposed credential and entered its replacement
through the runner's hidden prompt. The replacement value was not retained or
logged. v11 made one provider request: the event-list receipt returned HTTP 200,
proving provider access, but the first T-4h target permanently failed before any
quote was resolved. The provider start was 60 seconds later than the official
MLB start, so the former exact-start identity contract rejected the otherwise
exact home/away event. v11 was stopped immediately and is permanently
non-economic, non-retryable, non-backfillable, and not certifiable.

The retained outcome-blind v11 slate contained 16 MLB games and 16 provider
events. Exact normalized team-pair mappings resolved all 16; 15 start deltas
were +60 seconds and one was zero. No provider prices, model probabilities,
official outcomes, or May data were inspected. The diagnostic is retained at
`reports/v11_event_identity_offset_diagnostic_2026-07-18.json` with SHA-256
`053b8b2e2227fd9baeef45277a1550157d5020714f4751cc1f7d8b3fb754f795`.

The incompatible successor event identity requires exact normalized home/away
teams, equal team-pair cardinality, and unique chronological ordinal for
doubleheaders. Provider and official starts remain separate facts. The measured
60-second value is a hard rejection bound only; it never chooses a nearest
event. Missing, extra, tied, repeated, reversed, ambiguous, or 61-second cases
hard-fail. Runtime v2 verifies the diagnostic hash and equality of the bound
before any provider request. The complete local readiness suite is now 223/223.

The corrected successor is now the single active v13 local operational smoke:

  clean release root
    .codex-release-v13
  source commit
    a6374ca7b3154a50b3a389e5ad2eda2e5634426f
  fixed official date
    2026-07-19
  immutable plan
    16 targets / 16 unique MLB game PKs
    plan SHA-256 e3e1d4b706363f2c90e8a36518dd3a453f377c49b0fa406f01d5ba97b7c9daf9
  first and last entry targets
    2026-07-19T12:15:00Z through 2026-07-19T19:20:00Z
  evidence-scope file SHA-256
    ce03881079eb0a8c7b1bf47ea55bac8aa270b32736fae677bde3b80d0903a145
  internal evidence-scope SHA-256
    237c9b3c505527b1e2ab4469419dc0c4fc30bf940e70a531c60e742ecd67f853
  runtime-manifest file SHA-256
    ec2d0237113d6132813a194ad091e0828b5ba0638e3394cc27fe94a1e6e75099
  runtime fingerprint
    55747f459e86f04740ca74f75750b5d9ab140582f5b3f54619a0f314357c5b94

The current 16-target plan superseded the earlier 15-target plan at
`2026-07-18T21:02:29Z`, before any old target was due and before any terminal
evidence existed. The collector retained the old plan under `superseded/` and
recorded the transition in a `shadow-plan-supersession-v1` artifact. This is a
valid pre-capture official-schedule identity update, not a retry or backfill.

At the time of this context update, v13 is running normally but has no due
targets, terminal receipts, source errors, or ledger rows. That is the expected
pre-window state, not completion. Keep its PowerShell process open and the
computer awake. Never start a second copy, retry a target, backfill a miss, or
certify early. The exact full-day schema-v2 certifier may run only after the
entire entry, prestart, official-final, settlement, and ledger lifecycle is
complete. v13 remains permanently `economic_evidence_eligible=false` and cannot
authorize wagering.

The Tuesday report now retains v11 as a permanent failed gate and accepts v13
only as a separate successor. A handcrafted `verified=true` value cannot
advance it; the exact certificate chain is required. The current reporting
release also compares all 57 collector-critical v13 bound files before allowing
certificate transfer. All 57 currently match. A future mismatch blocks the
handoff and requires either an exactly compatible release or a new incompatible
smoke.

A post-freeze completeness audit found that `run_slate.py` can statically reach
56 local Python runtime files, while only 5 of those files are individually in
the 57-file readiness boundary. The remaining 51 include prediction,
simulation, feature, data, learning, and utility modules. This does not alter or
invalidate the running v13 smoke: its entire release checkout remains clean at
the exact recorded commit. It does mean that bound-file equality alone is not
sufficient evidence for transferring a smoke certificate to a later commit.
The Tuesday reporting handoff therefore also compares the complete tracked Git
tree between the smoke commit and reporting release and permits only an exact,
named reporting-only allowlist. Any other tracked change is a material runtime
change and fails the transfer closed. No future economic era may rely on the
57-file comparison by itself.

The full-day schema-v2 certifier is tracked in the v13 source commit but is not
one of the 57 individually listed readiness files. The successor reporting gate
therefore independently observes the actual smoke checkout and requires both
the exact scope-recorded Git commit and a completely clean tracked/untracked
tree. A certificate from a dirty or different checkout is rejected even if its
JSON fields and the original 57 hashes appear valid.

Until v13 certifies, no forward-economic era may be created. After it certifies,
a clean hash-bound release and a durable external primary remain mandatory;
GitHub remains verification and alerting only, never the sole collector.

-------------------------------------------------------------------------------
GITHUB / RELEASE STATUS (2026-07-18)

  * `codex/hits-forward-evidence-release` is the explicitly approved publication
    branch in NickNicky19/baseball-predictor. Query Git for the authoritative
    current source commit; do not copy a historical commit from this context.
  * The working tree is a large mixed set of source changes, artifacts, logs,
    caches, and unrelated scratch files. Never stage it with `git add -A`.
  * A tracked documentation example containing a plaintext-looking odds
    credential was redacted. The user attested that the compromised value was
    rotated. The replacement was entered through a hidden prompt and was not
    retained. The tracked-source scanner is green; Git history remains evidence
    that the retired value was once exposed.
  * GitHub CLI is not currently installed/authenticated on this machine.
  * GitHub is NOT fully updated until an explicitly scoped branch is scanned,
    committed, pushed, and reviewed through a draft pull request.

May 2026 performance remains sealed. Older aggregate inventory metadata may have
been read, but current work has not opened May prices, row-level performance, or
used May to tune a model or policy.

===============================================================================
CURRENT STATUS (2026-07-13) — *** THE MARKET EVALUATOR WAS GRADING AGAINST A
WRONG TARGET. EVERY CAPTURE NUMBER THIS PROJECT HAS PRODUCED IS RETIRED. ***

+0.002 (frozen), +0.040 (PA fix), +0.041 (K/BB) are NOT baselines and NOT
"improved from". They were scored against SmartStake's `result` column, which
disagrees with MLB's official actuals on 16.5% of the exact rows the A/B scores.
They are RETIRED. The rerun establishes a NEW baseline and says NOTHING about
whether the model got better or worse.

The model is UNCHANGED today. Nothing was promoted, nothing was refit. What
changed is that the EVALUATOR can now be trusted, and a large amount of what we
believed about the market data turned out to be false.

BETTING: NO. The run is RESEARCH_ONLY and cannot claim the +0.10 bar (see
POLICY below).

-------------------------------------------------------------------------------
THE HEADLINE FINDING: THE VENDOR'S `result` IS NOT TRUTH

run_pa_market_ab.py graded with `won_over = (result > line)` -- the VENDOR's
number. MEASURED (scripts/audit_result_integrity.py, June, DK, T-4h):

    eligible A/B selections      5,446
    DISAGREE WITH MLB OFFICIAL     896      *** 16.5% ***

AND IT IS NOT AN IDENTITY ARTIFACT:
    multi-fragment game_id   18.4% wrong
    single-fragment game_id   6.7% wrong   <- CLEAN games. ONE start_time.

Hard identity is still ESSENTIAL (it is how prices attach to games) but it
CANNOT repair the target. Two hypotheses died in the measuring:
  * ZERO games are wholly bad (>=90% of selections wrong). The corruption is
    SCATTERED WITHIN games, not concentrated in mis-joined ones -- so "graded
    against the wrong event" is DEAD.
  * The error is SYMMETRIC (47.3% over / 52.7% under). Not a correctable bias.
  * hits 16.8% wrong vs home_runs 6.4%. The MECHANISM IS NOT ESTABLISHED and
    none is claimed (rule 10).

THE REMEDY DOES NOT DEPEND ON THE MECHANISM:
    *** SmartStake supplies PRICES and SETTLEMENT PRESENCE. ***
    *** MLB game-keyed official actuals supply the SCORED TARGET. ***
The vendor's numeric `result` NEVER enters won_over, Brier, or capture. Two
truth sources in one pipeline is the bug, whichever one is wrong.

-------------------------------------------------------------------------------
`game_id` IS NOT A GAME KEY. The vendor README says it is. IT IS NOT.

MEASURED (scripts/check_game_id_stability.py, June, 243 game_ids):
    one start_time       69     <- only 28% are clean
    <= 5 min            120     <- drift
    6-90 min             14     <- +/-1min twin + a +60min low-volume fragment
    91-400 min            8
    *** 1020-1399 min    32 *** <- 17 to 23 HOURS under ONE game_id

Those ~23-hour spans are the plausible source of the ctrl4 leakage already on
record ("entry prices recorded 23 HOURS AFTER first pitch"). The bug was patched
at the consumer; THE DATA STILL CONTAINS WHAT CAUSED IT.

So `game_id` is closer to a MATCHUP key. Identity must be resolved at
(vendor_game_id, start_time) and mapped to mlb_game_pk by a vote of the
fragment's own players against MLB box scores.

===============================================================================
WHAT WAS BUILT (2026-07-13)

  src/evaluation/market_eligibility.py     THE SHARED DEFINITION of the A/B's
                                           universe. One callable, imported by
                                           every consumer. Freshness lives HERE.
  scripts/build_crosswalk_v3.py            vendor -> MLB identity. A MAPPING.
  scripts/build_strict_unique_hits.py      the research artifact for the A/B.
  scripts/run_market_ab.py                 MLB-truth grading, per market.
  config/ab_policy.json                    what a bet IS, with each parameter's
                                           ORIGIN. Placeholders => research-only.

  scripts/audit_result_integrity.py        vendor `result` vs MLB official
  scripts/audit_duplicate_keys.py          duplicate MARKET_KEY: exact or real?
  scripts/audit_market_availability.py     book x market x side, at the contract
  scripts/audit_settlement_agreement.py    vendor null vs DK's void rule
  scripts/check_game_id_stability.py       is `game_id` a game key? (no)
  scripts/check_mixed_nullness.py          does the settlement filter move prices?
  scripts/diagnose_fragments.py            every fragment, every pair, 3 axes

  HARNESSES (all green):
  check_one_denominator_offline    19/19   one universe, incl. PRICES
  check_strict_unique_offline      10/10   both rows of a duplicate excluded
  check_crosswalk_v3_offline       10/10   a non-scoreable extra must NOT fail
  run_market_ab --self-test        19/19   the target contract

-------------------------------------------------------------------------------
THE JUNE ARTIFACT — hand this to the reconstruction

  data/market/v3/strict_unique_hits.csv           3,386 rows
  data/market/v3/strict_unique_hits_manifest.json sha 0a211a217faeccdc

  THE FUNNEL:
    eligible                6,461
      settlement_absent       892
      stale                 1,912   <- the evaluator drops these
    SCOREABLE               3,657
      crosswalk-unmapped       85
    HARD-MAPPED             3,572
      duplicate MARKET_KEY    186   (93 keys, *** BOTH rows excluded ***)
    STRICT-UNIQUE           3,386   (92.59% of scoreable)

  *** THE DATE UNIVERSE IS 19 DATES (2026-06-01 .. 2026-06-19), NOT 21. ***
  The crosswalk's accepted_date_universe says 21; two of those have no
  strict-unique survivors. The reconstruction must cover the MANIFEST's
  official_date_universe. Reconstructing from the wrong list is how a run
  silently scores a different universe than it thinks it does.

  The exclusions are CONCENTRATED, not diffuse: 13 of 19 dates are 100% clean;
  2026-06-15 is the worst at 63.7%. A date-block bootstrap will feel that
  unevenly.

===============================================================================
OPEN ITEMS (2026-07-13) — in priority order

#1  RUN THE CLEAN JUNE A/B.  *** BLOCKED ON THREE ARTIFACTS. ***

    Codex produces, for EXACTLY the 19 dates in the manifest:
      frozen / candidate : mlb_game_pk, player_id, category, line, sim_p_over
      official           : mlb_game_pk, player_id, game_date, category,
                           actual_value      (the column is actual_value,
                                              NOT actual)

    python scripts/run_market_ab.py \
      --frozen <...> --candidate <...> --official <...> \
      --crosswalk data/market/v3/crosswalk_players.csv \
      --policy config/ab_policy.json --months 2026-06

    The runner HARD-FAILS if the arms do not cover identical MODEL_KEY sets, or
    if any scoreable row has no official target. By design.

    *** JUNE FIRST, NOT MAR-JUN. *** March/April/May fragment ~33% harder than
    June (frag_ratio 2.67 / 2.72 / 2.03 -- MEASURED) and nobody knows what that
    does to the duplicate rate. Every error caught today was caught by measuring
    small before measuring big.

#1b PROMOTE `settlement_presence_rule` OUT OF PLACEHOLDER.
    *** THE CHEAPEST AND MOST CONSEQUENTIAL OF THE FOUR. ***

    The runner excludes on the VENDOR's null. That has NEVER been compared to
    DK's actual void rule, which has THREE clauses:
        1. a STARTER with pa == 1   -> VOID
        2. ANY SUBSTITUTE           -> VOID   (regardless of pa)
        3. *** THE UNDER SIDE IS EXEMPT ***

    scripts/audit_settlement_agreement.py compares them -- SIDE-AWARE and
    ROLE-AWARE -- and it will REFUSE TO RUN today, because the training set has
    `out_pa` but NO STARTER/SUBSTITUTE COLUMN. A pa-only test ignores two of the
    three clauses, would produce a reassuring agreement number, and would be
    measuring A RULE NEITHER DK NOR THE CODE USES (rule 3). The refusal is
    correct: false confidence is worse than no check.

    TO UNBLOCK: add a starter flag to the training builder (the box score's
    battingOrder -- a starter has slot 1-9), rebuild, re-run.

    THE CELL THAT MATTERS is FALSE_INCLUSION: vendor GRADED it, DK would have
    VOIDED it, and the runner SCORED it as a win or a loss. That is not noise --
    void-eligible players (1-PA starters, substitutes) are NOT a random sample,
    so it is BIASED noise in the capture ratio.

    WHY IT MATTERS, ALREADY MEASURED: regrading hits 0.5 from all-rows to
    DK-gradeable moved the apparent bias +0.1146 -> +0.0687. *** ~40% OF A
    HEADLINE BIAS WAS A GRADING ARTIFACT. ***

#2  THE OTHER THREE PLACEHOLDERS (min_edge, max_quote_age, min_bets).
    They DEFINE WHAT A BET IS, and capture is computed ON BETS ONLY -- so an
    unjustified min_edge makes the headline a statistic about an ARBITRARY
    SUBSET. See config/ab_policy.json -> path_out_of_research_only.

#3  MAR-JUN. Only June is built. The graded range is Mar 21 - Jun 30 (~95
    dates; MEASURED -- March starts on the 21st, and July is 0% GRADED and
    therefore useless). Build after June proves out.

#4  HOME RUNS.  *** AND I WAS WRONG ABOUT WHY. ***
    HR produced zero rows and I asserted, in four files, that "an HR prop is a
    YES/NO market, so there is no under side, AT ANY BOOK."

    MEASURED (audit_market_availability.py, Mar-Jun):
        player home runs   over 65,168,992   *** under 17,503,855 ***
        TWO-SIDED AT 33 BOOKS (hard_rock, novig, bracco, pinnacle, ...)

    THE REAL REASON: *** DRAFTKINGS DOES NOT POST A TWO-SIDED HR MARKET. *** DK
    is in the table for hits and RBIs and NOT ONCE for HR. A BOOK-SPECIFIC GAP,
    not a market-structural one.

    HR is therefore POTENTIALLY EVALUABLE -- and it is a SEPARATE, GATED change,
    because three things are UNVERIFIED:
      1. SAME PRODUCT? A Yes/No priced both ways gives a "No" leg, and a No is
         NOT an over/under under. *** A COLUMN NAMED `under` IS NOT AN UNDER
         SIDE. ***
      2. SAME LINE? An alt-line 1.5 is a different bet from a 0.5.
      3. TIME-ALIGNED? A stale under with a fresh over yields a probability
         NEITHER BOOK EVER OFFERED.
    And pairing ACROSS books is not a de-vig at all -- it is a SPREAD BETWEEN
    VENUES. Switching venue for HR would change it for hits too, where the whole
    3,386-row result is priced against DK.

    NOTE, for anyone who would only ever bet the OVER: the under quote is an
    INSTRUMENT, not a wager.
        p_over_devig = (1/odds_over) / ((1/odds_over) + (1/odds_under))
    Without it you have 1/odds_over, which still carries the book's margin -- so
    you would compare the model against an INFLATED price and call the difference
    "edge". *** THE VIG IS THE THING YOU ARE TRYING TO BEAT. ***

#5  THE 93 CONFLICTING DUPLICATE KEYS (June).
    Two fragments, one MARKET_KEY, BOTH fresh, and EVERY key conflicts on at
    least one evaluator field:
        entry_p_over  median 0.0046  p95 0.0183  max 0.0499
        close_p_over  median 0.0075  p95 0.0285  max 0.0601
    max(entry gap) 0.0499 sits ABOVE min_edge (0.04) -- so ON THE WORST KEY,
    WHICH FRAGMENT YOU PICK DECIDES WHETHER IT IS A BET AT ALL.

    The strict-unique artifact EXCLUDES BOTH ROWS. 7.4% cost, quantified and
    boring. Boring is the goal. Choosing which price was "real" is a JUDGMENT,
    and this system exists to keep judgment out of identity.

    The MECHANISM is not established. Fragment separation is SECONDS, not
    minutes (the first report said "0 minutes" -- that was integer binning, not
    a finding).

#6  THE MODEL ITSELF IS UNTOUCHED TODAY.
    K/BB remains a TIE and UNPROMOTED. HRR's structural fix FAILED its gate and
    is INERT (so `runs`/`rbi` still must not be emitted -- they would bake a
    known-broken component into a real money line). xwoba_scale = 7.81 is still
    a red flag. bias_corrections are still OFF. total_bases is still the biggest
    unbuilt market. None of that moved. The EVALUATOR moved.

===============================================================================
POLICY — config/ab_policy.json   *** RESEARCH_ONLY ***

  min_edge                 0.04    placeholder
  max_quote_age            90      placeholder
  min_bets_for_capture     30      placeholder
  settlement_presence_rule vendor  placeholder
  entry_hours              4       predeclared_structural_UNVERIFIED
  book                     dk      predeclared_structural_UNVERIFIED

  The loader is an ALLOWLIST: only `fitted` and `predeclared_structural`
  authorize betting. ANYTHING ELSE -- a placeholder, an "UNVERIFIED", a typo, an
  origin nobody anticipated -- forces RESEARCH_ONLY. A DENYLIST silently permits
  what it has never heard of, and that escape hatch WAS LIVE until it was caught:
  promote the four placeholders and an unverified T-4h assumption would have
  authorized betting.

  *** HASHING A NUMBER RECORDS A CHOICE. IT DOES NOT MAKE THAT CHOICE
  LEGITIMATE. ***

  The run may establish a BASELINE and reveal where information is missing. It
  MAY NOT claim the +0.10 capture bar and MAY NOT authorize betting.

===============================================================================
THE STANDING RULES (non-negotiable)

1.  VERIFY, DON'T DEFER. Check a claim against the code/data before acting on it.
2.  NO HAND-PICKED NUMBERS. Fitted, or a flagged structural placeholder. Never
    chosen and quietly shipped.
3.  THE METRIC MUST SEE THE BUG. If it cannot distinguish the bug from its
    absence, it is the wrong metric.
4.  MUTATION-TEST THE HARNESS. A test that passes on broken AND fixed code
    guards nothing. Prove it fails on the bug.
5.  STUB AT THE RIGHT LAYER. Patch at the boundary, never inside the thing under
    test. If you mock what you are testing, you measure nothing.
6.  CHEAP TEST BEFORE EXPENSIVE RUN.
7.  NO STATISTIC WITHOUT A SANITY RANGE. State the plausible range BEFORE
    computing. Out of range => the METRIC is broken, not the model.
8.  THE ASSUMPTION UNDER A STATISTIC IS ITSELF A CLAIM. Say what each input MEANS
    before dividing by it.
9.  RULE 1 APPLIES TO YOUR OWN PREMISES. "I have not checked this" is a required
    sentence.
10. SEPARATE DIAGNOSIS FROM INTERVENTION. A diagnostic isolates WHERE, not WHY.
    Enumerate the competing mechanisms and check whether the measurement can
    distinguish them. If it cannot, the next step is another MEASUREMENT, not a
    patch.
11. MINIMUM JUSTIFIED COMPLEXITY. Implement the smallest mechanism that satisfies
    the verified contract and catches the measured failure. Every abstraction,
    option, and fallback must have a current consumer or named invariant. Fewer
    lines are not the goal; removing unjustified complexity is. Never remove a
    necessary identity, provenance, safety, mutation, or authorization guard to
    make the implementation look simpler.
12. SURGICAL SCOPE. Every changed line must trace to the requested outcome, a
    measured defect, or a required invariant. Preserve unrelated behavior,
    comments, and user changes. Remove only the orphaned code created by the
    current change; report unrelated cleanup opportunities separately.
13. PREDECLARE SUCCESS BEFORE INTERVENTION. Before implementing a fix or launching
    an experiment, state the expected success condition, failure condition,
    protected invariants, and the mutation that should distinguish fixed from
    broken behavior. Completion requires evidence for all four.
14. MANAGE UNCERTAINTY EXPLICITLY. Never silently choose among materially different
    interpretations. Verify from code/data when possible. If the choice would
    change the experiment, architecture, or authorized behavior and cannot be
    verified, stop and surface the alternatives and tradeoffs.
15. CONTENT-ADDRESSED MODEL RELEASES. Every output-affecting change creates a new
    model release whose authoritative identity binds the exact code snapshot,
    effective config, fitted/data artifacts, random seeds, market contracts, and
    prediction outputs. A human-readable version label is metadata, not proof.
    Evidence affected by the change is invalid until it is regenerated and its
    hashes validate; unaffected evidence may be reused only when inertness is
    explicitly demonstrated.
16. FORWARD MEANS CAPTURED FORWARD. Evidence is prospective only when the exact
    model output, executable market quote, selection policy, and decision-time
    provenance were immutably captured before the event. A historical or spent
    period can never be relabelled forward shadow after its outcomes are known.
17. AUTHORIZE EXACT MARKET CONTRACTS, NEVER CATEGORIES IN THE ABSTRACT. A contract
    names the book or platform, product, category, side, line semantics, scoring,
    settlement/void rules, decision horizon, and payout terms. Evidence for one
    contract does not authorize another, even when both display the same category
    name.
18. SPENT-DATA SUBGROUPS GENERATE HYPOTHESES, NOT AUTHORIZATION. Residual slices
    must use only facts available at the declared decision time and report sample
    size, uncertainty, and multiple-comparison risk. A feature or correction
    suggested by spent data requires a new model release and untouched confirming
    evidence.
19. MULTI-LEG PRODUCTS REQUIRE ENTRY-LEVEL EVIDENCE. Leg-level probabilities do
    not establish the expected value of a parlay or pick-em entry. Evaluate the
    exact payout schedule, promotions, void behavior, and dependence between legs.
    Never assume independence merely because it simplifies the calculation.

    SIMPLICITY MEANS using no more complexity than the verified problem requires
    while retaining every necessary guard. A shorter implementation is better
    only when it enforces the same contracts and fails on the same mutations. If
    it cannot see the bug, it is incomplete, not simpler.

-------------------------------------------------------------------------------
*** THE LESSONS THAT COST THE MOST TODAY ***

A DECLARATION NOTHING ENFORCES DECAYS INTO FICTION.
  * ab_policy.json v1 declared, under origin='predeclared_structural', that the
    runner applies DK's void regime (pa>=2, substitutes void, under exempt).
    THE RUNNER IMPLEMENTS NONE OF IT. No `pa` column, no role split, no side
    exemption. The artifact built to prevent unexamined assumptions CONTAINED ONE,
    IN ITS OWN CERTIFICATION FIELD.
  * The policy check was a DENYLIST (`origin == "placeholder"`), so an origin it
    had never heard of was SILENTLY PERMITTED -- including the
    `predeclared_structural_UNVERIFIED` I invented to flag two unproven claims.
    A marker nothing enforces is not a marker.
  * The runner now ASSERTS the declared settlement rule against the production
    SQL and hard-fails if they disagree.

A TEST THAT CHECKS IDENTITY BUT NOT VALUE PASSES ON A BUG THAT PRESERVES IDENTITY
AND CHANGES VALUE.
  Same selection, same MARKET_KEY, entry_p_over 0.3827 vs 0.5000 depending on the
  implementation. A key-set equality test passes CLEANLY on that -- and capture is
  a RATIO, so the numerator is silently wrong while the denominator looks healthy.
  Every cross-caller comparison now compares the FULL projection, unrounded.

A SHARED MODULE IS A CONVENTION. A TEST IS AN ENFORCEMENT.
  eligibility_sql was extracted to kill a two-denominator bug. It shipped, a test
  asserted 8/8 -- AND THE DRIFT WAS STILL THERE, one filter downstream: the runner
  applied max_quote_age AFTER the shared call, the crosswalk and the audit did
  not. I MOVED THE BOUNDARY AND LEFT A PIECE OF IT BEHIND, then wrote a test that
  only checked the part I had moved.
  THE COST: 2,898 "duplicate rows" -> 186. 1,449 keys -> 93. *** 94% OF THEM WERE
  NEVER DUPLICATES *** -- one fragment was STALE and the evaluator would have
  dropped it. And a "53% coverage cost" I stated as FACT was really 5.2%.
  Freshness now lives INSIDE the shared function, and every caller reads
  max_quote_age from the POLICY (a CLI default would recreate the drift sideways).

ORDERING IS NOT VERIFICATION.
  v1's crosswalk called drop_duplicates(subset=K) and THEN asserted uniqueness on
  K. The assertion COULD NOT FIRE. It printed "[OK] ... is UNIQUE" -- a property
  it MANUFACTURED. `drop_duplicates` is now BANNED in the strict-unique builder
  and the harness verifies that AT THE AST LEVEL, because the file documents the
  ban in prose and a grep cannot tell a call from the sentence forbidding it.
  (A live drop_duplicates DID survive the first draft.)

*** I INFERRED FIVE MECHANISMS FROM PARTIAL VIEWS. ALL FIVE WERE WRONG. ***
    "the extra fragments are stubs"      -> more than half were not
    "zero duplicate MARKET_KEYs"         -> 2,898 (pre-stale) / 186 (real)
    "they are exact re-posts"            -> 1,449/1,449 CONFLICTING
    "they carry different market histories" -> most were simply STALE
    "HR is one-sided at every book"      -> two-sided at 33 books
  The MEASUREMENTS were right every time. The STORIES were wrong every time.
  Rule 10 is not bureaucracy: a diagnostic isolates WHERE, never WHY.

  Corollary, and it bit twice: I READ ARTIFACTS OF MY OWN INSTRUMENT AS FACTS
  ABOUT THE DATA. `entry_age_min` differing on 100% of duplicate keys (median 582
  min) was not a finding about the vendor -- it was the freshness filter I had
  failed to apply. Fragment separation of "0 minutes" was integer binning of a
  SECONDS-level gap.

===============================================================================
STILL TRUE, AND STILL EARNING THEIR KEEP

  A CONTROL GUARDS THE FAILURE IT WAS DESIGNED FOR AND NOTHING ELSE. ctrl4
  (entry_age_min >= 0) caught entry prices recorded 23 HOURS AFTER FIRST PITCH.
  ctrl1/2/3 all PASSED on that fully-leaked data. Negative age remains a HARD
  FAIL, never a status.

  CAPTURE IS ON BETS ONLY. An all-rows denominator EXPLODES as edge -> 0: a model
  that COPIES THE BOOK scored +33.3 and read as "CONVERTED".

  BOOKS VOID LOW-PA PROPS. DK voids pa==1 for starters and all substitutes; the
  UNDER side is EXEMPT. PrizePicks voids pa<=2. See #1b.

  CLV RISES WITH LEAD TIME (T-1h +0.0003 -> T-6h +0.0024). BET EARLY. (Recorded
  in prose; it is NOT yet a hashed artifact, which is why entry_hours is
  UNVERIFIED.)

  DUCKDB TYPES ARE NOT WHAT YOU ASSUME. list() returns a NUMPY ARRAY.
  CAST(... AS DATE) returns a TIMESTAMP (str() -> "2026-06-01 00:00:00" -> HTTP
  400). Normalise EXPLICITLY.

  SMARTSTAKE IS NOW LOCAL: data/market/smartstake/ (901 MB, gitignored). 621M
  rows, Mar 21 - Jul 5. *** JULY IS 0% GRADED and therefore useless. *** No more
  HTTP 429, and the artifact cannot shift when the vendor re-publishes.

-------------------------------------------------------------------------------
OPEN-PERIOD HITS PA COUNTERFACTUAL (2026-07-15; MAY REMAINS SEALED)

  Protocol:
    data/analysis/market_policy_hits_2026/pa_counterfactual_protocol_v1.json
    sha256 ac2de170f6de04441a50c1e93a1d0bd19c61d5cfbed1be61874b45a5b32b4c75
  Report:
    data/analysis/market_policy_hits_2026/pa_counterfactual_time_safe_v1/report.json
    sha256 6b4a9babcfae1dc8ca26ea48e5fff81d9cf6d0c418591260d158db80fae404fc

  10/10 mutations plus independent artifact validation passed. Exact 3,502
  strict market rows / 3,386 player-games survived; the fitted EV threshold
  stayed 0.032116815573421054; no May date was read into the scored diagnostic.

  The analytic constant-per-PA hit mixture is compatible with the published
  8,000-draw hits Monte Carlo: mean |P(H>=2) difference| 0.003919; 95.39% of
  player-games fall within |z|<=1.96 and p95 |z|=1.924. Exact analytic hits is
  therefore supported as a reproducibility/performance candidate, but its
  measured metric movement is small and it is NOT the missing capture edge.

  Selector-only mean PA alignment applied unchanged to confirmation:
    hits 0.5: Brier 0.234637 -> 0.234807 (worse), capture 0.061161 -> 0.065540
    hits 1.5: Brier 0.207792 -> 0.206851 (better), capture 0.039205 -> 0.045953
  A blanket PA-mean increase is NOT supported. The 1.5 line is a diagnostic
  candidate only; confirmation is already open evidence, not a promotion set.

  Perfect realised-PA oracle improves confirmation Brier materially but lowers
  capture on both lines. This is not contradictory: realised PA is postgame
  opportunity information that the closing market cannot know. It shows outcome
  accuracy headroom, not automatically monetisable pregame information. The
  predeclared PA-support condition failed on both lines. No production model or
  policy changed; betting remains unauthorized.

  Per-PA skill calibration protocol/report:
    protocol sha256 e1d4da61cfea0ffbd7e66a0d542a5257f8831494982ca94d66820cfb8c81af01
    report   sha256 ea185273869385309e73a420f17e0f2975be6d341e30d62084cdbaf0b72963ca
  The confirmation candidate expected 0.222296 hits/PA vs 0.214608 observed:
  residual +0.007688, date-block 95% [-0.000097, +0.015747]. The locked global
  bias rule therefore FAILED by a narrow margin. Selector band residuals did
  not reproduce strongly (Spearman 0.196); lineup-slot residuals reversed
  (Spearman -0.533). No blanket q calibration or slot correction is supported.

  Per-PA discrimination control protocol/report:
    protocol sha256 3c303ccdfa74ef6722b37f8243d43b7d61e776d2e313fc72366b75e1384c37ca
    report   sha256 8632257afdd14c549fbbbc8caf31c20e3fe85b18afe77b812517a1c8372939d4
  On 1,785 confirmation player-games / 7,325 PA, player-specific q beat the
  selector-global constant at the point estimate but NOT at the locked 95% bar:
    Brier delta   -0.000511 [-0.001180, +0.000101]
    log-loss delta -0.001568 [-0.003558, +0.000242]
  It did clear both paired bounds against the selector slot-only baseline, but
  the predeclared contract required all four comparisons. Directionally useful
  player information exists; stable incremental discrimination beyond one
  well-fitted constant rate is NOT yet proven. This makes hitter-skill input
  quality/strength the next evidence-supported research target, not PA mean,
  Monte Carlo count, or a post-hoc calibration patch.

  PowerShell: $env:PYTHONPATH="." per session. Use Set-Content -Encoding utf8 for
  heredocs (the default codepage breaks non-ASCII).

===============================================================================
HOW TO WORK ON THIS

  FRESH chat per roadmap item. Upload this file plus the source files needed.
  Name the item. Offline harness -> single-date smoke -> full run -> commit.
  Update this file after each session.

  AND: the measurements have been right and the stories have been wrong. When a
  number surprises you, the first suspect is THE INSTRUMENT, not the world.

-------------------------------------------------------------------------------
STATCAST AS-OF LEAKAGE DISCOVERY (2026-07-15; MAY REMAINS SEALED)

  The hitter-input audit found that historical reconstruction passed the target
  game date to pybaseball's INCLUSIVE Statcast end date. A direct April 30 probe
  returned 3,289 April 30 rows, and those rows entered the April 30 hitter
  profiles. All pre-fix historical reconstruction probabilities and downstream
  residual diagnostics are therefore PROVISIONAL/INVALID FOR AUTHORIZATION.

  Repair: StatcastFeatureEngine now ends every pregame profile on target_date-1.
  Mutation test 4/4; reconstruction contract 28/28; feature-manifest contract
  5/5. A source-aware April 30 frozen/candidate smoke passed with the provider
  fetching only through April 29: 1,056 model rows, 616 official outcomes, 984
  changed candidate probabilities, mean |drift| 0.02877.

  Do not launch the 37-date rebuild yet. The same audit found that the gate
  manifest omitted the source files that defined this boundary and preserved
  only a config hash, not the effective config or model-ready feature snapshots.
  Canonical release provenance must be hardened first, then March-April rebuilt
  and the locked diagnostics rerun unchanged. May remains unopened.

  Detailed audit: reports/hits_input_path_audit_2026-07-15.md

-------------------------------------------------------------------------------
LEAKAGE-FREE HITS DEVELOPMENT + PITCHER CONTACT AUDIT (2026-07-15)

  The 37-date March-April leakage-free frozen/candidate reconstructions were
  subsequently completed and source-aware validated. The locked PA, per-PA
  calibration, and per-PA discrimination diagnostics were rerun against those
  artifacts. May 2026 remained sealed and unread; betting remained unauthorized.

  Opposing-pitcher expected contact quality was then audited on already-open
  2023-2025 development evidence with two rolling-origin folds. Protocol:
    data/analysis/pitcher_contact_audit_v1/protocol.json
    sha256 bed3f3c5a6a26eddc71b89caf73c91c60a33db5a8882a04d1c81c79da3468574
  Report:
    data/analysis/pitcher_contact_audit_v1/report.json
    sha256 19a127819eae4f65112d23d4606343b889a61356a4fde25073a47495bd253c2c

  The audit verified 1.485 GB / 2,130 raw Statcast files and scored 79,049
  unique evaluation player-games across 367 dates. Coverage was preserved;
  6,263 of 118,525 development player-games used an explicitly neutral
  pitcher-history fallback. Nine of nine mutations passed.

  Result: REJECT the pitcher-contact candidate.
    2023 -> 2024: alpha +0.0470; Brier +0.00000065; log loss +0.00000175
    2023-24 -> 2025: alpha +0.0341; Brier -0.00000639; log loss -0.00001471
    pooled Brier -0.00000288, 95% [-0.00000922, +0.00000344]
    pooled log loss -0.00000650, 95% [-0.00002104, +0.00000800]
  Direction was positive but fold consistency and the predeclared minimum
  worthwhile-effect gate both failed. No production candidate, March-April
  reconstruction, May opening, policy change, GUI action, or betting
  authorization follows from this audit.

  Separately, Statcast display-name metadata is now canonicalized from the
  MLB-ID-keyed hitter context. A profile/key MLB-ID mismatch fails closed.
  The 7/7 boundary/identity harness and 20 targeted prediction tests passed;
  numeric prediction features were unchanged by the display-name repair.

-------------------------------------------------------------------------------
HITTER HANDEDNESS-SPLIT AUDIT (2026-07-15; MAY REMAINS SEALED)

  Protocol:
    data/analysis/platoon_split_audit_v1/protocol.json
    sha256 720fa16be162cdbef331d358cd6c2083fbde0ac7637ee4326b4e4877ef9de4c8
  Report:
    data/analysis/platoon_split_audit_v1/report.json
    sha256 f00716327e6629f99a9baa657dca304204570cc4f156862e2dccdad2cc2e86e6

  Three isolated point-in-time levers were tested with two rolling-origin
  folds and Bonferroni-adjusted family intervals: hitter contact xBA versus
  pitcher hand, hitter K rate versus pitcher hand, and hitter BB rate versus
  pitcher hand. All split history was strict-prior 45-calendar-day evidence;
  missing history copied baseline exactly and never removed a row.

  A predecision source-truth check found that Statcast events=truncated_pa is
  not an official PA. There were 1,062 such raw rows globally; they caused 800
  training player-game PA mismatches. The locked integrity amendment excluded
  only that factual non-PA event. Afterward, raw PA/K/BB exactly matched official
  outcomes on all 131,202 player-games. Harness 13/13; artifact/content hashes,
  unique keys, probability ranges, and 367-date chronology validated.

  Result: NO LEVER PASSED; build no candidate.
    contact xBA: Brier +0.00000066, log loss +0.00000142 (slightly worse)
    K split:     Brier -0.00000658, log loss -0.00001416 (tiny; CI crosses zero)
    BB split:    Brier -0.00000075, log loss -0.00000519 (tiny; CI crosses zero)
  The contact arm failed fold consistency. K improved both fold point estimates
  but failed family-adjusted uncertainty and the minimum worthwhile-effect gate.
  BB failed fold consistency and materiality. Production, policy, GUI, and May
  remain untouched; betting remains unauthorized.

-------------------------------------------------------------------------------
HITS CAPTURE-FEASIBILITY AUDIT (2026-07-15; MAY REMAINS SEALED)

  Protocol and report:
    data/analysis/hits_capture_feasibility_v1/protocol.json
    data/analysis/hits_capture_feasibility_v1/report.json

  The predeclared audit used only the already-open March-April Hits period. It
  retained every observed quote age, then excluded both rows on 18 duplicated
  final MARKET_KEYs: 7,176 source rows -> 7,140 strict rows across 37 dates.
  A selector-only ordinary-least-squares diagnostic used six T-4h-available
  fields with no feature search or regularization and was applied unchanged to
  the later 19 spent confirmation dates. Harness: 8/8, including deliberate
  May, confirmation-in-training, missing-date, hash, and false-executability
  mutations.

  Result: PREDECLARED_LINEAR_FEASIBILITY_GATE_FAILED.
    confirmation selected rows: 1,552 across all 19 dates
    capture point: +0.05373; 95% [+0.03938, +0.06781]
    flat-stake ROI point: -0.03613; 95% [-0.10070, +0.02600]
    locked requirements: capture lower bound > +0.10 AND ROI lower bound > 0

  Historical executable price was NOT established. All 7,176 exact source
  selections had both sides observed before T-4h; 5,497 were observed again on
  both sides afterward, but only 615 bracketed T-4h at the same two prices.
  Vendor observations do not prove sportsbook availability or a real fill.

  Interpretation: available edge/quote-age/overround/line/side information did
  predict some closing movement (confirmation correlation +0.1459), but the
  predeclared filter did not approach the locked +0.10 lower-bound requirement
  and did not establish positive ROI. This does not prove Hits can never clear
  the bar; it does reject this simple market-timing explanation. Do not tune a
  subgroup from the diagnostic table, lower the bar, open May, or authorize
  betting. Park adjustment is not promoted by these results.

-------------------------------------------------------------------------------
DRAFTKINGS HR-OVER-0.5 RESEARCH CONTRACT (2026-07-15; MAY REMAINS SEALED)

  A separate one-sided HR research lane is now locked. It does not pool with or
  rescue Hits. Exact contract: DraftKings `player home runs`, over 0.5 only,
  model target P(official MLB HR >= 1), last observed over price at/before T-4h,
  and last observed over price before first pitch. There is no manufactured
  under, cross-book pairing, or de-vig claim. `1 / decimal_odds` is labeled raw
  break-even probability with unknown margin. Posted-price expected profit is
  model_p * decimal_odds - 1. Harness: 11/11 mutations.

  Outcome-blind open-month audit (March, April, June; May excluded):
    raw HR-over-0.5 selections: 16,478
    T-4h entry + pregame close: 14,006
    plus vendor settlement presence: 12,482
    median entry age: 66 minutes
    mixed settlement snapshots: 0
  Historical executability remains NOT ESTABLISHED and no freshness cutoff was
  selected. The official DraftKings HR participation/void rule was not exposed
  clearly enough by its current public page to certify; scored evaluation stays
  blocked rather than inheriting the Hits rule.

  Fresh March-April crosswalk (no May input) completed after its 12/12 harness:
  1,184 fragments, 1,071 mapped, 31,772 player mappings, consumer key unique,
  player identity well-defined, 39 accepted official dates through April 30.
  The availability evidence supports research reconstruction only. It does not
  establish performance, profitability, an HR authorization gate, or betting
  authorization.

  Procedural disclosure: during a later inventory of available crosswalk files,
  the existing March-May crosswalk REPORT was opened. It exposed only the month
  list, accepted-date list, and aggregate mapping/coverage counts. No May market
  prices, model probabilities, official outcomes, capture, ROI, or row-level
  performance evidence were read or used. A fresh March-April-only crosswalk was
  built for HR instead. May's economic/performance holdout remains UNSPENT, but
  the stronger literal statement "no May artifact has ever been opened" is no
  longer accurate and must not be repeated.

-------------------------------------------------------------------------------
PREDICTOR INTEGRITY REPAIR AND STRICT CANDIDATE ADJUDICATION (2026-07-20)

  A live plumbing defect was found and repaired. DailyPredictor recenters the
  contact-conditional league baselines after building a Statcast snapshot. The
  refresh path rebuilt PASimulatorConfig from league defaults and silently
  discarded the complete `pa_simulator` config block, including the hash-bound
  fitted K/BB model. The refresh now re-applies the exact effective config.
  Mutation tests prove configured PA parameters survive the refresh. On the
  existing 2026-07-12 feature snapshot, the intended K/BB path differed on all
  270 hitters: +0.01941 strikeout probability per PA and -0.00692 hit
  probability per PA versus the silently reset path. This is an implementation
  repair of the already-preserved K/BB research baseline, not a new fitted
  candidate and not betting authorization.

  Research automation is now aligned to `config/config.kbb.json`; the GUI's
  promoted-model default remains unchanged and explicit. Prediction and outcome
  automation both use the same research config. `pa_simulator` is now part of
  model_version, so frozen and fitted-K/BB outputs can no longer share an
  identity. The fitted PA-distribution artifact is also content-bound in both
  current configs; a missing or hash-mismatched declared artifact now fails
  instead of silently reverting to the legacy PA draw. Its nine lineup-slot
  distributions now also fail closed on missing slots, duplicate/invalid keys,
  negative/non-finite probabilities, or totals other than 1.0; runtime no
  longer clips or renormalizes malformed fitted evidence. Base config version is
  `5fa493e73b34`; K/BB research version is `bd7a467f966c` under the repaired
  identity contract. Outcome grading rejects
  any prediction-time versus grading-time model-version mismatch.

  Prediction archives now atomically persist both the full simulation
  distribution and the explicit PA outcome distribution. Malformed, partial,
  duplicated, non-finite, or out-of-range archived probability evidence hard
  fails instead of silently falling back to a normal approximation. Old
  archives without decision-time provenance are not re-labeled or backfilled.

  A monotonicity defect was also confirmed in the legacy HR formula: higher
  opposing-pitcher HR/9 lowered hitter HR probability. A separately gated,
  opt-in sign correction was tested on 8,827 official open-period keys and
  rejected. It improved some point estimates but worsened diagnostic Brier,
  failed paired uncertainty, and regressed confirmation AUC. The flag remains
  disabled; frozen HR behavior is unchanged. Every affected HR projection now
  carries `opposing_pitcher_hr9_direction_unqualified`, and the daily policy
  layer forcibly keeps those rows research-only even if an authorization
  certificate were otherwise present. This records the known limitation and
  prevents an unsupported HR wager without pretending the rejected correction
  improved predictions. Report:
    data/analysis/hr_over_contract_v1/pitcher_hr9_direction_candidate_v1/report.json
    SHA-256 a2ac12f6df03fdcd1b18279eedb27aa253595678fcab068d4ad0861dcf30e191

  A fitted batter probability challenger was also rejected before production
  or market evaluation. Removing 72 `actual_starter` rows was necessary but not
  sufficient: the historical builder had no verified decision-horizon receipt
  hashes for lineups/starters/weather/officials. Exploratory 2025 gains cannot
  qualify a time-safe candidate. No May performance evidence was used, no gate
  was weakened, production/GUI authorization is unchanged, and wagering remains
  unauthorized.

  Verification after these repairs: 118/118 project tests passed; the leakage-
  critical fitted-model harness passed 20/20. No rejected intervention was
  promoted and no May performance evidence was opened.
