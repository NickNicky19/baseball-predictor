#!/usr/bin/env python3
"""Validate and mutation-test the certified cumulative-selection rejection."""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_canonical_selection import sha256  # noqa: E402


def valid(record: dict, *, evidence_root: Path) -> bool:
    try:
        if record["status"] != "CUMULATIVE_SELECTION_REJECTION_CERTIFIED":
            return False
        if record["betting_authorized"] or record["production_changed"]:
            return False
        if record["confirmation_2025_opened"] or record["may_2026_opened"]:
            return False
        artifacts = record["artifacts"]
        loaded = {}
        for name, artifact_record in artifacts.items():
            path = evidence_root / artifact_record["path"]
            if not path.exists() or sha256(path) != artifact_record["sha256"]:
                return False
            loaded[name] = path
        report = json.loads(loaded["report"].read_text(encoding="utf-8"))
        certificate = json.loads(loaded["certificate"].read_text(encoding="utf-8"))
        if report["status"] != "CUMULATIVE_SELECTION_REJECTED_NO_CANDIDATE":
            return False
        if certificate["status"] != "CUMULATIVE_SELECTION_REJECTION_CERTIFIED":
            return False
        if report["selection_passed"] or certificate["validation"]["selection_passed"]:
            return False
        if report["model_artifact"] is not None:
            return False
        for label, source in (
            ("strongest_simple", report["simple_baselines"][report["simple_baselines"]["selected"]]["scores"]),
            ("canonical_v1", report["canonical_v1_scores"]),
            ("cumulative_core", report["core_scores"]),
        ):
            for metric, expected in record["scores"][label].items():
                if not math.isclose(float(expected), float(source[metric]), rel_tol=0.0, abs_tol=1e-14):
                    return False
        for metric in ("multiclass_log_loss", "multiclass_brier"):
            item = record["paired_candidate_minus_baseline"]["vs_strongest_simple"][metric]
            report_interval = report["core_intervals_vs_strongest_simple"][metric]
            floor = report["complexity_benefit_floor"][metric]
            if not math.isclose(float(item["point"]), float(report_interval["point"]), rel_tol=0.0, abs_tol=1e-14):
                return False
            if not math.isclose(float(item["upper_95"]), float(report_interval["upper"]), rel_tol=0.0, abs_tol=1e-14):
                return False
            if not math.isclose(float(item["fraction_of_materiality_floor_reached"]), abs(float(item["point"])) / float(floor), rel_tol=0.0, abs_tol=1e-14):
                return False
            if item["passed"]:
                return False
        if not all(item["passed"] for item in record["paired_candidate_minus_baseline"]["vs_canonical_v1"].values()):
            return False
        decision = record["decision"]
        if decision["selection_passed"] or not decision["model_not_published"] or not decision["confirmation_2025_must_remain_unread"]:
            return False
        for attempt in record["invalid_attempts"]:
            invalid = json.loads((ROOT / attempt["record"]).read_text(encoding="utf-8"))
            if attempt["admissible"] or invalid["status"] != "INVALID_INCOMPLETE_NOT_ADJUDICATED":
                return False
            if invalid["partial_output"]["scores_inspected"]:
                return False
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False
    return True


def mutate(record: dict, fn: object) -> dict:
    result = copy.deepcopy(record)
    fn(result)  # type: ignore[operator]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    record = json.loads((ROOT / "reports/shared_pa_cumulative_selection_v3_CERTIFIED_REJECTION.json").read_text(encoding="utf-8"))
    evidence_root = args.evidence_root.resolve()
    checks = [
        ("certified rejection accepted", valid(record, evidence_root=evidence_root)),
        ("status mutation rejected", not valid(mutate(record, lambda r: r.update(status="PASSED")), evidence_root=evidence_root)),
        ("authorization mutation rejected", not valid(mutate(record, lambda r: r.update(betting_authorized=True)), evidence_root=evidence_root)),
        ("production mutation rejected", not valid(mutate(record, lambda r: r.update(production_changed=True)), evidence_root=evidence_root)),
        ("2025 mutation rejected", not valid(mutate(record, lambda r: r.update(confirmation_2025_opened=True)), evidence_root=evidence_root)),
        ("artifact hash mutation rejected", not valid(mutate(record, lambda r: r["artifacts"]["report"].update(sha256="0" * 64)), evidence_root=evidence_root)),
        ("score mutation rejected", not valid(mutate(record, lambda r: r["scores"]["cumulative_core"].update(multiclass_brier=0.0)), evidence_root=evidence_root)),
        ("interval mutation rejected", not valid(mutate(record, lambda r: r["paired_candidate_minus_baseline"]["vs_strongest_simple"]["multiclass_brier"].update(upper_95=-99.0)), evidence_root=evidence_root)),
        ("materiality mutation rejected", not valid(mutate(record, lambda r: r["paired_candidate_minus_baseline"]["vs_strongest_simple"]["multiclass_log_loss"].update(fraction_of_materiality_floor_reached=1.0)), evidence_root=evidence_root)),
        ("simple pass mutation rejected", not valid(mutate(record, lambda r: r["paired_candidate_minus_baseline"]["vs_strongest_simple"]["multiclass_log_loss"].update(passed=True)), evidence_root=evidence_root)),
        ("decision mutation rejected", not valid(mutate(record, lambda r: r["decision"].update(selection_passed=True)), evidence_root=evidence_root)),
        ("invalid artifact admission rejected", not valid(mutate(record, lambda r: r["invalid_attempts"][0].update(admissible=True)), evidence_root=evidence_root)),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"certified cumulative rejection checks failed: {failed}")
    print(f"CUMULATIVE CERTIFIED REJECTION VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
