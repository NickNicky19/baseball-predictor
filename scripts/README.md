# Development & Diagnostic Scripts

One-off tools for debugging, validation, and model inspection. These are **not**
part of the production daily pipeline.

Run any script from the project root:

```bash
python scripts/diagnose_probabilities.py
python scripts/validate_current_model.py
```

## Scripts

| Script | Purpose |
|--------|---------|
| `diagnose_probabilities.py` | Per-PA outcome probabilities vs Monte Carlo for a single player |
| `diagnose_full_pipeline.py` | End-to-end pipeline trace for a single player |
| `diagnose_simulation_count.py` | Inspect per-game hit accumulation across MC runs |
| `validate_current_model.py` | Quick slate-wide realism check on hits/HR/HRR |

Production entry points remain at the project root: `run_daily.py`, `run_validate.py`,
`run_retrain.py`, `run_record_outcomes.py`, `main.py`, `gui.py`.