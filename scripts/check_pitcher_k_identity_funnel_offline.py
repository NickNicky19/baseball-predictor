#!/usr/bin/env python3
"""Mutation checks for the outcome-blind pitcher-K identity funnel."""
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

import src.evaluation.pitcher_k_identity_funnel as funnel  # noqa: E402


def expect_failure(label: str, operation) -> None:
    try:
        operation()
    except (AssertionError, FileNotFoundError, TypeError, ValueError):
        print(f"  [OK] {label}")
        return
    raise AssertionError(f"mutation did not fail: {label}")


def protocol_mutations(protocol: dict) -> None:
    cases = []

    changed = copy.deepcopy(protocol)
    changed["inputs"]["market_parquets"][0]["sha256"] = "0" * 64
    cases.append(("input hash drift", changed))

    changed = copy.deepcopy(protocol)
    changed["chronology"]["months"].append("2026-05")
    cases.append(("May injection", changed))

    changed = copy.deepcopy(protocol)
    changed["market_contract"]["complete_path_count"] -= 1
    cases.append(("denominator drift", changed))

    changed = copy.deepcopy(protocol)
    changed["identity_contract"]["fuzzy_matching"] = True
    cases.append(("fuzzy identity", changed))

    changed = copy.deepcopy(protocol)
    changed["identity_contract"]["vote_threshold"] = 8
    cases.append(("threshold identity", changed))

    changed = copy.deepcopy(protocol)
    changed["market_contract"]["freshness_filter"] = 90
    cases.append(("freshness assumption", changed))

    changed = copy.deepcopy(protocol)
    changed["betting_authorized"] = True
    cases.append(("betting authorization", changed))

    changed = copy.deepcopy(protocol)
    changed["production_unchanged"] = False
    cases.append(("production mutation", changed))

    for label, payload in cases:
        expect_failure(label, lambda payload=payload: funnel.validate_protocol_structure(payload))


class IdentityOnlyPayload(dict):
    def get(self, key, default=None):
        if key == "liveData":
            raise AssertionError("official outcome branch was accessed")
        return super().get(key, default)


def identity_branch_check() -> None:
    record = {"mlb_game_pk": 777001, "game_date": "2026-04-01"}
    payload = IdentityOnlyPayload({
        "gamePk": 777001,
        "gameData": {"datetime": {"officialDate": "2026-04-01"}, "players": {}},
        "liveData": object(),
    })
    wrapper = {
        "url": "https://statsapi.mlb.com/api/v1.1/game/777001/feed/live",
        "params": {},
        "data": payload,
    }
    returned, game_data = funnel.unwrap_official_identity_feed(
        wrapper, record, source=Path("synthetic.json")
    )
    assert returned is payload and game_data is payload["gameData"]
    source = inspect.getsource(funnel.unwrap_official_identity_feed)
    assert "liveData" not in source
    print("  [OK] official outcome branch inaccessible")


class QueryCaptured(Exception):
    def __init__(self, query: str):
        self.query = query


def query_boundary_check(protocol: dict) -> None:
    original = funnel.duckdb.sql

    def capture(query: str):
        raise QueryCaptured(query)

    funnel.duckdb.sql = capture
    try:
        for label, operation in (
            ("complete paths", lambda: funnel.read_complete_paths([], protocol)),
            ("fragment names", lambda: funnel.read_fragment_names([])),
        ):
            try:
                operation()
            except QueryCaptured as captured:
                lowered = captured.query.lower()
                assert " result" not in lowered and " won" not in lowered
                assert "2026-05" not in lowered
                print(f"  [OK] {label} query excludes forbidden outcome fields")
            else:
                raise AssertionError(f"query was not captured: {label}")
    finally:
        funnel.duckdb.sql = original


def duplicate_boundary_check() -> None:
    start = pd.Timestamp("2026-04-01T23:10:00")
    common = {
        "start_time": start,
        "line": 5.5,
        "entry_over_time": start - pd.Timedelta(hours=5),
        "entry_under_time": start - pd.Timedelta(hours=5),
        "close_over_time": start - pd.Timedelta(minutes=5),
        "close_under_time": start - pd.Timedelta(minutes=5),
        "entry_over_price_count": 1,
        "entry_under_price_count": 1,
        "close_over_price_count": 1,
        "close_under_price_count": 1,
        "entry_over_odds": 1.91,
        "entry_under_odds": 1.91,
        "close_over_odds": 1.91,
        "close_under_odds": 1.91,
    }
    paths = pd.DataFrame([
        {**common, "vendor_game_id": "g-a", "player": "Pitcher One"},
        {**common, "vendor_game_id": "g-b", "player": "Pitcher One"},
        {**common, "vendor_game_id": "g-c", "player": "Pitcher Two"},
    ])
    fragments = pd.DataFrame([
        {"vendor_game_id": "g-a", "start_time": start, "slate_date": "2026-04-01",
         "fragment_state": "mapped", "mlb_game_pk": 100, "vendor_name_count": 1,
         "matched_name_count": 1, "unmatched_name_count": 0, "matched_game_pks": "[100]"},
        {"vendor_game_id": "g-b", "start_time": start, "slate_date": "2026-04-01",
         "fragment_state": "mapped", "mlb_game_pk": 100, "vendor_name_count": 1,
         "matched_name_count": 1, "unmatched_name_count": 0, "matched_game_pks": "[100]"},
        {"vendor_game_id": "g-c", "start_time": start, "slate_date": "2026-04-01",
         "fragment_state": "mapped", "mlb_game_pk": 101, "vendor_name_count": 1,
         "matched_name_count": 1, "unmatched_name_count": 0, "matched_game_pks": "[101]"},
    ])
    fragments["mlb_game_pk"] = pd.array(fragments["mlb_game_pk"], dtype="Int64")
    mapped = funnel.assign_terminal_states(
        paths,
        fragments,
        {
            100: {"pitcher one": {200}},
            101: {"pitcher two": {201}},
        },
    )
    assert mapped.terminal_state.tolist().count("conflicting_duplicate") == 2
    assert mapped.terminal_state.tolist().count("mapped_unique") == 1
    changed = mapped.copy()
    changed.loc[changed.terminal_state.eq("conflicting_duplicate").idxmax(), "terminal_state"] = "mapped_unique"
    expect_failure(
        "partial duplicate retention",
        lambda: funnel.validate_terminal_ledger(changed, expected_count=3),
    )
    expect_failure(
        "silent path drop",
        lambda: funnel.validate_terminal_ledger(mapped.iloc[:-1], expected_count=3),
    )


def main() -> int:
    protocol_path = ROOT / "config/pitcher_k_identity_funnel_protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    funnel.validate_protocol_structure(protocol)
    print("  [OK] locked protocol")
    protocol_mutations(protocol)
    identity_branch_check()
    query_boundary_check(protocol)
    duplicate_boundary_check()
    print("PITCHER-K IDENTITY FUNNEL OFFLINE CHECKS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
