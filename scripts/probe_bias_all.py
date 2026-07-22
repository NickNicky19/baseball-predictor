"""
Is the LIVE model systematically overconfident across the board?

The hrr bias probe found the frozen (= live, 43a43880e377) model saying
P(hrr>=2) = 48.8% when the actual base rate is 42.0% -- a +6.8pp bias that has
nothing to do with the HRR fix and was invisible under an undecomposed Brier.

Question: is it hrr-only, or is the whole model hot?

If hits and home_runs are also biased high, that is a GLOBAL calibration failure
and it dominates every other open item -- including the market comparison, which
a systematically overconfident model will lose to the close by construction.
"""
import math
import pandas as pd

f = pd.read_csv(r"data\analysis\hrr\gate_frozen.csv")
p = pd.read_csv(r"data\models\gbm\wf_predictions_catboost.csv", low_memory=False)

print("LIVE MODEL (frozen, 43a43880e377) -- calibration bias by category/line")
print(f"{'category':12s} {'line':>5s} {'n':>6s} {'mean p':>8s} {'actual':>8s} {'bias':>9s}")
print("-" * 56)
for cat in ("hits", "home_runs", "hrr", "strikeouts"):
    a = p[p.category == cat][["player_id", "game_date", "actual_value"]].drop_duplicates()
    for line in sorted(f[f.category == cat].line.unique()):
        d = f[(f.category == cat) & (f.line == line)].merge(
            a, on=["player_id", "game_date"])
        if not len(d):
            continue
        over = (d.actual_value >= math.ceil(line)).astype(float)
        bias = d.sim_p_over.mean() - over.mean()
        flag = "  <-- HOT" if bias > 0.03 else ("  <-- COLD" if bias < -0.03 else "")
        print(f"{cat:12s} {line:5.1f} {len(d):6d} {d.sim_p_over.mean():8.4f} "
              f"{over.mean():8.4f} {bias:+9.4f}{flag}")
    print()
