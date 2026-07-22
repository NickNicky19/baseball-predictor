"""Fail-closed, hash-bound Tuesday market readiness ranking."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def locked_inputs(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for rel, expected in protocol["inputs_sha256"].items():
        path = ROOT / rel
        require(path.is_file(), f"missing ranking input: {rel}")
        actual = sha256(path)
        require(actual == expected, f"ranking input hash mismatch: {rel}")
        values[rel] = load_json(path)
    return values


def build(protocol_path: Path, protocol: dict[str, Any], values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    hr_rejection = values[
        "data/analysis/hr_over_contract_v1/batted_ball_candidate_v1/REJECTED_OPEN_PERIOD.json"
    ]
    hits = values["data/analysis/hits_evidence_consolidation_v1/report.json"]
    other = values["data/analysis/other_market_contracts_v1/report.json"]
    navigation = values[
        "data/analysis/other_market_contracts_v1/official_navigation_check_2026-07-17.json"
    ]

    require(protocol["status"] == "LOCKED_BEFORE_MARKET_RANKING", "ranking protocol unlocked")
    require(protocol["selection_rule"]["maximum_additional_candidates"] == 1,
            "more than one candidate permitted")
    require(protocol["betting_authorized"] is False, "ranking protocol authorized betting")
    require(hr_rejection["status"] == "REJECTED_ON_LOCKED_OPEN_PERIOD_GATE",
            "HR rejection relabeled")
    require(hr_rejection["candidate_supported"] is False, "HR candidate relabeled supported")
    require(hr_rejection["may_opened"] is False, "HR opened May")
    require(hr_rejection["betting_authorized"] is False, "HR authorized betting")
    require(hits["status"] == "CONSOLIDATED_RESEARCH_ONLY", "Hits status changed")
    require(hits["may_2026_holdout_read"] is False, "Hits opened May")
    require(hits["betting_authorized"] is False, "Hits authorized betting")
    require(float(hits["locked_capture_lower_bound"]) == 0.10, "Hits bar changed")
    require(other["may_2026_read"] is False, "other-market audit opened May")
    require(other["betting_authorized"] is False, "other-market audit authorized betting")
    require(navigation["may_2026_read"] is False, "navigation check opened May")
    require(navigation["betting_authorized"] is False, "navigation check authorized betting")

    other_reports = other["market_reports"]
    for name in ("total_bases", "rbi", "hrr"):
        require(other_reports[name]["reconstruction_ready"] is False,
                f"{name} readiness failure disappeared")

    next_hits = hits["single_highest_value_measured_next_experiment"]
    require(next_hits["id"] == "hits_point_in_time_hitter_contact_adapter_v1",
            "Hits next experiment changed")
    require(next_hits["not_yet_authorized"] is True, "Hits experiment pre-authorized")
    require(hr_rejection["highest_value_measured_next_experiment"][
        "automatic_implementation_permitted"
    ] is False, "HR follow-up became automatic")

    matrix = {
        "hits": {
            "contract_completeness": "PARTIAL",
            "clean_historical_sample_size": "PASS",
            "official_grading_reliability": "PARTIAL",
            "executable_price_evidence": "FAIL",
            "model_coverage_and_data_health": "PASS",
            "open_period_calibration_and_discrimination": "PARTIAL_POSITIVE_SIGNAL",
            "economic_signal_and_uncertainty": "FAIL_AUTHORIZATION_BAR",
            "implementation_complexity_and_risk": "MODERATE_CANDIDATE_ONLY_ADAPTER",
            "specific_independently_supported_uninstalled_lever": True,
        },
        "home_runs_over_0_5": {
            "contract_completeness": "PASS_RESEARCH_CONTRACT",
            "clean_historical_sample_size": "PASS",
            "official_grading_reliability": "PASS_RESEARCH_BRIDGE",
            "executable_price_evidence": "FAIL",
            "model_coverage_and_data_health": "PASS",
            "open_period_calibration_and_discrimination": "FAILED_LATEST_CANDIDATE",
            "economic_signal_and_uncertainty": "FAIL_LATEST_CANDIDATE",
            "implementation_complexity_and_risk": "HIGH_REQUIRES_NEW_PRE2026_FITTED_MAPPING",
            "specific_independently_supported_uninstalled_lever": False,
        },
        "total_bases": {
            "contract_completeness": "FAIL_IDENTITY_SETTLEMENT_DUPLICATES_EXECUTABILITY",
            "clean_historical_sample_size": "PROMISING_UNCERTIFIED",
            "official_grading_reliability": "PARTIAL_EXACT_OUTCOME_SETTLEMENT_MISSING",
            "executable_price_evidence": "FAIL",
            "model_coverage_and_data_health": "PASS_CANDIDATE_PATH_ONLY",
            "open_period_calibration_and_discrimination": "NOT_MEASURED",
            "economic_signal_and_uncertainty": "NOT_MEASURED",
            "implementation_complexity_and_risk": "HIGH_NEW_MARKET_IDENTITY_UNIVERSE",
            "specific_independently_supported_uninstalled_lever": False,
        },
        "rbi": {
            "contract_completeness": "FAIL_OUTCOME_MODEL_IDENTITY_SETTLEMENT_EXECUTABILITY",
            "clean_historical_sample_size": "PROMISING_UNCERTIFIED",
            "official_grading_reliability": "FAIL_CODE_FALLS_THROUGH_TO_HITS",
            "executable_price_evidence": "FAIL",
            "model_coverage_and_data_health": "FAIL_NO_RBI_OUTPUT_PATH",
            "open_period_calibration_and_discrimination": "NOT_MEASURED",
            "economic_signal_and_uncertainty": "NOT_MEASURED",
            "implementation_complexity_and_risk": "HIGH_NEW_OUTCOME_AND_SIMULATION_PATH",
            "specific_independently_supported_uninstalled_lever": False,
        },
        "hits_runs_rbi": {
            "contract_completeness": "FAIL_HISTORICAL_PRODUCT_ABSENT_IN_LOCKED_EXPORT",
            "clean_historical_sample_size": "FAIL_ZERO_MATCHING_PRODUCT",
            "official_grading_reliability": "PARTIAL_EXACT_OUTCOME_SETTLEMENT_MISSING",
            "executable_price_evidence": "FAIL",
            "model_coverage_and_data_health": "PASS_INTERNAL_OUTPUT_ONLY",
            "open_period_calibration_and_discrimination": "NOT_MARKET_MEASURABLE",
            "economic_signal_and_uncertainty": "NOT_MARKET_MEASURABLE",
            "implementation_complexity_and_risk": "BLOCKED_ON_PRODUCT_DATA",
            "specific_independently_supported_uninstalled_lever": False,
        },
    }

    ranking = [
        {
            "rank": 1,
            "market": "hits",
            "decision": "ADVANCE_ONE_CANDIDATE_ON_OPEN_PERIOD_ONLY",
            "reason": "Only market with a specific uninstalled lever that passed an independent pre-2026 selector/confirmation gate and can be isolated against the current K/BB candidate.",
        },
        {
            "rank": 2,
            "market": "home_runs_over_0_5",
            "decision": "KEEP_FROZEN_RESEARCH_BASELINE",
            "reason": "Research contract is strong, but the latest candidate failed and its next mapping requires a new audit and predeclared fitted experiment before implementation.",
        },
        {
            "rank": 3,
            "market": "total_bases",
            "decision": "CONTRACT_WORK_ONLY",
            "reason": "Price sample and candidate model path are promising, but canonical identity, settlement, duplicate resolution, and executability are incomplete.",
        },
        {
            "rank": 4,
            "market": "rbi",
            "decision": "BLOCK_RECONSTRUCTION",
            "reason": "The current official-outcome function falls through to Hits and the simulator/projection stack has no RBI category.",
        },
        {
            "rank": 5,
            "market": "hits_runs_rbi",
            "decision": "BLOCK_RECONSTRUCTION",
            "reason": "No matching historical vendor product was observed in the locked no-May export, so market evaluation and executability cannot be established.",
        },
    ]
    selected = [{
        "market": "hits",
        "candidate_id": next_hits["id"],
        "scope": next_hits["scope"],
        "betting_authorized": False,
        "may_permitted": False,
    }]
    require(len(selected) <= 1, "more than one candidate selected")
    require(selected[0]["market"] == "hits", "ineligible market selected")
    require(matrix["hits"]["specific_independently_supported_uninstalled_lever"] is True,
            "selected Hits lever lacks independent support")

    return {
        "schema_version": 1,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "MARKETS_RANKED_RESEARCH_ONLY",
        "protocol": {
            "path": str(protocol_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(protocol_path),
        },
        "evaluator": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "ordered_dimensions": protocol["ordered_dimensions"],
        "weighted_score_used": False,
        "market_matrix": matrix,
        "ranking": ranking,
        "selected_additional_candidates": selected,
        "may_2026_read": False,
        "betting_authorized": False,
        "inputs_sha256": protocol["inputs_sha256"],
    }


def assert_report(report: dict[str, Any]) -> None:
    require(report["may_2026_read"] is False, "ranking opened May")
    require(report["betting_authorized"] is False, "ranking authorized betting")
    require(report["weighted_score_used"] is False, "weighted rescue used")
    selected = report["selected_additional_candidates"]
    require(len(selected) <= 1, "more than one candidate selected")
    if selected:
        market = selected[0]["market"]
        require(market == "hits", "market without supported lever selected")
        require(report["market_matrix"][market][
            "specific_independently_supported_uninstalled_lever"
        ] is True, "selected lever is unsupported")
    ranks = [row["rank"] for row in report["ranking"]]
    require(ranks == list(range(1, 6)), "ranking is incomplete or pooled")


def mutation_tests(protocol_path: Path) -> None:
    protocol = load_json(protocol_path)
    inputs = locked_inputs(protocol)

    bad_protocol = json.loads(json.dumps(protocol))
    first = next(iter(bad_protocol["inputs_sha256"]))
    bad_protocol["inputs_sha256"][first] = "0" * 64
    try:
        locked_inputs(bad_protocol)
    except ValueError:
        print("[OK] MUTATION tampered input hash")
    else:
        raise AssertionError("tampered hash mutation did not fail")

    changed = json.loads(json.dumps(inputs))
    changed[
        "data/analysis/hr_over_contract_v1/batted_ball_candidate_v1/REJECTED_OPEN_PERIOD.json"
    ]["candidate_supported"] = True
    try:
        build(protocol_path, protocol, changed)
    except ValueError:
        print("[OK] MUTATION HR rejection relabeled")
    else:
        raise AssertionError("HR rejection mutation did not fail")

    report = build(protocol_path, protocol, inputs)

    def must_fail(label: str, mutate) -> None:
        item = json.loads(json.dumps(report))
        mutate(item)
        try:
            assert_report(item)
        except ValueError:
            print(f"[OK] MUTATION {label}")
            return
        raise AssertionError(f"mutation did not fail: {label}")

    must_fail("two candidates", lambda x: x["selected_additional_candidates"].append({"market": "total_bases"}))
    must_fail("ineligible market selected", lambda x: x["selected_additional_candidates"][0].__setitem__("market", "rbi"))
    must_fail("May opened", lambda x: x.__setitem__("may_2026_read", True))
    must_fail("betting authorized", lambda x: x.__setitem__("betting_authorized", True))
    print("6/6")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", default="data/analysis/tuesday_market_ranking_v1/protocol.json")
    parser.add_argument("--out", default="data/analysis/tuesday_market_ranking_v1/report.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    protocol_path = ROOT / args.protocol
    if args.self_test:
        mutation_tests(protocol_path)
        return 0
    protocol = load_json(protocol_path)
    report = build(protocol_path, protocol, locked_inputs(protocol))
    assert_report(report)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("TUESDAY MARKET RANKING — RESEARCH ONLY")
    for row in report["ranking"]:
        print(f"  {row['rank']}. {row['market']}: {row['decision']}")
    print(f"  selected candidate: {report['selected_additional_candidates'][0]['candidate_id']}")
    print(f"  report_sha256: {sha256(out)}")
    print("  May opened: NO; betting authorized: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
