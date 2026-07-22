#!/usr/bin/env python3
"""Run the locked 2023-only chronological composition experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts.select_direct_batter_pa_foundation import (  # noqa: E402
    _calibration, _component, _paired_component_interval, _scores,
)
from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.learning.shared_pa_model import fit_catboost, fit_rate_baseline, proper_scores  # noqa: E402


SEED = 20260721
PARAMS = {"depth": 4, "iterations": 1200, "l2_leaf_reg": 10.0, "learning_rate": 0.03, "thread_count": 2}
NEW_COMPOSITION = [
    "history_hard_hit_non_barrel_count",
    "history_other_measured_bbe_count",
    "history_barrel_share_bbe",
    "history_hard_hit_non_barrel_share_bbe",
    "history_other_measured_bbe_share_bbe",
    "history_measured_bbe_per_pa",
]
OVERLAPPING_REMOVED = [
    "history_barrel_count", "history_hard_hit_count",
    "history_barrel_rate", "history_hard_hit_rate",
]
COMPONENTS = ("hr_pa_event", "hits_pa_event")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite 2023 development evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def feature_sets(columns: list[str]) -> tuple[list[str], list[str]]:
    history = [name for name in columns if name.startswith("history_")]
    recency = [name for name in columns if name.startswith("days_since_")]
    missing = sorted(set(NEW_COMPOSITION + OVERLAPPING_REMOVED).difference(columns))
    if missing:
        raise ValueError(f"locked feature columns missing: {missing}")
    legacy = ["player_id", *[name for name in history if name not in NEW_COMPOSITION], *recency]
    composition = [name for name in legacy if name not in OVERLAPPING_REMOVED] + NEW_COMPOSITION
    if set(OVERLAPPING_REMOVED).intersection(composition):
        raise AssertionError("overlapping batted-ball fields entered composition candidate")
    if not set(NEW_COMPOSITION).issubset(composition):
        raise AssertionError("composition candidate is incomplete")
    return legacy, composition


def top_decile(counts: np.ndarray, probability: np.ndarray) -> dict[str, float | int]:
    exposure = counts.sum(axis=1).astype(float)
    positive = counts[:, 1].astype(float)
    p = probability[:, 1].astype(float)
    threshold = float(np.quantile(p, 0.90))
    mask = p >= threshold
    total = float(exposure[mask].sum())
    predicted = float((p[mask] * exposure[mask]).sum() / total)
    observed = float(positive[mask].sum() / total)
    return {
        "threshold_from_predictions_only": threshold,
        "rows": int(mask.sum()), "pa": int(total),
        "predicted": predicted, "observed": observed,
        "gap": observed - predicted, "absolute_gap": abs(observed - predicted),
    }


def calibration_gate(counts: np.ndarray, candidate: np.ndarray, legacy: np.ndarray) -> dict[str, Any]:
    candidate_cal = _calibration(counts, candidate)["1"]
    legacy_cal = _calibration(counts, legacy)["1"]
    candidate_tail = top_decile(counts, candidate)
    legacy_tail = top_decile(counts, legacy)
    gates = {
        "intercept": abs(candidate_cal["intercept"]) <= abs(legacy_cal["intercept"]),
        "slope": abs(candidate_cal["slope"] - 1.0) <= abs(legacy_cal["slope"] - 1.0),
        "top_decile": candidate_tail["absolute_gap"] <= legacy_tail["absolute_gap"],
    }
    return {
        "candidate": {"logistic": candidate_cal, "top_decile": candidate_tail},
        "legacy": {"logistic": legacy_cal, "top_decile": legacy_tail},
        "gates": gates, "passed": all(gates.values()),
    }


def run(*, panel: Path, manifest: Path, protocol: Path, calibration_gate_path: Path, report: Path, predictions: Path) -> dict[str, Any]:
    if report.exists() or predictions.exists():
        raise FileExistsError("refusing to overwrite 2023 fold evidence")
    source = json.loads(manifest.read_text(encoding="utf-8"))
    contract = json.loads(protocol.read_text(encoding="utf-8"))
    calibration_contract = json.loads(calibration_gate_path.read_text(encoding="utf-8"))
    if source.get("status") != "CERTIFIED_2023_ONLY_DEVELOPMENT_RESEARCH_ONLY":
        raise ValueError("2023 development panel is not certified")
    if source.get("output", {}).get("sha256") != sha256_file(panel):
        raise ValueError("2023 development panel hash mismatch")
    if source.get("protocol", {}).get("sha256") != sha256_file(protocol):
        raise ValueError("2023 development protocol drifted after extraction")
    if contract.get("status") != "LOCKED_BEFORE_2023_EXTRACTION_OR_FOLD_SCORING":
        raise ValueError("2023 development protocol is not locked")
    if calibration_contract.get("status") != "LOCKED_BEFORE_ANY_2023_FOLD_SCORING":
        raise ValueError("calibration gate is not locked")
    if calibration_contract.get("parent_protocol_sha256") != sha256_file(protocol):
        raise ValueError("calibration gate parent protocol mismatch")

    frame = pd.read_csv(panel, low_memory=False)
    years = pd.to_numeric(frame["season"], errors="raise").astype(int)
    if not years.eq(2023).all():
        raise ValueError("development panel contains a non-2023 row")
    eligible = outcome_counts(frame).sum(axis=1).gt(0)
    frame = frame.loc[eligible].reset_index(drop=True)
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise")
    unique_dates = np.asarray(sorted(dates.dt.normalize().unique()))
    splitter = TimeSeriesSplit(n_splits=4)
    legacy_features, composition_features = feature_sets(frame.columns.tolist())
    oof_parts: list[pd.DataFrame] = []
    fold_reports: list[dict[str, Any]] = []

    for fold_number, (train_date_index, valid_date_index) in enumerate(splitter.split(unique_dates), 1):
        train_dates = set(unique_dates[train_date_index])
        valid_dates = set(unique_dates[valid_date_index])
        train = frame.loc[dates.dt.normalize().isin(train_dates)].copy()
        valid = frame.loc[dates.dt.normalize().isin(valid_dates)].copy()
        if train["game_date"].max() >= valid["game_date"].min():
            raise ValueError("fold chronology overlap")
        legacy_model = fit_catboost(train, features=legacy_features, params=PARAMS, seed=SEED, categorical_features=["player_id"])
        candidate_model = fit_catboost(train, features=composition_features, params=PARAMS, seed=SEED, categorical_features=["player_id"])
        p_legacy = legacy_model.predict_proba(valid)
        p_candidate = candidate_model.predict_proba(valid)
        league_model = fit_rate_baseline(train, kind="league_rate")
        eb_model = fit_rate_baseline(train, kind="empirical_bayes_player_rate", prior_strength_pa=200.0)
        p_league, _ = league_model.predict(valid)
        p_eb, eb_fallback = eb_model.predict(valid)
        fold_row: dict[str, Any] = {
            "fold": fold_number,
            "train_start": str(train["game_date"].min()), "train_end": str(train["game_date"].max()),
            "validation_start": str(valid["game_date"].min()), "validation_end": str(valid["game_date"].max()),
            "train_rows": len(train), "validation_rows": len(valid),
            "eb_unseen_player_fraction": float(eb_fallback.mean()), "components": {},
        }
        for component in COMPONENTS:
            component_counts, candidate_component = _component(valid, p_candidate, component)
            _, legacy_component = _component(valid, p_legacy, component)
            candidate_score = _scores(component_counts, candidate_component)
            legacy_score = _scores(component_counts, legacy_component)
            gates = {
                "brier_improved": candidate_score["brier"] < legacy_score["brier"],
                "log_loss_improved": candidate_score["log_loss"] < legacy_score["log_loss"],
                "auc_noninferior": candidate_score["auc"] >= legacy_score["auc"],
            }
            fold_row["components"][component] = {
                "candidate": candidate_score, "legacy_overlap": legacy_score,
                "gates": gates, "passed": all(gates.values()),
            }
        fold_reports.append(fold_row)
        part = valid[["game_pk", "player_id", "game_date"]].copy()
        part["fold"] = fold_number
        for label, probability in (("candidate", p_candidate), ("legacy", p_legacy), ("league", p_league), ("eb200", p_eb)):
            for index, outcome in enumerate(PA_OUTCOMES):
                part[f"{label}_{outcome}"] = probability[:, index]
        oof_parts.append(part)

    oof = pd.concat(oof_parts, ignore_index=True)
    scored = frame.merge(oof, on=["game_pk", "player_id", "game_date"], how="inner", validate="one_to_one")
    if len(scored) != len(oof):
        raise ValueError("OOF identity coverage mismatch")
    probability_matrices = {
        label: scored[[f"{label}_{outcome}" for outcome in PA_OUTCOMES]].to_numpy(float)
        for label in ("candidate", "legacy", "league", "eb200")
    }
    aggregate_components: dict[str, Any] = {}
    market_passes: dict[str, bool] = {}
    for component in COMPONENTS:
        counts, candidate_component = _component(scored, probability_matrices["candidate"], component)
        _, legacy_component = _component(scored, probability_matrices["legacy"], component)
        row: dict[str, Any] = {
            "candidate": _scores(counts, candidate_component),
            "comparators": {},
            "calibration": calibration_gate(counts, candidate_component, legacy_component),
        }
        all_material = True
        for label in ("legacy", "league", "eb200"):
            _, comparator_component = _component(scored, probability_matrices[label], component)
            comparator_score = _scores(counts, comparator_component)
            interval = _paired_component_interval(counts, candidate_component, comparator_component, scored["game_date"])
            gates = {}
            for metric in ("brier", "log_loss"):
                required = -0.01 * comparator_score[metric]
                passed = interval[metric]["point"] < required and interval[metric]["upper"] < required
                gates[metric] = {"required_below": required, "passed": passed}
                all_material = all_material and passed
            auc_noninferior = row["candidate"]["auc"] >= comparator_score["auc"]
            all_material = all_material and auc_noninferior
            row["comparators"][label] = {
                "scores": comparator_score, "paired_interval": interval,
                "materiality": gates, "auc_noninferior": auc_noninferior,
            }
        fold_consistent = all(fold["components"][component]["passed"] for fold in fold_reports)
        passed = all_material and fold_consistent and row["calibration"]["passed"]
        row["fold_consistency_passed"] = fold_consistent
        row["all_materiality_and_auc_passed"] = all_material
        row["development_survivor"] = passed
        aggregate_components[component] = row
        market_passes[component] = passed

    output_predictions = oof.sort_values(["game_date", "game_pk", "player_id"], kind="stable")
    atomic(predictions, output_predictions.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    result: dict[str, Any] = {
        "schema_version": "direct-batter-pa-2023-development-report-v1",
        "status": "DEVELOPMENT_SURVIVOR_REQUIRES_PROSPECTIVE_LOCK" if any(market_passes.values()) else "DEVELOPMENT_REJECTED_NO_SURVIVOR",
        "scope": "2023 internal chronological development only; not selection, confirmation, market evaluation, or promotion",
        "inputs": {
            "panel": {"path": str(panel), "sha256": sha256_file(panel), "rows": len(frame)},
            "manifest_sha256": sha256_file(manifest), "protocol_sha256": sha256_file(protocol),
            "calibration_gate_sha256": sha256_file(calibration_gate_path),
        },
        "features": {"legacy_overlap": legacy_features, "composition_candidate": composition_features},
        "model": {"params": PARAMS, "seed": SEED},
        "folds": fold_reports,
        "oof_population": {"rows": len(oof), "dates": int(scored["game_date"].nunique()), "coverage_loss": 0},
        "overall_multiclass": {
            label: proper_scores(outcome_counts(scored), probability)
            for label, probability in probability_matrices.items()
        },
        "components": aggregate_components,
        "market_passes": market_passes,
        "predictions": {"path": str(predictions), "sha256": sha256_file(predictions), "rows": len(output_predictions)},
        "chronology": {"2024_opened": False, "2025_confirmation_opened": False, "may_2026_opened": False},
        "next_boundary": "A survivor may only be locked prospectively; these 2023 folds cannot promote production or authorize a market.",
        "production_changed": False, "betting_authorized": False,
        "script_sha256": sha256_file(Path(__file__)),
    }
    atomic(report, (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--calibration-gate-path", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    args = parser.parse_args()
    result = run(**vars(args))
    print(json.dumps({"status": result["status"], "market_passes": result["market_passes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
