#!/usr/bin/env python3
"""Mutation harness for official, side-specific HR-over grading."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_over_settlement import (  # noqa: E402
    GRADED_LOSS,
    GRADED_WIN,
    UNRESOLVED_GAME_COMPLETION,
    UNRESOLVED_OFFICIAL_ROLE,
    UNRESOLVED_ONE_PA_STARTER,
    VOID_EARLY_EXIT,
    VOID_GAME_LENGTH,
    VOID_NONSTARTER,
    VOID_NO_PA,
    attach_official_grades,
    build_settlement_bridge,
    hr_over_grade,
    official_positive_ev_metrics,
)
from src.evaluation.official_game_completion import (  # noqa: E402
    game_completion_row,
    validate_game_completion,
)
from src.evaluation.official_hitter_eligibility import ELIGIBILITY_COLUMNS  # noqa: E402


def expect_error(function, text: str) -> None:
    try:
        function()
    except ValueError as exc:
        assert text.lower() in str(exc).lower(), str(exc)
    else:
        raise AssertionError(f"expected ValueError containing {text!r}")


def fixtures() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    specs = [
        # game, player, starter, PA, replaced, HR, regular, expected
        (1, 101, True, 4, False, 1, True, GRADED_WIN),
        (2, 102, True, 4, False, 0, True, GRADED_LOSS),
        (3, 103, False, 2, False, 1, True, VOID_NONSTARTER),
        (4, 104, True, 0, False, 0, True, VOID_NO_PA),
        (5, 105, True, 1, True, 0, True, VOID_EARLY_EXIT),
        (6, 106, True, 1, True, 1, True, GRADED_WIN),
        (7, 107, True, 4, False, 0, False, VOID_GAME_LENGTH),
        (10, 110, True, 1, False, 0, True, UNRESOLVED_ONE_PA_STARTER),
    ]
    market_rows = []
    role_rows = []
    game_rows = []
    for game, player, starter, pa, replaced, hr, regular, _ in specs:
        date = "2026-03-25" if game <= 5 else "2026-06-01"
        market_rows.append({
            "mlb_game_pk": game, "player_id": player, "category": "home_runs",
            "line": 0.5, "official_game_date": date, "actual_value": hr,
            "settlement_present": True,
        })
        role_rows.append({
            "mlb_game_pk": game, "player_id": player,
            "official_game_date": date, "is_starter": starter,
            "official_lineup_slot": 1 if starter else pd.NA,
            "starter_replaced_in_slot": replaced,
            "official_pa": pa, "official_hits": hr,
        })
        game_rows.append({
            "mlb_game_pk": game, "official_game_date": date,
            "coded_game_state": "F", "scheduled_innings": 9,
            "final_inning": 9 if regular else 7,
            "regular_game_completed": regular,
        })
    # Explicit role-unresolved row.
    market_rows.append({
        "mlb_game_pk": 8, "player_id": 108, "category": "home_runs", "line": 0.5,
        "official_game_date": "2026-06-01", "actual_value": 0,
        "settlement_present": False,
    })
    game_rows.append({
        "mlb_game_pk": 8, "official_game_date": "2026-06-01",
        "coded_game_state": "F", "scheduled_innings": 9, "final_inning": 9,
        "regular_game_completed": True,
    })
    # Explicit game-completion-unresolved row.
    market_rows.append({
        "mlb_game_pk": 9, "player_id": 109, "category": "home_runs", "line": 0.5,
        "official_game_date": "2026-06-01", "actual_value": 0,
        "settlement_present": True,
    })
    role_rows.append({
        "mlb_game_pk": 9, "player_id": 109, "official_game_date": "2026-06-01",
        "is_starter": True, "official_lineup_slot": 1,
        "starter_replaced_in_slot": False, "official_pa": 4, "official_hits": 0,
    })
    return (
        pd.DataFrame(market_rows),
        pd.DataFrame(role_rows, columns=ELIGIBILITY_COLUMNS),
        pd.DataFrame(game_rows),
    )


def main() -> int:
    print("HR OVER SETTLEMENT - OFFLINE MUTATION HARNESS")
    assert hr_over_grade(is_starter=True, official_pa=4, starter_replaced_in_slot=False,
                         official_home_runs=1, regular_game_completed=True) == GRADED_WIN
    assert hr_over_grade(is_starter=True, official_pa=4, starter_replaced_in_slot=False,
                         official_home_runs=0, regular_game_completed=True) == GRADED_LOSS
    print("  [OK] ordinary starter HR/no-HR becomes graded win/loss")
    assert hr_over_grade(is_starter=False, official_pa=3, starter_replaced_in_slot=False,
                         official_home_runs=1, regular_game_completed=True) == VOID_NONSTARTER
    print("  [OK] substitute remains void even if he homers")
    assert hr_over_grade(is_starter=True, official_pa=0, starter_replaced_in_slot=False,
                         official_home_runs=0, regular_game_completed=True) == VOID_NO_PA
    print("  [OK] zero-PA starter is void")
    assert hr_over_grade(is_starter=True, official_pa=1, starter_replaced_in_slot=True,
                         official_home_runs=0, regular_game_completed=True) == VOID_EARLY_EXIT
    assert hr_over_grade(is_starter=True, official_pa=1, starter_replaced_in_slot=True,
                         official_home_runs=1, regular_game_completed=True) == GRADED_WIN
    print("  [OK] one-PA early exit voids a loss but preserves an already-won Over")
    assert hr_over_grade(is_starter=True, official_pa=4, starter_replaced_in_slot=False,
                         official_home_runs=0, regular_game_completed=False) == VOID_GAME_LENGTH
    print("  [OK] shortened-game unresolved loss is void")
    expect_error(lambda: hr_over_grade(
        is_starter=True, official_pa=0, starter_replaced_in_slot=False,
        official_home_runs=1, regular_game_completed=True,
    ), "cannot exceed official PA")
    print("  [OK] MUTATION impossible zero-PA HR hard-fails")

    market, roles, games = fixtures()
    bridge, funnel = build_settlement_bridge(
        market, roles, games, ["2026-03-25", "2026-06-01"]
    )
    status = bridge.set_index("mlb_game_pk").official_grade_status.to_dict()
    assert status[8] == UNRESOLVED_OFFICIAL_ROLE
    assert status[9] == UNRESOLVED_GAME_COMPLETION
    assert status[10] == UNRESOLVED_ONE_PA_STARTER
    assert funnel["input_rows"] == len(market)
    print("  [OK] unresolved role/game/one-PA evidence stays explicit; no row disappears")

    scored = market.copy()
    scored["arm"] = "candidate"
    scored["positive_ev"] = True
    scored["entry_decimal_odds"] = 5.0
    regraded = attach_official_grades(scored, bridge)
    metrics = official_positive_ev_metrics(regraded)
    assert regraded.loc[~regraded.official_gradeable, "official_realised_profit"].isna().all()
    assert metrics["official_gradeable_rows"] == int(bridge.official_gradeable.sum())
    assert metrics["void_or_unresolved_rows"] == int((~bridge.official_gradeable).sum())
    print("  [OK] MUTATION void/unresolved rows cannot enter ROI stake or profit")

    toggled = market.copy()
    toggled["settlement_present"] = ~toggled.settlement_present
    toggled_bridge, _ = build_settlement_bridge(
        toggled, roles, games, ["2026-03-25", "2026-06-01"]
    )
    assert bridge.official_grade_status.equals(toggled_bridge.official_grade_status)
    print("  [OK] MUTATION vendor settlement presence cannot change official grading")

    actual_mutation = market.copy()
    actual_mutation.loc[actual_mutation.mlb_game_pk.eq(2), "actual_value"] = 1
    actual_bridge, _ = build_settlement_bridge(
        actual_mutation, roles, games, ["2026-03-25", "2026-06-01"]
    )
    assert actual_bridge.set_index("mlb_game_pk").loc[2, "official_grade_status"] == GRADED_WIN
    print("  [OK] MUTATION official HR actual changes loss to win")

    game_mutation = games.copy()
    game_mutation.loc[game_mutation.mlb_game_pk.eq(2), ["final_inning", "regular_game_completed"]] = [7, False]
    game_bridge, _ = build_settlement_bridge(
        market, roles, game_mutation, ["2026-03-25", "2026-06-01"]
    )
    assert game_bridge.set_index("mlb_game_pk").loc[2, "official_grade_status"] == VOID_GAME_LENGTH
    print("  [OK] MUTATION official game length changes loss to void")

    expect_error(lambda: build_settlement_bridge(
        pd.concat([market, market.iloc[[0]]]), roles, games,
        ["2026-03-25", "2026-06-01"],
    ), "duplicated")
    print("  [OK] MUTATION duplicate final MARKET_KEY hard-fails")

    may = market.copy()
    may.loc[0, "official_game_date"] = "2026-05-01"
    expect_error(lambda: build_settlement_bridge(
        may, roles, games, ["2026-03-25", "2026-06-01", "2026-05-01"],
    ), "May")
    print("  [OK] MUTATION injecting May hard-fails")

    feed = {
        "gameData": {
            "datetime": {"officialDate": "2026-03-25"},
            "status": {"codedGameState": "F"},
        },
        "liveData": {"linescore": {"scheduledInnings": 9, "currentInning": 9}},
    }
    completion = game_completion_row(77, "2026-03-25", feed)
    assert bool(completion.iloc[0].regular_game_completed)
    print("  [OK] official final feed creates regular-game evidence")
    bad_label = completion.copy()
    bad_label["regular_game_completed"] = False
    expect_error(lambda: validate_game_completion(bad_label), "contradicts")
    print("  [OK] MUTATION derived regular-game label cannot contradict innings")
    nonfinal = {**feed, "gameData": {**feed["gameData"], "status": {"codedGameState": "D"}}}
    expect_error(lambda: game_completion_row(77, "2026-03-25", nonfinal), "not coded final")
    print("  [OK] MUTATION non-final game cannot enter official completion")
    expect_error(lambda: game_completion_row(77, "2026-03-26", feed), "differs")
    print("  [OK] MUTATION feed/declared official-date mismatch hard-fails")
    print("  17/17")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
