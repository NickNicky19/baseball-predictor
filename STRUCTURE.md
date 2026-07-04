# Project Structure

High-level layout for `baseball_predictor`. See [ARCHITECTURE.md](ARCHITECTURE.md) for
layer design and data flow.

## Root (production entry points)

| Path | Role |
|------|------|
| `run_daily.py` | Daily prop predictions CLI |
| `run_validate.py` | Walk-forward / pipeline validation CLI |
| `run_retrain.py` | Learning / bias correction retrain CLI |
| `run_record_outcomes.py` | Record actuals for backtests |
| `main.py` | GUI launcher |
| `gui.py` | Tkinter prediction UI |
| `config/config.json` | League baselines, simulation, calibration settings |

## `src/` — core production code

| Package | Purpose |
|---------|---------|
| `src/models/` | Shared dataclasses (`LeagueBaselines`, `PropProjection`, …) |
| `src/data/` | MLB API, Savant, weather, umpire, odds clients |
| `src/features/` | Feature bundles, factory, matchup/lineup intelligence |
| `src/features/ml/` | **New** modular feature engineering (pipeline, rolling, context) |
| `src/simulation/` | PA simulator, game sim, Monte Carlo, probability engine |
| `src/prediction/` | DailyPredictor, PropEngine, edges, corrections |
| `src/evaluation/` | Backtest, calibration, walk-forward, safeguards |
| `src/learning/` | Outcome recording, retraining, bias correction |
| `src/utils/` | Logging, cache, errors |

### Feature layer split

| Module | Role |
|--------|------|
| `legacy_statcast_features.py` | **Current production** — builds `StatcastProfile` for slates |
| `feature_factory.py` | Orchestrates bundles (Statcast + matchup + vectors) |
| `feature_vector.py` | Rich z-score / interaction features |
| `rich_feature_enricher.py` | Bridge to `src/features/ml/` (additive, optional) |
| `ml/statcast_features.py` | **Future path** — `StatcastFeatureEngineer` for ML pipeline |

## `scripts/` — development & diagnostics

One-off debugging and validation tools. Not used in production runs.
See [scripts/README.md](scripts/README.md).

## `tests/` — pytest suite

Integration and unit tests for all layers.

## `legacy/` — archived reference

Deprecated pre-rebuild documentation and audit artifacts only. No runtime imports.
See [legacy/README.md](legacy/README.md).

## `data/` — runtime artifacts

Features, odds, learning pairs, prediction archives (gitignored contents).