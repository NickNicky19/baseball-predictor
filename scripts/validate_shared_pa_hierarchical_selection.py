#!/usr/bin/env python3
"""Independently certify the hierarchical 2024 selector."""
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

from scripts.select_shared_pa_hierarchical_challenger import (  # noqa: E402
    PRIMARY, derived_market_report, interval_report,
)
from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_cumulative_selection import load_bound_pa_distribution  # noqa: E402
from src.evaluation.shared_pa_hierarchical_selection import (  # noqa: E402
    MARKETS, clears_primary, load_protocol,
)
from src.evaluation.shared_pa_rejection_diagnostic import sha256  # noqa: E402
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.learning.shared_pa_model import proper_scores  # noqa: E402


IDENTITY = ["game_pk", "player_id"]


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise ValueError("hierarchical certificate already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def close(expected: Any, actual: Any, label: str) -> None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            raise ValueError(f"hierarchical {label} keys changed")
        for key in expected:
            close(expected[key], actual[key], f"{label}/{key}")
        return
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            raise ValueError(f"hierarchical {label} length changed")
        for index, (left, right) in enumerate(zip(expected, actual)):
            close(left, right, f"{label}/{index}")
        return
    if isinstance(expected, bool) or isinstance(actual, bool):
        if expected is not actual:
            raise ValueError(f"hierarchical {label} boolean changed")
        return
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if not math.isclose(float(expected), float(actual), rel_tol=0.0, abs_tol=1e-14):
            raise ValueError(f"hierarchical {label} numeric value changed")
        return
    if expected != actual:
        raise ValueError(f"hierarchical {label} value changed")


def matrix(oof: pd.DataFrame, prefix: str) -> np.ndarray:
    columns = [f"{prefix}_{outcome}" for outcome in PA_OUTCOMES]
    if not set(columns).issubset(oof.columns):
        raise ValueError(f"hierarchical OOF matrix missing: {prefix}")
    values = oof[columns].to_numpy(float)
    if not np.isfinite(values).all() or (values < 0).any() or not np.allclose(values.sum(axis=1), 1.0, atol=1e-9):
        raise ValueError(f"hierarchical OOF matrix invalid: {prefix}")
    return values


def scoring_frame_from_actual(oof: pd.DataFrame, actual: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct raw official-count columns exactly from exhaustive PA classes."""
    scoring = oof.copy()
    scoring["out_k"] = actual["strikeout"].to_numpy()
    scoring["out_bb"] = actual["walk"].to_numpy()
    scoring["out_hits"] = actual[["single", "double", "triple", "home_run"]].sum(axis=1).to_numpy()
    scoring["out_doubles"] = actual["double"].to_numpy()
    scoring["out_triples"] = actual["triple"].to_numpy()
    scoring["out_hr"] = actual["home_run"].to_numpy()
    scoring["out_ab"] = actual[["strikeout", "single", "double", "triple", "home_run", "bip_out"]].sum(axis=1).to_numpy()
    scoring["out_pa"] = actual.sum(axis=1).to_numpy()
    reconstructed = outcome_counts(scoring).loc[:, PA_OUTCOMES]
    if not np.array_equal(reconstructed.to_numpy(float), actual.loc[:, PA_OUTCOMES].to_numpy(float)):
        raise ValueError("hierarchical validator outcome round trip changed")
    return scoring


def validate_report(
    report: dict[str, Any],
    oof: pd.DataFrame,
    protocol: dict[str, Any],
    base: dict[str, Any],
    *,
    evidence_root: Path,
) -> dict[str, Any]:
    if report.get("schema_version") != "shared-pa-hierarchical-selection-report-v1":
        raise ValueError("hierarchical report schema changed")
    if report.get("status") not in {"HIERARCHICAL_SELECTION_PASSED_CANDIDATE_FROZEN", "HIERARCHICAL_SELECTION_REJECTED_NO_CANDIDATE"}:
        raise ValueError("hierarchical report status is invalid")
    if report.get("betting_authorized") or report.get("production_changed"):
        raise ValueError("hierarchical report changed authorization or production")
    if report.get("confirmation_2025_opened") or report.get("may_2026_opened"):
        raise ValueError("hierarchical report crossed protected evidence")
    if report.get("protected_invariants") != {
        "confirmation_2025_unread": True, "may_2026_unread": True,
        "production_unchanged": True, "betting_unauthorized": True,
    }:
        raise ValueError("hierarchical protected invariants changed")
    base_columns = [*IDENTITY, "game_date", "lineup_slot", "_selection_fold"]
    actual_columns = [f"actual_{outcome}" for outcome in PA_OUTCOMES]
    prefixes = ["strongest_simple", "canonical_v1", "hierarchical_raw", "hierarchical_selected"]
    expected_columns = set(base_columns + actual_columns)
    for prefix in prefixes:
        expected_columns.update(f"{prefix}_{outcome}" for outcome in PA_OUTCOMES)
    for record in report["stability"]["records"]:
        expected_columns.update(f"hierarchical_seed_{int(record['seed'])}_{outcome}" for outcome in PA_OUTCOMES)
    if set(oof.columns) != expected_columns:
        raise ValueError("hierarchical OOF schema changed")
    if len(oof) != int(report["oof_artifact"]["rows"]) or oof.duplicated(IDENTITY).any() or oof[base_columns].isna().any().any():
        raise ValueError("hierarchical OOF identity or rows changed")
    dates = pd.to_datetime(oof["game_date"], format="%Y-%m-%d", errors="raise")
    if set(dates.dt.year) != {2024} or dates.dt.date.nunique() != 183 or set(oof["_selection_fold"].astype(int)) != {0, 1, 2, 3}:
        raise ValueError("hierarchical OOF chronology changed")
    actual = oof[actual_columns].copy(); actual.columns = PA_OUTCOMES
    values = actual.to_numpy(float)
    if not np.isfinite(values).all() or (values < 0).any() or not np.allclose(values, np.rint(values)) or (values.sum(axis=1) <= 0).any():
        raise ValueError("hierarchical OOF outcomes are invalid")
    scoring = scoring_frame_from_actual(oof, actual)
    matrices = {prefix: matrix(oof, prefix) for prefix in prefixes}
    close(report["comparators"]["strongest_simple"]["scores"], proper_scores(actual, matrices["strongest_simple"]), "simple scores")
    close(report["comparators"]["canonical_v1"]["scores"], proper_scores(actual, matrices["canonical_v1"]), "v1 scores")
    close(protocol["comparators"]["required_exact_simple_scores"], report["comparators"]["strongest_simple"]["scores"], "locked simple scores")
    close(protocol["comparators"]["required_exact_canonical_v1_scores"], report["comparators"]["canonical_v1"]["scores"], "locked v1 scores")
    close(report["raw"]["scores"], proper_scores(actual, matrices["hierarchical_raw"]), "raw scores")
    raw_simple = interval_report(scoring, matrices["hierarchical_raw"], matrices["strongest_simple"], protocol)
    raw_v1 = interval_report(scoring, matrices["hierarchical_raw"], matrices["canonical_v1"], protocol)
    close(report["raw"]["intervals_vs_strongest_simple"], raw_simple, "raw/simple intervals")
    close(report["raw"]["intervals_vs_canonical_v1"], raw_v1, "raw/v1 intervals")
    raw_passed, raw_decisions = clears_primary(protocol, vs_simple=raw_simple, vs_v1=raw_v1)
    close(report["raw"]["decisions"], raw_decisions, "raw decisions")
    if bool(report["raw"]["primary_passed"]) != raw_passed:
        raise ValueError("hierarchical raw decision changed")

    stability = report["stability"]
    if bool(stability["evaluated"]) != raw_passed:
        raise ValueError("hierarchical stability boundary changed")
    expected_seeds = protocol["model"]["stability_audit_seeds"] if raw_passed else []
    if [int(item["seed"]) for item in stability["records"]] != expected_seeds:
        raise ValueError("hierarchical stability seed coverage changed")
    stability_passed = bool(raw_passed)
    for record in stability["records"]:
        probability = matrix(oof, f"hierarchical_seed_{int(record['seed'])}")
        score = proper_scores(actual, probability)
        close(record["scores"], score, "stability scores")
        vs_simple = interval_report(scoring, probability, matrices["strongest_simple"], protocol)
        vs_v1 = interval_report(scoring, probability, matrices["canonical_v1"], protocol)
        close(record["intervals_vs_strongest_simple"], vs_simple, "stability/simple")
        close(record["intervals_vs_canonical_v1"], vs_v1, "stability/v1")
        passed = all(float(group[metric]["point"]) < 0.0 and float(group[metric]["upper"]) < 0.0 for group in (vs_simple, vs_v1) for metric in PRIMARY)
        if bool(record["passed"]) != passed:
            raise ValueError("hierarchical stability record decision changed")
        stability_passed = stability_passed and passed
    if bool(stability["passed"]) != stability_passed:
        raise ValueError("hierarchical stability aggregate changed")

    calibration = report["calibration"]
    if bool(calibration["evaluated"]) != bool(raw_passed and stability_passed):
        raise ValueError("hierarchical calibration boundary changed")
    if calibration["installed"]:
        selected_scores = proper_scores(actual, matrices["hierarchical_selected"])
        close(calibration["scores"], selected_scores, "calibrated scores")
        intervals = interval_report(scoring, matrices["hierarchical_selected"], matrices["hierarchical_raw"], protocol)
        close(calibration["intervals_vs_raw"], intervals, "calibration intervals")
        if not all(float(selected_scores[m]) < float(report["raw"]["scores"][m]) and float(intervals[m]["upper"]) < 0.0 for m in PRIMARY):
            raise ValueError("hierarchical calibration installation is unsupported")
    elif not np.array_equal(matrices["hierarchical_selected"], matrices["hierarchical_raw"]):
        raise ValueError("hierarchical uninstalled calibration changed probabilities")

    close(report["selected"]["scores"], proper_scores(actual, matrices["hierarchical_selected"]), "selected scores")
    selected_simple = interval_report(scoring, matrices["hierarchical_selected"], matrices["strongest_simple"], protocol)
    selected_v1 = interval_report(scoring, matrices["hierarchical_selected"], matrices["canonical_v1"], protocol)
    close(report["selected"]["intervals_vs_strongest_simple"], selected_simple, "selected/simple")
    close(report["selected"]["intervals_vs_canonical_v1"], selected_v1, "selected/v1")
    selected_primary, selected_decisions = clears_primary(protocol, vs_simple=selected_simple, vs_v1=selected_v1)
    close(report["selected"]["decisions"], selected_decisions, "selected decisions")
    if bool(report["selected"]["primary_passed"]) != selected_primary:
        raise ValueError("hierarchical selected primary decision changed")
    pa_distribution = load_bound_pa_distribution(base, evidence_root=evidence_root)
    derived = derived_market_report(
        scoring, matrices["hierarchical_selected"], matrices["strongest_simple"], matrices["canonical_v1"],
        protocol, pa_distribution,
    )
    close(report["derived_markets"], derived, "derived markets")
    eligible = [market for market in MARKETS if derived[market]["eligible_for_confirmation"]]
    if report["eligible_markets_for_2025_confirmation"] != eligible:
        raise ValueError("hierarchical eligible market list changed")
    foundation_passed = bool(selected_primary and stability_passed)
    selection_passed = bool(foundation_passed and eligible)
    if bool(report["foundation_passed"]) != foundation_passed or bool(report["selection_passed"]) != selection_passed:
        raise ValueError("hierarchical final decision changed")
    expected_status = "HIERARCHICAL_SELECTION_PASSED_CANDIDATE_FROZEN" if selection_passed else "HIERARCHICAL_SELECTION_REJECTED_NO_CANDIDATE"
    if report["status"] != expected_status:
        raise ValueError("hierarchical final status changed")
    if selection_passed:
        artifacts = report.get("model_artifacts")
        if not artifacts:
            raise ValueError("hierarchical passing model artifacts are absent")
        for stage in ("stage_1", "stage_2"):
            path = Path(artifacts[stage]["path"])
            if not path.is_file() or sha256(path) != artifacts[stage]["sha256"]:
                raise ValueError(f"hierarchical passing model changed: {stage}")
    elif report.get("model_artifacts") is not None:
        raise ValueError("hierarchical rejected selector published a model")
    return {
        "rows": int(len(oof)), "dates": int(oof["game_date"].nunique()),
        "raw_primary_passed": bool(raw_passed), "stability_passed": bool(stability_passed),
        "selected_primary_passed": bool(selected_primary), "foundation_passed": foundation_passed,
        "eligible_markets": eligible, "selection_passed": selection_passed,
        "selected_scores": proper_scores(actual, matrices["hierarchical_selected"]),
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
        raise ValueError("hierarchical protocol hash changed")
    protocol, _, base = load_protocol(protocol_path, code_root=ROOT, evidence_root=evidence_root)
    if report.get("inputs") != protocol["inputs"]:
        raise ValueError("hierarchical report input bindings changed")
    retry_path = ROOT / "config/shared_pa_hierarchical_validator_retry_v2.json"
    retry = json.loads(retry_path.read_text(encoding="utf-8"))
    if retry.get("status") != "LOCKED_VALIDATOR_ONLY_RETRY_BEFORE_CERTIFICATION":
        raise ValueError("hierarchical validator retry is not locked")
    failure_path = ROOT / retry["failed_validator_record"]["path"]
    if not failure_path.is_file() or sha256(failure_path) != retry["failed_validator_record"]["sha256"]:
        raise ValueError("hierarchical validator failure record changed")
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    retry_v3_path = ROOT / "config/shared_pa_hierarchical_validator_retry_v3.json"
    retry_v3 = json.loads(retry_v3_path.read_text(encoding="utf-8"))
    if retry_v3.get("status") != "LOCKED_SCOPE_RESTORATION_BEFORE_CERTIFICATION":
        raise ValueError("hierarchical validator scope restoration is not locked")
    failure_v2_path = ROOT / retry_v3["failure_v2_record"]["path"]
    if not failure_v2_path.is_file() or sha256(failure_v2_path) != retry_v3["failure_v2_record"]["sha256"]:
        raise ValueError("hierarchical validator v2 failure record changed")
    failure_v2 = json.loads(failure_v2_path.read_text(encoding="utf-8"))
    if retry_v3["required_hashes"]["repaired_validator_before_v3"] != failure_v2["repaired_validator_sha256"]:
        raise ValueError("hierarchical repaired validator predecessor changed")
    mutation_checker = ROOT / "scripts/check_shared_pa_hierarchical_selection_validator_mutations.py"
    if sha256(mutation_checker) != retry_v3["required_hashes"]["restored_mutation_checker"]:
        raise ValueError("hierarchical validator mutation checker was not restored")
    report_expected = failure["unchanged_complete_selector_artifacts"]["report"]
    oof_expected = failure["unchanged_complete_selector_artifacts"]["oof"]
    if sha256(report_path) != report_expected["sha256"]:
        raise ValueError("hierarchical selector report changed during validator retry")
    for relative, expected in report["runtime"]["file_hashes"].items():
        path = ROOT / relative
        if relative == "scripts/validate_shared_pa_hierarchical_selection.py":
            if expected != failure["failed_validator"]["sha256"]:
                raise ValueError("hierarchical failed validator binding changed")
            continue
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"hierarchical runtime changed: {relative}")
    oof_path = Path(report["oof_artifact"]["path"])
    if not oof_path.is_file() or sha256(oof_path) != report["oof_artifact"]["sha256"] or sha256(oof_path) != oof_expected["sha256"]:
        raise ValueError("hierarchical OOF artifact changed")
    validation = validate_report(report, pd.read_csv(oof_path, low_memory=False), protocol, base, evidence_root=evidence_root)
    certificate = {
        "schema_version": "shared-pa-hierarchical-selection-certificate-v1",
        "certified_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "HIERARCHICAL_SELECTION_PASS_CERTIFIED" if validation["selection_passed"] else "HIERARCHICAL_SELECTION_REJECTION_CERTIFIED",
        "betting_authorized": False, "production_changed": False,
        "confirmation_2025_opened": False, "may_2026_opened": False,
        "report": {"path": str(report_path), "sha256": sha256(report_path)},
        "protocol": report["protocol"], "oof_artifact": report["oof_artifact"],
        "validator_retries": {
            "v2": {
                "path": "config/shared_pa_hierarchical_validator_retry_v2.json",
                "sha256": sha256(retry_path),
                "failure_record_sha256": sha256(failure_path)
            },
            "v3": {
                "path": "config/shared_pa_hierarchical_validator_retry_v3.json",
                "sha256": sha256(retry_v3_path),
                "failure_record_sha256": sha256(failure_v2_path),
                "restored_mutation_checker_sha256": sha256(mutation_checker)
            },
            "final_repaired_validator_sha256": sha256(Path(__file__)),
        },
        "validation": validation, "protected_invariants": report["protected_invariants"],
    }
    atomic_json(args.out.resolve(), certificate)
    print(json.dumps({"status": certificate["status"], **validation}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
