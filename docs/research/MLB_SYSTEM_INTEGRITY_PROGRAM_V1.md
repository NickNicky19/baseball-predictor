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
| Persistence and identity | `src/features/feature_store.py`, `src/learning/prediction_archive.py`, `src/utils/model_version.py` | Atomic write, hashes, complete distributions, code/config/data identity | All |
| Corrections and output policy | `src/prediction/correction_manager.py`, market policy modules/config | No post-hoc tuning; invalid input remains research-only/quarantined | Separate gates |
| Operations | daily runners, shadow collectors, Task Scheduler design | Locked runtime, immutable logs, terminal missed records, no invented data | Non-economic until proven |

## Prioritized defect register

| ID | Priority | Finding/root cause | Affected scope | Required remediation/status | Classification |
|---|---:|---|---|---|---|
| SI-001 | P0 | Sparse `barrel` and `hard_hit` source columns were averaged independently. Kwan reached the live feature archive as 0.50 versus 0.094. | HR directly; Hits/TB through shared PA inputs | Preserved archive; shared BBE denominator and fail-closed boundaries implemented. Candidate v3 verified; probability evaluation pending. | Integrity repair |
| SI-002 | P0 | No invariant prevented barrel rate exceeding hard-hit rate after rich overrides or JSON round trip. | Hits, HR, TB | Shared validation at parse, override, serialization, and probability consumption; mutations pass. Complete as integrity boundary. | Integrity repair |
| SI-003 | P0 | General-purpose backfill utilities do not encode the current sealed-month/prospective-evidence boundary. | All research evidence | Contract and runner now skip May before fetching, exclude outcome fields, and forbid prospective classes. Complete for this backfill path. | Research governance repair |
| SI-004 | P0 | Canonical PA builders divide barrels by classified buckets while hard hits use measured EV, so denominator parity is not certified. | Shared batter PA research | Canonical transformers now share the count-bearing primitive and fork schema v2. Pre-2026 reconstruction/evaluation pending. | Integrity repair/new candidate |
| SI-005 | P0 | Direct batter history repeats the mismatched barrel/hard-hit denominators. | Shared batter PA candidate | Direct history now emits common counts/denominator/rates. Pre-2026 reconstruction/evaluation pending. | Integrity repair/new candidate |
| SI-006 | P1 | Missing Statcast fields can silently become league averages; fetch failures can return an empty frame. | Hits, HR, TB | Make source/fallback status explicit and quarantine silent source loss; never disguise failure as player evidence. Pending. | Integrity repair |
| SI-007 | P1 | Rich features override scalar fields independently without stored count/denominator lineage. | Hits, HR, TB | Bind effective values to source/count/denominator/window; reject contradictory partial overrides. Partial invariant implemented. | Integrity repair |
| SI-008 | P1 | Pitcher fields can affect probabilities without a receipt-proven pregame starter identity in historical candidates. | Hits, HR, TB | Exclude pitcher block unless receipt contract passes. Existing block retained; full consumer audit pending. | Governance/integrity |
| SI-009 | P1 | Active PA formulas contain multiple hand-specified scaling and interaction paths that may double count the same batter quality. | HR first; Hits/TB | Trace every term and run locked ablation/monotonicity/simulation-consistency diagnostics only after source repair. Pending. | Research candidate audit |
| SI-010 | P1 | Some invalid K/BB inputs are converted to NaN and routed into a legacy path rather than failing closed. | Hits, HR, TB | Separate legitimate missingness from invalid values; mutation-test the consumer. Pending. | Integrity repair |
| SI-011 | P1 | Feature artifacts do not uniformly persist raw counts, denominators, fallback reasons, and source hashes. | Hits, HR, TB | Versioned lineage schema and reconstruction manifest. Pending. | Integrity repair |
| SI-012 | P1 | Scheduled collection lacks one hash-bound local supervisor with immutable health/missed records. | Prospective research operations | Task Scheduler publisher/collector/watchdog/report design; isolated dry run only. Pending. | Automation hardening |
| SI-013 | P2 | Output corrections and market policy are separate consumers that can obscure whether probability changes came from data, model, or policy. | Separate markets | Complete consumption map, model-version binding, and no post-outcome policy mutation. Pending. | Systems audit |
| SI-014 | P0 | Statcast `player_name` identifies the pitcher, but the first repaired feature reconstruction attached it to the batter ID. | Repaired batter feature identity | Rejected/preserved v1; ambiguous name removed; v2 raw inputs and v3 features use numeric batter identity only. Complete pending a separately verified name map. | Integrity repair |
| SI-015 | P0 | Contact speed is present on some foul pitches; the first reconstructed denominator treated every measured contact as BBE. | Barrel/hard-hit counts and EV summaries | BBE restricted to `type == X`; foul mutation passes; rejected v1 preserved; v3 rebuilt. Complete. | Integrity repair |
| SI-016 | P0 | Empty feature dates serialized as zero-byte CSVs with no columns, defeating deterministic schema validation. | Historical feature artifacts and consumers | Rejected/preserved feature v2; header-only schema artifacts implemented and mutation-tested; v3 rebuilt. Complete. | Integrity repair |
| SI-017 | P2 | The first v3 report labeled its candidate ID `v2` and omitted the report-builder hash. | Candidate identity/provenance | Rejected report preserved; builder now self-bound and candidate correctly labeled. Complete. | Provenance repair |

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

## Promotion boundary

An integrity repair is not a performance improvement. Reconstructed artifacts
fork code/config/data/feature/test identities. Hits, HR, and Total Bases must be
evaluated separately and chronologically. HR must independently beat the
league-rate, time-safe empirical-Bayes player-rate, frozen simulator, and valid
market-implied comparators where available. No repaired candidate can use May,
spent HR confirmation as fresh proof, reconstructed prospective evidence, or
unverified prices. Failing any gate retains the frozen baseline.
