# Validation report — 2026-10-05

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-10-05 22:07:07 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-10-05 22:07:08 | INFO     | matplotlib.font_manager | generated new fontManager
2026-10-05 22:07:08 | INFO     | src.learning.retrain_runner | Filtered to 1143 pairs within last 14 days (from 10581)
{
  "dates_evaluated": 7,
  "total_matched_pairs": 1143,
  "mean_weighted_mae": 1.2493,
  "results": [
    {
      "game_date": "2026-09-21",
      "matched_pairs": 6,
      "weighted_mae": 2.3217,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 6,
          "mae": 2.3216666666666668,
          "rmse": 2.5329528223004867,
          "mean_error": 0.6849999999999999,
          "median_error": 1.6949999999999998,
          "mean_abs_pct_error": 0.6877777777777778
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-09-23",
      "matched_pairs": 161,
      "weighted_mae": 0.9138,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 44,
          "mae": 0.6524090909090909,
          "rmse": 0.7932761756847931,
          "mean_error": 0.1538181818181818,
          "median_error": 0.0615,
          "mean_abs_pct_error": 1.7430075757575763
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 44,
          "mae": 0.1655909090909091,
          "rmse": 0.24579980767800746,
          "mean_error": 0.05490909090909091,
          "median_error": 0.105,
          "mean_abs_pct_error": 0.49634090909090905
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 44,
          "mae": 1.1877727272727272,
          "rmse": 1.3856095475211687,
          "mean_error": 0.38972727272727264,
          "median_error": 0.745,
          "mean_abs_pct_error": 2.895127651515151
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 29,
          "mae": 2.0300000000000002,
          "rmse": 2.2234634368867896,
          "mean_error": 0.4148275862068966,
          "median_error": 0.7400000000000002,
          "mean_abs_pct_error": 1.2471613300492612
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-09-26",
      "matched_pairs": 186,
      "weighted_mae": 0.9173,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 54,
          "mae": 0.6813518518518517,
          "rmse": 0.8191493216203694,
          "mean_error": 0.11412962962962962,
          "median_error": 0.026499999999999968,
          "mean_abs_pct_error": 1.7359753086419754
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 54,
          "mae": 0.23685185185185184,
          "rmse": 0.398804324059919,
          "mean_error": 0.0028888888888888823,
          "median_error": 0.12,
          "mean_abs_pct_error": 0.5785555555555556
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 54,
          "mae": 1.3350185185185184,
          "rmse": 1.7075030773791477,
          "mean_error": 0.21198148148148144,
          "median_error": 0.5485,
          "mean_abs_pct_error": 2.6703615079365077
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 24,
          "mae": 2.0395833333333333,
          "rmse": 2.5632945922516726,
          "mean_error": 0.42791666666666667,
          "median_error": 0.8700000000000001,
          "mean_abs_pct_error": 0.8854401455026455
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-09-27",
      "matched_pairs": 673,
      "weighted_mae": 0.7992,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 215,
          "mae": 0.6390325581395349,
          "rmse": 0.8088590126379142,
          "mean_error": 0.048055813953488354,
          "median_error": -0.04700000000000004,
          "mean_abs_pct_error": 1.5297538759689924
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 215,
          "mae": 0.23181860465116283,
          "rmse": 0.4005089610819922,
          "mean_error": -0.023390697674418603,
          "median_error": 0.103,
          "mean_abs_pct_error": 0.5270372093023256
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 215,
          "mae": 1.391432558139535,
          "rmse": 1.7661589225024128,
          "mean_error": 0.033060465116279046,
          "median_error": 0.496,
          "mean_abs_pct_error": 2.604684213228632
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 28,
          "mae": 1.8382142857142856,
          "rmse": 2.309719370709042,
          "mean_error": 1.2282142857142861,
          "median_error": 1.0350000000000001,
          "mean_abs_pct_error": 1.5970922619047616
        }
      },
      "error": ""
    },
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
    }
  ],
  "retrain_report": null
}

Validated 7 dates, 1143 pairs, mean MAE=1.2493
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-10-05 22:07:09 | INFO     | src.learning.retrain_runner | Filtered to 1143 pairs within last 14 days (from 10581)
2026-10-05 22:07:10 | INFO     | src.learning.outcome_retrainer | Ingested 1143 prediction-outcome pairs
2026-10-05 22:07:10 | INFO     | src.learning.retrain_runner | Retrain input: 1143 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-10-05 22:07:10 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=1143, confidence=0.86, MAE=0.847)
{
  "sample_size": 1143,
  "confidence": 0.859,
  "weighted_mae": 0.8473998250218723,
  "state_path": "",
  "notes": "[dry-run] Fitted on 1143 pairs. Weighted MAE=0.847. Confidence=0.86.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 1143 pairs, confidence=0.86, MAE=0.847
```
