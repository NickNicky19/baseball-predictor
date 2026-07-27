# Prospective Batter Opportunity Evidence v1 — Independent QA

## Decision

**VERDICT: NOT READY FOR RUNTIME DEPLOYMENT OR EVIDENCE COLLECTION.**

The candidate is a useful, research-only Phase 0 foundation, and its core chronology, source sanitization, explicit missingness, replay, and non-production boundaries are substantially implemented. It is not safe to deploy yet because two integrity boundaries remain open:

1. a history capture plan can claim linkage to a T−4 plan and active-roster receipt using arbitrary well-formed SHA-256 strings; neither plan construction nor the history ledger resolves and replays the referenced evidence; and
2. the side ledger writes a snapshot immutably after only the lightweight `validate_snapshot` check. A semantically false snapshot with a recomputed hash is accepted and becomes an immutable terminal before `verify()` later rejects it.

The final candidate hash manifest matches the delivered bytes. Its reported 58-test selection is not independently reconstructible because the report does not record the exact command/file list; QA therefore reports its own exact 55-test related selection separately.

This verdict does **not** authorize fitting, scoring, prediction, production probability consumption, market evaluation, deployment, promotion, activation, or betting.

## Scope and isolation

- Candidate reviewed: `C:\Projects\baseball_predictor_prospective_evidence_v1`
- Branch: `codex/prospective-target-opportunity-evidence-v1`
- Base/HEAD: `adb4f481009927102bc400eca5bf1766cd2aafe6`
- Reviewed only the new prospective-batter-opportunity contract, reports, three implementation modules, two test modules, and the final sanitized pre-2026 offline fixture.
- Did not inspect any May 2026 artifact, target-game outcome archive, price/economic evidence, AWS state, active process, or dirty live working tree.
- The only May references exercised were synthetic negative-control inputs already present in the isolated tests.
- No candidate source, configuration, tests, fixture, model, or runtime artifact was changed by QA. This report is the sole QA write.

## Final byte identities reviewed

| Artifact | SHA-256 |
|---|---|
| `config/prospective_batter_opportunity_contract_v1.json` | `4e7d5a4bf861de75070a39d8ba0f9b13c699a44d55963eee264deb4643e48599` |
| `src/evaluation/prospective_batter_opportunity.py` | `fc5ca7d433a94a2c848635adb37b64a501f0746dfff10e053729a7489943abed` |
| `src/evaluation/prospective_batter_opportunity_history.py` | `c6fe8e7908d8a715a8d313b29434aba6f6b64352b7f3de6d8255a617187160b9` |
| `src/evaluation/prospective_batter_opportunity_ledger.py` | `f652022f89fe5c48cdfbd451862b01881567984f1286d8c45ae0284a84fe4a4d` |
| `tests/test_prospective_batter_opportunity.py` | `86055f1fcb47adbd66ca6f4ba84fa45d240d321b9a5f11916409a41f60d0e3ec` |
| `tests/test_prospective_batter_opportunity_history.py` | `f492b4198f0d847bb652dc172db5717c357d8f4334db8963600fe82c91ded767` |
| `tests/fixtures/prospective_batter_opportunity/official_mlb_717545_sanitized_v1.json` | `37a1a9f7b8c713c154fd0e1c4ec2ac70dc1030c5787dfdf062b41aaee5a5158b` |
| `reports/prospective_opportunity_real_source_audit_v1.json` | `cb385e41dd056996f9813333d08cb16fa2d8d999129166e908d11da632b9d674` |
| `reports/prospective_batter_opportunity_evidence_v1.json` | `a073dc53f6e5efdeb6953aae60690eea3c3f627a4e07102c969dbd9234f270d5` |

## Findings

### QA-PBO-001 — History-plan roster binding is asserted, not proven

- **Severity:** BLOCKER
- **Paths/symbols:** `src/evaluation/prospective_batter_opportunity.py::build_history_capture_plan`, `validate_history_capture_plan`; `src/evaluation/prospective_batter_opportunity_history.py::ProspectiveOpportunityHistoryLedger.append_plan`, `_read_plan`
- **Evidence:** the three linkage fields are validated only as 64-character lowercase digests. No source T−4 plan, roster-side terminal, active-roster receipt, or roster raw bytes are supplied, resolved, or replayed. The tests intentionally construct accepted plans with `"a" * 64`, `"b" * 64`, and `"c" * 64`.
- **Impact:** a syntactically valid future plan can falsely claim an existing T−4 roster basis. The later history record and snapshot faithfully preserve that unproven claim. This defeats the stated receipt-proven roster-binding contract even though the plan itself is pre-outcome and hash-stable.
- **Required repair:** the runtime planner must resolve an existing immutable T−4 plan and side terminal, replay the active-roster receipt from retained raw bytes, and verify plan/game/date/side/team/horizon/target/receipt identities before constructing or appending a history plan. Persist enough references for independent replay; arbitrary caller-supplied digests must fail closed.
- **Required regression/mutation proof:** reject nonexistent plan hashes, arbitrary receipt hashes, wrong side target, wrong game/team/date/horizon, a nonterminal roster entry, altered raw bytes, and a receipt that does not replay to the referenced digest.
- **Blocks:** evidence collection, deployment, protocol locking, fitting, shadow use, promotion, activation.

