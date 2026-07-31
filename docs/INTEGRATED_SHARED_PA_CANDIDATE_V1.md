# Integrated shared-PA candidate v1

## Repository truth

The frozen daily path is `run_slate.py -> DailyPredictor -> FeatureFactory ->
PropEngine -> GameSimulator/HybridPASimulator`.  It consumes the mutable local
Savant CSV override, the frozen K/BB artifact, and the frozen PA artifact.  Its
HR path retains the known reversed opposing-pitcher HR/9 effect.  These bytes
are preserved and fingerprinted in `config/model_arms_v1.json`.

The repository also contains exact shared-PA distributions and receipt-bound
projected-opportunity components.  They are reusable, but the old 2023
PA-volume artifact is not eligible: PR #47 established that its upstream source
authority was incomplete.  A matching artifact hash does not repair that
defect.  The candidate therefore rejects that artifact explicitly.

## Candidate boundary

`shared_pa_candidate_v1` consumes retained side bundles only after checking
hard MLB identities, source and record hashes, source qualification, target
date, decision horizon, source cutoff, start/slot/PA distributions, and exact
market replay.  It never calls the mutable Savant CSV, direct BvP, the frozen
PA/KBB artifacts, or an unreceipted pitcher matchup.

For every eligible batter it deterministically derives full count PMFs for
Hits, HR, Total Bases, batter strikeouts, and batter walks.  Total Bases uses
0/1/2/3/4 per-PA values and exact convolution.  Probability intervals are
reported unavailable until parameter uncertainty is fitted and qualified;
inventing narrow bounds would be misleading.

The currently available source records do not include a separately identified
HBP outcome, so `other_non_ab` remains explicit rather than being relabeled.

## Commands

Legacy behavior remains available:

```text
python run_slate.py --date YYYY-MM-DD
```

Explicit frozen arm:

```text
python run_slate.py --model frozen_baseline --date YYYY-MM-DD
```

Candidate arm, requiring a qualified retained evidence bundle:

```text
python run_slate.py --model shared_pa_candidate_v1 --date YYYY-MM-DD --candidate-evidence PATH
```

Same-slate engineering comparison:

```text
python run_slate.py --compare-models --date YYYY-MM-DD --candidate-evidence PATH
```

Arm-specific archives are written below `frozen_baseline/`,
`shared_pa_candidate_v1/`, and `comparisons/`.  A changed probability is not
called an improvement.

## Deliberately deferred

- Real candidate predictions remain abstentions until the official 2023 source
  release is captured, independently verified, and produces a new qualified PA
  artifact.  Historical capture was not authorized or performed here.
- Pitcher candidate markets remain deferred because no qualified,
  receipt-proven joint pitcher-opportunity artifact exists.  The existing
  fixed-innings shortcut is not imported.
- RBI, HRR, and earned runs remain deferred because the repository lacks a
  qualified point-in-time ordered lineup/base-out/run-context state.
- Historical market evaluation cannot lawfully run until the source release
  exists.  The repository's market-separated evaluator already supplies Brier,
  log loss, calibration, coverage, abstention, and date/game clustered
  uncertainty, but synthetic mechanics are not performance evidence.

May 2026 is rejected before candidate evidence is opened.  No May artifact was
read, generated, fitted, or evaluated during this implementation.

## Parameter inventory

Structural parameters: the five supported markets, sportsbook threshold set
through 5.5, exact Total Bases weights, hard-key identity, strict chronology,
and zero-tolerance fallback policy.

Preserved frozen parameters: every value in `config/config.kbb.json`, the
frozen PA and K/BB artifacts, mutable Savant override behavior, simulator
rules, and rejected-candidate records.  None is reused by the candidate.

Candidate fitted/learned parameters: none were fitted in this task.  Per-PA and
opportunity probabilities must arrive in a qualified, hash-bound evidence
record.  The present EB-200 control is not promoted as the new fitted model.
