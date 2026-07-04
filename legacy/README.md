# Legacy / Archived Reference

This folder contains **deprecated code documentation and historical artifacts only**.
Nothing here is imported by the active production pipeline.

## Contents

| Path | Description |
|------|-------------|
| `BUG_AUDIT_REPORT.txt` | Pre-rebuild audit from the original monolithic codebase |
| `README.md` | This file — migration notes |

## Removed modules (recover from git history)

| Old path | Replaced by |
|----------|-------------|
| `src/models/predictor.py` | `src/prediction/daily_predictor.py` |
| `src/models/backtester.py` | `src/evaluation/backtest_engine.py` + `calibration.py` |
| `src/models/value.py` | `src/prediction/edge_calculator.py` |
| `src/models/simulation/` | `src/simulation/` |
| `src/data/mlb_client.py` | `src/data/mlb_api.py` |
| `src/savant_engine.py` | `src/data/savant.py` |
| `src/features/hitters/` | `src/features/legacy_statcast_features.py` + `feature_store.py` |
| `src/features/pitchers/` | (planned — pitcher feature pipeline) |
| `src/features/scoring.py`, `matchup.py`, `recency.py` | `matchup_intelligence.py`, `PropEngine` |
| `gui/` package | Root `gui.py` |
| `cli/main.py` | `run_daily.py` |

## Statcast naming note

- **Production profiles:** `src/features/legacy_statcast_features.py` (`StatcastFeatureEngine`)
- **ML feature layer:** `src/features/ml/statcast_features.py` (`StatcastFeatureEngineer`)

See [STRUCTURE.md](../STRUCTURE.md) for the current layout.