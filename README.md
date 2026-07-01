# Baseball Daily Predictor

Daily MLB prop prediction system for hitters (hits, HRR, home runs, fantasy) and pitchers (strikeouts). The project combines live MLB Stats API data with Statcast/Savant metrics and Monte Carlo simulation, with an optional self-improvement loop that learns from past prediction errors.

The codebase was rebuilt in five phases, productionized in Phase 6, strengthened in Phase 7, and polished for release. It follows a layered architecture designed for **accuracy**, **modularity**, **backtestability**, and **self-improvement** — not as a black-box script, but as a system you can test, calibrate, and extend.

## Current Architecture

The system follows a strict layer flow:

```
Data → Features → Simulation → Prediction → Evaluation → Learning
```

- **Data** — schedule, lineups, and stats from the MLB Stats API; Statcast from pybaseball or a local Savant CSV
- **Features** — Statcast profiles and daily feature bundles
- **Simulation** — plate-appearance and game-level Monte Carlo engine
- **Prediction** — `DailyPredictor` orchestration and prop projections
- **Evaluation** — backtesting and calibration metrics
- **Learning** — outcome retraining and optional bias corrections at prediction time

For full architecture details — class responsibilities, data flow diagrams, configuration reference, and extension patterns — see **[ARCHITECTURE.md](ARCHITECTURE.md)**.

### Self-Improvement

The model can learn from historical prediction–outcome pairs via `OutcomeRetrainer`, persist corrections with `BiasCorrector`, and apply them on future runs through `CorrectionManager`. Corrections are **optional and off by default**; they adjust league/PA simulator parameters and per-category projection offsets when enabled.

## Key Features

| Feature | Description |
|---------|-------------|
| **Multi-source data** | MLB Stats API for slates and stats; Statcast via pybaseball or `data/savant/stats.csv` |
| **Monte Carlo simulation** | Configurable N-run simulation for hitter props (hits, HRR, HR, fantasy) with correlated game outcomes |
| **Pitcher strikeouts** | K/9 regression from season + recent stats (full pitcher game sim not yet implemented) |
| **Learned corrections** | Optional bias correction from backtested errors — league baselines, PA coefficients, category offsets |
| **+EV edge analysis** | `EdgeCalculator` + extensible odds providers (file CSV/JSON, The Odds API) |
| **Self-improvement loop** | Archive predictions → record MLB actuals → append pairs → retrain corrections |
| **Backtesting & calibration** | `BacktestEngine` and `CalibrationEngine` for structured evaluation and parameter fitting |
| **Retrain runner** | `run_retrain.py` + `RetrainRunner` for fitting and saving corrections from historical pairs |
| **Lineup intelligence** | Confirmed vs projected lineup toggle with stats-driven PA/confidence/simulation adjustments |
| **Statistical purification** | League-derived PA coefficients, dynamic park/slot PA estimation, expanded calibration |
| **Context enrichment** | Weather, umpire, injury clients; Statcast launch-angle/exit-velo distributions |
| **Validation infrastructure** | Walk-forward validation, champion/challenger testing, calibration tracking, ROI simulation |
| **Production CLI** | `run_daily.py` with console/CSV/JSON output, corrections toggle, projected lineups flag |
| **Injectable dependencies** | All major components accept mocks for offline testing and backtests |

