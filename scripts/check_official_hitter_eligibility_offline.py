#!/usr/bin/env python3
"""Mutation checks for the official hitter eligibility bridge."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.official_hitter_eligibility import (  # noqa: E402
    base_pregame_player_prop_eligible,
    base_settlement_cell,
    canonical_game_dates,
    apply_base_hitter_rule,
    assert_official_date_agreement,
    game_eligibility_rows,
    official_hits_actuals,
    partition_official_role_resolution,
    validate_eligibility,
)


def expect_error(fn, contains: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert contains in str(exc), str(exc)
    else:
        raise AssertionError(f"expected error containing {contains!r}")


def main() -> int:
    hitters = {
        10: SimpleNamespace(pa=4, hits=2),  # starter
        11: SimpleNamespace(pa=3, hits=1),  # substitute with meaningful PA
        12: SimpleNamespace(pa=0, hits=0),  # starter removed before first PA
    }
    roles = {
        10: {"lineup_slot": 1, "starter_replaced_in_slot": True},
        11: {"lineup_slot": 1, "starter_replaced_in_slot": False},
        12: {"lineup_slot": 2, "starter_replaced_in_slot": False},
    }
    bridge = game_eligibility_rows(
        700001, "2026-06-01", hitters, {10, 12}, batting_roles=roles,
    )
    assert bridge.is_starter.tolist() == [True, False, True]
    assert bridge.starter_replaced_in_slot.tolist() == [True, False, False]
    assert bridge.official_pa.tolist() == [4.0, 3.0, 0.0]
    print("[PASS] bridge retains every official hitter and marks starters independently of PA")

    actuals = official_hits_actuals(bridge)
    assert set(actuals.category) == {"hits"}
    assert actuals.actual_value.tolist() == [2.0, 1.0, 0.0]
    print("[PASS] official hits targets derive from the same game-keyed bridge")

    assert base_pregame_player_prop_eligible(True, 1)
    assert not base_pregame_player_prop_eligible(False, 3)
    assert not base_pregame_player_prop_eligible(True, 0)
    print("[PASS] base rule requires a starter and at least one PA; no unsupported PA>=2 rule")

    mask = apply_base_hitter_rule(bridge)
    assert mask.tolist() == [True, False, False]
    print("[PASS] frame-level base rule retains starter PA=4, rejects substitute PA=3 and starter PA=0")

    role_fixture = pd.concat([
        bridge,
        pd.DataFrame([{
            "mlb_game_pk": 700002, "player_id": 99,
            "official_game_date": "2026-06-02", "is_starter": None,
            "official_pa": None, "official_hits": None,
        }]),
    ], ignore_index=True)
    resolved, unresolved = partition_official_role_resolution(role_fixture)
    assert len(resolved) == 3 and len(unresolved) == 1
    assert int(unresolved.iloc[0].player_id) == 99
    expect_error(lambda: apply_base_hitter_rule(role_fixture), "unresolved")
    print("[PASS] missing official role is an explicit unresolved exclusion, never a manufactured void")

    date_agreement = pd.DataFrame([{
        "official_game_date": "2026-06-01",
        "official_game_date_bridge": "2026-06-01",
    }])
    assert_official_date_agreement(date_agreement)
    bad_date = date_agreement.copy()
    bad_date.loc[0, "official_game_date_bridge"] = "2026-06-02"
    expect_error(lambda: assert_official_date_agreement(bad_date), "disagreement")
    print("[PASS] MUTATION crosswalk and official bridge date disagreement fails")

    assert base_settlement_cell(True, False, 3) == "FALSE_INCLUSION"
    assert base_settlement_cell(False, True, 3) == "FALSE_EXCLUSION"
    assert base_settlement_cell(True, True, 1) == "AGREE_scored"
    assert base_settlement_cell(True, None, None) == "UNRESOLVED_ROLE"
    print("[PASS] four-cell audit distinguishes base-rule disagreement from unknown role")

    fragments = pd.DataFrame([
        {"pk": 700001, "date": "2026-06-01"},
        {"pk": 700001, "date": "2026-06-01"},  # second hard fragment
    ])
    dates = canonical_game_dates(fragments, "pk", "date", "fixture")
    assert dates.to_dict("records") == [
        {"mlb_game_pk": 700001, "official_game_date": "2026-06-01"}
    ]
    print("[PASS] repeated hard fragments aggregate only their verified game/date pair")

    conflict = pd.concat([
        fragments,
        pd.DataFrame([{ "pk": 700001, "date": "2026-06-02" }]),
    ], ignore_index=True)
    expect_error(lambda: canonical_game_dates(conflict, "pk", "date", "fixture"),
                 "multiple official dates")
    print("[PASS] MUTATION one game_pk on two official dates fails; no fragment is chosen")

    expect_error(
        lambda: game_eligibility_rows(
            700001, "2026-06-01", hitters, {10, 99}, batting_roles=roles,
        ),
        "no official hitter record",
    )
    print("[PASS] MUTATION starter without an official hitter row fails")

    dup = pd.concat([bridge, bridge.iloc[[0]]], ignore_index=True)
    expect_error(lambda: validate_eligibility(dup), "duplicate")
    print("[PASS] MUTATION duplicate game/player identity fails")
    print("11/11 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
