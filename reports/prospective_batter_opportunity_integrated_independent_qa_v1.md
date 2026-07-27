# Prospective Batter Opportunity Integrated Runtime — Independent QA v1

## Decision

Exact candidate commit reviewed: `5e761c442f2b6cda1014a0db356ac2c564f11028` on `codex/prospective-target-opportunity-evidence-v1`.

| Decision | Result |
|---|---|
| Preserve this candidate for repair and continued research | **PASS** |
| Clean Linux verification of the exact candidate tree | **PASS** |
| Eligible to seek explicit future-only non-economic smoke deployment authorization | **FAIL** |

The candidate materially improves source truth, chronology, roster/schedule semantic replay, sanitization, and immutable history evidence. It remains ineligible for even a future-only smoke authorization because the runtime can silently report a successful all-zero tick while leaving no durable terminal record for (a) receipt-proven prior-day sides missed during a full-day outage and (b) a current T−4 plan whose required upstream roster ledger is absent. Those are operational evidence-integrity failures, not predictive-performance findings.

This report does **not** authorize deployment, fitting, scoring, prediction consumption, promotion, activation, economic use, or betting.

## Scope and isolation

- Worktree: `C:\Projects\baseball_predictor_prospective_evidence_v1`
- Branch and HEAD matched the requested commit exactly; the tracked worktree was clean before QA.
- Scope was limited to the prospective batter-opportunity runtime, contracts/configuration, tests/reports, Linux workflow, and the modified projected-lineup roster ledger/runner boundary.
- No sealed May artifact or path content was opened, enumerated, parsed, copied, or written. May coverage was assessed from source and synthetic fail-closed tests only.
- No outcome archive, economic/price evidence, AWS resource, live collector/process, production tree, or spent confirmation evidence was inspected.
- No code, configuration, test, fixture, or existing report was modified. This report is the sole file created by QA.

## Findings

### PBO-IQA-001 — Full-day outage loses the required terminal planning-miss record

- **Severity:** BLOCKER
- **Confidence:** High
- **Paths/symbols:** `scripts/run_prospective_batter_opportunity_tick.py::run_all`, especially the current-date-only plan lookup at lines 339–350 and planning loop at lines 358–446.
- **Evidence:** planning inspects only `plan_dir/<current Eastern date>.plan.json`. It never revisits earlier non-May T−4 plans/roster ledgers to terminalize receipt-proven sides that were never planned. In an isolated July-only probe, a valid prior-day T−4 plan, captured roster terminal, schedule raw, and roster raw existed. The collector's first run was the following day. It returned `collector_state=processed` with every planning and collection count equal to zero; no history root or `planning_terminal` record was created.
- **Impact:** an ordinary day-long outage permanently removes the audit trail distinguishing “collector never ran” from “no expected evidence.” The missed side cannot later satisfy a terminal-coverage audit, and the runtime's success output hides the gap. Backfilling the game record remains correctly forbidden, but recording the immutable miss is not backfilling.
- **Smallest correct repair:** scan every permissible non-May T−4 plan date from the evidence epoch through the current date. For each captured roster side lacking both a pregame history plan and a planning terminal, create `missed_before_plan` at/after first pitch using the retained T−4 proof; never create a late capture plan. Date traversal must skip May before constructing or inspecting a May path.
- **Required regression/mutation tests:** first run one day and several days late; restart across an off day; restart across the sealed month without forming a sealed path; verify exactly one immutable planning terminal per receipt-proven missed side; prove no fetch and no late plan; detect mutation and duplicate plan/terminal overlap.
- **Blocks:** smoke-deployment authorization, durable evidence collection, protocol locking, fitting, shadow use, promotion, and activation.

### PBO-IQA-002 — Missing same-day upstream roster evidence is a silent successful tick

- **Severity:** BLOCKER
- **Confidence:** High
- **Paths/symbols:** `scripts/run_prospective_batter_opportunity_tick.py::run_all`, lines 350–386.
- **Evidence:** when today's T−4 plan exists but its expected roster-ledger manifest is absent, the `manifest_path.is_file()` branch is skipped without an error or an explicit unavailable count. A temporary July-only probe returned `collector_state=processed`, all-zero planning/collection counts, `roster_unavailable=0`, and no history ledger.
- **Impact:** upstream collection failure, path drift, or incomplete deployment is indistinguishable from a healthy no-op. Automation and health monitoring can therefore miss the exact condition that prevents future opportunity evidence.
- **Smallest correct repair:** if a valid current T−4 plan exists and its bound roster ledger/manifest is absent, fail closed with a distinct non-success collector state and CLI exit status, or write a separate hash-bound operational-health terminal that is explicitly ineligible as model evidence. Do not fabricate a receipt-proven planning terminal when no roster proof exists.
- **Required regression/mutation tests:** missing ledger root, missing manifest, unreadable manifest, expected plan with zero terminals, partial side coverage, and recovery on a later tick; every case must be explicit and must never report a healthy all-zero processed tick.
- **Blocks:** smoke-deployment authorization and operational readiness.

