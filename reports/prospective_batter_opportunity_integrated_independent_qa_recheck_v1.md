# Prospective Batter Opportunity Integrated Runtime - Independent QA Recheck v1

## Decision

Exact repaired candidate reviewed: `80c94b37bf9d685e382d8e34e6d92862b49a5629` on `codex/prospective-target-opportunity-evidence-v1`.

| Decision | Result |
|---|---|
| Preserve this candidate for continued research | **PASS** |
| Clean Linux verification of the exact candidate tree | **PASS** |
| Prior integrated-QA blockers PBO-IQA-001 through PBO-IQA-004 repaired | **PASS** |
| Eligible to seek explicit authorization for a separate future-only, non-May, non-economic operational smoke | **PASS** |

The four findings from `reports/prospective_batter_opportunity_integrated_independent_qa_v1.md` are independently verified as repaired in the exact candidate bytes. No new blocker was found within this deliberately bounded recheck.

This is an eligibility decision only. It does **not** authorize deployment, modify a collector, start evidence collection, backfill a missed observation, fit or score a model, consume a probability, promote or activate a candidate, make an economic claim, or authorize betting.

## Scope and isolation

- Worktree: `C:\Projects\baseball_predictor_prospective_evidence_v1`
- Local HEAD and the remote branch both resolved to `80c94b37bf9d685e382d8e34e6d92862b49a5629`.
- Candidate Git tree: `dc405394e6adb3cfdce061ad6c7f3e6b6fbfc0df`.
- The tracked worktree was clean before recheck execution.
- Review scope was limited to the four prior integrated-QA findings, their source-level repairs, regression/mutation tests, declared hashes, and the exact Linux workflow run.
- No May 2026 artifact or path content was opened, enumerated, parsed, copied, or written. May behavior was assessed from source and synthetic guards only.
- No outcome archive, price/economic evidence, AWS resource, live process, production tree, or spent confirmation evidence was inspected.
- No code, configuration, test, fixture, or existing report was modified. This report is the only repository file created by this recheck.

## Finding dispositions

### PBO-IQA-001 - Multi-day outage and exact-once planning-miss repair

- **Disposition:** VERIFIED_REPAIRED
- **Confidence:** High
- **Paths/symbols:** `scripts/run_prospective_batter_opportunity_tick.py::run_all`, `_history_ledgers`; `ProspectiveOpportunityHistoryLedger.append_planning_exclusion`, `planning_exclusion_ids`, `verify`.
- **Source evidence:** `run_all` traverses from the immutable collection epoch through the current Eastern official date. It skips May before constructing a plan path. For every retained non-May T-4 plan, it replays the roster ledger and examines captured side terminals. A captured side with neither a history plan nor an existing planning terminal is handled as follows: before first pitch, a history plan is published; at or after first pitch, an immutable `missed_before_plan` terminal is published and no capture plan is created. Existing plans and planning terminals are separately detected, so later ticks cannot duplicate or overlap them. Final-source fetching occurs only from published history plans; a receipt-proven planning miss therefore causes no late fetch.
- **Regression evidence:**
  - first run on the next day creates one planning terminal, no plan, and makes no final-source fetch;
  - a second tick reports the existing terminal instead of writing another;
  - first run after multiple plan dates terminalizes every receipt-proven missed side;
  - the plan traversal guard proves May is skipped before a May source path is constructed.
- **Practical result:** a full-day or multi-day outage no longer silently loses the durable distinction between a receipt-proven missed planning window and no expected evidence. It still cannot backfill a game record or manufacture a late plan.

### PBO-IQA-002 - Missing or incomplete upstream roster evidence health repair

