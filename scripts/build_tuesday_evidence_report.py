#!/usr/bin/env python3
"""Build the fail-closed, hash-bound Tuesday authorization-progress report.

The report may summarize certified open research artifacts and operational
completeness. It must not inspect prospective economic outcomes before the
locked evidence boundary, open May 2026, or imply betting authorization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.certify_forward_operational_smoke_full_day import (  # noqa: E402
    require_full_day_completion,
)
from src.evaluation.forward_evidence_era import (  # noqa: E402
    validate_operational_smoke_certificate,
)
from src.evaluation.shadow_capture_plan import load_capture_plan  # noqa: E402

LOCKED_GOAL_SHA = "25d845efeb0189c313a6a9d3e646c53619d12667c8b16929013287b04bee4f4e"
SMOKE_ROOT = ROOT / "data/learning/shadow/operational_smoke_v11"

INPUTS = {
    "goal_contract": (
        "GOAL_TUESDAY_MARKET_AUTHORIZATION.md",
        LOCKED_GOAL_SHA,
    ),
    "hr_rejection": (
        "data/analysis/hr_over_contract_v1/batted_ball_candidate_v1/REJECTED_OPEN_PERIOD.json",
        "5f7fa92f841cb19ebb93390448b4741bc6728d5370e06aaef80fe7663017b2cf",
    ),
    "hits_consolidation": (
        "data/analysis/hits_evidence_consolidation_v1/report.json",
        "d8f20e6f440341076aea36f288cce908a0a2c99f22385f1429811e21f6ecce43",
    ),
    "other_market_contracts": (
        "data/analysis/other_market_contracts_v1/report.json",
        "9f9d118790b7aee7001a6e1d336d818382fffdbcc2357ec7defbdbd3756ac26c",
    ),
    "market_ranking": (
        "data/analysis/tuesday_market_ranking_v1/report.json",
        "2b0eaf165d43d66bfc6b2fb66ecd8956b0cea933aba17bf1914d25e47bf66ec3",
    ),
    "hits_contact_protocol": (
        "data/analysis/hits_contact_adapter_candidate_v1/protocol.json",
        "27ab9c23eb78dd8cd9045b3e243ad768b5bbbab076a12a8c49d34de17c53336b",
    ),
    "hits_contact_adjudication": (
        "data/analysis/hits_contact_adapter_candidate_v1/open_adjudication_report.json",
        "aace784fded2c1c32b0759ceef392b113fc8e55940ea3791b90cd9f2fe55deac",
    ),
    "june_identity_correction": (
        "data/analysis/hits_contact_adapter_candidate_v1/june_sequence_bridge_correction_certificate.json",
        "0755b8348e609397a97cdf935b4fbb708548c6a025911b7a7b846b0089bb26fb",
    ),
    "june_economic_enrichment": (
        "data/analysis/hits_contact_adapter_candidate_v1/june_sequence_v3_economic_enrichment_audit.json",
        "509e000cb124edf99de24c0c54ab5ef61b409e19afaf2967e33eb5b198bc45a5",
    ),
    "forward_shadow_readiness": (
        "reports/forward_shadow_readiness_v23.json",
        "bf7840ab0cb1ad466ef7792695c6802b403e3089e5872321835b639675d0e3cf",
    ),
    "operational_smoke_preflight": (
        "reports/forward_shadow_operational_smoke_preflight_v6_2026-07-18.json",
        "6906c23d4dd3dc759cf69eab855c44aa41ed629384a665df185816e03301048f",
    ),
    "operational_smoke_scope": (
        "data/learning/shadow/operational_smoke_v11/evidence_scope.json",
        "0d5fefe301ef0c8353a1d40f3b7e22fc74c509ec02a8ec426f8e51337f9b136e",
    ),
    "operational_smoke_runtime": (
        "data/learning/shadow/operational_smoke_v11/runtime_manifest.json",
        "76cf3169b89d4796380e6707df6ec1789506b39063d0de9848f553f8a7bd6585",
    ),
    "forward_evidence_boundary": (
        "config/forward_shadow_evidence_boundary.json",
        "3a68c32e32ea3479b7b567ae195bf76a0c2b7eadb97bc0910e999c7ad9beaecf",
    ),
    "deployment_protocol": (
        "config/forward_shadow_deployment_protocol.json",
        "9fe3939378276337d800e4e6f3a986d6d3833ee7b65508e022c5782d95165b00",
    ),
    "execution_product_contracts": (
        "config/hits_execution_product_contracts.json",
        "2176655f236649852de70f2ac7bad639876db64b8a3a5f612aae8ced34aea3fd",
    ),
    "onyx_public_rules": (
        "data/evidence/execution_products/onyx_public_rules_observation_2026-07-17.json",
        "8370950090a3fc8fa67157bfe0a72e5baeec4805c1e082b3ff4092323916116b",
    ),
    "novig_public_rules": (
        "data/evidence/execution_products/novig_public_rules_observation_2026-07-17.json",
        "17c6bea31eb201b450aa8c692e8f8da20d3093290f97ada3bddbec781a5130c8",
    ),
    "chalkboard_public_rules": (
        "data/evidence/execution_products/chalkboard_public_rules_observation_2026-07-17.json",
        "040efa29660fddb9d21cdd7d1b0ee18c74179287bd1f59438cbbadb061e2d3f4",
    ),
    "prizepicks_public_rules": (
        "data/evidence/execution_products/prizepicks_public_rules_observation_2026-07-17.json",
        "a249e3e6ff3d3edda438f80a2793ba24f06df6cf597a211b597fe5ca79a9109d",
    ),
    "pitcher_strikeout_readiness_audit": (
        "reports/pitcher_strikeout_readiness_audit_2026-07-17.md",
        "66b9519c0e25da1bd710367ec1c16fa2f339ecdfe7a32bc3fc0c295188a62b82",
    ),
}


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def verify_inputs() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, Any]]]:
    bound: dict[str, dict[str, str]] = {}
    loaded: dict[str, dict[str, Any]] = {}
    for label, (relative, expected) in INPUTS.items():
        path = ROOT / relative
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"{label} hash mismatch: expected {expected}, got {actual}")
        bound[label] = {"path": relative, "sha256": actual}
        if path.suffix == ".json":
            loaded[label] = load_json(path)
    return bound, loaded


def require_facts(items: dict[str, dict[str, Any]]) -> None:
    hr = items["hr_rejection"]
    hits = items["hits_consolidation"]
    contact = items["hits_contact_adjudication"]
    other = items["other_market_contracts"]
    ranking = items["market_ranking"]
    shadow = items["forward_shadow_readiness"]
    preflight = items["operational_smoke_preflight"]
    scope = items["operational_smoke_scope"]
    runtime = items["operational_smoke_runtime"]
    boundary = items["forward_evidence_boundary"]
    products = items["execution_product_contracts"]

    if hr.get("candidate_supported") is not False or hr.get("may_opened") is not False:
        raise ValueError("HR rejection or May state drifted")
    if hits.get("betting_authorized") is not False or hits.get("may_2026_holdout_read") is not False:
        raise ValueError("Hits consolidation authorization or May state drifted")
    if contact.get("status") != "CANDIDATE_REJECTED_OPEN_GATE_FAILED":
        raise ValueError("Hits contact candidate is not in its certified rejected state")
    if contact.get("may_2026_read") is not False or contact.get("betting_authorized") is not False:
        raise ValueError("Hits contact report violates May or authorization state")
    if other.get("may_2026_read") is not False or other.get("official_outcomes_read") is not False:
        raise ValueError("other-market audit no longer proves outcome-blind/no-May scope")
    selected = ranking.get("selected_additional_candidates")
    if not isinstance(selected, list) or not selected or selected[0].get("candidate_id") != "hits_point_in_time_hitter_contact_adapter_v1":
        raise ValueError("ranked additional candidate drifted")
    if shadow.get("guard_checks_passed") != 215 or shadow.get("betting_authorized") is not False:
        raise ValueError("forward-shadow readiness is not the certified 215/215 research state")
    if shadow["capture_timing"].get("durable_external_primary_collector_deployed") is not False:
        raise ValueError("unexpected durable collector state")
    if shadow["forward_evidence"].get("capture_measurable") is not False:
        raise ValueError("unexpected measurable forward evidence before the boundary")
    if preflight.get("live_request_made") is not False or preflight.get("economic_evidence_eligible") is not False:
        raise ValueError("operational-smoke preflight is no longer an unused excluded preflight")
    if scope.get("mode") != "operational_smoke" or scope.get("economic_evidence_eligible") is not False:
        raise ValueError("operational smoke scope is not permanently excluded")
    if (
        runtime.get("schema_version") != "forward-runtime-manifest-v1"
        or runtime.get("contains_secrets") is not False
        or scope.get("runtime_manifest", {}).get("sha256")
        != INPUTS["operational_smoke_runtime"][1]
        or scope.get("runtime_manifest", {}).get("fingerprint_sha256")
        != runtime.get("fingerprint_sha256")
    ):
        raise ValueError("operational smoke runtime is not exactly frozen and secret-free")
    first_look = boundary.get("first_economic_look_boundary", {})
    if first_look.get("minimum_complete_official_date_blocks") != 56:
        raise ValueError("forward evidence boundary no longer requires 56 complete dates")
    if boundary.get("open_data_derivation", {}).get("may_2026_used") is not False:
        raise ValueError("May entered the boundary derivation")
    if products.get("betting_authorized") is not False or products.get("cross_product_inheritance_permitted") is not False:
        raise ValueError("execution-product contract is not fail-closed")
    for product, contract in products.get("products", {}).items():
        if contract.get("authorization") is not False:
            raise ValueError(f"{product} unexpectedly has authorization")
        if contract.get("actual_submit_accept_receipt_verified") is not False:
            raise ValueError(f"{product} unexpectedly claims accepted-entry evidence")
        if contract.get("actual_settlement_receipt_verified") is not False:
            raise ValueError(f"{product} unexpectedly claims settlement evidence")


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    commit = result.stdout.strip().lower()
    if len(commit) != 40:
        raise ValueError("source release commit is unavailable")
    return commit


def _git_upstream_state(head: str) -> dict[str, Any]:
    upstream_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if upstream_result.returncode != 0:
        return {
            "upstream": None,
            "ahead": None,
            "behind": None,
            "head_present_on_upstream": False,
        }
    upstream = upstream_result.stdout.strip()
    counts = subprocess.run(
        ["git", "rev-list", "--left-right", "--count", f"{upstream}...{head}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if counts.returncode != 0:
        raise ValueError("source release upstream divergence is unavailable")
    pieces = counts.stdout.split()
    if len(pieces) != 2:
        raise ValueError("source release upstream divergence is malformed")
    behind, ahead = (int(value) for value in pieces)
    published = subprocess.run(
        ["git", "merge-base", "--is-ancestor", head, upstream],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if published.returncode not in (0, 1):
        raise ValueError("source release upstream ancestry is unavailable")
    return {
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "head_present_on_upstream": published.returncode == 0,
    }


def _head_file_sha256(relative: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"HEAD:{relative}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return hashlib.sha256(result.stdout).hexdigest()


def _source_release() -> dict[str, Any]:
    head = _git_head()
    current_files: dict[str, dict[str, Any]] = {}
    for relative in (
        "GOAL_TUESDAY_MARKET_AUTHORIZATION.md",
        "scripts/build_tuesday_evidence_report.py",
        "scripts/check_tuesday_evidence_report_offline.py",
    ):
        disk_sha = sha256(ROOT / relative)
        head_sha = _head_file_sha256(relative)
        current_files[relative] = {
            "sha256": disk_sha,
            "head_sha256": head_sha,
            "matches_head": disk_sha == head_sha,
        }
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {
        "commit": head,
        "branch": branch,
        "reporting_layer_committed": all(
            item["matches_head"] for item in current_files.values()
        ),
        "upstream_publication": _git_upstream_state(head),
        "current_files": current_files,
    }


def _operational_smoke_status() -> dict[str, Any]:
    plans = sorted(SMOKE_ROOT.glob("plans/*/plan.json"))
    expected_targets = 0
    planned_dates: list[str] = []
    for path in plans:
        payload = load_json(path)
        targets = payload.get("targets")
        if not isinstance(targets, list):
            raise ValueError(f"{path}: smoke plan targets are malformed")
        expected_targets += len(targets)
        planned_dates.append(path.parent.name)

    entry_terminal = len(list(SMOKE_ROOT.glob("live/*/*/terminal_attempt.json")))
    entry_bundles = len(list(SMOKE_ROOT.glob("live/*/*/target_bundle.json")))
    prestart_bundles = len(list(SMOKE_ROOT.glob("close/*/*/prestart_reference_bundle.json")))
    prestart_errors = len(list(SMOKE_ROOT.glob("close/*/*/prestart_terminal_error.json")))
    ledger = SMOKE_ROOT / "ledger/forward_ledger.jsonl"
    ledger_rows = 0
    if ledger.is_file():
        ledger_rows = sum(1 for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip())

    certificates: list[dict[str, Any]] = []
    for path in SMOKE_ROOT.glob("**/*.json"):
        try:
            payload = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if payload.get("schema_version") == "forward-operational-smoke-certificate-v2":
            certificates.append({"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "payload": payload})
    if len(certificates) > 1:
        raise ValueError("multiple operational-smoke certificates exist")
    verified = False
    if certificates:
        certificate = certificates[0]
        certificate_path = ROOT / certificate["path"]
        validated = validate_operational_smoke_certificate(certificate_path, root=ROOT)
        official_date = str(validated["official_game_date"])
        if planned_dates != [official_date]:
            raise ValueError("operational-smoke certificate does not cover the exact planned date")
        lifecycle_path = certificate_path.parent / validated["lifecycle_verification"]["path"]
        lifecycle = load_json(lifecycle_path)
        plan = load_capture_plan(SMOKE_ROOT / "plans" / official_date / "plan.json")
        require_full_day_completion(plan, lifecycle)
        verified = True
    if verified:
        state = "VERIFIED_COMPLETE_PERMANENTLY_EXCLUDED"
    elif plans or entry_terminal or entry_bundles:
        state = "IN_PROGRESS_NOT_CERTIFIED"
    else:
        state = "NOT_STARTED"
    certificate = None
    if certificates:
        certificate = {key: certificates[0][key] for key in ("path", "sha256")}
    return {
        "state": state,
        "planned_dates": planned_dates,
        "expected_targets": expected_targets,
        "entry_terminal_receipts": entry_terminal,
        "eligible_entry_bundles": entry_bundles,
        "prestart_reference_bundles": prestart_bundles,
        "prestart_terminal_errors": prestart_errors,
        "ledger_rows": ledger_rows,
        "certificate": certificate,
        "verified_complete": verified,
        "economic_evidence_eligible": False,
        "betting_authorized": False,
    }


def _product_readiness(contracts: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in contracts["products"].items():
        result[key] = {
            "contract_id": value["contract_id"],
            "readiness": value["readiness"],
            "public_platform_texas_eligibility_observed": value.get("public_platform_texas_eligibility_observed", False),
            "legal_and_account_access_verified": value["legal_and_account_access_verified"],
            "live_account_visible_hits_verified": value["live_account_visible_hits_verified"],
            "payout_and_fee_schedule_hash": value["payout_and_fee_schedule_hash"],
            "actual_submit_accept_receipt_verified": value["actual_submit_accept_receipt_verified"],
            "actual_settlement_receipt_verified": value["actual_settlement_receipt_verified"],
            "authorization": value["authorization"],
        }
    return result


def _market_ranking(other: dict[str, Any]) -> list[dict[str, Any]]:
    reports = other["market_reports"]
    return [
        {"rank": 1, "market": "hits", "readiness": "highest", "state": "KBB_RESEARCH_BASELINE_CONTACT_ADAPTER_REJECTED"},
        {"rank": 2, "market": "home_runs_over_0.5", "readiness": "research contract complete", "state": "BATTED_BALL_CANDIDATE_REJECTED"},
        {
            "rank": 3,
            "market": "total_bases",
            "readiness": "contract work only",
            "state": "UNCERTIFIED_MARKET_UNIVERSE",
            "measured_two_sided_entry_close_selections": reports["total_bases"]["price_contract"]["draftkings_entry_and_close_two_sided_selections"],
        },
        {
            "rank": 4,
            "market": "pitcher_strikeouts",
            "readiness": "outcome-blind price inventory only",
            "state": "HARD_IDENTITY_AND_EXACT_UNIVERSE_BLOCKED",
            "draftkings_two_sided_t4_and_close_paths": 1948,
        },
        {"rank": 5, "market": "rbi", "readiness": "not scoreable", "state": "OFFICIAL_OUTCOME_AND_SIMULATION_CONTRACT_BLOCKED"},
        {"rank": 6, "market": "hits_runs_rbi", "readiness": "not scoreable", "state": "HISTORICAL_PRODUCT_ABSENT"},
    ]


def _markdown(report: dict[str, Any]) -> str:
    smoke = report["operational_smoke"]
    hits = report["market_evidence"]["hits"]
    lines = [
        "# Tuesday Market-Authorization Evidence Report",
        "",
        f"Status: **{report['status']}**",
        "",
        "Betting is **not authorized**. May 2026 remained sealed.",
        "",
        "## Source and forward lifecycle",
        "",
        f"- Source commit: `{report['source_release']['commit']}`.",
        f"- Local lifecycle guards: {report['forward_shadow_readiness']['guard_checks_passed']}/206.",
        f"- Operational smoke: {smoke['state']} ({smoke['entry_terminal_receipts']}/{smoke['expected_targets']} terminal entry receipts).",
        f"- First economic look: {report['prospective_boundary']['completed_complete_dates']}/{report['prospective_boundary']['required_complete_dates']} complete future dates.",
        "",
        "## Open research evidence",
        "",
        f"- Hits/KBB combined open capture: {hits['current_kbb_open']['combined_capture']:.4f}.",
        f"- Hits/KBB combined flat-stake theoretical ROI: {hits['current_kbb_open']['combined_flat_stake_roi']:.4f}.",
        "- The Hits contact adapter and HR batted-ball candidate were rejected under their locked gates.",
        "- Pitcher strikeouts have 1,948 outcome-blind DraftKings T-4h/close paths, but no hard-keyed scoreable universe or admissible economic gate.",
        "- Total Bases, pitcher strikeouts, RBI, and Hits+Runs+RBI remain separate research contracts and are not authorization candidates.",
        "",
        "## Execution products",
        "",
    ]
    for product, state in report["execution_product_readiness"].items():
        lines.append(f"- {product}: {state['readiness']}; authorization=false.")
    lines.extend([
        "",
        "## Cloud recommendation",
        "",
        report["cloud_hosting_recommendation"]["recommendation"],
        "",
        "## Single highest-value next action",
        "",
        report["single_highest_value_next_action"]["action"],
        "",
        report["single_highest_value_next_action"]["why"],
        "",
        "No report field constitutes legal advice, an executable recommendation, or betting authorization.",
    ])
    return "\n".join(lines) + "\n"


def build_report(bound: dict[str, dict[str, str]], items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    hr = items["hr_rejection"]
    hits = items["hits_consolidation"]
    contact = items["hits_contact_adjudication"]
    other = items["other_market_contracts"]
    shadow = items["forward_shadow_readiness"]
    boundary = items["forward_evidence_boundary"]
    contracts = items["execution_product_contracts"]
    smoke = _operational_smoke_status()
    contact_combined = contact["combined_open"]
    contact_march = contact["blocks"]["march_april_fit"]
    contact_june = contact["blocks"]["june_replication"]

    source_release = _source_release()
    if not source_release["reporting_layer_committed"]:
        status = "REPORTING_LAYER_UNCOMMITTED_RESEARCH_ONLY"
        action = "Commit and publish the strengthened goal and mutation-tested reporting layer before treating this report as immutable evidence."
        why = "The report correctly detected that its own goal or builder bytes are not yet contained in the stated source commit."
    elif not source_release["upstream_publication"]["head_present_on_upstream"]:
        status = "REPORTING_LAYER_UNPUBLISHED_RESEARCH_ONLY"
        action = "Publish the exact committed reporting release to its configured trusted upstream before treating it as shared immutable evidence."
        why = "The reporting layer is committed locally, but the exact source commit is not present on the configured upstream reference."
    elif smoke["verified_complete"]:
        status = "SMOKE_VERIFIED_FORWARD_ERA_NOT_STARTED_RESEARCH_ONLY"
        action = "Prepare a clean checkout of the immutable release, deploy the durable external primary, and create the new hash-bound forward evidence era."
        why = "The excluded lifecycle smoke has passed; prospective evidence still requires an always-on primary and one clean frozen era."
    else:
        status = "OPERATIONAL_SMOKE_REQUIRED_RESEARCH_ONLY"
        action = "Rotate the exposed provider credential and complete the permanently excluded operational smoke before a future T-4h target."
        why = "The lifecycle is mutation-tested but has not yet been proven against one real future entry, prestart reference, official final, settlement, and ledger sequence."

    products = _product_readiness(contracts)
    return {
        "schema_version": "tuesday-market-authorization-evidence-v2",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "deadline": "2026-07-21T08:00:00-05:00",
        "status": status,
        "betting_authorized": False,
        "may_2026_read": False,
        "source_release": source_release,
        "locked_gates": {
            "capture_lower_95_bound_strictly_greater_than": 0.10,
            "net_roi_lower_95_bound_strictly_greater_than": 0.0,
            "point_estimates_can_authorize": False,
            "product_pooling_permitted": False,
        },
        "completed_gates": [
            "future-only T-4h lifecycle implemented and mutation-tested locally",
            "official MLB final-feed outcome and settlement workers implemented",
            "immutable ledger and exclusion funnels implemented",
            "public rules observations retained separately for four execution products",
            "source release committed with readiness-bound hashes",
            *(
                ["exact source commit present on the configured upstream reference"]
                if source_release["upstream_publication"]["head_present_on_upstream"]
                else []
            ),
            "exact secret-free Python/runtime fingerprint bound to the operational smoke",
        ],
        "operational_smoke": smoke,
        "expected_vs_captured_targets": {
            "expected": smoke["expected_targets"],
            "terminal_entry_receipts": smoke["entry_terminal_receipts"],
            "eligible_entry_bundles": smoke["eligible_entry_bundles"],
            "prestart_reference_bundles": smoke["prestart_reference_bundles"],
            "ledger_rows": smoke["ledger_rows"],
        },
        "explicit_exclusion_funnel": {
            "operational_smoke_rows_count_as_economic_evidence": False,
            "prestart_terminal_errors": smoke["prestart_terminal_errors"],
            "incomplete_dates_count_toward_boundary": False,
            "missed_targets_backfilled": False,
            "vendor_results_used_for_grading": False,
        },
        "execution_product_readiness": products,
        "jurisdiction_execution_blockers": [
            "public Texas availability is not an independent legal determination",
            "the user's current account eligibility and geolocation acceptance are unverified",
            "no account-visible Hits market or complete lineup evidence is retained",
            "no accepted entry, fill or match, settlement receipt, or actual return is retained",
        ],
        "prospective_boundary": {
            "required_complete_dates": boundary["first_economic_look_boundary"]["minimum_complete_official_date_blocks"],
            "completed_complete_dates": 0,
            "economic_outcomes_inspected": False,
            "operational_smoke_counts": False,
            "may_counts": False,
        },
        "cloud_hosting_recommendation": {
            "status": "NO_DURABLE_HOST_RELIABILITY_EVIDENCE",
            "recommendation": "Do not rank or purchase a host on unmeasured reliability. Complete the excluded local smoke, then deploy the immutable collector to one inexpensive always-on external primary and measure missed ticks, restarts, network failures, and complete-date rate; keep GitHub verification-only.",
            "github_as_primary": False,
        },
        "candidate_register": {
            "accepted_for_betting": [],
            "ready_to_open_may": [],
            "research_baselines": ["hits_fitted_kbb", "hr_over_0_5_frozen"],
            "readiness_audits": [
                {
                    "market": "pitcher_strikeouts",
                    "status": "HISTORICAL_PRICE_INVENTORY_PRESENT_CONTRACT_BLOCKED",
                    "audit_sha256": bound["pitcher_strikeout_readiness_audit"]["sha256"],
                }
            ],
            "rejected": [
                {"market": "home_runs_over_0.5", "candidate": hr["candidate"], "reason": "worsened proper scores in both open blocks"},
                {"market": "hits", "candidate": "hits_point_in_time_hitter_contact_adapter_v1", "reason": "locked open-period gate failed"},
                {"market": "hits", "candidate": "blanket_pa_or_monte_carlo_intervention", "reason": hits["evidence"]["pa_and_simulation"]["reason"]},
                {"market": "hits", "candidate": "pitcher_contact_input", "reason": hits["evidence"]["pitcher_contact"]["decision"]},
            ],
        },
        "market_evidence": {
            "hits": {
                "current_kbb_open": {
                    "march_april_strict_rows": contact_march["strict_rows"],
                    "june_strict_rows": contact_june["strict_rows"],
                    "combined_capture": contact_combined["economics"]["baseline"]["capture"],
                    "combined_flat_stake_roi": contact_combined["economics"]["baseline"]["flat_stake_roi"],
                },
                "rejected_contact_candidate": {
                    "combined_capture": contact_combined["economics"]["candidate"]["capture"],
                    "combined_capture_95": [
                        contact_combined["economics"]["candidate"]["capture_interval"]["lower"],
                        contact_combined["economics"]["candidate"]["capture_interval"]["upper"],
                    ],
                    "gate_passed": contact["gate"]["all_pass"],
                },
                "settlement": hits["evidence"]["settlement"],
                "executability": hits["evidence"]["executability"],
            },
            "home_runs_over_0.5": {
                "official_gradeable_keys": hr["funnel"]["official_gradeable_keys"],
                "latest_candidate_supported": False,
                "historical_executability_verified": False,
            },
            "total_bases": other["market_reports"]["total_bases"],
            "pitcher_strikeouts": {
                "audit_sha256": bound["pitcher_strikeout_readiness_audit"]["sha256"],
                "draftkings_two_sided_t4_and_close_paths": 1948,
                "draftkings_start_dates": 56,
                "may_2026_read": False,
                "official_outcomes_read": False,
                "hard_identity_complete": False,
                "exact_model_universe_complete": False,
                "betting_authorized": False,
            },
            "rbi": other["market_reports"]["rbi"],
            "hits_runs_rbi": other["market_reports"]["hrr"],
        },
        "market_readiness_ranking": _market_ranking(other),
        "forward_shadow_readiness": {
            "status": shadow["status"],
            "guard_checks_passed": shadow["guard_checks_passed"],
            "durable_primary_collector_deployed": False,
            "capture_measurable": False,
            "blockers": shadow["blockers"],
        },
        "remaining_authorization_blockers": [
            "operational smoke is not yet certified" if not smoke["verified_complete"] else "durable external primary is not deployed",
            "no forward evidence era has 56 complete future official dates",
            "no product has account-visible executable and settlement evidence",
            "no product-specific positive net-ROI lower bound exists",
            "no immutable product-specific authorization record exists",
        ],
        "single_highest_value_next_action": {
            "action": action,
            "why": why,
            "requires_user_authority": True,
            "requires_model_change": False,
            "opens_may": False,
            "authorizes_betting": False,
        },
        "inputs": bound,
        "terminal_statement": "The strengthened Tuesday goal remains in progress. This package reports verified state and blockers; it does not authorize betting.",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--markdown", required=True)
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args(argv)

    bound, items = verify_inputs()
    require_facts(items)
    report = build_report(bound, items)
    out = Path(args.out)
    markdown = Path(args.markdown)
    manifest = Path(args.manifest)
    for path in (out, markdown, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    markdown.write_text(_markdown(report), encoding="utf-8", newline="\n")
    binding = {
        "schema_version": "tuesday-evidence-report-binding-v2",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "report": {"path": str(out), "sha256": sha256(out)},
        "markdown": {"path": str(markdown), "sha256": sha256(markdown)},
        "goal_contract_sha256": LOCKED_GOAL_SHA,
        "source_commit": report["source_release"]["commit"],
        "status": report["status"],
        "betting_authorized": False,
        "may_2026_read": False,
    }
    manifest.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print("TUESDAY EVIDENCE PACKAGE BUILT")
    print(f"  status: {report['status']}")
    print("  betting authorized: FALSE")
    print("  May opened: FALSE")
    print(f"  operational smoke: {report['operational_smoke']['state']}")
    print(f"  report: {out} sha {binding['report']['sha256']}")
    print(f"  markdown: {markdown} sha {binding['markdown']['sha256']}")
    print(f"  binding: {manifest} sha {sha256(manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