### PBO-IQA-003 — History proof bundles do not independently replay retained schedule bytes

- **Severity:** MEDIUM
- **Confidence:** High
- **Paths/symbols:** `ProspectiveOpportunityHistoryLedger._verified_plan_bundle`, `_verify_plan_proof`, `append_planning_exclusion`, `_verify_planning_exclusion`.
- **Evidence:** plan creation correctly calls `source_roster_ledger.verify()`, which now replays schedule and roster bytes. The copied history proof then stores the T−4 plan, roster terminal, and roster raw bytes, but not the schedule raw bytes. Later history-ledger verification can replay the roster receipt and internal plan/terminal identities, but cannot rerun `projected_lineups_from_schedule` from the proof bundle itself.
- **Impact:** the history proof is self-contained for active-roster receipt identity but not for official schedule provenance. The wording “self-contained proof” is too broad unless the upstream roster ledger is permanently co-retained and independently verified alongside it.
- **Smallest correct repair:** either include the fields-limited schedule raw bytes plus observation timestamp in the history proof and replay them, or make co-retention of the exact upstream roster ledger an explicit contract and require downstream verification to resolve and replay it.
- **Required regression test:** remove or alter the upstream schedule evidence after plan publication and prove downstream verification cannot claim complete semantic provenance.
- **Blocks:** a claim of fully self-contained downstream semantic replay; it is not the primary smoke blocker if exact upstream and history ledgers are guaranteed to be co-retained.

### PBO-IQA-004 — Linux dependency/action pinning is version-exact but not content-hash locked

- **Severity:** LOW
- **Confidence:** High
- **Paths:** `.github/workflows/prospective-batter-opportunity-linux.yml`, `requirements-prospective-batter-opportunity-ci.lock`.
- **Evidence:** Python and five test packages are version-pinned, but package hashes are absent and pip does not use `--require-hashes`. Workflow actions use major tags (`actions/checkout@v5`, `actions/setup-python@v6`) rather than immutable commit SHAs. The successful run resolved them to `fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09` and `ece7cb06caefa5fff74198d8649806c4678c61a1` respectively.
- **Impact:** the observed Linux execution is valid, but future reruns are not fully supply-chain identical from repository bytes alone.
- **Repair:** pin action commit SHAs and use a hash-complete requirements file with `pip --require-hashes`.
- **Blocks:** maximal reproducibility, not the validity of the completed Linux run.

## Verified controls

### Source truth and identity

- Exact HTTPS host/path/query, HTTP 200, and JSON content type are revalidated downstream.
- Full final-feed transport is sanitized before persistence; only game/date/status/team/player/batting-order/PA fields survive.
- Source-invalid transport retains hash and byte size only, with no unsafe raw publication.
- The active-roster ledger now replays schedule observation time, game/date/start/team/side identity and active-roster raw/receipt identity.
- Runtime configuration, opportunity contract, upstream roster contract/code, collector code, evidence scope, and ledger manifests are hash-bound.
- All 20 artifact hashes declared in `reports/prospective_batter_opportunity_evidence_v1.json` recomputed successfully.
- Recomputed collector identities: roster `adb894089139cbfe62e0f241c9d46c4541ed45d97dbc9290422a27fd90282527`; opportunity `e4b5492f762f2b7a3480084ef3d12ba091838d187385656b8326e5cb98d12420`.

### Chronology, no backfill, and missingness

- Evidence scope requires a canonical non-May collection epoch and aware creation timestamp.
- History plans must be created after their T−4 roster receipt and before first pitch.
- Final receipt collection is bounded by the immutable plan deadline; expired plans become `deadline_missing` without fetching.
- Network/nonfinal states remain retryable only inside the declared window.
- A receipt-proven side first seen after first pitch becomes `missed_before_plan` rather than a backdated plan when it is inspected that day.
- Missing expected final games remain explicit and never enter opportunity denominators as zero.
- The uncovered restart and missing-upstream cases are the exceptions documented in PBO-IQA-001 and PBO-IQA-002.

