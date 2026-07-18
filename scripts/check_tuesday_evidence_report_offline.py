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
    checks.append(("report remains research-only and May-sealed", built["betting_authorized"] is False and built["may_2026_read"] is False))
    checks.append(("56-date boundary remains locked", built["prospective_boundary"]["required_complete_dates"] == 56))
    checks.append(("all products remain separately unauthorized", all(value["authorization"] is False for value in built["execution_product_readiness"].values())))
    pitcher_k = built["market_evidence"]["pitcher_strikeouts"]
    checks.append(("pitcher-K audit stays outcome-blind, May-sealed, and unauthorized",
                   pitcher_k["official_outcomes_read"] is False
                   and pitcher_k["may_2026_read"] is False
                   and pitcher_k["betting_authorized"] is False))
    checks.append(("215-to-194 readiness mutation is caught", caught(items, lambda x: x["forward_shadow_readiness"].__setitem__("guard_checks_passed", 194))))
    checks.append(("215-to-214 readiness mutation is caught", caught(items, lambda x: x["forward_shadow_readiness"].__setitem__("guard_checks_passed", 214))))
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

    original_smoke_root = report.SMOKE_ROOT
    try:
        with tempfile.TemporaryDirectory(prefix="fake_smoke_certificate_") as temporary:
            fake_root = Path(temporary)
            report.SMOKE_ROOT = fake_root
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
                report._operational_smoke_status()
            except ValueError:
                fake_certificate_caught = True
            else:
                fake_certificate_caught = False
    finally:
        report.SMOKE_ROOT = original_smoke_root
    checks.append(("handcrafted verified=true smoke certificate cannot change report status", fake_certificate_caught))

    original_source_release = report._source_release
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
