"""
Prop projection engine (Final Form).

Converts PlayerFeatureBundle inputs into PropProjection outputs using a
consistent Probability Engine + Monte Carlo approach with strong output
safeguards. Designed for high precision on HR/HRR and future explainability.
"""

from __future__ import annotations

from typing import Any, Optional

from src.data.mlb_api import PitchingStatsSnapshot
from src.models.dataclasses import (
    LeagueBaselines,
    PitcherGameContext,
    PlayerFeatureBundle,
    PropCategory,
    PropProjection,
)
from src.evaluation.output_safeguards import OutputSafeguards
from src.simulation.game_simulator import GameSimulator, GameSimulatorInput
from src.simulation.monte_carlo import FantasyScoring, MonteCarloEngine
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig
from src.simulation.probability_engine import ProbabilityEngine


class PropEngine:
    """
    Generates prop projections for hitters and pitchers.

    Uses ProbabilityEngine for explicit outcome probabilities and
    MonteCarloEngine for game-level simulation, with strong safeguards
    to prevent unrealistic HR/HRR outputs.
    """

    HITTER_CATEGORIES: tuple[PropCategory, ...] = ("hits", "hrr", "home_runs", "fantasy")

    def __init__(
        self,
        monte_carlo: Optional[MonteCarloEngine] = None,
        league_baselines: Optional[LeagueBaselines] = None,
        fantasy_scoring: Optional[FantasyScoring] = None,
        n_sims: Optional[int] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        self.config = config or {}
        self.league = league_baselines or LeagueBaselines.from_config(self.config)

        sim_cfg = self.config.get("simulation", {})
        self.n_sims = n_sims if n_sims is not None else int(sim_cfg.get("n_sims", 8000))

        self._fantasy_scoring = fantasy_scoring or FantasyScoring.from_config(self.config)
        self._pa_config = PASimulatorConfig.from_league(self.league)

        self.monte_carlo = monte_carlo or self._build_monte_carlo()
        self.probability_engine = ProbabilityEngine(
            league_baselines=self.league,
            pa_config=self._pa_config,
        )
        self.output_safeguards = OutputSafeguards(
            league_baselines=self.league,
            config=self.config,
        )

    def configure_simulation(
        self,
        league_baselines: Optional[LeagueBaselines] = None,
        pa_config: Optional[PASimulatorConfig] = None,
    ) -> None:
        """Update simulation configuration (used by CorrectionManager)."""
        if league_baselines is not None:
            self.league = league_baselines
        if pa_config is not None:
            self._pa_config = pa_config
        elif league_baselines is not None:
            self._pa_config = PASimulatorConfig.from_league(self.league)

        self.monte_carlo = self._build_monte_carlo()
        self.probability_engine = ProbabilityEngine(
            league_baselines=self.league,
            pa_config=self._pa_config,
        )
        self.output_safeguards = OutputSafeguards(
            league_baselines=self.league,
            config=self.config,
        )

    @property
    def pa_config(self) -> PASimulatorConfig:
        return self._pa_config

    def _build_monte_carlo(self) -> MonteCarloEngine:
        game_simulator = GameSimulator(
            pa_simulator=HybridPASimulator(
                config=self._pa_config,
                league_baselines=self.league,
            ),
            league_baselines=self.league,
        )
        return MonteCarloEngine(
            game_simulator=game_simulator,
            league_baselines=self.league,
            fantasy_scoring=self._fantasy_scoring,
        )

    def project_hitter(
        self,
        bundle: PlayerFeatureBundle,
        categories: Optional[tuple[PropCategory, ...]] = None,
    ) -> list[PropProjection]:
        """Return one PropProjection per requested category for a hitter."""
        cats = categories or self.HITTER_CATEGORIES
        sim_input = self._bundle_to_sim_input(bundle)
        projections: list[PropProjection] = []

        outcome_probs = self.probability_engine.from_bundle(bundle)

        n_sims_scale = float(bundle.metadata.get("lineup_simulation_n_sims_scale", 1.0))
        effective_n_sims = max(500, int(self.n_sims * n_sims_scale))

        for category in cats:
            mc_result = self.monte_carlo.run(
                sim_input,
                category=category,
                n_sims=effective_n_sims,
            )

            projection = PropProjection(
                player_id=bundle.hitter.player.mlb_id,
                player_name=bundle.hitter.player.name,
                category=category,
                game_date=bundle.hitter.game.game_date,
                projected_value=round(mc_result.mean, 3),
                confidence=self._hitter_confidence(bundle, mc_result),
                simulation=mc_result,
                outcome_probs=outcome_probs if category in ("hits", "home_runs", "hrr") else None,
                team=bundle.hitter.player.team,
                opponent=bundle.hitter.game.opponent,
                opposing_pitcher=bundle.hitter.opposing_pitcher_name,
                lineup_status=bundle.hitter.game.lineup_status,
            )

            # Apply output safeguards
            safeguard = self.output_safeguards.check_projection(
                projection,
                expected_pa=bundle.expected_pa,
            )
            if not safeguard.passed:
                from src.utils.logging import get_logger
                get_logger(__name__).warning(
                    "Output safeguard violation for %s (%s): %s",
                    bundle.hitter.player.name,
                    category,
                    "; ".join(safeguard.violations),
                )

            projections.append(projection)

        return projections

    def project_pitcher_strikeouts(
        self,
        pitcher: PitcherGameContext,
        season_stats: PitchingStatsSnapshot,
        recent_stats: PitchingStatsSnapshot,
    ) -> PropProjection:
        """
        Project pitcher strikeouts using PA-rate simulation.
        """
        weights = self.config.get("weights", {})
        season_w = float(weights.get("season", 0.35))
        recent_w = float(weights.get("recent", 0.65))

        season_k9 = season_stats.k_per_9 or self._league_k9()
        recent_k9 = recent_stats.k_per_9 or season_k9
        blended_k9 = (season_w * season_k9) + (recent_w * recent_k9)

        reg = self.config.get("pitcher_regression", {})
        season_blend = float(reg.get("season_k9_blend", 0.75))
        league_blend = float(reg.get("league_k9_blend", 0.25))
        league_k9 = self._league_k9()

        regressed_k9 = (season_blend * blended_k9) + (league_blend * league_k9)

        pitcher_k_pct = (regressed_k9 / 9.0) * 4.2 * 100.0
        pitcher_bb_pct = (
            recent_stats.bb_per_9 or season_stats.bb_per_9 or self.league.bb_pct
        )
        pitcher_bb_pct = (pitcher_bb_pct / 9.0) * 4.2 * 100.0

        expected_ip = pitcher.expected_innings
        batters_faced = expected_ip * 4.2

        pa_sim = HybridPASimulator(
            config=self.pa_config,
            league_baselines=self.league,
        )
        rates = pa_sim.expected_rates(
            pitcher_k_pct=pitcher_k_pct,
            pitcher_bb_pct=pitcher_bb_pct,
            park_hr_factor=1.0,
        )
        projected_k = rates["k_prob"] * batters_faced

        confidence = self._pitcher_confidence(recent_stats, season_stats)

        return PropProjection(
            player_id=pitcher.player.mlb_id,
            player_name=pitcher.player.name,
            category="strikeouts",
            game_date=pitcher.game.game_date,
            projected_value=round(projected_k, 2),
            confidence=confidence,
            simulation=None,
        )

    def _bundle_to_sim_input(self, bundle: PlayerFeatureBundle) -> GameSimulatorInput:
        pitcher_k = self.league.k_pct
        pitcher_bb = self.league.bb_pct

        if bundle.pitcher_statcast and bundle.pitcher_statcast.k_rate is not None:
            pitcher_k = bundle.pitcher_statcast.k_rate * 100.0
        if bundle.pitcher_statcast and bundle.pitcher_statcast.bb_rate is not None:
            pitcher_bb = bundle.pitcher_statcast.bb_rate * 100.0

        pitcher_hr_per_9 = None
        if bundle.pitcher_statcast and bundle.pitcher_statcast.hr_per_9 is not None:
            pitcher_hr_per_9 = bundle.pitcher_statcast.hr_per_9

        weather_hr = float(bundle.metadata.get("weather_hr_factor", 1.0))
        umpire_k_bias = 0.0
        if bundle.umpire:
            umpire_k_bias = bundle.umpire.k_bias

        return GameSimulatorInput(
            expected_pa=bundle.expected_pa,
            pitcher_k_pct=pitcher_k,
            pitcher_bb_pct=pitcher_bb,
            park_hr_factor=bundle.park.hr_factor,
            park_hits_factor=bundle.park.hits_factor,
            weather_hr_factor=weather_hr,
            umpire_k_bias=umpire_k_bias,
            handedness_advantage=bundle.matchup.platoon_advantage,
            recent_form_mult=bundle.matchup.recent_form_multiplier,
            bvp_ops_factor=bundle.matchup.bvp_ops_factor,
            bvp_hr_factor=bundle.matchup.bvp_hr_factor,
            statcast=bundle.statcast,
            pitcher_hr_per_9=pitcher_hr_per_9,
        )

    def _hitter_confidence(self, bundle: PlayerFeatureBundle, mc_result) -> float:
        """Confidence from Statcast sample size, simulation stability, and lineup certainty."""
        sample_boost = min(0.25, bundle.statcast.sample_pa / 300.0)
        variance_inflation = float(bundle.metadata.get("lineup_variance_inflation", 1.0))
        spread = (mc_result.p90 - mc_result.p10) * variance_inflation
        stability = max(0.0, 0.20 - spread * 0.04)
        base = 0.45 if bundle.statcast.has_advanced_data() else 0.38
        lineup_mult = float(bundle.metadata.get("lineup_confidence_multiplier", 1.0))
        raw = (base + sample_boost + stability) * lineup_mult
        return round(min(0.88, raw), 3)

    def _pitcher_confidence(
        self, recent: PitchingStatsSnapshot, season: PitchingStatsSnapshot
    ) -> float:
        conf = 0.50
        if recent.innings_pitched >= 15:
            conf += 0.12
        if season.games_started >= 8:
            conf += 0.10
        return round(min(0.85, conf), 3)

    def _league_k9(self) -> float:
        league = self.config.get("league_avg", {})
        return float(league.get("k_per_9", 8.8))
