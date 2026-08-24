# Validation report — 2026-08-24

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-08-24 16:03:56 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-08-24 16:03:56 | INFO     | matplotlib.font_manager | generated new fontManager
2026-08-24 16:03:56 | INFO     | src.learning.retrain_runner | Filtered to 174 pairs within last 14 days (from 8747)
{
  "dates_evaluated": 7,
  "total_matched_pairs": 174,
  "mean_weighted_mae": 1.8252,
  "results": [
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
    },
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
    }
  ],
  "retrain_report": null
}

Validated 7 dates, 174 pairs, mean MAE=1.8252
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-08-24 16:03:58 | INFO     | src.learning.retrain_runner | Filtered to 174 pairs within last 14 days (from 8747)
2026-08-24 16:03:58 | INFO     | src.learning.outcome_retrainer | Ingested 174 prediction-outcome pairs
2026-08-24 16:03:58 | INFO     | src.learning.retrain_runner | Retrain input: 174 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-08-24 16:03:58 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=174, confidence=0.70, MAE=1.793)
{
  "sample_size": 174,
  "confidence": 0.701,
  "weighted_mae": 1.792586206896552,
  "state_path": "",
  "notes": "[dry-run] Fitted on 174 pairs. Weighted MAE=1.793. Confidence=0.70.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 174 pairs, confidence=0.70, MAE=1.793
```
