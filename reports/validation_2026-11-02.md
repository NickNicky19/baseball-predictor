# Validation report — 2026-11-02

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-11-02 23:10:42 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-11-02 23:10:43 | INFO     | matplotlib.font_manager | generated new fontManager
2026-11-02 23:10:43 | INFO     | src.learning.retrain_runner | Filtered to 5 pairs within last 14 days (from 10771)
{
  "dates_evaluated": 3,
  "total_matched_pairs": 5,
  "mean_weighted_mae": 3.3267,
  "results": [
    {
      "game_date": "2026-10-23",
      "matched_pairs": 2,
      "weighted_mae": 2.615,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 2,
          "mae": 2.6149999999999998,
          "rmse": 3.2770184619559286,
          "mean_error": 1.975,
          "median_error": 1.975,
          "mean_abs_pct_error": 0.829
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-10-24",
      "matched_pairs": 2,
      "weighted_mae": 1.775,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 2,
          "mae": 1.775,
          "rmse": 1.778102921655549,
          "mean_error": 1.775,
          "median_error": 1.775,
          "mean_abs_pct_error": 0.44375
        }
      },
      "error": ""
    },
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

Validated 3 dates, 5 pairs, mean MAE=3.3267
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-11-02 23:10:44 | INFO     | src.learning.retrain_runner | Filtered to 5 pairs within last 14 days (from 10771)
2026-11-02 23:10:44 | INFO     | src.learning.outcome_retrainer | Ingested 5 prediction-outcome pairs
2026-11-02 23:10:44 | INFO     | src.learning.retrain_runner | Retrain input: 5 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
Retrain error: Insufficient pairs for retraining: 5 < 25 — Need at least 25 aligned prediction-outcome rows
```
