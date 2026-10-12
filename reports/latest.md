# Validation report — 2026-10-12

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-10-12 22:32:52 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-10-12 22:32:52 | INFO     | matplotlib.font_manager | generated new fontManager
2026-10-12 22:32:53 | INFO     | src.learning.retrain_runner | Filtered to 237 pairs within last 14 days (from 10701)
{
  "dates_evaluated": 7,
  "total_matched_pairs": 237,
  "mean_weighted_mae": 1.4981,
  "results": [
    {
      "game_date": "2026-10-01",
      "matched_pairs": 2,
      "weighted_mae": 2.16,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 2,
          "mae": 2.1599999999999997,
          "rmse": 3.019552947043651,
          "mean_error": 2.11,
          "median_error": 2.11,
          "mean_abs_pct_error": 2.1475
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-10-03",
      "matched_pairs": 61,
      "weighted_mae": 0.9306,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 18,
          "mae": 0.8154444444444445,
          "rmse": 0.8680664337096172,
          "mean_error": 0.47977777777777786,
          "median_error": 0.7925,
          "mean_abs_pct_error": 2.6841388888888886
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 18,
          "mae": 0.17661111111111108,
          "rmse": 0.24277481564427372,
          "mean_error": 0.08216666666666668,
          "median_error": 0.127,
          "mean_abs_pct_error": 0.5647777777777778
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 18,
          "mae": 1.559388888888889,
          "rmse": 1.7185777550055743,
          "mean_error": 0.9743888888888887,
          "median_error": 1.555,
          "mean_abs_pct_error": 4.999887037037038
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 7,
          "mae": 1.5485714285714285,
          "rmse": 2.1423018328090797,
          "mean_error": 0.040000000000000036,
          "median_error": -0.1299999999999999,
          "mean_abs_pct_error": 0.5979625850340137
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-10-04",
      "matched_pairs": 54,
      "weighted_mae": 0.7028,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 17,
          "mae": 0.6048823529411765,
          "rmse": 0.732479070168762,
          "mean_error": 0.3766470588235294,
          "median_error": 0.636,
          "mean_abs_pct_error": 2.030852941176471
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 17,
          "mae": 0.15970588235294117,
          "rmse": 0.17141470181988475,
          "mean_error": 0.15970588235294117,
          "median_error": 0.143,
          "mean_abs_pct_error": 0.6388235294117647
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 17,
          "mae": 1.2755882352941177,
          "rmse": 1.404632524688135,
          "mean_error": 0.5146470588235293,
          "median_error": 0.8859999999999999,
          "mean_abs_pct_error": 2.8471176470588238
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 3,
          "mae": 1.0899999999999999,
          "rmse": 1.2272598203586176,
          "mean_error": 0.5633333333333331,
          "median_error": 0.5999999999999996,
          "mean_abs_pct_error": 0.24055555555555552
        }
      },
      "error": ""
    },
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
    }
  ],
  "retrain_report": null
}

Validated 7 dates, 237 pairs, mean MAE=1.4981
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-10-12 22:32:54 | INFO     | src.learning.retrain_runner | Filtered to 237 pairs within last 14 days (from 10701)
2026-10-12 22:32:54 | INFO     | src.learning.outcome_retrainer | Ingested 237 prediction-outcome pairs
2026-10-12 22:32:54 | INFO     | src.learning.retrain_runner | Retrain input: 237 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-10-12 22:32:54 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=237, confidence=0.86, MAE=0.846)
{
  "sample_size": 237,
  "confidence": 0.859,
  "weighted_mae": 0.846029535864979,
  "state_path": "",
  "notes": "[dry-run] Fitted on 237 pairs. Weighted MAE=0.846. Confidence=0.86.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 237 pairs, confidence=0.86, MAE=0.846
```
