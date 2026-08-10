# Validation report — 2026-08-10

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-08-10 16:22:45 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-08-10 16:22:46 | INFO     | matplotlib.font_manager | generated new fontManager
2026-08-10 16:22:46 | INFO     | src.learning.retrain_runner | Filtered to 116 pairs within last 14 days (from 8573)
{
  "dates_evaluated": 5,
  "total_matched_pairs": 116,
  "mean_weighted_mae": 1.8318,
  "results": [
    {
      "game_date": "2026-07-27",
      "matched_pairs": 21,
      "weighted_mae": 1.2419,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 21,
          "mae": 1.241904761904762,
          "rmse": 1.5240516176735397,
          "mean_error": 0.3057142857142856,
          "median_error": 0.20999999999999996,
          "mean_abs_pct_error": 0.4995532879818595
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-08-03",
      "matched_pairs": 13,
      "weighted_mae": 1.7023,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 13,
          "mae": 1.7023076923076923,
          "rmse": 2.417664161954675,
          "mean_error": -0.19923076923076918,
          "median_error": 0.16000000000000014,
          "mean_abs_pct_error": 1.5172478632478634
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-08-04",
      "matched_pairs": 28,
      "weighted_mae": 1.495,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 28,
          "mae": 1.4949999999999999,
          "rmse": 1.997117565750342,
          "mean_error": 0.3142857142857142,
          "median_error": 0.02499999999999991,
          "mean_abs_pct_error": 1.2999375
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-08-07",
      "matched_pairs": 27,
      "weighted_mae": 2.2548,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 27,
          "mae": 2.254814814814815,
          "rmse": 2.8338078034098686,
          "mean_error": -0.11407407407407398,
          "median_error": 0.20999999999999996,
          "mean_abs_pct_error": 0.7900947971781305
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-08-09",
      "matched_pairs": 27,
      "weighted_mae": 2.4648,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 27,
          "mae": 2.4648148148148143,
          "rmse": 2.994794867148048,
          "mean_error": 0.07444444444444445,
          "median_error": 0.5899999999999999,
          "mean_abs_pct_error": 0.8162886403719738
        }
      },
      "error": ""
    }
  ],
  "retrain_report": null
}

Validated 5 dates, 116 pairs, mean MAE=1.8318
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-08-10 16:22:47 | INFO     | src.learning.retrain_runner | Filtered to 116 pairs within last 14 days (from 8573)
2026-08-10 16:22:47 | INFO     | src.learning.outcome_retrainer | Ingested 116 prediction-outcome pairs
2026-08-10 16:22:47 | INFO     | src.learning.retrain_runner | Retrain input: 116 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-08-10 16:22:47 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=116, confidence=0.69, MAE=1.875)
{
  "sample_size": 116,
  "confidence": 0.688,
  "weighted_mae": 1.8750000000000002,
  "state_path": "",
  "notes": "[dry-run] Fitted on 116 pairs. Weighted MAE=1.875. Confidence=0.69.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 116 pairs, confidence=0.69, MAE=1.875
```
