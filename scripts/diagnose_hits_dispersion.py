#!/usr/bin/env python3
"""
Diagnose the +11.5pp overconfidence on `hits >= 1`.

THE FINDING THIS INVESTIGATES
  The LIVE model (43a43880e377) says P(hits >= 1) = 0.678. Reality: 0.563.
  But the MEAN is nearly perfect (pred 0.8509 vs actual 0.8381, +0.0128).
  A right mean with a badly wrong P(>=1) means the SHAPE is wrong, not the level:
  the simulator is not producing enough ZERO-HIT GAMES.

  Actual zeros: 2132/4979 = 42.8%.  Simulator: ~32%.

THE HYPOTHESIS
  HybridPASimulator draws each PA independently from a fixed per-PA probability.
  That is a BINOMIAL. Real hitters are OVERDISPERSED relative to binomial --
  a hitter's effective rate varies game to game (that day's stuff, matchup,
  park, weather, health), so reality produces MORE zeros AND more big games than
  a binomial with the same mean. Under-produce zeros, and P(>=1) is inflated.

  Sanity arithmetic: mean 0.84 hits over ~4.1 PA -> p ~ 0.205/PA.
  Binomial P(0) = (1-0.205)^4.1 ~= 0.325.   Actual: 0.428.  That is the gap.

THE CONFOUND THIS CONTROLS FOR  (rule 3: the metric must be able to SEE the bug,
and must not fire on things that are NOT the bug)
  A naive var/mean test CANNOT distinguish:
    (a) genuine within-game overdispersion   <- a simulator bug
    (b) mere TALENT HETEROGENEITY across hitters -- pooling a .150 hitter with a
        .260 hitter inflates variance even if each is perfectly binomial within
        a game. The simulator ALREADY models this (each hitter gets his own
        rates), so it is NOT a bug.
  Validated on synthetic data with known ground truth (see docstring of
  _pearson_dispersion): a pooled var/mean ratio reports 1.02-1.06 on the pure
  talent-heterogeneity world -- i.e. it would FALSELY flag a correct simulator.

  The PEARSON DISPERSION statistic conditions on each row's OWN model prediction,
  which absorbs talent by construction:
        phi = mean[ (y - mu)^2 / (pa * p * (1-p)) ],   p = mu / pa
  Measured on synthetic worlds:
        binomial (no bug)         phi = 1.0022
        talent heterogeneity      phi = 0.9960   <- correctly NOT flagged
        true overdispersion       phi = 1.1428   <- correctly flagged

WHAT phi MEANS
  phi ~ 1.0  -> the binomial variance is right; the zero-deficit is NOT a
                dispersion problem and this diagnosis is WRONG. Look elsewhere
                (e.g. the mean model is right on average but wrong per-hitter).
  phi > 1.0  -> genuine overdispersion. The simulator needs per-game rate
                variance (Beta-Binomial / a per-game multiplier). The
                overdispersion parameter is then FITTABLE from these pairs --
                a real fit, not a structural placeholder (rule 2).

NOTE: gbm_calibrator.py ALREADY knows this. It fits `nbinom` for hrr because it
MEASURED a conditional var/mean of ~2.2. The calibration layer modelled the
overdispersion; the simulator never did.

Usage:
    python scripts/diagnose_hits_dispersion.py
    python scripts/diagnose_hits_dispersion.py --pairs data/models/gbm/wf_predictions_catboost.csv
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _pearson_dispersion(y: np.ndarray, mu: np.ndarray, pa: np.ndarray) -> tuple[float, int]:
    """phi = mean[(y-mu)^2 / Var_binom(mu)]. Immune to talent heterogeneity
    because mu is the model's OWN per-game prediction for THAT hitter."""
    p = np.clip(mu / pa, 1e-6, 1 - 1e-6)
    v = pa * p * (1.0 - p)
    ok = v > 0
    return float(np.mean((y[ok] - mu[ok]) ** 2 / v[ok])), int(ok.sum())


def _zero_analysis(y: np.ndarray, mu: np.ndarray, pa: np.ndarray) -> dict:
    """Actual P(0 hits) vs the binomial P(0) implied by each row's OWN prediction."""
    p = np.clip(mu / pa, 1e-9, 1 - 1e-9)
    p_zero_binom = (1.0 - p) ** pa
    return {
        "actual": float((y == 0).mean()),
        "binomial_predicted": float(p_zero_binom.mean()),
        "excess": float((y == 0).mean() - p_zero_binom.mean()),
    }


