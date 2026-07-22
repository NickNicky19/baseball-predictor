#!/usr/bin/env python3
"""Independently reproduce the HR level-mapping calibration lock."""
from __future__ import annotations

import argparse
import hashlib
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
    _allclose_payload,
    _identity_sha,
    _load_role_rows,
    _mapping_inputs,
    _mapping_parameters,
    _mapping_pipeline,
    _resolve,
    _signal_models,
    load_contract,
    select_regularization_lodo,
)
from src.evaluation.hr_pre2026_level_mapping import sha256  # noqa: E402


EXPECTED_KEYS = {
    "schema_version", "status", "betting_authorized", "may_2026_opened",
    "confirmation_opened", "implementation_contract", "evaluator", "dates",
    "rows", "hr_occurrences", "identity_sha256", "raw_probability_sha256",
    "selected_regularization", "cross_validation", "models",
    "signal_model_report_sha256", "input_hashes", "interpretation",
}


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("calibration lock is not a JSON object")
    return value


def validate_shape(payload: dict[str, Any], expected_dates: list[str]) -> None:
    if set(payload) != EXPECTED_KEYS:
        raise ValueError("calibration lock contains missing or unexpected fields")
    if payload.get("schema_version") != LOCK_SCHEMA:
        raise ValueError("calibration lock schema differs")
    if payload.get("status") != "CALIBRATION_LOCKED_CONFIRMATION_NOT_OPENED":
        raise ValueError("calibration lock status differs")
    if payload.get("betting_authorized") is not False or payload.get("may_2026_opened") is not False:
        raise ValueError("calibration lock opened May or authorized betting")
    if payload.get("confirmation_opened") is not False:
        raise ValueError("calibration lock already opened confirmation")
    if payload.get("dates") != expected_dates:
        raise ValueError("calibration lock date universe differs")
    if any(date.startswith("2026-05") for date in expected_dates):
        raise ValueError("May 2026 is forbidden")
    if set(payload.get("models") or {}) != {"control", "candidate"}:
        raise ValueError("calibration mapping arms differ")
    if set(payload.get("selected_regularization") or {}) != {"control_C", "candidate_C"}:
        raise ValueError("calibration selected regularization differs")


def validate(contract_path: Path, report_path: Path) -> dict[str, Any]:
    contract = load_contract(contract_path, verify_files=True)
    lock_path = _resolve(contract["outputs"]["calibration_lock"])
    confirmation_path = _resolve(contract["outputs"]["confirmation_report"])
    if confirmation_path.exists():
        raise ValueError("confirmation already exists; calibration-only validation is too late")
    sidecar = lock_path.with_suffix(lock_path.suffix + ".sha256")
    if not sidecar.is_file() or sidecar.read_text().strip() != sha256(lock_path):
        raise ValueError("calibration lock sidecar differs")
    lock = _read(lock_path)
    dates = list(contract["chronology"]["calibration_dates"])
    validate_shape(lock, dates)
    if lock["implementation_contract"]["sha256"] != sha256(contract_path):
        raise ValueError("calibration lock contract hash differs")
    evaluator = _resolve(contract["inputs"]["evaluator"]["path"])
    if lock["evaluator"]["sha256"] != sha256(evaluator):
        raise ValueError("calibration lock evaluator hash differs")
    if lock["input_hashes"] != {
        name: record["sha256"] for name, record in contract["inputs"].items()
    }:
        raise ValueError("calibration lock input hashes differ")

    rows = _load_role_rows(contract, dates)
    baseline, candidate, baseline_features, candidate_features = _signal_models(contract)
    raw, control_x, candidate_x, y = _mapping_inputs(
        rows, baseline, candidate, baseline_features, candidate_features
    )
    control_c, control_cv = select_regularization_lodo(control_x, y, rows.game_date)
    candidate_c, candidate_cv = select_regularization_lodo(candidate_x, y, rows.game_date)
    control = _mapping_pipeline(control_c).fit(control_x, y)
    challenger = _mapping_pipeline(candidate_c).fit(candidate_x, y)
    expected = {
        "rows": int(len(rows)),
        "hr_occurrences": int(y.sum()),
        "identity_sha256": _identity_sha(rows),
        "raw_probability_sha256": hashlib.sha256(np.asarray(raw, dtype="<f8").tobytes()).hexdigest(),
        "selected_regularization": {"control_C": control_c, "candidate_C": candidate_c},
        "cross_validation": {"control": control_cv, "candidate": candidate_cv},
        "models": {
            "control": _mapping_parameters(control),
            "candidate": _mapping_parameters(challenger),
        },
    }
    for key, value in expected.items():
        if not _allclose_payload(lock.get(key), value):
            raise ValueError(f"calibration lock does not reproduce: {key}")
    return {
        "schema_version": "hr-pre2026-level-mapping-calibration-validation-v1",
        "status": "VALID_CALIBRATION_LOCK_CONFIRMATION_NOT_OPENED",
        "betting_authorized": False,
        "may_2026_opened": False,
        "confirmation_opened": False,
        "dates": dates,
        "rows": len(rows),
        "hr_occurrences": int(y.sum()),
        "selected_regularization": expected["selected_regularization"],
        "implementation_contract": {"path": str(contract_path), "sha256": sha256(contract_path)},
        "calibration_lock": {"path": str(lock_path), "sha256": sha256(lock_path)},
        "validator": {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__))},
        "verdict": "PASS_REPRODUCED_CALIBRATION_LOCK_CONFIRMATION_MAY_OPEN_ONCE",
    }


def self_test() -> int:
    dates = [f"2025-01-{day:02d}" for day in range(1, 13)]
    base = {
        "schema_version": LOCK_SCHEMA,
        "status": "CALIBRATION_LOCKED_CONFIRMATION_NOT_OPENED",
        "betting_authorized": False,
        "may_2026_opened": False,
        "confirmation_opened": False,
        "implementation_contract": {}, "evaluator": {}, "dates": dates,
        "rows": 1, "hr_occurrences": 1, "identity_sha256": "x",
        "raw_probability_sha256": "y", "selected_regularization": {"control_C": 1, "candidate_C": 1},
        "cross_validation": {}, "models": {"control": {}, "candidate": {}},
        "signal_model_report_sha256": "z", "input_hashes": {}, "interpretation": "locked",
    }
    validate_shape(base, dates)
    print("[OK] valid calibration lock shape passes")
    mutations = []
    for field in ("betting_authorized", "may_2026_opened", "confirmation_opened"):
        bad = dict(base); bad[field] = True; mutations.append((bad, field))
    bad = dict(base); bad["dates"] = dates[:-1]; mutations.append((bad, "date loss"))
    bad = dict(base); bad["metrics"] = {}; mutations.append((bad, "premature metrics"))
    bad = dict(base); bad["models"] = {"control": {}}; mutations.append((bad, "arm loss"))
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
    report_path = _resolve(args.report)
    if report_path.exists():
        raise FileExistsError(f"calibration validation report already exists: {report_path}")
    payload = validate(_resolve(args.contract), report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    print("HR LEVEL-MAPPING CALIBRATION LOCK VALID")
    print(f"  dates/rows: {len(payload['dates'])} / {payload['rows']}")
    print(f"  selected C: {payload['selected_regularization']}")
    print("  confirmation opened: NO; betting authorized: NO")
    print(f"  report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
