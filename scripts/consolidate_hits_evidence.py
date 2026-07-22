"""Hash-bound consolidation of the authoritative open-period Hits evidence.

This script does not fit a model, select a policy, inspect May, or authorize a
wager.  It only verifies the locked consolidation protocol and records what the
existing evidence does and does not support.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


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


def iter_key_values(value: Any, key: str) -> Iterable[Any]:
    if isinstance(value, dict):
        for current_key, current_value in value.items():
            if current_key == key:
                yield current_value
            yield from iter_key_values(current_value, key)
    elif isinstance(value, list):
        for item in value:
            yield from iter_key_values(item, key)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify_no_authorization(name: str, value: dict[str, Any]) -> None:
    flags = list(iter_key_values(value, "betting_authorized"))
    require(not any(flag is True for flag in flags), f"{name}: betting authorization found")


def verify_no_may_open(name: str, value: dict[str, Any]) -> None:
    for key in ("may_opened", "may_2026_holdout_read", "may_holdout_permitted_to_open_once"):
        flags = list(iter_key_values(value, key))
        require(not any(flag is True for flag in flags), f"{name}: May access/permitted flag found")


def load_locked_inputs(protocol: dict[str, Any]) -> dict[str, Any]:
    loaded: dict[str, Any] = {}
    for rel, expected in protocol["inputs_sha256"].items():
        path = ROOT / rel
        require(path.is_file(), f"missing locked input: {rel}")
        actual = sha256(path)
        require(actual == expected, f"locked input hash mismatch: {rel}: {actual} != {expected}")
        if path.suffix.lower() == ".json":
            loaded[rel] = load_json(path)
    return loaded


def read_market_ab(path: Path) -> dict[str, dict[str, float]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    require(len(rows) == 2, "June market A/B must contain exactly two arms")
    by_arm = {row["arm"]: row for row in rows}
    require(set(by_arm) == {"frozen", "candidate"}, "June market A/B arm set mismatch")
    require({row["market"] for row in rows} == {"hits"}, "June market A/B pooled markets")
    fields = ("n", "n_bets", "capture", "capture_ci_lo", "capture_ci_hi", "mean_clv_bets")
    return {
        arm: {field: float(row[field]) for field in fields}
        for arm, row in by_arm.items()
    }


def build_report(protocol_path: Path, protocol: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    june_manifest = inputs["data/analysis/market_hits_june_twosided/market_ab_manifest.json"]
    june_residuals = inputs["data/analysis/market_hits_june_twosided/market_ab_residuals.json"]
    fit = inputs["data/analysis/market_policy_hits_2026/policy_fit_time_safe_v6_release/fit_report.json"]
    pa = inputs["data/analysis/market_policy_hits_2026/pa_counterfactual_time_safe_v6_release/report.json"]
    per_pa = inputs["data/analysis/market_policy_hits_2026/per_pa_skill_time_safe_v6_release/report.json"]
    discrimination = inputs["data/analysis/market_policy_hits_2026/per_pa_discrimination_time_safe_v6_release/report.json"]
    hitter_contact = inputs["data/analysis/hitter_skill_contact_audit_v1/report.json"]
    pitcher_contact = inputs["data/analysis/pitcher_contact_audit_v1/report.json"]
    feasibility = inputs["data/analysis/hits_capture_feasibility_v1/report.json"]
    settlement = inputs["data/market/v3/settlement_agreement.json"]
    rules = inputs["data/analysis/market_policy_hits_2026/draftkings_baseball_rules_evidence_2026-07-14.json"]

    for name, value in inputs.items():
        verify_no_authorization(name, value)
        verify_no_may_open(name, value)

    require(protocol["market"] == "hits", "protocol market mismatch")
    require(protocol["chronology"]["may_2026_must_remain_sealed"] is True, "May is not sealed")
    locked_bar = float(protocol["required_invariants"]["locked_capture_lower_bound"])
    require(locked_bar == 0.10, "capture bar changed")

    require(june_manifest["policy"]["research_only"] is True, "June evidence is not research-only")
    require(june_manifest["months"] == ["2026-06"], "June evidence month mismatch")
    require(int(june_manifest["strict"]["artifact_rows"]) == 1772, "June strict row count mismatch")
    require(june_residuals["research_only"] is True, "June residuals not research-only")

    require(fit["verdict"] == "NO_POLICY_QUALIFIES", "failed Hits policy relabeled")
    require(fit["fit_gate_passed"] is False, "Hits policy gate unexpectedly passed")
    require(fit["may_holdout_permitted_to_open_once"] is False, "May permission found")
    confirmation = fit["confirmation"]
    paired = confirmation["candidate_minus_frozen_capture_interval"]
    require(float(paired["lower"]) > 0.0, "K/BB open-period paired signal is not strictly positive")
    require(float(confirmation["candidate"]["capture_interval"]["lower"]) < locked_bar,
            "capture lower bound unexpectedly clears locked bar")

    require(pa["verdict"] == "DIAGNOSTIC_ONLY", "PA diagnostic relabeled")
    require(per_pa["verdict"] == "DIAGNOSTIC_ONLY", "per-PA diagnostic relabeled")
    require(per_pa["global_per_pa_bias_detected"] is False, "global per-PA bias claim changed")
    require(discrimination["verdict"] == "DIAGNOSTIC_ONLY", "discrimination diagnostic relabeled")
    require(discrimination["all_four_paired_score_gates_passed"] is False,
            "per-PA discrimination failure relabeled")
    require(hitter_contact["production_candidate_supported"] is True,
            "supported hitter-contact next experiment missing")
    require(hitter_contact["production_change_installed"] is False,
            "hitter-contact production change already installed")
    require(pitcher_contact["production_candidate_supported"] is False,
            "failed pitcher-contact candidate relabeled")
    require(pitcher_contact["gate"]["all_pass"] is False,
            "pitcher-contact gate failure relabeled")
    require(feasibility["verdict"] == "PREDECLARED_LINEAR_FEASIBILITY_GATE_FAILED",
            "failed executability/CLV feasibility gate relabeled")
    require(feasibility["executability"]["verdict"] == "HISTORICAL_EXECUTABILITY_NOT_ESTABLISHED",
            "historical timestamps relabeled as executable")
    require(settlement["verdict"] == "UNRESOLVED", "vendor settlement proxy relabeled")
    require(int(settlement["counts"]["false_inclusion"]) > 0,
            "settlement proxy contradiction disappeared")
    require(rules["betting_authorized"] is False, "rules evidence authorized betting")

    june = read_market_ab(ROOT / "data/analysis/market_hits_june_twosided/market_ab.csv")
    june_delta = june["candidate"]["capture"] - june["frozen"]["capture"]
    require(june_delta > 0.0, "June K/BB candidate point capture is not positive")

    selector = hitter_contact["selector_fit"]
    require(int(selector["chosen_recency_window_games"]) == 30, "hitter-contact window changed")
    require(float(selector["fitted_prior_strength_bip"]) == 135.0, "hitter-contact shrinkage changed")

    candidate_fit = confirmation["candidate"]
    frozen_fit = confirmation["frozen"]
    fit_lower = float(candidate_fit["capture_interval"]["lower"])
    june_lower = float(june["candidate"]["capture_ci_lo"])

    return {
        "schema_version": 1,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": {
            "path": str(protocol_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(protocol_path),
        },
        "evaluator": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "market": "hits",
        "status": "CONSOLIDATED_RESEARCH_ONLY",
        "betting_authorized": False,
        "may_2026_holdout_read": False,
        "locked_capture_lower_bound": locked_bar,
        "evidence": {
            "kbb_candidate": {
                "classification": "SURVIVES_AS_RESEARCH_SIGNAL_NOT_PROMOTED",
                "march_april_open_confirmation": {
                    "candidate_capture": float(candidate_fit["point"]["capture"]),
                    "candidate_capture_95": [fit_lower, float(candidate_fit["capture_interval"]["upper"])],
                    "candidate_flat_stake_roi": float(candidate_fit["point"]["flat_stake_roi"]),
                    "candidate_flat_stake_roi_95": [
                        float(candidate_fit["flat_stake_roi_interval"]["lower"]),
                        float(candidate_fit["flat_stake_roi_interval"]["upper"]),
                    ],
                    "frozen_capture": float(frozen_fit["point"]["capture"]),
                    "paired_capture_difference_95": [float(paired["lower"]), float(paired["upper"])],
                    "shortfall_to_locked_bar_from_lower_bound": locked_bar - fit_lower,
                },
                "june_open_baseline": {
                    "candidate_capture": june["candidate"]["capture"],
                    "candidate_capture_95": [june_lower, june["candidate"]["capture_ci_hi"]],
                    "frozen_capture": june["frozen"]["capture"],
                    "candidate_minus_frozen_capture_point": june_delta,
                    "candidate_mean_clv_bets": june["candidate"]["mean_clv_bets"],
                    "shortfall_to_locked_bar_from_lower_bound": locked_bar - june_lower,
                },
                "reason": "The K/BB seam produced a replicated positive open-period capture signal, but absolute capture and payout uncertainty do not clear authorization gates.",
            },
            "policy_fit": {
                "classification": "REJECTED_NO_POLICY_QUALIFIES",
                "verdict": fit["verdict"],
                "reason": fit["fit_gate_reason"],
                "selected_threshold_is_not_authorized": float(fit["selected_min_expected_profit_per_unit"]),
                "freshness_proxy_status": fit["historical_freshness_proxy"],
            },
            "pa_and_simulation": {
                "classification": "REJECTED_AS_CURRENT_INTERVENTION",
                "verdict": pa["verdict"],
                "monte_carlo_compatibility": pa["monte_carlo_compatibility"],
                "reason": "Monte Carlo noise was compatible with the declared simulation size, while PA counterfactuals did not support a blanket production intervention.",
            },
            "global_and_slot_calibration": {
                "classification": "REJECTED_AS_CURRENT_INTERVENTION",
                "global_per_pa_bias_detected": False,
                "all_four_discrimination_gates_passed": False,
            },
            "pitcher_contact": {
                "classification": "REJECTED_PREDECLARED_GATE_FAILED",
                "gate": pitcher_contact["gate"],
                "decision": pitcher_contact["decision"],
            },
            "hitter_contact": {
                "classification": "NEXT_EXPERIMENT_ELIGIBLE_NOT_INSTALLED",
                "supported_levers": hitter_contact["supported_diagnostic_levers"],
                "recency_window_games": int(selector["chosen_recency_window_games"]),
                "prior_strength_bip": float(selector["fitted_prior_strength_bip"]),
                "decision": hitter_contact["decision"],
            },
            "executability": {
                "classification": "UNRESOLVED",
                "verdict": feasibility["executability"]["verdict"],
                "feasibility_gate": feasibility["verdict"],
                "reason": feasibility["executability"]["reason"],
            },
            "settlement": {
                "classification": "OFFICIAL_BASE_RULE_REQUIRED_VENDOR_PROXY_REJECTED",
                "vendor_proxy_verdict": settlement["verdict"],
                "vendor_false_inclusions": int(settlement["counts"]["false_inclusion"]),
                "vendor_false_exclusions": int(settlement["counts"]["false_exclusion"]),
                "rules_evidence": rules["verified_facts"],
            },
        },
        "single_highest_value_measured_next_experiment": {
            "id": "hits_point_in_time_hitter_contact_adapter_v1",
            "scope": "Build one point-in-time production adapter using the independently confirmed 30-game contact window and fitted BIP prior strength 135; compare it against the current K/BB candidate on open 2026 only.",
            "why_ranked_first": "It is the only uninstalled Hits input lever that passed an independent pre-2026 selector/confirmation gate. Pitcher contact, PA intervention, global calibration, and historical CLV feasibility all failed their existing gates.",
            "not_yet_authorized": True,
            "must_wait_for_cross_market_ranking": True,
        },
        "remaining_blockers": [
            "absolute capture lower bound remains below +0.10",
            "flat-stake ROI uncertainty does not establish positive payout performance",
            "historical price executability is not established",
            "quote freshness remains an unresolved timestamp proxy",
            "May remains sealed until a candidate and success contract are frozen",
            "forward shadow at executable prices has not been completed",
            "no immutable market-specific authorization record exists",
        ],
        "inputs_sha256": dict(protocol["inputs_sha256"]),
    }


def mutation_tests(protocol_path: Path) -> None:
    protocol = load_json(protocol_path)
    inputs = load_locked_inputs(protocol)

    bad_protocol = json.loads(json.dumps(protocol))
    first_path = next(iter(bad_protocol["inputs_sha256"]))
    bad_protocol["inputs_sha256"][first_path] = "0" * 64
    try:
        load_locked_inputs(bad_protocol)
    except ValueError:
        print("[OK] MUTATION tampered input hash")
    else:
        raise AssertionError("mutation did not fail: tampered input hash")

    def must_fail(label: str, mutate) -> None:
        changed = json.loads(json.dumps(inputs))
        mutate(changed)
        try:
            build_report(protocol_path, protocol, changed)
        except ValueError:
            print(f"[OK] MUTATION {label}")
            return
        raise AssertionError(f"mutation did not fail: {label}")

    must_fail("betting authorization", lambda d: d[
        "data/analysis/hits_capture_feasibility_v1/report.json"
    ].__setitem__("betting_authorized", True))
    must_fail("May opened", lambda d: d[
        "data/analysis/hits_capture_feasibility_v1/report.json"
    ].__setitem__("may_opened", True))
    must_fail("failed policy relabeled", lambda d: d[
        "data/analysis/market_policy_hits_2026/policy_fit_time_safe_v6_release/fit_report.json"
    ].__setitem__("fit_gate_passed", True))
    must_fail("historical executability invented", lambda d: d[
        "data/analysis/hits_capture_feasibility_v1/report.json"
    ]["executability"].__setitem__("verdict", "EXECUTABLE"))
    must_fail("pitcher contact failure relabeled", lambda d: d[
        "data/analysis/pitcher_contact_audit_v1/report.json"
    ].__setitem__("production_candidate_supported", True))
    must_fail("capture bar silently cleared", lambda d: d[
        "data/analysis/market_policy_hits_2026/policy_fit_time_safe_v6_release/fit_report.json"
    ]["confirmation"]["candidate"]["capture_interval"].__setitem__("lower", 0.11))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        default="data/analysis/hits_evidence_consolidation_v1/protocol.json",
    )
    parser.add_argument(
        "--out",
        default="data/analysis/hits_evidence_consolidation_v1/report.json",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    protocol_path = ROOT / args.protocol
    if args.self_test:
        mutation_tests(protocol_path)
        print("7/7")
        return 0

    protocol = load_json(protocol_path)
    inputs = load_locked_inputs(protocol)
    report = build_report(protocol_path, protocol, inputs)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("HITS EVIDENCE CONSOLIDATED — RESEARCH ONLY")
    print(f"  report: {out.relative_to(ROOT)}")
    print(f"  report_sha256: {sha256(out)}")
    print("  May opened: NO")
    print("  betting authorized: NO")
    print(f"  next experiment: {report['single_highest_value_measured_next_experiment']['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
