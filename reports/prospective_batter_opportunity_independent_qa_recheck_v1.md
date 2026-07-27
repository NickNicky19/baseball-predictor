# Prospective Batter Opportunity Evidence v1 — Independent QA Recheck

## Decision

**RECHECK VERDICT: QA-PBO-001, QA-PBO-002, and QA-PBO-004 are CLOSED in the repaired exact bytes.**

**Integration readiness:** PASS for beginning the isolated thin runtime planner/adapter integration.

**Deployment readiness:** NOT YET READY for durable runtime deployment or prospective evidence collection. The repaired library boundaries now pass recheck, but the candidate still has no completed runtime planner/adapter, no Linux runtime verification, and no end-to-end terminal network/source-failure dry run. Those are explicit remaining gates, not failures of the three repaired defects.

No fitting, scoring, prediction consumption, market evaluation, promotion, activation, or betting is authorized.

## Scope and isolation

- Candidate: `C:\Projects\baseball_predictor_prospective_evidence_v1`
- Branch: `codex/prospective-target-opportunity-evidence-v1`
- Base/HEAD: `adb4f481009927102bc400eca5bf1766cd2aafe6`
- Rechecked only the repaired exact bytes relevant to QA-PBO-001, QA-PBO-002, QA-PBO-004, their tests, and the candidate hash/test-provenance report.
- Did not inspect May 2026 artifacts, outcomes, economic/price evidence, AWS, active processes, or the dirty live project tree.
- No source, configuration, test, fixture, model, or runtime artifact was modified. This recheck report is the sole write.

## Repaired byte identities

| Artifact | SHA-256 |
|---|---|
| `config/prospective_batter_opportunity_contract_v1.json` | `1714310ebd23c2b712975d4faabb1a066bd26806adaf0335fd9af66a3a20be81` |
| `src/evaluation/prospective_batter_opportunity.py` | `fc5ca7d433a94a2c848635adb37b64a501f0746dfff10e053729a7489943abed` |
| `src/evaluation/prospective_batter_opportunity_history.py` | `40683ae56a2276030823b924c33372691169be8bbd3adf0efd30e012401da43e` |
| `src/evaluation/prospective_batter_opportunity_ledger.py` | `268b4c3fd646beeac4f1ffc77a7e55e93a202baa73fd8c5b2a065e3cc1c79d4d` |
| `tests/test_prospective_batter_opportunity.py` | `1580eff1008cf5e94f88f9b5c67812d7fd8ec8eb35899c9be67b88a9cf36ab3f` |
| `tests/test_prospective_batter_opportunity_history.py` | `dc422aa28338d4401ff5b8eb3e976a4f3d55321a0100b2b4e233399b945ea40d` |
| Static sanitized source-surface fixture | `37a1a9f7b8c713c154fd0e1c4ec2ac70dc1030c5787dfdf062b41aaee5a5158b` |
| Original independent QA report | `c4cceb742203bec5b898b45b58af3ad86a5cd82410ac256a58f318aaaaeaadf0` |
| Candidate evidence report | `755a09cf405a70667d8fb265d35d2eaa51cfcd86833f52b40b13d07a70120dc3` |

All paths declared by the candidate evidence report recomputed to their declared SHA-256 values.

## Closure adjudication

### QA-PBO-001 — CLOSED

The history ledger no longer accepts an unproved digest-only roster binding.

- `ProspectiveOpportunityHistoryLedger.append_plan` now requires a `ProjectedLineupRosterLedger`.
- The source roster ledger is verified before the history plan is published.
- The source T−4 plan must be a valid T−4 plan and contain the exact game/date/start target.
- The exact side target, captured terminal, plan/game/team/date/side identities, roster receipt digest, committed time, retained raw digest, and active-roster receipt replay are checked.
- The verified T−4 plan, captured side terminal, and canonical base64 roster raw proof are stored in the immutable history-plan proof bundle and revalidated whenever the stored plan is read.
- The plan cannot predate the active-roster receipt.

