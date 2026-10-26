# Validation report — 2026-10-26

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-10-26 20:39:58 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-10-26 20:39:59 | INFO     | matplotlib.font_manager | generated new fontManager
2026-10-26 20:39:59 | INFO     | src.learning.retrain_runner | Filtered to 69 pairs within last 14 days (from 10770)
{
  "dates_evaluated": 7,
  "total_matched_pairs": 69,
  "mean_weighted_mae": 1.8343,
  "results": [
    {
      "game_date": "2026-10-13",
      "matched_pairs": 2,
      "weighted_mae": 1.92,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 2,
          "mae": 1.92,
          "rmse": 2.1421717951648978,
          "mean_error": 0.9500000000000002,
          "median_error": 0.9500000000000002,
          "mean_abs_pct_error": 0.5591666666666666
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-10-14",
      "matched_pairs": 2,
      "weighted_mae": 2.815,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 2,
          "mae": 2.8150000000000004,
          "rmse": 2.875143474680873,
          "mean_error": -0.585,
          "median_error": -0.585,
          "mean_abs_pct_error": 0.5605555555555557
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-10-15",
      "matched_pairs": 57,
      "weighted_mae": 0.6854,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 18,
          "mae": 0.6076111111111111,
          "rmse": 0.7246507587950365,
          "mean_error": 0.3846111111111112,
          "median_error": 0.6805,
          "mean_abs_pct_error": 2.0638055555555557
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 18,
          "mae": 0.16550000000000004,
          "rmse": 0.24677756516074686,
          "mean_error": 0.06527777777777778,
          "median_error": 0.121,
          "mean_abs_pct_error": 0.5116666666666667
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 18,
          "mae": 1.1572777777777778,
          "rmse": 1.3814496210704015,
          "mean_error": 0.6396111111111111,
          "median_error": 0.78,
          "mean_abs_pct_error": 3.2858564814814817
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 3,
          "mae": 1.4400000000000002,
          "rmse": 1.4885115608105521,
          "mean_error": 0.20666666666666686,
          "median_error": 0.9400000000000004,
          "mean_abs_pct_error": 0.3757407407407407
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-10-17",
      "matched_pairs": 2,
      "weighted_mae": 1.43,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 2,
          "mae": 1.4300000000000002,
          "rmse": 1.4317122615944868,
          "mean_error": 1.4300000000000002,
          "median_error": 1.4300000000000002,
          "mean_abs_pct_error": 0.9766666666666667
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-10-18",
      "matched_pairs": 2,
      "weighted_mae": 1.6,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 2,
          "mae": 1.6,
          "rmse": 2.042767730311011,
          "mean_error": 1.27,
          "median_error": 1.27,
          "mean_abs_pct_error": 0.5058333333333334
        }
      },
      "error": ""
    },
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
    }
  ],
  "retrain_report": null
}

Validated 7 dates, 69 pairs, mean MAE=1.8343
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-10-26 20:40:00 | INFO     | src.learning.retrain_runner | Filtered to 69 pairs within last 14 days (from 10770)
2026-10-26 20:40:00 | INFO     | src.learning.outcome_retrainer | Ingested 69 prediction-outcome pairs
2026-10-26 20:40:00 | INFO     | src.learning.retrain_runner | Retrain input: 69 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-10-26 20:40:00 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=69, confidence=0.69, MAE=0.919)
{
  "sample_size": 69,
  "confidence": 0.692,
  "weighted_mae": 0.9185072463768116,
  "state_path": "",
  "notes": "[dry-run] Fitted on 69 pairs. Weighted MAE=0.919. Confidence=0.69.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 69 pairs, confidence=0.69, MAE=0.919
```
