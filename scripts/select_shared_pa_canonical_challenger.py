#!/usr/bin/env python3
"""Select one canonical shared-PA challenger using 2023-2024 only."""
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

from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_canonical_selection import (  # noqa: E402
    clears_materiality,
    complexity_benefit_floor,
    feature_columns,
    load_protocol,
    load_selection_frame,
    sha256,
)
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.learning.shared_pa_model import (  # noqa: E402
    binary_class_metrics,
    derived_market_probabilities,
    fit_catboost,
    fit_temperature,
    proper_scores,
)
from scripts.select_shared_pa_challenger import (  # noqa: E402
    cross_fitted_temperature_probabilities,
    evaluate_simple_baselines,
    inner_split,
    interval_report,
)


EPSILON = 1e-12


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_csv(temporary, index=False, float_format="%.17g")
    os.replace(temporary, path)


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


def evaluate_candidate(
    frame: pd.DataFrame,
    protocol: dict[str, Any],
    *,
    variant_id: str,
    params: dict[str, Any],
    threads: int,
    base_seed: int,
) -> tuple[np.ndarray, pd.DataFrame, list[int]]:
    features = feature_columns(protocol, variant_id)
    probabilities: list[np.ndarray] = []
    validations: list[pd.DataFrame] = []
    iterations: list[int] = []
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise")
    holdout_dates = int(protocol["model_family"]["inner_early_stopping"]["holdout_tail_official_dates"])
    early_stopping = int(protocol["model_family"]["bounded_grid"]["early_stopping_rounds"][0])
    for fold_number, (start, end) in enumerate(protocol["selection_folds"]):
        outer_train = frame[dates < pd.Timestamp(start)]
        outer_validation = frame[
            (dates >= pd.Timestamp(start))
            & (dates <= pd.Timestamp(end))
            & (pd.to_numeric(frame["out_pa"], errors="raise") > 0)
        ]
        if outer_train.empty or outer_validation.empty:
            raise ValueError(f"canonical candidate fold is empty: {start}..{end}")
        fit, early_stop = inner_split(outer_train, holdout_dates)
        model = fit_catboost(
            fit,
            features=features,
            params={**params, "thread_count": int(threads)},
            seed=int(base_seed) + fold_number,
            validation_frame=early_stop,
            early_stopping_rounds=early_stopping,
        )
        probabilities.append(model.predict_proba(outer_validation))
        validation = outer_validation.copy()
        validation["_selection_fold"] = fold_number
        validations.append(validation)
        best = int(model.model.get_best_iteration())
        iterations.append(best + 1 if best >= 0 else int(params["iterations"]))
    return np.vstack(probabilities), pd.concat(validations, ignore_index=True), iterations


