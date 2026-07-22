"""Integration tests for DailyPredictor with injectable mocks."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.mlb_api import HittingStatsSnapshot, PitchingStatsSnapshot
from src.data.odds import CompositeOddsProvider, FileOddsSettings, OddsSettings
from src.models.dataclasses import (
    GameContext,
    HitterGameContext,
    LeagueBaselines,
    MonteCarloResult,
    OddsLine,
    PitcherGameContext,
    PlayerIdentity,
    PropProjection,
    StatcastProfile,
)
from src.prediction import DailyPredictor, PropEngine
from src.simulation.game_simulator import GameSimulatorInput
from src.utils.errors import PredictionPipelineError

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MockMLBAPI:
    """Minimal MLB API mock for offline prediction tests."""

    def __init__(self):
        self.season = 2026

    def clear_cache(self, game_date: str) -> None:
        pass

    def get_hitters_for_date(
        self, game_date: str, include_projected: bool = False
    ) -> list[HitterGameContext]:
        return [
            HitterGameContext(
                player=PlayerIdentity(mlb_id=1, name="Test Player", team="NYY", bats="R"),
                game=GameContext(
                    game_pk=1,
                    game_date=game_date,
                    venue="Yankee Stadium",
                    is_home=True,
                    opponent="BOS",
                    lineup_status="confirmed",
                ),
                lineup_slot=3,
                opposing_pitcher_id=99,
                opposing_pitcher_name="Ace Pitcher",
                opposing_pitcher_throws="R",
            )
        ]

    def get_pitchers_for_date(self, game_date: str) -> list[PitcherGameContext]:
        return []

    def get_hitting_stats(self, player_id: int):
        season = HittingStatsSnapshot(obp=0.34, slg=0.44, pa=400, home_runs=18)
        recent = HittingStatsSnapshot(obp=0.35, slg=0.45, pa=60, home_runs=3)
        return season, recent

    def get_platoon_splits(self, player_id: int):
        return None, None

    def get_bvp_stats(self, hitter_id: int, pitcher_id: int):
        return None

    def get_pitching_stats(self, player_id: int):
        season = PitchingStatsSnapshot(
            innings_pitched=50.0,
            strikeouts=60,
            walks=15,
            k_per_9=10.8,
            bb_per_9=2.7,
            hr_per_9=1.0,
        )
        recent = PitchingStatsSnapshot(
            innings_pitched=20.0,
            strikeouts=28,
            walks=5,
            k_per_9=12.6,
            bb_per_9=2.25,
            hr_per_9=0.9,
        )
        return season, recent


class MockStatcastEngine:
    league = LeagueBaselines()

    def __init__(self):
        # DailyPredictor synchronizes calibrated league context through this
        # public boundary.  The mock must expose that same boundary.
        self.savant = SimpleNamespace(league=self.league)

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


def _fast_predictor() -> DailyPredictor:
    league = LeagueBaselines()
    engine = PropEngine(league_baselines=league, n_sims=200, config={"simulation": {"n_sims": 200}})
    return DailyPredictor(
        mlb_api=MockMLBAPI(),
        statcast_engine=MockStatcastEngine(),
        prop_engine=engine,
        config={"season": 2026, "pitcher_matchup": {"platoon_weight": 0.25}},
    )


def test_predict_without_corrections():
    predictor = _fast_predictor()
    result = predictor.predict(
        "2026-07-01",
        hitter_categories=("hrr",),
        include_pitchers=False,
        apply_corrections=False,
        include_edges=False,
    )
    assert result.game_date == date(2026, 7, 1)
    assert len(result.hitter_projections) == 1
    assert result.hitter_projections[0].category == "hrr"
    assert result.hitter_projections[0].input_health_flags
    assert result.value_plays == []
    assert result.prediction_provenance is None


def test_prediction_provenance_is_explicit_and_serialized():
    predictor = _fast_predictor()
    expected = {
        "schema_version": "daily-prediction-provenance-v1",
        "model_version": "test-model",
    }
    predictor._prediction_provenance = lambda **_: expected  # type: ignore[method-assign]
    result = predictor.predict(
        "2026-07-01",
        hitter_categories=("hrr",),
        include_pitchers=False,
        apply_corrections=False,
        include_edges=False,
        capture_prediction_provenance=True,
    )
    assert result.prediction_provenance == expected
    assert result.to_dict()["prediction_provenance"] == expected


def test_predict_with_corrections_no_state_fails_closed():
    predictor = _fast_predictor()
    with pytest.raises(PredictionPipelineError, match="correction state not found"):
        predictor.predict(
            "2026-07-01",
            hitter_categories=("hrr",),
            include_pitchers=False,
            apply_corrections=True,
            include_edges=False,
        )


def test_predict_with_edges():
    predictor = _fast_predictor()
    settings = OddsSettings(
        enabled=True,
        file=FileOddsSettings(csv_path=str(FIXTURES / "sample_odds.csv")),
    )
    predictor.odds_loader = CompositeOddsProvider(settings=settings, project_root=PROJECT_ROOT)

    mc = MonteCarloResult(
        n_sims=200,
        category="hrr",
        mean=2.2,
        median=2.0,
        p10=1.0,
        p90=3.5,
        # MC tails are P(actual >= integer count), not book-line keys.
        p_ge_threshold={2.0: 0.62},
    )
    original_project = predictor.prop_engine.project_hitter

    def patched_project(bundle, categories=None):
        projections = original_project(bundle, categories=categories)
        for p in projections:
            if p.category == "hrr":
                p.simulation = mc
                p.projected_value = 2.2
                p.confidence = 0.7
        return projections

    predictor.prop_engine.project_hitter = patched_project  # type: ignore[method-assign]

    result = predictor.predict(
        "2026-07-01",
        hitter_categories=("hrr",),
        include_pitchers=False,
        include_edges=True,
    )
    assert len(result.value_plays) >= 1
    assert result.value_plays[0].player_name == "Test Player"


def test_predict_with_inline_odds():
    predictor = _fast_predictor()
    odds = [
        OddsLine(
            player_name="Test Player",
            category="hrr",
            line=1.5,
            over_odds_american=-110,
            under_odds_american=-110,
        )
    ]
    mc = MonteCarloResult(
        n_sims=100,
        category="hrr",
        mean=2.5,
        median=2.4,
        p10=1.2,
        p90=3.8,
        # Over 1.5 requires at least two H+R+RBI.
        p_ge_threshold={2.0: 0.70},
    )

    def patched_project(bundle, categories=None):
        return [
            PropProjection(
                player_id=1,
                player_name="Test Player",
                category="hrr",
                game_date="2026-07-01",
                projected_value=2.5,
                confidence=0.75,
                simulation=mc,
            )
        ]

    predictor.prop_engine.project_hitter = patched_project  # type: ignore[method-assign]
    result = predictor.predict(
        "2026-07-01",
        hitter_categories=("hrr",),
        include_edges=True,
        odds_lines=odds,
        min_edge_pct=1.0,
    )
    assert len(result.value_plays) == 1
    assert result.value_plays[0].edge_pct != 0


def test_daily_prediction_to_dict():
    predictor = _fast_predictor()
    result = predictor.predict(
        "2026-07-01",
        hitter_categories=("hrr",),
        include_pitchers=False,
        include_edges=False,
    )
    data = result.to_dict()
    assert data["game_date"] == "2026-07-01"
    assert len(data["hitter_projections"]) == 1