### QA-PBO-002 — Semantically false snapshots can enter the immutable side ledger

- **Severity:** BLOCKER
- **Paths/symbols:** `src/evaluation/prospective_batter_opportunity_ledger.py::ProspectiveBatterOpportunityLedger.append_snapshot`; `src/evaluation/prospective_batter_opportunity.py::validate_snapshot`
- **Evidence:** `append_snapshot` calls the lightweight validator, checks target identity and raw-map hashes, then publishes. It does not rebuild the snapshot from the roster/history/schedule materials before `_publish_once`. In a temporary mutation probe, QA incremented `prior_starts`, recomputed `snapshot_sha256`, and supplied all original raw materials. `append_snapshot` returned `True`; only the subsequent `ledger.verify()` rejected the immutable terminal with `captured opportunity snapshot differs from replay`.
- **Impact:** an integration bug can permanently occupy a side target with invalid evidence. Later verification detects the defect but cannot replace the publish-once terminal, turning a preventable input error into permanent missing evidence.
- **Required repair:** perform the same full `build_pregame_opportunity_snapshot` replay and exact equality check inside `append_snapshot` before any raw or terminal publication. Prefer staging/replay before publishing any referenced raw files, then verify the newly published entry.
- **Required regression/mutation proof:** a rehashed mutation to every derived count, denominator, feature row, coverage field, roster binding, history list, or schedule list must be rejected before `terminal/<side_target_id>.json` exists.
- **Blocks:** evidence collection, deployment, protocol locking, fitting, shadow use, promotion, activation.

### QA-PBO-003 — Test execution provenance is incomplete and one classification is stale

- **Severity:** LOW
- **Path:** `reports/prospective_batter_opportunity_evidence_v1.json`
- **Evidence:** all declared code/config/test/fixture/source-audit hashes recomputed successfully, and the fixture state is now accurate. The report records `58 passed` without the exact command or selected files. It also says `tests_are_synthetic_only: true` although the new suite contains a sanitized real-source surface fixture (correctly classified as non-prospective evidence).
- **Impact:** candidate identity is reproducible, but the exact 58-test claim and the test-corpus classification are not self-contained.
- **Required repair:** record executable, environment controls, exact test paths/options, and captured output; describe the corpus as synthetic mutation tests plus one sanitized pre-2026 static source-surface fixture.
- **Required regression:** a manifest-verification test must recompute every declared path hash and fail on unlisted required candidate artifacts.
- **Blocks:** exact execution-claim certification only; it does not invalidate QA's independently passing selections.

### QA-PBO-004 — Source-invalid no-retention behavior is implemented but lacks a direct regression assertion

- **Severity:** MEDIUM
- **Paths/symbols:** `src/evaluation/prospective_batter_opportunity_history.py::run_history_tick`, `append_exclusion`; `tests/test_prospective_batter_opportunity_history.py::test_malformed_required_opportunity_field_is_terminal_source_invalid`
- **Evidence:** current code records only the full transport SHA-256 and byte size for `source_invalid`; it does not pass the unsafe bytes to `_raw`. QA’s temporary probe confirmed `raw_exists=False`, `raw_ref=None`, and hash/size present. The shipped test checks only the terminal count, so a future regression that stores unsafe bytes could pass.
- **Impact:** current behavior is correct, but the outcome-isolation repair is not protected at its most sensitive failure path.
- **Required repair:** add assertions that no raw file is created, `raw` is null, the stored transport hash/size match the rejected bytes, and ledger replay still succeeds.
- **Blocks:** deployment until regression coverage is added; not a current implementation failure.

### QA-PBO-005 — The reported 58-test result is not independently reproducible from a recorded command

- **Severity:** LOW
- **Path:** `reports/prospective_batter_opportunity_evidence_v1.json`
- **Evidence:** no exact command or file list accompanies `related_chronology_and_contract_suite: 58 passed`. QA’s explicitly listed, narrow related suite collected and passed 55 tests after the new offline fixture test.
- **Impact:** the claim may reflect a different valid selection, but it cannot be independently reconstructed from the report.
- **Required repair:** record executable, environment controls, exact test paths, options, and output for every claimed suite.
- **Blocks:** exact claim verification only.

