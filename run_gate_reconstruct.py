#!/usr/bin/env python3
"""
Gate #4(b) — simulator side. Emit sim_probs.csv on the SAME rows the GBM was
scored on, so run_calibration_gate.py compare can inner-join and judge
"equal-or-better calibration than the simulator" on matched keys.

WHY A RECONSTRUCTION (settled in the B2 chat): the sim cache stores point
projections only. The simulator's P(over) is MonteCarloResult.p_ge_threshold,
which only exists on freshly-reconstructed hitter projections. So we replay a
SAMPLE of the 2024+ walk-forward dates through reconstruct_objects() (the
leakage-safe A2/B1 seam, additive — no live-model change) and read
p_ge_threshold off each projection.

SCOPE — HITTERS ONLY (hits, hrr, home_runs). Pitcher strikeouts are EXCLUDED
on purpose: project_pitcher_strikeouts() returns simulation=None (a bare point
estimate, no distribution), so the simulator has no honest P(over) for K to
compare against. Fabricating one would score the GBM against a strawman. The K
column of the gate waits for B3 (distributional pitcher K); this same harness
picks it up once PropProjection.simulation is populated for pitchers.

KEY CONVERSION: betting line L (half-integer) -> over means actual >= ceil(L),
so sim P(over L) = p_ge_threshold[ceil(L)]. Required integer keys:
  hits 0.5/1.5 -> {1,2} ; hrr 1.5/2.5 -> {2,3} ; home_runs 0.5 -> {1}.
If a required key is absent from p_ge_threshold, we RECOMPUTE it from the
MonteCarloResult.per_game_samples (independent of whatever thresholds the live
path happened to request); if samples are also unavailable, the row is dropped
and the date/category flagged (never fabricated).

Usage:
  # sample dates from the GBM pairs (2024+), reconstruct, emit sim P(over)
  python run_gate_reconstruct.py \
      --pairs data/models/gbm/wf_predictions_catboost.csv \
      --config config/config.json \
      --n-dates 18 --seed 17 \
      --out data/models/gbm/calibration/sim_probs.csv

  # then close the gate:
  python run_calibration_gate.py compare \
      --gbm data/models/gbm/calibration/gbm_deployed_probs.csv \
      --sim data/models/gbm/calibration/sim_probs.csv
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.learning.retrain_runner import RetrainRunner
from run_reconstruct_date import reconstruct_objects

# hitter categories the simulator baseline is defined over (K excluded; see docstring)
STANDARD_LINES = {"hits": [0.5, 1.5], "hrr": [1.5, 2.5], "home_runs": [0.5]}
GATE_CATEGORIES = tuple(STANDARD_LINES)  # ("hits", "hrr", "home_runs")


def _p_over_from_projection(proj, line: float) -> Optional[float]:
    """sim P(actual >= line) for a hitter projection, or None if unavailable.

    Primary: MonteCarloResult.p_ge_threshold[ceil(line)].
    Fallback: recompute from per_game_samples (category totals) if present.
    """
    sim = getattr(proj, "simulation", None)
    if sim is None:
        return None
    k = math.ceil(line)  # over L  <=>  count >= ceil(L)
    thr = getattr(sim, "p_ge_threshold", None) or {}
    # dict keys may be float(1.0) or int(1) or str("1.0"); probe tolerantly
    for cand in (float(k), int(k), k, f"{float(k)}", f"{k}"):
        if cand in thr:
            return float(thr[cand])
    # fallback: empirical from per-game samples (leakage-free; same sims)
    samples = getattr(sim, "per_game_samples", None)
    if samples:
        vals = []
        for s in samples:
            v = getattr(s, "category_total", None)
            if v is None:
                v = getattr(s, "total", None)
            if v is not None:
                vals.append(float(v))
        if vals:
            arr = np.asarray(vals, float)
            return float((arr >= k).mean())
    return None


def sample_dates(pairs_path: str, n_dates: int, seed: int) -> list[str]:
    df = pd.read_csv(pairs_path)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df[df["game_date"] >= "2024-01-01"]
    # weight sampling toward dates with more hitter rows so we get stable per-
    # category counts; keep it reproducible.
    hit = df[df.category.isin(GATE_CATEGORIES)]
    counts = hit.groupby(hit.game_date.dt.strftime("%Y-%m-%d")).size()
    dates = counts.index.to_numpy()
    if len(dates) <= n_dates:
        return sorted(dates.tolist())
    rng = np.random.default_rng(seed)
    w = counts.to_numpy(float); w = w / w.sum()
    pick = rng.choice(dates, size=n_dates, replace=False, p=w)
    return sorted(pick.tolist())


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True, help="wf_predictions_catboost.csv (defines the row universe)")
    ap.add_argument("--config", default=None, help="config.json (default: RetrainRunner default)")
    ap.add_argument("--n-dates", type=int, default=18, help="number of 2024+ dates to reconstruct")
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out", required=True, help="output sim_probs.csv path")
    ap.add_argument("--dates", nargs="*", default=None,
                    help="explicit date list (overrides sampling), e.g. 2024-06-15 2025-06-20")
    args = ap.parse_args(argv)

    config = RetrainRunner.load_config(args.config)
    dates = args.dates if args.dates else sample_dates(args.pairs, args.n_dates, args.seed)
    print(f"reconstructing {len(dates)} dates: {', '.join(dates)}")

    rows: list[dict[str, Any]] = []
    flags: list[str] = []
    for i, d in enumerate(dates, 1):
        try:
            projections, _outcomes = reconstruct_objects(d, config)
        except Exception as exc:  # a bad date shouldn't kill the whole run
            flags.append(f"{d}: reconstruction failed ({exc})")
            print(f"  [{i}/{len(dates)}] {d}  FAILED: {exc}", file=sys.stderr)
            continue

        n_before = len(rows)
        for proj in projections:
            cat = proj.category
            if cat not in STANDARD_LINES:      # skip strikeouts / fantasy
                continue
            for L in STANDARD_LINES[cat]:
                p = _p_over_from_projection(proj, L)
                if p is None:
                    flags.append(f"{d}/{cat}/L{L}: no p_ge_threshold[{math.ceil(L)}] and no samples")
                    continue
                rows.append(dict(
                    player_id=int(proj.player_id),
                    game_date=d,
                    category=cat,
                    line=float(L),
                    sim_p_over=float(np.clip(p, 1e-6, 1 - 1e-6)),
                ))
        print(f"  [{i}/{len(dates)}] {d}  +{len(rows) - n_before} sim rows")

    if not rows:
        print("no sim rows produced — check reconstruction/threshold keys", file=sys.stderr)
        return 2

    out = pd.DataFrame(rows).drop_duplicates(subset=["player_id", "game_date", "category", "line"])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"\nwrote {len(out)} sim P(over) rows -> {args.out}")
    print(out.groupby("category").size().to_string())
    if flags:
        print(f"\n{len(flags)} flagged (dropped, never fabricated):")
        for f in flags[:20]:
            print("  -", f)
        if len(flags) > 20:
            print(f"  ... and {len(flags) - 20} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
