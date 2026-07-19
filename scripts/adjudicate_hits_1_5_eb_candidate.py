#!/usr/bin/env python3
"""Run the locked open-period Hits 1.5 rolling-EB adjudication."""
from __future__ import annotations

import argparse
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

from src.evaluation.hits_1_5_eb_adjudication import (  # noqa: E402
    MODEL_KEY, arm_rows, build_scored, date_block_intervals, decide,
    load_protocol, point_metrics, prepare_market,
)
from src.evaluation.multi_market_foundation import sha256  # noqa: E402


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        frame.to_csv(handle, index=False, lineterminator="\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def atomic_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        handle.write((json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def read_input(root: Path, protocol: dict[str, Any], name: str) -> pd.DataFrame:
    return pd.read_csv(root / protocol["inputs"][name]["path"])


def expected_status(decision: dict[str, Any]) -> tuple[str, str]:
    if decision["probability_ready"] and decision["historical_economic_ready"]:
        return (
            "HITS_1_5_PROBABILITY_AND_HISTORICAL_ECONOMIC_READY_FOR_MAY_PROTOCOL",
            "Freeze the Hits 1.5 candidate and predeclare the one-time May holdout protocol; do not open May, install production, or authorize betting yet.",
        )
    if decision["probability_ready"]:
        return (
            "HITS_1_5_PROBABILITY_READY_HISTORICAL_ECONOMICS_BLOCKED",
            "Retain the candidate only as a probability research component; do not open May or install production while historical economics remain blocked.",
        )
    return (
        "HITS_1_5_EB_REJECTED_ON_OPEN_GATE",
        "Reject the Hits 1.5 simplification candidate and preserve immutable production.",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    out_dir = args.out_dir.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("Hits 1.5 output directory is not fresh")
    protocol_path = args.protocol.resolve()
    protocol = load_protocol(protocol_path, evidence_root=evidence_root)
    market, excluded, market_funnel = prepare_market(
        read_input(evidence_root, protocol, "diagnostic_market"),
        read_input(evidence_root, protocol, "confirmation_market"),
        protocol,
    )
    scored, unavailable, coverage_funnel = build_scored(
        market,
        read_input(evidence_root, protocol, "benchmark_predictions"),
        read_input(evidence_root, protocol, "official_outcomes"),
        protocol,
    )
    draws = int(protocol["inference"]["bootstrap_draws"])
    seed = int(protocol["inference"]["bootstrap_seed"])
    roles: dict[str, Any] = {}
    for role in ("diagnostic", "confirmation"):
        subset = scored.loc[scored["chronology_role"].eq(role)].sort_values(MODEL_KEY).reset_index(drop=True)
        arm_frames = {name: arm_rows(subset, name) for name in ("candidate", "production", "league")}
        roles[role] = {
            **{name: point_metrics(frame) for name, frame in arm_frames.items()},
            "changed_candidate_vs_production": int((~np.isclose(subset["candidate_p_over"], subset["production_p_over"])).sum()),
            "date_block_intervals": date_block_intervals(
                arm_frames, protocol["chronology"][f"{role}_dates"], draws=draws, seed=seed,
            ),
        }
    expected_market = {
        "diagnostic_raw_rows": 898, "confirmation_raw_rows": 495,
        "conflicting_duplicate_keys": 1, "conflicting_duplicate_rows": 2,
        "strict_market_rows": 1391, "entry_two_sided_rows": 1391,
        "close_two_sided_rows": 1391,
    }
    expected_coverage = {
        "strict_market_rows": 1391, "model_available_rows": 1383,
        "model_unavailable_rows": 8, "diagnostic_scored_rows": 896,
        "confirmation_scored_rows": 487, "official_positive_pa_rows": 1383,
    }
    exact_coverage = market_funnel == expected_market and coverage_funnel == expected_coverage
    all_draws_valid = all(role["date_block_intervals"]["valid_draws"] == draws for role in roles.values())
    decision = decide(roles, exact_coverage=exact_coverage, all_draws_valid=all_draws_valid)
    status, next_action = expected_status(decision)

    scored_path = out_dir / "scored_rows.csv"
    unavailable_path = out_dir / "model_unavailable_rows.csv"
    excluded_path = out_dir / "excluded_conflicting_duplicate_rows.csv"
    atomic_csv(scored, scored_path)
    atomic_csv(unavailable, unavailable_path)
    atomic_csv(excluded, excluded_path)
    report = {
        "schema_version": "hits-1-5-eb-open-gate-report-v1",
        "status": status,
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "historical_executability_verified": False,
        "candidate": protocol["candidate"],
        "market_funnel": market_funnel,
        "coverage_funnel": coverage_funnel,
        "roles": roles,
        "decision": decision,
        "next_action": next_action,
        "artifacts": {
            "protocol": {"path": protocol_path.relative_to(ROOT).as_posix(), "sha256": sha256(protocol_path)},
            "scored_rows": {"path": scored_path.relative_to(evidence_root).as_posix(), "sha256": sha256(scored_path), "rows": len(scored)},
            "model_unavailable_rows": {"path": unavailable_path.relative_to(evidence_root).as_posix(), "sha256": sha256(unavailable_path), "rows": len(unavailable)},
            "excluded_conflicting_duplicate_rows": {"path": excluded_path.relative_to(evidence_root).as_posix(), "sha256": sha256(excluded_path), "rows": len(excluded)},
        },
        "runtime": {
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "runner": {"path": "scripts/adjudicate_hits_1_5_eb_candidate.py", "sha256": sha256(Path(__file__))},
            "module": {"path": "src/evaluation/hits_1_5_eb_adjudication.py", "sha256": sha256(ROOT / "src/evaluation/hits_1_5_eb_adjudication.py")},
        },
        "protected_invariants": protocol["protected_invariants"],
    }
    report_path = out_dir / "report.json"
    atomic_json(report, report_path)
    print(status)
    print(f"probability_ready={decision['probability_ready']}")
    print(f"historical_economic_ready={decision['historical_economic_ready']}")
    print(f"report_sha256={sha256(report_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
