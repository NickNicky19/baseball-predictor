#!/usr/bin/env python3
"""Validate the immutable multi-market readiness selection."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import sha256  # noqa: E402


EXPECTED_MARKETS = {
    "hits_0_5", "hits_1_5", "home_runs_0_5_over", "total_bases",
    "rbi", "hits_runs_rbi", "pitcher_strikeouts",
}


def validate(payload: dict, *, evidence_root: Path) -> None:
    if payload.get("schema_version") != "multi-market-probability-readiness-v1":
        raise ValueError("multi-market readiness schema changed")
    if payload.get("status") != "READINESS_UPDATED_AFTER_HITS_1_5_REJECTION":
        raise ValueError("multi-market readiness status changed")
    if payload.get("betting_authorized") is not False or payload.get("production_unchanged") is not True:
        raise ValueError("multi-market readiness changed production or betting")
    if payload.get("may_2026_opened") is not False or payload.get("markets_are_not_pooled") is not True:
        raise ValueError("multi-market readiness opened May or pooled markets")
    local_names = {
        "cumulative_rejections", "open_probability_benchmark", "hr_open_gate",
        "hr_2025_confirmation", "hits_1_5_open_gate", "pitcher_k_readiness",
    }
    for name, record in (payload.get("evidence") or {}).items():
        base = ROOT if name in local_names else evidence_root
        path = base / str(record.get("path", ""))
        if not path.is_file() or sha256(path) != record.get("sha256"):
            raise ValueError(f"multi-market evidence changed: {name}")
    markets = payload.get("market_readiness") or {}
    if set(markets) != EXPECTED_MARKETS:
        raise ValueError("multi-market readiness set changed")
    if any(item.get("production_action") != "RETAIN" for item in markets.values()):
        raise ValueError("a multi-market production action changed")
    if markets["hits_1_5"].get("probability_candidate") != "REJECTED_ON_EXACT_MARKET_GRADEABLE_UNIVERSE":
        raise ValueError("Hits 1.5 rejection changed")
    if markets["home_runs_0_5_over"].get("per_pa_component") != "CONFIRMED_ON_2025":
        raise ValueError("HR confirmed component changed")
    pitcher = markets["pitcher_strikeouts"]
    if pitcher.get("historical_draftkings_two_sided_paths") != 1948:
        raise ValueError("pitcher-K denominator changed")
    action = payload.get("selected_next_action") or {}
    if action.get("market") != "pitcher_strikeouts" or action.get("success_condition", "").find("1,948") < 0:
        raise ValueError("selected next action changed")
    invariants = action.get("protected_invariants") or []
    if len(invariants) != 7 or "May remains sealed" not in invariants or "No betting authorization" not in invariants:
        raise ValueError("selected next-action invariants changed")


def mutation_tests(payload: dict, evidence_root: Path) -> None:
    mutations = []

    def add(name: str, mutate) -> None:
        changed = copy.deepcopy(payload)
        mutate(changed)
        caught = False
        try:
            validate(changed, evidence_root=evidence_root)
        except ValueError:
            caught = True
        mutations.append((name, caught))

    add("hash", lambda p: p["evidence"]["hits_1_5_open_gate"].update(sha256="0" * 64))
    add("betting", lambda p: p.update(betting_authorized=True))
    add("production", lambda p: p.update(production_unchanged=False))
    add("May", lambda p: p.update(may_2026_opened=True))
    add("pooling", lambda p: p.update(markets_are_not_pooled=False))
    add("market removed", lambda p: p["market_readiness"].pop("rbi"))
    add("Hits rejection", lambda p: p["market_readiness"]["hits_1_5"].update(probability_candidate="PASS"))
    add("production action", lambda p: p["market_readiness"]["total_bases"].update(production_action="INSTALL"))
    add("pitcher denominator", lambda p: p["market_readiness"]["pitcher_strikeouts"].update(historical_draftkings_two_sided_paths=1947))
    add("next market", lambda p: p["selected_next_action"].update(market="rbi"))
    add("invariant", lambda p: p["selected_next_action"]["protected_invariants"].remove("May remains sealed"))
    failed = [name for name, caught in mutations if not caught]
    if failed:
        raise ValueError(f"multi-market readiness mutations escaped: {failed}")
    print(f"MULTI_MARKET_READINESS_MUTATIONS_VALID {len(mutations)}/{len(mutations)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--mutations", action="store_true")
    args = parser.parse_args()
    payload = json.loads(args.report.resolve().read_text(encoding="utf-8"))
    evidence_root = args.evidence_root.resolve()
    validate(payload, evidence_root=evidence_root)
    if args.mutations:
        mutation_tests(payload, evidence_root)
    print("MULTI_MARKET_PROBABILITY_READINESS_VALID")
    print(f"report_sha256={sha256(args.report.resolve())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
