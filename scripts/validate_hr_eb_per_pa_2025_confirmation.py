#!/usr/bin/env python3
"""Independently certify the untouched-2025 per-PA HR confirmation result."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_eb_per_pa_confirmation import (
    ARMS, KEY, RESULT_SCHEMA, build_predictions, decide, load_protocol, metrics,
    paired_intervals, validate_source,
)
from src.evaluation.multi_market_foundation import sha256


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _period(frame: pd.DataFrame, dates: list[str]) -> dict[str, Any]:
    rows = frame[frame["game_date"].isin(dates)]
    arm_metrics = {arm: metrics(rows, arm) for arm in ARMS}
    return {
        "dates": dates,
        "metrics": arm_metrics,
        "deltas": {
            baseline: {
                name: float(arm_metrics["candidate"][name] - arm_metrics[baseline][name])
                for name in ("pa_weighted_binary_log_loss", "pa_weighted_binary_brier")
            }
            for baseline in ("rolling_league", "rolling_raw_player")
        },
    }


def _assert_close(actual: Any, expected: Any, label: str) -> None:
    if isinstance(expected, dict):
        if set(actual) != set(expected):
            raise ValueError(f"{label} keys changed")
        for key in expected:
            _assert_close(actual[key], expected[key], f"{label}.{key}")
    elif isinstance(expected, list):
        if len(actual) != len(expected):
            raise ValueError(f"{label} length changed")
        for index, value in enumerate(expected):
            _assert_close(actual[index], value, f"{label}[{index}]")
    elif isinstance(expected, float):
        if not np.isfinite(actual) or not np.isclose(float(actual), expected, rtol=1e-12, atol=1e-12):
            raise ValueError(f"{label} changed")
    elif actual != expected:
        raise ValueError(f"{label} changed")


def validate_loaded(protocol: dict[str, Any], source: pd.DataFrame, report: dict[str, Any], published: pd.DataFrame) -> dict[str, Any]:
    if report.get("schema_version") != RESULT_SCHEMA:
        raise ValueError("unrecognized confirmation report")
    if report.get("betting_authorized") is not False or report.get("production_unchanged") is not True:
        raise ValueError("confirmation report altered production or betting status")
    if report.get("may_2026_opened") is not False or report.get("confirmation_2025_opened") is not True:
        raise ValueError("confirmation seal state is false")
    if report.get("full_game_probability_confirmed") is not False or report.get("economic_evidence_generated") is not False:
        raise ValueError("per-PA confirmation made a broader claim")
    dates = list(protocol["confirmation_dates"])
    expected, coverage = build_predictions(
        source, dates, float(protocol["candidate"]["prior_strength_pa"]),
    )
    if list(published.columns) != list(expected.columns):
        raise ValueError("published prediction schema changed")
    if published[KEY].isna().any().any() or published.duplicated(KEY).any():
        raise ValueError("published prediction identity is null or duplicated")
    published = published.sort_values(KEY).reset_index(drop=True)
    pd.testing.assert_frame_equal(
        published, expected, check_dtype=False, check_exact=False, rtol=1e-13, atol=1e-13,
    )
    split = len(dates) // 2
    periods = {
        "early": _period(expected, dates[:split]),
        "late": _period(expected, dates[split:]),
        "full": _period(expected, dates),
    }
    draws = int(protocol["metrics"]["bootstrap_draws"])
    seed = int(protocol["metrics"]["bootstrap_seed"])
    comparisons = {
        baseline: {"intervals": paired_intervals(
            expected, "candidate", baseline, dates=dates, draws=draws, seed=seed,
        )}
        for baseline in ("rolling_league", "rolling_raw_player")
    }
    _assert_close(report.get("periods"), periods, "periods")
    _assert_close(report.get("comparisons"), comparisons, "comparisons")
    if report.get("coverage") != coverage or report.get("prediction_rows") != len(expected):
        raise ValueError("confirmation coverage changed")
    decision_input = dict(report)
    decision_input["periods"] = periods
    decision_input["comparisons"] = comparisons
    expected_decision = decide(decision_input)
    if report.get("decision") != expected_decision:
        raise ValueError("confirmation decision changed")
    expected_status = (
        "PER_PA_HR_COMPONENT_CONFIRMED_FULL_GAME_AND_ECONOMICS_BLOCKED"
        if expected_decision["per_pa_component_confirmed"]
        else "PER_PA_HR_COMPONENT_REJECTED_ON_UNTOUCHED_2025"
    )
    if report.get("status") != expected_status:
        raise ValueError("confirmation status disagrees with decision")
    if report.get("protected_invariants") != protocol.get("protected_invariants"):
        raise ValueError("protected invariants changed")
    return {
        "status": "HR_EB_PER_PA_2025_CONFIRMATION_CERTIFIED",
        "report_status": expected_status,
        "per_pa_component_confirmed": expected_decision["per_pa_component_confirmed"],
        "full_game_probability_confirmed": False,
        "betting_authorized": False,
        "prediction_rows": len(expected),
        "plate_appearances": int(expected["out_pa"].sum()),
        "home_runs": int(expected["out_hr"].sum()),
    }


def atomic_json(payload: dict[str, Any], path: Path) -> None:
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write((json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence_root.resolve()
    protocol = load_protocol(args.protocol.resolve(), evidence_root=root)
    report_path = args.out_dir.resolve() / "report.json"
    prediction_path = args.out_dir.resolve() / "predictions.csv"
    report = _json(report_path)
    artifacts = report.get("artifacts") or {}
    if artifacts.get("predictions", {}).get("sha256") != sha256(prediction_path):
        raise ValueError("published prediction hash changed")
    if artifacts.get("protocol", {}).get("sha256") != sha256(args.protocol.resolve()):
        raise ValueError("published protocol hash changed")
    source = validate_source(pd.read_csv(
        root / protocol["inputs"]["training"]["path"], compression="gzip",
    ))
    certificate = validate_loaded(protocol, source, report, pd.read_csv(prediction_path))
    certificate["artifacts"] = {
        "protocol": {"path": str(args.protocol.resolve()), "sha256": sha256(args.protocol.resolve())},
        "report": {"path": str(report_path), "sha256": sha256(report_path)},
        "predictions": {"path": str(prediction_path), "sha256": sha256(prediction_path)},
    }
    certificate_path = args.out_dir.resolve() / "certificate.json"
    atomic_json(certificate, certificate_path)
    print(certificate["status"])
    print(f"report_status={certificate['report_status']}")
    print(f"certificate_sha256={sha256(certificate_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
