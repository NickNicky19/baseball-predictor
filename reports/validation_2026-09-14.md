# Validation report — 2026-09-14

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-09-14 19:54:55 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-09-14 19:54:55 | INFO     | matplotlib.font_manager | generated new fontManager
2026-09-14 19:54:55 | INFO     | src.learning.retrain_runner | Filtered to 637 pairs within last 14 days (from 9411)
{
  "dates_evaluated": 4,
  "total_matched_pairs": 637,
  "mean_weighted_mae": 1.1804,
  "results": [
    {
      "game_date": "2026-08-31",
      "matched_pairs": 182,
      "weighted_mae": 1.0827,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 53,
          "mae": 0.726320754716981,
          "rmse": 0.9228396189285281,
          "mean_error": -0.18405660377358488,
          "median_error": -0.129,
          "mean_abs_pct_error": 1.3144889937106918
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 53,
          "mae": 0.31820754716981137,
          "rmse": 0.5118732338647625,
          "mean_error": -0.10284905660377358,
          "median_error": 0.114,
          "mean_abs_pct_error": 0.6066320754716981
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 53,
          "mae": 1.8613207547169812,
          "rmse": 2.4868885690189217,
          "mean_error": -0.766377358490566,
          "median_error": -0.516,
          "mean_abs_pct_error": 2.1117392891175912
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 23,
          "mae": 1.8717391304347826,
          "rmse": 2.441767891258833,
          "mean_error": -0.4178260869565218,
          "median_error": -0.3700000000000001,
          "mean_abs_pct_error": 0.5448961352657005
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-09-02",
      "matched_pairs": 245,
      "weighted_mae": 1.0636,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 72,
          "mae": 0.8032222222222223,
          "rmse": 0.9621002055688147,
          "mean_error": -0.02405555555555556,
          "median_error": -0.06,
          "mean_abs_pct_error": 1.760377314814815
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 72,
          "mae": 0.28379166666666666,
          "rmse": 0.4363772418701762,
          "mean_error": -0.055430555555555566,
          "median_error": 0.113,
          "mean_abs_pct_error": 0.6134444444444443
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 72,
          "mae": 1.7488888888888887,
          "rmse": 2.2139608485447275,
          "mean_error": -0.1552222222222222,
          "median_error": 0.833,
          "mean_abs_pct_error": 2.9122741567460317
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 29,
          "mae": 1.9444827586206896,
          "rmse": 2.355733285061114,
          "mean_error": 0.20517241379310344,
          "median_error": 0.1299999999999999,
          "mean_abs_pct_error": 0.6461552346121312
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-09-03",
      "matched_pairs": 180,
      "weighted_mae": 0.7156,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 54,
          "mae": 0.5696296296296296,
          "rmse": 0.7127108135167473,
          "mean_error": 0.1695555555555556,
          "median_error": 0.013500000000000012,
          "mean_abs_pct_error": 1.594712962962963
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 54,
          "mae": 0.13012962962962962,
          "rmse": 0.17570993563678033,
          "mean_error": 0.09549999999999997,
          "median_error": 0.1055,
          "mean_abs_pct_error": 0.468574074074074
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 54,
          "mae": 1.0879074074074075,
          "rmse": 1.3170974330504743,
          "mean_error": 0.10601851851851853,
          "median_error": 0.39649999999999996,
          "mean_abs_pct_error": 1.8233070987654316
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 18,
          "mae": 1.7933333333333334,
          "rmse": 2.0078567899352007,
          "mean_error": -0.33222222222222225,
          "median_error": -0.73,
          "mean_abs_pct_error": 0.4491909171075838
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-09-05",
      "matched_pairs": 30,
      "weighted_mae": 1.8597,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 30,
          "mae": 1.8596666666666666,
          "rmse": 2.241941866626638,
          "mean_error": -0.669,
          "median_error": -1.265,
          "mean_abs_pct_error": 0.5508580687830688
        }
      },
      "error": ""
    }
  ],
  "retrain_report": null
}

Validated 4 dates, 637 pairs, mean MAE=1.1804
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-09-14 19:54:56 | INFO     | src.learning.retrain_runner | Filtered to 637 pairs within last 14 days (from 9411)
2026-09-14 19:54:56 | INFO     | src.learning.outcome_retrainer | Ingested 637 prediction-outcome pairs
2026-09-14 19:54:56 | INFO     | src.learning.retrain_runner | Retrain input: 637 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-09-14 19:54:56 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=637, confidence=0.83, MAE=1.008)
{
  "sample_size": 637,
  "confidence": 0.832,
  "weighted_mae": 1.0082244897959185,
  "state_path": "",
  "notes": "[dry-run] Fitted on 637 pairs. Weighted MAE=1.008. Confidence=0.83.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 637 pairs, confidence=0.83, MAE=1.008
```
