PROJECT_CONTEXT.md — v17 (master roadmap) — paste into any new chat

===============================================================================
AUTHORITATIVE INTEGRITY MILESTONE (2026-07-22) - SOURCE LOSS FAILS CLOSED

SI-006 is repaired on isolated branch
`codex/statcast-source-loss-fail-closed-v1`. Statcast dependency loss, provider
exceptions, empty/invalid responses, malformed pitch-level schemas, and
missing/empty CSVs now fail before probability production. Observed,
partial-field-fallback, and no-player-history profiles persist explicit source
lineage through feature storage, health reporting, and PA probability
consumption. Full tests pass 172/172; the existing 7/7 as-of, 9/9 batted-ball,
and 22/22 health mutation harnesses also pass. Valid numeric probabilities are
unchanged, so this is an integrity repair, not a scoring improvement or model
promotion. No data/features were rebuilt or relabeled, May 2026 was untouched,
the frozen baseline remains in force, and betting remains unauthorized. Exact
report: `docs/research/STATCAST_SOURCE_LOSS_REPAIR_V1_REPORT.json`.

The prior `NO_ADMISSIBLE_NEW_CANDIDATE_FRESH_EVIDENCE_REQUIRED` conclusion
below remains authoritative for performance promotion. The single highest-value
next action remains fresh certified T-minus-4 starter receipts plus source-bound
prospective batter rows; never backfill or reuse spent confirmation evidence.

SI-007 is also repaired in the same isolated research lineage. Rolling-provider
payloads can no longer replace source-bound Statcast values, and every effective
rich Statcast field is bound to source kind/status/window/row count. Barrel and
hard-hit fields additionally bind their shared denominator and counts. Missing,
changed, or contradictory lineage fails again at feature storage and PA
probability consumption. Full tests pass 179/179. Valid probabilities remain
unchanged; this is another integrity repair, not a performance promotion.

SI-022 is repaired in the same isolated lineage. Rolling Statcast/MLB game-log
fetch failures can no longer become blank histories, corrupt caches are
preserved rather than deleted/refetched, and a configured rolling-provider
failure now stops feature construction. Observed and confirmed-empty histories
carry exact target cutoff, maximum strict-prior source date, row count, and
content hash through storage. Full tests pass 187/187; the rolling leakage and
denominator harness passes 19/19. No valid probability changed and no model was
promoted.

===============================================================================
AUTHORITATIVE NEXT-ACTION AUDIT (2026-07-21) - FRESH RECEIPTS REQUIRED

After the repaired direct-batter v2 rejection, the remaining admissible research
path was audited without starting or modifying a collector. The cumulative,
hierarchical, direct-batter, and calibration-oracle batter-only families are all
bound to their preserved rejection records. Reusing 2024, spent 2025 HR, or May
2026 for a new candidate is forbidden.

Two existing July 20 T-minus-4 pitcher-context attempts were independently
hashed. Each planned 15 targets, but each append-only ledger has zero records and
an empty terminal index. All 15 deadlines are past. The attempts therefore do
not provide receipt-proven starter identity and may never be backfilled. Pitcher
features remain excluded. Operational smoke remains permanently non-economic.

  admissibility report:
    reports/shared_pa_next_action_admissibility_v1.json
  report sha256:
    fd03645d2a12bf5b3d26a9a3516cdbd860aced27699d443bb6cf38707866ea5d
  audit contract sha256:
    0ab8a6a8635079d98e7ba187bb73affd59f46c6fd3bcb8dcb1079bb7723b2a4a
  auditor sha256:
    8e7736f639956c28eced698e52998360d768121eb6e68ea391dabd493572c62a
  mutation-test sha256:
    db734c5410d39167ed82696790b42656906138ef113b8a4690dd16010319ceab

Status: `NO_ADMISSIBLE_NEW_CANDIDATE_FRESH_EVIDENCE_REQUIRED`. No market is
eligible and betting remains unauthorized. The single highest-value next action
is a future untouched regular-season slate with complete, certified T-minus-4
probable-starter receipts under the released lifecycle. Do not backfill July 20.

===============================================================================
AUTHORITATIVE REPAIR ADDENDUM (2026-07-21) - DIRECT BATTER PA V2 REJECTED

This addendum supersedes the Direct Batter PA V1 addendum immediately below,
while preserving v1 as an immutable rejected-candidate record. Research remains
non-operational: no betting authorization, no production change, May 2026 sealed,
2025 HR confirmation not reopened/reused, and no collector or operational-smoke
runtime touched.

