# Validation report — 2026-09-21

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-09-21 20:05:28 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-09-21 20:05:29 | INFO     | matplotlib.font_manager | generated new fontManager
2026-09-21 20:05:30 | INFO     | src.learning.retrain_runner | Filtered to 27 pairs within last 14 days (from 9438)
{
  "dates_evaluated": 1,
  "total_matched_pairs": 27,
  "mean_weighted_mae": 1.5256,
  "results": [
    {
      "game_date": "2026-09-18",
      "matched_pairs": 27,
      "weighted_mae": 1.5256,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 27,
          "mae": 1.5255555555555556,
          "rmse": 2.043661383852974,
          "mean_error": 0.7462962962962963,
          "median_error": 0.45999999999999996,
          "mean_abs_pct_error": 1.1445348324514992
        }
      },
      "error": ""
    }
  ],
  "retrain_report": null
}

Validated 1 dates, 27 pairs, mean MAE=1.5256
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-09-21 20:05:31 | INFO     | src.learning.retrain_runner | Filtered to 27 pairs within last 14 days (from 9438)
2026-09-21 20:05:31 | INFO     | src.learning.outcome_retrainer | Ingested 27 prediction-outcome pairs
2026-09-21 20:05:31 | INFO     | src.learning.retrain_runner | Retrain input: 27 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-09-21 20:05:31 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=27, confidence=0.38, MAE=1.526)
{
  "sample_size": 27,
  "confidence": 0.381,
  "weighted_mae": 1.5255555555555556,
  "state_path": "",
  "notes": "[dry-run] Fitted on 27 pairs. Weighted MAE=1.526. Confidence=0.38.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 27 pairs, confidence=0.38, MAE=1.526
```