## Area-by-area adjudication

| Area | Result | QA conclusion |
|---|---|---|
| P0/Phase 0 conclusion | **PARTIALLY VERIFIED** | A previously absent prospective opportunity-evidence boundary now exists and is probability-free. Deployment readiness is not earned. |
| Chronology and May seal | **VERIFIED for exercised interfaces** | Canonical dates, aware UTC timestamps, prior-date-only history, T−4 schedule age/late checks, post-start final capture, predeclared deadlines, and synthetic May rejection are enforced. No May artifact was accessed. |
| Predeclared plan/no backfill | **VERIFIED with binding blocker** | Plans must be created before start; final receipts must fall inside their declared window; missed deadlines are terminal and never retried. The referenced roster evidence is not independently proven (QA-PBO-001). |
| Active-roster identity | **VERIFIED at snapshot replay; NOT VERIFIED at history-plan creation** | Snapshot construction replays the target T−4 roster raw exactly. History plans merely carry unproven digests. |
| Schedule denominator replay | **VERIFIED for the implemented contract** | Exact source/query/schema, freshness, team/game/date identity, complete requested-range coverage, raw replay, non-overlap, and captured/missing partition are enforced. Nonfinal games remain explicit and outside the final-game denominator. |
| Sanitizer/outcome-field isolation | **VERIFIED** | Captured history retains only the positive canonical game/date/status/team/player/batting-order/PA surface. Extra season/outcome fields are discarded. Source-invalid transport bytes are hash/size-only in current code. |
| Missingness | **VERIFIED** | Missing expected final games are terminal, explicit, make coverage incomplete, disable fit eligibility, and are never converted to zero opportunities. |
| Immutable ledgers | **PARTIALLY VERIFIED** | Publish-once files, content hashes, traversal controls, orphan detection, and replay verification work. Prepublication semantic replay is missing for side snapshots (QA-PBO-002). |
| Production-probability boundary | **VERIFIED** | No candidate probability, fit, calibration, scoring, market comparison, or production import was added; all artifacts state research-only, non-betting, and non-production consumption. |
| Real-source fixture | **VERIFIED as static source-surface replay only** | The sanitized pre-2026 projection is hash-bound, excludes named outcome fields, and replays both sides offline. It is not prospective evidence and does not prove live runtime reliability. |
| Test quality | **PARTIALLY VERIFIED** | Strong synthetic mutation coverage exists and all selected tests pass. Missing tests are identified in QA-PBO-001, QA-PBO-002, and QA-PBO-004. Linux/runtime/network behavior remains untested. |

## Independent execution evidence

Python executable used for all QA execution:

`C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe`

Interpreter/runtime observed: Python `3.14.6`; pytest `9.1.1`. No package was installed.

Focused final suite:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='.'
& 'C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe' -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\prospective-opportunity-qa-focused-current' tests/test_prospective_batter_opportunity.py tests/test_prospective_batter_opportunity_history.py
```

Result: **37 passed in 0.41s**.

Narrow related chronology/contract suite:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='.'
& 'C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe' -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\prospective-opportunity-qa-related-current' tests/test_prospective_batter_opportunity.py tests/test_prospective_batter_opportunity_history.py tests/test_projected_lineup_contract.py tests/test_projected_lineup_official_roster.py tests/test_projected_lineup_roster_runner.py
```

Result: **55 passed in 0.51s**.

Additional safe temporary probes:

- A rehashed derived-feature mutation was accepted by `append_snapshot` and rejected only by later `verify()` (QA-PBO-002).
- A malformed required source field produced `source_invalid`, no raw directory/reference, and retained only transport hash/size (QA-PBO-004).
- Read-only syntax compilation via Python `compile()` succeeded for the three implementation modules and two original test modules before the final fixture-only update.

## Readiness

| Stage | Decision |
|---|---|
| Preserve as research candidate for repair | **YES** |
| Thin runtime integration development in an isolated branch | **YES, repair-only** — implement QA-PBO-001 and QA-PBO-002 before any collector can write evidence |
| Dry-run collector that writes disposable temporary evidence | **NO until blockers are repaired and retested** |
| Durable prospective evidence collection | **NO** |
| Protocol locking | **NO** |
| Fitting or statistical evaluation | **NO** |
| Shadow prediction consumption | **NO** |
| Production deployment/promotion/activation | **NO** |
| Betting authorization | **NO** |

## Highest-value next action

Repair the immutable ingress boundary first: make `append_snapshot` perform full prepublication replay, and make the runtime history planner prove the referenced T−4 roster terminal/raw rather than accepting digest-shaped claims. Add the exact negative controls above, freeze all bytes, regenerate the hash-bound report, rerun independent QA on Linux, and only then consider a non-production runtime dry run.