The source-truth audit found a real defect. V1 stored raw-reconciled `target_*`
fields but the probability path consumed inherited `out_*` fields. Suspended game
746942/player 643376 had four raw terminal PAs (single, strikeout, two BIP outs)
but zero inherited exposure. V2 repairs the true source: unique regular-season
raw terminal PA events construct both target classes and consumed aggregates.
It fails closed on unknown events/game types, missing or duplicate terminal-PA
identity, denominator imbalance, and any consumer/raw-truth disagreement.

The rebuilt v2 panel preserves all 87,462 identities and 52 batter-only feature
columns. Independent validation rehashed 1,291 raw files and found zero remaining
source mismatches, chronology violations, identity duplicates, or eligible-row
coverage loss, with exact probability-consumer parity. The false zero-PA count
fell from 32 to 31. Fit remained 2023 and selection remained 2024 only.

V2 remains `SELECTION_REJECTED_NO_CANDIDATE`. Candidate multiclass Brier/log loss
are 0.70775636 / 1.50402365. Hits, HR over 0.5, and Total Bases separately fail.
HR Brier/log loss/AUC are 0.05838546 / 0.13373415 / 0.59630341. It misses the
league-rate Brier materiality gate, both player-EB 1% proper-score gates, and the
all-prior core AUC (0.59963070). The high-probability tail predicts 5.2118% versus
4.7476% observed. Frozen-simulator comparison is not reached after upstream
failure and without receipt-proven pregame PA volume. No hash-bound executable
HR price join exists, so historical prices remain non-executable.

  committed v2 rejection summary:
    reports/direct_batter_pa_foundation_v2_CERTIFIED_REJECTION.json
  panel sha256:
    faaf76b07373526b3435e43469e017825c08726953f0610404708e383879207a
  source-truth audit sha256:
    5af519963034d6b8edf3b447160d97db6556429415b5962f4541fca44f3249f4
  selection report sha256:
    456d5d72cce48179b39f5f01d491b7de0540f01fd7c34f66805a801c88018bc6
  selection certificate sha256:
    5e53d20f136c9132b1564326102562e44fef632ade2dbe47b95c3bd1f799de54
  HR required-comparator audit sha256:
    f2c65f63ca91260f59a7119f8062aca79967d5b7861922d7559c89a5dce687e3

Pitcher-matchup inputs remain excluded because no completed certified T-minus-4
probable-starter receipt lifecycle exists. The single highest-value next action
is to collect and certify fresh prospective T-minus-4 starter receipts under the
existing exact-identity lifecycle; never guess or backfill the starter.

===============================================================================
CURRENT RESEARCH ADDENDUM (2026-07-21) — DIRECT BATTER PA V1 REJECTED

The authoritative 2026-07-21 handoff rules remain in force: research only; no
betting authorization; May 2026 sealed; frozen production and every prior
rejection preserved; historical prices are not executable; markets/products
remain separate; operational smoke remains permanently non-economic.

Research is isolated in `C:\Projects\baseball_predictor\.codex-direct-batter-pa-v1`
on branch `codex/direct-batter-pa-foundation-v1`, based on committed probability-
foundation lineage `70449da`. It did not touch production or collector runtime.

The distinct direct batter-only PA candidate used all-prior regular-season PA
outcomes, recency, plate-discipline, contact-quality, pitch-shape summaries,
and batter identity. Opposing pitcher/starter, lineup slot, team/opponent,
venue/park, weather, umpire, market, and policy inputs were excluded. Every
feature source date was strictly before its target game; 1,291 raw files were
independently rehashed. The certified panel had 87,462 player-games, 52 feature
columns, zero chronology violations, zero identity duplicates, and no eligible-
row coverage loss. Fit was 2023 and selection was opened once on 2024. The
spent 2025 HR confirmation was not reused and May 2026 was not opened.

