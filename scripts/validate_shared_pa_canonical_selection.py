#!/usr/bin/env python3
"""Independently validate a canonical shared-PA selection report and OOF chain."""
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

from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_canonical_selection import (  # noqa: E402
    clears_materiality,
    complexity_benefit_floor,
    sha256,
)
from src.learning.shared_pa_model import paired_date_block_interval, proper_scores  # noqa: E402


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def validate_report(report: dict[str, Any], oof: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != "shared-pa-canonical-selection-report-v1":
        raise ValueError("canonical selection report schema changed")
    if report.get("status") not in {"SELECTION_REJECTED_NO_CANDIDATE", "SELECTION_PASSED_CANDIDATE_FROZEN"}:
        raise ValueError("canonical selection status is invalid")
    if report.get("betting_authorized") or report.get("production_changed"):
        raise ValueError("canonical selection altered authorization or production")
    if report.get("confirmation_2025_opened") or report.get("may_2026_opened"):
        raise ValueError("canonical selection crossed protected evidence")
    required = ["game_pk", "player_id", "game_date", "lineup_slot", "_selection_fold"]
    required += [f"actual_{name}" for name in PA_OUTCOMES]
    required += [f"strongest_simple_{name}" for name in PA_OUTCOMES]
    required += [f"canonical_raw_{name}" for name in PA_OUTCOMES]
    required += [f"canonical_selected_{name}" for name in PA_OUTCOMES]
    if set(oof.columns) != set(required):
        raise ValueError("canonical OOF schema changed")
    if oof[["game_pk", "player_id"]].isna().any().any() or oof.duplicated(["game_pk", "player_id"]).any():
        raise ValueError("canonical OOF identity is null or duplicated")
    if len(oof) != int(report["oof_artifact"]["rows"]):
        raise ValueError("canonical OOF row count differs")
    if set(oof["_selection_fold"].astype(int)) != {0, 1, 2, 3}:
        raise ValueError("canonical OOF fold coverage changed")
    dates = pd.to_datetime(oof["game_date"], format="%Y-%m-%d", errors="raise")
    if set(dates.dt.year) != {2024}:
        raise ValueError("canonical OOF is not exactly 2024 selection")
    actual = oof[[f"actual_{name}" for name in PA_OUTCOMES]].copy()
    actual.columns = PA_OUTCOMES
    if actual.isna().any().any() or (actual < 0).any().any():
        raise ValueError("canonical OOF actual PA counts are invalid")
    if not np.equal(actual.to_numpy(), np.floor(actual.to_numpy())).all():
        raise ValueError("canonical OOF actual PA counts are non-integer")
    if not actual.sum(axis=1).gt(0).all():
        raise ValueError("canonical OOF contains zero-PA scoring rows")
    matrices: dict[str, np.ndarray] = {}
    for prefix in ("strongest_simple", "canonical_raw", "canonical_selected"):
        matrix = oof[[f"{prefix}_{name}" for name in PA_OUTCOMES]].to_numpy(float)
        if not np.isfinite(matrix).all() or (matrix < 0).any() or not np.allclose(matrix.sum(axis=1), 1.0, atol=1e-9):
            raise ValueError(f"canonical OOF probability matrix invalid: {prefix}")
        matrices[prefix] = matrix
    simple_scores = proper_scores(actual, matrices["strongest_simple"])
    raw_scores = proper_scores(actual, matrices["canonical_raw"])
    selected_scores = proper_scores(actual, matrices["canonical_selected"])
    for expected, recomputed, label in (
        (report["simple_baselines"][report["simple_baselines"]["selected"]]["scores"], simple_scores, "simple"),
        (report["raw_scores"], raw_scores, "raw"),
        (report["selected_scores"], selected_scores, "selected"),
    ):
        for metric, value in recomputed.items():
            if not math.isclose(float(expected[metric]), value, rel_tol=0.0, abs_tol=1e-14):
                raise ValueError(f"canonical {label} score does not recompute: {metric}")
    floor = complexity_benefit_floor(report["simple_baselines"])
    for metric, value in floor.items():
        if not math.isclose(value, float(report["complexity_benefit_floor"][metric]), rel_tol=0.0, abs_tol=1e-15):
            raise ValueError(f"canonical materiality floor does not recompute: {metric}")
    metrics = protocol["required_metrics"]
    intervals = {
        metric: paired_date_block_interval(
            actual,
            matrices["canonical_raw"],
            matrices["strongest_simple"],
            oof["game_date"],
            metric="log_loss" if metric == "multiclass_log_loss" else "brier",
            draws=int(metrics["bootstrap_draws"]),
            seed=int(metrics["bootstrap_seed"]),
        )
        for metric in metrics["pa_primary"]
    }
    for metric, values in intervals.items():
        for key in ("point", "lower", "upper"):
            if not math.isclose(float(values[key]), float(report["core_intervals_vs_strongest_simple"][metric][key]), rel_tol=0.0, abs_tol=1e-14):
                raise ValueError(f"canonical paired interval does not recompute: {metric}/{key}")
    passed, decisions = clears_materiality(intervals, floor)
    if bool(report["core_passed"]) != passed:
        raise ValueError("canonical materiality decision does not recompute")
    for metric, decision in decisions.items():
        reported = report["materiality_decisions"][metric]
        if bool(reported["passed"]) != bool(decision["passed"]):
            raise ValueError(f"canonical materiality decision differs: {metric}")
        for key in ("required_candidate_minus_simple_below", "point", "upper"):
            if not math.isclose(float(reported[key]), float(decision[key]), rel_tol=0.0, abs_tol=1e-14):
                raise ValueError(f"canonical materiality value differs: {metric}/{key}")
    expected_status = "SELECTION_PASSED_CANDIDATE_FROZEN" if passed else "SELECTION_REJECTED_NO_CANDIDATE"
    if report["status"] != expected_status:
        raise ValueError("canonical status disagrees with materiality result")
    if report["status"] == "SELECTION_REJECTED_NO_CANDIDATE":
        if report.get("model_artifact") is not None:
            raise ValueError("rejected canonical selection published a model")
        if report.get("stability_audit") or report.get("stability_passed"):
            raise ValueError("rejected core improperly used stability evidence")
        if report.get("context_step", {}).get("evaluated"):
            raise ValueError("rejected core improperly evaluated context variant")
    return {
        "rows": int(len(oof)),
        "dates": int(oof["game_date"].nunique()),
        "duplicate_identity_rows": 0,
        "simple_scores": simple_scores,
        "raw_scores": raw_scores,
        "selected_scores": selected_scores,
        "materiality_floor": floor,
        "materiality_passed": bool(passed),
        "materiality_decisions": decisions,
        "intervals_recomputed": intervals,
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
    protocol_path = ROOT / report["protocol"]["path"]
    if sha256(protocol_path) != report["protocol"]["sha256"]:
        raise ValueError("canonical selection protocol hash changed")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    for name, record in report["inputs"].items():
        path = (ROOT if name == "runtime_feature_contract" else evidence_root) / record["path"]
        if not path.exists() or sha256(path) != record["sha256"]:
            raise ValueError(f"canonical selection report input hash changed: {name}")
    for relative, expected in report["runtime"]["file_hashes"].items():
        path = ROOT / relative
        if not path.exists() or sha256(path) != expected:
            raise ValueError(f"canonical selection runtime hash changed: {relative}")
    oof_path = Path(report["oof_artifact"]["path"])
    if not oof_path.exists() or sha256(oof_path) != report["oof_artifact"]["sha256"]:
        raise ValueError("canonical OOF artifact hash changed")
    validation = validate_report(report, pd.read_csv(oof_path), protocol)
    certificate = {
        "schema_version": "shared-pa-canonical-selection-certificate-v1",
        "certified_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "CANONICAL_SELECTION_REJECTION_CERTIFIED" if not validation["materiality_passed"] else "CANONICAL_SELECTION_PASS_CERTIFIED",
        "betting_authorized": False,
        "production_changed": False,
        "confirmation_2025_opened": False,
        "may_2026_opened": False,
        "report": {"path": str(report_path), "sha256": sha256(report_path)},
        "protocol": report["protocol"],
        "oof_artifact": report["oof_artifact"],
        "validation": validation,
        "protected_invariants": {
            "confirmation_2025_unread": True,
            "may_2026_unread": True,
            "production_unchanged": True,
            "betting_unauthorized": True,
        },
    }
    atomic_json(args.out.resolve(), certificate)
    print(certificate["status"])
    print(f"rows: {validation['rows']}")
    print(f"materiality_passed: {validation['materiality_passed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
