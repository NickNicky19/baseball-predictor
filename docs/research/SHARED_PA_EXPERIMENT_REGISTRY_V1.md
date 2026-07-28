# Shared-PA Experiment Registry v1

## Purpose

This registry makes shared-PA research attempts explicit before any protected selection or forward outcome is observed. It is an integrity and reproducibility boundary, not a model evaluator and not betting authorization.

The checked-in policy is bound to source base `9f9d839187550f7a7a6da3b4bfdb25b2dff3e794`. It treats the already-spent 2024 selection window as `INELIGIBLE_ALREADY_SPENT`; its token begins `UNISSUED` and `UNCONSUMED`, and issuance is disabled. No command in this scaffold can make that historical window fresh again.

## Predeclared experiment identity

An `EXPERIMENT_PREDECLARED` event freezes:

- candidate and parent identities;
- a multiplicity family derived from the canonical outcome-affecting design and the complete ordered list of prior attempts;
- hypothesis and feature, grid, fold, code, configuration, data, test, and output-schema bindings;
- allowed evidence windows and protected evidence boundaries;
- the evaluator-interface hash;
- the untouched forward-window state and terminal disposition state;
- an outcome-affecting equivalence fingerprint.

Candidate names, caller-supplied family labels, and output paths are deliberately excluded from the equivalence fingerprint. JSON contracts are parsed to semantic objects and Python candidate code is normalized to an abstract syntax tree, so harmless reserialization cannot manufacture a distinct experiment. The caller must use `family-<semantic fingerprint>` exactly. Renaming, changing formatting, or moving outputs therefore cannot create a new attempt. Every bound artifact is still rehashed during replay, so changed bytes invalidate the registry even when the semantic experiment identity is unchanged.

Output namespaces use normalized lowercase ASCII and are reserved globally, not merely within one family. The reservation check and append occur under the same exclusive lock.

A separate `CANDIDATE_FROZEN` event binds model artifacts and a release hash. `SELECTION_TOKEN_ISSUED` and `SELECTION_TOKEN_CONSUMED` are separate transitions, each limited to one occurrence. `TERMINAL_DISPOSITION` is append-only and cannot be revised.

## Storage and verification

The registry writes immutable canonical-JSON entries and checkpoints, followed by an atomically replaced `HEAD.json`. Appends use an exclusive creation lock. Replay fails closed on an orphan, deletion, reordering, truncation, payload mutation, checkpoint mismatch, unexpected path, symlink/reparse point, concurrent writer, or changed bound artifact.

An independently retained trusted checkpoint is mandatory at the command-line boundary. Its path is fixed by an externally digested release expectation; its independently supplied byte digest and exact monotonic sequence are checked on every authoritative operation. Replacement, rollback, a different path, or a symlink/junction/reparse ancestor fails closed. The local hash chain by itself cannot detect an attacker who deletes the same suffix from entries, checkpoints, and `HEAD.json`; that limitation remains explicit.

The external release expectation binds the exact registry ID, source base commit, policy, evaluator interface, and artifact manifest. Every authoritative append also requires a separately digested external append receipt that binds the prior checkpoint, next sequence, entry type, semantic design fingerprint, SHA-256 of the complete canonical validated payload, and trusted UTC timestamp. The two payload identities are intentionally separate: semantic equivalence prevents experiment-family spoofing, while the full canonical hash prevents a receipt for candidate A from authorizing a substituted candidate ID, output namespace, hypothesis, or other governance metadata. A caller-provided timestamp cannot override this receipt, and timestamps must be monotonic and no earlier than the release authority's declared boundary.

No independent release expectation is delivered by this research scaffold. Its default authority state is therefore `UNBOUND_EXTERNAL_TRUST_REQUIRED`, and public authoritative append operations are refused. Explicit unbound append support exists only for synthetic mechanics and mutation tests; it cannot issue real authority or make the spent 2024 window eligible.

## Protected boundaries

The policy preserves all of the following:

- May 2026 is sealed and cannot be used or inspected;
- the spent 2025 HR confirmation is not fresh evidence;
- historical prices are not executable prices;
- missed prospective evidence is never backfilled;
- Hits, HR over 0.5, and Total Bases are adjudicated independently;
- one market cannot rescue another;
- frozen baselines are immutable;
- no registry event authorizes betting.

Evidence windows use a positive exact allowlist. Version 1 permits only `development_2023_only`; spelling variants, case variants, aliases, 2024, 2025, and May representations all fail closed.

## Evaluator binding

`config/shared_pa_market_evaluator_binding_interface_v1.json` is only a hash-bound interface. It requires market-separated rows, identity and probability lineage, coverage and abstention accounting, proper scores, calibration (including the HR upper tail), discrimination, clustered uncertainty, chronology checks, and valid receipt-bound market comparisons where available.

The full evaluator is intentionally not implemented here. This scaffold cannot fit, score, consume the real 2024 authority, promote, activate, settle, or authorize betting.

## Command-line operations

The management script supports only:

- `initialize`: create either an explicitly unbound test registry or a registry configured against an independently digested release expectation;
- `verify`: replay authoritatively only with the exact external release expectation and fixed-path, independently digested checkpoint;
- `append`: require the release expectation, current checkpoint, and an independently digested monotonic append receipt before writing one event.

All artifact paths are normalized relative paths below an explicit artifact root. Absolute paths, traversal, links, non-regular files, and byte mismatches fail closed.

## Test evidence

The implementation is covered by regression and mutation tests for family spoofing, renamed and reserialized equivalent candidates, candidate/output/hypothesis substitution under a receipt for another payload, global namespace collision, output-path bypass, changed bytes, missing parents, evidence-window aliases, alternate policy bytes, unbound authority, backdated and nonmonotonic append receipts, caller-time override, checkpoint replacement and rollback, protected-boundary mutation, double freezing, double token use, wrong selection output paths, terminal-disposition mutation, deletion/truncation/orphans, complete-suffix rollback, concurrent/stale locks, and link/reparse-ancestor rejection.

On Windows, link creation may be unavailable to an unprivileged test process. The relevant test skips only when the operating system refuses creation; the runtime code still rejects both symbolic links and Windows reparse points.
