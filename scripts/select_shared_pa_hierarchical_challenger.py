#!/usr/bin/env python3
"""Select one evidence-targeted hierarchical PA challenger on 2024 only."""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.select_shared_pa_canonical_challenger import (  # noqa: E402
    binary_date_interval, binary_scores,
)
from scripts.select_shared_pa_challenger import (  # noqa: E402
    cross_fitted_temperature_probabilities, evaluate_simple_baselines, inner_split,
)
from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_cumulative_selection import (  # noqa: E402
    aligned_v1_probabilities, assert_expected_simple, load_bound_pa_distribution,
    load_selection_frame, load_v1_comparator,
)
from src.evaluation.shared_pa_hierarchical_selection import (  # noqa: E402
    MARKETS, clears_primary, eligible_market, load_protocol, stage_features,
)
from src.evaluation.shared_pa_rejection_diagnostic import sha256  # noqa: E402
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.learning.shared_pa_hierarchical_model import (  # noqa: E402
    STAGE_1, STAGE_2, combine, fit_stage, fit_stage_full, hierarchical_counts,
)
from src.learning.shared_pa_model import (  # noqa: E402
    derived_market_probabilities, fit_temperature, paired_date_block_interval,
    proper_scores, temperature_scale,
)


PRIMARY = ("multiclass_log_loss", "multiclass_brier")


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


def atomic_publish_directory(staging: Path, final: Path) -> None:
    if not staging.is_dir() or final.exists():
        raise ValueError("hierarchical atomic publication boundary failed")
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, final)