The old failure modes were rerun: arbitrary source plan digest, arbitrary receipt digest, arbitrary side target, mutated raw, wrong game, wrong team, wrong date, wrong horizon, non-captured terminal, and plan creation before roster commitment all failed before a history plan file was created.

### QA-PBO-002 — CLOSED

`ProspectiveBatterOpportunityLedger.append_snapshot` now performs a complete `build_pregame_opportunity_snapshot` replay and requires exact equality before publishing any raw or terminal artifact.

The old safe mutation—incrementing `prior_starts` and recomputing `snapshot_sha256`—was rerun. It was rejected with the prepublication replay boundary, and neither the `terminal` nor `raw` directory was created.

This closes the prior risk that a semantically false but internally rehashed snapshot could permanently occupy an immutable side target.

### QA-PBO-004 — CLOSED

The direct source-invalid retention regression now proves that:

- terminal state is `source_invalid`;
- the terminal raw reference is null;
- the rejected transport SHA-256 and byte size are recorded exactly;
- no raw directory is created; and
- ledger verification succeeds on the hash/size-only terminal.

The old malformed-required-field path was rerun and passed these assertions.

## Independent execution evidence

Executable:

`C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe`

Environment: `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=.`. No package was installed.

Focused repaired-candidate suite:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='.'
& 'C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe' -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\prospective-opportunity-qa-recheck-focused' tests/test_prospective_batter_opportunity.py tests/test_prospective_batter_opportunity_history.py
```

Result: **50 passed in 0.54s**.

Exact related suite recorded by the candidate report:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='.'
& 'C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe' -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\prospective-opportunity-qa-recheck-related' tests/test_prospective_batter_opportunity.py tests/test_prospective_batter_opportunity_history.py tests/test_projected_lineup_contract.py tests/test_projected_lineup_official_roster.py tests/test_projected_lineup_roster_runner.py tests/test_projected_lineup_history.py
```

Result: **75 passed in 0.62s**.

Targeted rerun of the three old QA defect paths:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='.'
& 'C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe' -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\prospective-opportunity-qa-recheck-old-mutations' 'tests/test_prospective_batter_opportunity_history.py::test_plan_requires_replayed_t4_roster_proof' 'tests/test_prospective_batter_opportunity.py::test_ledger_rejects_rehashed_semantic_snapshot_mutation_before_publication' 'tests/test_prospective_batter_opportunity_history.py::test_malformed_required_opportunity_field_is_terminal_source_invalid'
```

Result: **12 passed in 0.33s**.

The candidate report now records the Python executable, environment controls, pytest options, exact focused paths, exact related paths, test corpus classification, and passing counts. The command provenance is independently reproducible.

## Remaining deploy gates

These items remain outside this narrow defect recheck:

1. implement the thin runtime planner/adapter that invokes the now-proven ledger APIs without changing the existing shadow prediction stream;
2. exercise retryable network/nonfinal behavior and terminal source/missed behavior end to end in disposable evidence roots;
3. verify the exact release and dependencies on Linux;
4. rerun independent QA over the integrated exact bytes; and
5. obtain explicit human deployment authorization.

## Final readiness matrix

| Stage | Decision |
|---|---|
| Three initial QA repairs | **PASS — CLOSED** |
| Preserve repaired engineering candidate | **YES** |
| Begin isolated thin runtime integration | **YES** |
| Deploy durable collector now | **NO — remaining runtime/Linux gates** |
| Treat any collected record as prospective evidence before integrated QA | **NO** |
| Protocol locking, fitting, scoring, shadow probability use, promotion, activation, or betting | **NO** |

## Highest-value next action

Build only the thin, non-production runtime planner/adapter around these repaired APIs, run it against disposable evidence roots with forced source/network/missed-window failures, verify it on Linux, freeze exact hashes, and submit the integrated bytes for independent deployment QA.