- **Disposition:** VERIFIED_REPAIRED
- **Confidence:** High
- **Paths/symbols:** `scripts/run_prospective_batter_opportunity_tick.py::run_all`, `_exit_code_for_result`; `ProjectedLineupRosterLedger.verify`.
- **Source evidence:** every discovered source plan now increments `source_plans_seen`. A missing bound roster-ledger manifest increments `source_ledgers_missing`. A verified ledger with zero or partial terminal coverage increments `source_terminal_coverage_missing` from the ledger's explicit missing count. Either condition returns `collector_state=blocked_upstream_roster_evidence`, and `_exit_code_for_result` maps that state to command status 2. An unreadable or identity-mismatched manifest raises a fail-closed runtime error, which the command also returns as status 2. No receipt-proven evidence terminal is fabricated when upstream proof is absent.
- **Regression evidence:**
  - a missing manifest returns blocked state and nonzero health status, creates no history evidence, and successfully recovers after the exact manifest is restored;
  - malformed manifest JSON fails closed;
  - zero-side and partial-side roster terminal coverage both return blocked state and expose the nonzero missing count;
  - captured valid sides may still be truthfully planned while incomplete coverage keeps overall health blocked.
- **Practical result:** a valid plan with missing, unreadable, zero-coverage, or partial-coverage roster evidence cannot be reported as a healthy all-zero tick.

### PBO-IQA-003 - Self-contained schedule proof and timestamp replay repair

- **Disposition:** VERIFIED_REPAIRED
- **Confidence:** High
- **Paths/symbols:** `ProspectiveOpportunityHistoryLedger._verified_plan_bundle`, `_verify_plan_proof`, `append_planning_exclusion`, `_verify_planning_exclusion`.
- **Source evidence:** history plan proofs and planning-miss terminals now copy the retained schedule raw bytes as canonical base64 in addition to the roster raw bytes, T-4 plan, and captured roster terminal. The copied roster terminal carries the schedule observation timestamp and its own hash. Verification checks the terminal hash, schedule-reference schema, schedule-byte SHA-256, and then reruns `projected_lineups_from_schedule(RawPregameResponse(schedule_raw, schedule_received_at_utc), source_plan, target)`. That replay revalidates observation-time eligibility, game/date/start identity, and the side's team identity. The active-roster raw bytes are separately hashed and replayed against the same date, team, and horizon.
- **Mutation evidence:** changing copied schedule raw bytes is rejected during downstream history-proof replay. The underlying replay boundary also rejects a schedule timestamp one microsecond after the T-4 horizon, so subsecond lateness cannot be truncated into eligibility.
- **Practical result:** downstream history evidence no longer depends on an external roster ledger merely to recover schedule bytes; its copied proof re-establishes both byte identity and schedule chronology/identity semantics.

### PBO-IQA-004 - Immutable workflow and hash-complete test lock repair

- **Disposition:** VERIFIED_REPAIRED
- **Confidence:** High
- **Paths:** `.github/workflows/prospective-batter-opportunity-linux.yml`, `requirements-prospective-batter-opportunity-ci.lock`.
- **Source evidence:**
  - `actions/checkout` is pinned to `fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09`;
  - `actions/setup-python` is pinned to `ece7cb06caefa5fff74198d8649806c4678c61a1`;
  - all five direct test-only packages are exact-version and SHA-256 bound;
  - installation uses `--no-deps --require-hashes`, followed by `pip check`;
  - lock-file SHA-256 is `97860e387e41f3feed182c9775ed1dc9de36b50274c3bdfd8b2fc6f870e8fb6e`.
- **Linux evidence:** the exact run downloaded both pinned action commits, installed `Pygments 2.20.0`, `iniconfig 2.3.0`, `packaging 26.2`, `pluggy 1.6.0`, and `pytest 9.1.1`, reported no broken requirements, and reproduced the declared lock-file hash.
- **Practical result:** future executions of this bounded Linux gate no longer depend on mutable major action tags or unhashed Python package content.

## Independent execution evidence

### Local Windows recheck

Executable: `C:\Users\nicho\AppData\Local\Python\pythoncore-3.14-64\python.exe` (`Python 3.14.6`).

