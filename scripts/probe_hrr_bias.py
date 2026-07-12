"""
Is the candidate's hrr systematically HOT?

The gate says FROZEN wins on hrr 1.5 (dbrier +0.00122, CI excludes 0). Two
competing explanations:

  (A) p_score_from_base was fitted against the BROKEN sampling. The structure is
      now fixed but the constants are not re-fit, so the candidate runs hot --
      and a hot p(over) against a 0.42 base rate loses Brier. This is Step 5 of
      the original plan, never run.

  (B) The fix genuinely degrades the distribution, and no re-fit saves it.

(A) predicts: candidate's mean p(over) is meaningfully ABOVE the actual base
rate, while frozen's is not. (B) predicts: both are near zero bias, and the
loss is in the SHAPE, not the level.

One query, falsifiable, distinguishes them.
"""
import pandas as pd

f = pd.read_csv(r"data\analysis\hrr\gate_frozen.csv")
c = pd.read_csv(r"data\analysis\hrr\gate_cand.csv")
p = pd.read_csv(r"data\models\gbm\wf_predictions_catboost.csv", low_memory=False)
p = p[p.category == "hrr"][["player_id", "game_date", "actual_value"]].drop_duplicates()

print(f"{'arm':10s} {'line':>5s} {'n':>6s} {'mean p(over)':>13s} {'base rate':>10s} {'bias':>9s}")
print("-" * 60)
for line, k in ((1.5, 2), (2.5, 3)):
    for name, d in (("frozen", f), ("candidate", c)):
        d = d[(d.category == "hrr") & (d.line == line)].merge(
            p, on=["player_id", "game_date"])
        over = (d.actual_value >= k).astype(float)
        bias = d.sim_p_over.mean() - over.mean()
        print(f"{name:10s} {line:5.1f} {len(d):6d} {d.sim_p_over.mean():13.4f} "
              f"{over.mean():10.4f} {bias:+9.4f}")
    print()
