# MLB system-integrity program v1

Status: research only; no betting authorization. May 2026 is sealed under a
no-fetch, no-read, no-parse, no-write rule. The July 22 pitcher-context
collector is outside this worktree and must not be changed.

## Backfill boundary

Permitted backfill is raw baseball/statistical source evidence for feature
repair, source auditing, and chronological development. It cannot create or
replace T-4 starter receipts, live lineups, prices, executable availability,
decision-time predictions, settlements, or execution observations. For 2026,
the repair workflow projects only point-in-time input columns and excludes
outcome/result columns. Exact machine-readable scope is in
`config/historical_raw_backfill_v1.json` and is enforced by
`src/data/historical_backfill_contract.py`.

## Active feature-to-probability map

| Stage | Active implementation | Truth/lineage obligation | Downstream markets |
|---|---|---|---|
| Schedule, roster, lineup, starter identity | `src/data/mlb_api.py`, `src/data/lineup_provider.py`, `src/features/feature_factory.py` | Target/game/player/team keys; receipt time; pregame availability | Hits, HR, TB |
| Batter Statcast retrieval | `src/data/savant.py`, `src/features/legacy_statcast_features.py` | Source window ends target_date-1; batter MLB ID; source columns and hash | Hits, HR, TB |
| Batted-ball parsing | `src/data/statcast_integrity.py`, `src/data/statcast_batted_ball_rates.py` | Counts and one measured-EV denominator; barrel subset of hard hit | HR first; Hits/TB shared |
| Direct/canonical PA history | `src/features/direct_batter_pa_history.py`, `src/features/canonical_pa_features.py`, `src/features/canonical_cumulative_pa_features.py` | Strict prior dates; terminal-event accounting; count/rate denominators | Hits, HR, TB research |
| Scalar profile and fallback | `src/data/savant.py`, `src/models/dataclasses.py` | Missingness and fallback provenance must remain explicit | Hits, HR, TB |
| Context and rich features | `src/features/feature_factory.py`, `src/features/rich_feature_enricher.py`, `src/features/ml/feature_pipeline.py` | Per-field source, override order, units, sample size, target cutoff | Hits, HR, TB |
| Pitcher matchup | `src/features/matchup_intelligence.py`, `src/simulation/probability_engine.py` | Receipt-proven probable starter only; otherwise block/exclude pitcher block | Hits, HR, TB |
| Effective PA inputs | `src/simulation/pa_simulator.py::_build_latent_profile` | Rich > scalar > league precedence must validate the effective pair | Hits, HR, TB |
| PA outcome probabilities | `src/simulation/pa_simulator.py` | K/BB/HR/BIP formulas; sum-to-one; no duplicated effects; fitted-artifact binding | Hits, HR, TB |
| Per-game PA count | `src/simulation/game_simulator.py` | Lineup-slot distribution artifact and target-time lineup provenance | Hits, HR, TB |
| Game simulation | `src/simulation/game_simulator.py`, `src/simulation/monte_carlo.py` | Shared PA distribution; sampling/explicit consistency; sufficient draws | Hits, HR, TB |
| Market projections | `src/prediction/prop_engine.py` | Hits count, HR>=1, and TB count evaluated separately | Separate gates |
| Probability consumption | `src/prediction/edge_calculator.py`, `src/evaluation/*` | Exact tails only; price/executability labels; no market pooling | Separate gates |
| Persistence and identity | `src/features/feature_store.py`, `src/learning/prediction_archive.py`, `src/utils/model_version.py` | Atomic write, hashes, complete distributions, code/config/data identity, every consumed `pa_simulator` block | All |
| Corrections and output policy | `src/learning/bias_corrector.py`, `src/prediction/correction_manager.py`, `src/evaluation/market_output_policy.py` | Exact correction source/effective-state hashes; strict schema; model-parameter consumption before probability generation; output-only offsets forbidden; policy remains a separate authorization boundary | Separate gates |
| Operations | daily runners, shadow collectors, Task Scheduler design | Locked runtime, immutable logs, terminal missed records, no invented data | Non-economic until proven |

## Prioritized defect register

