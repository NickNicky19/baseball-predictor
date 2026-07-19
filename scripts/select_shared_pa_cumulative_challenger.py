#!/usr/bin/env python3
"""Select the one predeclared cumulative-history shared-PA challenger."""
from __future__ import annotations

import argparse
import json
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

from scripts.select_shared_pa_canonical_challenger import (  # noqa: E402
    atomic_csv,
    atomic_json,
    candidate_grid,
    derived_diagnostics,
    evaluate_candidate,
)
from scripts.select_shared_pa_challenger import (  # noqa: E402
    cross_fitted_temperature_probabilities,
    evaluate_simple_baselines,
    interval_report,
)
from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_canonical_selection import (  # noqa: E402
    complexity_benefit_floor,
    feature_columns,
    sha256,
)
from src.evaluation.shared_pa_cumulative_selection import (  # noqa: E402
    aligned_v1_probabilities,
    assert_expected_simple,
    clears_all_comparisons,
    load_protocol,
    load_bound_pa_distribution,
    load_retry_authorization,
    load_selection_frame,
    load_v1_comparator,
)
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.learning.shared_pa_model import fit_catboost, fit_temperature, proper_scores  # noqa: E402


PRIMARY_METRICS = ("multiclass_log_loss", "multiclass_brier")


def same_identity(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    columns = ["game_pk", "player_id", "game_date", "lineup_slot", "_selection_fold"]
    return left[columns].reset_index(drop=True).equals(right[columns].reset_index(drop=True))


def stability_audit(
    frame: pd.DataFrame,
    protocol: dict[str, Any],
    *,
    variant_id: str,
    params: dict[str, Any],
    simple_scores: dict[str, float],
    reference_validation: pd.DataFrame,
    threads: int,
) -> tuple[bool, list[dict[str, Any]], dict[int, np.ndarray]]:
    records: list[dict[str, Any]] = []
    probabilities: dict[int, np.ndarray] = {}
    passed_all = True
    for seed in protocol["model_family"]["stability_audit_seeds"]:
        probability, validation, iterations = evaluate_candidate(
            frame,
            protocol,
            variant_id=variant_id,
            params=params,
            threads=threads,
            base_seed=int(seed),
        )
        if not same_identity(validation, reference_validation):
            raise ValueError("cumulative stability identity differs")
        scores = proper_scores(outcome_counts(validation).loc[:, PA_OUTCOMES], probability)
        passed = all(float(scores[metric]) < float(simple_scores[metric]) for metric in PRIMARY_METRICS)
        records.append({
            "seed": int(seed),
            "scores": scores,
            "best_iterations": iterations,
            "passed_vs_strongest_simple": bool(passed),
        })
        probabilities[int(seed)] = probability
        passed_all = passed_all and passed
    return bool(passed_all), records, probabilities


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "config/shared_pa_cumulative_selection_protocol.json",
    )
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    protocol_path = args.protocol.resolve()
    protocol, base_protocol = load_protocol(protocol_path, evidence_root=evidence_root, code_root=ROOT)
    retry_path = ROOT / "config/shared_pa_cumulative_selection_retry_v2.json"
    load_retry_authorization(retry_path, code_root=ROOT)
    frame = load_selection_frame(protocol, base_protocol, evidence_root=evidence_root)

    simple_report, best_simple, simple_validation = evaluate_simple_baselines(frame, protocol)
    assert_expected_simple(simple_report, protocol)
    simple_scores = simple_report[simple_report["selected"]]["scores"]
    floor = complexity_benefit_floor(simple_report)
    v1_comparator = load_v1_comparator(protocol, evidence_root=evidence_root)

    primary_seed = int(protocol["model_family"]["random_seed"])
    grid_records: list[dict[str, Any]] = []
    best: tuple[float, float, dict[str, Any], np.ndarray, pd.DataFrame, list[int]] | None = None
    for params in candidate_grid(protocol):
        probability, validation, iterations = evaluate_candidate(
            frame,
            protocol,
            variant_id="canonical_recent_plus_cumulative",
            params=params,
            threads=args.threads,
            base_seed=primary_seed,
        )
        if not same_identity(validation, simple_validation):
            raise ValueError("cumulative candidate/simple identity differs")
        scores = proper_scores(outcome_counts(validation).loc[:, PA_OUTCOMES], probability)
        grid_records.append({"params": params, "scores": scores, "best_iterations": iterations})
        key = (float(scores["multiclass_log_loss"]), float(scores["multiclass_brier"]))
        if best is None or key < best[:2]:
            best = (*key, params, probability, validation, iterations)
    if best is None:
        raise ValueError("cumulative bounded grid produced no candidate")

    _, _, selected_params, core_probability, selected_validation, selected_iterations = best
    v1_probability = aligned_v1_probabilities(selected_validation, v1_comparator)
    core_simple_intervals = interval_report(selected_validation, core_probability, best_simple, protocol)
    core_v1_intervals = interval_report(selected_validation, core_probability, v1_probability, protocol)
    core_passed, core_decisions = clears_all_comparisons(
        simple_intervals=core_simple_intervals,
        simple_floor=floor,
        v1_intervals=core_v1_intervals,
    )

    core_stability_passed = False
    core_stability: list[dict[str, Any]] = []
    core_stability_probabilities: dict[int, np.ndarray] = {}
    if core_passed:
        core_stability_passed, core_stability, core_stability_probabilities = stability_audit(
            frame,
            protocol,
            variant_id="canonical_recent_plus_cumulative",
            params=selected_params,
            simple_scores=simple_scores,
            reference_validation=selected_validation,
            threads=args.threads,
        )

    selected_variant = "canonical_recent_plus_cumulative"
    raw_selected_probability = core_probability
    selected_stability_passed = core_stability_passed
    context_step: dict[str, Any] = {
        "evaluated": False,
        "incremental_passed": False,
        "stability_passed": False,
        "installed": False,
    }
    context_probability: np.ndarray | None = None
    context_stability_probabilities: dict[int, np.ndarray] = {}
    if core_passed and core_stability_passed:
        context_probability, context_validation, context_iterations = evaluate_candidate(
            frame,
            protocol,
            variant_id="canonical_recent_plus_cumulative_context",
            params=selected_params,
            threads=args.threads,
            base_seed=primary_seed,
        )
        if not same_identity(context_validation, selected_validation):
            raise ValueError("cumulative context identity differs")
        context_intervals = interval_report(context_validation, context_probability, core_probability, protocol)
        context_incremental_passed = all(float(item["upper"]) < 0.0 for item in context_intervals.values())
        context_stability_passed = False
        context_stability: list[dict[str, Any]] = []
        if context_incremental_passed:
            context_stability_passed, context_stability, context_stability_probabilities = stability_audit(
                frame,
                protocol,
                variant_id="canonical_recent_plus_cumulative_context",
                params=selected_params,
                simple_scores=simple_scores,
                reference_validation=selected_validation,
                threads=args.threads,
            )
        installed = bool(context_incremental_passed and context_stability_passed)
        context_step = {
            "evaluated": True,
            "intervals_vs_core": context_intervals,
            "best_iterations": context_iterations,
            "incremental_passed": bool(context_incremental_passed),
            "stability_audit": context_stability,
            "stability_passed": bool(context_stability_passed),
            "installed": installed,
        }
        if installed:
            selected_variant = "canonical_recent_plus_cumulative_context"
            raw_selected_probability = context_probability
            selected_iterations = context_iterations
            selected_stability_passed = context_stability_passed

    raw_simple_intervals = interval_report(selected_validation, raw_selected_probability, best_simple, protocol)
    raw_v1_intervals = interval_report(selected_validation, raw_selected_probability, v1_probability, protocol)
    raw_comparisons_passed, raw_decisions = clears_all_comparisons(
        simple_intervals=raw_simple_intervals,
        simple_floor=floor,
        v1_intervals=raw_v1_intervals,
    )
    raw_scores = proper_scores(outcome_counts(selected_validation).loc[:, PA_OUTCOMES], raw_selected_probability)

    calibrated, calibration_folds = cross_fitted_temperature_probabilities(
        selected_validation, raw_selected_probability, protocol
    )
    calibration_scores = proper_scores(outcome_counts(selected_validation).loc[:, PA_OUTCOMES], calibrated)
    calibration_intervals = interval_report(selected_validation, calibrated, raw_selected_probability, protocol)
    calibration_installed = (
        raw_comparisons_passed
        and selected_stability_passed
        and all(float(calibration_scores[m]) < float(raw_scores[m]) for m in PRIMARY_METRICS)
        and all(float(item["upper"]) < 0.0 for item in calibration_intervals.values())
    )
    selected_probability = calibrated if calibration_installed else raw_selected_probability
    selected_scores = calibration_scores if calibration_installed else raw_scores
    final_temperature = fit_temperature(
        outcome_counts(selected_validation).loc[:, PA_OUTCOMES],
        raw_selected_probability,
        log_temperature_bounds=tuple(protocol["calibration"]["log_temperature_bounds"]),
    )
    selected_temperature = float(final_temperature) if calibration_installed else 1.0
    selected_simple_intervals = interval_report(selected_validation, selected_probability, best_simple, protocol)
    selected_v1_intervals = interval_report(selected_validation, selected_probability, v1_probability, protocol)
    selected_comparisons_passed, selected_decisions = clears_all_comparisons(
        simple_intervals=selected_simple_intervals,
        simple_floor=floor,
        v1_intervals=selected_v1_intervals,
    )
    selection_passed = bool(selected_comparisons_passed and selected_stability_passed)

    counts = outcome_counts(selected_validation).loc[:, PA_OUTCOMES]
    oof = selected_validation[["game_pk", "player_id", "game_date", "lineup_slot", "_selection_fold"]].copy()
    for index, outcome in enumerate(PA_OUTCOMES):
        oof[f"actual_{outcome}"] = counts[outcome].to_numpy()
        oof[f"strongest_simple_{outcome}"] = best_simple[:, index]
        oof[f"canonical_v1_{outcome}"] = v1_probability[:, index]
        oof[f"cumulative_core_{outcome}"] = core_probability[:, index]
        oof[f"cumulative_raw_{outcome}"] = raw_selected_probability[:, index]
        oof[f"cumulative_selected_{outcome}"] = selected_probability[:, index]
        if context_probability is not None:
            oof[f"cumulative_context_{outcome}"] = context_probability[:, index]
        for seed, probability in core_stability_probabilities.items():
            oof[f"cumulative_core_seed_{seed}_{outcome}"] = probability[:, index]
        for seed, probability in context_stability_probabilities.items():
            oof[f"cumulative_context_seed_{seed}_{outcome}"] = probability[:, index]

    pa_distribution = load_bound_pa_distribution(base_protocol, evidence_root=evidence_root)
    runtime_files = [
        Path(__file__),
        ROOT / "scripts/select_shared_pa_canonical_challenger.py",
        ROOT / "scripts/select_shared_pa_challenger.py",
        ROOT / "src/evaluation/shared_pa_cumulative_selection.py",
        ROOT / "src/evaluation/shared_pa_canonical_selection.py",
        ROOT / "src/evaluation/shared_pa_training_data.py",
        ROOT / "src/learning/shared_pa_model.py",
        protocol_path,
        ROOT / base_protocol["inputs"]["runtime_feature_contract"]["path"],
        ROOT / "src/features/canonical_cumulative_pa_features.py",
        ROOT / "config/shared_pa_cumulative_selection_retry_v2.json",
        ROOT / "reports/shared_pa_cumulative_selection_v1_INVALID_INCOMPLETE.json",
        ROOT / "scripts/validate_shared_pa_cumulative_selection.py",
        ROOT / "scripts/check_shared_pa_cumulative_selection_validator_mutations.py",
        ROOT / "scripts/check_shared_pa_cumulative_selection_retry_v2_offline.py",
        ROOT / "scripts/check_shared_pa_cumulative_selection_protocol_offline.py",
        ROOT / "scripts/check_shared_pa_cumulative_selection_logic_offline.py",
    ]
    try:
        source_commit = subprocess.check_output(
            ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        source_commit = "UNAVAILABLE"

    args.out_dir.mkdir(parents=True, exist_ok=True)
    oof_path = args.out_dir / "selection_oof.csv"
    atomic_csv(oof_path, oof)
    report: dict[str, Any] = {
        "schema_version": "shared-pa-cumulative-selection-report-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "CUMULATIVE_SELECTION_PASSED_CANDIDATE_FROZEN" if selection_passed else "CUMULATIVE_SELECTION_REJECTED_NO_CANDIDATE",
        "betting_authorized": False,
        "production_changed": False,
        "confirmation_2025_opened": False,
        "may_2026_opened": False,
        "selection_seasons": [2023, 2024],
        "source_commit": source_commit,
        "retry_authorization": {
            "path": "config/shared_pa_cumulative_selection_retry_v2.json",
            "sha256": sha256(ROOT / "config/shared_pa_cumulative_selection_retry_v2.json"),
        },
        "protocol": {"path": str(protocol_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(protocol_path)},
        "inputs": protocol["inputs"],
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "thread_count": int(args.threads),
            "file_hashes": {str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path) for path in runtime_files},
        },
        "pre_fit_mutation_contract": {
            "protocol_checks_required": 17,
            "selection_logic_checks_required": 14,
            "mechanical_retry_checks_required": 10,
            "validator_mutations_required_after_publication": true,
        },
        "population": {
            "source_rows": int(len(frame)),
            "positive_pa_scoring_rows": int(len(selected_validation)),
            "selection_dates": int(selected_validation["game_date"].nunique()),
            "identity_duplicates": 0,
            "coverage_loss": 0,
        },
        "simple_baselines": simple_report,
        "complexity_benefit_floor": floor,
        "canonical_v1_scores": proper_scores(counts, v1_probability),
        "grid": grid_records,
        "selected_params": selected_params,
        "core_scores": proper_scores(counts, core_probability),
        "core_intervals_vs_strongest_simple": core_simple_intervals,
        "core_intervals_vs_canonical_v1": core_v1_intervals,
        "core_decisions": core_decisions,
        "core_passed": bool(core_passed),
        "core_stability_audit": core_stability,
        "core_stability_passed": bool(core_stability_passed),
        "context_step": context_step,
        "selected_variant": selected_variant if selection_passed else None,
        "raw_selected_variant": selected_variant,
        "raw_scores": raw_scores,
        "raw_intervals_vs_strongest_simple": raw_simple_intervals,
        "raw_intervals_vs_canonical_v1": raw_v1_intervals,
        "raw_decisions": raw_decisions,
        "raw_comparisons_passed": bool(raw_comparisons_passed),
        "selected_stability_passed": bool(selected_stability_passed),
        "calibration": {
            "method": "single_temperature_multiclass",
            "cross_fitted_fold_temperatures": calibration_folds,
            "candidate_scores": calibration_scores,
            "intervals_vs_raw": calibration_intervals,
            "final_temperature_fit_on_all_2024_oof": float(final_temperature),
            "installed": bool(calibration_installed),
            "selected_temperature": selected_temperature,
        },
        "selected_scores": selected_scores,
        "selected_intervals_vs_strongest_simple": selected_simple_intervals,
        "selected_intervals_vs_canonical_v1": selected_v1_intervals,
        "selected_decisions": selected_decisions,
        "selected_comparisons_passed": bool(selected_comparisons_passed),
        "selection_passed": selection_passed,
        "derived_market_diagnostics": {
            "vs_strongest_simple": derived_diagnostics(selected_validation, selected_probability, best_simple, protocol, pa_distribution),
            "vs_canonical_v1": derived_diagnostics(selected_validation, selected_probability, v1_probability, protocol, pa_distribution),
            "selection_only_not_a_promotion_gate": True,
        },
        "oof_artifact": {"path": str(oof_path), "sha256": sha256(oof_path), "rows": int(len(oof)), "seasons": [2024]},
        "model_artifact": None,
        "protected_invariants": {
            "confirmation_2025_unread": True,
            "may_2026_unread": True,
            "production_unchanged": True,
            "betting_unauthorized": True,
        },
    }
    if selection_passed:
        features = feature_columns(protocol, selected_variant)
        iterations = max(1, int(statistics.median(selected_iterations)))
        model = fit_catboost(
            frame,
            features=features,
            params={**selected_params, "iterations": iterations, "thread_count": int(args.threads)},
            seed=primary_seed,
        )
        model_path = args.out_dir / "shared_pa_cumulative_catboost.cbm"
        model.model.save_model(str(model_path))
        report["model_artifact"] = {
            "path": str(model_path),
            "sha256": sha256(model_path),
            "features": features,
            "categorical_features": model.categorical_features,
            "iterations": iterations,
            "temperature": selected_temperature,
        }
    report_path = args.out_dir / "selection_report.json"
    atomic_json(report_path, report)
    print(json.dumps({
        "status": report["status"],
        "selected_simple": simple_report["selected"],
        "core_passed": core_passed,
        "selection_passed": selection_passed,
        "selected_variant": report["selected_variant"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
