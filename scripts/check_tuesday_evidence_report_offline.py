#!/usr/bin/env python3
"""Mutation checks for the strengthened Tuesday evidence report."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_tuesday_evidence_report as report  # noqa: E402


def caught(items: dict, mutate) -> bool:
    candidate = copy.deepcopy(items)
    mutate(candidate)
    try:
        report.require_facts(candidate)
    except ValueError:
        return True
    return False


def main() -> int:
    checks: list[tuple[str, bool]] = []
    bound, items = report.verify_inputs()
    report.require_facts(items)
    built = report.build_report(bound, items)
    checks.append(("current hash-bound inputs pass", True))
    checks.append(("report safety dependency set is complete",
                   {
                       "scripts/certify_forward_operational_smoke_full_day.py",
                       "scripts/validate_execution_product_observation_contained.py",
                       "src/evaluation/forward_evidence_era.py",
                       "src/evaluation/shadow_capture_plan.py",
                   }.issubset(report.REPORTING_SOURCE_FILES)))
    checks.append(("report remains research-only and May-sealed", built["betting_authorized"] is False and built["may_2026_read"] is False))
    checks.append(("uncommitted reporting bytes are not listed as a completed gate",
                   built["source_release"]["reporting_layer_committed"]
                   or "source release committed with readiness-bound hashes"
                   not in built["completed_gates"]))
    checks.append(("56-date boundary remains locked", built["prospective_boundary"]["required_complete_dates"] == 56))
    checks.append(("all products remain separately unauthorized", all(value["authorization"] is False for value in built["execution_product_readiness"].values())))
    pitcher_k = built["market_evidence"]["pitcher_strikeouts"]
    checks.append(("pitcher-K audit stays outcome-blind, May-sealed, and unauthorized",
                   pitcher_k["official_outcomes_read"] is False
                   and pitcher_k["may_2026_read"] is False
                   and pitcher_k["betting_authorized"] is False
                   and pitcher_k["draftkings_two_sided_t4_and_close_paths"] == 1948
                   and pitcher_k["draftkings_start_dates"] == 56))
    changed_pitcher_items = copy.deepcopy(items)
    changed_pitcher_items["pitcher_strikeout_readiness_audit"][
        "two_sided_t4_and_close_paths"
    ] = 1947
    changed_pitcher_report = report.build_report(bound, changed_pitcher_items)
    checks.append(("pitcher-K report derives inventory from the bound audit",
                   changed_pitcher_report["market_evidence"]["pitcher_strikeouts"]
                   ["draftkings_two_sided_t4_and_close_paths"] == 1947))
    checks.append(("223-to-194 readiness mutation is caught", caught(items, lambda x: x["forward_shadow_readiness"].__setitem__("guard_checks_passed", 194))))
    checks.append(("223-to-222 readiness mutation is caught", caught(items, lambda x: x["forward_shadow_readiness"].__setitem__("guard_checks_passed", 222))))
    checks.append(("product authorization mutation is caught", caught(items, lambda x: x["execution_product_contracts"]["products"]["onyx"].__setitem__("authorization", True))))
    checks.append(("56-to-55 boundary mutation is caught", caught(items, lambda x: x["forward_evidence_boundary"]["first_economic_look_boundary"].__setitem__("minimum_complete_official_date_blocks", 55))))
    checks.append(("economic smoke mutation is caught", caught(items, lambda x: x["operational_smoke_scope"].__setitem__("economic_evidence_eligible", True))))
    original = report.INPUTS["goal_contract"]
    try:
        report.INPUTS["goal_contract"] = (original[0], "0" * 64)
        try:
            report.verify_inputs()
        except ValueError:
            goal_hash_caught = True
        else:
            goal_hash_caught = False
    finally:
        report.INPUTS["goal_contract"] = original
    checks.append(("goal hash mutation is caught", goal_hash_caught))
    pitcher_input = report.INPUTS["pitcher_strikeout_readiness_audit"]
    try:
        report.INPUTS["pitcher_strikeout_readiness_audit"] = (pitcher_input[0], "0" * 64)
        try:
            report.verify_inputs()
        except ValueError:
            pitcher_hash_caught = True
        else:
            pitcher_hash_caught = False
    finally:
        report.INPUTS["pitcher_strikeout_readiness_audit"] = pitcher_input
    checks.append(("pitcher-K audit hash mutation is caught", pitcher_hash_caught))
    checks.append(("runtime secret mutation is caught", caught(items, lambda x: x["operational_smoke_runtime"].__setitem__("contains_secrets", True))))
    checks.append(("credential retention mutation is caught", caught(items, lambda x: x["credential_rotation_attestation"].__setitem__("credential_value_retained", True))))
    checks.append(("credential rotation false mutation is caught", caught(items, lambda x: x["credential_rotation_attestation"].__setitem__("user_attested_previous_credential_rotated", False))))
    checks.append(("credential state is non-secret and research-only",
                   built["credential_security"]["rotation_status"] == "USER_ATTESTED_COMPLETE"
                   and built["credential_security"]["replacement_value_retained"] is False
                   and built["credential_security"]["replacement_value_logged"] is False
                   and built["credential_security"]["economic_evidence_eligible"] is False
                   and built["credential_security"]["betting_authorized"] is False))
    checks.append(("v11 is permanently failed and cannot masquerade as in progress",
                   built["operational_smokes"]["retained_v11"]["state"]
                   == "FAILED_PERMANENTLY_EXCLUDED_NOT_CERTIFIABLE"
                   and built["operational_smokes"]["retained_v11"]["source_error_receipts"] == 1
                   and built["operational_smokes"]["retained_v11"]["verified_complete"] is False))
    checks.append(("rotated credential provider access is distinguished from an eligible quote capture",
                   built["credential_security"]["provider_access_verified"] is True
                   and built["credential_security"]["provider_capture_verified"] is False))
    checks.append(("v11 diagnostic remains outcome-blind and successor-bound",
                   built["inputs"]["v11_event_identity_diagnostic"]["sha256"]
                   == report.INPUTS["v11_event_identity_diagnostic"][1]
                   and items["v11_event_identity_diagnostic"]["official_outcomes_inspected"] is False
                   and built["operational_smokes"]["retained_v11"]["state"]
                   == "FAILED_PERMANENTLY_EXCLUDED_NOT_CERTIFIABLE"))
    checks.append(("identity diagnostic outcome mutation is caught", caught(
        items,
        lambda x: x["v11_event_identity_diagnostic"].__setitem__("official_outcomes_inspected", True),
    )))
    rendered = report._markdown(built)
    checks.append(("markdown lifecycle guard denominator is current", "Local lifecycle guards: 223/223." in rendered and "/215" not in rendered))

    with tempfile.TemporaryDirectory(prefix="fake_smoke_certificate_") as temporary:
        fake_root = Path(temporary)
        (fake_root / "handcrafted.json").write_text(
            json.dumps({
                "schema_version": "forward-operational-smoke-certificate-v2",
                "official_game_date": "2026-07-18",
                "evidence_scope": {
                    "path": "missing_scope.json",
                    "sha256": "0" * 64,
                    "scope_sha256": "0" * 64,
                },
                "lifecycle_verification": {
                    "path": "missing_lifecycle.json",
                    "sha256": "0" * 64,
                },
                "verified": True,
                "complete_lifecycle": True,
                "operational_smoke": True,
                "economic_evidence_eligible": False,
                "betting_authorized": False,
            }),
            encoding="utf-8",
        )
        try:
            report._operational_smoke_status(fake_root, repo_root=ROOT)
        except ValueError:
            fake_certificate_caught = True
        else:
            fake_certificate_caught = False
    checks.append(("handcrafted verified=true smoke certificate cannot change report status", fake_certificate_caught))

    successor = copy.deepcopy(built["operational_smokes"]["retained_v11"])
    successor.update({
        "state": "IN_PROGRESS_NOT_CERTIFIED",
        "expected_targets": 15,
        "entry_terminal_receipts": 0,
        "eligible_entry_bundles": 0,
        "source_error_receipts": 0,
        "verified_complete": False,
    })
    original_source_release = report._source_release
    published = copy.deepcopy(built["source_release"])
    published["reporting_layer_committed"] = True
    published["upstream_publication"]["head_present_on_upstream"] = True
    try:
        report._source_release = lambda: published
        successor_report = report.build_report(bound, items, successor_smoke=successor)

        verified_successor = copy.deepcopy(successor)
        verified_successor.update({
            "state": "VERIFIED_COMPLETE_PERMANENTLY_EXCLUDED",
            "entry_terminal_receipts": 15,
            "eligible_entry_bundles": 15,
            "verified_complete": True,
        })
        verified_report = report.build_report(
            bound,
            items,
            successor_smoke=verified_successor,
        )
    finally:
        report._source_release = original_source_release
    checks.append(("successor smoke is reported without erasing retained v11 failure",
                   successor_report["operational_smoke"]["state"]
                   == "IN_PROGRESS_NOT_CERTIFIED"
                   and successor_report["operational_smokes"]["retained_v11"]["state"]
                   == "FAILED_PERMANENTLY_EXCLUDED_NOT_CERTIFIABLE"
                   and successor_report["status"]
                   == "SUCCESSOR_SMOKE_IN_PROGRESS_RESEARCH_ONLY"
                   and successor_report["cloud_hosting_recommendation"]["status"]
                   == "SUCCESSOR_SMOKE_INCOMPLETE_NO_DURABLE_HOST_RELIABILITY_EVIDENCE"))
    checks.append(("only the separately verified successor advances the lifecycle gate",
                   verified_report["status"]
                   == "SMOKE_VERIFIED_FORWARD_ERA_NOT_STARTED_RESEARCH_ONLY"
                   and verified_report["operational_smokes"]["retained_v11"]["verified_complete"] is False
                   and verified_report["betting_authorized"] is False
                   and verified_report["cloud_hosting_recommendation"]["status"]
                   == "EXCLUDED_LOCAL_SMOKE_VERIFIED_DURABLE_PRIMARY_NOT_DEPLOYED"))

    try:
        unpublished = copy.deepcopy(built["source_release"])
        unpublished["reporting_layer_committed"] = True
        unpublished["upstream_publication"]["head_present_on_upstream"] = False
        report._source_release = lambda: unpublished
        unpublished_report = report.build_report(bound, items)
    finally:
        report._source_release = original_source_release
    checks.append((
        "unpublished source commit cannot be reported as a completed publication gate",
        unpublished_report["status"] == "REPORTING_LAYER_UNPUBLISHED_RESEARCH_ONLY"
        and not any("present on the configured upstream" in gate for gate in unpublished_report["completed_gates"]),
    ))

    passed = sum(ok for _, ok in checks)
    for label, ok in checks:
        print(f"[{'OK' if ok else 'FAIL'}] {label}")
    print(f"{passed}/{len(checks)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