### May fail-closed behavior

- A sealed-month tick returns before constructing or inspecting plan, roster-ledger, or history-ledger paths and before any source call.
- Historical ledger scanning skips a sealed date before constructing its date path.
- No sealed artifact/path content was used in this review.

### Immutable and non-economic boundaries

- Plan, proof, raw, terminal, snapshot, and scope records are publish-once and content-hash checked; orphaned raw receipts fail verification.
- Snapshot semantic replay occurs before any snapshot raw/terminal publication.
- No target-game prediction, model fit, calibration, score, price, settlement, execution, ROI, or betting authority exists in this runtime.
- Final PA receipts are prior-game descriptive research inputs only and are not consumed by a production probability path in this commit.

## Local execution evidence

Executable: `C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe`

Environment: `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=.`; pytest cache disabled; no dependency installed or changed.

Focused command:

```powershell
& 'C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe' -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\prospective-opportunity-integrated-qa-focused' tests/test_prospective_batter_opportunity.py tests/test_prospective_batter_opportunity_history.py tests/test_run_prospective_batter_opportunity_tick.py
```

Result: **64 passed in 0.84s**.

Related command:

```powershell
& 'C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe' -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\prospective-opportunity-integrated-qa-related' tests/test_prospective_batter_opportunity.py tests/test_prospective_batter_opportunity_history.py tests/test_projected_lineup_contract.py tests/test_projected_lineup_official_roster.py tests/test_projected_lineup_roster_runner.py tests/test_projected_lineup_history.py tests/test_run_prospective_batter_opportunity_tick.py tests/test_aws_projected_lineup_roster_runtime.py
```

Result: **95 passed in 1.11s**.

Two additional temporary July-only probes reproduced PBO-IQA-001 and PBO-IQA-002. They touched only temporary directories and made no candidate changes.

## Linux verification

**PASS for the exact candidate file tree.**

- Pull request: `#30`
- Workflow/run: `Prospective Batter Opportunity Linux Gate`, run `30265441377`
- Result: completed successfully on Ubuntu `24.04.4`, CPython `3.12.3`, pytest `9.1.1`.
- Linux focused suite: **64 passed in 0.69s**.
- Linux related suite: **95 passed in 1.26s**.
- Dependency check passed; lock-file SHA-256 was `b01968cfd800101e7ebce4d8e2cb14b45d141fda20c7668fc0a24e1241350044`.
- Secret scan: no likely credentials in 747 tracked files.
- Checkout mutation check passed.

The pull-request workflow checked out synthetic merge commit `187b614eaf17b481b3778a1ad3d80c348d37d211`, not the head commit object directly. Independent verification found both the merge commit and requested head commit have the same tree SHA-1, `40a9c1450fe88f77e85791c3085e3fd29265ac62`; therefore the executed repository bytes match the requested candidate tree.

## Test-quality assessment

The test suite has strong positive-schema, chronology, identity, raw mutation, hash mutation, no-retention, deadline, network-retry, denominator, May guard, and prepublication-replay coverage. It correctly detected the earlier library defects. It does not cover a first run after an entire missed game date or the missing-current-roster-ledger success path; both escaped 95 passing related tests. It also tests source behavior mostly with synthetic fixtures plus one sanitized static pre-2026 source-surface fixture, not a live operational source.

## Required remaining gates

1. Repair PBO-IQA-001 and PBO-IQA-002 at the runtime source and add the required restart/source-absence tests.
2. Resolve or explicitly contract the schedule-proof limitation in PBO-IQA-003.
3. Freeze new code/config/test/report hashes and rerun independent local and Linux QA on the repaired exact tree.
4. Only if those checks pass, seek explicit human authorization for a separate future-only, non-May, non-economic operational smoke.
5. Preserve all smoke missingness and failures without backfill; do not consume smoke outputs as model probabilities or performance evidence.
6. Untouched prospective accumulation, protocol locking, market-specific adjudication, and all model/economic gates remain future work.

## Final conclusion

Commit `5e761c4` is worth preserving and its exact tree is Linux-verified. It is not yet eligible to seek smoke deployment authorization because it can silently lose or conceal required planning-missing evidence under realistic restart and upstream-absence conditions. Repair those operational truth defects; do not deploy around them and do not reinterpret 64/95 passing tests as evidence of predictive improvement.
