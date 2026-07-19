#!/usr/bin/env python3
"""Check the hierarchical validator-only retry authorization."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def valid(retry: dict, failure: dict) -> bool:
    try:
        if retry["status"] != "LOCKED_VALIDATOR_ONLY_RETRY_BEFORE_CERTIFICATION":
            return False
        if retry["betting_authorized"] or not retry["production_unchanged"]:
            return False
        if not retry["confirmation_2025_forbidden"] or not retry["may_2026_forbidden"]:
            return False
        if not retry["selection_report_must_remain_byte_identical"] or not retry["selection_oof_must_remain_byte_identical"]:
            return False
        if failure["status"] != "VALIDATOR_MECHANICAL_FAILURE_RESULT_UNADJUDICATED":
            return False
        if failure["failure"]["model_result_adjudicated"] or failure["failure"]["certificate_published"]:
            return False
        if retry["allowed_change"]["id"] != "reconstruct_raw_outcome_columns_from_exhaustive_actual_pa_classes_inside_validator_only":
            return False
        required_mapping = {"out_k", "out_bb", "out_hits", "out_doubles", "out_triples", "out_hr", "out_ab", "out_pa"}
        if set(retry["allowed_change"]["exact_mapping"]) != required_mapping:
            return False
        if not retry["allowed_change"]["must_prove_round_trip_outcome_counts_exact"]:
            return False
        required_forbidden = {
            "selector", "model", "features", "probabilities", "outcomes", "protocol",
            "materiality", "market_gates", "stability", "calibration", "report", "oof",
        }
        if set(retry["forbidden_changes"]) != required_forbidden:
            return False
        if not all(retry["post_retry_requirements"].values()):
            return False
        if not all(failure["protected_invariants"].values()):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def main() -> int:
    retry = json.loads((ROOT / "config/shared_pa_hierarchical_validator_retry_v2.json").read_text(encoding="utf-8"))
    failure_path = ROOT / retry["failed_validator_record"]["path"]
    if sha256(failure_path) != retry["failed_validator_record"]["sha256"]:
        raise SystemExit("hierarchical validator failure-record hash changed")
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    checks = [("locked retry accepted", valid(retry, failure))]
    mutations = [
        ("authorization", lambda r: r.update(betting_authorized=True)),
        ("2025", lambda r: r.update(confirmation_2025_forbidden=False)),
        ("May", lambda r: r.update(may_2026_forbidden=False)),
        ("report mutation", lambda r: r.update(selection_report_must_remain_byte_identical=False)),
        ("allowed change", lambda r: r["allowed_change"].update(id="change_model")),
        ("mapping", lambda r: r["allowed_change"]["exact_mapping"].pop("out_k")),
        ("forbidden", lambda r: r["forbidden_changes"].remove("model")),
        ("requirements", lambda r: r["post_retry_requirements"].update(validator_mutations_required=False)),
    ]
    for name, fn in mutations:
        changed = copy.deepcopy(retry); fn(changed)
        checks.append((f"{name} mutation rejected", not valid(changed, failure)))
    changed_failure = copy.deepcopy(failure); changed_failure["failure"]["model_result_adjudicated"] = True
    checks.append(("premature adjudication rejected", not valid(retry, changed_failure)))
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"hierarchical validator retry checks failed: {failed}")
    print(f"HIERARCHICAL VALIDATOR RETRY V2 CHECKS VALID: {len(checks)}/{len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
