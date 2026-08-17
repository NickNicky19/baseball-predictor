# Validation report — 2026-08-17

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-08-17 15:49:37 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-08-17 15:49:37 | INFO     | matplotlib.font_manager | generated new fontManager
2026-08-17 15:49:38 | INFO     | src.learning.retrain_runner | Filtered to 167 pairs within last 14 days (from 8645)
{
  "dates_evaluated": 7,
  "total_matched_pairs": 167,
  "mean_weighted_mae": 1.9463,
  "results": [
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
    },
    {
      "game_date": "2026-08-13",
      "matched_pairs": 18,
      "weighted_mae": 2.1811,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 18,
          "mae": 2.181111111111111,
          "rmse": 2.5321378758318476,
          "mean_error": -0.03000000000000005,
          "median_error": -0.010000000000000231,
          "mean_abs_pct_error": 1.0887103174603174
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-08-14",
      "matched_pairs": 25,
      "weighted_mae": 1.526,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 25,
          "mae": 1.5260000000000002,
          "rmse": 1.8956001688119781,
          "mean_error": 0.07239999999999995,
          "median_error": -0.15000000000000036,
          "mean_abs_pct_error": 0.4741568253968254
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-08-15",
      "matched_pairs": 29,
      "weighted_mae": 2.0003,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 29,
          "mae": 2.000344827586207,
          "rmse": 2.347696596740053,
          "mean_error": -0.6665517241379312,
          "median_error": -0.6399999999999997,
          "mean_abs_pct_error": 0.8047902672040601
        }
      },
      "error": ""
    }
  ],
  "retrain_report": null
}

Validated 7 dates, 167 pairs, mean MAE=1.9463
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-08-17 15:49:39 | INFO     | src.learning.retrain_runner | Filtered to 167 pairs within last 14 days (from 8645)
2026-08-17 15:49:39 | INFO     | src.learning.outcome_retrainer | Ingested 167 prediction-outcome pairs
2026-08-17 15:49:39 | INFO     | src.learning.retrain_runner | Retrain input: 167 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-08-17 15:49:39 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=167, confidence=0.67, MAE=1.957)
{
  "sample_size": 167,
  "confidence": 0.674,
  "weighted_mae": 1.9571257485029943,
  "state_path": "",
  "notes": "[dry-run] Fitted on 167 pairs. Weighted MAE=1.957. Confidence=0.67.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 167 pairs, confidence=0.67, MAE=1.957
```
