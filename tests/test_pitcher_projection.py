"""Regression tests for pitcher strikeout projection.

Guards the K%/BB% per-9 -> per-PA unit conversion. The pre-fix code computed
(k9 / 9) * 4.2 * 100 (multiplying by PA-per-inning instead of dividing),
which produced K% of 280-550%, pinned every pitcher at the k_max clamp, and
projected the identical strikeout total for a 5 K/9 and a 13 K/9 pitcher.
"""

from src.data.mlb_api import PitchingStatsSnapshot
from src.models.dataclasses import GameContext, PitcherGameContext, PlayerIdentity
from src.prediction.prop_engine import PA_PER_INNING, PropEngine, rate_per_9_to_pct


def _pitcher(name: str) -> PitcherGameContext:
    return PitcherGameContext(
        player=PlayerIdentity(mlb_id=1, name=name, team="X"),
        game=GameContext(
            game_pk=1, game_date="2026-07-01", venue="Test Park",
            is_home=True, opponent="Z",
        ),
        expected_innings=5.5,
    )


def _snapshots(k9: float, bb9: float = 3.0):
    season = PitchingStatsSnapshot(
        innings_pitched=90, k_per_9=k9, bb_per_9=bb9, games_started=15
    )
    recent = PitchingStatsSnapshot(
        innings_pitched=20, k_per_9=k9, bb_per_9=bb9, games_started=4
    )
    return season, recent


def test_rate_per_9_to_pct_scale():
    """8.8 K/9 is about a 23% K rate, not 411%."""
    pct = rate_per_9_to_pct(8.8)
    assert 20.0 <= pct <= 27.0, f"8.8 K/9 -> {pct:.1f}% (expected ~23%)"
    assert rate_per_9_to_pct(3.0) < 12.0  # BB/9 sanity


def test_pitcher_k_projection_discriminates_by_skill():
    engine = PropEngine(config={"simulation": {"n_sims": 500}})

    elite = engine.project_pitcher_strikeouts(_pitcher("Elite"), *_snapshots(12.5))
    average = engine.project_pitcher_strikeouts(_pitcher("Average"), *_snapshots(8.8))
    soft = engine.project_pitcher_strikeouts(_pitcher("Soft"), *_snapshots(5.0))

    assert elite.projected_value > average.projected_value > soft.projected_value, (
        f"projections must order by skill: elite={elite.projected_value}, "
        f"avg={average.projected_value}, soft={soft.projected_value}"
    )
    # A league-average starter over ~5.5 IP should land near 4-6 K.
    assert 3.5 <= average.projected_value <= 6.5, (
        f"league-average pitcher projected {average.projected_value} K over 5.5 IP"
    )


def test_pitcher_projection_reasonable_bounds():
    engine = PropEngine(config={"simulation": {"n_sims": 500}})
    elite = engine.project_pitcher_strikeouts(_pitcher("Elite"), *_snapshots(13.0))
    # Even an elite starter over 5.5 IP faces ~23 batters; K projection
    # should be high single digits, not double digits every night.
    assert elite.projected_value <= 5.5 * PA_PER_INNING * 0.45