**Not yet available:** HRR combo markets via Odds API (use file odds for HRR), weather, BvP, injuries, built-in cron scheduler, and a full-featured GUI. See [ARCHITECTURE.md — Known Gaps](ARCHITECTURE.md#known-gaps-future-phases).

## Quick Start

### Install

```bash
cd baseball_predictor
pip install -r requirements.txt
# or: pip install -e ".[dev]"   # includes pytest
```

For live Statcast fetching (optional):

```bash
pip install pybaseball
```

Without pybaseball, place a Savant export at `data/savant/stats.csv` (path configurable in `config/config.json`).

### Run Predictions

**CLI** (primary interface):

```bash
python run_daily.py --date 2026-07-01 --category hrr
python run_daily.py --apply-corrections --format all -o data/predictions.csv
python run_daily.py --with-edges --odds-file data/odds/lines.csv --min-edge 5
python run_daily.py --category strikeouts --pitchers-only
python run_daily.py --no-corrections --verbose   # explicit flags, debug logging
python run_daily.py --format json -o data/predictions.json
python run_daily.py --refresh                    # bypass MLB API day cache
python run_daily.py --include-projected-lineups  # include predicted lineups when unconfirmed
```

**Self-improvement loop** (enable `outcome_recording.enabled` in config):

```bash
python run_daily.py --date 2026-07-01 --category hrr    # archives predictions
python run_record_outcomes.py --date 2026-07-01         # after games are final
python run_retrain.py                                   # fit corrections
python run_daily.py --apply-corrections                 # use saved state
python run_validate.py --from-pairs                     # validate pipeline metrics
```

**Live odds** (set `ODDS_API_KEY` and enable `odds.odds_api` in config):

```bash
python run_daily.py --with-edges --date 2026-07-01 --category hits
```

**GUI** (minimal Tkinter shell):

```bash
python main.py
```

Supports date, category, corrections toggle, projected lineup checkbox, Team/Opponent/Pitcher columns, and CSV export.

### Run Tests

```bash
python -m pytest tests/ -v
```

## Correction / Self-Improvement (Brief)

1. Run predictions with `outcome_recording.enabled` (archives to `data/learning/predictions/`), then `python run_record_outcomes.py --date YYYY-MM-DD` to append pairs to `data/learning/prediction_outcomes.csv`.
2. Run `python run_retrain.py` (uses `retraining` config block; saves to `data/learning/bias_corrections.json`).
3. Apply on future runs: `python run_daily.py --apply-corrections` or set `learning.apply_corrections: true` in config.

Programmatic alternative: `OutcomeRetrainer.ingest()` → `fit()` → `BiasCorrector.save()`.

See [ARCHITECTURE.md — Self-Improvement](ARCHITECTURE.md#self-improvement--correction-system) for the full workflow and programmatic examples.

## Project Structure

```
baseball_predictor/
├── ARCHITECTURE.md          # Detailed architecture reference
├── config/config.json       # League baselines, park factors, simulation, learning settings
├── run_daily.py             # Production CLI entry point
├── run_retrain.py           # Retrain corrections from historical pairs
├── run_record_outcomes.py   # Append prediction-outcome pairs after slate
├── run_validate.py          # End-to-end pipeline validation
├── main.py                  # GUI launcher
├── gui.py                   # Minimal Tkinter UI
├── data/                    # Runtime data (gitignored; see data/.gitkeep)
├── legacy/README.md         # Migration map for removed pre-rebuild code
├── src/
│   ├── models/              # Shared dataclasses (LeagueBaselines, PropProjection, …)
│   ├── data/                # MLBStatsAPI, SavantClient, odds/ providers
│   ├── features/            # StatcastFeatureEngine, LineupIntelligence, FeatureStore
│   ├── simulation/          # HybridPASimulator, GameSimulator, MonteCarloEngine
│   ├── prediction/          # DailyPredictor, PropEngine, CorrectionManager
│   ├── evaluation/          # BacktestEngine, CalibrationEngine, PipelineValidator
│   ├── learning/            # OutcomeRetrainer, BiasCorrector, RetrainRunner, archive/recorder
│   └── utils/               # Logging, TTL cache, structured errors
└── tests/
```

Legacy monolithic modules (`predictor.py`, `mlb_client.py`, old `gui/` package, `cli/`) were removed during the cleanup pass. See `legacy/README.md` for what replaced them.

## Requirements & Setup

| Requirement | Notes |
|-------------|-------|
| **Python** | 3.11+ |
| **Core deps** | `requests`, `pandas`, `numpy`, `scipy` (see `requirements.txt` or `pyproject.toml`) |
| **Optional: pybaseball** | Live Statcast fetch; without it, use a local Savant CSV |
| **Optional: dev** | `pip install -e ".[dev]"` for pytest |
| **Network** | Required for MLB Stats API calls on prediction runs |
| **Data files** | Optional `data/savant/stats.csv`; odds CSV at `data/odds/lines.csv`; correction state at `data/learning/bias_corrections.json` |

Configuration lives in `config/config.json`. Key blocks: `league_avg`, `park_factors`, `simulation.n_sims`, `odds` (file + `odds_api`), `edge`, `outcome_recording`, `learning`, `retraining`, and `calibration`.

### Odds file format (CSV)

```csv
player_name,category,line,over_odds,under_odds,sportsbook
Aaron Judge,hrr,1.5,-110,-110,DraftKings
```

Set `odds.enabled: true` in config, or pass `--with-edges --odds-file path.csv` on the CLI.

## Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — layers, classes, correction system, configuration, extension guide
- **[legacy/README.md](legacy/README.md)** — removed modules and their replacements