| ID | Priority | Finding/root cause | Affected scope | Required remediation/status | Classification |
|---|---:|---|---|---|---|
| SI-001 | P0 | Sparse `barrel` and `hard_hit` source columns were averaged independently. Kwan reached the live feature archive as 0.50 versus 0.094. | HR directly; Hits/TB through shared PA inputs | Preserved archive; shared BBE denominator and fail-closed boundaries implemented. Candidate v3 verified; probability evaluation pending. | Integrity repair |
| SI-002 | P0 | No invariant prevented barrel rate exceeding hard-hit rate after rich overrides or JSON round trip. | Hits, HR, TB | Shared validation at parse, override, serialization, and probability consumption; mutations pass. Complete as integrity boundary. | Integrity repair |
| SI-003 | P0 | General-purpose backfill utilities do not encode the current sealed-month/prospective-evidence boundary. | All research evidence | Contract and runner now skip May before fetching, exclude outcome fields, and forbid prospective classes. Complete for this backfill path. | Research governance repair |
| SI-004 | P0 | Canonical PA builders divide barrels by classified buckets while hard hits use measured EV, so denominator parity is not certified. | Shared batter PA research | Canonical transformers now share the count-bearing primitive and fork schema v2. Pre-2026 reconstruction/evaluation pending. | Integrity repair/new candidate |
| SI-005 | P0 | Direct batter history repeats the mismatched barrel/hard-hit denominators. | Shared batter PA candidate | Direct history now emits common counts/denominator/rates. Pre-2026 reconstruction/evaluation pending. | Integrity repair/new candidate |
| SI-006 | P1 | Missing Statcast fields could silently become league averages; unavailable, failed, empty, or schema-drifted fetches could become an empty player pool and then league profiles. | Hits, HR, TB | Complete in `statcast_source_truth_integrity_v1`: source loss/schema drift now terminates; May is rejected before a source call; every valid-source league substitution carries field-level lineage through serialization and input-health reporting; contradictory lineage fails at the probability-consumption validator. No valid-source probability formula changed and no historical outcome was opened. | Integrity repair |
| SI-007 | P1 | Rich features overrode scalar fields independently without stored identity, cutoff, source hash, count, denominator, fallback, or value lineage. | Hits, HR, TB | Complete for every rich field consumed by the shared PA probability path in `rich_feature_lineage_integrity_v1`: direct Statcast copies must equal the hash-bound profile; contact-adapter and active rolling overrides require their own lineage; missing, contradictory, post-cutoff, cross-player, rehashed-value, and denominator mutations fail at serialization and probability consumption. | Integrity repair |
| SI-008 | P1 | Pitcher fields can affect probabilities without a receipt-proven pregame starter identity in historical candidates. | Hits, HR, TB | Exclude pitcher block unless receipt contract passes. Existing block retained; full consumer audit pending. | Governance/integrity |
| SI-009 | P1 | The frozen HR formula consumes barrel signal in `H_power`, consumes it again through xSLG, then adds a distribution quality score containing barrel/hard-hit and a further EV/launch-angle term. These are hand-specified, overlapping paths. | HR first; Hits/TB through the shared simulator | Frozen formula retained only as comparator. The repaired challenger is a locked fitted batter-only PA model; no coefficient tuning or claim that the legacy formula was repaired. | Research candidate audit |
| SI-010 | P1 | Invalid K/BB inputs were converted to NaN and routed into the legacy path, contrary to the comment claiming the defect was visible. | Hits, HR, TB | Invalid/nonfinite/out-of-range values now raise; legitimate missing recent rates retain the predeclared season substitution; missing season rates fail when the fitted path is enabled. Regression and mutation tests pass. | Integrity repair |
| SI-011 | P1 | Feature artifacts did not uniformly persist raw counts, denominators, fallback reasons, and source hashes. | Hits, HR, TB | Statcast profile sources, every probability-consumed rich override, and strict point-in-time hitter K/BB now persist and validate this lineage through feature-store round trips and health reports. Non-rich context, pitcher, and market-input blocks remain separately governed and are not certified by this repair. Partial program completion; shared batter probability inputs covered. | Integrity repair |
| SI-012 | P1 | Scheduled collection lacks one hash-bound local supervisor with immutable health/missed records. | Prospective research operations | Task Scheduler publisher/collector/watchdog/report design; isolated dry run only. Pending. | Automation hardening |
| SI-013 | P2 | Output corrections and market policy are separate consumers that can obscure whether probability changes came from data, model, or policy. | Separate markets | Complete for the active prediction path in `probability_consumption_identity_integrity_v1`: correction source/effective-state identity is archived separately from the market-policy hash; output-only probability-incoherent offsets terminate; policy cannot change model identity or rescue a market. No policy or probability was promoted. | Systems/integrity repair |
| SI-014 | P0 | Statcast `player_name` identifies the pitcher, but the first repaired feature reconstruction attached it to the batter ID. | Repaired batter feature identity | Rejected/preserved v1; ambiguous name removed; v2 raw inputs and v3 features use numeric batter identity only. Complete pending a separately verified name map. | Integrity repair |
| SI-015 | P0 | Contact speed is present on some foul pitches; the first reconstructed denominator treated every measured contact as BBE. | Barrel/hard-hit counts and EV summaries | BBE restricted to `type == X`; foul mutation passes; rejected v1 preserved; v3 rebuilt. Complete. | Integrity repair |
| SI-016 | P0 | Empty feature dates serialized as zero-byte CSVs with no columns, defeating deterministic schema validation. | Historical feature artifacts and consumers | Rejected/preserved feature v2; header-only schema artifacts implemented and mutation-tested; v3 rebuilt. Complete. | Integrity repair |
| SI-017 | P2 | The first v3 report labeled its candidate ID `v2` and omitted the report-builder hash. | Candidate identity/provenance | Rejected report preserved; builder now self-bound and candidate correctly labeled. Complete. | Provenance repair |
| SI-018 | P0 | `FeaturePipeline.compute` caught every engineer exception and returned a partial feature dictionary; downstream rich/scalar/league precedence could therefore hide a crash as ordinary fallback. | Hits, HR, TB | Pipeline now fails closed on engineer failure, non-dictionary output, and duplicate feature ownership. Regression and override-mutation tests pass. | Integrity repair |
| SI-019 | P0 | The frozen pitcher HR/9 term had reversed monotonic direction: a higher HR/9 reduced hitter HR probability. | HR; shared simulator | Historical sign is explicitly isolated as `legacy_frozen`; corrected direction is separately selectable and monotonicity-tested. Batter-only v3 excludes the pitcher block. No pitcher candidate may advance without T-4 identity evidence. | Integrity repair/research candidate boundary |
| SI-020 | P0 | The v2 selector called per-PA HR scoring `HR over 0.5` and similarly treated hit/TB PA outcomes as game-market probabilities. A full-game probability requires a point-in-time PA-volume distribution. | Hits, HR over 0.5, Total Bases | v3 labels these PA-foundation diagnostics only and records every market as blocked. Realized game PA is forbidden as a prediction input. | Evaluation-boundary repair |
| SI-021 | P1 | Barrel rate and hard-hit rate overlap because every barrel is also a hard-hit BBE; exposing both rates can duplicate one contact-quality signal and hides sample exposure. | HR first; Hits/TB shared PA foundation | Implemented mutually exclusive barrels, hard-hit non-barrels, and other measured BBE with counts, one denominator, and measured BBE per PA. Fail-closed mutations pass. The single locked 2023 experiment found no material survivor, so the repair is retained but the representation is rejected as a model upgrade. | Integrity repair/rejected research representation |
| SI-022 | P0 | The fitted hitter K/BB bridge could consume current season/last-X snapshots during historical reconstruction, overwrite league-fallback values without changing fallback lineage, and provide no source hash or cutoff proof. | Hits, HR, TB | Repaired with an explicit `point_in_time_required` mode backed only by strict prior-game logs, count/rate/source hashes, and per-field lineage. Source loss now terminates. The frozen legacy mode remains byte-compatible only as a comparator; new fitted-K/BB candidates must enable the simulator lineage gate. | Integrity/governance repair |
| SI-023 | P0 | `model_version` omitted the output-affecting `pa_simulator` block and any active correction-state identity, so fitted K/BB or learned model-parameter changes could retain the same recorded version. | Hits, HR, TB; all archived projections | `pa_simulator` is now version-bound. An active correction forks identity by the canonical effective-state SHA-256 and archives both source and effective hashes. Missing/malformed/inactive states fail closed. | Identity/integrity repair |
| SI-024 | P0 | Reconfiguring the simulator after source-derived league-anchor refresh rebuilt from league defaults only, silently discarding every `pa_simulator` override; applying corrections before that refresh could also overwrite an active correction. | Hits, HR, TB | League reconfiguration now re-consumes the exact config block. Corrections validate before source work and apply exactly once after point-in-time league anchors are established. Override-preservation regression passes. | Probability-consumption repair |
| SI-025 | P0 | Correction-state parsing accepted unknown fields, invalid values, and unconsumed overrides; explicit correction requests could silently degrade to the frozen model. Output offsets changed a displayed mean without changing its probability distribution and rebuilt projections while dropping identity/lineage fields. | Hits, HR, TB; simulation consistency and archives | Strict schema/type/range/field validation is terminal. Explicit requests require one active hash-bound artifact. Decorative overrides and output-only offsets are rejected. The low-level copier preserves all fields, but the active daily path permits only coherent pre-simulation model-parameter corrections. | Probability/lineage repair |

