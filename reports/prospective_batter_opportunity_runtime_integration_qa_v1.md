# Prospective Batter Opportunity Runtime Integration QA v1

## Decision

**INTERNAL ENGINEERING QA: PASS. Independent repaired-candidate re-review and clean-Linux verification passed.**

**Deployment readiness: ELIGIBLE TO SEEK EXPLICIT AUTHORIZATION FOR A SEPARATE FUTURE-ONLY, NON-MAY, NON-ECONOMIC SMOKE.** No deployment or collection is authorized by this report.

This record does not authorize fitting, calibration, prediction consumption, outcome scoring, market evaluation, production deployment, promotion, activation, or betting.

## Scope and boundaries

- Isolated candidate worktree: `C:\Projects\baseball_predictor_prospective_evidence_v1`
- Branch: `codex/prospective-target-opportunity-evidence-v1`
- Base commit: `adb4f481009927102bc400eca5bf1766cd2aafe6`
- Review scope: the prospective batter-opportunity library, its thin runtime tick, the upstream projected-lineup roster proof boundary needed by that runtime, configuration, and regression/mutation tests.
- No May 2026 path, source, feature, prediction, or outcome artifact was inspected or created.
- No outcomes, prices, settlement, economic evidence, model fitting, calibration, or probability generation occurred.
- No AWS resource, live collector, production release, GitHub branch, or pull request was changed.

## Source-level integrity findings and repairs

### 1. Missed-before-plan state was not durable

The runtime originally could count a receipt-proven side discovered after first pitch without preserving a terminal, immutable reason that the pregame history plan was permanently unavailable.

The history ledger now publishes `missed_before_plan` only at or after first pitch. The record contains replayable T-4 plan, side terminal, and roster-raw proof. Verification rejects mutation and any overlap between a planned side and a terminally missed side. Later ticks cannot convert the terminal miss into a backfilled pregame plan.

### 2. Collection epoch and release identity were not bound by an immutable scope

The runtime now requires a deployment-bound evidence-scope file and expected SHA-256. The scope binds the collection epoch, runtime manifest, opportunity contract, runtime collector code, upstream roster contract, upstream roster collector code, and the research-only/no-backfill/no-betting policy. Replacement or mutation fails closed.

### 3. The upstream active-roster ledger proved schedule bytes but not schedule meaning

`ProjectedLineupRosterLedger.verify` previously checked the schedule byte digest without replaying the schedule observation timestamp, game identity, target date, first pitch, or team/side identity. It also did not bind its manifest contract and collector-code identities, and the terminal receipt time was not independently replayed from retained roster bytes.

The repaired source boundary now replays the retained official schedule bytes using the recorded observation timestamp, requires exact game/date/start/team/side identity, replays retained roster bytes at the terminal commitment time, and binds the exact upstream contract and collector-code hashes. The runtime rejects upstream release mismatches before publishing history evidence.

### 4. A first tick after the target date could omit durable terminal missingness

The runtime now scans every permissible non-May T-4 plan date from the immutable collection epoch through the current date. A receipt-proven captured roster side first discovered at or after first pitch is written exactly once as `missed_before_plan`; no capture plan is backdated and no final source is fetched for that side.

### 5. Missing or incomplete upstream roster evidence could resemble a healthy empty tick

Missing roster manifests and incomplete terminal coverage are now counted explicitly. The collector returns `blocked_upstream_roster_evidence`, and its command-line boundary returns a nonzero status. Valid captured sides may still be preserved, but the tick cannot be represented as healthy while expected upstream evidence is absent.

### 6. Copied history proof omitted the schedule bytes establishing game/team identity

Both capture-plan proofs and planning-miss terminals now retain the exact fields-limited schedule bytes and observation timestamp already referenced by the active-roster terminal. Replay verifies byte identity, chronology, game/date/start, side, and team identity without depending on the mutable upstream filesystem.

## Fail-closed and mutation proof

The repaired focused suite exercises:

- immutable evidence-scope replacement rejection;
- upstream roster contract/code release mismatch;
- rehashed schedule team-identity mutation;
- valid prestart planning and later final capture;
- terminal missed-before-plan behavior and mutation rejection;
- retryable network/nonfinal behavior only inside the declared window;
- terminal missing at the declared operational deadline;
- source-invalid hash/size-only retention with no raw publication;
- May early return before evidence paths are constructed;
- history scanning that refuses to touch any May path;
- no probability, price, outcome-scoring, or production authority.
- first-run-next-day terminal missingness and exact-once behavior;
- missing, unreadable, zero-coverage, partial-coverage, and later-recovered roster evidence;
- schedule-proof mutation after proof copying;
- plan traversal across May without constructing or inspecting a May path;
- nonzero command status for blocked upstream evidence.

## Execution evidence

Executable: `C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe`

Environment: `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=.`. Pytest cache disabled. No dependency was installed or changed.

Focused runtime/library suite:

