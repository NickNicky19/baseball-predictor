#!/usr/bin/env python3
"""Validate and mutation-test the certified rejection diagnostic record."""
from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_rejection_diagnostic import sha256  # noqa: E402


def valid(record: dict, *, evidence_root: Path) -> bool:
    try:
        if record["status"] != "CERTIFIED_REJECTION_DIAGNOSIS_REPRODUCED":
            return False
        if record["betting_authorized"] or record["production_changed"]:
            return False
        if record["confirmation_2025_opened"] or record["may_2026_opened"]:
            return False
        loaded: dict[str, Path] = {}
        for name, item in record["artifacts"].items():
            root = ROOT if item["root"] == "code" else evidence_root
            path = root / item["path"]
            if not path.is_file() or sha256(path) != item["sha256"]:
                return False
            loaded[name] = path
        report = json.loads(loaded["report"].read_text(encoding="utf-8"))
        certificate = json.loads(loaded["certificate"].read_text(encoding="utf-8"))
        if report["status"] != "CERTIFIED_REJECTION_DIAGNOSIS_COMPLETE":
            return False
        if certificate["status"] != record["status"] or certificate["report"]["sha256"] != record["artifacts"]["report"]["sha256"]:
            return False
        if report["source_commit"] != record["source_commit"] or certificate["source_commit"] != record["source_commit"]:
            return False
        analysis = report["analysis"]
        if record["population"] != analysis["population"] or record["diagnostic_ranking"] != analysis["diagnostic_ranking"]:
            return False
        for outcome in ("bip_out", "single"):
            source = analysis["class_contributions"][outcome]
            finding = record["measured_findings"][outcome]
            if not math.isclose(finding["actual_rate"], source["actual_rate"], rel_tol=0.0, abs_tol=1e-14):
                return False
            if not math.isclose(finding["mean_share_of_positive_improvement"], source["mean_share_of_positive_improvement"], rel_tol=0.0, abs_tol=1e-14):
                return False
            for comparator in ("strongest_simple", "canonical_v1"):
                interval = source["candidate_comparisons"][f"vs_{comparator}"]
                compact = finding[f"vs_{comparator}"]
                for metric, prefix in (("multiclass_log_loss", "log_loss"), ("multiclass_brier", "brier")):
                    if not math.isclose(compact[f"{prefix}_point"], interval[metric]["point"], rel_tol=0.0, abs_tol=1e-14):
                        return False
                    if not math.isclose(compact[f"{prefix}_upper_95"], interval[metric]["upper"], rel_tol=0.0, abs_tol=1e-14):
                        return False
        strikeout = analysis["class_contributions"]["strikeout"]["candidate_comparisons"]
        offset = record["measured_findings"]["largest_offsetting_log_loss_regression"]
        if offset["pa_outcome"] != "strikeout":
            return False
        for comparator in ("strongest_simple", "canonical_v1"):
            interval = strikeout[f"vs_{comparator}"]["multiclass_log_loss"]
            if not math.isclose(offset[f"vs_{comparator}_point"], interval["point"], rel_tol=0.0, abs_tol=1e-14):
                return False
            if not math.isclose(offset[f"vs_{comparator}_upper_95"], interval["upper"], rel_tol=0.0, abs_tol=1e-14):
                return False
        if analysis["diagnostic_ranking"]["eligible_derived_markets"]:
            return False
        decision = record["decision"]
        if not all((decision["cumulative_candidate_remains_rejected"], decision["confirmation_2025_must_remain_unread"], decision["next_experiment_must_not_reuse_flat_multiclass_cumulative_design"])):
            return False
        if not all(record["protected_invariants"].values()):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def mutate(record: dict, fn: object) -> dict:
    changed = copy.deepcopy(record)
    fn(changed)  # type: ignore[operator]
    return changed


def main() -> int:
    evidence_root = Path(r"C:\Projects\baseball_predictor")
    record = json.loads((ROOT / "reports/shared_pa_cumulative_rejection_diagnostic_v1_CERTIFIED.json").read_text(encoding="utf-8"))
    checks = [("certified record accepted", valid(record, evidence_root=evidence_root))]
    mutations = [
        ("status", lambda r: r.update(status="PASSED")),
        ("authorization", lambda r: r.update(betting_authorized=True)),
        ("production", lambda r: r.update(production_changed=True)),
        ("2025", lambda r: r.update(confirmation_2025_opened=True)),
        ("May", lambda r: r.update(may_2026_opened=True)),
        ("report hash", lambda r: r["artifacts"]["report"].update(sha256="0" * 64)),
        ("population", lambda r: r["population"].update(rows=1)),
        ("ranking", lambda r: r["diagnostic_ranking"].update(highest_pa_outcome_target="home_run")),
        ("finding", lambda r: r["measured_findings"]["bip_out"].update(actual_rate=0.0)),
        ("offset", lambda r: r["measured_findings"]["largest_offsetting_log_loss_regression"].update(pa_outcome="walk")),
        ("decision", lambda r: r["decision"].update(cumulative_candidate_remains_rejected=False)),
        ("invariant", lambda r: r["protected_invariants"].update(may_2026_unread=False)),
    ]
    for name, fn in mutations:
        checks.append((f"{name} mutation rejected", not valid(mutate(record, fn), evidence_root=evidence_root)))
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"certified rejection diagnostic record checks failed: {failed}")
    print(f"CERTIFIED REJECTION DIAGNOSTIC RECORD VALID: {len(checks)}/{len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
