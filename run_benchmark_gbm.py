#!/usr/bin/env python3
"""
B1 benchmark — the real "does the GBM beat the simulator?" test.

For each sampled held-out DATE:
  1. reconstruct_objects(date)  -> simulator PropProjections + OutcomeRecords
     (the leakage-safe A2 baseline; the outcomes are the ground truth).
  2. Load that date's rows from the enriched training file, run the trained
     CatBoost models -> GBM PropProjections (same players, same date).
  3. Score BOTH against the SAME OutcomeRecords via BacktestEngine, then
     compare_reports(simulator, gbm) with the phantom-category guard.

Only the GATED categories are compared: hits, hrr, home_runs (hitter) and
strikeouts (pitcher) — each has a simulator baseline. Fantasy has none and is
excluded (would be a phantom win).

Sampling: --sample N picks N dates evenly spread across the holdout window
(default 12) for a fast first read; --dates d1,d2,... overrides with an explicit
list; --all uses every holdout date (slow). Each reconstruction hits the MLB
API but your A3 disk cache absorbs most of it.

Usage:
    python run_benchmark_gbm.py `
        --hitters data/training/training_hitters_2023_2025_statcast.csv.gz `
        --pitchers data/training/training_pitchers_2023_2025.csv.gz `
        --models-dir data/models/gbm `
        --split-date 2025-06-07 `
        --sample 12
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.evaluation.backtest_engine import BacktestEngine, OutcomeRecord
from src.learning.gbm_benchmark import assert_comparable, score_reports
from src.learning.gbm_dataset import (
    GATED_HITTER_CATEGORIES,
    GATED_PITCHER_CATEGORIES,
    HITTER_CATEGORICAL,
    PITCHER_CATEGORICAL,
    feature_columns,
)
from src.models.dataclasses import PropProjection
from src.utils.logging import setup_logging

log = logging.getLogger("benchmark_gbm")

EXPECTED = tuple(GATED_HITTER_CATEGORIES) + tuple(GATED_PITCHER_CATEGORIES)  # 4 gated


def _coerce_like_training(df: pd.DataFrame, feats: list[str], categorical: list[str]) -> pd.DataFrame:
    """Reproduce gbm_dataset._coerce_features so inference matches training."""
    out = pd.DataFrame(index=df.index)
    cat_set = set(categorical)
    for c in feats:
        if c in cat_set:
            col = df[c].astype("object")
            col = col.where(~col.isna(), "__NA__").replace({"": "__NA__"})
            out[c] = col.astype(str)
        else:
            out[c] = pd.to_numeric(df[c], errors="coerce")
    return out


def _load_catboost(models_dir: Path, category: str):
    from catboost import CatBoostRegressor

    path = models_dir / f"gbm_{category}_catboost.cbm"
    if not path.exists():
        raise FileNotFoundError(f"Missing model: {path}")
    reg = CatBoostRegressor()
    reg.load_model(str(path))
    return reg


def _gbm_projections_for_date(
    date: str,
    hitters: pd.DataFrame,
    pitchers: pd.DataFrame,
    models: dict[str, object],
) -> list[PropProjection]:
    """Run trained models on one date's rows -> PropProjections."""
    projs: list[PropProjection] = []

    hrows = hitters[hitters["game_date"].astype(str) == date]
    if not hrows.empty:
        feats_h = feature_columns(hrows, kind="hitter")
        cat_h = [c for c in HITTER_CATEGORICAL if c in hrows.columns]
        Xh = _coerce_like_training(hrows, feats_h, cat_h)
        from catboost import Pool

        for cat in GATED_HITTER_CATEGORIES:
            preds = np.clip(models[cat].predict(Pool(Xh[feats_h], cat_features=cat_h or None)), 0, None)
            for (_, row), pred in zip(hrows.iterrows(), preds):
                projs.append(PropProjection(
                    player_id=int(row["player_id"]), player_name=str(row.get("player_name", "")),
                    category=cat, game_date=date, projected_value=round(float(pred), 4),
                    confidence=0.0, team=str(row.get("team", "")), opponent=str(row.get("opponent", "")),
                ))

    prows = pitchers[pitchers["game_date"].astype(str) == date]
    if not prows.empty:
        feats_p = feature_columns(prows, kind="pitcher")
        cat_p = [c for c in PITCHER_CATEGORICAL if c in prows.columns]
        Xp = _coerce_like_training(prows, feats_p, cat_p)
        from catboost import Pool

        for cat in GATED_PITCHER_CATEGORIES:
            preds = np.clip(models[cat].predict(Pool(Xp[feats_p], cat_features=cat_p or None)), 0, None)
            for (_, row), pred in zip(prows.iterrows(), preds):
                projs.append(PropProjection(
                    player_id=int(row["player_id"]), player_name=str(row.get("player_name", "")),
                    category=cat, game_date=date, projected_value=round(float(pred), 4),
                    confidence=0.0, team=str(row.get("team", "")), opponent=str(row.get("opponent", "")),
                ))
    return projs


