# This Session — What Changed and How to Apply It

This bundle contains the files changed in THIS session. But first, an honest
caveat you need to read, because it affects whether these files will work.

## Important: I can't see your PC repo

Across our sessions I've sent several packages (HRR fixes, the xBA model
rewrite, the point-in-time module, the automation, the edge engine). I don't
know for certain which you actually merged into `C:\Projects\baseball_predictor`.
Some files below depend on earlier changes. **Before dropping these in, run the
verification at the bottom** so you don't end up with a half-merged repo.

## Files in this bundle (7)

### New files (were NOT in your uploaded repo — safe to add)
- `src/prediction/shadow_logger.py` — logs would-be value plays during
  collection (read-only shadow record).
- `src/evaluation/calibration_report.py` — the calibration report. Reads your
  pairs file and tells you per-category bias/error + whether confidence is
  meaningful. **This is the thing you run after ~3 weeks of data.** Verified
  working; on synthetic data with a planted strikeout over-projection it
  correctly flagged "strikeouts OVER-projecting by +0.849."
- `tests/test_edge_engine.py` — 8 regression tests for the edge math.

### Modified files (replace yours — but see dependency notes)
- `src/prediction/edge_calculator.py` — de-vigging, both-sides edge, true HR
  distribution, fractional-Kelly staking, kelly/edge ranking.
- `src/models/dataclasses.py` — adds EdgeResult de-vig/Kelly fields AND
  (from earlier sessions) `xba_on_contact` on LeagueBaselines. **If your PC
  copy doesn't already have `xba_on_contact`, this file brings it — good — but
  it also means your PC needs the matching xBA changes in `pa_simulator.py`
  and `legacy_statcast_features.py`. See dependency check.**
- `gui.py` — click-to-sort column headers (read-only; confidence sort carries
  an "unvalidated" warning).
- `src/data/mlb_api.py` — logs which teams were skipped for missing lineups
  (logging only; no projection change).

## Dependency check — RUN THIS FIRST

Open PowerShell in your repo and run:

```
python -c "from src.models.dataclasses import LeagueBaselines; print('xba_on_contact' in dir(LeagueBaselines()))"
python -c "from src.simulation.pa_simulator import PASimulatorConfig; print(hasattr(PASimulatorConfig(), 'xba_shrinkage_pa'))"
```

- If BOTH print `True`: your repo already has the xBA model. These files drop
  in cleanly. Proceed.
- If EITHER prints `False` or errors: your PC is missing the xBA-model changes
  from the earlier package (`baseball_xba.zip`). Applying `dataclasses.py`
  alone will half-merge you. **Stop and tell me** — I'll give you a single
  clean full-repo bundle instead of incremental files.

## If the dependency check passes — how to apply

1. Copy the 7 files into your repo at the same paths.
2. Verify nothing broke:
   ```
   python -c "import src.prediction.edge_calculator, src.evaluation.calibration_report, src.prediction.shadow_logger, gui; print('imports OK')"
   ```
3. Run the tests if you have pytest, or just confirm imports.
4. Commit:
   ```
   git add -A
   git commit -m "Edge engine, shadow logger, calibration report, GUI sort, lineup logging"
   git push
   ```

## What each piece is FOR (quick reference)

- **Edge engine** — computes +EV plays vs a real odds line (de-vigged, Kelly).
  Needs an odds feed to do anything; useless without lines. Not for betting
  until calibration validates the model.
- **Shadow logger** — during the 3-week wait, records what the engine WOULD
  play so you can grade it later. Read-only.
- **Calibration report** — the payoff. After ~3 weeks of pairs, run
  `python -m src.evaluation.calibration_report`. It tells you which categories
  to trust and becomes the basis for the color-coding thresholds.
- **GUI sort** — quality-of-life; sort any column. Don't trust the confidence
  sort until the calibration report says confidence is meaningful.
- **Lineup logging** — explains thin slates instead of silently dropping teams.

## The honest status

Nothing here changes the model's projections (the edge engine, shadow logger,
calibration report, GUI sort, and lineup logging are all read-only or
additive). So applying these does NOT reset your data-collection clock. Good to
merge now.

The real work remains what it's been: let the collection run, test an odds API
key, and in ~3 weeks run the calibration report on real data. These files are
the tools that make that moment productive — not a reason to act sooner.
