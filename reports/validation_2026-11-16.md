# Validation report — 2026-11-16

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-11-16 23:47:58 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-11-16 23:47:58 | INFO     | matplotlib.font_manager | generated new fontManager
2026-11-16 23:47:59 | INFO     | src.learning.retrain_runner | Filtered to 0 pairs within last 14 days (from 10771)
{
  "dates_evaluated": 0,
  "total_matched_pairs": 0,
  "mean_weighted_mae": 0.0,
  "results": [],
  "retrain_report": null
}

Validated 0 dates, 0 pairs, mean MAE=0.0000
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-11-16 23:48:00 | INFO     | src.learning.retrain_runner | Filtered to 0 pairs within last 14 days (from 10771)
2026-11-16 23:48:00 | INFO     | src.learning.outcome_retrainer | Ingested 0 prediction-outcome pairs
2026-11-16 23:48:00 | INFO     | src.learning.retrain_runner | Retrain input: 0 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
Retrain error: Insufficient pairs for retraining: 0 < 25 — Need at least 25 aligned prediction-outcome rows
```
