#!/usr/bin/env python3
"""Independently validate the open-period HR rolling-EB simplification gate."""
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

from scripts.adjudicate_hr_batted_ball_candidate import apply_official_grading  # noqa: E402
from src.evaluation.hr_eb_simplification import (  # noqa: E402
    auc, build_candidate_probabilities, decide, load_protocol,
)
from src.evaluation.hr_over_evaluation import (  # noqa: E402
    MODEL_KEY, arm_rows, build_pairs, date_block_intervals, point_metrics,
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


def read_input(evidence_root: Path, protocol: dict[str, Any], name: str) -> pd.DataFrame:
    return pd.read_csv(evidence_root / protocol["inputs"][name]["path"])


def recompute(
    evidence_root: Path,
    protocol: dict[str, Any],
    candidate_path: Path,
) -> dict[str, Any]:
    reused = json.loads((evidence_root / protocol["inputs"]["reused_inference_protocol"]["path"]).read_text(encoding="utf-8"))
    diagnostic_dates = list(reused["chronology"]["diagnostic_dates"])
    confirmation_dates = list(reused["chronology"]["confirmation_dates"])
    frozen = read_input(evidence_root, protocol, "frozen_probabilities")
    benchmark = read_input(evidence_root, protocol, "benchmark_predictions")
    settlement = read_input(evidence_root, protocol, "official_settlement_bridge")
    expected_candidate, candidate_funnel = build_candidate_probabilities(frozen, benchmark, settlement)
    candidate = pd.read_csv(candidate_path)
    try:
        pd.testing.assert_frame_equal(
            candidate.reset_index(drop=True), expected_candidate.reset_index(drop=True),
            check_dtype=False, rtol=1e-12, atol=1e-14,
        )
    except AssertionError as exc:
        raise ValueError("published HR EB candidate differs from independently rebuilt candidate") from exc
    market = read_input(evidence_root, protocol, "market_source")
    official = read_input(evidence_root, protocol, "official_outcomes")
    pairs, market_funnel = build_pairs(
        market, frozen, candidate, official, diagnostic_dates, confirmation_dates,
    )
    graded, void = apply_official_grading(pairs, settlement, 8854, 8827, 27)
    draws = int(protocol["inference"]["bootstrap_draws"])
    seed = int(protocol["inference"]["bootstrap_seed"])
    roles: dict[str, Any] = {}
    for role, dates in (("diagnostic", diagnostic_dates), ("confirmation", confirmation_dates)):
        subset = graded[graded["official_game_date"].isin(dates)].sort_values(MODEL_KEY).reset_index(drop=True)
        frozen_rows = arm_rows(subset, "frozen")
        candidate_rows = arm_rows(subset, "candidate")
        frozen_metrics = point_metrics(frozen_rows)
        candidate_metrics = point_metrics(candidate_rows)
        frozen_metrics["auc"] = auc(subset, "frozen")
        candidate_metrics["auc"] = auc(subset, "candidate")
        roles[role] = {
            "frozen": frozen_metrics,
            "candidate": candidate_metrics,
            "changed_probabilities": int((~np.isclose(subset["frozen_p_over"], subset["candidate_p_over"])).sum()),
            "date_block_intervals": date_block_intervals(frozen_rows, candidate_rows, dates, draws=draws, seed=seed),
        }
    exact_coverage = (
        market_funnel["strict_market_rows"] == 9356
        and market_funnel["model_available_rows"] == 8854
        and market_funnel["model_unavailable_rows"] == 502
        and market_funnel["officially_scored_rows"] == 8854
        and market_funnel["diagnostic_rows"] == 6657
        and market_funnel["confirmation_rows"] == 2197
        and len(graded) == 8827 and len(void) == 27
    )
    all_draws_valid = all(role["date_block_intervals"]["valid_draws"] == draws for role in roles.values())
    decision = decide(roles, exact_gradeable_coverage=exact_coverage, all_draws_valid=all_draws_valid)
    return {
        "candidate": candidate,
        "candidate_funnel": candidate_funnel,
        "market_funnel": market_funnel,
        "graded": graded,
        "void": void,
        "roles": roles,
        "decision": decision,
    }


def expected_status(decision: dict[str, Any]) -> tuple[str, str]:
    if decision["probability_confirmation_ready"] and decision["historical_economic_ready"]:
        return (
            "PROBABILITY_AND_HISTORICAL_ECONOMIC_RESEARCH_READY_FOR_UNTOUCHED_CONFIRMATION",
            "Freeze the HR probability candidate and predeclare untouched confirmation; do not install production or authorize betting.",
        )
    if decision["probability_confirmation_ready"]:
        return (
            "PROBABILITY_READY_HISTORICAL_ECONOMICS_BLOCKED",
            "Proceed only to untouched probability confirmation; retain the economic block and do not install production.",
        )
    return (
        "HR_EB_SIMPLIFICATION_REJECTED_ON_MARKET_GRADEABLE_PROBABILITY_GATE",
        "Reject the HR simplification candidate and preserve production.",
    )


def validate_report_claims(
    report: dict[str, Any],
    protocol: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    status, next_action = expected_status(expected["decision"])
    fixed = {
        "schema_version": "hr-eb-simplification-open-gate-report-v1",
        "status": status,
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "confirmation_2025_opened": False,
        "historical_executability_verified": False,
        "probability_label": "RAW_ONE_SIDED_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN_AND_IS_NOT_VERIFIED_EXECUTABLE",
        "candidate": protocol["candidate"],
        "candidate_funnel": expected["candidate_funnel"],
        "market_funnel": expected["market_funnel"],
        "official_gradeable_rows": len(expected["graded"]),
        "void_or_unresolved_rows": len(expected["void"]),
        "roles": expected["roles"],
        "decision": expected["decision"],
        "next_action": next_action,
        "protected_invariants": protocol["protected_invariants"],
    }
    for key, value in fixed.items():
        assert_close(report.get(key), value, f"report.{key}")


def validate_runtime(
    report: dict[str, Any],
    protocol_path: Path,
    report_path: Path,
    candidate_path: Path,
    evidence_root: Path,
) -> None:
    artifacts = report["artifacts"]
    if artifacts["protocol"]["sha256"] != sha256(protocol_path):
        raise ValueError("HR EB report protocol hash changed")
    for name, path in (
        ("candidate_probabilities", candidate_path),
        ("scored_gradeable_rows", report_path.parent / "scored_gradeable_rows.csv"),
        ("void_or_unresolved_rows", report_path.parent / "void_or_unresolved_rows.csv"),
    ):
        if artifacts[name]["sha256"] != sha256(path):
            raise ValueError(f"HR EB report artifact hash changed: {name}")
        if Path(artifacts[name]["path"]) != path.relative_to(evidence_root):
            raise ValueError(f"HR EB report artifact path changed: {name}")
    commit = report["runtime"]["source_commit"]
    subprocess.check_call(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=ROOT)
    for name in ("runner", "module"):
        record = report["runtime"][name]
        content = subprocess.check_output(["git", "show", f"{commit}:{record['path']}"], cwd=ROOT)
        if hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise ValueError(f"HR EB {name} hash does not match source commit")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    protocol_path = args.protocol.resolve()
    report_path = args.report.resolve()
    candidate_path = args.candidate.resolve()
    protocol = load_protocol(protocol_path, evidence_root=evidence_root)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected = recompute(evidence_root, protocol, candidate_path)
    validate_report_claims(report, protocol, expected)
    validate_runtime(report, protocol_path, report_path, candidate_path, evidence_root)
    certificate = {
        "schema_version": "hr-eb-simplification-open-gate-certificate-v1",
        "status": "HR_EB_SIMPLIFICATION_OPEN_GATE_CERTIFIED",
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "confirmation_2025_opened": False,
        "probability_confirmation_ready": expected["decision"]["probability_confirmation_ready"],
        "historical_economic_ready": expected["decision"]["historical_economic_ready"],
        "artifacts": {
            "protocol": {"path": protocol_path.relative_to(ROOT).as_posix(), "sha256": sha256(protocol_path)},
            "report": {"path": report_path.relative_to(evidence_root).as_posix(), "sha256": sha256(report_path)},
            "candidate": {"path": candidate_path.relative_to(evidence_root).as_posix(), "sha256": sha256(candidate_path)},
        },
        "validator": {
            "path": "scripts/validate_hr_eb_simplification_candidate.py",
            "sha256": sha256(Path(__file__)),
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        },
        "protected_invariants": {
            "candidate_rebuilt_independently": True,
            "official_gradeable_universe_rebuilt": True,
            "metrics_and_bootstraps_recomputed": True,
            "historical_executability_unverified": True,
            "production_unchanged": True,
            "May_2026_remains_sealed": True,
            "2025_confirmation_remains_unread": True,
            "no_betting_authorization": True,
        },
    }
    atomic_json(args.out.resolve(), certificate)
    print("HR_EB_SIMPLIFICATION_OPEN_GATE_CERTIFIED")
    print(f"probability_confirmation_ready={certificate['probability_confirmation_ready']}")
    print(f"historical_economic_ready={certificate['historical_economic_ready']}")
    print(f"certificate_sha256={sha256(args.out.resolve())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