def binary_scores(actual: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    y = np.asarray(actual, dtype=float)
    p = np.clip(np.asarray(probability, dtype=float), EPSILON, 1.0 - EPSILON)
    return {
        "brier": float(np.square(p - y).mean()),
        "log_loss": float(-(y * np.log(p) + (1.0 - y) * np.log1p(-p)).mean()),
    }


def binary_date_interval(
    actual: np.ndarray,
    candidate: np.ndarray,
    baseline: np.ndarray,
    dates: pd.Series,
    *,
    metric: str,
    draws: int,
    seed: int,
) -> dict[str, float | int]:
    y = np.asarray(actual, dtype=float)
    cp = np.clip(np.asarray(candidate, dtype=float), EPSILON, 1.0 - EPSILON)
    bp = np.clip(np.asarray(baseline, dtype=float), EPSILON, 1.0 - EPSILON)
    if metric == "brier":
        delta = np.square(cp - y) - np.square(bp - y)
    elif metric == "log_loss":
        delta = -(y * np.log(cp) + (1.0 - y) * np.log1p(-cp)) + (y * np.log(bp) + (1.0 - y) * np.log1p(-bp))
    else:
        raise ValueError("unknown binary interval metric")
    block = pd.DataFrame({
        "date": pd.to_datetime(dates, format="%Y-%m-%d", errors="raise").dt.date,
        "delta": delta,
        "rows": 1,
    }).groupby("date", as_index=False).sum()
    rng = np.random.default_rng(int(seed))
    samples = np.empty(int(draws), dtype=float)
    values = block["delta"].to_numpy(float)
    rows = block["rows"].to_numpy(float)
    for draw in range(int(draws)):
        selected = rng.integers(0, len(block), size=len(block))
        samples[draw] = values[selected].sum() / rows[selected].sum()
    lower, upper = np.quantile(samples, [0.025, 0.975])
    return {
        "point": float(delta.mean()),
        "lower": float(lower),
        "upper": float(upper),
        "dates": int(len(block)),
        "draws": int(draws),
    }


def derived_diagnostics(
    frame: pd.DataFrame,
    candidate: np.ndarray,
    baseline: np.ndarray,
    protocol: dict[str, Any],
    pa_distribution: dict[str, dict[str, float]],
) -> dict[str, Any]:
    candidate_market = derived_market_probabilities(candidate, frame["lineup_slot"], pa_distribution)
    baseline_market = derived_market_probabilities(baseline, frame["lineup_slot"], pa_distribution)
    singles = frame["out_hits"] - frame["out_doubles"] - frame["out_triples"] - frame["out_hr"]
    total_bases = singles + 2 * frame["out_doubles"] + 3 * frame["out_triples"] + 4 * frame["out_hr"]
    actual: dict[str, np.ndarray] = {
        "hits_0.5": (frame["out_hits"].to_numpy() >= 1).astype(int),
        "hits_1.5": (frame["out_hits"].to_numpy() >= 2).astype(int),
        "home_runs_0.5": (frame["out_hr"].to_numpy() >= 1).astype(int),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
        actual[f"total_bases_{line}"] = (total_bases.to_numpy() > line).astype(int)
    required = protocol["derived_market_diagnostics"]["markets"]
    if set(required) != set(actual) or set(required) != set(candidate_market):
        raise ValueError("canonical derived-market diagnostic set changed")
    metrics = protocol["required_metrics"]
    report: dict[str, Any] = {}
    for index, market in enumerate(required):
        report[market] = {
            "candidate": binary_scores(actual[market], candidate_market[market]),
            "strongest_simple": binary_scores(actual[market], baseline_market[market]),
            "candidate_minus_simple_interval": {
                score: binary_date_interval(
                    actual[market], candidate_market[market], baseline_market[market],
                    frame["game_date"], metric=score,
                    draws=int(metrics["bootstrap_draws"]),
                    seed=int(metrics["bootstrap_seed"]) + index,
                )
                for score in ("brier", "log_loss")
            },
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "config/shared_pa_canonical_selection_protocol.json",
    )
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    protocol_path = args.protocol.resolve()
    protocol = load_protocol(protocol_path, evidence_root=evidence_root, code_root=ROOT)
    frame = load_selection_frame(protocol, evidence_root=evidence_root)
    simple_report, best_simple, simple_validation = evaluate_simple_baselines(frame, protocol)
    floor = complexity_benefit_floor(simple_report)
    primary_seed = int(protocol["model_family"]["random_seed"])
    grid_report: list[dict[str, Any]] = []
    best: tuple[float, float, dict[str, Any], np.ndarray, pd.DataFrame, list[int]] | None = None
    for params in candidate_grid(protocol):
        probabilities, validation, best_iterations = evaluate_candidate(
            frame, protocol, variant_id="canonical_hitter_46d", params=params,
            threads=args.threads, base_seed=primary_seed,
        )
        if not validation[["game_pk", "player_id"]].equals(simple_validation[["game_pk", "player_id"]]):
            raise ValueError("canonical candidate/simple identity differs")
        scores = proper_scores(outcome_counts(validation).loc[:, PA_OUTCOMES], probabilities)
        grid_report.append({"params": params, "scores": scores, "best_iterations": best_iterations})
        key = (scores["multiclass_log_loss"], scores["multiclass_brier"])
        if best is None or key < best[:2]:
            best = (*key, params, probabilities, validation, best_iterations)
    assert best is not None
    _, _, selected_params, selected_probabilities, selected_validation, selected_iterations = best
    core_intervals = interval_report(selected_validation, selected_probabilities, best_simple, protocol)
    core_passed, materiality_decisions = clears_materiality(core_intervals, floor)

    stability: list[dict[str, Any]] = []
    stability_passed = False
    if core_passed:
        simple_scores = simple_report[simple_report["selected"]]["scores"]
        stability_passed = True
        for seed in protocol["model_family"]["stability_audit_seeds"]:
            probability, validation, iterations = evaluate_candidate(
                frame, protocol, variant_id="canonical_hitter_46d",
                params=selected_params, threads=args.threads, base_seed=int(seed),
            )
            scores = proper_scores(outcome_counts(validation).loc[:, PA_OUTCOMES], probability)
            passed = all(scores[metric] < simple_scores[metric] for metric in protocol["materiality"]["primary_metrics"])
            stability.append({"seed": int(seed), "scores": scores, "best_iterations": iterations, "passed": passed})
            stability_passed = stability_passed and passed

    selected_variant = "canonical_hitter_46d"
    selected_probability = selected_probabilities
    context_step: dict[str, Any] = {"evaluated": False, "installed": False}
    if core_passed and stability_passed:
        context_probability, context_validation, context_iterations = evaluate_candidate(
            frame, protocol, variant_id="canonical_hitter_46d_context",
            params=selected_params, threads=args.threads, base_seed=primary_seed,
        )
        context_intervals = interval_report(context_validation, context_probability, selected_probability, protocol)
        context_passed = all(item["upper"] < 0.0 for item in context_intervals.values())
        context_step = {
            "evaluated": True,
            "intervals_vs_core": context_intervals,
            "best_iterations": context_iterations,
            "installed": context_passed,
        }
        if context_passed:
            selected_variant = "canonical_hitter_46d_context"
            selected_probability = context_probability
            selected_iterations = context_iterations

    counts = outcome_counts(selected_validation).loc[:, PA_OUTCOMES]
    raw_scores = proper_scores(counts, selected_probability)
    calibrated, calibration_folds = cross_fitted_temperature_probabilities(
        selected_validation, selected_probability, protocol
    )
    calibration_scores = proper_scores(counts, calibrated)
    calibration_intervals = interval_report(selected_validation, calibrated, selected_probability, protocol)
    calibration_installed = (
        core_passed and stability_passed
        and all(calibration_scores[metric] < raw_scores[metric] for metric in protocol["materiality"]["primary_metrics"])
        and all(item["upper"] < 0.0 for item in calibration_intervals.values())
    )
    selected_oof_probability = calibrated if calibration_installed else selected_probability
    selected_temperature = (
        fit_temperature(
            counts,
            selected_probability,
            log_temperature_bounds=tuple(protocol["calibration"]["log_temperature_bounds"]),
        )
        if calibration_installed else 1.0
    )
    selection_passed = bool(core_passed and stability_passed)
    pa_distribution_payload = json.loads((evidence_root / protocol["inputs"]["pa_distribution"]["path"]).read_text(encoding="utf-8"))
    derived = derived_diagnostics(
        selected_validation, selected_oof_probability, best_simple, protocol,
        pa_distribution_payload["by_lineup_slot"],
    )

    out_dir = args.out_dir.resolve()
    oof = selected_validation[["game_pk", "player_id", "game_date", "lineup_slot", "_selection_fold"]].copy()
    for index, outcome in enumerate(PA_OUTCOMES):
        oof[f"actual_{outcome}"] = counts[outcome].to_numpy()
        oof[f"strongest_simple_{outcome}"] = best_simple[:, index]
        oof[f"canonical_raw_{outcome}"] = selected_probability[:, index]
        oof[f"canonical_selected_{outcome}"] = selected_oof_probability[:, index]
    oof_path = out_dir / "selection_oof.csv"
    atomic_csv(oof_path, oof)

    model_record: dict[str, Any] | None = None
    if selection_passed:
        final_iterations = max(1, int(round(statistics.median(selected_iterations))))
        final_model = fit_catboost(
            frame,
            features=feature_columns(protocol, selected_variant),
            params={**selected_params, "iterations": final_iterations, "thread_count": int(args.threads)},
            seed=primary_seed,
        )
        model_path = out_dir / "shared_pa_model.cbm"
        out_dir.mkdir(parents=True, exist_ok=True)
        temporary_model = model_path.with_name(f".{model_path.name}.{os.getpid()}.tmp")
        final_model.model.save_model(str(temporary_model), format="cbm")
        os.replace(temporary_model, model_path)
        model_record = {
            "path": str(model_path), "sha256": sha256(model_path),
            "features": feature_columns(protocol, selected_variant),
            "params": {**selected_params, "iterations": final_iterations},
            "temperature": float(selected_temperature),
        }

    runtime_files = [
        Path(__file__),
        ROOT / "src/evaluation/shared_pa_canonical_selection.py",
        ROOT / "src/features/canonical_pa_features.py",
        ROOT / "src/learning/shared_pa_model.py",
        ROOT / "scripts/select_shared_pa_challenger.py",
        protocol_path,
        ROOT / protocol["inputs"]["runtime_feature_contract"]["path"],
    ]
    source_commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    report = {
        "schema_version": "shared-pa-canonical-selection-report-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "SELECTION_PASSED_CANDIDATE_FROZEN" if selection_passed else "SELECTION_REJECTED_NO_CANDIDATE",
        "betting_authorized": False,
        "production_changed": False,
        "confirmation_2025_opened": False,
        "may_2026_opened": False,
        "selection_seasons": [2023, 2024],
        "source_commit": source_commit,
        "protocol": {"path": str(protocol_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(protocol_path)},
        "inputs": protocol["inputs"],
        "runtime": {
            "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
            "thread_count": int(args.threads),
            "file_hashes": {str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path) for path in runtime_files},
        },
        "population": {
            "source_rows": int(len(frame)),
            "selection_rows_scored": int(len(selected_validation)),
            "selection_dates": int(selected_validation["game_date"].nunique()),
            "zero_outcome_pa_source_rows": int((pd.to_numeric(frame["out_pa"]) == 0).sum()),
        },
        "simple_baselines": simple_report,
        "complexity_benefit_floor": floor,
        "grid": grid_report,
        "selected_params": selected_params,
        "core_intervals_vs_strongest_simple": core_intervals,
        "materiality_decisions": materiality_decisions,
        "core_passed": bool(core_passed),
        "stability_audit": stability,
        "stability_passed": bool(stability_passed),
        "context_step": context_step,
        "selected_variant": selected_variant,
        "raw_scores": raw_scores,
        "per_class_raw": binary_class_metrics(counts, selected_probability),
        "calibration": {
            "folds": calibration_folds,
            "raw_scores": raw_scores,
            "crossfit_scores": calibration_scores,
            "intervals": calibration_intervals,
            "installed": bool(calibration_installed),
            "frozen_temperature": float(selected_temperature),
        },
        "selected_scores": proper_scores(counts, selected_oof_probability),
        "per_class_selected": binary_class_metrics(counts, selected_oof_probability),
        "derived_market_diagnostics": derived,
        "oof_artifact": {"path": str(oof_path), "sha256": sha256(oof_path), "rows": int(len(oof))},
        "model_artifact": model_record,
        "protected_invariants": {
            "confirmation_2025_unread": True,
            "may_2026_unread": True,
            "opposing_pitcher_features_quarantined": True,
            "no_league_fallback_imputation": True,
            "production_unchanged": True,
        },
    }
    report_path = out_dir / "selection_report.json"
    atomic_json(report_path, report)
    print(report["status"])
    print(f"core_passed: {core_passed}")
    print(f"stability_passed: {stability_passed}")
    print(f"selected_variant: {selected_variant}")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
