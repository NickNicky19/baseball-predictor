"""
Hybrid Plate Appearance Simulator (Final Form Foundation).

Logit-based PA outcome model with calibratable coefficients. Latent skills are
centered on LeagueBaselines so the learning layer can update both baselines and
weights from backtests without editing method bodies.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from src.models.dataclasses import LeagueBaselines, PAOutcome, StatcastProfile

if TYPE_CHECKING:
    from src.simulation.base_state import BaseState


@dataclass
class PASimulatorConfig:
    """
    Configurable coefficients for the hybrid PA simulator.

    All logit weights and latent scaling factors live here so the evaluation
    and learning layers can recalibrate them over time.
    """

    # --- K rate logit ---
    k_intercept: float = 0.16
    k_pitcher_miss: float = 1.10
    k_hitter_contact: float = -1.05
    k_form: float = 0.30
    k_quality: float = 0.14

    # --- BB rate logit ---
    bb_intercept: float = -2.30
    bb_pitcher_control: float = 0.90
    bb_hitter_contact: float = -0.50
    bb_handedness: float = 0.20

    # --- HR rate logit ---
    hr_intercept: float = -3.28
    hr_hitter_power: float = 1.30
    hr_pitcher_miss: float = 0.20
    hr_park: float = 0.80
    hr_form: float = 0.28
    hr_handedness: float = 0.15
    hr_quality: float = 0.16

    # --- Latent skill scaling (z-score denominators) ---
    contact_scale: float = 2.0
    power_scale: float = 2.7
    speed_scale: float = 1.5
    pitcher_k_scale: float = 9.5
    pitcher_bb_scale: float = 7.5

    # --- Statcast feature scaling ---
    xwoba_scale: float = 3.2
    xslg_scale: float = 2.0
    barrel_scale: float = 1.6
    hard_hit_scale: float = 1.3

    # --- Context scaling ---
    form_log_min: float = 0.75
    form_log_max: float = 1.30
    handedness_scale: float = 0.85
    park_hr_log_floor: float = 0.72

    # --- Hit type distribution ---
    single_base_weight: float = 0.60
    double_base_weight: float = 0.225
    triple_base_weight: float = 0.028
    single_power_penalty: float = 0.55
    single_contact_bonus: float = 0.26
    double_power_bonus: float = 0.50
    triple_speed_bonus: float = 0.14
    single_weight_floor: float = 0.38
    double_weight_floor: float = 0.135
    triple_weight_floor: float = 0.008

    # --- Probability clamps (league-feasible per-PA ranges) ---
    k_min: float = 0.085
    k_max: float = 0.47
    bb_min: float = 0.03
    bb_max: float = 0.185
    hr_min: float = 0.004
    hr_max: float = 0.118

    @classmethod
    def from_league(cls, league: LeagueBaselines, **overrides: float) -> PASimulatorConfig:
        """Build config with optional overrides while keeping default coefficient structure."""
        return cls(**overrides)


class HybridPASimulator:
    """
    Hybrid Plate Appearance Simulator.

    Hierarchical outcome model: K / BB -> BIP -> HR vs hit type.
    Designed to be calibratable, backtestable, and driven by LeagueBaselines.
    """

    def __init__(
        self,
        config: Optional[PASimulatorConfig] = None,
        league_baselines: Optional[LeagueBaselines] = None,
        random_seed: Optional[int] = None,
    ):
        self.league = league_baselines or LeagueBaselines()
        self.config = config or PASimulatorConfig.from_league(self.league)
        self.rng = random.Random(random_seed)

    def simulate(
        self,
        pitcher_k_pct: Optional[float] = None,
        pitcher_bb_pct: Optional[float] = None,
        pitcher_hr_per_9: Optional[float] = None,
        hitter_contact: Optional[float] = None,
        hitter_power: Optional[float] = None,
        hitter_speed: Optional[float] = None,
        park_hr_factor: float = 1.0,
        handedness_advantage: float = 0.0,
        recent_form_mult: float = 1.0,
        statcast: Optional[StatcastProfile] = None,
    ) -> PAOutcome:
        """
        Simulate one plate appearance.

        Pitcher rates default to league averages. Hitter skill defaults are
        derived from league contact_rate and barrel_rate unless Statcast
        overrides are supplied. pitcher_hr_per_9 is reserved for future
        pitcher-HR skill integration in the HR logit.
        """
        _ = pitcher_hr_per_9  # wired in Phase 3 game simulator

        pitcher_k = pitcher_k_pct if pitcher_k_pct is not None else self.league.k_pct
        pitcher_bb = pitcher_bb_pct if pitcher_bb_pct is not None else self.league.bb_pct

        contact = hitter_contact if hitter_contact is not None else self.league.contact_rate
        power = hitter_power if hitter_power is not None else self.league.barrel_rate
        speed = hitter_speed if hitter_speed is not None else self._default_speed_anchor()

        latent = self._build_latent_profile(
            hitter_contact=contact,
            hitter_power=power,
            hitter_speed=speed,
            pitcher_k_pct=pitcher_k,
            pitcher_bb_pct=pitcher_bb,
            recent_form_mult=recent_form_mult,
            handedness_advantage=handedness_advantage,
            statcast=statcast,
        )

        k_prob, bb_prob = self._calculate_k_bb_probs(latent)
        roll = self.rng.random()

        if roll < k_prob:
            return PAOutcome("out", "Strikeout")
        if roll < k_prob + bb_prob:
            return PAOutcome("walk", "Walk")

        hr_prob = self._calculate_hr_prob(latent, park_hr_factor)
        if self.rng.random() < hr_prob:
            return PAOutcome("home_run", "Home Run")

        return self._sample_hit_type(latent)

    def expected_rates(
        self,
        pitcher_k_pct: Optional[float] = None,
        pitcher_bb_pct: Optional[float] = None,
        park_hr_factor: float = 1.0,
        handedness_advantage: float = 0.0,
        recent_form_mult: float = 1.0,
        statcast: Optional[StatcastProfile] = None,
        hitter_contact: Optional[float] = None,
        hitter_power: Optional[float] = None,
        hitter_speed: Optional[float] = None,
    ) -> dict[str, float]:
        """Return model-implied PA probabilities for calibration and backtests."""
        latent = self._build_latent_profile(
            hitter_contact=hitter_contact or self.league.contact_rate,
            hitter_power=hitter_power or self.league.barrel_rate,
            hitter_speed=hitter_speed or self._default_speed_anchor(),
            pitcher_k_pct=pitcher_k_pct if pitcher_k_pct is not None else self.league.k_pct,
            pitcher_bb_pct=pitcher_bb_pct if pitcher_bb_pct is not None else self.league.bb_pct,
            recent_form_mult=recent_form_mult,
            handedness_advantage=handedness_advantage,
            statcast=statcast,
        )
        k_prob, bb_prob = self._calculate_k_bb_probs(latent)
        hr_prob = self._calculate_hr_prob(latent, park_hr_factor)
        return {
            "k_prob": k_prob,
            "bb_prob": bb_prob,
            "bip_prob": max(0.0, 1.0 - k_prob - bb_prob),
            "hr_prob_on_bip": hr_prob,
        }

    def _build_latent_profile(
        self,
        hitter_contact: float,
        hitter_power: float,
        hitter_speed: float,
        pitcher_k_pct: float,
        pitcher_bb_pct: float,
        recent_form_mult: float,
        handedness_advantage: float,
        statcast: Optional[StatcastProfile],
    ) -> dict[str, float]:
        cfg = self.config
        lg = self.league

        if statcast and statcast.contact_rate is not None:
            hitter_contact = statcast.contact_rate
        if statcast and statcast.barrel_rate is not None:
            hitter_power = statcast.barrel_rate

        h_contact = (hitter_contact - lg.contact_rate) * cfg.contact_scale
        h_power = (hitter_power - lg.barrel_rate) * cfg.power_scale
        h_speed = (hitter_speed - self._default_speed_anchor()) * cfg.speed_scale

        p_miss = (pitcher_k_pct - lg.k_pct) / cfg.pitcher_k_scale
        p_control = (pitcher_bb_pct - lg.bb_pct) / cfg.pitcher_bb_scale

        form_effect = math.log(
            max(cfg.form_log_min, min(cfg.form_log_max, recent_form_mult))
        )
        handedness = handedness_advantage * cfg.handedness_scale

        xwoba = statcast.xwoba if statcast and statcast.xwoba is not None else lg.xwoba
        xslg = statcast.xslg if statcast and statcast.xslg is not None else lg.xslg
        barrel = statcast.barrel_rate if statcast and statcast.barrel_rate is not None else lg.barrel_rate
        hard_hit = (
            statcast.hard_hit_rate
            if statcast and statcast.hard_hit_rate is not None
            else lg.hard_hit_rate
        )

        xwoba_boost = (xwoba - lg.xwoba) * cfg.xwoba_scale
        power_boost = (xslg - lg.xslg) * cfg.xslg_scale
        barrel_boost = barrel * cfg.barrel_scale
        hard_hit_boost = (hard_hit - lg.hard_hit_rate) * cfg.hard_hit_scale

        return {
            "H_contact": h_contact,
            "H_power": h_power + power_boost + barrel_boost,
            "H_speed": h_speed,
            "H_quality": xwoba_boost + hard_hit_boost,
            "P_miss": p_miss,
            "P_control": p_control,
            "form": form_effect,
            "handedness": handedness,
        }

    def _calculate_k_bb_probs(self, latent: dict[str, float]) -> tuple[float, float]:
        cfg = self.config
        k_logit = (
            cfg.k_intercept
            + cfg.k_pitcher_miss * latent["P_miss"]
            + cfg.k_hitter_contact * latent["H_contact"]
            + cfg.k_form * latent["form"]
            + cfg.k_quality * latent["H_quality"]
        )
        k_prob = self._clamp(self._sigmoid(k_logit), cfg.k_min, cfg.k_max)

        bb_logit = (
            cfg.bb_intercept
            + cfg.bb_pitcher_control * latent["P_control"]
            + cfg.bb_hitter_contact * latent["H_contact"]
            + cfg.bb_handedness * latent["handedness"]
        )
        bb_prob = self._clamp(self._sigmoid(bb_logit), cfg.bb_min, cfg.bb_max)
        return k_prob, bb_prob

    def _calculate_hr_prob(self, latent: dict[str, float], park_hr_factor: float) -> float:
        cfg = self.config
        hr_logit = (
            cfg.hr_intercept
            + cfg.hr_hitter_power * latent["H_power"]
            + cfg.hr_pitcher_miss * latent["P_miss"]
            + cfg.hr_park * math.log(max(cfg.park_hr_log_floor, park_hr_factor))
            + cfg.hr_form * latent["form"]
            + cfg.hr_handedness * latent["handedness"]
            + cfg.hr_quality * latent["H_quality"]
        )
        return self._clamp(self._sigmoid(hr_logit), cfg.hr_min, cfg.hr_max)

    def _sample_hit_type(self, latent: dict[str, float]) -> PAOutcome:
        cfg = self.config
        power = latent["H_power"]
        speed = latent["H_speed"]
        contact = latent["H_contact"]

        single_w = max(
            cfg.single_weight_floor,
            cfg.single_base_weight
            - power * cfg.single_power_penalty
            + contact * cfg.single_contact_bonus,
        )
        double_w = max(cfg.double_weight_floor, cfg.double_base_weight + power * cfg.double_power_bonus)
        triple_w = max(cfg.triple_weight_floor, cfg.triple_base_weight + speed * cfg.triple_speed_bonus)

        total = single_w + double_w + triple_w
        p_single = single_w / total
        p_double = double_w / total
        p_triple = triple_w / total

        r = self.rng.random()
        if r < p_single:
            return PAOutcome("single", "Single")
        if r < p_single + p_double:
            return PAOutcome("double", "Double")
        if r < p_single + p_double + p_triple:
            return PAOutcome("triple", "Triple")
        return PAOutcome("out", "Ball in play out")

    def apply_to_state(self, state: BaseState, outcome: PAOutcome) -> None:
        if outcome.outcome == "out":
            state.record_out(is_strikeout=outcome.is_strikeout)
        elif outcome.outcome == "walk":
            state.advance_walk()
        elif outcome.outcome == "single":
            state.advance_single()
        elif outcome.outcome == "double":
            state.advance_double()
        elif outcome.outcome == "triple":
            state.advance_triple()
        elif outcome.outcome == "home_run":
            state.advance_home_run()

    def _default_speed_anchor(self) -> float:
        """Speed anchor until sprint-speed Statcast is wired in Phase 2."""
        return 0.10

    @staticmethod
    def _sigmoid(x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x))

    @staticmethod
    def _clamp(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))


# Backward compatibility alias
HybridPASimulatorV2 = HybridPASimulator