## HR probability-consumption audit

The frozen simulator path is:
`FeatureFactory/RichFeatureEnricher -> PropEngine -> ProbabilityEngine ->
HybridPASimulator -> GameSimulator -> MonteCarlo`. Within the HR logit,
barrel-derived `H_power` is combined with xSLG and barrel boosts; xwOBA,
hard-hit rate, and rolling xwOBA enter `H_quality`; the optional distribution
then adds another composite quality score and another exit-velocity/launch-angle
term. This is not an admissible repaired challenger because the effects are
overlapping and hand-specified. It remains hash-preserved as a comparator.

The source-truth v3 challenger is batter-only and fitted with locked parameters
on 2023, with one selection on 2024. It excludes every pitcher feature and
cannot produce a game-market probability until a separately contracted
point-in-time PA-volume layer is available. The v3 selector evaluates a 2023
league-rate baseline, a time-safe 200-PA empirical-Bayes player baseline, and
an all-prior fitted core comparator. HR promotion additionally remains blocked
until the frozen simulator can be identity-aligned and valid point-in-time
market evidence exists where available.

## 2026 input-repair milestone

Verified candidate `statcast-integrity-v3-7db213dfacc6f820` binds 14 source
chunks, 322,042 input-only rows, 55,400 measured BBE, 88 feature dates, and
38,891 strictly-prior feature rows. The July 21 numeric MLB identity 680757 is
reconstructed only through July 20 with 0 barrels and 10 hard-hit BBE over one
98-BBE denominator. The preserved incident archive remains 0.50/0.094 and is
not overwritten or relabeled. Full tests pass 132/132; focused integrity and
mutation tests pass 15/15.

