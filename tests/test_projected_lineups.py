"""Tests for projected lineup flag and PropProjection context fields."""

from __future__ import annotations

from src.models.dataclasses import (
    GameContext,
    HitterGameContext,
    LeagueBaselines,
    PlayerIdentity,
    StatcastProfile,
)
from src.prediction import DailyPredictor, PropEngine


class LineupModeMLBAPI:
    """Mock API that returns different hitters based on include_projected."""

    def __init__(self):
        self.season = 2026
        self.last_include_projected: bool | None = None

    def clear_cache(self, game_date: str) -> None:
        pass

    def get_hitters_for_date(self, game_date: str, include_projected: bool = False):
        self.last_include_projected = include_projected
        if not include_projected:
            return [
                HitterGameContext(
                    player=PlayerIdentity(mlb_id=1, name="Confirmed Player", team="NYY", bats="R"),
                    game=GameContext(
                        game_pk=1,
                        game_date=game_date,
                        venue="Yankee Stadium",
                        is_home=True,
                        opponent="BOS",
                        lineup_status="confirmed",
                    ),
                    lineup_slot=2,
                    opposing_pitcher_name="Confirmed Ace",
                )
            ]
        return [
            HitterGameContext(
                player=PlayerIdentity(mlb_id=1, name="Confirmed Player", team="NYY", bats="R"),
                game=GameContext(
                    game_pk=1,
                    game_date=game_date,
                    venue="Yankee Stadium",
                    is_home=True,
                    opponent="BOS",
                    lineup_status="confirmed",
                ),
                lineup_slot=2,
                opposing_pitcher_name="Confirmed Ace",
            ),
            HitterGameContext(
                player=PlayerIdentity(mlb_id=2, name="Projected Player", team="LAD", bats="L"),
                game=GameContext(
                    game_pk=2,
                    game_date=game_date,
                    venue="Dodger Stadium",
                    is_home=False,
                    opponent="SF",
                    lineup_status="projected",
                ),
                lineup_slot=4,
                opposing_pitcher_name="Projected Ace",
            ),
        ]

    def get_pitchers_for_date(self, game_date: str):
        return []

    def get_pitching_stats(self, player_id: int):
        from src.data.mlb_api import PitchingStatsSnapshot

        snap = PitchingStatsSnapshot(innings_pitched=30.0, strikeouts=40, k_per_9=10.0, bb_per_9=3.0)
        return snap, snap


class MockStatcastEngine:
    league = LeagueBaselines()

    def build_profiles_for_hitters(self, hitters, game_date=None, savant_csv_path=None):
        return {
            h.player.mlb_id: StatcastProfile(
                player_id=h.player.mlb_id,
                player_name=h.player.name,
                sample_pa=120,
                xwoba=0.340,
            )
            for h in hitters
        }


def _predictor(api: LineupModeMLBAPI) -> DailyPredictor:
    league = LeagueBaselines()
    engine = PropEngine(league_baselines=league, n_sims=200, config={"simulation": {"n_sims": 200}})
    return DailyPredictor(
        mlb_api=api,
        statcast_engine=MockStatcastEngine(),
        prop_engine=engine,
        config={
            "season": 2026,
            "lineup_intelligence": {},
            "lineup_slot_runs_rbi": {str(i): {"runs": 1.0, "rbi": 1.0} for i in range(1, 10)},
        },
    )


def test_confirmed_only_returns_single_hitter():
    api = LineupModeMLBAPI()
    predictor = _predictor(api)
    bundles = predictor.build_feature_bundles("2026-07-01", use_projected_lineups=False)
    assert api.last_include_projected is False
    assert len(bundles) == 1
    assert bundles[0].hitter.game.lineup_status == "confirmed"


def test_projected_mode_includes_extra_hitter():
    api = LineupModeMLBAPI()
    predictor = _predictor(api)
    bundles = predictor.build_feature_bundles("2026-07-01", use_projected_lineups=True)
    assert api.last_include_projected is True
    assert len(bundles) == 2
    statuses = {b.hitter.game.lineup_status for b in bundles}
    assert statuses == {"confirmed", "projected"}


def test_projected_lineup_lowers_confidence():
    api = LineupModeMLBAPI()
    predictor = _predictor(api)
    confirmed = predictor.predict(
        "2026-07-01",
        hitter_categories=("hrr",),
        include_pitchers=False,
        use_projected_lineups=False,
    )
    with_projected = predictor.predict(
        "2026-07-01",
        hitter_categories=("hrr",),
        include_pitchers=False,
        use_projected_lineups=True,
    )
    confirmed_conf = confirmed.hitter_projections[0].confidence
    projected_conf = next(
        p.confidence
        for p in with_projected.hitter_projections
        if p.player_name == "Projected Player"
    )
    assert projected_conf < confirmed_conf


def test_projection_includes_context_fields():
    api = LineupModeMLBAPI()
    predictor = _predictor(api)
    result = predictor.predict(
        "2026-07-01",
        hitter_categories=("hrr",),
        include_pitchers=False,
        use_projected_lineups=False,
    )
    proj = result.hitter_projections[0]
    assert proj.team == "NYY"
    assert proj.opponent == "BOS"
    assert proj.opposing_pitcher == "Confirmed Ace"
    assert proj.lineup_status == "confirmed"