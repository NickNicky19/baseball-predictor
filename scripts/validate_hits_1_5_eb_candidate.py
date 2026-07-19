#!/usr/bin/env python3
"""Independently rebuild and certify the Hits 1.5 rolling-EB open gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.adjudicate_hits_1_5_eb_candidate import expected_status  # noqa: E402
from src.evaluation.hits_1_5_eb_adjudication import (  # noqa: E402
    MODEL_KEY, arm_rows, build_scored, date_block_intervals, decide,
    load_protocol, point_metrics, prepare_market,
)
from src.evaluation.multi_market_foundation import sha256  # noqa: E402


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        handle.write((json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def assert_close(actual: Any, expected: Any, path: str = "root") -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            raise ValueError(f"mapping keys changed at {path}")
        for key in expected:
            assert_close(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError(f"list changed at {path}")
        for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
            assert_close(left, right, f"{path}[{index}]")
    elif isinstance(expected, float):
        if not isinstance(actual, (int, float)) or not np.isclose(float(actual), expected, rtol=1e-11, atol=1e-13):
            raise ValueError(f"numeric value changed at {path}")
    elif actual != expected:
        raise ValueError(f"value changed at {path}")


def read_input(root: Path, protocol: dict[str, Any], name: str) -> pd.DataFrame:
    return pd.read_csv(root / protocol["inputs"][name]["path"])


def recompute(evidence_root: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    market, excluded, market_funnel = prepare_market(
        read_input(evidence_root, protocol, "diagnostic_market"),
        read_input(evidence_root, protocol, "confirmation_market"), protocol,
    )
    scored, unavailable, coverage_funnel = build_scored(
        market, read_input(evidence_root, protocol, "benchmark_predictions"),
        read_input(evidence_root, protocol, "official_outcomes"), protocol,
    )
    draws = int(protocol["inference"]["bootstrap_draws"])
    seed = int(protocol["inference"]["bootstrap_seed"])
    roles: dict[str, Any] = {}
    for role in ("diagnostic", "confirmation"):
        subset = scored.loc[scored["chronology_role"].eq(role)].sort_values(MODEL_KEY).reset_index(drop=True)
        arms = {name: arm_rows(subset, name) for name in ("candidate", "production", "league")}
        roles[role] = {
            **{name: point_metrics(frame) for name, frame in arms.items()},
            "changed_candidate_vs_production": int((~np.isclose(subset["candidate_p_over"], subset["production_p_over"])).sum()),
            "date_block_intervals": date_block_intervals(
                arms, protocol["chronology"][f"{role}_dates"], draws=draws, seed=seed,
            ),
        }
    exact_coverage = (
        market_funnel == {
            "diagnostic_raw_rows": 898, "confirmation_raw_rows": 495,
            "conflicting_duplicate_keys": 1, "conflicting_duplicate_rows": 2,
            "strict_market_rows": 1391, "entry_two_sided_rows": 1391,
            "close_two_sided_rows": 1391,
        }
        and coverage_funnel == {
            "strict_market_rows": 1391, "model_available_rows": 1383,
            "model_unavailable_rows": 8, "diagnostic_scored_rows": 896,
            "confirmation_scored_rows": 487, "official_positive_pa_rows": 1383,
        }
    )
    all_draws_valid = all(role["date_block_intervals"]["valid_draws"] == draws for role in roles.values())
    return {
        "market_funnel": market_funnel, "coverage_funnel": coverage_funnel,
        "scored": scored, "unavailable": unavailable, "excluded": excluded,
        "roles": roles,
        "decision": decide(roles, exact_coverage=exact_coverage, all_draws_valid=all_draws_valid),
    }


def validate_report(report: dict[str, Any], protocol: dict[str, Any], expected: dict[str, Any]) -> None:
    status, next_action = expected_status(expected["decision"])
    fixed = {
        "schema_version": "hits-1-5-eb-open-gate-report-v1", "status": status,
        "betting_authorized": False, "production_unchanged": True,
        "may_2026_opened": False, "historical_executability_verified": False,
        "candidate": protocol["candidate"], "market_funnel": expected["market_funnel"],
        "coverage_funnel": expected["coverage_funnel"], "roles": expected["roles"],
        "decision": expected["decision"], "next_action": next_action,
        "protected_invariants": protocol["protected_invariants"],
    }
    for key, value in fixed.items():
        assert_close(report.get(key), value, f"report.{key}")


def validate_runtime(report: dict[str, Any], protocol_path: Path, report_path: Path, evidence_root: Path) -> None:
    if report["artifacts"]["protocol"]["sha256"] != sha256(protocol_path):
        raise ValueError("Hits 1.5 report protocol hash changed")
    for name, filename in (
        ("scored_rows", "scored_rows.csv"),
        ("model_unavailable_rows", "model_unavailable_rows.csv"),
        ("excluded_conflicting_duplicate_rows", "excluded_conflicting_duplicate_rows.csv"),
    ):
        path = report_path.parent / filename
        record = report["artifacts"][name]
        if record["sha256"] != sha256(path) or Path(record["path"]) != path.relative_to(evidence_root):
            raise ValueError(f"Hits 1.5 artifact changed: {name}")
    commit = report["runtime"]["source_commit"]
    subprocess.check_call(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=ROOT)
    for name in ("runner", "module"):
        record = report["runtime"][name]
        content = subprocess.check_output(["git", "show", f"{commit}:{record['path']}"], cwd=ROOT)
        if hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise ValueError(f"Hits 1.5 {name} hash does not match source commit")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    protocol_path = args.protocol.resolve()
    report_path = args.report.resolve()
    protocol = load_protocol(protocol_path, evidence_root=evidence_root)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected = recompute(evidence_root, protocol)
    validate_report(report, protocol, expected)
    for expected_frame, filename, label in (
        (expected["scored"], "scored_rows.csv", "scored"),
        (expected["unavailable"], "model_unavailable_rows.csv", "unavailable"),
        (expected["excluded"], "excluded_conflicting_duplicate_rows.csv", "excluded"),
    ):
        try:
            pd.testing.assert_frame_equal(
                pd.read_csv(report_path.parent / filename).reset_index(drop=True),
                expected_frame.reset_index(drop=True), check_dtype=False, rtol=1e-12, atol=1e-14,
            )
        except AssertionError as exc:
            raise ValueError(f"published Hits 1.5 {label} rows changed") from exc
    validate_runtime(report, protocol_path, report_path, evidence_root)
    certificate = {
        "schema_version": "hits-1-5-eb-open-gate-certificate-v1",
        "status": "HITS_1_5_EB_OPEN_GATE_CERTIFIED",
        "betting_authorized": False, "production_unchanged": True, "may_2026_opened": False,
        "probability_ready": expected["decision"]["probability_ready"],
        "historical_economic_ready": expected["decision"]["historical_economic_ready"],
        "artifacts": {
            "protocol": {"path": protocol_path.relative_to(ROOT).as_posix(), "sha256": sha256(protocol_path)},
            "report": {"path": report_path.relative_to(evidence_root).as_posix(), "sha256": sha256(report_path)},
        },
        "validator": {
            "path": "scripts/validate_hits_1_5_eb_candidate.py", "sha256": sha256(Path(__file__)),
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        },
        "protected_invariants": {
            "market_and_truth_rebuilt_independently": True,
            "duplicate_and_unavailable_rows_rebuilt": True,
            "metrics_and_bootstraps_recomputed": True,
            "historical_executability_unverified": True,
            "production_unchanged": True, "May_2026_remains_sealed": True,
            "no_betting_authorization": True,
        },
    }
    atomic_json(args.out.resolve(), certificate)
    print("HITS_1_5_EB_OPEN_GATE_CERTIFIED")
    print(f"probability_ready={certificate['probability_ready']}")
    print(f"historical_economic_ready={certificate['historical_economic_ready']}")
    print(f"certificate_sha256={sha256(args.out.resolve())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
