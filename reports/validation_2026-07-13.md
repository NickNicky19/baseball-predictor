# Validation report — 2026-07-13

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-07-13 17:29:39 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-07-13 17:29:39 | INFO     | matplotlib.font_manager | generated new fontManager
2026-07-13 17:29:39 | INFO     | src.learning.retrain_runner | Filtered to 5279 pairs within last 14 days (from 5279)
{
  "dates_evaluated": 8,
  "total_matched_pairs": 5121,
  "mean_weighted_mae": 0.8629,
  "results": [
    {
      "game_date": "2026-07-05",
      "matched_pairs": 270,
      "weighted_mae": 1.5157,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hrr": {
          "category": "hrr",
          "n_samples": 270,
          "mae": 1.5156888888888889,
          "rmse": 1.9055248795716804,
          "mean_error": -0.0258222222222222,
          "median_error": 0.578,
          "mean_abs_pct_error": 2.645927081128748
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-06",
      "matched_pairs": 135,
      "weighted_mae": 0.2383,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "home_runs": {
          "category": "home_runs",
          "n_samples": 135,
          "mae": 0.2382962962962963,
          "rmse": 0.4220937231363564,
          "mean_error": -0.05157037037037037,
          "median_error": 0.092,
          "mean_abs_pct_error": 0.49812962962962964
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-07",
      "matched_pairs": 854,
      "weighted_mae": 0.8907,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 274,
          "mae": 0.6921313868613139,
          "rmse": 0.9008332841538528,
          "mean_error": -0.0027883211678832224,
          "median_error": -0.008000000000000007,
          "mean_abs_pct_error": 1.5157483576642334
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 274,
          "mae": 0.19459489051094891,
          "rmse": 0.3391141583726034,
          "mean_error": 0.014215328467153284,
          "median_error": 0.0985,
          "mean_abs_pct_error": 0.5006812652068127
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 274,
          "mae": 1.5618211678832115,
          "rmse": 2.151398183120867,
          "mean_error": -0.22702554744525547,
          "median_error": -0.01749999999999996,
          "mean_abs_pct_error": 2.475634534288168
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 32,
          "mae": 2.8040625,
          "rmse": 3.4547670869394365,
          "mean_error": 0.5634375,
          "median_error": 0.9199999999999999,
          "mean_abs_pct_error": 1.6240563559704184
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-08",
      "matched_pairs": 840,
      "weighted_mae": 0.856,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 270,
          "mae": 0.6628666666666666,
          "rmse": 0.8559550481447284,
          "mean_error": 0.11045925925925926,
          "median_error": 0.024499999999999966,
          "mean_abs_pct_error": 1.657891975308642
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 270,
          "mae": 0.21324814814814816,
          "rmse": 0.3618782432848968,
          "mean_error": -0.003907407407407406,
          "median_error": 0.0995,
          "mean_abs_pct_error": 0.5168203703703704
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 270,
          "mae": 1.5274222222222225,
          "rmse": 1.942380002490263,
          "mean_error": 0.06029629629629627,
          "median_error": 0.663,
          "mean_abs_pct_error": 2.74434573633157
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 30,
          "mae": 2.336666666666667,
          "rmse": 2.861153613492292,
          "mean_error": 0.48800000000000016,
          "median_error": 0.43999999999999995,
          "mean_abs_pct_error": 0.7366122174122175
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-09",
      "matched_pairs": 620,
      "weighted_mae": 0.8689,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 198,
          "mae": 0.6812575757575758,
          "rmse": 0.8464364247904985,
          "mean_error": 0.24269191919191918,
          "median_error": 0.17200000000000004,
          "mean_abs_pct_error": 1.862936026936027
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 198,
          "mae": 0.24428787878787878,
          "rmse": 0.36311795812814257,
          "mean_error": -0.0021565656565656548,
          "median_error": 0.12,
          "mean_abs_pct_error": 0.6029722222222222
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 198,
          "mae": 1.5292171717171719,
          "rmse": 1.8685301129037988,
          "mean_error": 0.25953030303030306,
          "median_error": 0.8095,
          "mean_abs_pct_error": 3.101530044492545
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 26,
          "mae": 2.026153846153846,
          "rmse": 2.633118946157842,
          "mean_error": 0.3692307692307692,
          "median_error": 0.45999999999999996,
          "mean_abs_pct_error": 0.5658828671328672
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-10",
      "matched_pairs": 786,
      "weighted_mae": 0.8837,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 252,
          "mae": 0.7092341269841271,
          "rmse": 0.9017686743636043,
          "mean_error": 0.011853174603174595,
          "median_error": 0.0014999999999999458,
          "mean_abs_pct_error": 1.5800522486772486
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 252,
          "mae": 0.23958730158730157,
          "rmse": 0.37580108086719544,
          "mean_error": -0.05278571428571428,
          "median_error": 0.0905,
          "mean_abs_pct_error": 0.5197896825396826
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 252,
          "mae": 1.501464285714286,
          "rmse": 1.9020006144324948,
          "mean_error": -0.01423412698412699,
          "median_error": 0.5279999999999999,
          "mean_abs_pct_error": 2.6699126181027966
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 30,
          "mae": 2.570333333333333,
          "rmse": 3.258776355218832,
          "mean_error": 1.6283333333333334,
          "median_error": 1.475,
          "mean_abs_pct_error": 3.3215132275132278
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-11",
      "matched_pairs": 776,
      "weighted_mae": 0.8409,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 248,
          "mae": 0.6954677419354839,
          "rmse": 0.8618185105701144,
          "mean_error": 0.22989516129032256,
          "median_error": 0.17049999999999998,
          "mean_abs_pct_error": 1.8888024193548387
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 248,
          "mae": 0.23916532258064518,
          "rmse": 0.3634342965423404,
          "mean_error": 0.01747177419354839,
          "median_error": 0.128,
          "mean_abs_pct_error": 0.6164939516129032
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 248,
          "mae": 1.512802419354839,
          "rmse": 1.775845472198815,
          "mean_error": 0.4679637096774194,
          "median_error": 0.9349999999999999,
          "mean_abs_pct_error": 3.4529870151689708
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 32,
          "mae": 1.4246874999999999,
          "rmse": 1.8894170728031436,
          "mean_error": 0.06843750000000007,
          "median_error": 0.2400000000000002,
          "mean_abs_pct_error": 0.8243457341269842
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-12",
      "matched_pairs": 840,
      "weighted_mae": 0.8093,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 270,
          "mae": 0.6797481481481481,
          "rmse": 0.8396759560158377,
          "mean_error": 0.25685925925925923,
          "median_error": 0.16799999999999993,
          "mean_abs_pct_error": 1.9098234567901238
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 270,
          "mae": 0.21237037037037035,
          "rmse": 0.30654623239014467,
          "mean_error": 0.041962962962962966,
          "median_error": 0.122,
          "mean_abs_pct_error": 0.5938703703703703
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 270,
          "mae": 1.4272296296296296,
          "rmse": 1.7708050585977992,
          "mean_error": 0.33697037037037036,
          "median_error": 0.7474999999999999,
          "mean_abs_pct_error": 3.054795978835979
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 30,
          "mae": 1.787333333333333,
          "rmse": 2.2222661106777166,
          "mean_error": 0.336,
          "median_error": 0.21499999999999986,
          "mean_abs_pct_error": 1.6678603174603175
        }
      },
      "error": ""
    }
  ],
  "retrain_report": null
}

Validated 8 dates, 5121 pairs, mean MAE=0.8629
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-07-13 17:29:41 | INFO     | src.learning.retrain_runner | Filtered to 5279 pairs within last 14 days (from 5279)
2026-07-13 17:29:41 | INFO     | src.learning.outcome_retrainer | Ingested 5279 prediction-outcome pairs
2026-07-13 17:29:41 | INFO     | src.learning.retrain_runner | Retrain input: 5279 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-07-13 17:29:41 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=5279, confidence=0.85, MAE=0.881)
{
  "sample_size": 5279,
  "confidence": 0.853,
  "weighted_mae": 0.8806452605268218,
  "state_path": "",
  "notes": "[dry-run] Fitted on 5279 pairs. Weighted MAE=0.881. Confidence=0.85.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 5279 pairs, confidence=0.85, MAE=0.881
```
