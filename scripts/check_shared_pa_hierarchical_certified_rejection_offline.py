#!/usr/bin/env python3
"""Validate and mutation-test the certified hierarchical rejection record."""
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
        if record["status"] != "HIERARCHICAL_SELECTION_REJECTION_CERTIFIED":
            return False
        if record["betting_authorized"] or record["production_changed"] or record["confirmation_2025_opened"] or record["may_2026_opened"]:
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
        if report["status"] != "HIERARCHICAL_SELECTION_REJECTED_NO_CANDIDATE" or certificate["status"] != record["status"]:
            return False
        if report["source_commit"] != record["selector_source_commit"]:
            return False
        if record["population"] != report["population"]:
            return False
        for metric, value in record["selected_scores"].items():
            if not math.isclose(value, report["selected"]["scores"][metric], rel_tol=0, abs_tol=1e-14):
                return False
        for comparator in ("strongest_simple", "canonical_v1"):
            source = report["selected"][f"intervals_vs_{comparator}"]
            for metric, item in record["paired_candidate_minus_comparator"][f"vs_{comparator}"].items():
                if not math.isclose(item["point"], source[metric]["point"], rel_tol=0, abs_tol=1e-14):
                    return False
                if not math.isclose(item["upper_95"], source[metric]["upper"], rel_tol=0, abs_tol=1e-14):
                    return False
                if comparator == "strongest_simple":
                    ratio = abs(item["point"]) / abs(item["required_below"])
                    if not math.isclose(item["fraction_of_materiality_floor_reached"], ratio, rel_tol=0, abs_tol=1e-14) or item["passed"]:
                        return False
                elif not item["passed"]:
                    return False
        if record["market_adjudication"]["eligible_for_2025_confirmation"] != report["eligible_markets_for_2025_confirmation"]:
            return False
        if record["market_adjudication"]["evaluated_separately"] != list(report["derived_markets"]):
            return False
        if report["model_artifacts"] is not None or report["selection_passed"] or report["foundation_passed"]:
            return False
        decision = record["decision"]
        if decision["foundation_passed"] or decision["selection_passed"] or not decision["model_not_published"] or not decision["confirmation_2025_must_remain_unread"]:
            return False
        for item in record["validator_evidence"]["failed_attempts_preserved"]:
            path = ROOT / item["record"]
            if not path.is_file() or sha256(path) != item["sha256"]:
                return False
        for item in record["validator_evidence"]["retry_authorizations"]:
            path = ROOT / item["path"]
            if not path.is_file() or sha256(path) != item["sha256"]:
                return False
        if record["validator_evidence"]["final_mutations_passed"] != 16 or record["validator_evidence"]["final_mutations_required"] != 16:
            return False
        if not all(record["protected_invariants"].values()):
            return False
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False
    return True


def mutate(record: dict, fn: object) -> dict:
    changed = copy.deepcopy(record); fn(changed)  # type: ignore[operator]
    return changed


def main() -> int:
    evidence_root = Path(r"C:\Projects\baseball_predictor")
    record = json.loads((ROOT / "reports/shared_pa_hierarchical_selection_v1_CERTIFIED_REJECTION.json").read_text(encoding="utf-8"))
    checks = [("certified rejection accepted", valid(record, evidence_root=evidence_root))]
    mutations = [
        ("status", lambda r: r.update(status="PASSED")),
        ("authorization", lambda r: r.update(betting_authorized=True)),
        ("production", lambda r: r.update(production_changed=True)),
        ("2025", lambda r: r.update(confirmation_2025_opened=True)),
        ("May", lambda r: r.update(may_2026_opened=True)),
        ("report hash", lambda r: r["artifacts"]["report"].update(sha256="0" * 64)),
        ("score", lambda r: r["selected_scores"].update(multiclass_log_loss=0.0)),
        ("interval", lambda r: r["paired_candidate_minus_comparator"]["vs_canonical_v1"]["multiclass_brier"].update(upper_95=-99.0)),
        ("materiality", lambda r: r["paired_candidate_minus_comparator"]["vs_strongest_simple"]["multiclass_log_loss"].update(fraction_of_materiality_floor_reached=1.0)),
        ("market", lambda r: r["market_adjudication"]["eligible_for_2025_confirmation"].append("hits_0.5")),
        ("failure hash", lambda r: r["validator_evidence"]["failed_attempts_preserved"][0].update(sha256="0" * 64)),
        ("mutation count", lambda r: r["validator_evidence"].update(final_mutations_passed=15)),
        ("decision", lambda r: r["decision"].update(selection_passed=True)),
        ("invariant", lambda r: r["protected_invariants"].update(may_2026_unread=False)),
    ]
    for name, fn in mutations:
        checks.append((f"{name} mutation rejected", not valid(mutate(record, fn), evidence_root=evidence_root)))
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"hierarchical certified rejection checks failed: {failed}")
    print(f"HIERARCHICAL CERTIFIED REJECTION VALID: {len(checks)}/{len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
