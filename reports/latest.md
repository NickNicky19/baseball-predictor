# Validation report — 2026-11-09

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-11-09 23:49:48 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-11-09 23:49:48 | INFO     | matplotlib.font_manager | generated new fontManager
2026-11-09 23:49:49 | INFO     | src.learning.retrain_runner | Filtered to 1 pairs within last 14 days (from 10771)
{
  "dates_evaluated": 1,
  "total_matched_pairs": 1,
  "mean_weighted_mae": 5.59,
  "results": [
    {
      "game_date": "2026-10-28",
      "matched_pairs": 1,
      "weighted_mae": 5.59,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 1,
          "mae": 5.59,
          "rmse": 5.59,
          "mean_error": 5.59,
          "median_error": 5.59,
          "mean_abs_pct_error": 2.795
        }
      },
      "error": ""
    }
  ],
  "retrain_report": null
}

Validated 1 dates, 1 pairs, mean MAE=5.5900
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-11-09 23:49:50 | INFO     | src.learning.retrain_runner | Filtered to 1 pairs within last 14 days (from 10771)
2026-11-09 23:49:50 | INFO     | src.learning.outcome_retrainer | Ingested 1 prediction-outcome pairs
2026-11-09 23:49:50 | INFO     | src.learning.retrain_runner | Retrain input: 1 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
Retrain error: Insufficient pairs for retraining: 1 < 25 — Need at least 25 aligned prediction-outcome rows
```
