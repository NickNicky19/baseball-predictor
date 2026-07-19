#!/usr/bin/env python3
"""Select and freeze one shared batter PA challenger using 2023-2024 only.

2025 is neither scored nor loaded into a selection frame.  The script stops
without a model whenever the shared challenger fails to beat the strongest
simple chronological baseline on both proper scores.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import platform
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import PA_OUTCOMES, sha256  # noqa: E402
from src.evaluation.shared_pa_benchmark_protocol import load_protocol  # noqa: E402
from src.evaluation.shared_pa_training_data import (  # noqa: E402
    feature_columns,
    outcome_counts,
    sanitize_point_in_time_features,
)
from src.learning.shared_pa_model import (  # noqa: E402
    fit_catboost,
    fit_rate_baseline,
    fit_temperature,
    paired_date_block_interval,
    proper_scores,
    temperature_scale,
)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_csv(temp, index=False, float_format="%.17g")
    os.replace(temp, path)


def load_selection_rows(source: Path, protocol: dict[str, Any]) -> pd.DataFrame:
    boundary = protocol["selection_input_boundary"]
    frame = pd.read_csv(
        source,
        nrows=int(boundary["maximum_rows_read"]),
        low_memory=False,
    )
    if set(frame["season"].astype(int)) != {2023, 2024}:
        raise ValueError("selection frame is not exactly 2023-2024")
    dates = pd.to_datetime(frame["game_date"], errors="raise")
    if (dates.dt.year >= int(boundary["forbid_any_loaded_year_at_or_after"])).any():
        raise ValueError("confirmation rows entered selection frame")
    actual_rows = {
        str(int(season)): int(count)
        for season, count in frame.groupby("season").size().items()
    }
    if actual_rows != boundary["expected_rows_by_season"]:
        raise ValueError("selection row boundary or season counts changed")
    if dates.min().date().isoformat() != boundary["expected_date_min"] or dates.max().date().isoformat() != boundary["expected_date_max"]:
        raise ValueError("selection date boundary changed")
    return frame


def interval_report(
    frame: pd.DataFrame,
    candidate: np.ndarray,
    baseline: np.ndarray,
    protocol: dict[str, Any],
) -> dict[str, Any]:
    counts = outcome_counts(frame).loc[:, PA_OUTCOMES]
    metrics = protocol["required_metrics"]
    return {
        metric: paired_date_block_interval(
            counts,
            candidate,
            baseline,
            frame["game_date"],
            metric="log_loss" if metric == "multiclass_log_loss" else "brier",
            draws=int(metrics["bootstrap_draws"]),
            seed=int(metrics["bootstrap_seed"]),
        )
        for metric in metrics["pa_primary"]
    }


def evaluate_simple_baselines(
    frame: pd.DataFrame, protocol: dict[str, Any]
) -> tuple[dict[str, Any], np.ndarray, pd.DataFrame]:
    records: dict[str, list[np.ndarray]] = {
        "league_rate": [], "lineup_slot_rate": [],
    }
    fallback: dict[str, list[np.ndarray]] = {
        "league_rate": [], "lineup_slot_rate": [],
    }
    prior_grid = protocol["simple_baseline_selection"]["empirical_bayes_prior_strength_pa_grid"]
    for prior in prior_grid:
        records[f"empirical_bayes_player_rate_pa_{prior}"] = []
        fallback[f"empirical_bayes_player_rate_pa_{prior}"] = []
    validation_parts: list[pd.DataFrame] = []
    dates = pd.to_datetime(frame["game_date"])
    for fold_number, (start, end) in enumerate(protocol["selection_folds"]):
        train = frame[dates < pd.Timestamp(start)]
        validation = frame[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
        if train.empty or validation.empty:
            raise ValueError(f"empty simple-baseline fold: {start}..{end}")
        validation = validation.copy()
        validation["_selection_fold"] = fold_number
        validation_parts.append(validation)
        for kind in ("league_rate", "lineup_slot_rate"):
            model = fit_rate_baseline(train, kind=kind)
            probabilities, flags = model.predict(validation)
            records[kind].append(probabilities)
            fallback[kind].append(flags)
        for prior in prior_grid:
            name = f"empirical_bayes_player_rate_pa_{prior}"
            model = fit_rate_baseline(
                train, kind="empirical_bayes_player_rate", prior_strength_pa=float(prior)
            )
            probabilities, flags = model.predict(validation)
            records[name].append(probabilities)
            fallback[name].append(flags)
    validation_frame = pd.concat(validation_parts, ignore_index=True)
    counts = outcome_counts(validation_frame).loc[:, PA_OUTCOMES]
    report: dict[str, Any] = {}
    best_name = ""
    best_key = (float("inf"), float("inf"))
    best_probabilities: np.ndarray | None = None
    for name, parts in records.items():
        probabilities = np.vstack(parts)
        scores = proper_scores(counts, probabilities)
        report[name] = {
            "scores": scores,
            "fallback_fraction": float(np.concatenate(fallback[name]).mean()),
        }
        key = (scores["multiclass_log_loss"], scores["multiclass_brier"])
        if key < best_key:
            best_name, best_key, best_probabilities = name, key, probabilities
    if best_probabilities is None:
        raise ValueError("no simple baseline was evaluated")
    report["selected"] = best_name
    return report, best_probabilities, validation_frame


def inner_split(train: pd.DataFrame, n_dates: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    unique_dates = sorted(train["game_date"].astype(str).unique())
    if len(unique_dates) <= n_dates:
        raise ValueError("insufficient dates for inner early-stopping split")
    boundary = unique_dates[-n_dates]
    fit = train[train["game_date"].astype(str) < boundary]
    validation = train[train["game_date"].astype(str) >= boundary]
    return fit, validation


def evaluate_candidate(
    frame: pd.DataFrame,
    protocol: dict[str, Any],
    *,
    variant_id: str,
    params: dict[str, Any],
    threads: int,
) -> tuple[np.ndarray, pd.DataFrame, list[int]]:
    features = feature_columns(protocol, variant_id)
    probability_parts: list[np.ndarray] = []
    validation_parts: list[pd.DataFrame] = []
    best_iterations: list[int] = []
    dates = pd.to_datetime(frame["game_date"])
    for fold_number, (start, end) in enumerate(protocol["selection_folds"]):
        outer_train = frame[dates < pd.Timestamp(start)]
        outer_validation = frame[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
        fit, early_stop = inner_split(
            outer_train,
            n_dates=int(protocol["model_family"]["inner_early_stopping"]["holdout_tail_official_dates"]),
        )
        model = fit_catboost(
            fit,
            features=features,
            params={**params, "thread_count": int(threads)},
            seed=int(protocol["model_family"]["random_seed"]) + fold_number,
            validation_frame=early_stop,
            early_stopping_rounds=int(protocol["model_family"]["bounded_grid"]["early_stopping_rounds"][0]),
        )
        probability_parts.append(model.predict_proba(outer_validation))
        outer_validation = outer_validation.copy()
        outer_validation["_selection_fold"] = fold_number
        validation_parts.append(outer_validation)
        best_iteration = int(model.model.get_best_iteration())
        best_iterations.append(best_iteration + 1 if best_iteration >= 0 else int(params["iterations"]))
    return np.vstack(probability_parts), pd.concat(validation_parts, ignore_index=True), best_iterations


def candidate_grid(protocol: dict[str, Any]) -> list[dict[str, Any]]:
    grid = protocol["model_family"]["bounded_grid"]
    return [
        {
            "depth": int(depth),
            "l2_leaf_reg": float(l2),
            "learning_rate": float(rate),
            "iterations": int(iterations),
        }
        for depth, l2, rate, iterations in itertools.product(
            grid["depth"], grid["l2_leaf_reg"], grid["learning_rate"], grid["iterations_max"]
        )
    ]


def cross_fitted_temperature_probabilities(
    frame: pd.DataFrame,
    probabilities: np.ndarray,
    protocol: dict[str, Any],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Apply each fold using a temperature fitted only on earlier OOF folds."""
    if "_selection_fold" not in frame:
        raise ValueError("selection-fold identity missing for calibration")
    calibration = protocol["calibration"]
    bounds = tuple(float(value) for value in calibration["log_temperature_bounds"])
    output = np.asarray(probabilities, dtype=float).copy()
    records: list[dict[str, Any]] = []
    folds = sorted(int(value) for value in frame["_selection_fold"].unique())
    if folds != list(range(len(protocol["selection_folds"]))):
        raise ValueError("calibration folds do not match locked selection folds")
    counts = outcome_counts(frame).loc[:, PA_OUTCOMES]
    for fold in folds:
        apply_mask = frame["_selection_fold"].to_numpy(dtype=int) == fold
        if fold == folds[0]:
            temperature = float(calibration["first_fold_temperature"])
            training_rows = 0
        else:
            train_mask = frame["_selection_fold"].to_numpy(dtype=int) < fold
            temperature = fit_temperature(
                counts.loc[train_mask].reset_index(drop=True),
                probabilities[train_mask],
                log_temperature_bounds=bounds,
            )
            training_rows = int(train_mask.sum())
        output[apply_mask] = temperature_scale(probabilities[apply_mask], temperature)
        records.append({
            "apply_fold": fold,
            "trained_on_prior_oof_rows": training_rows,
            "temperature": temperature,
        })
    return output, records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    protocol_path = ROOT / "config/shared_pa_benchmark_protocol.json"
    protocol = load_protocol(protocol_path, evidence_root=args.evidence_root)
    foundation = json.loads((ROOT / "config/multi_market_probability_foundation_protocol.json").read_text(encoding="utf-8"))
    source_record = foundation["training_inputs"]["corrected_hitters"]
    source = args.evidence_root / source_record["path"]
    if sha256(source) != source_record["sha256"]:
        raise ValueError("corrected hitter source hash changed")
    frame = load_selection_rows(source, protocol)
    frame, sanitization_report = sanitize_point_in_time_features(frame, protocol)

    simple_report, best_simple, simple_validation = evaluate_simple_baselines(frame, protocol)
    variants = [item["id"] for item in protocol["sequential_feature_variants"]]
    grid_reports: list[dict[str, Any]] = []
    best_core: tuple[float, float, dict[str, Any], np.ndarray, pd.DataFrame, list[int]] | None = None
    for params in candidate_grid(protocol):
        probabilities, validation, best_iterations = evaluate_candidate(
            frame, protocol, variant_id="core", params=params, threads=args.threads
        )
        if not validation[["game_pk", "player_id"]].equals(simple_validation[["game_pk", "player_id"]]):
            raise ValueError("candidate/simple validation identity mismatch")
        scores = proper_scores(outcome_counts(validation).loc[:, PA_OUTCOMES], probabilities)
        record = {"params": params, "scores": scores, "best_iterations": best_iterations}
        grid_reports.append(record)
        key = (scores["multiclass_log_loss"], scores["multiclass_brier"])
        if best_core is None or key < best_core[:2]:
            best_core = (*key, params, probabilities, validation, best_iterations)
    assert best_core is not None
    _, _, selected_params, selected_probabilities, selected_validation, selected_iterations = best_core

    selection_steps: list[dict[str, Any]] = []
    current_variant = "core"
    current_probabilities = selected_probabilities
    simple_intervals = interval_report(selected_validation, current_probabilities, best_simple, protocol)
    core_pass = all(item["upper"] < 0.0 for item in simple_intervals.values())
    selection_steps.append({
        "variant": "core", "comparison": "strongest_simple_baseline",
        "intervals": simple_intervals, "passed": core_pass,
    })
    if core_pass:
        for variant in variants[1:]:
            probabilities, validation, best_iterations = evaluate_candidate(
                frame, protocol, variant_id=variant, params=selected_params, threads=args.threads
            )
            if not validation[["game_pk", "player_id"]].equals(selected_validation[["game_pk", "player_id"]]):
                raise ValueError("sequential candidate identity mismatch")
            intervals = interval_report(validation, probabilities, current_probabilities, protocol)
            passed = all(item["upper"] < 0.0 for item in intervals.values())
            selection_steps.append({
                "variant": variant, "comparison": current_variant,
                "intervals": intervals, "passed": passed,
            })
            if not passed:
                break
            current_variant = variant
            current_probabilities = probabilities
            selected_iterations = best_iterations

    counts = outcome_counts(selected_validation).loc[:, PA_OUTCOMES]
    raw_scores = proper_scores(counts, current_probabilities)
    calibrated, calibration_fold_records = cross_fitted_temperature_probabilities(
        selected_validation, current_probabilities, protocol
    )
    calibration_scores = proper_scores(counts, calibrated)
    calibration_intervals = interval_report(selected_validation, calibrated, current_probabilities, protocol)
    calibration_installed = (
        core_pass
        and calibration_scores["multiclass_log_loss"] < raw_scores["multiclass_log_loss"]
        and calibration_scores["multiclass_brier"] < raw_scores["multiclass_brier"]
        and all(item["upper"] < 0.0 for item in calibration_intervals.values())
    )
    final_temperature = fit_temperature(
        counts,
        current_probabilities,
        log_temperature_bounds=tuple(protocol["calibration"]["log_temperature_bounds"]),
    )
    selected_temperature = final_temperature if calibration_installed else 1.0

    selection_oof = selected_validation.loc[:, [
        "game_pk", "player_id", "game_date", "lineup_slot", "_selection_fold"
    ]].copy()
    for index, outcome in enumerate(PA_OUTCOMES):
        selection_oof[f"actual_{outcome}"] = counts[outcome].to_numpy()
        selection_oof[f"best_simple_{outcome}"] = best_simple[:, index]
        selection_oof[f"candidate_raw_{outcome}"] = current_probabilities[:, index]
        selection_oof[f"candidate_crossfit_calibrated_{outcome}"] = calibrated[:, index]

    runtime_files = [
        Path(__file__),
        ROOT / "src/learning/shared_pa_model.py",
        ROOT / "src/evaluation/shared_pa_training_data.py",
        ROOT / "src/evaluation/shared_pa_benchmark_protocol.py",
        protocol_path,
        ROOT / "config/multi_market_probability_foundation_protocol.json",
    ]
    try:
        source_commit = subprocess.check_output(
            ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        source_commit = "UNAVAILABLE"

    selection_passed = core_pass
    report: dict[str, Any] = {
        "schema_version": "shared-pa-selection-report-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "SELECTION_PASSED_CANDIDATE_FROZEN" if selection_passed else "SELECTION_REJECTED_NO_CANDIDATE",
        "betting_authorized": False,
        "may_2026_opened": False,
        "confirmation_2025_opened": False,
        "selection_seasons": [2023, 2024],
        "source_commit": source_commit,
        "source": source_record,
        "historical_feature_sanitization": sanitization_report,
        "protocol": {"path": str(protocol_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(protocol_path)},
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "thread_count": int(args.threads),
            "file_hashes": {
                str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path)
                for path in runtime_files
            },
        },
        "simple_baselines": simple_report,
        "core_grid": grid_reports,
        "selected_params": selected_params,
        "selected_variant": current_variant if selection_passed else None,
        "selection_steps": selection_steps,
        "calibration": {
            "method": "single_temperature_multiclass",
            "raw_scores": raw_scores,
            "cross_fitted_fold_temperatures": calibration_fold_records,
            "final_temperature_fit_on_all_2024_oof": final_temperature,
            "candidate_scores": calibration_scores,
            "intervals": calibration_intervals,
            "installed": calibration_installed,
            "selected_temperature": selected_temperature,
        },
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    oof_path = args.out_dir / "selection_oof_predictions.csv"
    atomic_csv(oof_path, selection_oof)
    report["selection_oof"] = {
        "path": oof_path.name,
        "sha256": sha256(oof_path),
        "rows": int(len(selection_oof)),
        "seasons": [2024],
    }
    if selection_passed:
        features = feature_columns(protocol, current_variant)
        iterations = max(1, int(statistics.median(selected_iterations)))
        final_model = fit_catboost(
            frame,
            features=features,
            params={**selected_params, "iterations": iterations, "thread_count": int(args.threads)},
            seed=int(protocol["model_family"]["random_seed"]),
        )
        model_path = args.out_dir / "shared_pa_catboost.cbm"
        final_model.model.save_model(str(model_path))
        report["frozen_model"] = {
            "path": model_path.name,
            "sha256": sha256(model_path),
            "features": features,
            "categorical_features": final_model.categorical_features,
            "iterations": iterations,
            "temperature": selected_temperature,
            "trainer_sha256": sha256(Path(__file__)),
            "model_module_sha256": sha256(ROOT / "src/learning/shared_pa_model.py"),
        }
    atomic_json(args.out_dir / "selection_report.json", report)
    print(json.dumps({
        "status": report["status"],
        "selected_simple": simple_report["selected"],
        "selected_variant": report["selected_variant"],
        "calibration_installed": calibration_installed,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
