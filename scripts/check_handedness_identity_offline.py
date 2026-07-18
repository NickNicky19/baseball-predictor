#!/usr/bin/env python3
"""Offline/mutation harness for MLB handedness identity ingestion."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.mlb_api import HittingStatsSnapshot, MLBStatsAPI
from src.features.matchup_intelligence import MatchupIntelligence
from src.models.dataclasses import GameContext, HitterGameContext, PlayerIdentity


PASS = 0
FAIL = 0


def check(condition: bool, label: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


class FixtureAPI(MLBStatsAPI):
    def __init__(self) -> None:
        super().__init__(season=2026, max_retries=0)
        self.people = {
            1: {"fullName": "Left Hitter", "batSide": {"code": "L"}},
            2: {"fullName": "Switch Hitter", "batSide": {"code": "S"}},
            3: {"fullName": "Right Hitter", "batSide": {"code": "R"}},
            # Mutation fixture: the retired, incorrect field must not be read.
            4: {"fullName": "Legacy Field", "batHand": {"code": "L"}},
            99: {"fullName": "Left Pitcher", "pitchHand": {"code": "L"}},
        }

    def get_player_raw(self, player_id: int) -> dict:
        return self.people[player_id]


class SplitProvider:
    def __init__(self) -> None:
        self.split_calls = 0

    def get_platoon_splits(self, player_id: int):
        self.split_calls += 1
        split = HittingStatsSnapshot(obp=0.400, slg=0.600, pa=100)
        return split, split


def main() -> int:
    api = FixtureAPI()

    check(api.get_player_identity(1).bats == "L", "official batSide preserves L")
    check(api.get_player_identity(2).bats == "S", "official batSide preserves S")
    check(api.get_player_identity(3).bats == "R", "official batSide preserves R")
    check(
        api.get_player_identity(4).bats == "U",
        "mutation: retired batHand-only payload stays unknown instead of passing",
    )
    check(
        api.get_player_identity(1).throws == "U",
        "missing throwing hand stays neutral instead of defaulting right",
    )
    check(
        PlayerIdentity(10, "Unknown", "TEST").bats == "U"
        and PlayerIdentity(10, "Unknown", "TEST").throws == "U",
        "domain defaults preserve unknown handedness",
    )

    rows = api._hitters_from_order(
        order=[1],
        lineup_status="confirmed",
        game_pk=700001,
        game_date="2026-07-16",
        venue="Test Park",
        team_name="HOME",
        opp_name="AWAY",
        is_home=True,
        # Schedule probable lacks inline pitchHand. The player identity is the
        # authoritative fallback and must be resolved once for the whole order.
        opp_probable={"id": 99, "fullName": "Left Pitcher"},
        fail_on_error=True,
    )
    check(len(rows) == 1, "lineup row builds with a hand-less schedule probable")
    check(
        rows[0].opposing_pitcher_throws == "L",
        "missing schedule pitchHand hydrates the probable pitcher's identity",
    )

    split_provider = SplitProvider()
    neutral_hitter = HitterGameContext(
        player=api.get_player_identity(1),
        game=GameContext(700002, "2026-07-16", "Test Park", True, "AWAY"),
        lineup_slot=1,
        opposing_pitcher_id=100,
        opposing_pitcher_throws="U",
    )
    matchup = MatchupIntelligence(data_provider=split_provider)
    platoon_advantage, platoon_ops_z, platoon_slg_z, _ = matchup._platoon_context(
        neutral_hitter
    )
    check(
        split_provider.split_calls == 0,
        "mutation: unknown pitcher hand cannot silently select vs-RHP splits",
    )
    check(
        platoon_advantage == platoon_ops_z == platoon_slg_z == 0.0,
        "unknown handedness remains neutral through matchup intelligence",
    )

    print(f"\n{PASS}/{PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
