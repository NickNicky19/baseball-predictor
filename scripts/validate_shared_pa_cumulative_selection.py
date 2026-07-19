#!/usr/bin/env python3
"""Independently certify the cumulative-history 2023-2024 selector."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.select_shared_pa_canonical_challenger import atomic_json  # noqa: E402
from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_canonical_selection import complexity_benefit_floor, sha256  # noqa: E402
from src.evaluation.shared_pa_cumulative_selection import (  # noqa: E402
    assert_expected_simple,
    clears_all_comparisons,
)
from src.learning.shared_pa_model import paired_date_block_interval, proper_scores  # noqa: E402


PRIMARY_METRICS = ("multiclass_log_loss", "multiclass_brier")
IDENTITY = ["game_pk", "player_id"]


def close_scores(expected: dict[str, Any], actual: dict[str, float], label: str) -> None:
    for metric in PRIMARY_METRICS:
        if not math.isclose(float(expected[metric]), float(actual[metric]), rel_tol=0.0, abs_tol=1e-14):
            raise ValueError(f"cumulative {label} score does not recompute: {metric}")


def probability_matrix(oof: pd.DataFrame, prefix: str) -> np.ndarray:
    columns = [f"{prefix}_{outcome}" for outcome in PA_OUTCOMES]
    if not set(columns).issubset(oof.columns):
        raise ValueError(f"cumulative OOF is missing probability matrix: {prefix}")
    matrix = oof[columns].to_numpy(float)
    if not np.isfinite(matrix).all() or (matrix < 0).any() or not np.allclose(matrix.sum(axis=1), 1.0, atol=1e-9):
        raise ValueError(f"cumulative OOF probability matrix is invalid: {prefix}")
    return matrix


def paired_intervals(
    actual: pd.DataFrame,
    candidate: np.ndarray,
    baseline: np.ndarray,
    dates: pd.Series,
    protocol: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    metrics = protocol["required_metrics"]
    return {
        metric: paired_date_block_interval(
            actual,
            candidate,
            baseline,
            dates,
            metric="log_loss" if metric == "multiclass_log_loss" else "brier",
            draws=int(metrics["bootstrap_draws"]),
            seed=int(metrics["bootstrap_seed"]),
        )
        for metric in PRIMARY_METRICS
    }


def compare_intervals(expected: dict[str, Any], actual: dict[str, Any], label: str) -> None:
    for metric in PRIMARY_METRICS:
        for key in ("point", "lower", "upper"):
            if not math.isclose(float(expected[metric][key]), float(actual[metric][key]), rel_tol=0.0, abs_tol=1e-14):
                raise ValueError(f"cumulative {label} interval does not recompute: {metric}/{key}")


def validate_report(report: dict[str, Any], oof: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != "shared-pa-cumulative-selection-report-v1":
        raise ValueError("cumulative selection report schema changed")
    if report.get("status") not in {
        "CUMULATIVE_SELECTION_REJECTED_NO_CANDIDATE",
        "CUMULATIVE_SELECTION_PASSED_CANDIDATE_FROZEN",
    }:
        raise ValueError("cumulative selection status is invalid")
    if report.get("betting_authorized") or report.get("production_changed"):
        raise ValueError("cumulative selection altered authorization or production")
    if report.get("confirmation_2025_opened") or report.get("may_2026_opened"):
        raise ValueError("cumulative selection crossed protected evidence")
    if report.get("selection_seasons") != [2023, 2024]:
        raise ValueError("cumulative selection seasons changed")
    if report.get("pre_fit_mutation_contract") != {
        "protocol_checks_required": 17,
        "selection_logic_checks_required": 14,
        "mechanical_retry_v2_checks_required": 10,
        "mechanical_retry_v3_checks_required": 14,
        "validator_mutations_required_after_publication": True,
    }:
        raise ValueError("cumulative pre-fit mutation contract changed")
    protected = report.get("protected_invariants", {})
    if protected != {
        "confirmation_2025_unread": True,
        "may_2026_unread": True,
        "production_unchanged": True,
        "betting_unauthorized": True,
    }:
        raise ValueError("cumulative protected invariants changed")

    base = [*IDENTITY, "game_date", "lineup_slot", "_selection_fold"]
    actual_columns = [f"actual_{outcome}" for outcome in PA_OUTCOMES]
    required_prefixes = ["strongest_simple", "canonical_v1", "cumulative_core", "cumulative_raw", "cumulative_selected"]
    required = set(base + actual_columns)
    for prefix in required_prefixes:
        required.update(f"{prefix}_{outcome}" for outcome in PA_OUTCOMES)
    allowed = set(required)
    if report["context_step"]["evaluated"]:
        allowed.update(f"cumulative_context_{outcome}" for outcome in PA_OUTCOMES)
    for record in report["core_stability_audit"]:
        allowed.update(f"cumulative_core_seed_{int(record['seed'])}_{outcome}" for outcome in PA_OUTCOMES)
    for record in report["context_step"].get("stability_audit", []):
        allowed.update(f"cumulative_context_seed_{int(record['seed'])}_{outcome}" for outcome in PA_OUTCOMES)
    if set(oof.columns) != allowed:
        raise ValueError("cumulative OOF schema changed")
    if len(oof) != int(report["oof_artifact"]["rows"]):
        raise ValueError("cumulative OOF row count changed")
    if oof[IDENTITY].isna().any().any() or oof.duplicated(IDENTITY).any():
        raise ValueError("cumulative OOF identity is null or duplicated")
    dates = pd.to_datetime(oof["game_date"], format="%Y-%m-%d", errors="raise")
    if set(dates.dt.year) != {2024} or set(oof["_selection_fold"].astype(int)) != {0, 1, 2, 3}:
        raise ValueError("cumulative OOF chronology or fold coverage changed")
    actual = oof[actual_columns].copy()
    actual.columns = PA_OUTCOMES
    values = actual.to_numpy(float)
    if not np.isfinite(values).all() or (values < 0).any() or not np.equal(values, np.floor(values)).all():
        raise ValueError("cumulative OOF outcome counts are invalid")
    if not actual.sum(axis=1).gt(0).all():
        raise ValueError("cumulative OOF contains zero-PA scoring rows")

    matrices = {prefix: probability_matrix(oof, prefix) for prefix in required_prefixes}
    assert_expected_simple(report["simple_baselines"], protocol)
    close_scores(
        report["simple_baselines"][report["simple_baselines"]["selected"]]["scores"],
        proper_scores(actual, matrices["strongest_simple"]),
        "simple",
    )
    close_scores(report["canonical_v1_scores"], proper_scores(actual, matrices["canonical_v1"]), "canonical v1")
    close_scores(report["core_scores"], proper_scores(actual, matrices["cumulative_core"]), "core")
    close_scores(report["raw_scores"], proper_scores(actual, matrices["cumulative_raw"]), "raw")
    close_scores(report["selected_scores"], proper_scores(actual, matrices["cumulative_selected"]), "selected")

    floor = complexity_benefit_floor(report["simple_baselines"])
    for metric in PRIMARY_METRICS:
        if not math.isclose(float(report["complexity_benefit_floor"][metric]), float(floor[metric]), rel_tol=0.0, abs_tol=1e-15):
            raise ValueError(f"cumulative materiality floor changed: {metric}")

    core_simple = paired_intervals(actual, matrices["cumulative_core"], matrices["strongest_simple"], oof["game_date"], protocol)
    core_v1 = paired_intervals(actual, matrices["cumulative_core"], matrices["canonical_v1"], oof["game_date"], protocol)
    compare_intervals(report["core_intervals_vs_strongest_simple"], core_simple, "core/simple")
    compare_intervals(report["core_intervals_vs_canonical_v1"], core_v1, "core/v1")
    core_passed, _ = clears_all_comparisons(simple_intervals=core_simple, simple_floor=floor, v1_intervals=core_v1)
    if bool(report["core_passed"]) != bool(core_passed):
        raise ValueError("cumulative core decision does not recompute")

    seeds = [int(seed) for seed in protocol["model_family"]["stability_audit_seeds"]]
    expected_core_records = seeds if core_passed else []
    if [int(item["seed"]) for item in report["core_stability_audit"]] != expected_core_records:
        raise ValueError("cumulative core stability coverage changed")
    simple_scores = report["simple_baselines"][report["simple_baselines"]["selected"]]["scores"]
    core_stability_passed = bool(core_passed)
    for record in report["core_stability_audit"]:
        prefix = f"cumulative_core_seed_{int(record['seed'])}"
        score = proper_scores(actual, probability_matrix(oof, prefix))
        close_scores(record["scores"], score, prefix)
        passed = all(float(score[m]) < float(simple_scores[m]) for m in PRIMARY_METRICS)
        if bool(record["passed_vs_strongest_simple"]) != passed:
            raise ValueError("cumulative core stability decision changed")
        core_stability_passed = core_stability_passed and passed
    if bool(report["core_stability_passed"]) != core_stability_passed:
        raise ValueError("cumulative core stability aggregate changed")

    context = report["context_step"]
    expected_context_evaluated = bool(core_passed and core_stability_passed)
    if bool(context["evaluated"]) != expected_context_evaluated:
        raise ValueError("cumulative context evaluation boundary changed")
    if context["evaluated"]:
        context_matrix = probability_matrix(oof, "cumulative_context")
        intervals = paired_intervals(actual, context_matrix, matrices["cumulative_core"], oof["game_date"], protocol)
        compare_intervals(context["intervals_vs_core"], intervals, "context/core")
        incremental = all(float(item["upper"]) < 0.0 for item in intervals.values())
        if bool(context["incremental_passed"]) != incremental:
            raise ValueError("cumulative context incremental decision changed")
        expected_context_seeds = seeds if incremental else []
        if [int(item["seed"]) for item in context.get("stability_audit", [])] != expected_context_seeds:
            raise ValueError("cumulative context stability coverage changed")
        context_stability_passed = bool(incremental)
        for record in context.get("stability_audit", []):
            prefix = f"cumulative_context_seed_{int(record['seed'])}"
            score = proper_scores(actual, probability_matrix(oof, prefix))
            close_scores(record["scores"], score, prefix)
            passed = all(float(score[m]) < float(simple_scores[m]) for m in PRIMARY_METRICS)
            if bool(record["passed_vs_strongest_simple"]) != passed:
                raise ValueError("cumulative context stability decision changed")
            context_stability_passed = context_stability_passed and passed
        installed = bool(incremental and context_stability_passed)
        if bool(context["stability_passed"]) != context_stability_passed or bool(context["installed"]) != installed:
            raise ValueError("cumulative context installation decision changed")
        expected_raw = context_matrix if installed else matrices["cumulative_core"]
    else:
        expected_raw = matrices["cumulative_core"]
    if not np.array_equal(matrices["cumulative_raw"], expected_raw):
        raise ValueError("cumulative raw selected matrix does not match variant decision")

    raw_simple = paired_intervals(actual, matrices["cumulative_raw"], matrices["strongest_simple"], oof["game_date"], protocol)
    raw_v1 = paired_intervals(actual, matrices["cumulative_raw"], matrices["canonical_v1"], oof["game_date"], protocol)
    compare_intervals(report["raw_intervals_vs_strongest_simple"], raw_simple, "raw/simple")
    compare_intervals(report["raw_intervals_vs_canonical_v1"], raw_v1, "raw/v1")
    raw_passed, _ = clears_all_comparisons(simple_intervals=raw_simple, simple_floor=floor, v1_intervals=raw_v1)
    if bool(report["raw_comparisons_passed"]) != raw_passed:
        raise ValueError("cumulative raw comparison decision changed")

    calibration = report["calibration"]
    calibration_intervals = paired_intervals(actual, matrices["cumulative_selected"], matrices["cumulative_raw"], oof["game_date"], protocol)
    if calibration["installed"]:
        compare_intervals(calibration["intervals_vs_raw"], calibration_intervals, "calibration/raw")
        if not all(float(proper_scores(actual, matrices["cumulative_selected"])[m]) < float(proper_scores(actual, matrices["cumulative_raw"])[m]) for m in PRIMARY_METRICS):
            raise ValueError("cumulative installed calibration does not improve both scores")
        if not all(float(item["upper"]) < 0.0 for item in calibration_intervals.values()):
            raise ValueError("cumulative installed calibration lacks paired strength")
    elif not np.array_equal(matrices["cumulative_selected"], matrices["cumulative_raw"]):
        raise ValueError("cumulative uninstalled calibration changed selected probabilities")

    selected_simple = paired_intervals(actual, matrices["cumulative_selected"], matrices["strongest_simple"], oof["game_date"], protocol)
    selected_v1 = paired_intervals(actual, matrices["cumulative_selected"], matrices["canonical_v1"], oof["game_date"], protocol)
    compare_intervals(report["selected_intervals_vs_strongest_simple"], selected_simple, "selected/simple")
    compare_intervals(report["selected_intervals_vs_canonical_v1"], selected_v1, "selected/v1")
    selected_comparisons, _ = clears_all_comparisons(simple_intervals=selected_simple, simple_floor=floor, v1_intervals=selected_v1)
    selected_stability = bool(context["stability_passed"] if context.get("installed") else core_stability_passed)
    selection_passed = bool(selected_comparisons and selected_stability)
    if bool(report["selected_comparisons_passed"]) != selected_comparisons:
        raise ValueError("cumulative selected comparison decision changed")
    if bool(report["selected_stability_passed"]) != selected_stability:
        raise ValueError("cumulative selected stability decision changed")
    if bool(report["selection_passed"]) != selection_passed:
        raise ValueError("cumulative final selection decision changed")
    expected_status = "CUMULATIVE_SELECTION_PASSED_CANDIDATE_FROZEN" if selection_passed else "CUMULATIVE_SELECTION_REJECTED_NO_CANDIDATE"
    if report["status"] != expected_status:
        raise ValueError("cumulative report status disagrees with recomputed decision")
    if selection_passed:
        model = report.get("model_artifact")
        if not model or not Path(model["path"]).exists() or sha256(Path(model["path"])) != model["sha256"]:
            raise ValueError("cumulative passing model artifact is missing or changed")
    elif report.get("model_artifact") is not None:
        raise ValueError("cumulative rejected selection published a model")

    return {
        "rows": int(len(oof)),
        "dates": int(oof["game_date"].nunique()),
        "core_passed": bool(core_passed),
        "core_stability_passed": bool(core_stability_passed),
        "selected_comparisons_passed": bool(selected_comparisons),
        "selected_stability_passed": bool(selected_stability),
        "selection_passed": selection_passed,
        "simple_scores": proper_scores(actual, matrices["strongest_simple"]),
        "canonical_v1_scores": proper_scores(actual, matrices["canonical_v1"]),
        "selected_scores": proper_scores(actual, matrices["cumulative_selected"]),
        "selected_intervals_vs_simple": selected_simple,
        "selected_intervals_vs_v1": selected_v1,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    report_path = args.report.resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    retry = report.get("retry_authorization")
    if not retry:
        raise ValueError("cumulative retry authorization is missing")
    retry_path = ROOT / retry["path"]
    if not retry_path.exists() or sha256(retry_path) != retry["sha256"]:
        raise ValueError("cumulative retry authorization changed")
    protocol_path = ROOT / report["protocol"]["path"]
    if sha256(protocol_path) != report["protocol"]["sha256"]:
        raise ValueError("cumulative selection protocol hash changed")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    for name, record in report["inputs"].items():
        root = ROOT if name in {"base_selection_protocol", "cumulative_reproducibility"} else evidence_root
        path = root / record["path"]
        if not path.exists() or sha256(path) != record["sha256"]:
            raise ValueError(f"cumulative selection input changed: {name}")
    for relative, expected in report["runtime"]["file_hashes"].items():
        path = ROOT / relative
        if not path.exists() or sha256(path) != expected:
            raise ValueError(f"cumulative selection runtime changed: {relative}")
    oof_path = Path(report["oof_artifact"]["path"])
    if not oof_path.exists() or sha256(oof_path) != report["oof_artifact"]["sha256"]:
        raise ValueError("cumulative OOF artifact is missing or changed")
    validation = validate_report(report, pd.read_csv(oof_path, low_memory=False), protocol)
    certificate = {
        "schema_version": "shared-pa-cumulative-selection-certificate-v1",
        "certified_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "CUMULATIVE_SELECTION_PASS_CERTIFIED" if validation["selection_passed"] else "CUMULATIVE_SELECTION_REJECTION_CERTIFIED",
        "betting_authorized": False,
        "production_changed": False,
        "confirmation_2025_opened": False,
        "may_2026_opened": False,
        "report": {"path": str(report_path), "sha256": sha256(report_path)},
        "protocol": report["protocol"],
        "oof_artifact": report["oof_artifact"],
        "validation": validation,
        "protected_invariants": report["protected_invariants"],
    }
    atomic_json(args.out.resolve(), certificate)
    print(certificate["status"])
    print(f"rows: {validation['rows']}")
    print(f"selection_passed: {validation['selection_passed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
