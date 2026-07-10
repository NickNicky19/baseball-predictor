"""
Gate #4(b) harness — "equal or better calibration than the simulator".

Two modes:

  # (1) EMIT the deployed GBM calibrated P(over) for the 2024+ walk-forward
  #     rows at the standard lines. This is HALF the matched join; run it now.
  python run_calibration_gate.py emit \
      --pairs data/models/gbm/wf_predictions_catboost.csv \
      --calibrators data/models/gbm/calibration/calibrators.json \
      --out data/models/gbm/calibration/gbm_deployed_probs.csv

  # (2) COMPARE against the simulator once sim P(over) rows exist.
  python run_calibration_gate.py compare \
      --gbm data/models/gbm/calibration/gbm_deployed_probs.csv \
      --sim data/models/gbm/calibration/sim_probs.csv

WHY THIS IS A SEPARATE STEP (flagged in the B2 chat): the sim cache
(data/cache/wf_simulator/sim_*.json) stores POINT projections only, not
distributions, so it cannot yield P(over). The sim's P(over) comes from its
Monte-Carlo p_ge_threshold, which is captured in the run_slate archives and
in a reconstruction that keeps the simulation block. Because the archives
cover DIFFERENT dates/players than the walk-forward pairs (and the pre-
2026-07-09 ones are hrr-only/version-blank), a gate-clean comparison needs
sim P(over) on the SAME (player_id, game_date, category, line) rows — i.e. a
scoped reconstruction over a representative sample of 2024+ WF dates that
captures p_ge_threshold. Matched inner-join scoring is mandatory (same rule
that the whole B1 verdict rests on).

sim_probs.csv REQUIRED columns:
    player_id, game_date, category, line, sim_p_over
(one row per player/date/category/line; game_date ISO; line half-integer).
"""
from __future__ import annotations
import argparse, json, sys
import numpy as np, pandas as pd
try:
    from src.learning import gbm_calibrator as gc
except ImportError:
    import gbm_calibrator as gc


def _load_pairs(path):
    df = pd.read_csv(path); df["game_date"] = pd.to_datetime(df["game_date"])
    return df[df["game_date"] >= "2024-01-01"].copy()


def emit(args):
    pairs = _load_pairs(args.pairs)
    art = json.load(open(args.calibrators))
    out = []
    for cat, d in art.items():
        cal = gc.CategoryCalibrator.from_dict(d)
        sub = pairs[pairs.category == cat]
        for L in gc.STANDARD_LINES[cat]:
            p = cal.prob_over(sub.predicted_value.values, L)
            out.append(pd.DataFrame(dict(
                player_id=sub.player_id.values,
                game_date=sub.game_date.dt.strftime("%Y-%m-%d").values,
                category=cat, line=L,
                predicted_value=sub.predicted_value.values,
                actual_value=sub.actual_value.values,
                gbm_p_over=p,
                over_outcome=(sub.actual_value.values >= L).astype(int),
            )))
    res = pd.concat(out, ignore_index=True)
    res.to_csv(args.out, index=False)
    print(f"wrote {len(res)} deployed-GBM P(over) rows -> {args.out}")
    print(res.groupby('category').size().to_string())


def _metrics(p, y):
    return dict(n=int(len(y)), base_rate=round(float(np.mean(y)), 4),
                brier=round(gc.brier(p, y), 4),
                log_loss=round(gc.log_loss(p, y), 4),
                ece=round(gc.ece(p, y, 10), 4))


def compare(args):
    gbm = pd.read_csv(args.gbm)
    sim = pd.read_csv(args.sim)
    key = ["player_id", "game_date", "category", "line"]
    for c in key:
        if c not in sim.columns:
            sys.exit(f"sim file missing required column: {c}")
    m = gbm.merge(sim[key + ["sim_p_over"]], on=key, how="inner")
    if len(m) == 0:
        sys.exit("no matched rows — check keys (player_id/game_date/category/line).")
    print(f"matched rows: {len(m)}  (gbm={len(gbm)}, sim={len(sim)})\n")

    verdict_pass = True
    print(f"{'category':11s} {'line':>4s} {'n':>5s}  "
          f"{'GBM brier':>9s} {'sim brier':>9s}  {'GBM ll':>7s} {'sim ll':>7s}  winner")
    for (cat, L), g in m.groupby(["category", "line"]):
        y = g.over_outcome.values
        gm, sm = _metrics(g.gbm_p_over.values, y), _metrics(g.sim_p_over.values, y)
        better = gm["brier"] <= sm["brier"] and gm["log_loss"] <= sm["log_loss"]
        verdict_pass &= (gm["brier"] <= sm["brier"] + 1e-4)  # gate: equal-or-better Brier
        print(f"{cat:11s} {L:>4} {gm['n']:>5d}  {gm['brier']:>9.4f} {sm['brier']:>9.4f}  "
              f"{gm['log_loss']:>7.4f} {sm['log_loss']:>7.4f}  {'GBM' if better else 'sim'}")
    print("\nGATE #4(b): calibration equal-or-better than simulator ->",
          "PASS" if verdict_pass else "FAIL (sim calibrates better on >=1 category/line)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("emit"); e.add_argument("--pairs", required=True)
    e.add_argument("--calibrators", required=True); e.add_argument("--out", required=True)
    c = sub.add_parser("compare"); c.add_argument("--gbm", required=True); c.add_argument("--sim", required=True)
    args = ap.parse_args()
    (emit if args.cmd == "emit" else compare)(args)
