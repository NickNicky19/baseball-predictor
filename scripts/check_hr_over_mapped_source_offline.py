#!/usr/bin/env python3
"""Mutation-focused offline checks for the hard-keyed HR-over source."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_over_mapped_source import (  # noqa: E402
    assert_duplicate_rows_preserved,
    complete_price_paths,
    load_json,
    map_price_paths,
    month_funnel,
    normalize_player_name,
    resolve_input,
    sha256,
    validate_crosswalk_frames,
    validate_protocol,
    validate_quote_source,
)


def expect_failure(label: str, operation) -> None:
    try:
        operation()
    except (AssertionError, FileNotFoundError, ValueError):
        print(f"  [OK] {label}")
        return
    raise AssertionError(f"mutation escaped: {label}")


def quote_fixture() -> pd.DataFrame:
    rows = []
    for game_id, start_time, player in [
        ("m~a", "2026-03-28T20:00:00Z", "José Ramírez Jr."),
        ("m~b", "2026-03-28T20:01:00Z", "Jose Ramirez"),
        ("m~c", "2026-03-28T20:02:00Z", "Missing Person"),
    ]:
        horizon = pd.Timestamp(start_time) - pd.Timedelta(hours=4)
        rows.append({
            "sportsbook": "draftkings",
            "vendor_market": "player home runs",
            "selection_side": "over",
            "market_date": "2026-03-28",
            "market_month": "2026-03",
            "vendor_game_id": game_id,
            "start_time": start_time,
            "player": player,
            "line": 0.5,
            "horizon": horizon,
            "settlement_present": game_id != "m~c",
            "status": "price_path_observed" if game_id != "m~c" else "vendor_settlement_absent",
            "over_observations": 4,
            "entry_quote_time": horizon - pd.Timedelta(minutes=30),
            "entry_decimal_odds": 8.0,
            "close_quote_time": pd.Timestamp(start_time) - pd.Timedelta(minutes=5),
            "close_decimal_odds": 7.5,
            "entry_age_min": 30.0,
            "non_over_rows": 0,
            "entry_raw_break_even_probability": 0.125,
            "close_raw_break_even_probability": 1.0 / 7.5,
            "raw_implied_probability_movement": 1.0 / 7.5 - 0.125,
        })
    return pd.DataFrame(rows)


def crosswalk_fixture() -> tuple[pd.DataFrame, pd.DataFrame]:
    players = pd.DataFrame([
        {
            "vendor_game_id": "m~a",
            "start_time": "2026-03-28T20:00:00Z",
            "player_key": "jose ramirez",
            "mlb_game_pk": 1,
            "player_id": 10,
        },
        {
            "vendor_game_id": "m~b",
            "start_time": "2026-03-28T20:01:00Z",
            "player_key": "jose ramirez",
            "mlb_game_pk": 1,
            "player_id": 10,
        },
    ])
    fragments = pd.DataFrame([
        {
            "vendor_game_id": game_id,
            "start_time": start_time,
            "slate_date": "2026-03-28",
            "outcome": "mapped",
            "mlb_game_pk": 1,
            "official_date": "2026-03-28",
        }
        for game_id, start_time in [
            ("m~a", "2026-03-28T20:00:00Z"),
            ("m~b", "2026-03-28T20:01:00Z"),
            ("m~c", "2026-03-28T20:02:00Z"),
        ]
    ])
    return players, fragments


def main() -> int:
    checks = 0
    print("HR-OVER MAPPED SOURCE — OFFLINE MUTATION HARNESS")

    protocol_path = ROOT / "data/analysis/hr_over_contract_v1/mapped_source_protocol_v2.json"
    protocol = load_json(protocol_path)
    validate_protocol(protocol)
    print("  [OK] locked research-only protocol validates")
    checks += 1

    assert normalize_player_name("José Ramírez Jr.") == "jose ramirez"
    assert normalize_player_name("O'Neil-Cruz III") == "oneilcruz"
    print("  [OK] crosswalk-equivalent accents, punctuation, and suffix normalization")
    checks += 1

    raw = quote_fixture()
    quotes = validate_quote_source(raw, ["2026-03"])
    complete = complete_price_paths(quotes)
    print("  [OK] exact DK / HR / over / 0.5 fixture passes")
    checks += 1

    for column, value, label in [
        ("sportsbook", "another_book", "MUTATION another book fails"),
        ("selection_side", "under", "MUTATION under side fails"),
        ("line", 1.5, "MUTATION HR 1.5 fails"),
    ]:
        mutated = raw.copy()
        mutated.loc[0, column] = value
        expect_failure(label, lambda m=mutated: validate_quote_source(m, ["2026-03"]))
        checks += 1

    may = raw.copy()
    may["market_month"] = "2026-05"
    may["market_date"] = "2026-05-01"
    expect_failure(
        "MUTATION May injection fails",
        lambda: validate_quote_source(may, ["2026-03"]),
    )
    checks += 1

    numeric_truth = raw.assign(result=1.0)
    expect_failure(
        "MUTATION numeric vendor result fails",
        lambda: validate_quote_source(numeric_truth, ["2026-03"]),
    )
    checks += 1

    duplicate_raw = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)
    expect_failure(
        "MUTATION duplicate raw source key fails",
        lambda: validate_quote_source(duplicate_raw, ["2026-03"]),
    )
    checks += 1

    raw_players, raw_fragments = crosswalk_fixture()
    players, fragments = validate_crosswalk_frames(
        raw_players, raw_fragments, ["2026-03"], report=None
    )
    duplicate_consumer = pd.concat([raw_players, raw_players.iloc[[0]]], ignore_index=True)
    expect_failure(
        "MUTATION duplicate crosswalk consumer key fails",
        lambda: validate_crosswalk_frames(
            duplicate_consumer, raw_fragments, ["2026-03"], report=None
        ),
    )
    checks += 1

    mapped = map_price_paths(complete, players, fragments)
    assert_duplicate_rows_preserved(complete, mapped)
    assert mapped.mapping_status.value_counts().to_dict() == {
        "hard_mapped": 2,
        "player_key_unmapped": 1,
    }
    print("  [OK] removed player mapping is counted as player_key_unmapped")
    checks += 1

    duplicate_rows = mapped[mapped.duplicate_market_key]
    assert len(duplicate_rows) == 2
    assert duplicate_rows.duplicate_market_key_group_size.eq(2).all()
    assert duplicate_rows.vendor_game_id.tolist() == ["m~a", "m~b"]
    print("  [OK] duplicate final MARKET_KEY preserves and names BOTH fragments")
    checks += 1

    expect_failure(
        "MUTATION dropping one duplicate row fails preservation guard",
        lambda: assert_duplicate_rows_preserved(complete, mapped.iloc[1:].copy()),
    )
    checks += 1

    funnel = month_funnel(mapped)[0]
    assert funnel["complete_price_paths"] == 3
    assert funnel["hard_mapped"] == 2
    assert funnel["player_key_unmapped"] == 1
    assert funnel["duplicate_market_keys"] == 1
    print("  [OK] exhaustive funnel distinguishes coverage from duplicate ambiguity")
    checks += 1

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "source.txt"
        path.write_text("original", encoding="utf-8")
        spec = {"path": str(path), "sha256": sha256(path)}
        assert resolve_input(ROOT, spec) == path
        path.write_text("tampered", encoding="utf-8")
        expect_failure(
            "MUTATION tampered input hash fails",
            lambda: resolve_input(ROOT, spec),
        )
    checks += 1

    protocol_mutation = json.loads(json.dumps(protocol))
    protocol_mutation["betting_authorized"] = True
    expect_failure(
        "MUTATION identity artifact cannot authorize betting",
        lambda: validate_protocol(protocol_mutation),
    )
    checks += 1

    print(f"\n  {checks}/{checks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