Result: `SELECTION_REJECTED_NO_CANDIDATE`. Candidate multiclass Brier/log loss
were 0.70775755 / 1.50402857. The all-prior core was better at 0.70750268 /
1.50347683, with both paired candidate-minus-core 95% intervals wholly above
zero. Improvements versus empirical Bayes were real but far below the locked
1% materiality floors. Hits, HR over 0.5, and Total Bases each failed their
separate gates. No confirmation, full-game model, policy, production, market,
or betting step follows.

  committed rejection summary:
    reports/direct_batter_pa_foundation_v1_CERTIFIED_REJECTION.json
  panel sha256:
    bdf7913b889f3e26528d636deed6241feafe8ee97fc318be43c40ca0939b97d5
  panel certificate sha256:
    ed3c3bc165cf5bf68a205bb040657b8c72c05e96a69d3857993fcb906a5d73b2
  selection report sha256:
    daa046513c3f6230c5004d5a9dac00fd8c13f477ac5ca8e3d528c6ba27c687c2
  selection certificate sha256:
    2c5fa00d187fede999ca8ab72e0f021a2bf4d380f3b2b6edaed5ca6dc2a881e5
  rejection record sha256:
    83faffeee29bea634af41b4eab05fea50fc7a2692064c6674712fe099623cf52

Pitcher-matchup work remains excluded. The future-only T-minus-4 runtime
contract exists, but no completed certified probable-starter receipt lifecycle
was found. Never guess or backfill starters. The single highest-value next
action is to collect and certify fresh prospective T-minus-4 probable-starter
receipts under the exact identity lifecycle; only then predeclare one pitcher-
matchup candidate.

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
A new excluded full-day smoke is still required before a forward-economic era,
and a durable external primary remains mandatory for that later era.

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
2026-07-22 HANDOFF ADDENDUM — STATCAST FULL-PITCH DENOMINATOR REPAIR (AUTHORITATIVE)

SI-023 is repaired on isolated branch `codex/statcast-source-loss-fail-closed-v1`.
The generic pitch-level Savant path previously discarded every non-terminal
pitch before computing swing/contact/whiff/chase/zone rates and could copy an
uncertified Statcast `player_name` into a batter profile. Qualification and
`sample_pa` now use terminal events, pitch-rate construction retains all source
pitches, `source_row_count` records all consumed pitches, and numeric batter ID
remains authoritative until slate identity resolution. Regression and mutation
proof passes; the full suite is 189 passed and 1 skipped. Exact evidence is in
`docs/research/STATCAST_PITCH_DENOMINATOR_REPAIR_V1_REPORT.json`. This is an
integrity repair, not a measured probability improvement. The certified direct
batter 2023/2024 panel already used a separate truthful all-pitch history path,
so it is not rebuilt or rescored as new evidence; its rejected result remains
binding. May 2026, spent 2025 HR confirmation, frozen production, archived
predictions, AWS runtime, and the protected local collector were untouched.

2026-07-22 HANDOFF ADDENDUM — FEATURE MANIFEST LOAD BOUNDARY (AUTHORITATIVE)

SI-024 is repaired in the same isolated lineage. A present feature manifest is
now verified before `FeatureStore.load` reads any artifact, an unlisted sibling
cannot override a declared artifact, and loaded bundle count must match the
manifest. Missing-manifest legacy artifacts remain readable only with an
explicit unverified warning; no provenance is invented. Mutation proof is
10/10 in the offline harness and the full suite is 194 passed. Exact hashes are
in `docs/research/FEATURE_MANIFEST_CONSUMPTION_REPAIR_V1_REPORT.json`. The
repair is numerically inert on valid inputs and is not model-performance or
promotion evidence. No archived feature, probability, outcome, May 2026 data,
AWS runtime, or collector was changed.

2026-07-22 HANDOFF ADDENDUM — STATCAST ZONE DENOMINATOR REPAIR (AUTHORITATIVE)

SI-025 is repaired in the same isolated lineage. The generic live Statcast
profile previously divided in-zone pitches by all source rows, including rows
with no classified zone. An outcome-blind audit read only the `zone` column in
permissible 2023/2024 caches and measured 53,661 missing-zone rows among
1,572,200 rows; it read no outcome field and touched no 2026 or May artifact.
Zone rate now uses all nonmissing classified zones 1–14 as its denominator, and
the shared chase/zone parser fails closed on nonnumeric nonmissing or
out-of-domain zones. Regression and mutation proof passes; the full suite is
203 passed. Exact hashes are in
`docs/research/STATCAST_ZONE_DENOMINATOR_REPAIR_V1_REPORT.json`. The certified
direct batter panel already used the truthful denominator, so it was not
rebuilt, relabeled, or rescored. This is an integrity repair with no performance
or promotion claim. Frozen production, rejected candidates, spent confirmation,
prediction archives, AWS runtime, and the protected collector were untouched.