def _sample_dates(all_dates: list[str], n: int) -> list[str]:
    if n >= len(all_dates):
        return all_dates
    idx = np.linspace(0, len(all_dates) - 1, n).round().astype(int)
    return [all_dates[i] for i in sorted(set(idx))]


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    config = {}
    if args.config and Path(args.config).exists():
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))

    hitters = pd.read_csv(args.hitters, low_memory=False)
    pitchers = pd.read_csv(args.pitchers, low_memory=False)

    # Holdout dates = on/after split, present in the data.
    hd = sorted(set(hitters.loc[hitters["game_date"].astype(str) >= args.split_date, "game_date"].astype(str)))
    if args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    elif args.all:
        dates = hd
    else:
        dates = _sample_dates(hd, args.sample)
    log.info("Benchmarking %d held-out dates (of %d available >= %s)", len(dates), len(hd), args.split_date)

    models = {c: _load_catboost(Path(args.models_dir), c) for c in EXPECTED}

    import run_reconstruct_date as a2

    engine = BacktestEngine()
    all_sim: list[PropProjection] = []
    all_gbm: list[PropProjection] = []
    all_out: list[OutcomeRecord] = []
    per_date = {}

    for d in dates:
        try:
            sim_projs, outcomes = a2.reconstruct_objects(d, config)
        except Exception as exc:
            log.warning("reconstruct_objects failed for %s: %s", d, exc)
            continue
        gbm_projs = _gbm_projections_for_date(d, hitters, pitchers, models)
        all_sim.extend(sim_projs)
        all_gbm.extend(gbm_projs)
        all_out.extend(outcomes)
        per_date[d] = len(outcomes)
        log.info("  %s: sim=%d gbm=%d outcomes=%d", d, len(sim_projs), len(gbm_projs), len(outcomes))

    if not all_out:
        print("No outcomes reconstructed — cannot benchmark. Check MLB API/cache.", file=sys.stderr)
        return 1

    sim_report, gbm_report, comparison = score_reports(all_sim, all_gbm, all_out, EXPECTED, engine)
    try:
        compared = assert_comparable(comparison, EXPECTED, baseline=sim_report, candidate=gbm_report)
    except ValueError as exc:
        print(f"\nGATE GUARD TRIPPED: {exc}", file=sys.stderr)
        return 2

    _print_verdict(dates, per_date, comparison, compared)
    out = Path(args.out or "reports/benchmark_gbm.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "split_date": args.split_date, "dates": dates, "per_date_outcomes": per_date,
        "comparison": comparison,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nReport: {out}")
    return 0


def _print_verdict(dates, per_date, comparison, compared):
    print("\n" + "=" * 66)
    print(f"GBM vs SIMULATOR — {len(dates)} held-out dates, "
          f"{sum(per_date.values())} outcome rows")
    print("=" * 66)
    print(f"{'category':<12}{'sim MAE':>10}{'gbm MAE':>10}{'delta':>10}  winner")
    print("-" * 66)
    for cat in compared:
        c = comparison["categories"][cat]
        delta = c["mae_delta"]  # candidate(gbm) - baseline(sim); negative = gbm better
        winner = "GBM" if delta < 0 else ("sim" if delta > 0 else "tie")
        print(f"{cat:<12}{c['baseline_mae']:>10.4f}{c['candidate_mae']:>10.4f}{delta:>+10.4f}  {winner}")
    print("-" * 66)
    print(f"{'combined':<12}{comparison['baseline_score']:>10.4f}"
          f"{comparison['candidate_score']:>10.4f}"
          f"{comparison['candidate_score']-comparison['baseline_score']:>+10.4f}  "
          f"{'GBM' if comparison['overall_improved'] else 'sim'}")
    print("=" * 66)
    print("Negative delta = GBM lower MAE = GBM better. This is a FIRST READ on "
          f"{len(dates)} sampled dates, not the promotion decision — widen the "
          "sample and add calibration (B2) before any gate call.")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Benchmark trained GBMs vs the simulator (B1).")
    p.add_argument("--hitters", required=True)
    p.add_argument("--pitchers", required=True)
    p.add_argument("--models-dir", default="data/models/gbm")
    p.add_argument("--split-date", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--config", default="config/config.json")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--sample", type=int, default=12, help="N dates evenly spread across holdout")
    g.add_argument("--dates", help="Explicit comma-separated dates")
    g.add_argument("--all", action="store_true", help="Every holdout date (slow)")
    p.add_argument("--out", help="Report JSON path")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
