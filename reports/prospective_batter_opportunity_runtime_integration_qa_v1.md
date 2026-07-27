# Prospective Batter Opportunity Runtime Integration QA v1

## Decision

**INTERNAL ENGINEERING QA: PASS for preservation and independent review.**

**Deployment readiness: NOT READY.** Clean-Linux verification, an independent review of the integrated exact bytes, explicit deployment authorization, and a separately authorized future non-May operational smoke remain mandatory.

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

## Fail-closed and mutation proof

The final focused suite exercised:

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

## Execution evidence

Executable: `C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe`

Environment: `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=.`. Pytest cache disabled. No dependency was installed or changed.

Focused runtime/library suite:

`tests/test_prospective_batter_opportunity.py tests/test_prospective_batter_opportunity_history.py tests/test_run_prospective_batter_opportunity_tick.py`

Result: **64 passed in 0.89s**.

Related opportunity, chronology, roster-source, history, and AWS-runtime-contract suite:

`tests/test_prospective_batter_opportunity.py tests/test_prospective_batter_opportunity_history.py tests/test_projected_lineup_contract.py tests/test_projected_lineup_official_roster.py tests/test_projected_lineup_roster_runner.py tests/test_projected_lineup_history.py tests/test_run_prospective_batter_opportunity_tick.py tests/test_aws_projected_lineup_roster_runtime.py`

Result: **95 passed in 1.03s**.

## Exact candidate identities

| Artifact | SHA-256 |
|---|---|
| `src/evaluation/prospective_batter_opportunity.py` | `fc5ca7d433a94a2c848635adb37b64a501f0746dfff10e053729a7489943abed` |
| `src/evaluation/prospective_batter_opportunity_ledger.py` | `268b4c3fd646beeac4f1ffc77a7e55e93a202baa73fd8c5b2a065e3cc1c79d4d` |
| `src/evaluation/prospective_batter_opportunity_history.py` | `6007dfbcb72321f8365ccb618992616bb3d5765caa10e390245b092bae2f9b12` |
| `scripts/run_prospective_batter_opportunity_tick.py` | `f00a9fe1aec90419534f6668c364eb22ceb5eb89c0e91b2a3fdddda21f736a79` |
| `src/evaluation/projected_lineup_roster_ledger.py` | `66743e99aaaede887cc2c025987521cc257b7318c4d9a2c3c1e3f5639d7a7527` |
| `src/evaluation/projected_lineup_roster_runner.py` | `a35543aeb4669c083ad1b3287beafc802a173753105c363471b0ec7b8df7a7ed` |
| `config/prospective_batter_opportunity_contract_v1.json` | `a537015b4189e61f384526509aa89607b99dcbfd378f5457831182532b6d0cf1` |
| `config/prospective_batter_opportunity_runtime_v1.json` | `fba9cc301043989bc7293a173747e68cb587b71f94ed1f56060533a6b72d986d` |
| `config/projected_lineup_contract_v1.json` | `88910f66c0a51a7aa33baa1dffe9c6cbe8fc9aebcc90d76df26f41937640182a` |
| `config/projected_lineup_roster_runtime_v1.json` | `4d7a316170f143df2b9bb8407e887cfcfbd841fa2f2b971b79d8464f63d25f9d` |
| `.gitattributes` | `8d1ed80058a1704ff073a6739abe10406f60298e1ebc9104479cc9481b9859f9` |
| `requirements-prospective-batter-opportunity-ci.lock` | `b01968cfd800101e7ebce4d8e2cb14b45d141fda20c7668fc0a24e1241350044` |
| `.github/workflows/prospective-batter-opportunity-linux.yml` | `f661f1f72655393c5577c765e85e792f43667a12f102a5eeb2a6a393bbc49fab` |
| `tests/test_prospective_batter_opportunity.py` | `1580eff1008cf5e94f88f9b5c67812d7fd8ec8eb35899c9be67b88a9cf36ab3f` |
| `tests/test_prospective_batter_opportunity_history.py` | `3c1e8ab5dbbf6e3b11f8fbc64418da63ffcbb9c149486efd9fbe851f56cdf0ad` |
| `tests/test_run_prospective_batter_opportunity_tick.py` | `caa8dddbb62a55746bc34a9ad32231482221668900e1d848b2e209a164f8e7ba` |

## Readiness conclusion

The integrated local bytes truthfully implement a future-only, non-economic batter target/opportunity evidence path and close the identified source-to-ledger integrity defects. They do **not** establish predictive improvement and are not yet deployable.

Required next gates, in order:

1. independent review of these integrated exact bytes;
2. clean Ubuntu 24.04 / CPython 3.12.3 execution of the hash-bound Linux gate;
3. explicit human authorization for a separate future-only, non-May, non-economic operational smoke;
4. preserve the smoke result, including terminal missingness, without backfill;
5. only after those gates, begin collecting untouched prospective inputs for a predeclared market-specific research protocol.
