from __future__ import annotations

import math

import pytest

from src.models.dataclasses import (
    GameContext,
    HitterGameContext,
    LeagueBaselines,
    MatchupContext,
    ParkFactors,
    PlayerFeatureBundle,
    PlayerIdentity,
    StatcastProfile,
    UmpireContext,
    WeatherContext,
)
from src.prediction.prop_engine import PropEngine
from src.evaluation.output_safeguards import SafeguardReport
from src.simulation.input_contract import (
    SimulationInputContractError,
    effective_pa_context,
)
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig
from src.simulation.probability_engine import ProbabilityEngine


def _bundle(**matchup_updates) -> PlayerFeatureBundle:
    game = GameContext(1, "2026-07-22", "Test Park", True, "AWY", "confirmed")
    matchup = MatchupContext(
        recent_form_multiplier=matchup_updates.get("recent_form_multiplier", 1.0),
        bvp_ops_factor=matchup_updates.get("bvp_ops_factor", 1.0),
        bvp_hr_factor=matchup_updates.get("bvp_hr_factor", 1.0),
        platoon_advantage=matchup_updates.get("platoon_advantage", 0.0),
    )
    return PlayerFeatureBundle(
        hitter=HitterGameContext(
            PlayerIdentity(7, "Parity Hitter", "HME"), game, lineup_slot=1
        ),
        statcast=StatcastProfile(7, "Parity Hitter"),
        park=ParkFactors("Test Park", hits_factor=1.03, hr_factor=1.07),
        weather=WeatherContext("Test Park", "2026-07-22"),
        matchup=matchup,
        umpire=UmpireContext(name="Test Umpire", k_bias=0.4),
        metadata={"weather_hr_factor": 1.05},
    )


class _SpyPASimulator:
    def __init__(self) -> None:
        self.kwargs = None

    def expected_outcome_probabilities(self, **kwargs):
        self.kwargs = kwargs
        return {
            "strikeout": 0.20,
            "walk": 0.08,
            "home_run": 0.03,
            "single": 0.15,
            "double": 0.05,
            "triple": 0.01,
            "out_on_bip": 0.48,
        }


@pytest.mark.parametrize(
    "updates",
    [
        {"recent_form_multiplier": 9.0},
        {"bvp_ops_factor": -4.0},
        {"bvp_hr_factor": 8.0},
    ],
)
def test_mutation_out_of_bound_context_fails_instead_of_clipping(updates) -> None:
    bundle = _bundle(**updates)
    with pytest.raises(SimulationInputContractError, match="frozen comparator bounds"):
        effective_pa_context(bundle, LeagueBaselines())


@pytest.mark.parametrize(
    "field,value",
    [
        ("recent_form_multiplier", float("nan")),
        ("bvp_ops_factor", float("inf")),
        ("platoon_advantage", "bad"),
    ],
)
def test_invalid_context_fails_closed(field, value) -> None:
    with pytest.raises(SimulationInputContractError, match="non-finite|not numeric"):
        effective_pa_context(_bundle(**{field: value}), LeagueBaselines())


def test_explicit_empty_category_request_stays_empty() -> None:
    engine = PropEngine(n_sims=500, config={"simulation": {"n_sims": 500}})
    assert engine.project_hitter(_bundle(), categories=()) == []


def test_invalid_non_null_lineup_slot_cannot_select_legacy_pa_fallback() -> None:
    bundle = _bundle()
    bundle.hitter.lineup_slot = 10
    engine = PropEngine(n_sims=500, config={"simulation": {"n_sims": 500}})
    with pytest.raises(ValueError, match="refusing silent fallback"):
        engine._bundle_to_sim_input(bundle)


def test_output_safeguard_violation_cannot_be_published(monkeypatch) -> None:
    engine = PropEngine(n_sims=500, config={"simulation": {"n_sims": 500}})
    monkeypatch.setattr(
        engine.output_safeguards,
        "check_projection",
        lambda *args, **kwargs: SafeguardReport(
            passed=False,
            violations=["injected impossible probability"],
            warnings=[],
        ),
    )
    with pytest.raises(ValueError, match="output safeguard rejected"):
        engine.project_hitter(_bundle(), categories=("hits",))


def test_in_bound_explicit_probabilities_are_numerically_unchanged() -> None:
    league = LeagueBaselines()
    bundle = _bundle(
        recent_form_multiplier=1.05,
        bvp_ops_factor=1.10,
        bvp_hr_factor=1.20,
        platoon_advantage=0.02,
    )
    simulator = HybridPASimulator(
        config=PASimulatorConfig.from_league(league), league_baselines=league
    )
    actual = ProbabilityEngine(
        league_baselines=league, pa_simulator=simulator
    ).from_bundle(bundle)
    expected = simulator.expected_outcome_probabilities(
        pitcher_k_pct=league.k_pct + 0.4,
        pitcher_bb_pct=league.bb_pct,
        pitcher_hr_per_9=None,
        park_hr_factor=1.07 * 1.05,
        park_hits_factor=1.03,
        handedness_advantage=0.02,
        recent_form_mult=1.05,
        bvp_ops_factor=1.10,
        bvp_hr_factor=1.20,
        statcast=bundle.statcast,
        rich_features={},
    )

    for key in expected:
        assert math.isclose(getattr(actual, key), expected[key], abs_tol=1e-15)
