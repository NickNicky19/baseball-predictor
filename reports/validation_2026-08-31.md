# Validation report — 2026-08-31

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-08-31 20:58:14 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-08-31 20:58:15 | INFO     | matplotlib.font_manager | generated new fontManager
2026-08-31 20:58:15 | INFO     | src.learning.retrain_runner | Filtered to 129 pairs within last 14 days (from 8774)
{
  "dates_evaluated": 5,
  "total_matched_pairs": 129,
  "mean_weighted_mae": 1.7393,
  "results": [
    {
      "game_date": "2026-08-18",
      "matched_pairs": 28,
      "weighted_mae": 1.8721,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 28,
          "mae": 1.872142857142857,
          "rmse": 2.1996054841071584,
          "mean_error": 0.03999999999999999,
          "median_error": -0.21500000000000008,
          "mean_abs_pct_error": 0.5759732142857142
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-08-20",
      "matched_pairs": 18,
      "weighted_mae": 2.1278,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 18,
          "mae": 2.1277777777777778,
          "rmse": 2.41530306356964,
          "mean_error": -0.4555555555555556,
          "median_error": -0.5949999999999998,
          "mean_abs_pct_error": 0.4772441678691679
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-08-21",
      "matched_pairs": 27,
      "weighted_mae": 1.6137,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 27,
          "mae": 1.6137037037037034,
          "rmse": 1.8508246410321676,
          "mean_error": 0.6640740740740741,
          "median_error": 0.8899999999999997,
          "mean_abs_pct_error": 0.5029097589653145
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-08-23",
      "matched_pairs": 29,
      "weighted_mae": 1.4552,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 29,
          "mae": 1.4551724137931037,
          "rmse": 1.8342432433736997,
          "mean_error": 0.3317241379310345,
          "median_error": 0.54,
          "mean_abs_pct_error": 0.742801724137931
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-08-25",
      "matched_pairs": 27,
      "weighted_mae": 1.6278,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 27,
          "mae": 1.6277777777777775,
          "rmse": 2.1773641897520988,
          "mean_error": -0.19592592592592592,
          "median_error": -0.29000000000000004,
          "mean_abs_pct_error": 0.45713484046817365
        }
      },
      "error": ""
    }
  ],
  "retrain_report": null
}

Validated 5 dates, 129 pairs, mean MAE=1.7393
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-08-31 20:58:16 | INFO     | src.learning.retrain_runner | Filtered to 129 pairs within last 14 days (from 8774)
2026-08-31 20:58:16 | INFO     | src.learning.outcome_retrainer | Ingested 129 prediction-outcome pairs
2026-08-31 20:58:16 | INFO     | src.learning.retrain_runner | Retrain input: 129 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-08-31 20:58:16 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=129, confidence=0.71, MAE=1.709)
{
  "sample_size": 129,
  "confidence": 0.715,
  "weighted_mae": 1.7088372093023256,
  "state_path": "",
  "notes": "[dry-run] Fitted on 129 pairs. Weighted MAE=1.709. Confidence=0.71.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 129 pairs, confidence=0.71, MAE=1.709
```
