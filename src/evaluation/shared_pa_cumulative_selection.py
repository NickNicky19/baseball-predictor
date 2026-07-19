"""Gates for the single cumulative-history shared-PA intervention."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.evaluation.multi_market_foundation import PA_OUTCOMES
from src.evaluation.shared_pa_canonical_selection import (
    clears_materiality,
    feature_columns,
    load_protocol as load_base_protocol,
    load_selection_frame as load_base_frame,
    sha256,
)


IDENTITY = ["game_pk", "player_id"]


def _root_for_input(name: str, *, evidence_root: Path, code_root: Path) -> Path:
    return code_root if name in {"base_selection_protocol", "cumulative_reproducibility"} else evidence_root


def load_protocol(path: Path, *, evidence_root: Path, code_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if protocol.get("status") != "LOCKED_BEFORE_2023_2024_CUMULATIVE_SELECTION":
        raise ValueError("cumulative selection protocol is not locked")
    if not protocol.get("confirmation_2025_forbidden_during_selection") or not protocol.get("may_2026_forbidden"):
        raise ValueError("cumulative selection does not protect confirmation evidence")
    for name, record in protocol["inputs"].items():
        artifact = _root_for_input(name, evidence_root=evidence_root, code_root=code_root) / record["path"]
        if not artifact.exists() or sha256(artifact) != record["sha256"]:
            raise ValueError(f"cumulative selection input hash changed: {name}")
    base_record = protocol["inputs"]["base_selection_protocol"]
    base_path = code_root / base_record["path"]
    base = load_base_protocol(base_path, evidence_root=evidence_root, code_root=code_root)
    certificate = json.loads((evidence_root / protocol["inputs"]["cumulative_certificate"]["path"]).read_text(encoding="utf-8"))
    if certificate.get("status") != protocol["inputs"]["cumulative_certificate"]["required_status"]:
        raise ValueError("cumulative feature certificate status changed")
    if certificate.get("confirmation_2025_read") or certificate.get("may_2026_read"):
        raise ValueError("cumulative feature certificate crossed protected evidence")
    if certificate.get("artifact", {}).get("sha256") != protocol["inputs"]["cumulative_features"]["sha256"]:
        raise ValueError("cumulative feature certificate artifact binding changed")
    reproducibility = json.loads((code_root / protocol["inputs"]["cumulative_reproducibility"]["path"]).read_text(encoding="utf-8"))
    if reproducibility.get("status") != protocol["inputs"]["cumulative_reproducibility"]["required_status"]:
        raise ValueError("cumulative feature reproducibility status changed")
    if not reproducibility.get("replay", {}).get("byte_identical"):
        raise ValueError("cumulative feature replay is not byte-identical")
    v1_report = json.loads((evidence_root / protocol["inputs"]["canonical_v1_report"]["path"]).read_text(encoding="utf-8"))
    v1_certificate = json.loads((evidence_root / protocol["inputs"]["canonical_v1_certificate"]["path"]).read_text(encoding="utf-8"))
    if v1_report.get("status") != protocol["inputs"]["canonical_v1_report"]["required_status"]:
        raise ValueError("canonical v1 comparator status changed")
    if v1_certificate.get("status") != protocol["inputs"]["canonical_v1_certificate"]["required_status"]:
        raise ValueError("canonical v1 comparator certificate changed")
    if v1_report.get("confirmation_2025_opened") or v1_report.get("may_2026_opened"):
        raise ValueError("canonical v1 comparator crossed protected evidence")
    return protocol, base


def load_selection_frame(
    protocol: dict[str, Any],
    base_protocol: dict[str, Any],
    *,
    evidence_root: Path,
) -> pd.DataFrame:
    base = load_base_frame(base_protocol, evidence_root=evidence_root)
    history = pd.read_csv(evidence_root / protocol["inputs"]["cumulative_features"]["path"], low_memory=False)
    if len(history) != int(protocol["inputs"]["cumulative_features"]["rows"]):
        raise ValueError("cumulative selection history length changed")
    expected = {"season", "game_date", *IDENTITY, *protocol["feature_groups"]["canonical_hitter_cumulative"]}
    if set(history.columns) != expected:
        raise ValueError("cumulative selection history schema changed")
    if history.duplicated(IDENTITY).any() or history[IDENTITY].isna().any().any():
        raise ValueError("cumulative selection history identity is invalid")
    if set(history["season"].astype(int)) != {2023, 2024}:
        raise ValueError("cumulative selection history crossed into 2025")
    merged = base.merge(history, on=IDENTITY, how="inner", validate="one_to_one", suffixes=("", "_history"))
    if len(merged) != len(base):
        raise ValueError("cumulative selection history coverage differs")
    for column in ("season", "game_date"):
        other = f"{column}_history"
        if not merged[column].astype(str).equals(merged[other].astype(str)):
            raise ValueError(f"cumulative selection history {column} differs")
        merged.drop(columns=[other], inplace=True)
    for variant in protocol["feature_variants"]:
        missing = sorted(set(feature_columns(protocol, variant["id"])) - set(merged.columns))
        if missing:
            raise ValueError(f"cumulative selection features missing: {missing}")
    return merged.sort_values(["game_date", "game_pk", "player_id"]).reset_index(drop=True)


def load_v1_comparator(protocol: dict[str, Any], *, evidence_root: Path) -> pd.DataFrame:
    frame = pd.read_csv(evidence_root / protocol["inputs"]["canonical_v1_oof"]["path"], low_memory=False)
    if len(frame) != int(protocol["inputs"]["canonical_v1_oof"]["rows"]):
        raise ValueError("canonical v1 OOF length changed")
    required = [*IDENTITY, "game_date", "lineup_slot", "_selection_fold"]
    required += [f"canonical_selected_{name}" for name in PA_OUTCOMES]
    if not set(required).issubset(frame.columns):
        raise ValueError("canonical v1 OOF schema changed")
    if frame.duplicated(IDENTITY).any() or frame[IDENTITY].isna().any().any():
        raise ValueError("canonical v1 OOF identity is invalid")
    if set(pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise").dt.year) != {2024}:
        raise ValueError("canonical v1 OOF chronology changed")
    probabilities = frame[[f"canonical_selected_{name}" for name in PA_OUTCOMES]].to_numpy(float)
    if not np.isfinite(probabilities).all() or (probabilities < 0).any() or not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-9):
        raise ValueError("canonical v1 OOF probabilities are invalid")
    return frame[required].copy()


def aligned_v1_probabilities(validation: pd.DataFrame, comparator: pd.DataFrame) -> np.ndarray:
    left = validation[[*IDENTITY, "game_date", "lineup_slot", "_selection_fold"]].copy()
    aligned = left.merge(comparator, on=IDENTITY, how="left", validate="one_to_one", suffixes=("", "_v1"))
    if aligned.isna().any().any():
        raise ValueError("canonical v1 comparator coverage differs")
    for column in ("game_date", "lineup_slot", "_selection_fold"):
        if not aligned[column].astype(str).equals(aligned[f"{column}_v1"].astype(str)):
            raise ValueError(f"canonical v1 comparator {column} differs")
    return aligned[[f"canonical_selected_{name}" for name in PA_OUTCOMES]].to_numpy(float)


def clears_all_comparisons(
    *,
    simple_intervals: dict[str, dict[str, float]],
    simple_floor: dict[str, float],
    v1_intervals: dict[str, dict[str, float]],
) -> tuple[bool, dict[str, Any]]:
    simple_passed, simple_decisions = clears_materiality(simple_intervals, simple_floor)
    v1_decisions: dict[str, Any] = {}
    for metric in ("multiclass_log_loss", "multiclass_brier"):
        interval = v1_intervals[metric]
        passed = float(interval["point"]) < 0.0 and float(interval["upper"]) < 0.0
        v1_decisions[metric] = {
            "required_candidate_minus_v1_below": 0.0,
            "point": float(interval["point"]),
            "upper": float(interval["upper"]),
            "passed": bool(passed),
        }
    v1_passed = all(item["passed"] for item in v1_decisions.values())
    return bool(simple_passed and v1_passed), {
        "vs_strongest_simple": simple_decisions,
        "vs_canonical_v1": v1_decisions,
    }


def assert_expected_simple(report: dict[str, Any], protocol: dict[str, Any]) -> None:
    expected = protocol["simple_baseline_selection"]
    if report.get("selected") != expected["required_exact_selected"]:
        raise ValueError("strongest simple baseline changed")
    scores = report[report["selected"]]["scores"]
    for metric, value in expected["required_exact_scores"].items():
        if not math.isclose(float(scores[metric]), float(value), rel_tol=0.0, abs_tol=1e-14):
            raise ValueError(f"strongest simple baseline score changed: {metric}")


def validate_pa_distribution_payload(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    if not isinstance(payload, dict) or "by_lineup_slot" not in payload:
        raise ValueError("PA distribution wrapper is missing by_lineup_slot")
    distributions = payload["by_lineup_slot"]
    if not isinstance(distributions, dict) or set(distributions) != {str(slot) for slot in range(1, 10)}:
        raise ValueError("PA distribution does not cover exact lineup slots 1 through 9")
    validated: dict[str, dict[str, float]] = {}
    for slot, raw in distributions.items():
        if not isinstance(raw, dict) or not raw:
            raise ValueError(f"PA distribution is empty for lineup slot {slot}")
        values = np.asarray([float(value) for value in raw.values()], dtype=float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f"PA distribution is invalid for lineup slot {slot}")
        if not math.isclose(float(values.sum()), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(f"PA distribution does not sum to one for lineup slot {slot}")
        if any(int(pa) < 0 for pa in raw):
            raise ValueError(f"PA distribution contains a negative count for lineup slot {slot}")
        validated[str(slot)] = {str(pa): float(value) for pa, value in raw.items()}
    return validated


def load_bound_pa_distribution(base_protocol: dict[str, Any], *, evidence_root: Path) -> dict[str, dict[str, float]]:
    record = base_protocol["inputs"]["pa_distribution"]
    path = evidence_root / record["path"]
    if not path.exists() or sha256(path) != record["sha256"]:
        raise ValueError("bound PA distribution hash changed")
    return validate_pa_distribution_payload(json.loads(path.read_text(encoding="utf-8")))


def load_retry_authorization(path: Path, *, code_root: Path) -> dict[str, Any]:
    retry = json.loads(path.read_text(encoding="utf-8"))
    if retry.get("status") != "LOCKED_MECHANICAL_RETRY_BEFORE_RESULT_READ":
        raise ValueError("cumulative retry authorization is not locked")
    if retry.get("betting_authorized") or not retry.get("production_unchanged"):
        raise ValueError("cumulative retry altered authorization or production")
    if not retry.get("confirmation_2025_forbidden") or not retry.get("may_2026_forbidden"):
        raise ValueError("cumulative retry does not protect confirmation evidence")
    if retry.get("allowed_change", {}).get("id") != "extract_bound_by_lineup_slot_distribution":
        raise ValueError("cumulative retry scope changed")
    if not retry.get("failed_output_must_remain_quarantined"):
        raise ValueError("cumulative retry released its failed output")
    record = retry["failed_attempt_record"]
    failure_path = code_root / record["path"]
    if not failure_path.exists() or sha256(failure_path) != record["sha256"]:
        raise ValueError("cumulative failed-attempt record changed")
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    if failure.get("status") != "INVALID_INCOMPLETE_NOT_ADJUDICATED":
        raise ValueError("cumulative failed attempt is not invalidated")
    if failure.get("partial_output", {}).get("scores_inspected"):
        raise ValueError("cumulative failed attempt was inspected before retry")
    if failure.get("confirmation_2025_opened") or failure.get("may_2026_opened"):
        raise ValueError("cumulative failed attempt crossed protected evidence")
    return retry


def load_retry_v3_authorization(path: Path, *, code_root: Path) -> dict[str, Any]:
    retry = json.loads(path.read_text(encoding="utf-8"))
    if retry.get("status") != "LOCKED_SECOND_MECHANICAL_RETRY_BEFORE_RESULT_READ":
        raise ValueError("cumulative retry v3 is not locked")
    if retry.get("betting_authorized") or not retry.get("production_unchanged"):
        raise ValueError("cumulative retry v3 altered authorization or production")
    if not retry.get("confirmation_2025_forbidden") or not retry.get("may_2026_forbidden"):
        raise ValueError("cumulative retry v3 does not protect confirmation evidence")
    expected_changes = {
        "replace_python_name_true_with_True",
        "publish_complete_selector_directory_atomically",
        "add_execution_level_report_contract_and_static_name_checks",
    }
    if set(retry.get("allowed_changes", [])) != expected_changes:
        raise ValueError("cumulative retry v3 scope changed")
    if not retry.get("all_failed_outputs_must_remain_quarantined"):
        raise ValueError("cumulative retry v3 released a failed output")
    for name in ("previous_retry", "failed_attempt_record"):
        record = retry[name]
        artifact = code_root / record["path"]
        if not artifact.exists() or sha256(artifact) != record["sha256"]:
            raise ValueError(f"cumulative retry v3 binding changed: {name}")
    previous = json.loads((code_root / retry["previous_retry"]["path"]).read_text(encoding="utf-8"))
    if previous.get("status") != "LOCKED_MECHANICAL_RETRY_BEFORE_RESULT_READ":
        raise ValueError("cumulative retry v3 predecessor changed")
    failure = json.loads((code_root / retry["failed_attempt_record"]["path"]).read_text(encoding="utf-8"))
    if failure.get("status") != "INVALID_INCOMPLETE_NOT_ADJUDICATED":
        raise ValueError("cumulative retry v3 failed attempt is not invalidated")
    if failure.get("partial_output", {}).get("scores_inspected"):
        raise ValueError("cumulative retry v3 failed attempt was inspected")
    if failure.get("confirmation_2025_opened") or failure.get("may_2026_opened"):
        raise ValueError("cumulative retry v3 failed attempt crossed protected evidence")
    return retry
