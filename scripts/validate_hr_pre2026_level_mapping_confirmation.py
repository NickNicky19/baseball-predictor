#!/usr/bin/env python3
"""Independently reproduce the one-time HR level-mapping confirmation report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from adjudicate_hr_pre2026_level_mapping import (  # noqa: E402
    LOCK_SCHEMA,
    REPORT_SCHEMA,
    _allclose_payload,
    _identity_sha,
    _load_role_rows,
    _mapping_inputs,
    _metrics,
    _paired_intervals,
    _predict_parameters,
    _read_json,
    _resolve,
    _signal_models,
    _losses,
    load_contract,
)
from src.evaluation.hr_pre2026_level_mapping import sha256  # noqa: E402


EXPECTED_KEYS = {
    "schema_version", "status", "betting_authorized", "may_2026_opened",
    "confirmation_opened_once", "implementation_contract", "calibration_lock",
    "dates", "rows", "identity_sha256", "metrics", "paired_date_intervals",
    "success_checks", "decision", "scored_rows", "interpretation",
}


def validate_shape(payload: dict[str, Any], dates: list[str]) -> None:
    if set(payload) != EXPECTED_KEYS:
        raise ValueError("confirmation report contains missing or unexpected fields")
    if payload.get("schema_version") != REPORT_SCHEMA:
        raise ValueError("confirmation report schema differs")
    if payload.get("status") not in {"PASS_LEVEL_MAPPING_RESEARCH_ONLY", "REJECTED_LEVEL_MAPPING"}:
        raise ValueError("confirmation report status differs")
    if payload.get("betting_authorized") is not False or payload.get("may_2026_opened") is not False:
        raise ValueError("confirmation report opened May or authorized betting")
    if payload.get("confirmation_opened_once") is not True:
        raise ValueError("confirmation report is not the create-once output")
    if payload.get("dates") != dates:
        raise ValueError("confirmation date universe differs")
    if set(payload.get("metrics") or {}) != {"raw", "control", "candidate"}:
        raise ValueError("confirmation metric arms differ")
    if len(payload.get("scored_rows") or []) != int(payload.get("rows", -1)):
        raise ValueError("confirmation scored-row count differs")


def _expected_checks(metrics: dict[str, Any], intervals: dict[str, Any], rows: int) -> dict[str, bool]:
    return {
        "exact_gradeable_identity_coverage": rows > 0,
        "candidate_Brier_lower_than_raw_and_control": (
            metrics["candidate"]["brier"] < metrics["raw"]["brier"]
            and metrics["candidate"]["brier"] < metrics["control"]["brier"]
        ),
        "candidate_log_loss_lower_than_raw_and_control": (
            metrics["candidate"]["log_loss"] < metrics["raw"]["log_loss"]
            and metrics["candidate"]["log_loss"] < metrics["control"]["log_loss"]
        ),
        "candidate_ROC_AUC_not_lower_than_raw_and_control": (
            metrics["candidate"]["roc_auc"] >= metrics["raw"]["roc_auc"]
            and metrics["candidate"]["roc_auc"] >= metrics["control"]["roc_auc"]
        ),
        "candidate_absolute_mean_probability_bias_not_worse_than_control": (
            abs(metrics["candidate"]["mean_probability_bias"])
            <= abs(metrics["control"]["mean_probability_bias"])
        ),
        "paired_date_Brier_delta_upper_95_below_zero_vs_raw_and_control": (
            intervals["candidate_minus_raw_brier"]["upper_95"] < 0.0
            and intervals["candidate_minus_control_brier"]["upper_95"] < 0.0
        ),
        "paired_date_log_loss_delta_upper_95_below_zero_vs_raw_and_control": (
            intervals["candidate_minus_raw_log_loss"]["upper_95"] < 0.0
            and intervals["candidate_minus_control_log_loss"]["upper_95"] < 0.0
        ),
    }


def validate(contract_path: Path) -> dict[str, Any]:
    contract = load_contract(contract_path, verify_files=True)
    report_path = _resolve(contract["outputs"]["confirmation_report"])
    sidecar = report_path.with_suffix(report_path.suffix + ".sha256")
    if not sidecar.is_file() or sidecar.read_text().strip() != sha256(report_path):
        raise ValueError("confirmation report sidecar differs")
    report = _read_json(report_path)
    dates = list(contract["chronology"]["confirmation_dates_open_once"])
    validate_shape(report, dates)
    lock_path = _resolve(contract["outputs"]["calibration_lock"])
    lock = _read_json(lock_path)
    if lock.get("schema_version") != LOCK_SCHEMA:
        raise ValueError("calibration lock schema differs")
    if report["implementation_contract"]["sha256"] != sha256(contract_path):
        raise ValueError("confirmation contract hash differs")
    if report["calibration_lock"]["sha256"] != sha256(lock_path):
        raise ValueError("confirmation calibration-lock hash differs")

    rows = _load_role_rows(contract, dates)
    baseline, candidate, baseline_features, candidate_features = _signal_models(contract)
    raw, control_x, candidate_x, y = _mapping_inputs(
        rows, baseline, candidate, baseline_features, candidate_features
    )
    control_p = _predict_parameters(control_x, lock["models"]["control"])
    candidate_p = _predict_parameters(candidate_x, lock["models"]["candidate"])
    metrics = {
        "raw": _metrics(y, raw),
        "control": _metrics(y, control_p),
        "candidate": _metrics(y, candidate_p),
    }
    raw_brier, raw_log = _losses(y, raw)
    control_brier, control_log = _losses(y, control_p)
    candidate_brier, candidate_log = _losses(y, candidate_p)
    intervals = _paired_intervals(
        rows.game_date,
        {
            "candidate_minus_raw_brier": (candidate_brier, raw_brier),
            "candidate_minus_control_brier": (candidate_brier, control_brier),
            "candidate_minus_raw_log_loss": (candidate_log, raw_log),
            "candidate_minus_control_log_loss": (candidate_log, control_log),
        },
    )
    checks = _expected_checks(metrics, intervals, len(rows))
    passed = all(checks.values())
    expected_status = "PASS_LEVEL_MAPPING_RESEARCH_ONLY" if passed else "REJECTED_LEVEL_MAPPING"
    if report["status"] != expected_status:
        raise ValueError("confirmation status differs from its success checks")
    for key, value in (
        ("rows", len(rows)),
        ("identity_sha256", _identity_sha(rows)),
        ("metrics", metrics),
        ("paired_date_intervals", intervals),
        ("success_checks", checks),
    ):
        if not _allclose_payload(report.get(key), value):
            raise ValueError(f"confirmation report does not reproduce: {key}")
    expected_rows = []
    for index, identity in enumerate(rows[["game_date", "mlb_game_pk", "player_id"]].itertuples(index=False)):
        expected_rows.append(
            {
                "game_date": str(identity.game_date),
                "mlb_game_pk": int(identity.mlb_game_pk),
                "player_id": int(identity.player_id),
                "actual_hr": int(rows.actual_value.iloc[index]),
                "raw_probability": float(raw[index]),
                "control_probability": float(control_p[index]),
                "candidate_probability": float(candidate_p[index]),
                "signal_increment": float(candidate_x[index, 1]),
            }
        )
    if not _allclose_payload(report["scored_rows"], expected_rows):
        raise ValueError("confirmation scored rows do not reproduce")
    decision = report.get("decision") or {}
    if (
        decision.get("level_mapping_supported") is not passed
        or decision.get("2026_open_period_experiment_permitted") is not passed
        or decision.get("model_install_permitted") is not False
        or decision.get("betting_authorized") is not False
        or decision.get("may_2026_opened") is not False
    ):
        raise ValueError("confirmation decision differs from the locked result")
    return {
        "schema_version": "hr-pre2026-level-mapping-confirmation-validation-v1",
        "status": "VALID_" + expected_status,
        "betting_authorized": False,
        "may_2026_opened": False,
        "dates": dates,
        "rows": len(rows),
        "success_checks": checks,
        "confirmation_report": {"path": str(report_path), "sha256": sha256(report_path)},
        "implementation_contract": {"path": str(contract_path), "sha256": sha256(contract_path)},
        "validator": {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__))},
        "verdict": "REJECT_AND_PRESERVE" if not passed else "PASS_RESEARCH_ONLY_OPEN_PERIOD_EXPERIMENT_ONLY",
    }


def self_test() -> int:
    dates = [f"2025-07-{day:02d}" for day in range(1, 13)]
    base = {
        "schema_version": REPORT_SCHEMA,
        "status": "REJECTED_LEVEL_MAPPING",
        "betting_authorized": False,
        "may_2026_opened": False,
        "confirmation_opened_once": True,
        "implementation_contract": {}, "calibration_lock": {}, "dates": dates,
        "rows": 1, "identity_sha256": "x",
        "metrics": {"raw": {}, "control": {}, "candidate": {}},
        "paired_date_intervals": {}, "success_checks": {}, "decision": {},
        "scored_rows": [{}], "interpretation": "research only",
    }
    validate_shape(base, dates)
    print("[OK] valid confirmation report shape passes")
    mutations = []
    for field in ("betting_authorized", "may_2026_opened"):
        bad = dict(base); bad[field] = True; mutations.append((bad, field))
    bad = dict(base); bad["confirmation_opened_once"] = False; mutations.append((bad, "not create-once"))
    bad = dict(base); bad["dates"] = dates[:-1]; mutations.append((bad, "date loss"))
    bad = dict(base); bad["metrics"] = {"raw": {}, "candidate": {}}; mutations.append((bad, "control loss"))
    bad = dict(base); bad["scored_rows"] = []; mutations.append((bad, "row loss"))
    for payload, label in mutations:
        try:
            validate_shape(payload, dates)
        except ValueError:
            print(f"[OK] MUTATION {label} fails")
        else:
            raise AssertionError(f"mutation unexpectedly passed: {label}")
    print("7/7")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--contract")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.contract or not args.report:
        parser.error("production validation requires --contract and --report")
    output = _resolve(args.report)
    if output.exists():
        raise FileExistsError(f"confirmation validation already exists: {output}")
    payload = validate(_resolve(args.contract))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    print("HR LEVEL-MAPPING CONFIRMATION VALID")
    print(f"  status: {payload['status']}")
    print(f"  rows: {payload['rows']}")
    print(f"  failed checks: {[name for name, value in payload['success_checks'].items() if not value]}")
    print("  betting authorized: NO; May 2026 opened: NO")
    print(f"  report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
