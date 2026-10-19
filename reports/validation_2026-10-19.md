# Validation report — 2026-10-19

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-10-19 20:40:17 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-10-19 20:40:17 | INFO     | matplotlib.font_manager | generated new fontManager
2026-10-19 20:40:17 | INFO     | src.learning.retrain_runner | Filtered to 185 pairs within last 14 days (from 10766)
{
  "dates_evaluated": 9,
  "total_matched_pairs": 185,
  "mean_weighted_mae": 1.6826,
  "results": [
    {
      "game_date": "2026-10-06",
      "matched_pairs": 28,
      "weighted_mae": 0.708,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 8,
          "mae": 0.45899999999999996,
          "rmse": 0.6049156966057336,
          "mean_error": 0.37349999999999994,
          "median_error": 0.3175,
          "mean_abs_pct_error": 1.7077499999999999
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 8,
          "mae": 0.14775,
          "rmse": 0.15799920885877877,
          "mean_error": 0.14775,
          "median_error": 0.1515,
          "mean_abs_pct_error": 0.591
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 8,
          "mae": 1.045,
          "rmse": 1.1564876782741786,
          "mean_error": 0.9757500000000001,
          "median_error": 1.036,
          "mean_abs_pct_error": 2.7406875
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 4,
          "mae": 1.6524999999999999,
          "rmse": 2.0996964066264434,
          "mean_error": -0.1825000000000001,
          "median_error": 0.3799999999999999,
          "mean_abs_pct_error": 0.3197023809523809
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-10-07",
      "matched_pairs": 88,
      "weighted_mae": 0.8102,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 27,
          "mae": 0.6098518518518519,
          "rmse": 0.7451460209868885,
          "mean_error": 0.08007407407407406,
          "median_error": 0.008999999999999897,
          "mean_abs_pct_error": 1.531962962962963
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 27,
          "mae": 0.17455555555555555,
          "rmse": 0.21889952032839177,
          "mean_error": 0.11381481481481481,
          "median_error": 0.137,
          "mean_abs_pct_error": 0.607111111111111
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 27,
          "mae": 1.2847037037037037,
          "rmse": 1.5691676609426246,
          "mean_error": -0.08618518518518524,
          "median_error": 0.09600000000000009,
          "mean_abs_pct_error": 2.138351234567901
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 7,
          "mae": 2.204285714285714,
          "rmse": 2.425910727364644,
          "mean_error": 1.7042857142857142,
          "median_error": 1.5300000000000002,
          "mean_abs_pct_error": 3.0912619047619048
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-10-10",
      "matched_pairs": 2,
      "weighted_mae": 3.745,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 2,
          "mae": 3.7449999999999997,
          "rmse": 3.9046190595242445,
          "mean_error": -1.105,
          "median_error": -1.105,
          "mean_abs_pct_error": 0.5320833333333332
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-10-11",
      "matched_pairs": 2,
      "weighted_mae": 1.43,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 2,
          "mae": 1.4299999999999997,
          "rmse": 1.841331040307527,
          "mean_error": 1.1600000000000001,
          "median_error": 1.1600000000000001,
          "mean_abs_pct_error": 0.27828571428571425
        }
      },
      "error": ""
    },
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
    }
  ],
  "retrain_report": null
}

Validated 9 dates, 185 pairs, mean MAE=1.6826
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-10-19 20:40:18 | INFO     | src.learning.retrain_runner | Filtered to 185 pairs within last 14 days (from 10766)
2026-10-19 20:40:18 | INFO     | src.learning.outcome_retrainer | Ingested 185 prediction-outcome pairs
2026-10-19 20:40:18 | INFO     | src.learning.retrain_runner | Retrain input: 185 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-10-19 20:40:18 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=185, confidence=0.86, MAE=0.844)
{
  "sample_size": 185,
  "confidence": 0.859,
  "weighted_mae": 0.8436054054054054,
  "state_path": "",
  "notes": "[dry-run] Fitted on 185 pairs. Weighted MAE=0.844. Confidence=0.86.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 185 pairs, confidence=0.86, MAE=0.844
```