2026-07-22 HANDOFF ADDENDUM — SAVANT PLAYER-SUMMARY CONTRACT (AUTHORITATIVE)

SI-026 is repaired in the same isolated lineage. Nine daily configs declare
`data/savant/stats.csv`, which is absent; the old resolvers silently converted
that declared source loss to no override. If a summary appeared, a NaN legacy
alias could suppress the canonical barrel value, official percentage-point
values at or below 1.0 could be inflated 100-fold, aggregate barrel/hard-hit
rates lacked common-denominator counts, identities could overwrite, and no
strict-prior cutoff was enforced. Configured loss now fails closed. CSV profile
construction requires an explicit target date; pitch rows are filtered strictly
prior by `game_date`, summaries require one strict-prior `source_window_end`,
official percent fields have explicit units, ambiguous aliases fail, and
barrel/hard-hit rates require coherent BBE/barrel/hard-hit counts. Mutation and
regression proof passes; the full suite is 221 passed. Exact hashes and the
outcome-blind 53-config audit are in
`docs/research/SAVANT_PLAYER_SUMMARY_CONTRACT_REPAIR_V1_REPORT.json`. No source
row, outcome field, 2026/May artifact, archive, frozen runtime, AWS service, or
collector was changed. This is integrity repair only; no market earned a
performance or promotion claim.

2026-07-22 HANDOFF ADDENDUM — STATCAST PROFILE CONTENT HASHES (AUTHORITATIVE)

SI-027 completes the remaining exact-content portion of SI-011 in the same
isolated lineage. Source-bound profiles previously carried kind/status/cutoff/
row count but no digest of the actual consumed rows. Canonical SHA-256 binding
is now row- and column-order independent, duplicate preserving, typed for
missing values, and fail-closed for nonfinite/unsupported cells. Every observed
profile hashes its player-specific consumed rows; league fallback hashes its
exact mapping. The digest is required and propagated through JSON/Parquet,
rich-field lineage, prediction health v4, and final probability consumption.
Hash mutations fail at both storage and consumption. Targeted proof is 57
passed and the full suite is 231 passed. Exact hashes are in
`docs/research/STATCAST_PROFILE_CONTENT_HASH_REPAIR_V1_REPORT.json`. Valid
probabilities are numerically unchanged; old artifacts are preserved but cannot
be retro-certified. No data or outcome was opened, May/2026 remained untouched,
and frozen production, AWS, operational smoke, and the collector were unchanged.
No market promotion or betting authorization follows.

2026-07-23 HANDOFF ADDENDUM — CORRECTION CONSUMPTION CONTRACT (AUTHORITATIVE)

SI-013 is repaired on isolated branch `codex/statcast-source-loss-fail-closed-v1`.
The optional correction path previously treated a missing/corrupt explicitly
requested state as an ordinary uncorrected run, could alter displayed means
without regenerating exact Monte Carlo probabilities, and could compound
parameter blends across repeated calls. Persisted states lacked point-in-time,
protocol, source, code, config, test, market-scope, and promotion identities.
Schema-v2 states now validate those identities; automated retraining creates
only `RESEARCH_ONLY` state; runtime consumption requires an independently
`PROMOTED`, strictly-prior, exact-market artifact; output-only offsets are
quarantined; publication is atomic; and every run begins from immutable
uncorrected parameters. Focused proof is 25 passed and the full suite is 255
passed. Exact hashes are recorded in
`docs/research/CORRECTION_CONSUMPTION_CONTRACT_REPAIR_V1_REPORT.json`. No
historical outcome/price, 2026/May, feature, prediction, AWS, or collector
artifact was opened or changed. Valid uncorrected probabilities are numerically
unchanged, no candidate was promoted, and betting remains unauthorized.
The report SHA-256 is
`b77f92a1d98884549fe6fc589bbba09c58344c1b7db8ccb921a49149b62ecaa6`.

2026-07-23 HANDOFF ADDENDUM — PA SIMULATOR CONFIG CONTRACT (AUTHORITATIVE)