Environment: `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=.`; pytest cache disabled; unique temporary bases under `C:\tmp`; no dependency installed or changed.

- Focused suite: `tests/test_prospective_batter_opportunity.py`, `tests/test_prospective_batter_opportunity_history.py`, and `tests/test_run_prospective_batter_opportunity_tick.py` - **71 passed in 0.94s**.
- Related chronology/source-truth suite named by the workflow - **102 passed in 1.07s**.
- Eight-test prior-defect and boundary selection - **8 passed in 0.34s**. It directly included next-day exact-once/no-fetch, multiple missed plan dates, missing-manifest block/recovery, unreadable manifest, zero/partial coverage, pre-path May skipping, copied schedule-raw mutation, and the one-microsecond-late schedule timestamp boundary.
- `git diff --check` and `git diff --exit-code` passed after test execution.

### Exact GitHub Linux run

- Repository: `NickNicky19/baseball-predictor`
- Pull request: `#30`
- Workflow: `Prospective Batter Opportunity Linux Gate`
- Run: `30267258778`
- Job: `89980816958`, `Ubuntu 24.04 / CPython 3.12.3`
- Run event/status: `pull_request`, completed successfully on attempt 1.
- Run `head_sha`: `80c94b37bf9d685e382d8e34e6d92862b49a5629`.
- Focused Linux suite: **71 passed in 4.79s**.
- Related Linux suite: **102 passed in 2.37s**.
- Tracked-source secret scan: no likely credentials in 748 tracked files.
- Post-test `git diff --exit-code`: passed.

GitHub Actions checked synthetic merge commit `8ac6cc1ff8a774aba36b7851e32a33b154b10cd0` over base `adb4f481009927102bc400eca5bf1766cd2aafe6`. Independent GitHub object inspection found both that merge commit and the requested head commit have tree `dc405394e6adb3cfdce061ad6c7f3e6b6fbfc0df`. Therefore the tested repository bytes are exactly the candidate tree reviewed here.

## Hash-manifest verification

Every path/digest pair declared in the `code`, `configuration`, `data`, `tests`, and `source_audit` sections of `reports/prospective_batter_opportunity_evidence_v1.json` was recomputed from the exact candidate tree.

- Declared file hashes checked: **22**
- Missing files: **0**
- Digest mismatches: **0**

The evidence report itself intentionally records `linux_tested=false` and `integrated_runtime_bytes_independently_rechecked=false` because those external events had not occurred when the candidate bytes were frozen. This independent recheck report records the later evidence without rewriting that earlier artifact.

## Remaining boundaries and gates

- The repaired runtime is still descriptive, prospective, research-only evidence infrastructure. It creates no HR, Hits, or Total Bases probability and establishes no predictive improvement.
- A passing synthetic/fixture suite and clean Linux run do not prove live source availability, operational reliability, complete future coverage, statistical value, market value, or profitability.
- Deployment remains a separate, explicit human-authorized action. The active AWS collectors and production runtime were not touched here.
- If a future smoke is separately authorized, it must remain non-May, future-only, non-economic, immutable, and unable to backfill missed evidence. Any blocked or failed health state must remain explicit.
- Untouched prospective evidence accumulation, a locked analysis protocol, market-specific scoring, calibration/discrimination/coverage and uncertainty gates, valid executable-price evidence, settlement, and prospective replication all remain required before any promotion or betting question can arise.

## Final conclusion

Commit `80c94b3` closes PBO-IQA-001 through PBO-IQA-004 at their true source boundaries and prevents the old failures from silently re-entering. Its exact tree passed independent local regression/mutation execution and the exact clean Ubuntu gate. It is technically eligible to seek explicit authorization for a separate future-only non-economic operational smoke, while remaining wholly unauthorized for deployment by this report, model consumption, performance claims, promotion, economic use, or betting.