`tests/test_prospective_batter_opportunity.py tests/test_prospective_batter_opportunity_history.py tests/test_run_prospective_batter_opportunity_tick.py`

Result: **71 passed locally**.

Related opportunity, chronology, roster-source, history, and AWS-runtime-contract suite:

`tests/test_prospective_batter_opportunity.py tests/test_prospective_batter_opportunity_history.py tests/test_projected_lineup_contract.py tests/test_projected_lineup_official_roster.py tests/test_projected_lineup_roster_runner.py tests/test_projected_lineup_history.py tests/test_run_prospective_batter_opportunity_tick.py tests/test_aws_projected_lineup_roster_runtime.py`

Result: **102 passed locally**.

GitHub Actions run `30267258778`, job `89980816958`, passed on Ubuntu 24.04.4 and CPython 3.12.3: **71 focused passed**, **102 related passed**, exact hash-locked dependencies installed, 748 tracked files passed the credential scan, and the checkout remained clean. The tested synthetic-merge tree `dc405394e6adb3cfdce061ad6c7f3e6b6fbfc0df` exactly equals repaired head `80c94b37bf9d685e382d8e34e6d92862b49a5629`'s tree.

Independent recheck report: `reports/prospective_batter_opportunity_integrated_independent_qa_recheck_v1.md`, SHA-256 `28bc18eae9e594cbb1dfbc7a71210c4b2422d66e9f0dfe8cc556575de26bb4ee`. Decision: PBO-IQA-001 through PBO-IQA-004 are `VERIFIED_REPAIRED`; targeted recheck 8/8 passed and 22/22 declared hashes matched.

## Exact candidate identities

| Artifact | SHA-256 |
|---|---|
| `src/evaluation/prospective_batter_opportunity.py` | `fc5ca7d433a94a2c848635adb37b64a501f0746dfff10e053729a7489943abed` |
| `src/evaluation/prospective_batter_opportunity_ledger.py` | `268b4c3fd646beeac4f1ffc77a7e55e93a202baa73fd8c5b2a065e3cc1c79d4d` |
| `src/evaluation/prospective_batter_opportunity_history.py` | `a8795877305519617ea4415d7f3d322e4194c7da6f3a27966b612a01fe0b4d35` |
| `scripts/run_prospective_batter_opportunity_tick.py` | `e81d8b2c10d9008bf05aed5bf07c6047419a2dc380a4d843692f5992313ed5f8` |
| `src/evaluation/projected_lineup_roster_ledger.py` | `66743e99aaaede887cc2c025987521cc257b7318c4d9a2c3c1e3f5639d7a7527` |
| `src/evaluation/projected_lineup_roster_runner.py` | `a35543aeb4669c083ad1b3287beafc802a173753105c363471b0ec7b8df7a7ed` |
| `config/prospective_batter_opportunity_contract_v1.json` | `a537015b4189e61f384526509aa89607b99dcbfd378f5457831182532b6d0cf1` |
| `config/prospective_batter_opportunity_runtime_v1.json` | `fba9cc301043989bc7293a173747e68cb587b71f94ed1f56060533a6b72d986d` |
| `config/projected_lineup_contract_v1.json` | `88910f66c0a51a7aa33baa1dffe9c6cbe8fc9aebcc90d76df26f41937640182a` |
| `config/projected_lineup_roster_runtime_v1.json` | `4d7a316170f143df2b9bb8407e887cfcfbd841fa2f2b971b79d8464f63d25f9d` |
| `.gitattributes` | `8d1ed80058a1704ff073a6739abe10406f60298e1ebc9104479cc9481b9859f9` |
| `requirements-prospective-batter-opportunity-ci.lock` | `97860e387e41f3feed182c9775ed1dc9de36b50274c3bdfd8b2fc6f870e8fb6e` |
| `.github/workflows/prospective-batter-opportunity-linux.yml` | `f9cb05f663eead98771e053a6b93a48f8ab7a0505e422c709c994e96d3405350` |
| `tests/test_prospective_batter_opportunity.py` | `1580eff1008cf5e94f88f9b5c67812d7fd8ec8eb35899c9be67b88a9cf36ab3f` |
| `tests/test_prospective_batter_opportunity_history.py` | `41fc0286581484304ff697a84f9e2745112fc57801f1ed859ddbdfa4194707e3` |
| `tests/test_run_prospective_batter_opportunity_tick.py` | `15510ffb99acf04d35c440540eb02a75c00627dfbf6d4fe379e91e4908f657c3` |

## Readiness conclusion

The repaired exact bytes passed Linux CI and independent re-review. They do **not** establish predictive improvement and are not deployed. The next gate is explicit human authorization for a separately isolated, future-only, non-May, non-economic smoke.

Required next gates, in order:

1. explicit human authorization for a separate future-only, non-May, non-economic operational smoke;
2. preserve the smoke result, including terminal missingness, without backfill;
3. only after those gates, begin collecting untouched prospective inputs for a predeclared market-specific research protocol.
