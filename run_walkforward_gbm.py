#!/usr/bin/env python3
"""
B1 walk-forward — harden the GBM-vs-simulator result across multiple time folds
and let the data pick CatBoost vs LightGBM (gate #4), while saving each fold's
OUT-OF-SAMPLE GBM predictions so B2 can fit calibration on them for free.

Design (one ruler, correct rails per model):

  Folds: computed EXACTLY like WalkForwardValidator.validate() (same date-based
  boundary math) so every model is scored on identical train_end/test windows.

  GBM side (cheap): for each fold, retrain CatBoost AND LightGBM on that fold's
  training rows, predict the test window. Retraining per fold is the whole point
  of walk-forward — no fold's model sees its own test data.

  Simulator side (expensive): reconstruct each fold's TEST dates once via A2
  reconstruct_objects (leakage-safe bundles + real outcomes), disk-cache the
  projections+outcomes so re-runs and both-model scoring are free. The simulator
  can't come from the A3 dataframe (it needs full FeatureBundles), so it runs on
  its own rail but is scored through the SAME BacktestEngine on the SAME folds.

  Only the GATED categories are compared: hits, hrr, home_runs, strikeouts.
  Fantasy is excluded (no simulator baseline -> phantom win).

  For B2: every fold writes its out-of-sample GBM predictions (winning model and
  both, keyed player/date/category with the actual) to
  data/models/gbm/wf_predictions_<model>.csv. B2 reads these and fits
  isotonic/Platt WITHOUT any new reconstruction.

Usage:
    python run_walkforward_gbm.py `
      --hitters data/training/training_hitters_2023_2025_statcast.csv.gz `
      --pitchers data/training/training_pitchers_2023_2025.csv.gz `
      --config config/config.json `
      --n-folds 5 --test-window-days 7
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.evaluation.backtest_engine import BacktestEngine, OutcomeRecord
from src.evaluation.identity_keys import OUTCOME_KEY, require_unique
from src.learning.gbm_benchmark import assert_comparable
from src.learning.gbm_dataset import (
    GATED_HITTER_CATEGORIES,
    GATED_PITCHER_CATEGORIES,
    FantasyWeights,
    assert_schema,
    build_category_data,
)
from src.learning.gbm_trainer import train_category
from src.models.dataclasses import PropProjection
from src.utils.logging import setup_logging

log = logging.getLogger("wf_gbm")

GATED = tuple(GATED_HITTER_CATEGORIES) + tuple(GATED_PITCHER_CATEGORIES)
MODELS = ("catboost", "lightgbm")


# ---------------------------------------------------------------------------
# Fold boundaries — replicate WalkForwardValidator.validate()'s math exactly
# ---------------------------------------------------------------------------

def compute_folds(dates: list, n_folds: int, test_window_days: int) -> list[tuple]:
    """Return [(train_end, test_start, test_end), ...] as date objects, matching
    the validator's boundary logic so every model sees identical folds."""
    dates = sorted(dates)
    if len(dates) < n_folds + 1:
        n_folds = max(1, len(dates) - 1)
    step = max(1, len(dates) // (n_folds + 1))
    folds = []
    for i in range(1, n_folds + 1):
        split_idx = min(i * step, len(dates) - 1)
        train_end = dates[split_idx - 1]
        test_start = dates[split_idx]
        test_end = min(test_start + timedelta(days=test_window_days - 1), dates[-1])
        folds.append((train_end, test_start, test_end))
    return folds


def load_fold_spec(path: Path) -> list[tuple[date, date, date]]:
    """Load explicitly frozen walk-forward boundaries.

    A historical gate must be rebuilt on its original test windows.  Recomputing
    evenly spaced folds after the training horizon grows would silently select a
    different cohort, so an explicit spec is a reproducibility input rather
    than a convenience override.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("folds", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"fold spec {path} must be a non-empty list of folds")
    folds = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"fold spec {path} row {i} is not an object")
        try:
            train_end = date.fromisoformat(str(row["train_end"]))
            test_start = date.fromisoformat(str(row["test_start"]))
            test_end = date.fromisoformat(str(row["test_end"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"fold spec {path} row {i} needs ISO train_end/test_start/test_end"
            ) from exc
        if train_end >= test_start or test_start > test_end:
            raise ValueError(f"fold spec {path} row {i} has invalid boundaries")
        folds.append((train_end, test_start, test_end))
    return folds


# ---------------------------------------------------------------------------
# Simulator baseline — reconstruct test dates once, disk-cached
# ---------------------------------------------------------------------------

def simulator_for_dates(dates: list[str], config: dict, cache_dir: Path):
    """Return (projections, outcomes) for the given dates, using a disk cache of
    per-date reconstructions so both models and re-runs are free."""
    import run_reconstruct_date as a2

    cache_dir.mkdir(parents=True, exist_ok=True)
    projs: list[PropProjection] = []
    outs: list[OutcomeRecord] = []
    for d in dates:
        cache = cache_dir / f"sim_{d}.json"
        if cache.exists():
            payload = json.loads(cache.read_text(encoding="utf-8"))
            if any("mlb_game_pk" not in row for row in payload.get("projections", [])):
                log.warning("identity-legacy cache %s ignored; reconstructing", cache)
                payload = None
        else:
            payload = None
        if payload is None:
            sp, so = a2.reconstruct_objects(d, config)
            payload = {
                "projections": [_proj_to_row(p) for p in sp],
                "outcomes": [_out_to_row(o) for o in so],
            }
            cache.write_text(json.dumps(payload), encoding="utf-8")
            log.info("  reconstructed+cached %s (sim=%d out=%d)", d,
                     len(payload["projections"]), len(payload["outcomes"]))
        projs.extend(_row_to_proj(r) for r in payload["projections"])
        outs.extend(_row_to_out(r) for r in payload["outcomes"])
    return projs, outs


def _proj_to_row(p: PropProjection) -> dict:
    return {"player_id": p.player_id, "player_name": p.player_name, "category": p.category,
            "game_date": p.game_date, "mlb_game_pk": p.mlb_game_pk,
            "projected_value": p.projected_value}

def _row_to_proj(r: dict) -> PropProjection:
    return PropProjection(player_id=int(r["player_id"]), player_name=r.get("player_name", ""),
                          category=r["category"], game_date=r["game_date"],
                          projected_value=float(r["projected_value"]), confidence=0.0,
                          mlb_game_pk=int(r["mlb_game_pk"]))

def _out_to_row(o: OutcomeRecord) -> dict:
    return {"player_id": o.player_id, "player_name": o.player_name, "category": o.category,
            "game_date": o.game_date, "mlb_game_pk": o.mlb_game_pk,
            "actual_value": o.actual_value}

def _row_to_out(r: dict) -> OutcomeRecord:
    return OutcomeRecord(player_id=int(r["player_id"]), player_name=r.get("player_name", ""),
                         game_date=r["game_date"], category=r["category"],
                         actual_value=float(r["actual_value"]),
                         mlb_game_pk=int(r["mlb_game_pk"]))


# ---------------------------------------------------------------------------
# GBM per-fold: retrain on train rows, predict test window
# ---------------------------------------------------------------------------

def gbm_fold_predictions(hitters, pitchers, train_end, test_start, test_end,
                         model_kind, fw) -> tuple[list[PropProjection], list[OutcomeRecord]]:
    """Retrain each gated category on rows <= train_end, predict rows in
    [test_start, test_end]. Returns (projections, outcomes) for the test window."""
    projs: list[PropProjection] = []
    outs: list[OutcomeRecord] = []
    te = str(train_end); ts = str(test_start); tend = str(test_end)

    for cat in GATED_HITTER_CATEGORIES:
        p, o = _fit_predict_one(hitters, cat, "hitter", te, ts, tend, model_kind, fw)
        projs.extend(p); outs.extend(o)
    for cat in GATED_PITCHER_CATEGORIES:
        p, o = _fit_predict_one(pitchers, cat, "pitcher", te, ts, tend, model_kind, fw)
        projs.extend(p); outs.extend(o)
    outcome_frame = pd.DataFrame([_out_to_row(o) for o in outs])
    if not outcome_frame.empty:
        require_unique(outcome_frame, OUTCOME_KEY, "GBM fold outcomes")
    return projs, outs


def _fit_predict_one(df, category, kind, train_end, test_start, test_end, model_kind, fw):
    gd = df["game_date"].astype(str)
    train_df = df[gd <= train_end]
    test_df = df[(gd >= test_start) & (gd <= test_end)]
    if train_df.empty or test_df.empty:
        return [], []

    # build_category_data with split_date=test_start puts train<test_start in
    # X_train and >=test_start in X_holdout — but we want holdout bounded by
    # test_end too, so slice test_df ourselves and reuse the loader's coercion.
    data_train = build_category_data(
        pd.concat([train_df, test_df], ignore_index=True), category, kind=kind,
        split_date=test_start, fantasy_weights=fw, drop_no_prior=True,
    )
    model = train_category(data_train, model=model_kind)

    # Holdout in data_train is rows >= test_start; but that may exceed test_end.
    # Re-filter holdout_ids by test_end.
    ids = data_train.holdout_ids
    mask = (ids["game_date"].astype(str) >= test_start) & (ids["game_date"].astype(str) <= test_end)
    if not mask.any():
        return [], []
    preds = model.predict(data_train.X_holdout[mask.to_numpy()])
    ids = ids[mask.to_numpy()].reset_index(drop=True)
    y = data_train.y_holdout[mask.to_numpy()]

    projs, outs = [], []
    for i in range(len(ids)):
        row = ids.iloc[i]
        if pd.isna(row.get("game_pk")):
            raise ValueError(f"GBM {category} prediction has no training game_pk")
        game_pk = int(row["game_pk"])
        projs.append(PropProjection(
            player_id=int(row["player_id"]), player_name=str(row.get("player_name", "")),
            category=category, game_date=str(row["game_date"]),
            projected_value=round(float(preds[i]), 4), confidence=0.0,
            team=str(row.get("team", "")), opponent=str(row.get("opponent", "")),
            mlb_game_pk=game_pk,
        ))
        outs.append(OutcomeRecord(
            player_id=int(row["player_id"]), player_name=str(row.get("player_name", "")),
            game_date=str(row["game_date"]), category=category, actual_value=float(y[i]),
            mlb_game_pk=game_pk,
        ))
    return projs, outs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    config = {}
    if args.config and Path(args.config).exists():
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    fw = FantasyWeights.from_config(config)

    hitters = pd.read_csv(args.hitters, low_memory=False)
    pitchers = pd.read_csv(args.pitchers, low_memory=False)
    assert_schema(hitters, kind="hitter")
    assert_schema(pitchers, kind="pitcher")

    # Folds from the hitter date axis (superset of pitcher dates), unless a
    # frozen historical gate supplies its original boundaries explicitly.
    hdates = sorted(pd.to_datetime(hitters["game_date"].astype(str)).dt.date.unique())
    folds = (load_fold_spec(Path(args.fold_spec)) if args.fold_spec
             else compute_folds(hdates, args.n_folds, args.test_window_days))
    log.info("Computed %d folds:", len(folds))
    for te, ts, tend in folds:
        log.info("  train<=%s  test %s..%s", te, ts, tend)

    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    if args.pairs_only:
        # Rebuild the historical row universe with its original training
        # game_pk.  This deliberately skips the simulator comparison: the
        # identity migration needs a hard-keyed selection artifact, not a new
        # model-promotion result.  The same fold/category builder determines
        # eligibility, so no player/date inference is introduced.
        pairs_projs, pairs_outs = [], []
        for fi, (te, ts, tend) in enumerate(folds, 1):
            gp, go = gbm_fold_predictions(
                hitters, pitchers, te, ts, tend, args.pairs_model, fw
            )
            pairs_projs.extend(gp)
            pairs_outs.extend(go)
            log.info("[pairs-only fold %d][%s] rows=%d", fi, args.pairs_model, len(go))
        path = out_dir / f"wf_predictions_{args.pairs_model}.csv"
        _save_wf_predictions({"projs": pairs_projs, "outs": pairs_outs}, path)
        print(f"game-keyed walk-forward selection artifact: {path}")
        return 0

    engine = BacktestEngine()
    sim_cache = Path(args.sim_cache_dir)

    # Accumulate per-model out-of-sample predictions for B2 + aggregate scoring.
    agg = {m: {"projs": [], "outs": []} for m in MODELS}
    sim_all_projs, sim_all_outs = [], []
    fold_rows = []

    for fi, (te, ts, tend) in enumerate(folds, 1):
        test_dates = [d.isoformat() for d in hdates if ts <= d <= tend]
        log.info("[fold %d] test dates: %s", fi, test_dates)

        # Simulator baseline (cached).
        sim_projs, sim_outs = simulator_for_dates(test_dates, config, sim_cache)
        sim_all_projs.extend(sim_projs); sim_all_outs.extend(sim_outs)

        for m in MODELS:
            gp, go = gbm_fold_predictions(hitters, pitchers, te, ts, tend, m, fw)
            agg[m]["projs"].extend(gp); agg[m]["outs"].extend(go)
            # Score this fold vs the simulator baseline on the same outcomes.
            gbm_rep = engine.evaluate_predictions(gp, go, categories=GATED)
            sim_rep = engine.evaluate_predictions(sim_projs, sim_outs, categories=GATED)
            comp = engine.compare_reports(sim_rep, gbm_rep)
            fold_rows.append({"fold": fi, "model": m, "train_end": str(te),
                              "test_start": str(ts), "test_end": str(tend),
                              "sim_score": comp["baseline_score"],
                              "gbm_score": comp["candidate_score"],
                              "improved": comp["overall_improved"]})
            log.info("[fold %d][%s] sim=%.4f gbm=%.4f %s", fi, m,
                     comp["baseline_score"], comp["candidate_score"],
                     "GBM" if comp["overall_improved"] else "sim")

    # Aggregate across all folds, per model, vs the pooled simulator baseline.
    print("\n" + "=" * 72)
    print("WALK-FORWARD AGGREGATE — GBM vs SIMULATOR (gated categories)")
    print("=" * 72)
    sim_rep = engine.evaluate_predictions(sim_all_projs, sim_all_outs, categories=GATED)
    verdicts = {}
    for m in MODELS:
        gbm_rep = engine.evaluate_predictions(agg[m]["projs"], agg[m]["outs"], categories=GATED)
        comp = engine.compare_reports(sim_rep, gbm_rep)
        try:
            assert_comparable(comp, GATED, baseline=sim_rep, candidate=gbm_rep)
        except ValueError as exc:
            print(f"[{m}] GATE GUARD: {exc}", file=sys.stderr)
            continue
        verdicts[m] = comp
        print(f"\n[{m}]  combined: sim={comp['baseline_score']:.4f} "
              f"gbm={comp['candidate_score']:.4f} "
              f"delta={comp['candidate_score']-comp['baseline_score']:+.4f} "
              f"{'GBM WINS' if comp['overall_improved'] else 'sim wins'}")
        print(f"{'category':<12}{'sim MAE':>10}{'gbm MAE':>10}{'delta':>10}")
        for cat in GATED:
            c = comp["categories"].get(cat)
            if c:
                print(f"{cat:<12}{c['baseline_mae']:>10.4f}{c['candidate_mae']:>10.4f}{c['mae_delta']:>+10.4f}")
        # Save out-of-sample predictions for B2.
        _save_wf_predictions(agg[m], out_dir / f"wf_predictions_{m}.csv")

    # Pick winner by lower combined candidate score (only among models that beat sim).
    winners = {m: v["candidate_score"] for m, v in verdicts.items()}
    if winners:
        best = min(winners, key=winners.get)
        beat = verdicts[best]["overall_improved"]
        print("\n" + "-" * 72)
        print(f"WINNER (lowest combined MAE): {best}  "
              f"({'beats' if beat else 'does NOT beat'} simulator)")
        print(f"B2 should calibrate '{best}' on wf_predictions_{best}.csv")
        print("-" * 72)

    report = {"folds": fold_rows,
              "aggregate": {m: v for m, v in verdicts.items()}}
    (out_dir / "walkforward_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nReport: {out_dir / 'walkforward_report.json'}")
    print("Reminder: MAE across folds is the model-selection signal. Promotion "
          "still needs B2 calibration (equal/better) + shadow logging (gate #4).")
    return 0


def _save_wf_predictions(bundle, path: Path):
    proj = {(p.mlb_game_pk, p.player_id, p.category): p.projected_value for p in bundle["projs"]}
    rows = []
    for o in bundle["outs"]:
        k = (o.mlb_game_pk, o.player_id, o.category)
        if k in proj:
            rows.append({"mlb_game_pk": o.mlb_game_pk, "player_id": o.player_id, "player_name": o.player_name,
                         "game_date": o.game_date, "category": o.category,
                         "predicted_value": proj[k], "actual_value": o.actual_value})
    frame = pd.DataFrame(rows)
    require_unique(frame, OUTCOME_KEY, f"walk-forward predictions for {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f"{path.suffix}.tmp")
    frame.to_csv(temp, index=False)
    written = pd.read_csv(temp)
    require_unique(written, OUTCOME_KEY, f"written walk-forward predictions for {path.name}")
    temp.replace(path)
    log.info("saved %d out-of-sample predictions -> %s", len(frame), path.name)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Walk-forward GBM (both models) vs simulator (B1).")
    p.add_argument("--hitters", required=True)
    p.add_argument("--pitchers", required=True)
    p.add_argument("--config", default="config/config.json")
    p.add_argument("--n-folds", type=int, default=5)
    p.add_argument("--test-window-days", type=int, default=7)
    p.add_argument("--fold-spec", default=None,
                   help="JSON list of frozen train_end/test_start/test_end windows; "
                        "use for historical-gate reconstruction instead of recomputing folds")
    p.add_argument("--out-dir", default="data/models/gbm")
    p.add_argument("--sim-cache-dir", default="data/cache/wf_simulator")
    p.add_argument("--pairs-only", action="store_true",
                   help="rebuild only the hard-keyed walk-forward selection artifact")
    p.add_argument("--pairs-model", choices=MODELS, default="catboost",
                   help="model whose walk-forward row universe to rebuild with --pairs-only")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
