#!/usr/bin/env python3
"""Build the terminal hash-bound Tuesday market-authorization evidence report."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCKED_GOAL_SHA = "a16fb86571d037dbdf7a268218d5086fea905f555af44ce1bee5f03aab38fbf5"

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
        "data/analysis/tuesday_market_authorization_2026-07-21/forward_shadow_readiness.json",
        "073d1ab3219052359fd8746025304f90fa588db1ea22aeecd60f6642fafdc6f0",
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
            raise ValueError(
                f"{label} hash mismatch: expected {expected}, got {actual}"
            )
        bound[label] = {"path": relative, "sha256": actual}
        if path.suffix == ".json":
            loaded[label] = load_json(path)
    return bound, loaded


def require_facts(items: dict[str, dict[str, Any]]) -> None:
    hr = items["hr_rejection"]
    hits = items["hits_consolidation"]
    contact = items["hits_contact_adjudication"]
    other = items["other_market_contracts"]
    shadow = items["forward_shadow_readiness"]
    ranking = items["market_ranking"]
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
    if ranking.get("selected_additional_candidates", [{}])[0].get("candidate_id") != "hits_point_in_time_hitter_contact_adapter_v1":
        raise ValueError("ranked additional candidate drifted")
    if shadow.get("guard_checks_passed") != 38:
        raise ValueError("forward-shadow guards are not fully validated")
    if shadow["capture_timing"].get("durable_external_primary_collector_deployed") is not False:
        raise ValueError("unexpected external collector state")
    if shadow["forward_evidence"].get("capture_measurable") is not False:
        raise ValueError("unexpected forward evidence state")


def _market_ranking(other: dict[str, Any]) -> list[dict[str, Any]]:
    reports = other["market_reports"]
    return [
        {
            "rank": 1,
            "market": "hits",
            "current_state": "KBB_ACTIVE_RESEARCH_BASELINE_CONTACT_ADAPTER_REJECTED",
            "readiness": "highest",
            "blocking_reason": (
                "absolute capture and payout uncertainty remain below authorization; "
                "historical executability and forward evidence are absent"
            ),
        },
        {
            "rank": 2,
            "market": "home_runs_over_0.5",
            "current_state": "FROZEN_RESEARCH_BASELINE_BATTED_BALL_CANDIDATE_REJECTED",
            "readiness": "research contract complete",
            "blocking_reason": (
                "latest candidate worsened proper scores; next mapping requires a "
                "separate pre-2026 fit and no executable forward evidence exists"
            ),
        },
        {
            "rank": 3,
            "market": "total_bases",
            "current_state": "CONTRACT_WORK_ONLY",
            "readiness": "promising price inventory, uncertified market universe",
            "measured_draftkings_two_sided_entry_close_selections": reports[
                "total_bases"
            ]["price_contract"]["draftkings_entry_and_close_two_sided_selections"],
            "blocking_reason": (
                "canonical identity, settlement, duplicate resolution, and executability "
                "are incomplete"
            ),
        },
        {
            "rank": 4,
            "market": "rbi",
            "current_state": "RECONSTRUCTION_BLOCKED",
            "readiness": "not scoreable",
            "blocking_reason": (
                "official-outcome code falls through to Hits and no RBI simulation output "
                "path is certified"
            ),
        },
        {
            "rank": 5,
            "market": "hits_runs_rbi",
            "current_state": "RECONSTRUCTION_BLOCKED",
            "readiness": "historical product absent",
            "blocking_reason": (
                "zero matching product in the locked no-May vendor export"
            ),
        },
    ]


def _markdown(report: dict[str, Any]) -> str:
    hits = report["market_evidence"]["hits"]
    hr = report["market_evidence"]["home_runs_over_0.5"]
    lines = [
        "# Tuesday Market-Authorization Evidence Report",
        "",
        f"Status: **{report['status']}**",
        "",
        "Betting is **not authorized**. May 2026 remained sealed.",
        "",
        "## Completed work",
        "",
        "- HR-over-0.5 batted-ball candidate: validated and rejected; frozen behavior preserved.",
        "- Hits evidence: consolidated; the K/BB model remains the active research baseline.",
        "- Hits contact adapter: fully reconstructed on March-April and corrected June, then rejected under its locked gate.",
        "- Total Bases, RBI, and Hits+Runs+RBI: separate outcome-blind readiness contracts completed.",
        "- Market ranking: completed; exactly one additional candidate was advanced.",
        "- Forward-shadow guards: 38/38 passed; no primary collector or forward evidence exists yet.",
        "",
        "## Main evidence",
        "",
        f"- Current Hits/KBB combined open capture: {hits['current_kbb_open']['combined_capture']:.4f}; combined flat-stake ROI: {hits['current_kbb_open']['combined_flat_stake_roi']:.4f}.",
        f"- Rejected Hits contact candidate combined capture: {hits['rejected_contact_candidate']['combined_capture']:.4f}, 95% CI [{hits['rejected_contact_candidate']['combined_capture_95'][0]:.4f}, {hits['rejected_contact_candidate']['combined_capture_95'][1]:.4f}].",
        f"- HR candidate confirmation Brier delta 95%: [{hr['confirmation_brier_delta_95'][0]:.6f}, {hr['confirmation_brier_delta_95'][1]:.6f}].",
        f"- Locked authorization capture lower-bound bar: +{report['locked_capture_lower_bound']:.2f}.",
        "",
        "## Final decisions",
        "",
        "- No candidate is ready to open May.",
        "- No market is profitable, bettable, approved, or authorized by this package.",
        "- Hits remains the highest-readiness market, but its evidence does not clear the bar.",
        "",
        "## Single highest-value next action",
        "",
        report["single_highest_value_next_action"]["action"],
        "",
        report["single_highest_value_next_action"]["why"],
        "",
        "This action requires explicit deployment authority and does not change the model or authorize wagering.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--markdown", required=True)
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args(argv)

    bound, items = verify_inputs()
    require_facts(items)
    hr = items["hr_rejection"]
    hits = items["hits_consolidation"]
    contact = items["hits_contact_adjudication"]
    other = items["other_market_contracts"]
    shadow = items["forward_shadow_readiness"]

    contact_combined = contact["combined_open"]
    contact_march = contact["blocks"]["march_april_fit"]
    contact_june = contact["blocks"]["june_replication"]
    report = {
        "schema_version": "tuesday-market-authorization-evidence-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "deadline": "2026-07-21T08:00:00-05:00",
        "status": "EVIDENCE_PACKAGE_COMPLETE_RESEARCH_ONLY",
        "betting_authorized": False,
        "may_2026_read": False,
        "locked_capture_lower_bound": 0.10,
        "goal_contract": bound["goal_contract"],
        "completed_stages": {
            "stage_0_active_run_protection": "completed",
            "stage_1_hr_over_0_5": "completed_candidate_rejected",
            "stage_2_hits": "completed_contact_candidate_rejected",
            "stage_3_other_market_contracts": "completed_outcome_blind",
            "stage_4_rank_and_advance_one": "completed_exactly_one_candidate_advanced",
        },
        "candidate_register": {
            "accepted_for_betting": [],
            "ready_to_open_may": [],
            "still_research_only": [
                {
                    "market": "hits",
                    "candidate": "fitted_kbb_current_baseline",
                    "status": "ACTIVE_RESEARCH_BASELINE_NOT_PROMOTED",
                },
                {
                    "market": "home_runs_over_0.5",
                    "candidate": "frozen_pre_batted_ball_baseline",
                    "status": "PRESERVED_RESEARCH_BASELINE_NOT_PROMOTED",
                },
            ],
            "rejected": [
                {
                    "market": "home_runs_over_0.5",
                    "candidate": hr["candidate"],
                    "reason": "worsened Brier and log loss in both open blocks",
                },
                {
                    "market": "hits",
                    "candidate": "hits_point_in_time_hitter_contact_adapter_v1",
                    "reason": (
                        "June proper scores and flat-stake ROI regressed; combined "
                        "proper-score intervals crossed zero"
                    ),
                },
                {
                    "market": "hits",
                    "candidate": "blanket_pa_or_monte_carlo_intervention",
                    "reason": hits["evidence"]["pa_and_simulation"]["reason"],
                },
                {
                    "market": "hits",
                    "candidate": "pitcher_contact_input",
                    "reason": hits["evidence"]["pitcher_contact"]["decision"],
                },
            ],
        },
        "market_evidence": {
            "hits": {
                "current_kbb_open": {
                    "march_april_strict_rows": contact_march["strict_rows"],
                    "june_strict_rows": contact_june["strict_rows"],
                    "combined_capture": contact_combined["economics"]["baseline"]["capture"],
                    "combined_flat_stake_roi": contact_combined["economics"]["baseline"]["flat_stake_roi"],
                    "march_april_capture_95": [
                        contact_march["economics"]["baseline"]["capture_interval"]["lower"],
                        contact_march["economics"]["baseline"]["capture_interval"]["upper"],
                    ],
                    "march_april_flat_stake_roi_95": [
                        contact_march["economics"]["baseline"]["flat_stake_roi_interval"]["lower"],
                        contact_march["economics"]["baseline"]["flat_stake_roi_interval"]["upper"],
                    ],
                    "june_capture_95": [
                        contact_june["economics"]["baseline"]["capture_interval"]["lower"],
                        contact_june["economics"]["baseline"]["capture_interval"]["upper"],
                    ],
                    "june_flat_stake_roi_95": [
                        contact_june["economics"]["baseline"]["flat_stake_roi_interval"]["lower"],
                        contact_june["economics"]["baseline"]["flat_stake_roi_interval"]["upper"],
                    ],
                },
                "rejected_contact_candidate": {
                    "combined_capture": contact_combined["economics"]["candidate"]["capture"],
                    "combined_capture_95": [
                        contact_combined["economics"]["candidate"]["capture_interval"]["lower"],
                        contact_combined["economics"]["candidate"]["capture_interval"]["upper"],
                    ],
                    "combined_capture_delta_95": [
                        contact_combined["economics"]["candidate_minus_baseline_capture"]["lower"],
                        contact_combined["economics"]["candidate_minus_baseline_capture"]["upper"],
                    ],
                    "combined_brier_delta_95": [
                        contact_combined["probability_scores"]["candidate_minus_baseline"]["brier"]["lower"],
                        contact_combined["probability_scores"]["candidate_minus_baseline"]["brier"]["upper"],
                    ],
                    "combined_log_loss_delta_95": [
                        contact_combined["probability_scores"]["candidate_minus_baseline"]["log_loss"]["lower"],
                        contact_combined["probability_scores"]["candidate_minus_baseline"]["log_loss"]["upper"],
                    ],
                    "gate_passed": contact["gate"]["all_pass"],
                },
                "coverage": {
                    "june_corrected_strict_keys": items["june_identity_correction"]["strict_universe"]["rows"],
                    "missing_model_keys": 0,
                    "identity_mutations_caught": 4,
                    "candidate_evaluator_mutations_caught": 4,
                },
                "settlement": hits["evidence"]["settlement"],
                "executability": hits["evidence"]["executability"],
            },
            "home_runs_over_0.5": {
                "certified_model_market_keys": hr["funnel"]["certified_model_market_keys"],
                "official_gradeable_keys": hr["funnel"]["official_gradeable_keys"],
                "confirmation_brier_delta_95": hr["economic_results"]["confirmation"]["candidate_minus_frozen_brier_95"],
                "confirmation_log_loss_delta_95": hr["economic_results"]["confirmation"]["candidate_minus_frozen_log_loss_95"],
                "confirmation_candidate_roi": hr["economic_results"]["confirmation"]["candidate_theoretical_flat_stake_roi"],
                "confirmation_candidate_roi_95": hr["economic_results"]["confirmation"]["candidate_theoretical_roi_95"],
                "latest_candidate_supported": False,
                "historical_executability_verified": False,
            },
            "total_bases": other["market_reports"]["total_bases"],
            "rbi": other["market_reports"]["rbi"],
            "hits_runs_rbi": other["market_reports"]["hrr"],
        },
        "market_readiness_ranking": _market_ranking(other),
        "forward_shadow_readiness": {
            "status": shadow["status"],
            "guard_checks_passed": shadow["guard_checks_passed"],
            "github_is_primary_collector": False,
            "durable_primary_collector_deployed": False,
            "capture_measurable": False,
            "blockers": shadow["blockers"],
        },
        "remaining_authorization_blockers": [
            "no candidate absolute capture lower bound clears +0.10",
            "proper-score and payout evidence do not support the rejected candidates",
            "historical timestamps do not prove executable sportsbook availability or fills",
            "the inherited 90-minute freshness proxy remains unapproved",
            "no durable per-game T-4h primary collector is deployed",
            "no prospective linked entry/close/outcome evidence exists",
            "May remains sealed and no candidate is ready to open it",
            "no immutable market-specific authorization record exists",
        ],
        "single_highest_value_next_action": {
            "action": (
                "Deploy the existing fail-closed hard-keyed forward-shadow stack as a "
                "durable per-game T-4h primary collector for DraftKings Hits using the "
                "current K/BB research baseline; keep GitHub as verification/alert backup."
            ),
            "why": (
                "Hits has the strongest current contract and replicated research signal, "
                "while exact historical executability is the largest unresolved fact that "
                "cannot be reconstructed later. Prospective capture is time-sensitive and "
                "does not require weakening a model gate or opening May."
            ),
            "requires_user_authority": True,
            "requires_model_change": False,
            "opens_may": False,
            "authorizes_betting": False,
        },
        "inputs": bound,
        "terminal_statement": (
            "All feasible stages in the locked Tuesday goal are complete. Further "
            "authorization progress requires deployment authority for prospective "
            "capture or a separately predeclared future candidate."
        ),
    }

    out = Path(args.out)
    markdown = Path(args.markdown)
    manifest = Path(args.manifest)
    for path in (out, markdown, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown.write_text(_markdown(report), encoding="utf-8")
    binding = {
        "schema_version": "tuesday-evidence-report-binding-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "report": {"path": str(out), "sha256": sha256(out)},
        "markdown": {"path": str(markdown), "sha256": sha256(markdown)},
        "goal_contract_sha256": LOCKED_GOAL_SHA,
        "betting_authorized": False,
        "may_2026_read": False,
    }
    manifest.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("TUESDAY EVIDENCE PACKAGE COMPLETE")
    print("  betting authorized: FALSE")
    print("  May opened: FALSE")
    print("  READY_TO_OPEN_MAY records: 0")
    print(f"  report: {out} sha {binding['report']['sha256']}")
    print(f"  markdown: {markdown} sha {binding['markdown']['sha256']}")
    print(f"  binding: {manifest} sha {sha256(manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
