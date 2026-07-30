# Integrated shared-PA candidate v1 — implementation result

Status: **RUNNABLE, FAIL-CLOSED, NOT PREDICTIVELY QUALIFIED**
Research only. No betting authorization.

## Repository and package audit

- Canonical implementation base: Git commit
  `62535c3626c875e694a37bc9922f5881c22c59fb`.
- Isolated implementation branch: `codex/integrated-shared-pa-candidate-v1`.
- Attached audit ZIP SHA-256:
  `986d8f4ba7c6c7adaea45112103d97969ca4731134079c76f960cb914e11e9a5`.
- The ZIP passed its internal 49-file hash/syntax/JSON checks. It was used as
  evidence only; no package file or supplied artifact was copied into the
  candidate.
- Active frozen path: `run_slate.py -> DailyPredictor -> FeatureFactory ->
  PropEngine -> GameSimulator/HybridPASimulator`.
- The frozen PA and K/BB artifacts and frozen config are active. The local
  Savant CSV overrides matching live profiles. The frozen HR path retains the
  known reversed opposing-pitcher HR/9 effect. All remain unchanged.
- The repository's exact shared-PA derivation is real and wired in the new arm.
  It is not itself proof that its inputs are qualified or its predictions are
  superior.

## Material implementation

The real `run_slate.py` now supports named frozen and candidate arms and a
same-slate comparison mode. The candidate:

- uses hard `gamePk + side + team_id + player_id` identities;
- requires source, runtime-release, evidence-envelope, protocol, raw-response,
  and transport-receipt identities;
- enforces strict pre-horizon chronology and strictly prior stats cutoff;
- excludes the mutable Savant CSV, direct BvP, unreceipted pitcher context, and
  every frozen artifact fallback;
- rejects the known blocked PA-volume artifact even if all enclosing hashes are
  recomputed;
- replays start, slot, PA, per-PA, and market-distribution semantics;
- emits exact deterministic PMFs for Hits, HR, Total Bases, batter strikeouts,
  and batter walks;
- writes arm-separated immutable JSON with model, code, feature-schema, config,
  source, and input identities;
- emits a typed slate abstention when qualified candidate evidence is absent.

No new predictive coefficient was fitted. The EB-200 control present elsewhere
in the repository was not promoted as a fitted candidate.

## Before/after prediction result

No lawful real before/after probability row exists yet.

| Observation universe | Frozen rows | Candidate rows | Candidate abstention | Interpretation |
|---|---:|---:|---|---|
| Newly qualified official-2023 opportunity source | N/A | 0 | `QUALIFIED_OPPORTUNITY_EVIDENCE_UNAVAILABLE` | The required source release has not been captured and independently qualified. |
| Old hash-bound PA-volume artifact | available to legacy research code | prohibited | `blocked PA-volume artifact` | PR #47 says `BLOCKED_DO_NOT_DEPLOY_OR_SCORE`; changing numbers with it would be invalid. |
| Synthetic wiring fixture | not performance-scored | 1 player × 5 markets | none | Proves runner wiring, exact PMFs, hashes, and determinism only. |

Therefore, probability change and predictive improvement are both **not
demonstrated**. Producing attractive numbers from the blocked artifact would
have violated the governing source-truth rule.

## Historical evaluation

The market-separated evaluator was verified by 84 focused tests. It supports
Brier score, log loss, calibration, full-distribution scores, abstention,
identical-observation comparisons, and date/game clustered uncertainty.

A lawful 2023 evaluation was not run because the independently verified
official-2023 source release and new PA artifact do not exist. Existing reports
explicitly prohibit scoring the old artifact. No 2024, spent 2025 HR, May 2026,
market price, outcome, or prospective evidence was opened to fill that gap.

## Coverage and abstention

- Qualified evidence supplied: produces every supported market for every valid
  retained player record and preserves producer abstentions.
- No evidence supplied: writes zero predictions and one typed slate abstention.
- Contradictory identity, chronology, source, or probability semantics: rejects
  the entire affected input rather than treating it as ordinary missingness.
- Pitcher markets: deferred, not silently inherited from the frozen shortcut.
- RBI, HRR, and earned runs: deferred, not synthesized from an unqualified
  base-runner mechanism.

## Tests and execution

- `109 passed` across integrated candidate, shared-PA evidence, active runner,
  and market-evaluation tests.
- Python compilation passed for `run_slate.py` and the candidate module.
- Candidate replay is byte-deterministic in the focused test.
- Candidate runner writes a model-separated archive in the active entry point.
- Missing evidence and May requests fail closed.
- A broader legacy v2 test group has tests that require a clean Git tree; those
  cannot pass in an intentionally uncommitted implementation worktree and were
  not misreported as implementation failures.

## Files changed

- Modified: `run_slate.py`
- Added: `src/prediction/integrated_shared_pa_candidate.py`
- Added: `config/model_arms_v1.json`
- Added: `docs/INTEGRATED_SHARED_PA_CANDIDATE_V1.md`
- Added: `tests/test_integrated_shared_pa_candidate.py`
- Added: `reports/integrated_shared_pa_candidate_v1_implementation.md`

## Parameters

Manual/structural: model identifiers, hard-key schema, five supported markets,
exact Total Bases weights, threshold enumeration through 5.5, strict UTC/date
rules, zero-fallback policy, known blocked artifact identity, and typed
abstention codes.

Preserved: all frozen config values, frozen PA/KBB artifacts, simulator rules,
and every rejected-candidate record.

Fitted/learned in this task: none.

Input-provided candidate quantities: start probability, conditional slot
probabilities, PA mixture, and coherent per-PA probabilities. These are accepted
only from a qualified and hash-bound producer record.

## May 2026 attestation

No May 2026 source, feature, prediction, outcome, price, or artifact path was
opened or generated. The implementation rejects May before candidate evidence
is opened, and the negative test uses only a synthetic malformed input.

## Known limitations and next dependency

The single blocking dependency is the official 2023 PA source release already
designed by PR #47: exact digest-bound source authorization, clean Linux runtime,
capture of only permitted 2023 final training outcomes, independent release
verification, and construction of a new qualified opportunity artifact. That
is source acquisition, not permission to reconstruct prospective evidence.

Only after that artifact exists can the integrated arm produce lawful real
comparisons and the market evaluator report actual development performance.
Untouched prospective evidence would still be required for any superiority,
promotion, ROI, or betting claim.

Proposed commit message:

`research: integrate fail-closed shared-PA candidate arm`
