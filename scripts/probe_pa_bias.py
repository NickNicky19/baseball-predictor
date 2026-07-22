"""
Is the +11.5pp P(hits>=1) bias concentrated in LOW-PA games?

The dispersion diagnosis was WRONG (phi=1668 was a broken statistic -- mu is
built from PROJECTED pa, so at pa=1 the model carries a 0.67-hit expectation
into one trial and the Pearson term explodes). But the per-PA strata exposed
the real pattern: at pa=4 the model is nearly perfect (phi 1.03, zero-excess
+1.1pp). The bias lives in the tails of the PA distribution.

Two hypotheses:
  H1 PA-PROJECTION BIAS: expected_pa is systematically higher than out_pa. The
     model projects ~4.3 PA, hitters average less (early exits, blowouts,
     pinch-hits), so every projection is scaled to too many trials.
  H2 UNAVOIDABLE: PA is genuinely unpredictable and the model cannot know a
     hitter will be pulled. The bias is irreducible without in-game information.

H1 is FIXABLE (recalibrate expected_pa). H2 is not, and would mean the model is
already near the achievable limit and the +11.5pp is the price of not knowing
the future.
"""
import math
import pandas as pd

wf = pd.read_csv(r"data\models\gbm\wf_predictions_catboost.csv", low_memory=False)
tr = pd.read_csv(r"data\training\training_hitters_2023_2026.csv.gz", low_memory=False)

h = wf[wf.category == "hits"].merge(
    tr[["player_id", "game_date", "out_pa"]].drop_duplicates(),
    on=["player_id", "game_date"], how="inner")
h = h.dropna(subset=["out_pa"])
h = h[h.out_pa > 0]

print(f"n = {len(h):,}\n")
print("P(hits >= 1): predicted vs actual, BY ACTUAL PA")
print(f"{'pa':>3s} {'n':>6s} {'pred p(>=1)':>12s} {'act p(>=1)':>11s} {'bias':>8s}")
print("-" * 46)
for pa in sorted(h.out_pa.unique()):
    s = h[h.out_pa == pa]
    if len(s) < 40:
        continue
    # simulator's implied P(>=1) under its own binomial, using PROJECTED rate
    p = (s.predicted_value / pa).clip(0, 1)
    pred_ge1 = (1 - (1 - p) ** pa).mean()
    act_ge1 = (s.actual_value >= 1).mean()
    print(f"{int(pa):3d} {len(s):6d} {pred_ge1:12.4f} {act_ge1:11.4f} "
          f"{pred_ge1 - act_ge1:+8.4f}")

print()
print("PA distribution: what the model assumes vs what happens")
print(f"  mean out_pa (actual): {h.out_pa.mean():.3f}")
print(f"  out_pa distribution:")
print(h.out_pa.value_counts().sort_index().to_string())
print()
print(f"  share of rows with pa <= 3: {(h.out_pa <= 3).mean():.1%}")
print(f"  those rows' actual P(>=1): {(h[h.out_pa<=3].actual_value >= 1).mean():.4f}")
print(f"  pa >= 4 rows' actual P(>=1): {(h[h.out_pa>=4].actual_value >= 1).mean():.4f}")
