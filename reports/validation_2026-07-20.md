# Validation report — 2026-07-20

Lookback: 14 days. Corrections OFF (read-only).

## Per-category validation (from pairs)
```
2026-07-20 16:54:55 | INFO     | matplotlib.font_manager | Failed to extract font properties from /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf: Non-scalable fonts are not supported
2026-07-20 16:54:55 | INFO     | matplotlib.font_manager | generated new fontManager
2026-07-20 16:54:56 | INFO     | src.learning.retrain_runner | Filtered to 7143 pairs within last 14 days (from 7413)
{
  "dates_evaluated": 12,
  "total_matched_pairs": 6964,
  "mean_weighted_mae": 0.8264,
  "results": [
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
    },
    {
      "game_date": "2026-07-14",
      "matched_pairs": 56,
      "weighted_mae": 0.9506,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 18,
          "mae": 0.6861666666666667,
          "rmse": 0.7815461527567575,
          "mean_error": 0.6833888888888889,
          "median_error": 0.8125,
          "mean_abs_pct_error": 2.6275
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 18,
          "mae": 0.21138888888888888,
          "rmse": 0.22220373534214047,
          "mean_error": 0.21138888888888888,
          "median_error": 0.2005,
          "mean_abs_pct_error": 0.8455555555555555
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 18,
          "mae": 1.5172222222222222,
          "rmse": 1.6434013508574221,
          "mean_error": 1.4037777777777778,
          "median_error": 1.705,
          "mean_abs_pct_error": 5.142296296296297
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 2,
          "mae": 4.885,
          "rmse": 4.885002558852963,
          "mean_error": 4.885,
          "median_error": 4.885,
          "mean_abs_pct_error": 2.035833333333333
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-16",
      "matched_pairs": 56,
      "weighted_mae": 0.896,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 18,
          "mae": 0.6812777777777778,
          "rmse": 0.8027887850071982,
          "mean_error": 0.3899444444444444,
          "median_error": 0.492,
          "mean_abs_pct_error": 2.0917499999999998
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 18,
          "mae": 0.33344444444444443,
          "rmse": 0.5442195227009858,
          "mean_error": -0.06377777777777778,
          "median_error": 0.151,
          "mean_abs_pct_error": 0.6867777777777777
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 18,
          "mae": 1.6221666666666668,
          "rmse": 1.8359441863944437,
          "mean_error": 0.8012777777777776,
          "median_error": 1.3175,
          "mean_abs_pct_error": 3.798324074074074
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 2,
          "mae": 1.355,
          "rmse": 1.468076973458817,
          "mean_error": -1.355,
          "median_error": -1.355,
          "mean_abs_pct_error": 0.20297619047619048
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-17",
      "matched_pairs": 462,
      "weighted_mae": 0.9321,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 144,
          "mae": 0.6880069444444445,
          "rmse": 0.8495289013133495,
          "mean_error": 0.1965763888888889,
          "median_error": 0.139,
          "mean_abs_pct_error": 1.8094907407407408
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 144,
          "mae": 0.2450486111111111,
          "rmse": 0.3660150175273626,
          "mean_error": 0.009645833333333334,
          "median_error": 0.1235,
          "mean_abs_pct_error": 0.6206666666666667
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 144,
          "mae": 1.6266319444444446,
          "rmse": 2.025739232354451,
          "mean_error": 0.18545138888888887,
          "median_error": 0.8065,
          "mean_abs_pct_error": 3.075599222883598
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 30,
          "mae": 2.0683333333333334,
          "rmse": 2.5631822668966273,
          "mean_error": -0.5016666666666668,
          "median_error": -0.5950000000000002,
          "mean_abs_pct_error": 1.2603558201058198
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-18",
      "matched_pairs": 698,
      "weighted_mae": 0.8498,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 223,
          "mae": 0.6849372197309417,
          "rmse": 0.8566018037712828,
          "mean_error": 0.19508968609865468,
          "median_error": 0.121,
          "mean_abs_pct_error": 1.800509715994021
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 223,
          "mae": 0.23222421524663675,
          "rmse": 0.3602734512023549,
          "mean_error": 0.0387354260089686,
          "median_error": 0.134,
          "mean_abs_pct_error": 0.6262937219730942
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 223,
          "mae": 1.5275739910313901,
          "rmse": 1.8577229480117605,
          "mean_error": 0.2801838565022422,
          "median_error": 0.8340000000000001,
          "mean_abs_pct_error": 3.098598761477685
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 29,
          "mae": 1.6555172413793102,
          "rmse": 2.185763497948833,
          "mean_error": -0.9017241379310343,
          "median_error": -0.54,
          "mean_abs_pct_error": 0.3160790914066776
        }
      },
      "error": ""
    },
    {
      "game_date": "2026-07-19",
      "matched_pairs": 841,
      "weighted_mae": 0.9005,
      "value_plays": 0,
      "corrections_applied": false,
      "metrics_by_category": {
        "hits": {
          "category": "hits",
          "n_samples": 270,
          "mae": 0.7189074074074074,
          "rmse": 0.9207738552937917,
          "mean_error": 0.0903888888888889,
          "median_error": 0.12350000000000005,
          "mean_abs_pct_error": 1.6821867283950618
        },
        "home_runs": {
          "category": "home_runs",
          "n_samples": 270,
          "mae": 0.25502962962962966,
          "rmse": 0.43050638572284633,
          "mean_error": -0.0027259259259259233,
          "median_error": 0.123,
          "mean_abs_pct_error": 0.6093228395061729
        },
        "hrr": {
          "category": "hrr",
          "n_samples": 270,
          "mae": 1.6152481481481482,
          "rmse": 2.13509353511824,
          "mean_error": 0.06800370370370372,
          "median_error": 0.6975,
          "mean_abs_pct_error": 2.8184685716957936
        },
        "strikeouts": {
          "category": "strikeouts",
          "n_samples": 31,
          "mae": 1.8774193548387097,
          "rmse": 2.2270608433538586,
          "mean_error": 0.08838709677419358,
          "median_error": 0.2699999999999996,
          "mean_abs_pct_error": 0.6091959805427547
        }
      },
      "error": ""
    }
  ],
  "retrain_report": null
}

Validated 12 dates, 6964 pairs, mean MAE=0.8264
Report saved to data/learning/validation_report.json
```

## Retrain dry-run (what corrections WOULD be — not applied)
```
2026-07-20 16:54:58 | INFO     | src.learning.retrain_runner | Filtered to 7143 pairs within last 14 days (from 7413)
2026-07-20 16:54:58 | INFO     | src.learning.outcome_retrainer | Ingested 7143 prediction-outcome pairs
2026-07-20 16:54:58 | INFO     | src.learning.retrain_runner | Retrain input: 7143 pairs ingested (required=25, lookback=14 days, file=prediction_outcomes.csv)
2026-07-20 16:54:58 | INFO     | src.learning.retrain_runner | Dry run: would save state to /home/runner/work/baseball-predictor/baseball-predictor/data/learning/bias_corrections.json (samples=7143, confidence=0.86, MAE=0.860)
{
  "sample_size": 7143,
  "confidence": 0.857,
  "weighted_mae": 0.859670783645656,
  "state_path": "",
  "notes": "[dry-run] Fitted on 7143 pairs. Weighted MAE=0.860. Confidence=0.86.",
  "skipped": false,
  "skip_reason": ""
}

Retrain complete: 7143 pairs, confidence=0.86, MAE=0.860
```
