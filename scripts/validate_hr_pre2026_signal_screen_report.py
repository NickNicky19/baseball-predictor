#!/usr/bin/env python3
"""Validate the create-once corrected HR batted-ball signal report."""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_hr_pre2026_batted_ball_signal_screen import load_protocol  # noqa: E402
from src.utils.provenance import sha256_file  # noqa: E402


def validate_report(report: dict[str, Any], protocol: dict[str, Any], protocol_path: Path) -> dict[str, Any]:
    if report.get("schema_version") != "hr-pre2026-batted-ball-signal-screen-report-v2":
        raise ValueError("signal report schema mismatch")
    if report.get("protocol_sha256") != sha256_file(protocol_path):
        raise ValueError("signal report protocol hash mismatch")
    input_path = ROOT / protocol["source_input"]["path"]
    if report.get("input_sha256") != sha256_file(input_path):
        raise ValueError("signal report input hash mismatch")
    if report.get("period_rows") != {
        "2023_fit": 43740, "2024_selection": 43722, "2025_confirmation": 43740
    }:
        raise ValueError("signal report period population mismatch")
    grid = {float(v) for v in protocol["model_family"]["C_grid"]}
    selected = report.get("selected_regularization") or {}
    if float(selected.get("baseline_C", -1)) not in grid or float(selected.get("candidate_C", -1)) not in grid:
        raise ValueError("signal report selected regularization not in locked grid")
    checks = report.get("success_checks") or {}
    expected_checks = set(protocol["success_condition"])
    if set(checks) != expected_checks:
        raise ValueError("signal report success checks changed")
    passed = all(value is True for value in checks.values())
    expected_status = "PASS_QUALIFIES_MODEL_RECONSTRUCTION" if passed else "REJECTED_SIGNAL_SCREEN"
    if report.get("status") != expected_status:
        raise ValueError("signal report status/checks disagree")
    confirmation = report.get("confirmation") or {}
    baseline = confirmation.get("baseline") or {}
    candidate = confirmation.get("candidate") or {}
    for arm in (baseline, candidate):
        if not all(math.isfinite(float(arm[key])) for key in (
            "brier", "log_loss", "roc_auc", "mean_probability",
            "observed_rate", "mean_probability_bias",
        )):
            raise ValueError("signal report has non-finite metric")
    b_delta = float(candidate["brier"]) - float(baseline["brier"])
    l_delta = float(candidate["log_loss"]) - float(baseline["log_loss"])
    if not math.isclose(b_delta, float(confirmation["candidate_minus_baseline_brier"]), abs_tol=1e-15):
        raise ValueError("signal Brier delta arithmetic mismatch")
    if not math.isclose(l_delta, float(confirmation["candidate_minus_baseline_log_loss"]), abs_tol=1e-15):
        raise ValueError("signal log-loss delta arithmetic mismatch")
    if passed:
        if b_delta >= 0 or l_delta >= 0 or float(candidate["roc_auc"]) < float(baseline["roc_auc"]):
            raise ValueError("signal PASS contradicts confirmation metrics")
        for name in ("brier_paired_date_interval", "log_loss_paired_date_interval"):
            interval = confirmation[name]
            if int(interval["dates"]) != 184 or float(interval["upper_95"]) >= 0:
                raise ValueError("signal PASS contradicts paired interval")
    decision = report.get("decision") or {}
    if decision != {
        "signal_screen_passed": passed,
        "current_model_reconstruction_permitted": passed,
        "candidate_install_permitted": False,
        "may_2026_opened": False,
        "betting_authorized": False,
    }:
        raise ValueError("signal report decision scope invalid")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--validation-out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    protocol_path = args.protocol.resolve()
    report_path = args.report.resolve()
    protocol = load_protocol(protocol_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    validate_report(report, protocol, protocol_path)
    if args.self_test:
        print("[OK] production create-once signal report passes")
        changes = [
            ("protocol hash", lambda p: p.update(protocol_sha256="0" * 64)),
            ("input hash", lambda p: p.update(input_sha256="0" * 64)),
            ("success check", lambda p: p["success_checks"].update(candidate_2025_Brier_lower=False)),
            ("Brier arithmetic", lambda p: p["confirmation"].update(candidate_minus_baseline_brier=1.0)),
            ("betting authorization", lambda p: p["decision"].update(betting_authorized=True)),
        ]
        for label, mutate in changes:
            candidate = copy.deepcopy(report)
            mutate(candidate)
            try:
                validate_report(candidate, protocol, protocol_path)
            except ValueError:
                print(f"[OK] MUTATION {label} fails")
            else:
                raise AssertionError(f"mutation passed: {label}")
        print("6/6")
    validation = {
        "status": "VALID_PASS_QUALIFIES_MODEL_RECONSTRUCTION",
        "report_path": report_path.relative_to(ROOT).as_posix(),
        "report_sha256": sha256_file(report_path),
        "protocol_sha256": sha256_file(protocol_path),
        "confirmation_open_once": True,
        "may_2026_opened": False,
        "betting_authorized": False,
    }
    if args.validation_out:
        output = args.validation_out.resolve()
        tmp = output.with_suffix(output.suffix + ".tmp")
        tmp.write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
        tmp.replace(output)
    print(json.dumps(validation, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