def same_identity(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    columns = ["game_pk", "player_id", "game_date", "lineup_slot", "_selection_fold"]
    return left[columns].reset_index(drop=True).equals(right[columns].reset_index(drop=True))


def interval_report(
    frame: pd.DataFrame,
    candidate: np.ndarray,
    baseline: np.ndarray,
    protocol: dict[str, Any],
) -> dict[str, Any]:
    counts = outcome_counts(frame).loc[:, PA_OUTCOMES]
    return {
        metric: paired_date_block_interval(
            counts, candidate, baseline, frame["game_date"],
            metric="log_loss" if metric == "multiclass_log_loss" else "brier",
            draws=int(protocol["inference"]["bootstrap_draws"]),
            seed=int(protocol["inference"]["bootstrap_seed"]),
        )
        for metric in PRIMARY
    }


def evaluate_hierarchical(
    frame: pd.DataFrame,
    protocol: dict[str, Any],
    cumulative: dict[str, Any],
    *,
    seed: int,
    threads: int,
) -> tuple[np.ndarray, pd.DataFrame, dict[str, list[int]]]:
    stage_1_features = stage_features(protocol, cumulative, "stage_1")
    stage_2_features = stage_features(protocol, cumulative, "stage_2")
    params = {**protocol["model"]["fixed_params_from_prior_bounded_selection"], "thread_count": int(threads)}
    holdout = int(protocol["model"]["inner_early_stopping"]["holdout_tail_official_dates"])
    early_stopping = int(protocol["model"]["inner_early_stopping"]["early_stopping_rounds"])
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise")
    probability_parts: list[np.ndarray] = []
    validation_parts: list[pd.DataFrame] = []
    iterations = {"stage_1": [], "stage_2": []}
    for fold, (start, end) in enumerate(protocol["selection_folds"]):
        outer_train = frame[dates < pd.Timestamp(start)]
        outer_validation = frame[
            (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
            & (pd.to_numeric(frame["out_pa"], errors="raise") > 0)
        ]
        if outer_train.empty or outer_validation.empty:
            raise ValueError(f"hierarchical outer fold is empty: {start}..{end}")
        fit, early_stop = inner_split(outer_train, holdout)
        fit_stage_1, fit_stage_2 = hierarchical_counts(fit)
        stop_stage_1, stop_stage_2 = hierarchical_counts(early_stop)
        first = fit_stage(
            fit, features=stage_1_features, counts=fit_stage_1, classes=STAGE_1,
            params=params, seed=int(seed) + fold, validation_frame=early_stop,
            validation_counts=stop_stage_1, early_stopping_rounds=early_stopping,
        )
        second = fit_stage(
            fit, features=stage_2_features, counts=fit_stage_2, classes=STAGE_2,
            params=params, seed=int(seed) + 100 + fold, validation_frame=early_stop,
            validation_counts=stop_stage_2, early_stopping_rounds=early_stopping,
        )
        probability_parts.append(combine(first.predict_proba(outer_validation), second.predict_proba(outer_validation)))
        validation = outer_validation.copy()
        validation["_selection_fold"] = fold
        validation_parts.append(validation)
        for name, model in (("stage_1", first), ("stage_2", second)):
            best = int(model.model.get_best_iteration())
            iterations[name].append(best + 1 if best >= 0 else int(params["iterations"]))
    return np.vstack(probability_parts), pd.concat(validation_parts, ignore_index=True), iterations


def stability_audit(
    frame: pd.DataFrame,
    protocol: dict[str, Any],
    cumulative: dict[str, Any],
    *,
    simple: np.ndarray,
    v1: np.ndarray,
    reference: pd.DataFrame,
    threads: int,
) -> tuple[bool, list[dict[str, Any]], dict[int, np.ndarray]]:
    records: list[dict[str, Any]] = []
    probabilities: dict[int, np.ndarray] = {}
    passed_all = True
    for seed in protocol["model"]["stability_audit_seeds"]:
        probability, validation, iterations = evaluate_hierarchical(
            frame, protocol, cumulative, seed=int(seed), threads=threads
        )
        if not same_identity(validation, reference):
            raise ValueError("hierarchical stability identity changed")
        vs_simple = interval_report(validation, probability, simple, protocol)
        vs_v1 = interval_report(validation, probability, v1, protocol)
        passed = all(
            float(interval[metric]["point"]) < 0.0 and float(interval[metric]["upper"]) < 0.0
            for interval in (vs_simple, vs_v1) for metric in PRIMARY
        )
        records.append({
            "seed": int(seed), "scores": proper_scores(outcome_counts(validation).loc[:, PA_OUTCOMES], probability),
            "intervals_vs_strongest_simple": vs_simple, "intervals_vs_canonical_v1": vs_v1,
            "best_iterations": iterations, "passed": bool(passed),
        })
        probabilities[int(seed)] = probability
        passed_all = passed_all and passed
    return bool(passed_all), records, probabilities


def derived_market_report(
    frame: pd.DataFrame,
    candidate: np.ndarray,
    simple: np.ndarray,
    v1: np.ndarray,
    protocol: dict[str, Any],
    pa_distribution: dict[str, dict[str, float]],
) -> dict[str, Any]:
    arms = {"candidate": candidate, "strongest_simple": simple, "canonical_v1": v1}
    probabilities = {
        name: derived_market_probabilities(value, frame["lineup_slot"], pa_distribution)
        for name, value in arms.items()
    }
    counts = outcome_counts(frame).loc[:, PA_OUTCOMES].to_numpy(float)
    index = {name: position for position, name in enumerate(PA_OUTCOMES)}
    hits = counts[:, index["single"]] + counts[:, index["double"]] + counts[:, index["triple"]] + counts[:, index["home_run"]]
    home_runs = counts[:, index["home_run"]]
    total_bases = counts[:, index["single"]] + 2 * counts[:, index["double"]] + 3 * counts[:, index["triple"]] + 4 * counts[:, index["home_run"]]
    actual: dict[str, np.ndarray] = {
        "hits_0.5": (hits >= 1).astype(float), "hits_1.5": (hits >= 2).astype(float),
        "home_runs_0.5": (home_runs >= 1).astype(float),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
        actual[f"total_bases_{line}"] = (total_bases > line).astype(float)
    result: dict[str, Any] = {}
    for market_index, market in enumerate(MARKETS):
        scores = {name: binary_scores(actual[market], value[market]) for name, value in probabilities.items()}
        comparisons: dict[str, Any] = {}
        for comparator in ("strongest_simple", "canonical_v1"):
            comparisons[f"vs_{comparator}"] = {
                metric: binary_date_interval(
                    actual[market], probabilities["candidate"][market], probabilities[comparator][market],
                    frame["game_date"], metric=metric,
                    draws=int(protocol["inference"]["bootstrap_draws"]),
                    seed=int(protocol["inference"]["bootstrap_seed"]) + market_index,
                )
                for metric in ("log_loss", "brier")
            }
        result[market] = {
            "scores": scores, "candidate_comparisons": comparisons,
            "eligible_for_confirmation": bool(eligible_market(comparisons)),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--protocol", type=Path, default=ROOT / "config/shared_pa_hierarchical_selection_protocol.json")
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    final_out = args.out_dir.resolve()
    staging_out = final_out.with_name(f".{final_out.name}.{os.getpid()}.tmp")
    if final_out.exists() or staging_out.exists():
        raise ValueError("hierarchical output or staging directory already exists")
    protocol_path = args.protocol.resolve()
    protocol, cumulative, base = load_protocol(protocol_path, code_root=ROOT, evidence_root=evidence_root)
    frame = load_selection_frame(cumulative, base, evidence_root=evidence_root)
    simple_report, simple, simple_validation = evaluate_simple_baselines(frame, cumulative)
    assert_expected_simple(simple_report, cumulative)
    v1_comparator = load_v1_comparator(cumulative, evidence_root=evidence_root)
    raw, validation, best_iterations = evaluate_hierarchical(
        frame, protocol, cumulative, seed=int(protocol["model"]["random_seed"]), threads=int(args.threads)
    )
    if not same_identity(validation, simple_validation):
        raise ValueError("hierarchical/simple validation identity changed")
    v1 = aligned_v1_probabilities(validation, v1_comparator)
    counts = outcome_counts(validation).loc[:, PA_OUTCOMES]
    raw_scores = proper_scores(counts, raw)
    raw_vs_simple = interval_report(validation, raw, simple, protocol)
    raw_vs_v1 = interval_report(validation, raw, v1, protocol)
    raw_primary_passed, raw_decisions = clears_primary(protocol, vs_simple=raw_vs_simple, vs_v1=raw_vs_v1)
    stability_passed = False
    stability: list[dict[str, Any]] = []
    stability_probabilities: dict[int, np.ndarray] = {}
    if raw_primary_passed:
        stability_passed, stability, stability_probabilities = stability_audit(
            frame, protocol, cumulative, simple=simple, v1=v1, reference=validation,
            threads=int(args.threads),
        )

    selected = raw
    calibration_report: dict[str, Any] = {"evaluated": False, "installed": False}
    if raw_primary_passed and stability_passed:
        calibrated, temperatures = cross_fitted_temperature_probabilities(validation, raw, protocol)
        calibrated_scores = proper_scores(counts, calibrated)
        intervals_vs_raw = interval_report(validation, calibrated, raw, protocol)
        installed = all(float(calibrated_scores[m]) < float(raw_scores[m]) for m in PRIMARY) and all(
            float(intervals_vs_raw[m]["upper"]) < 0.0 for m in PRIMARY
        )
        final_temperature = fit_temperature(
            counts, raw, log_temperature_bounds=tuple(protocol["calibration"]["log_temperature_bounds"])
        )
        calibration_report = {
            "evaluated": True, "cross_fitted_temperatures": temperatures,
            "scores": calibrated_scores, "intervals_vs_raw": intervals_vs_raw,
            "final_temperature_fit_on_all_2024_oof": float(final_temperature),
            "installed": bool(installed),
        }
        if installed:
            selected = calibrated
    selected_scores = proper_scores(counts, selected)
    selected_vs_simple = interval_report(validation, selected, simple, protocol)
    selected_vs_v1 = interval_report(validation, selected, v1, protocol)
    selected_primary_passed, selected_decisions = clears_primary(
        protocol, vs_simple=selected_vs_simple, vs_v1=selected_vs_v1
    )
    pa_distribution = load_bound_pa_distribution(base, evidence_root=evidence_root)
    market_report = derived_market_report(validation, selected, simple, v1, protocol, pa_distribution)
    eligible_markets = [market for market in MARKETS if market_report[market]["eligible_for_confirmation"]]
    foundation_passed = bool(selected_primary_passed and stability_passed)
    selection_passed = bool(foundation_passed and eligible_markets)

    oof = validation[["game_pk", "player_id", "game_date", "lineup_slot", "_selection_fold"]].copy()
    for outcome_index, outcome in enumerate(PA_OUTCOMES):
        oof[f"actual_{outcome}"] = counts[outcome].to_numpy()
        oof[f"strongest_simple_{outcome}"] = simple[:, outcome_index]
        oof[f"canonical_v1_{outcome}"] = v1[:, outcome_index]
        oof[f"hierarchical_raw_{outcome}"] = raw[:, outcome_index]
        oof[f"hierarchical_selected_{outcome}"] = selected[:, outcome_index]
        for seed, probability in stability_probabilities.items():
            oof[f"hierarchical_seed_{seed}_{outcome}"] = probability[:, outcome_index]

    source_commit = subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    runtime_files = [
        Path(__file__), ROOT / "scripts/validate_shared_pa_hierarchical_selection.py",
        ROOT / "scripts/check_shared_pa_hierarchical_selection_validator_mutations.py",
        ROOT / "src/learning/shared_pa_hierarchical_model.py",
        ROOT / "src/evaluation/shared_pa_hierarchical_selection.py",
        ROOT / "src/evaluation/shared_pa_cumulative_selection.py",
        ROOT / "src/learning/shared_pa_model.py", protocol_path,
    ]
    staging_out.mkdir(parents=True, exist_ok=False)
    staging_oof = staging_out / "selection_oof.csv"
    final_oof = final_out / "selection_oof.csv"
    atomic_csv(staging_oof, oof)
    report: dict[str, Any] = {
        "schema_version": "shared-pa-hierarchical-selection-report-v1",
        "status": "HIERARCHICAL_SELECTION_PASSED_CANDIDATE_FROZEN" if selection_passed else "HIERARCHICAL_SELECTION_REJECTED_NO_CANDIDATE",
        "betting_authorized": False, "production_changed": False,
        "confirmation_2025_opened": False, "may_2026_opened": False,
        "source_commit": source_commit,
        "protocol": {"path": str(protocol_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(protocol_path)},
        "inputs": protocol["inputs"],
        "runtime": {
            "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
            "thread_count": int(args.threads),
            "file_hashes": {str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path) for path in runtime_files},
        },
        "population": {"rows": int(len(validation)), "dates": int(validation["game_date"].nunique()), "coverage_loss": 0, "seasons": [2024]},
        "comparators": {
            "strongest_simple": {"id": simple_report["selected"], "scores": simple_report[simple_report["selected"]]["scores"]},
            "canonical_v1": {"scores": proper_scores(counts, v1)},
        },
        "raw": {
            "scores": raw_scores, "intervals_vs_strongest_simple": raw_vs_simple,
            "intervals_vs_canonical_v1": raw_vs_v1, "decisions": raw_decisions,
            "primary_passed": bool(raw_primary_passed), "best_iterations": best_iterations,
        },
        "stability": {"evaluated": bool(raw_primary_passed), "passed": bool(stability_passed), "records": stability},
        "calibration": calibration_report,
        "selected": {
            "scores": selected_scores, "intervals_vs_strongest_simple": selected_vs_simple,
            "intervals_vs_canonical_v1": selected_vs_v1, "decisions": selected_decisions,
            "primary_passed": bool(selected_primary_passed),
        },
        "derived_markets": market_report,
        "eligible_markets_for_2025_confirmation": eligible_markets,
        "foundation_passed": foundation_passed,
        "selection_passed": selection_passed,
        "oof_artifact": {"path": str(final_oof), "sha256": sha256(staging_oof), "rows": int(len(oof)), "seasons": [2024]},
        "model_artifacts": None,
        "protected_invariants": {
            "confirmation_2025_unread": True, "may_2026_unread": True,
            "production_unchanged": True, "betting_unauthorized": True,
        },
    }
    if selection_passed:
        stage_1_counts, stage_2_counts = hierarchical_counts(frame)
        fixed = protocol["model"]["fixed_params_from_prior_bounded_selection"]
        stage_1_iterations = max(1, int(statistics.median(best_iterations["stage_1"])))
        stage_2_iterations = max(1, int(statistics.median(best_iterations["stage_2"])))
        first = fit_stage_full(
            frame, features=stage_features(protocol, cumulative, "stage_1"), counts=stage_1_counts,
            classes=STAGE_1, params={**fixed, "iterations": stage_1_iterations, "thread_count": int(args.threads)},
            seed=int(protocol["model"]["random_seed"]),
        )
        second = fit_stage_full(
            frame, features=stage_features(protocol, cumulative, "stage_2"), counts=stage_2_counts,
            classes=STAGE_2, params={**fixed, "iterations": stage_2_iterations, "thread_count": int(args.threads)},
            seed=int(protocol["model"]["random_seed"]) + 100,
        )
        first_path = staging_out / "stage_1.cbm"
        second_path = staging_out / "stage_2.cbm"
        first.model.save_model(str(first_path)); second.model.save_model(str(second_path))
        report["model_artifacts"] = {
            "stage_1": {"path": str(final_out / "stage_1.cbm"), "sha256": sha256(first_path), "iterations": stage_1_iterations},
            "stage_2": {"path": str(final_out / "stage_2.cbm"), "sha256": sha256(second_path), "iterations": stage_2_iterations},
            "temperature": float(calibration_report.get("final_temperature_fit_on_all_2024_oof", 1.0)) if calibration_report.get("installed") else 1.0,
        }
    atomic_json(staging_out / "selection_report.json", report)
    atomic_publish_directory(staging_out, final_out)
    print(json.dumps({
        "status": report["status"], "foundation_passed": foundation_passed,
        "selection_passed": selection_passed, "eligible_markets": eligible_markets,
        "scores": selected_scores,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
