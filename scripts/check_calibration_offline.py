"""
Offline self-check for the B2 calibration layer. Pure-local, seconds.
Mirrors scripts/check_gbm_offline.py discipline. Validates the SHIPPED
artifact (calibrators.json) for structure/coherence/round-trip, and
reproduces the validation-split holdout ECE gate per category.

    python check_calibration_offline.py --pairs wf_predictions_catboost.csv \
        --calibrators calibration_out/calibrators.json
"""
from __future__ import annotations
import argparse, json, os, tempfile
import numpy as np, pandas as pd
try:
    from src.learning import gbm_calibrator as gc
except ImportError:
    import gbm_calibrator as gc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="wf_predictions_catboost.csv")
    ap.add_argument("--calibrators", default="calibration_out/calibrators.json")
    a = ap.parse_args()
    assert os.path.exists(a.calibrators), \
        f"missing {a.calibrators} — run run_calibrate_gbm.py first"
    art = json.load(open(a.calibrators))

    df = pd.read_csv(a.pairs); df["game_date"] = pd.to_datetime(df["game_date"])
    df = df[df.game_date >= "2024-01-01"]
    tr, va = df[df.game_date < "2025-01-01"], df[df.game_date >= "2025-01-01"]
    n = 0
    def ok(cond, msg):
        nonlocal n
        assert cond, "FAIL: " + msg
        n += 1; print(f"  [{n:2d}] ok: {msg}")

    ok(len(df) == 10053, "2024+ filter yields 10053 rows")
    ok(set(df.category) == {"hits", "hrr", "home_runs", "strikeouts"}, "four categories present")
    ok(np.allclose(df.actual_value, df.actual_value.round()), "actuals are integer counts")
    ok(set(art) == set(gc.STANDARD_LINES), "artifact has all four categories")

    for cat in ["hits", "hrr", "home_runs", "strikeouts"]:
        cal = gc.CategoryCalibrator.from_dict(art[cat])
        lines = gc.STANDARD_LINES[cat]
        ok(cal.family == gc.COUNT_FAMILY[cat], f"{cat} family == {gc.COUNT_FAMILY[cat]}")
        # coherence on the SHIPPED artifact
        grid = np.linspace(0.05, 8 if cat == "strikeouts" else 1.2, 30)
        p0 = cal.prob_over(grid, lines[0])
        ok(np.all(np.diff(p0) >= -1e-9), f"{cat} P(over) increases with prediction")
        if len(lines) > 1:
            xs = np.linspace(0.5, 6, 5)
            P = np.array([[float(cal.prob_over(x, L)) for L in lines] for x in xs])
            ok(np.all(np.diff(P, axis=1) <= 1e-9), f"{cat} P(over) decreases with line")
        allp = np.concatenate([cal.prob_over(grid, L) for L in lines])
        ok(np.all((allp > 0) & (allp < 1)), f"{cat} probabilities strictly in (0,1)")

    # betting-focus bias corrections behaved as validated
    ok(gc.CategoryCalibrator.from_dict(art["home_runs"]).mean_scale < 0.99,
       "home_runs carries a mean recalibration (<1; corrects hot HR rate)")
    ok(abs(gc.CategoryCalibrator.from_dict(art["strikeouts"]).mean_scale - 1.0) < 1e-9,
       "strikeouts mean_scale == 1 (thin-train scale correctly not deployed)")

    # reproduce the holdout-ECE gate per category using the artifact's config
    for cat in ["hits", "hrr", "home_runs", "strikeouts"]:
        cfg = gc.CategoryCalibrator.from_dict(art[cat])
        ms = None if abs(cfg.mean_scale - 1.0) > 1e-9 else 1.0   # re-fit c on train if used
        cal = gc.fit_category(cat, tr[tr.category == cat].predicted_value.values,
                              tr[tr.category == cat].actual_value.values,
                              gc.STANDARD_LINES[cat], cfg.deployed_stage2, mean_scale=ms)
        vc = va[va.category == cat]; P, Y = [], []
        for L in gc.STANDARD_LINES[cat]:
            P.append(cal.prob_over(vc.predicted_value.values, L))
            Y.append((vc.actual_value.values >= L).astype(float))
        e = gc.ece(np.concatenate(P), np.concatenate(Y), 8)
        ok(e < 0.05, f"{cat} validation-split holdout ECE {e:.4f} < 0.05")

    # artifact round-trips exactly
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.json"); json.dump(art, open(p, "w"))
        rt = {c: gc.CategoryCalibrator.from_dict(v) for c, v in json.load(open(p)).items()}
        same = all(abs(float(rt[c].prob_over(0.7, gc.STANDARD_LINES[c][0]))
                       - float(gc.CategoryCalibrator.from_dict(art[c]).prob_over(0.7, gc.STANDARD_LINES[c][0]))) < 1e-12
                   for c in art)
        ok(same, "artifact round-trips (save/load reproduces P(over))")

    print(f"\n{n}/{n} checks passed.")


if __name__ == "__main__":
    main()
