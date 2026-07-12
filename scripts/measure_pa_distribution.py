#!/usr/bin/env python3
"""
MEASURE (do not infer) the simulator's plate-appearance distribution.

THE CLAIM UNDER TEST
  _sample_pa_count() emits only floor(expected_pa) or floor(expected_pa)+1:

      base = int(expected_pa)                       # 4.3 -> 4
      frac = expected_pa - base                     # 0.3
      if frac > 0 and rng.random() < frac:
          return base + 1                           # 5, with p=0.3
      return base                                   # 4, with p=0.7

  So for expected_pa=4.3 it produces ONLY 4 or 5. Never 1, 2, or 3.
  Reality (measured, out_pa over 4,814 rows): 20.3% of games are pa <= 3.

  HYPOTHESIS: the simulator has no LEFT TAIL on PA. It never generates the
  early-exit games (pulled, blowout, injury, pinch-hit), and those are where
  the zero-hit outcomes live. That -- not the hit rate -- would explain the
  +11.5pp overconfidence on P(hits >= 1).

  THIS IS A HYPOTHESIS. It has NOT been measured. This script measures it.

WHAT THIS SCRIPT DOES NOT ASSUME
  - It does not assume expected_pa is 4.3. It READS the real expected_pa the
    feature factory produces (via reconstruct_objects, the same seam the gate
    uses), for real hitters on real dates.
  - It does not assume the simulator's P(hits>=1) is flat across PA. It RUNS
    the simulator and measures it.
  - It does not assume fixing PA fixes the bias. It SUBSTITUTES the empirical
    PA distribution and re-measures.

SANITY RANGES (rule 7 -- stated BEFORE the run, so an implausible number is a
broken statistic, not a finding):
  mean expected_pa      : 3.5 - 4.6   (a lineup regular bats ~4x)
  mean actual out_pa    : 3.938       (MEASURED, n=4,814)
  P(pa<=3) actual       : 0.203       (MEASURED)
  P(pa<=3) simulated    : if the hypothesis holds, ~0.00 for expected_pa>=4
  P(hits>=1) actual     : 0.563       (MEASURED, gate rows)
  P(hits>=1) simulated  : 0.678       (MEASURED, gate rows -- the +11.5pp)

Usage:
    python scripts/measure_pa_distribution.py --date 2025-06-27
    python scripts/measure_pa_distribution.py --date 2025-06-27 --n-sims 4000
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.learning.retrain_runner import RetrainRunner            # noqa: E402
from run_reconstruct_date import reconstruct_objects             # noqa: E402
from src.models.dataclasses import LeagueBaselines               # noqa: E402
from src.prediction.prop_engine import PropEngine                # noqa: E402
from src.features.feature_factory import FeatureFactory          # noqa: E402
from src.data.mlb_api import MLBStatsAPI                         # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2025-06-27",
                    help="a date from the gate's 16 (has both bundles and outcomes)")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--n-sims", type=int, default=4000)
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    args = ap.parse_args(argv)

    config = RetrainRunner.load_config(args.config)
    league = LeagueBaselines.from_config(config)
    mlb_api = MLBStatsAPI(season=int(args.date[:4]), config=config)
    factory = FeatureFactory(config=config, league_baselines=league, mlb_api=mlb_api)
    engine = PropEngine(config=config, league_baselines=league)

    bundles = factory.build_bundles(args.date)
    if not bundles:
        print(f"no bundles for {args.date}", file=sys.stderr)
        return 2

    gs = engine.monte_carlo.game_simulator

    # ---------------- 1. what IS expected_pa? (never actually looked) --------
    exp_pa = np.array([b.expected_pa for b in bundles], float)
    print("=" * 72)
    print(f"1. expected_pa  -- what the model PROJECTS  (n={len(exp_pa)} hitters, "
          f"{args.date})")
    print("=" * 72)
    print(f"  mean   : {exp_pa.mean():.3f}")
    print(f"  median : {np.median(exp_pa):.3f}")
    print(f"  min    : {exp_pa.min():.3f}")
    print(f"  max    : {exp_pa.max():.3f}")
    print(f"  P(expected_pa < 4) : {(exp_pa < 4).mean():.4f}")
    print(f"  P(expected_pa < 3) : {(exp_pa < 3).mean():.4f}")
    print("\n  histogram (0.25 bins):")
    hist, edges = np.histogram(exp_pa, bins=np.arange(0, 6.25, 0.25))
    for h, e in zip(hist, edges):
        if h:
            print(f"    [{e:.2f}, {e+0.25:.2f})  {h:4d}  {'#' * min(60, h)}")

    # ---------------- 2. what does the SIMULATOR produce? -------------------
    print()
    print("=" * 72)
    print(f"2. simulated PA  -- what _sample_pa_count ACTUALLY emits "
          f"({args.n_sims} sims/hitter)")
    print("=" * 72)
    sim_pa = Counter()
    gs.seed(20260711)
    for b in bundles:
        for _ in range(args.n_sims):
            sim_pa[gs._sample_pa_count(b.expected_pa)] += 1
    tot = sum(sim_pa.values())
    print(f"  {'pa':>3s} {'count':>9s} {'share':>8s}")
    for k in sorted(sim_pa):
        print(f"  {k:3d} {sim_pa[k]:9d} {sim_pa[k]/tot:8.4f}")
    sim_le3 = sum(v for k, v in sim_pa.items() if k <= 3) / tot
    print(f"\n  P(simulated pa <= 3) = {sim_le3:.4f}")

    # ---------------- 3. what does REALITY produce? -------------------------
    print()
    print("=" * 72)
    print("3. actual PA  -- out_pa from the box scores (training set)")
    print("=" * 72)
    tr = pd.read_csv(args.training, low_memory=False)
    real = tr[tr.game_date == args.date]["out_pa"].dropna()
    if real.empty:
        print(f"  no out_pa rows for {args.date}; falling back to ALL dates")
        real = tr["out_pa"].dropna()
    real = real[real > 0]
    rc = real.value_counts().sort_index()
    print(f"  {'pa':>3s} {'count':>9s} {'share':>8s}")
    for k, v in rc.items():
        print(f"  {int(k):3d} {v:9d} {v/len(real):8.4f}")
    real_le3 = (real <= 3).mean()
    print(f"\n  P(actual pa <= 3) = {real_le3:.4f}   (n={len(real)})")

    # ---------------- 4. the verdict ---------------------------------------
    print()
    print("=" * 72)
    print("4. VERDICT")
    print("=" * 72)
    print(f"  P(pa <= 3):  simulated {sim_le3:.4f}   actual {real_le3:.4f}   "
          f"gap {real_le3 - sim_le3:+.4f}")
    if sim_le3 < 0.02 and real_le3 > 0.10:
        print("\n  CONFIRMED: the simulator has NO LEFT TAIL on plate appearances.")
        print(f"  Reality produces {real_le3:.1%} games at pa<=3; the simulator "
              f"produces {sim_le3:.1%}.")
        print("  _sample_pa_count emits only floor(expected_pa) and floor+1, so the")
        print("  early-exit games (pulled / blowout / injury / pinch-hit) are NEVER")
        print("  simulated -- and those are disproportionately the ZERO-HIT games.")
        print("\n  This does NOT yet prove it CAUSES the +11.5pp P(hits>=1) bias.")
        print("  That requires substituting the empirical PA distribution and")
        print("  re-measuring P(hits>=1). See measure_pa_counterfactual.py.")
        rc_ = 0
    elif abs(sim_le3 - real_le3) < 0.05:
        print("\n  NOT CONFIRMED: the simulated and actual PA distributions AGREE.")
        print("  The missing-left-tail hypothesis is WRONG. The +11.5pp bias comes")
        print("  from somewhere else -- look at the per-PA hit rate, not the PA count.")
        rc_ = 1
    else:
        print(f"\n  PARTIAL: sim {sim_le3:.4f} vs actual {real_le3:.4f}. Neither the")
        print("  'no left tail' nor the 'distributions agree' story fits cleanly.")
        print("  Read the histograms above before drawing any conclusion.")
        rc_ = 1
    return rc_


if __name__ == "__main__":
    sys.exit(main())