SI-030 is repaired in the same isolated lineage. Unknown `pa_simulator` keys
were previously logged and ignored, while direct construction accepted wrong
types, NaN/infinity, invalid probability bounds and mixture shares, nonpositive
denominator scales, incomplete/decorative fitted coefficients, and inert legacy
overrides. The shared dataclass boundary now rejects those states and the daily
config bridge fails on unknown or inert keys. All three repository PA-config
blocks have no unknown/inert key; their declared K/BB artifact is absent from
this older isolated branch and remains a hard source failure rather than being
copied or fabricated. Focused proof is 36 passed and the full suite is 269
passed. Exact hashes are in
`docs/research/PA_SIMULATOR_CONFIG_CONTRACT_REPAIR_V1_REPORT.json`. Valid
configuration probabilities are unchanged; no data/feature artifact was
rebuilt, no market was promoted, May/2026 and outcomes/prices stayed unopened,
and AWS/collector/frozen production were untouched. Betting remains
unauthorized.
The report SHA-256 is
`9c076e07014e4eb6f130c1b7ac3ef849c4a495d675f0f61b187f0863f035d2b3`.

2026-07-23 HANDOFF ADDENDUM — MARKET TAIL CONSUMPTION (AUTHORITATIVE)

SI-031 is repaired in the same isolated lineage. Exact Monte Carlo tails could
previously reach edge arithmetic with invalid probability/distribution state;
an injected exact Total Bases tail bypassed its allowed line grid; direct calls
did not bind quote identity; player/category indexing overwrote sportsbooks;
and an unknown simulator category silently became HRR. The repaired boundary
validates the complete distribution, exact player/category/line identity, and
market quote, keeps each sportsbook and alternate line separate, rejects an
ambiguous duplicate product, and fails on unknown category or invalid draw
count. Focused proof is 41 passed and the full suite is 285 passed. Exact hashes
are in `docs/research/MARKET_TAIL_CONSUMPTION_CONTRACT_REPAIR_V1_REPORT.json`.
Valid probabilities and price arithmetic are unchanged; no historical
price/outcome, May/2026, feature, prediction, AWS, collector, or frozen runtime
artifact was opened or changed. No market was promoted and betting remains
unauthorized.
The report SHA-256 is
`8b895fb8af82d896f4f91694bc9ea25583af45a83302f2a7fe3445958f03dc53`.

2026-07-22 HANDOFF ADDENDUM — EXPLICIT/SAMPLED INPUT PARITY (AUTHORITATIVE)

SI-029 is repaired in the same isolated lineage. `ProbabilityEngine` archived
explicit PA probabilities from raw recent-form/BvP multipliers while
`PropEngine` sampled bounded versions from the same bundle. One validated
effective-context builder now owns pitcher rates, park/weather, umpire,
handedness, recent form, and BvP inputs for both consumers. The frozen
comparator's existing bounds are preserved exactly rather than retuned or
endorsed; invalid numeric context fails closed. Mutations outside all three
existing bounds now produce exact explicit/sampled input parity, combined
park/weather and pitcher/umpire inputs match, and valid in-bound explicit
probabilities are unchanged. Targeted proof is 13 passed; the full suite is
241 passed and 1 skipped. Exact hashes are in
`docs/research/EXPLICIT_SIMULATION_INPUT_PARITY_REPAIR_V1_REPORT.json`. This
repairs representation integrity only. The fitted batter-only challenger does
not inherit the hand-specified matchup effects, no historical archive was
rewritten, May/2026 and outcomes were untouched, AWS/collector were unchanged,
and no market promotion or betting authorization follows.

2026-07-22 HANDOFF ADDENDUM — FEATURE PUBLICATION TRANSACTION (AUTHORITATIVE)

SI-028 is repaired in the same isolated lineage. Feature artifacts were formerly
written to canonical loadable paths before the manifest; an interrupted first
save could be consumed as legacy-unverified, and replacement generations were
exposed one file at a time. New saves now use SHA-256 content-addressed artifact
names and atomically publish the manifest only after the complete generation
exists. Without a manifest, only old canonical legacy names are eligible;
temporary/content-addressed orphans are retained but never consumed. Manifest
paths are basename-only and hashes, byte counts, and bundle population all
verify before load. Crash, traversal, byte-count, hash, population, and sibling
mutations pass; targeted proof is 8 passed and the full suite is 235 passed.
Exact hashes are in
`docs/research/FEATURE_PUBLICATION_TRANSACTION_REPAIR_V1_REPORT.json`. No old
artifact was deleted or rewritten, valid features/probabilities are unchanged,
May/2026 and outcomes were untouched, and frozen production/AWS/collector were
not changed. No market promotion or betting authorization follows.
