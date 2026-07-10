#!/usr/bin/env python3
"""
B1 — Train per-category GBMs on the A3(+A4) training set and (optionally)
benchmark them against the simulator baseline on held-out dates.

The simulator stays the BASELINE to beat (roadmap). A GBM only matters if it
beats the simulator on held-out dates via backtest_engine.compare_reports and,
later (B2), calibrates at least as well. This script trains + reports; PROMOTION
is a separate, evidence-gated step (discipline #4).

Usage:
    # train all gated categories, CatBoost, 20% most-recent dates held out
    python run_train_gbm.py \\
        --hitters data/training/training_hitters_2023_2025_statcast.csv.gz \\
        --pitchers data/training/training_pitchers_2023_2025.csv.gz

    # LightGBM second opinion
    python run_train_gbm.py ... --model lightgbm

    # include the ungated fantasy model (trained, but NOT part of the gate)
    python run_train_gbm.py ... --include-fantasy

    # also run the simulator benchmark on the held-out dates (needs A2 wired)
    python run_train_gbm.py ... --benchmark

Categories: hits, hrr, home_runs (hitter shard) + strikeouts (pitcher shard)
are the GATED set — each has a simulator baseline via A2. fantasy is trainable
but has no simulator baseline, so it is excluded from the gate by default.

Requires catboost (primary) or lightgbm (--model lightgbm):
    pip install catboost lightgbm
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from src.learning.gbm_dataset import (
    GATED_HITTER_CATEGORIES,
    GATED_PITCHER_CATEGORIES,
    UNGATED_HITTER_CATEGORIES,
    FantasyWeights,
    assert_schema,
    build_category_data,
    choose_split_date,
)
from src.learning.gbm_trainer import train_category
from src.utils.logging import setup_logging

EXIT_OK = 0
EXIT_ERROR = 1

log = logging.getLogger("train_gbm")


def _load(path: str) -> pd.DataFrame:
    # A4 blanks are "" -> keep as object then coerce per-column in the loader;
    # low_memory=False avoids mixed-dtype chunk warnings on wide CSVs.
    return pd.read_csv(path, low_memory=False)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    config = {}
    if args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    fw = FantasyWeights.from_config(config)

    hitters = _load(args.hitters)
    pitchers = _load(args.pitchers)

    # PROVENANCE GATE — fail loudly before any training.
    try:
        assert_schema(hitters, kind="hitter")
        assert_schema(pitchers, kind="pitcher")
    except ValueError as exc:
        print(f"Schema gate failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # One split date per shard, from that shard's own date distribution.
    split_h = args.split_date or choose_split_date(hitters, args.holdout_fraction)
    split_p = args.split_date or choose_split_date(pitchers, args.holdout_fraction)
    log.info("Temporal split — hitters >= %s, pitchers >= %s held out", split_h, split_p)

    hitter_cats = list(GATED_HITTER_CATEGORIES)
    if args.include_fantasy:
        hitter_cats += list(UNGATED_HITTER_CATEGORIES)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries = {}
    trained = {}

    for cat in hitter_cats:
        data = build_category_data(
            hitters, cat, kind="hitter", split_date=split_h, fantasy_weights=fw
        )
        log.info("[%s] %s", cat, data.summary())
        model = train_category(data, model=args.model, seed=args.seed)
        trained[cat] = (model, data)
        summaries[cat] = data.summary()
        _save_model(model, out_dir, cat, args.model, log)

    for cat in GATED_PITCHER_CATEGORIES:
        data = build_category_data(
            pitchers, cat, kind="pitcher", split_date=split_p, fantasy_weights=fw
        )
        log.info("[%s] %s", cat, data.summary())
        model = train_category(data, model=args.model, seed=args.seed)
        trained[cat] = (model, data)
        summaries[cat] = data.summary()
        _save_model(model, out_dir, cat, args.model, log)

    # Holdout self-MAE (GBM vs actuals) — a quick sanity number, NOT the gate.
    # The gate is compare_reports vs the simulator (run with --benchmark once A2
    # exposes reconstruct_objects; see gbm_benchmark.A2_PATCH).
    report = {"model": args.model, "split_hitters": split_h, "split_pitchers": split_p,
              "categories": {}}
    for cat, (model, data) in trained.items():
        preds = model.predict(data.X_holdout) if len(data.y_holdout) else []
        mae = float(abs(preds - data.y_holdout).mean()) if len(preds) else None
        report["categories"][cat] = {**summaries[cat], "holdout_mae_vs_actual": mae,
                                     "gated": cat not in UNGATED_HITTER_CATEGORIES}
        if mae is not None:
            log.info("[%s] holdout MAE vs actual = %.4f", cat, mae)

    report_path = out_dir / f"train_report_{args.model}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nTrained {len(trained)} models ({args.model}). Report: {report_path}")
    print("NOTE: holdout MAE vs actual is a sanity check, not the promotion "
          "gate. Run the simulator benchmark (compare_reports) to test whether "
          "the GBM actually beats the simulator on the same held-out dates.")
    return EXIT_OK


def _save_model(model, out_dir: Path, cat: str, kind: str, log) -> None:
    try:
        if kind == "catboost":
            path = out_dir / f"gbm_{cat}_catboost.cbm"
            model.model.save_model(str(path))
        else:
            import joblib

            path = out_dir / f"gbm_{cat}_lightgbm.joblib"
            joblib.dump({"model": model.model, "label_maps": model.label_maps,
                         "feature_names": model.feature_names}, path)
        log.info("[%s] saved -> %s", cat, path.name)
    except Exception as exc:
        log.warning("[%s] could not save model: %s", cat, exc)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Train per-category GBMs on the A3(+A4) set (B1).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__,
    )
    p.add_argument("--hitters", required=True, help="Enriched hitter CSV(.gz) (A3+A4)")
    p.add_argument("--pitchers", required=True, help="Pitcher CSV(.gz) (A3)")
    p.add_argument("--config", help="config.json (for fantasy_scoring weights)")
    p.add_argument("--model", choices=["catboost", "lightgbm"], default="catboost")
    p.add_argument("--split-date", metavar="YYYY-MM-DD",
                   help="Explicit temporal split; default = most-recent holdout-fraction")
    p.add_argument("--holdout-fraction", type=float, default=0.2)
    p.add_argument("--include-fantasy", action="store_true",
                   help="Also train fantasy (ungated: no simulator baseline)")
    p.add_argument("--benchmark", action="store_true",
                   help="Run simulator benchmark on held-out dates (needs A2 wired)")
    p.add_argument("--out-dir", default="data/models/gbm")
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
