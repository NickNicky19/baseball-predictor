"""Independent integrity checks for a shared-PA selection artifact chain."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.evaluation.multi_market_foundation import PA_OUTCOMES, sha256


VALID_STATUSES = {
    "SELECTION_PASSED_CANDIDATE_FROZEN",
    "SELECTION_REJECTED_NO_CANDIDATE",
}


def validate_selection_report(
    report_path: str | Path,
    *,
    repo_root: str | Path,
    evidence_root: str | Path,
) -> dict[str, Any]:
    report_file = Path(report_path)
    report = json.loads(report_file.read_text(encoding="utf-8"))
    root = Path(repo_root)
    evidence = Path(evidence_root)
    if report.get("schema_version") != "shared-pa-selection-report-v1":
        raise ValueError("unknown shared-PA selection report schema")
    if report.get("status") not in VALID_STATUSES:
        raise ValueError("unknown shared-PA selection status")
    if report.get("betting_authorized") is not False:
        raise ValueError("selection report cannot authorize betting")
    if report.get("may_2026_opened") is not False or report.get("confirmation_2025_opened") is not False:
        raise ValueError("selection report opened protected evidence")
    if report.get("selection_seasons") != [2023, 2024]:
        raise ValueError("selection seasons changed")
    if report.get("pa_target_population") != {
        "source_rows_retained": 87462,
        "positive_pa_rows_scored": 87430,
        "zero_pa_rows_zero_weight": 32,
        "zero_pa_rows_by_season": {"2023": 14, "2024": 18},
        "market_settlement_inferred": False,
        "model_coverage_inferred": False,
    }:
        raise ValueError("selection PA-target population or interpretation changed")
    sanitization = report.get("historical_feature_sanitization") or {}
    if sanitization.get("rows_sanitized") != 27 or sanitization.get("source_values_removed") != ["actual_starter"]:
        raise ValueError("selection postgame-starter sanitization changed")

    source = report.get("source") or {}
    source_path = evidence / str(source.get("path", ""))
    if not source_path.is_file() or sha256(source_path) != source.get("sha256"):
        raise ValueError("selection source is missing or hash-mismatched")
    protocol = report.get("protocol") or {}
    protocol_path = root / str(protocol.get("path", ""))
    if not protocol_path.is_file() or sha256(protocol_path) != protocol.get("sha256"):
        raise ValueError("selection protocol is missing or hash-mismatched")
    runtime = report.get("runtime") or {}
    if not isinstance(runtime.get("thread_count"), int) or runtime["thread_count"] < 1:
        raise ValueError("selection runtime thread count is invalid")
    for relative, expected_hash in (runtime.get("file_hashes") or {}).items():
        path = root / relative
        if not path.is_file() or sha256(path) != expected_hash:
            raise ValueError(f"selection runtime file changed: {relative}")

    oof = report.get("selection_oof") or {}
    oof_path = report_file.parent / str(oof.get("path", ""))
    if not oof_path.is_file() or sha256(oof_path) != oof.get("sha256"):
        raise ValueError("selection OOF predictions are missing or hash-mismatched")
    frame = pd.read_csv(oof_path, low_memory=False)
    if len(frame) != int(oof.get("rows", -1)) or oof.get("seasons") != [2024]:
        raise ValueError("selection OOF row count or season declaration changed")
    required = {"game_pk", "player_id", "game_date", "lineup_slot", "_selection_fold"}
    for prefix in ("actual", "best_simple", "candidate_raw", "candidate_crossfit_calibrated"):
        required.update(f"{prefix}_{outcome}" for outcome in PA_OUTCOMES)
    if not required.issubset(frame.columns):
        raise ValueError("selection OOF schema is incomplete")
    if frame[["game_pk", "player_id"]].isna().any().any() or frame.duplicated(["game_pk", "player_id"]).any():
        raise ValueError("selection OOF identity is null or duplicated")
    dates = pd.to_datetime(frame["game_date"], errors="coerce")
    if dates.isna().any() or set(dates.dt.year) != {2024}:
        raise ValueError("selection OOF chronology changed")
    if sorted(frame["_selection_fold"].astype(int).unique().tolist()) != [0, 1, 2, 3]:
        raise ValueError("selection OOF fold identity changed")
    for prefix in ("best_simple", "candidate_raw", "candidate_crossfit_calibrated"):
        probabilities = frame[[f"{prefix}_{outcome}" for outcome in PA_OUTCOMES]].to_numpy(float)
        if not np.isfinite(probabilities).all() or (probabilities < 0).any():
            raise ValueError(f"{prefix} probabilities are invalid")
        if not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-9):
            raise ValueError(f"{prefix} probabilities do not sum to one")
    actual = frame[[f"actual_{outcome}" for outcome in PA_OUTCOMES]].to_numpy(float)
    if not np.isfinite(actual).all() or (actual < 0).any() or (actual.sum(axis=1) <= 0).any():
        raise ValueError("selection OOF official counts are invalid")

    passed = report["status"] == "SELECTION_PASSED_CANDIDATE_FROZEN"
    frozen = report.get("frozen_model")
    if passed:
        if report.get("selected_variant") is None or not isinstance(frozen, dict):
            raise ValueError("passing selection did not freeze a candidate")
        model_path = report_file.parent / str(frozen.get("path", ""))
        if not model_path.is_file() or sha256(model_path) != frozen.get("sha256"):
            raise ValueError("frozen selection model is missing or hash-mismatched")
    elif report.get("selected_variant") is not None or frozen is not None:
        raise ValueError("rejected selection published a candidate")
    return report
