#!/usr/bin/env python3
"""Fit a sealed chronological *game-probability* challenger for batter props.

This is deliberately not a production switch and not a market evaluation.
It answers one narrower question on a content-addressed, point-in-time
2023--2025 training set: can a regularized fitted model materially improve
binary-event probabilities over a transparent empirical-Bayes baseline?

Protocol (hard-coded so it cannot accidentally tune on confirmation):
  * 2023: fit; 2024: select one of two predeclared CatBoost capacities.
  * 2025: one final confirmation only, never used for selection.
  * The input manifest must attest to the declared decision horizon with raw
    receipt hashes; a completed-game reconstruction is not a substitute.
  * Every source row must carry the a3.2/a4.1 point-in-time stamps and a
    pregame probable opposing starter.  Actual-starter rows are refused.
  * The output directory must be new.  Results are written atomically and
    include input/config/code hashes.  No odds, policies, May 2026 data,
    prediction service, or live runtime are touched.

The direct game classifiers cover Hits 0.5/1.5, HR 0.5 and Total Bases
0.5/1.5.  They are a strict benchmark for a future coherent PA-outcome model,
not an authorization candidate and not a substitute for its PA distribution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.learning.gbm_dataset import (
    HITTER_CATEGORICAL,
    assert_schema,
    feature_columns,
    _coerce_features,
)


TASKS: tuple[tuple[str, str, int], ...] = (
    ("hits_over_0_5", "hits", 1),
    ("hits_over_1_5", "hits", 2),
    ("home_runs_over_0_5", "home_runs", 1),
    ("total_bases_over_0_5", "total_bases", 1),
    ("total_bases_over_1_5", "total_bases", 2),
)
CAPACITIES: tuple[dict[str, Any], ...] = (
    # Deliberately compact capacities: this is an initial, reproducible
    # benchmark.  A large unbounded search would merely spend compute on the
    # 2024 selector and create a hidden multiple-testing problem.
    {"name": "shallow", "iterations": 100, "depth": 4, "learning_rate": 0.04,
     "l2_leaf_reg": 12.0},
    {"name": "regular", "iterations": 180, "depth": 6, "learning_rate": 0.03,
     "l2_leaf_reg": 20.0},
)
SEED = 20260720


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _total_bases(df: pd.DataFrame) -> np.ndarray:
    n = lambda c: pd.to_numeric(df[c], errors="raise").to_numpy(dtype=float)
    return n("out_hits") + n("out_doubles") + 2.0 * n("out_triples") + 3.0 * n("out_hr")


def target(df: pd.DataFrame, stat: str, threshold: int) -> np.ndarray:
    if stat == "hits":
        value = pd.to_numeric(df["out_hits"], errors="raise").to_numpy(dtype=float)
    elif stat == "home_runs":
        value = pd.to_numeric(df["out_hr"], errors="raise").to_numpy(dtype=float)
    elif stat == "total_bases":
        value = _total_bases(df)
    else:  # pragma: no cover - TASKS is fixed above
        raise ValueError(stat)
    return (value >= threshold).astype(np.int8)


def eb_probability(df: pd.DataFrame, stat: str, threshold: int) -> np.ndarray:
    """Transparent no-outcome-tuning baseline from pregame cumulative fields.

    It models the event count as Poisson with a player-to-league shrunk rate.
    This is not declared a realistic game simulator; it is a deliberately
    simple control that uses only fields present before the evaluated game.
    """
    eps = 1e-9
    if stat == "hits":
        numerator = pd.to_numeric(df["pit_hits"], errors="coerce").fillna(0.0)
        denom = pd.to_numeric(df["pit_games"], errors="coerce").fillna(0.0)
    elif stat == "home_runs":
        numerator = pd.to_numeric(df["pit_hr"], errors="coerce").fillna(0.0)
        denom = pd.to_numeric(df["pit_games"], errors="coerce").fillna(0.0)
    elif stat == "total_bases":
        hits = pd.to_numeric(df["pit_hits"], errors="coerce").fillna(0.0)
        doubles = pd.to_numeric(df["pit_doubles"], errors="coerce").fillna(0.0)
        triples = pd.to_numeric(df["pit_triples"], errors="coerce").fillna(0.0)
        hrs = pd.to_numeric(df["pit_hr"], errors="coerce").fillna(0.0)
        numerator = hits + doubles + 2.0 * triples + 3.0 * hrs
        denom = pd.to_numeric(df["pit_games"], errors="coerce").fillna(0.0)
    else:  # pragma: no cover
        raise ValueError(stat)

    # Fit neither factor nor threshold on future outcomes: a fixed 30-game
    # prior prevents tiny early samples from producing extreme probabilities.
    league_rate = float(numerator.sum() / max(float(denom.sum()), eps))
    rate = (numerator + 30.0 * league_rate) / (denom + 30.0)
    lam = np.maximum(rate.to_numpy(dtype=float), eps)
    if threshold == 1:
        return np.clip(1.0 - np.exp(-lam), eps, 1.0 - eps)
    if threshold == 2:
        return np.clip(1.0 - np.exp(-lam) * (1.0 + lam), eps, 1.0 - eps)
    raise ValueError("only 0.5 and 1.5 lines are protocol-defined")


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1.0 - 1e-6)
    out: dict[str, float] = {
        "n": int(len(y)),
        "prevalence": float(np.mean(y)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
    }
    out["auc"] = float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else float("nan")
    # Equal-width ECE is reported, never used alone as a promotion criterion.
    bins = np.linspace(0.0, 1.0, 11)
    index = np.clip(np.digitize(p, bins, right=True) - 1, 0, 9)
    ece = 0.0
    for b in range(10):
        mask = index == b
        if mask.any():
            ece += mask.mean() * abs(float(np.mean(y[mask])) - float(np.mean(p[mask])))
    out["ece_10"] = float(ece)
    return out


def date_bootstrap_delta(
    dates: np.ndarray, y: np.ndarray, candidate: np.ndarray, baseline: np.ndarray,
    *, draws: int = 1000,
) -> dict[str, float]:
    """Deterministic date-block uncertainty for Brier and log-loss deltas."""
    unique = np.unique(dates)
    if len(unique) < 2:
        raise ValueError("confirmation has fewer than two dates; cannot bootstrap by date")
    rng = np.random.default_rng(SEED)
    daily: list[tuple[float, float]] = []
    for d in unique:
        m = dates == d
        cm = metrics(y[m], candidate[m])
        bm = metrics(y[m], baseline[m])
        daily.append((cm["brier"] - bm["brier"], cm["log_loss"] - bm["log_loss"]))
    arr = np.asarray(daily, dtype=float)
    samples = np.empty((draws, 2), dtype=float)
    for i in range(draws):
        samples[i] = arr[rng.integers(0, len(arr), size=len(arr))].mean(axis=0)
    return {
        "date_blocks": int(len(unique)),
        "brier_delta_mean": float(arr[:, 0].mean()),
        "brier_delta_95_low": float(np.quantile(samples[:, 0], 0.025)),
        "brier_delta_95_high": float(np.quantile(samples[:, 0], 0.975)),
        "log_loss_delta_mean": float(arr[:, 1].mean()),
        "log_loss_delta_95_low": float(np.quantile(samples[:, 1], 0.025)),
        "log_loss_delta_95_high": float(np.quantile(samples[:, 1], 0.975)),
    }


def train_predict(Xtr: pd.DataFrame, ytr: np.ndarray, Xev: pd.DataFrame,
                  cats: list[str], capacity: dict[str, Any]) -> np.ndarray:
    from catboost import CatBoostClassifier, Pool

    params = {
        "loss_function": "Logloss",
        "eval_metric": "Logloss",
        "random_seed": SEED,
        "verbose": False,
        "allow_writing_files": False,
        "thread_count": -1,
        **{k: v for k, v in capacity.items() if k != "name"},
    }
    model = CatBoostClassifier(**params)
    model.fit(Pool(Xtr, label=ytr, cat_features=cats or None))
    return model.predict_proba(Pool(Xev, cat_features=cats or None))[:, 1]


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, sort_keys=True, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except Exception:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"REFUSING: output already exists: {args.output}")
    if not args.input.is_file() or not args.manifest.is_file():
        raise SystemExit("REFUSING: input and manifest must both exist")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("decision_horizon_provenance_verified") is not True:
        raise SystemExit(
            "REFUSING: the input manifest lacks a verified decision-horizon "
            "attestation. A completed-game reconstruction can establish official "
            "outcomes but cannot prove that lineup, pitcher, weather, or umpire "
            "fields were available at prediction time."
        )
    if not manifest.get("decision_horizon_receipts_sha256"):
        raise SystemExit(
            "REFUSING: verified decision-horizon provenance requires receipt hashes"
        )
    actual_hash = sha256_file(args.input)
    if manifest.get("output_sha256") != actual_hash:
        raise SystemExit("REFUSING: input hash does not match its quarantine manifest")
    df = pd.read_csv(args.input, low_memory=False)
    assert_schema(df, kind="hitter")
    if len(df) != int(manifest.get("output_rows", -1)):
        raise SystemExit("REFUSING: input row count does not match its manifest")
    if set(df["opp_sp_source"].astype(str)) != {"probable"}:
        raise SystemExit("REFUSING: non-probable opposing-starter source survived quarantine")
    years = pd.to_datetime(df["game_date"], errors="raise").dt.year
    if set(years.unique()) != {2023, 2024, 2025}:
        raise SystemExit("REFUSING: protocol requires exactly 2023, 2024, and 2025")
    if df.duplicated(["game_pk", "player_id"]).any():
        raise SystemExit("REFUSING: duplicate game/player identity in challenger input")

    categorical = [c for c in HITTER_CATEGORICAL if c in df.columns]
    features = feature_columns(df, kind="hitter")
    X = _coerce_features(df, features, categorical)
    fit = years.eq(2023).to_numpy()
    select = years.eq(2024).to_numpy()
    confirm = years.eq(2025).to_numpy()
    if not (fit.any() and select.any() and confirm.any()):
        raise SystemExit("REFUSING: one chronological role has no rows")

    report: dict[str, Any] = {
        "schema_version": "time-safe-batter-game-probability-challenger-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "benchmark_only_not_production_not_market_evaluation",
        "protocol": {
            "fit_year": 2023, "selection_year": 2024, "confirmation_year": 2025,
            "tasks": [name for name, _, _ in TASKS], "capacities": CAPACITIES,
            "selection_rule": "lowest unweighted mean validation log_loss across tasks",
            "confirmation_rule": "report-only; never used for model/capacity selection",
            "bootstrap": "1000 deterministic date-block resamples, seed 20260720",
        },
        "input": {
            "path": str(args.input), "sha256": actual_hash, "rows": int(len(df)),
            "quarantine_manifest_sha256": sha256_file(args.manifest),
            "date_min": str(df["game_date"].min()), "date_max": str(df["game_date"].max()),
            "opp_sp_source_counts": df["opp_sp_source"].value_counts().to_dict(),
        },
        "features": {"count": len(features), "names": features, "categorical": categorical},
        "runtime": {"python": sys.version, "platform": platform.platform()},
        "tasks": {},
    }
    selected: dict[str, dict[str, Any]] = {}
    for name, stat, threshold in TASKS:
        y = target(df, stat, threshold)
        baseline_select = eb_probability(df.loc[select], stat, threshold)
        capacity_scores: list[dict[str, Any]] = []
        for capacity in CAPACITIES:
            p = train_predict(X.loc[fit], y[fit], X.loc[select], categorical, capacity)
            capacity_scores.append({"capacity": capacity["name"], "metrics": metrics(y[select], p)})
        chosen = min(capacity_scores, key=lambda r: r["metrics"]["log_loss"])["capacity"]
        capacity = next(x for x in CAPACITIES if x["name"] == chosen)
        # Refit solely on fit+selection after choice; confirmation remains unseen.
        train_mask = fit | select
        p_confirm = train_predict(X.loc[train_mask], y[train_mask], X.loc[confirm], categorical, capacity)
        p_base_confirm = eb_probability(df.loc[confirm], stat, threshold)
        model_m = metrics(y[confirm], p_confirm)
        base_m = metrics(y[confirm], p_base_confirm)
        uncertainty = date_bootstrap_delta(
            df.loc[confirm, "game_date"].astype(str).to_numpy(), y[confirm], p_confirm, p_base_confirm
        )
        # Materiality is intentionally stricter than a point improvement.  This
        # is only the simple-baseline gate; production comparison remains open.
        survives_simple = (
            uncertainty["brier_delta_95_high"] < 0.0
            and uncertainty["log_loss_delta_95_high"] < 0.0
            and model_m["auc"] >= base_m["auc"]
            and model_m["ece_10"] <= base_m["ece_10"]
        )
        report["tasks"][name] = {
            "stat": stat, "threshold": threshold,
            "selection": {"capacity_scores": capacity_scores, "chosen_capacity": chosen,
                          "baseline_metrics": metrics(y[select], baseline_select)},
            "confirmation": {"candidate": model_m, "empirical_bayes_baseline": base_m,
                               "date_block_uncertainty": uncertainty,
                               "survives_simple_baseline_gate": bool(survives_simple)},
        }
        selected[name] = {"capacity": chosen, "survives_simple": survives_simple}
    report["summary"] = {
        "all_tasks_survive_simple_baseline_gate": bool(all(v["survives_simple"] for v in selected.values())),
        "selected_capacities": selected,
        "not_evaluated": [
            "production simulator comparison", "PA distribution coherence", "market prices or ROI",
            "May 2026", "policy fitting", "betting authorization",
        ],
    }
    report["protocol_sha256"] = sha256_json(report["protocol"])
    report["script_sha256"] = sha256_file(Path(__file__))
    write_atomic(args.output, report)
    print(json.dumps({"output": str(args.output), "summary": report["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
