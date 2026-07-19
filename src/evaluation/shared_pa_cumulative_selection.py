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