This milestone is not a probability, calibration, ROI, market, or promotion
result. The frozen baseline remains in force and betting remains unauthorized.

## Direct batter PA v3 adjudication

Candidate `direct_batter_all_prior_regular_season_v3_source_truth_repair` was
built from 87,462 certified 2023/2024 player-game rows after the shared BBE
denominator and consumer repairs. The single locked 2024 selection rejected
all three PA foundations. HR proper scores improved slightly against the
league-rate, time-safe EB player-rate, and all-prior fitted-core comparators,
but failed the 1% materiality gates and lost AUC to the fitted core (0.59644
versus 0.59922). Hits and Total Bases also failed. These are PA diagnostics,
not full-game market probabilities. The frozen simulator is not yet
identity-aligned, verified point-in-time market evidence is unavailable, and
the point-in-time PA-volume layer is missing. Full tests pass 148/148. Exact
metrics, uncertainty intervals, hashes, and the next action are in
`data/analysis/system_integrity_v2/direct_batter_pa_foundation_v3/report.md`.

## 2023 batted-ball composition development

The single locked internal-development experiment replaced overlapping barrel
and hard-hit rates with count-bearing, mutually exclusive barrels, hard-hit
non-barrels, and other measured BBE over one denominator. Across 34,673
chronological 2023 out-of-fold predictions, Hits improved only trivially and
uncertainly versus the legacy representation and was worse than the time-safe
empirical-Bayes comparator. HR was fractionally worse than legacy on Brier,
log loss, and AUC and failed calibration and fold-consistency gates. Neither
component survived. No 2024, 2025 confirmation, May 2026, or market evidence
was opened. The source-truth repair remains; no probability model is promoted.
Exact metrics and hashes are in
`data/analysis/system_integrity_v2/direct_batter_pa_development_2023_v1/report.md`.

## Promotion boundary

An integrity repair is not a performance improvement. Reconstructed artifacts
fork code/config/data/feature/test identities. Hits, HR, and Total Bases must be
evaluated separately and chronologically. HR must independently beat the
league-rate, time-safe empirical-Bayes player-rate, frozen simulator, and valid
market-implied comparators where available. No repaired candidate can use May,
spent HR confirmation as fresh proof, reconstructed prospective evidence, or
unverified prices. Failing any gate retains the frozen baseline.