def _block_bootstrap_phi(y, mu, pa, dates, b=2000, seed=7) -> tuple[float, float]:
    """CI on phi, resampling DATES (rows within a slate are correlated)."""
    uniq, inv = np.unique(dates, return_inverse=True)
    idx_by_date = [np.where(inv == i)[0] for i in range(len(uniq))]
    rng = np.random.default_rng(seed)
    reps = np.empty(b)
    for i in range(b):
        pick = rng.integers(0, len(uniq), size=len(uniq))
        sel = np.concatenate([idx_by_date[j] for j in pick])
        reps[i], _ = _pearson_dispersion(y[sel], mu[sel], pa[sel])
    lo, hi = np.percentile(reps, [2.5, 97.5])
    return float(lo), float(hi)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="data/models/gbm/wf_predictions_catboost.csv")
    ap.add_argument("--outcomes", default="data/training/training_hitters_2023_2026.csv.gz",
                    help="source of actual_pa (out_pa); must cover the pairs' dates")
    ap.add_argument("--category", default="hits")
    ap.add_argument("--b", type=int, default=2000)
    args = ap.parse_args(argv)

    df = pd.read_csv(args.pairs, low_memory=False)
    df = df[df.category == args.category].copy()
    if df.empty:
        print(f"no rows for category={args.category}", file=sys.stderr)
        return 2

    # actual_pa is REQUIRED -- the binomial variance is pa*p*(1-p). Without the
    # number of trials the dispersion statistic is undefined.
    #
    # SOURCE NOTE: prediction_outcomes.csv HAS actual_pa but is the LIVE FORWARD
    # LOG (2026-07-05..07-10 only) -- ZERO date overlap with the 2023-2025 walk-
    # forward pairs (measured: 0/4979 rows matched). The training set is the
    # right source: `out_pa` is the actual box-score plate appearance count and
    # it spans 2023-2026.
    #
    # DO NOT substitute expected_pa. Projected-vs-actual PA slippage (projected
    # 4.3, got 3) is variance that has NOTHING to do with the hitter's rate, and
    # it would INFLATE phi -- manufacturing the very conclusion under test.
    if "actual_pa" not in df.columns or df["actual_pa"].isna().all():
        out = Path(args.outcomes)
        if not out.exists():
            print(f"FATAL: no actual_pa in pairs and {out} does not exist.\n"
                  f"  The binomial variance needs the trial count. Do not proceed.",
                  file=sys.stderr)
            return 2
        o = pd.read_csv(out, low_memory=False)
        if "out_pa" not in o.columns:
            print(f"FATAL: {out} has no out_pa column. Available: {list(o.columns)[:20]}",
                  file=sys.stderr)
            return 2
        o = (o[["player_id", "game_date", "out_pa"]]
             .rename(columns={"out_pa": "actual_pa"})
             .dropna(subset=["actual_pa"])
             .drop_duplicates(subset=["player_id", "game_date"]))
        n0 = len(df)
        df = df.merge(o, on=["player_id", "game_date"], how="inner")
        rate = len(df) / n0 if n0 else 0.0
        print(f"[join] actual_pa (out_pa) from {out}: {len(df)}/{n0} matched ({rate:.1%})")
        if rate < 0.80:
            print(f"FATAL: only {rate:.1%} of pairs matched a PA. A partial join is a\n"
                  f"  BIASED SUBSAMPLE, and a biased subsample gives a biased phi.\n"
                  f"  Fix the join before trusting any dispersion number.",
                  file=sys.stderr)
            return 2

    df = df.dropna(subset=["predicted_value", "actual_value", "actual_pa"])
    df = df[df.actual_pa > 0]
    if df.empty:
        print("no usable rows after requiring actual_pa > 0", file=sys.stderr)
        return 2

    y = df["actual_value"].to_numpy(float)
    mu = df["predicted_value"].to_numpy(float)
    pa = df["actual_pa"].to_numpy(float)
    dates = df["game_date"].to_numpy()

    print("=" * 72)
    print(f"DISPERSION DIAGNOSIS -- category '{args.category}'  (n = {len(df):,})")
    print("=" * 72)

    print(f"\nMEAN (is the level right?)")
    print(f"  mean predicted : {mu.mean():.4f}")
    print(f"  mean actual    : {y.mean():.4f}")
    print(f"  mean error     : {mu.mean() - y.mean():+.4f}")
    print(f"  mean PA        : {pa.mean():.2f}")

    z = _zero_analysis(y, mu, pa)
    print(f"\nSHAPE (is the simulator producing enough ZEROS?)")
    print(f"  actual  P(0 {args.category})       : {z['actual']:.4f}")
    print(f"  binomial-implied P(0)     : {z['binomial_predicted']:.4f}")
    print(f"  EXCESS ZEROS              : {z['excess']:+.4f}")
    print(f"  -> a binomial simulator would produce {z['binomial_predicted']:.1%} zeros;")
    print(f"     reality produces {z['actual']:.1%}. That deficit inflates P(>=1).")

    phi, n_used = _pearson_dispersion(y, mu, pa)
    lo, hi = _block_bootstrap_phi(y, mu, pa, dates, b=args.b)
    print(f"\nPEARSON DISPERSION  phi  (talent-heterogeneity is CONTROLLED FOR)")
    print(f"  phi = {phi:.4f}   95% CI [{lo:.4f}, {hi:.4f}]   (n = {n_used:,}, "
          f"block bootstrap over {len(np.unique(dates))} dates)")
    print(f"  reference: binomial = 1.0022, pure talent spread = 0.9960,")
    print(f"             genuine overdispersion = 1.1428  (synthetic, known truth)")

    print(f"\nPER-PA STRATA (the bug should appear at EVERY pa, not just one)")
    print(f"  {'pa':>3s} {'n':>6s} {'mean_y':>7s} {'mean_mu':>8s} {'phi':>7s} "
          f"{'P0_act':>7s} {'P0_bin':>7s} {'excess':>8s}")
    for k in sorted(df.actual_pa.unique()):
        s = df[df.actual_pa == k]
        if len(s) < 50:
            continue
        ys = s["actual_value"].to_numpy(float)
        ms = s["predicted_value"].to_numpy(float)
        ps = s["actual_pa"].to_numpy(float)
        ph, _ = _pearson_dispersion(ys, ms, ps)
        zz = _zero_analysis(ys, ms, ps)
        print(f"  {int(k):3d} {len(s):6d} {ys.mean():7.3f} {ms.mean():8.3f} "
              f"{ph:7.3f} {zz['actual']:7.4f} {zz['binomial_predicted']:7.4f} "
              f"{zz['excess']:+8.4f}")

    # ---------------- verdict ----------------
    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    if lo > 1.03:
        print(f"  OVERDISPERSION CONFIRMED. phi = {phi:.4f}, CI excludes 1.0.")
        print(f"  The simulator's PA-level independence assumption is wrong: real")
        print(f"  hitters' effective rates vary game to game, producing "
              f"{z['excess']:+.1%} more")
        print(f"  zero-{args.category} games than a binomial. Under-producing zeros is")
        print(f"  exactly what inflates P(>=1).")
        print(f"\n  FIX: give the hitter a per-game rate multiplier (Beta-Binomial /")
        print(f"  Gamma-Poisson). The dispersion parameter is FITTABLE from these")
        print(f"  {len(df):,} pairs -- a real fit, not a structural placeholder.")
        print(f"\n  Fit target: phi -> 1.0. Gate on Brier + P(>=1) calibration, NOT on")
        print(f"  the fitted moment (that would be training on the test set).")
        rc = 0
    elif hi < 0.97:
        print(f"  UNDERDISPERSION (phi = {phi:.4f} < 1). Unexpected -- the simulator")
        print(f"  is producing MORE variance than reality. Do not apply the")
        print(f"  overdispersion fix; diagnose the mean model instead.")
        rc = 1
    else:
        print(f"  phi = {phi:.4f}, CI [{lo:.4f}, {hi:.4f}] -- CONSISTENT WITH BINOMIAL.")
        print(f"  The overdispersion hypothesis is NOT supported. The zero-deficit")
        print(f"  must come from somewhere else -- most likely the mean model is")
        print(f"  right ON AVERAGE but wrong PER HITTER (systematically hot on the")
        print(f"  hitters who get the most PAs). Check bias vs predicted_value decile")
        print(f"  before touching the dispersion.")
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
