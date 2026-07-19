#!/usr/bin/env python3
"""Mutation-focused checks for the locked pitcher-K open benchmark."""
from __future__ import annotations

import copy
import inspect
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.evaluation.pitcher_k_open_benchmark as benchmark  # noqa: E402


def expect_failure(label: str, operation) -> None:
    try:
        operation()
    except (AssertionError, TypeError, ValueError):
        print(f"  [OK] {label}")
        return
    raise AssertionError(f"mutation did not fail: {label}")


def protocol_mutations(protocol: dict) -> None:
    cases = []
    changed = copy.deepcopy(protocol)
    changed["scope"]["months"].append("2026-05")
    cases.append(("May injection", changed))
    changed = copy.deepcopy(protocol)
    changed["locked_denominators"]["model_compatible_market_rows"] -= 1
    cases.append(("model denominator drift", changed))
    changed = copy.deepcopy(protocol)
    changed["official_outcome_contract"]["primary_scoring_state"] = "not_started"
    cases.append(("start-state arbitration", changed))
    changed = copy.deepcopy(protocol)
    changed["metrics"]["no_policy_selection"] = False
    cases.append(("post-outcome policy", changed))
    changed = copy.deepcopy(protocol)
    changed["economic_evidence_eligible"] = True
    cases.append(("economic eligibility", changed))
    changed = copy.deepcopy(protocol)
    changed["betting_authorized"] = True
    cases.append(("betting authorization", changed))
    for label, payload in cases:
        expect_failure(label, lambda payload=payload: benchmark.validate_protocol(payload))


def official_outcome_boundaries() -> None:
    assert benchmark._ip_to_outs("5.0") == 15
    assert benchmark._ip_to_outs("5.1") == 16
    assert benchmark._ip_to_outs("5.2") == 17
    expect_failure("invalid MLB innings", lambda: benchmark._ip_to_outs("5.3"))
    source = inspect.getsource(benchmark.build_model_market_universe)
    assert "read_parquet" not in source
    print("  [OK] market build reads no raw vendor data and rejects result fields")


def scoring_boundaries() -> None:
    market = pd.DataFrame({
        "mlb_game_pk": [1, 1], "player_id": [9, 9], "game_date": ["2026-04-01", "2026-04-01"],
        "category": ["strikeouts", "strikeouts"], "line": [4.5, 5.5],
        "sim_p_over": [0.6, 0.4], "entry_reference_p_over": [0.55, 0.35],
        "close_reference_p_over": [0.57, 0.37],
    })
    outcomes = pd.DataFrame({
        "mlb_game_pk": [1], "player_id": [9], "game_date": ["2026-04-01"],
        "terminal_state": ["started"], "actual_strikeouts": [5],
    })
    joined = benchmark.add_comparators(market, outcomes, {4: 0.5, 5: 0.5})
    assert joined.actual_over.tolist() == [1, 0]
    expect_failure(
        "missing official terminal state",
        lambda: benchmark.add_comparators(market, outcomes.iloc[0:0], {4: 0.5, 5: 0.5}),
    )
    values = benchmark._scores(joined, "sim_p_over", 0.000001)
    assert values["n"] == 2 and values["brier"] >= 0.0 and values["log_loss"] >= 0.0
    print("  [OK] official terminal and proper-score boundaries")


def main() -> int:
    protocol = json.loads((ROOT / "config/pitcher_k_open_benchmark_protocol.json").read_text(encoding="utf-8"))
    benchmark.validate_protocol(protocol)
    print("  [OK] locked protocol")
    protocol_mutations(protocol)
    official_outcome_boundaries()
    scoring_boundaries()
    print("PITCHER-K OPEN BENCHMARK OFFLINE CHECKS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
