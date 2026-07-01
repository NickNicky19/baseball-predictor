# Legacy Code (Removed During Cleanup)

The following pre-architecture files were removed in the final cleanup pass.
The active codebase now lives under the layered structure documented in `ARCHITECTURE.md`.

## Removed modules

| Path | Replaced by |
|------|-------------|
| `src/models/predictor.py` | `src/prediction/daily_predictor.py` |
| `src/models/backtester.py` | `src/evaluation/backtest_engine.py` + `calibration.py` |
| `src/models/value.py` | `src/prediction/edge_calculator.py` |
| `src/models/simulation/` | `src/simulation/` |
| `src/data/mlb_client.py` | `src/data/mlb_api.py` |
| `src/savant_engine.py` | `src/data/savant.py` |
| `src/features/hitters/` | `src/features/statcast_features.py` + `feature_store.py` |
| `src/features/pitchers/` | (not yet implemented — Phase 6+) |
| `src/features/scoring.py`, `matchup.py`, `recency.py` | Simulation + PropEngine |
| `gui/` package | Root `gui.py` (minimal, new architecture) |
| `cli/main.py` | `run_daily.py` |
| `src/data/bvp_client.py`, `weather_client.py`, `odds_parser.py` | Planned for future data layer |

Recover removed files from git history if needed.

## Historical artifacts

| Path | Description |
|------|-------------|
| `legacy/BUG_AUDIT_REPORT.txt` | Pre-rebuild audit from the original monolithic codebase (archived for reference) |