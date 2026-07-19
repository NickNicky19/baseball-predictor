#!/usr/bin/env python3
"""Check the exact scope-restoration retry for hierarchical certification."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256(); digest.update(path.read_bytes()); return digest.hexdigest()


def valid(retry: dict, failure: dict) -> bool:
    try:
        if retry["status"] != "LOCKED_SCOPE_RESTORATION_BEFORE_CERTIFICATION":
            return False
        if retry["betting_authorized"] or not retry["production_unchanged"]:
            return False
        if not retry["confirmation_2025_forbidden"] or not retry["may_2026_forbidden"]:
            return False
        if set(retry["allowed_changes"]) != {
            "restore_validator_mutation_checker_byte_for_byte_to_report_bound_hash",
            "teach_certifier_to_bind_retry_v3_after_scope_restoration",
        }:
            return False
        if failure["status"] != "VALIDATOR_RETRY_SCOPE_FAILURE_RESULT_UNADJUDICATED":
            return False
        if failure["failure"]["model_result_adjudicated"] or failure["failure"]["certificate_published"]:
            return False
        hashes = retry["required_hashes"]
        if hashes["selector_report"] != failure["unchanged_complete_selector_artifacts"]["report_sha256"]:
            return False
        if hashes["selector_oof"] != failure["unchanged_complete_selector_artifacts"]["oof_sha256"]:
            return False
        if hashes["repaired_validator_before_v3"] != failure["repaired_validator_sha256"]:
            return False
        if hashes["restored_mutation_checker"] != failure["expected_original_mutation_checker_sha256"]:
            return False
        if not all(retry["post_retry_requirements"].values()) or not all(failure["protected_invariants"].values()):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def main() -> int:
    retry_path = ROOT / "config/shared_pa_hierarchical_validator_retry_v3.json"
    retry = json.loads(retry_path.read_text(encoding="utf-8"))
    failure_path = ROOT / retry["failure_v2_record"]["path"]
    if sha256(failure_path) != retry["failure_v2_record"]["sha256"]:
        raise SystemExit("hierarchical validator v2 failure-record hash changed")
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    checks = [("locked scope restoration accepted", valid(retry, failure))]
    mutations = [
        ("authorization", lambda r: r.update(betting_authorized=True)),
        ("2025", lambda r: r.update(confirmation_2025_forbidden=False)),
        ("allowed", lambda r: r["allowed_changes"].append("change_model")),
        ("report hash", lambda r: r["required_hashes"].update(selector_report="0" * 64)),
        ("checker hash", lambda r: r["required_hashes"].update(restored_mutation_checker="0" * 64)),
        ("requirement", lambda r: r["post_retry_requirements"].update(original_mutation_checker_must_pass_unchanged=False)),
    ]
    for name, fn in mutations:
        changed = copy.deepcopy(retry); fn(changed)
        checks.append((f"{name} mutation rejected", not valid(changed, failure)))
    changed_failure = copy.deepcopy(failure); changed_failure["failure"]["certificate_published"] = True
    checks.append(("premature certificate rejected", not valid(retry, changed_failure)))
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"hierarchical validator retry v3 checks failed: {failed}")
    print(f"HIERARCHICAL VALIDATOR RETRY V3 CHECKS VALID: {len(checks)}/{len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
