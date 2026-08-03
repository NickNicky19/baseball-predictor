# Validation report — 2026-08-03

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-08-03 17:24:52 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-08-03 17:24:52 | INFO     | matplotlib.font_manager | generated new fontManager
2026-08-03 17:24:52 | INFO     | src.learning.retrain_runner | Filtered to 1065 pairs within last 14 days (from 8478)
2026-08-03 17:24:52 | WARNING  | src.evaluation.pipeline_validator | Validation failed for 2026-07-20: cannot convert float NaN to integer
{
  "dates_evaluated": 6,
  "total_matched_pairs": 468,
  "mean_weighted_mae": 1.4393,
  "results": [
    {
      "game_date": "2026-07-20",
      "matched_pairs": 0,
      "weighted_mae": 0.0,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {},
      "error": "cannot convert float NaN to integer"
    },
    {
      "game_date": "2026-07-23",
      "matched_pairs": 280,
      "weighted_mae": 0.8019,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 90,
          "mae": 0.6748888888888889,
          "rmse": 0.8452164614266967,
          "mean_error": 0.009911111111111107,
          "median_error": -0.03700000000000003,
          "mean_abs_pct_error": 1.5340814814814816
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 90,
          "mae": 0.19904444444444447,
          "rmse": 0.3060172108449676,
          "mean_error": 0.022088888888888887,
          "median_error": 0.107,
          "mean_abs_pct_error": 0.5307444444444445
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 90,
          "mae": 1.434788888888889,
          "rmse": 1.7826721733896498,
          "mean_error": -0.01083333333333332,
          "median_error": 0.4215,
          "mean_abs_pct_error": 2.6831927645502645
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 10,
          "mae": 1.6740000000000002,
          "rmse": 2.172592920912705,
          "mean_error": -0.14600000000000005,
          "median_error": 0.8700000000000001,
          "mean_abs_pct_error": 0.5446030303030304
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-24",
      "matched_pairs": 28,
      "weighted_mae": 2.2329,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 28,
          "mae": 2.2328571428571427,
          "rmse": 2.8975790387346274,
          "mean_error": 0.2107142857142858,
          "median_error": 0.52,
          "mean_abs_pct_error": 2.577516865079365
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-25",
      "matched_pairs": 28,
      "weighted_mae": 1.865,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 28,
          "mae": 1.8650000000000002,
          "rmse": 2.3428889370664954,
          "mean_error": -0.05000000000000001,
          "median_error": 0.030000000000000027,
          "mean_abs_pct_error": 0.44897375541125545
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-26",
      "matched_pairs": 111,
      "weighted_mae": 1.0549,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 27,
          "mae": 0.6294444444444445,
          "rmse": 0.7343203206594063,
          "mean_error": 0.42499999999999993,
          "median_error": 0.74,
          "mean_abs_pct_error": 2.159074074074074
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 27,
          "mae": 0.1834074074074074,
          "rmse": 0.276618335805318,
          "mean_error": 0.050666666666666665,
          "median_error": 0.109,
          "mean_abs_pct_error": 0.5345185185185185
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 27,
          "mae": 1.4177777777777778,
          "rmse": 1.5384599562669927,
          "mean_error": 0.7324444444444445,
          "median_error": 1.313,
          "mean_abs_pct_error": 3.819816666666666
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 30,
          "mae": 1.8956666666666666,
          "rmse": 2.333371666351791,
          "mean_error": 0.32966666666666666,
          "median_error": 0.6750000000000003,
          "mean_abs_pct_error": 1.150631746031746
        }
      },
      "error": ""
    },
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
    }
  ],
  "retrain_report": null
}

Validated 6 dates, 468 pairs, mean MAE=1.4393
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-08-03 17:24:54 | INFO     | src.learning.retrain_runner | Filtered to 1065 pairs within last 14 days (from 8478)
Traceback (most recent call last):
  File "/home/runner/work/baseball-predictor/baseball-predictor/run_retrain.py", line 115, in <module>
    sys.exit(main())
             ^^^^^^
  File "/home/runner/work/baseball-predictor/baseball-predictor/run_retrain.py", line 81, in main
    report = runner.run(
             ^^^^^^^^^^^
  File "/home/runner/work/baseball-predictor/baseball-predictor/src/learning/retrain_runner.py", line 137, in run
    ingested = self.retrainer.ingest_from_dataframe(df)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/runner/work/baseball-predictor/baseball-predictor/src/learning/outcome_retrainer.py", line 166, in ingest_from_dataframe
    mlb_game_pk=int(row["mlb_game_pk"]),
                ^^^^^^^^^^^^^^^^^^^^^^^
ValueError: cannot convert float NaN to integer
```
