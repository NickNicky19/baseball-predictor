#!/usr/bin/env python3
"""
B1 walk-forward RE-ANALYSIS (matched outcomes) — fixes two flaws in
run_walkforward_gbm without any reconstruction:

  Flaw 1: sim and GBM were scored on DIFFERENT outcome sets. Here we INNER-JOIN
          on (player_id, game_date, category), so both models are scored on the
          exact same rows. Spring-training rows the reconstruction included but
          A3 excluded simply drop out of the join.
  Flaw 2: season-blind folds put 2 of 5 folds on international-series openers
          (one game, near-empty features). We tag each date's matched sample
          size so thin/degenerate folds are visible and excluded from the
          headline, instead of silently swinging the aggregate.

Inputs (already on disk from the walk-forward run):
  data/models/gbm/wf_predictions_<model>.csv   (player_id,player_name,game_date,
                                                category,predicted_value,actual_value)
  data/cache/wf_simulator/sim_<date>.json      ({projections:[...], outcomes:[...]})

Output: per-category matched MAE for sim vs each GBM, on the FULL matched pool
and on an ADEQUATE pool (dates with >= --min-rows matched rows, which drops the
one-game opener folds). Pure local; runs in seconds.

Usage:
  python run_wf_reanalyze.py            # defaults to catboost + lightgbm
  python run_wf_reanalyze.py --min-rows 100
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

GATED = ("hits", "hrr", "home_runs", "strikeouts")


def load_sim(cache_dir: Path) -> pd.DataFrame:
    """Flatten cached per-date sim reconstructions into pred+actual rows."""
    rows = []
    files = sorted(cache_dir.glob("sim_*.json"))
    if not files:
        sys.exit(f"No sim cache in {cache_dir} (expected sim_<date>.json files).")
    for f in files:
        payload = json.loads(f.read_text(encoding="utf-8"))
        preds = {(int(p["player_id"]), p["game_date"], p["category"]): float(p["projected_value"])
                 for p in payload.get("projections", [])}
        for o in payload.get("outcomes", []):
            k = (int(o["player_id"]), o["game_date"], o["category"])
            if k in preds:
                rows.append({"player_id": k[0], "game_date": k[1], "category": k[2],
                             "sim_pred": preds[k], "actual": float(o["actual_value"])})
    return pd.DataFrame(rows)


def load_gbm(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns={"predicted_value": "gbm_pred", "actual_value": "actual_gbm"})
    df["player_id"] = df["player_id"].astype(int)
    df["game_date"] = df["game_date"].astype(str)
    return df[["player_id", "game_date", "category", "gbm_pred", "actual_gbm"]]


def matched(sim: pd.DataFrame, gbm: pd.DataFrame) -> pd.DataFrame:
    """Inner-join sim & gbm on identical keys → one row per (player,date,cat)
    with both predictions and a single shared actual."""
    m = sim.merge(gbm, on=["player_id", "game_date", "category"], how="inner")
    # Sanity: the two actuals should agree; if not, trust the sim/reconstruction
    # actual (ground truth from boxscore) and flag disagreement count.
    disagree = int((np.abs(m["actual"] - m["actual_gbm"]) > 1e-6).sum())
    if disagree:
        print(f"  note: {disagree} rows where sim/gbm actuals differ "
              f"(using reconstruction actual as truth).")
    return m


def mae(pred: pd.Series, actual: pd.Series) -> float:
    return float(np.abs(pred.to_numpy(float) - actual.to_numpy(float)).mean())


def report(m: pd.DataFrame, label: str):
    print(f"\n{'='*64}\n{label}  (matched rows: {len(m)})\n{'='*64}")
    print(f"{'category':<12}{'n':>7}{'sim MAE':>10}{'gbm MAE':>10}{'delta':>10}  winner")
    print("-" * 64)
    sim_tot = gbm_tot = n_tot = 0.0
    for cat in GATED:
        c = m[m["category"] == cat]
        if c.empty:
            continue
        s = mae(c["sim_pred"], c["actual"]); g = mae(c["gbm_pred"], c["actual"])
        d = g - s
        w = "GBM" if d < 0 else ("sim" if d > 0 else "tie")
        print(f"{cat:<12}{len(c):>7}{s:>10.4f}{g:>10.4f}{d:>+10.4f}  {w}")
        sim_tot += s * len(c); gbm_tot += g * len(c); n_tot += len(c)
    if n_tot:
        print("-" * 64)
        s = sim_tot / n_tot; g = gbm_tot / n_tot
        print(f"{'weighted':<12}{int(n_tot):>7}{s:>10.4f}{g:>10.4f}{g-s:>+10.4f}  "
              f"{'GBM' if g < s else 'sim'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default="data/models/gbm")
    ap.add_argument("--sim-cache-dir", default="data/cache/wf_simulator")
    ap.add_argument("--models", nargs="+", default=["catboost", "lightgbm"])
    ap.add_argument("--min-rows", type=int, default=100,
                    help="Per-date matched-row floor for the ADEQUATE pool "
                         "(drops one-game opener folds).")
    args = ap.parse_args(argv)

    sim = load_sim(Path(args.sim_cache_dir))
    print(f"Loaded sim predictions: {len(sim)} rows across "
          f"{sim['game_date'].nunique()} dates.")

    # Per-date matched sizes (using catboost as reference for the size map).
    for model in args.models:
        gpath = Path(args.models_dir) / f"wf_predictions_{model}.csv"
        if not gpath.exists():
            print(f"skip {model}: {gpath} missing"); continue
        gbm = load_gbm(gpath)
        m = matched(sim, gbm)
        if m.empty:
            print(f"{model}: no matched rows (key mismatch?)"); continue

        # date-size table -> adequate vs thin
        sizes = m.groupby("game_date").size().sort_index()
        thin = sizes[sizes < args.min_rows]
        adequate_dates = set(sizes[sizes >= args.min_rows].index)

        print(f"\n########## MODEL: {model} ##########")
        print(f"dates: {len(sizes)} | adequate (>= {args.min_rows} rows): "
              f"{len(adequate_dates)} | thin/degenerate: {len(thin)}")
        if len(thin):
            print("  thin dates (excluded from ADEQUATE pool): "
                  + ", ".join(f"{d}({n})" for d, n in thin.items()))

        report(m, f"[{model}] FULL matched pool")
        report(m[m["game_date"].isin(adequate_dates)],
               f"[{model}] ADEQUATE pool (thin opener folds removed)")

    print("\nRead: the ADEQUATE pool is the fair comparison — same outcomes for "
          "both models (flaw 1 fixed) and season-opener one-game folds removed "
          "(flaw 2 fixed). Per-category deltas decide the pick; a split verdict "
          "(GBM some cats, sim others) is a valid per-category promotion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
