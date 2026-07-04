"""
Hybrid Plate Appearance Simulator (Final Form).

Logit-based hierarchical PA model with calibratable coefficients.
Designed for high precision on HR/HRR, consistency between sampling
and explicit probability estimation, and future integration with a full
probability engine.

FIXES (this revision):
- power_boost is centered again: (xslg - lg.xslg) * scale. The previous
  inline refactor had an operator-precedence bug that applied the raw,
  uncentered xSLG (adding ~+12.5 logits of power to EVERY player with a
  Statcast profile).
- rich_features is now an explicit parameter threaded through internal
  methods instead of mutable instance state (self._rich_features). This
  removes the state-clobbering bug where expected_rates() silently dropped
  rich features, and makes the simulator safe for reuse/concurrency.
- Removed the extra barrel term that was added to the HR logit. Barrel rate
  already enters the HR logit twice (via hitter_power -> H_power, and via
  barrel_boost inside H_power); a third term re-introduced the double-count
  that an earlier fix explicitly removed.
- Rich features, when they carry rolling metrics (roll15_xwoba etc.), are
  blended into the quality signal with a sample-size-aware weight. Rich
  values that merely duplicate the StatcastProfile produce identical output
  to the profile-only path (verified by regression test).
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Optional, Any

from src.models.dataclasses import LeagueBaselines, PAOutcome, StatcastProfile

if TYPE_CHECKING:
    from src.simulation.base_state import BaseState


@dataclass
class PASimulatorConfig:
    """Configurable coefficients for the hybrid PA simulator."""

    # K rate logit
    k_intercept: float = 0.16
    k_pitcher_miss: float = 1.10
    k_hitter_contact: float = -1.05
    k_form: float = 0.30
    k_quality: float = 0.14

    # BB rate logit
    bb_intercept: float = -2.30
    bb_pitcher_control: float = 0.90
    bb_hitter_contact: float = -0.50
    bb_handedness: float = 0.20

    # HR rate logit
    hr_intercept: float = -3.28
    hr_hitter_power: float = 1.30
    hr_pitcher_miss: float = 0.20
    hr_park: float = 0.80
    hr_form: float = 0.28
    hr_handedness: float = 0.15
    hr_quality: float = 0.16

    # Latent skill scaling
    contact_scale: float = 2.0
    power_scale: float = 2.7
    speed_scale: float = 1.5
    pitcher_k_scale: float = 9.5
    pitcher_bb_scale: float = 7.5

    # Statcast feature scaling
    xwoba_scale: float = 3.2
    xslg_scale: float = 2.0
    barrel_scale: float = 1.6
    hard_hit_scale: float = 1.3

    # Rolling-form blending (rich feature layer). Weight applied to the
    # deviation of rolling xwOBA from season xwOBA, ramped by recent sample.
    rolling_quality_weight: float = 0.35
    rolling_pa_ramp: float = 60.0  # PA at which rolling signal gets full weight

    # Context
    form_log_min: float = 0.75
    form_log_max: float = 1.30
    handedness_scale: float = 0.85
    park_hr_log_floor: float = 0.72

    # BIP outcome weights
    bip_out_base_weight: float = 2.09
    single_base_weight: float = 0.60
    double_base_weight: float = 0.225
    triple_base_weight: float = 0.028
    out_weight_floor: float = 0.50
    out_contact_bonus: float = 0.15
    out_power_bonus: float = 0.08
    single_power_penalty: float = 0.55
    single_contact_bonus: float = 0.26
    double_power_bonus: float = 0.50
    triple_speed_bonus: float = 0.14
    single_weight_floor: float = 0.38
    double_weight_floor: float = 0.135
    triple_weight_floor: float = 0.008

    # Context scaling on BIP
    bvp_hr_weight: float = 0.26
    bvp_hit_weight: float = 0.21
    park_hits_weight: float = 0.35
    context_hit_scale_max: float = 1.12
    context_hit_scale_min: float = 0.88
    hit_prob_cap: float = 0.36

    # Probability clamps
    k_min: float = 0.085
    k_max: float = 0.47
    bb_min: float = 0.03
    bb_max: float = 0.185
    hr_min: float = 0.004
    hr_max: float = 0.118

    @classmethod
    def from_league(cls, league: LeagueBaselines, **overrides: float) -> PASimulatorConfig:
        """Build config with intercepts and scales derived from league baselines."""
        k_rate = max(0.05, min(0.45, league.k_pct / 100.0))
        bb_rate = max(0.02, min(0.18, league.bb_pct / 100.0))
        bip_rate = max(0.35, 1.0 - k_rate - bb_rate)
        pa_per_ip = 4.2
        hr_per_pa = max(0.002, (league.hr_per_9 / 9.0) / pa_per_ip)
        hr_on_bip = max(0.003, min(0.14, hr_per_pa / bip_rate))

        contact_anchor = max(0.01, league.contact_rate)
        barrel_anchor = max(0.01, league.barrel_rate)
        xwoba_anchor = max(0.01, abs(league.xwoba))
        xslg_anchor = max(0.01, abs(league.xslg))

        hit_rate_pa = league.hits_per_game / max(league.pa_per_game, 1.0)
        non_hr_hit_rate = max(0.01, hit_rate_pa - hr_per_pa)
        non_hr_bip_rate = bip_rate * (1.0 - hr_on_bip)
        babip = max(0.22, min(0.36, non_hr_hit_rate / max(non_hr_bip_rate, 0.01)))

        hit_base_sum = cls.single_base_weight + cls.double_base_weight + cls.triple_base_weight
        bip_out_weight = hit_base_sum * (1.0 - babip) / babip
        hit_scale_span = max(0.36 - babip, babip - 0.22) / max(babip, 0.01)

        derived = cls(
            k_intercept=_logit(k_rate),
            bb_intercept=_logit(bb_rate),
            hr_intercept=_logit(hr_on_bip),
            bip_out_base_weight=bip_out_weight,
            bvp_hr_weight=1.30 * 0.20,
            bvp_hit_weight=0.26 * 0.80,
            park_hits_weight=1.0 / max(hit_base_sum, 0.01),
            context_hit_scale_max=1.0 + hit_scale_span * 0.35,
            context_hit_scale_min=1.0 - hit_scale_span * 0.35,
            hit_prob_cap=min(0.38, hit_rate_pa * 1.65),
            contact_scale=1.0 / max(contact_anchor * 0.08, 0.01),
            power_scale=1.0 / max(barrel_anchor, 0.01),
            speed_scale=1.0 / max(barrel_anchor * 0.6, 0.01),
            pitcher_k_scale=max(league.k_pct, 1.0),
            pitcher_bb_scale=max(league.bb_pct, 1.0),
            xwoba_scale=1.0 / max(xwoba_anchor * 0.08, 0.01),
            xslg_scale=1.0 / max(xslg_anchor * 0.08, 0.01),
            barrel_scale=1.0 / max(barrel_anchor, 0.01),
            hard_hit_scale=1.0 / max(league.hard_hit_rate * 0.08, 0.01),
            k_min=max(0.05, k_rate * 0.55),
            k_max=min(0.50, k_rate * 1.85),
            bb_min=max(0.02, bb_rate * 0.55),
            bb_max=min(0.20, bb_rate * 1.85),
            hr_min=max(0.002, hr_on_bip * 0.45),
            hr_max=min(0.14, hr_on_bip * 2.20),
        )
        if overrides:
            return cls(**{**asdict(derived), **overrides})
        return derived


class HybridPASimulator:
    """
    Hybrid Plate Appearance Simulator (Final Form).

    Hierarchical model: K/BB -> BIP -> HR vs non-HR.
    rich_features is passed explicitly down the call chain (never stored on
    the instance), so calls are stateless and reproducible.
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def simulate(
        self,
        pitcher_k_pct: Optional[float] = None,
        pitcher_bb_pct: Optional[float] = None,
        pitcher_hr_per_9: Optional[float] = None,
        hitter_contact: Optional[float] = None,
        hitter_power: Optional[float] = None,
        hitter_speed: Optional[float] = None,
        park_hr_factor: float = 1.0,
        park_hits_factor: float = 1.0,
        handedness_advantage: float = 0.0,
        recent_form_mult: float = 1.0,
        bvp_ops_factor: float = 1.0,
        bvp_hr_factor: float = 1.0,
        statcast: Optional[StatcastProfile] = None,
        rich_features: Optional[dict[str, Any]] = None,
    ) -> PAOutcome:
        """Simulate one plate appearance (sampling path)."""
        rich = rich_features or {}

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
            rich=rich,
        )

        k_prob, bb_prob = self._calculate_k_bb_probs(latent)
        roll = self.rng.random()

        if roll < k_prob:
            return PAOutcome("out", "Strikeout")
        if roll < k_prob + bb_prob:
            return PAOutcome("walk", "Walk")

        hr_prob = self._calculate_hr_prob(
            latent,
            park_hr_factor,
            statcast=statcast,
            pitcher_hr_per_9=pitcher_hr_per_9,
            bvp_hr_factor=bvp_hr_factor,
        )
        if self.rng.random() < hr_prob:
            return PAOutcome("home_run", "Home Run")

        return self._sample_hit_type(
            latent,
            park_hits_factor=park_hits_factor,
            bvp_ops_factor=bvp_ops_factor,
        )

    def expected_outcome_probabilities(
        self,
        pitcher_k_pct: Optional[float] = None,
        pitcher_bb_pct: Optional[float] = None,
        pitcher_hr_per_9: Optional[float] = None,
        park_hr_factor: float = 1.0,
        park_hits_factor: float = 1.0,
        handedness_advantage: float = 0.0,
        recent_form_mult: float = 1.0,
        bvp_ops_factor: float = 1.0,
        bvp_hr_factor: float = 1.0,
        statcast: Optional[StatcastProfile] = None,
        hitter_contact: Optional[float] = None,
        hitter_power: Optional[float] = None,
        hitter_speed: Optional[float] = None,
        rich_features: Optional[dict[str, Any]] = None,
    ) -> dict[str, float]:
        """Return explicit per-PA outcome probabilities."""
        rich = rich_features or {}

        latent = self._build_latent_profile(
            hitter_contact=hitter_contact if hitter_contact is not None else self.league.contact_rate,
            hitter_power=hitter_power if hitter_power is not None else self.league.barrel_rate,
            hitter_speed=hitter_speed if hitter_speed is not None else self._default_speed_anchor(),
            pitcher_k_pct=pitcher_k_pct if pitcher_k_pct is not None else self.league.k_pct,
            pitcher_bb_pct=pitcher_bb_pct if pitcher_bb_pct is not None else self.league.bb_pct,
            recent_form_mult=recent_form_mult,
            handedness_advantage=handedness_advantage,
            statcast=statcast,
            rich=rich,
        )

        k_prob, bb_prob = self._calculate_k_bb_probs(latent)
        bip_prob = max(0.0, 1.0 - k_prob - bb_prob)

        hr_prob = self._calculate_hr_prob(
            latent,
            park_hr_factor,
            statcast=statcast,
            pitcher_hr_per_9=pitcher_hr_per_9,
            bvp_hr_factor=bvp_hr_factor,
        )
        hr_prob = min(hr_prob, bip_prob)
        non_hr_bip = max(0.0, bip_prob - hr_prob)

        out_w, single_w, double_w, triple_w = self._hit_type_weights(
            latent,
            park_hits_factor=park_hits_factor,
            bvp_ops_factor=bvp_ops_factor,
        )

        total = out_w + single_w + double_w + triple_w
        if total <= 0:
            return {
                "strikeout": k_prob,
                "walk": bb_prob,
                "home_run": hr_prob,
                "single": 0.0,
                "double": 0.0,
                "triple": 0.0,
                "out_on_bip": non_hr_bip,
            }

        result = {
            "strikeout": k_prob,
            "walk": bb_prob,
            "home_run": hr_prob,
            "single": non_hr_bip * (single_w / total),
            "double": non_hr_bip * (double_w / total),
            "triple": non_hr_bip * (triple_w / total),
            "out_on_bip": non_hr_bip * (out_w / total),
        }
        return self._apply_hit_prob_cap(result)

    def expected_rates(
        self,
        pitcher_k_pct: Optional[float] = None,
        pitcher_bb_pct: Optional[float] = None,
        park_hr_factor: float = 1.0,
        park_hits_factor: float = 1.0,
        handedness_advantage: float = 0.0,
        recent_form_mult: float = 1.0,
        bvp_ops_factor: float = 1.0,
        bvp_hr_factor: float = 1.0,
        statcast: Optional[StatcastProfile] = None,
        hitter_contact: Optional[float] = None,
        hitter_power: Optional[float] = None,
        hitter_speed: Optional[float] = None,
        pitcher_hr_per_9: Optional[float] = None,
        rich_features: Optional[dict[str, Any]] = None,
    ) -> dict[str, float]:
        """Return model-implied PA probabilities for calibration and backtests."""
        probs = self.expected_outcome_probabilities(
            pitcher_k_pct=pitcher_k_pct,
            pitcher_bb_pct=pitcher_bb_pct,
            pitcher_hr_per_9=pitcher_hr_per_9,
            park_hr_factor=park_hr_factor,
            park_hits_factor=park_hits_factor,
            handedness_advantage=handedness_advantage,
            recent_form_mult=recent_form_mult,
            bvp_ops_factor=bvp_ops_factor,
            bvp_hr_factor=bvp_hr_factor,
            statcast=statcast,
            hitter_contact=hitter_contact,
            hitter_power=hitter_power,
            hitter_speed=hitter_speed,
            rich_features=rich_features,  # FIX: previously dropped on the floor
        )
        bip_prob = max(0.0, 1.0 - probs["strikeout"] - probs["walk"])
        hr_on_bip = probs["home_run"] / bip_prob if bip_prob > 0 else 0.0
        return {
            "k_prob": probs["strikeout"],
            "bb_prob": probs["walk"],
            "bip_prob": bip_prob,
            "hr_prob_on_bip": hr_on_bip,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

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
        rich: Optional[dict[str, Any]] = None,
    ) -> dict[str, float]:
        cfg = self.config
        lg = self.league
        rich = rich or {}

        def _pick(key: str, statcast_value: Optional[float], league_value: float) -> float:
            """Prefer rich value, then statcast profile, then league anchor."""
            rich_value = rich.get(key)
            if rich_value is not None:
                return float(rich_value)
            if statcast_value is not None:
                return float(statcast_value)
            return league_value

        xwoba = _pick("xwoba", statcast.xwoba if statcast else None, lg.xwoba)
        xslg = _pick("xslg", statcast.xslg if statcast else None, lg.xslg)
        barrel = _pick("barrel_rate", statcast.barrel_rate if statcast else None, lg.barrel_rate)
        hard_hit = _pick(
            "hard_hit_rate", statcast.hard_hit_rate if statcast else None, lg.hard_hit_rate
        )

        if statcast and statcast.contact_rate is not None:
            hitter_contact = statcast.contact_rate
        if rich.get("contact_rate") is not None:
            hitter_contact = float(rich["contact_rate"])
        if statcast and statcast.barrel_rate is not None:
            hitter_power = statcast.barrel_rate
        if rich.get("barrel_rate") is not None:
            hitter_power = float(rich["barrel_rate"])

        h_contact = (hitter_contact - lg.contact_rate) * cfg.contact_scale
        h_power = (hitter_power - lg.barrel_rate) * cfg.power_scale
        h_speed = (hitter_speed - self._default_speed_anchor()) * cfg.speed_scale

        p_miss = (pitcher_k_pct - lg.k_pct) / cfg.pitcher_k_scale
        p_control = (pitcher_bb_pct - lg.bb_pct) / cfg.pitcher_bb_scale

        form_effect = math.log(
            max(cfg.form_log_min, min(cfg.form_log_max, recent_form_mult))
        )
        handedness = handedness_advantage * cfg.handedness_scale

        xwoba_boost = (xwoba - lg.xwoba) * cfg.xwoba_scale
        # FIX: centered against league anchor (the inline refactor dropped
        # the centering via an operator-precedence bug and applied raw xSLG).
        power_boost = (xslg - lg.xslg) * cfg.xslg_scale
        barrel_boost = (barrel - lg.barrel_rate) * cfg.barrel_scale
        hard_hit_boost = (hard_hit - lg.hard_hit_rate) * cfg.hard_hit_scale

        # NEW SIGNAL (not a duplicate): rolling-form deviation from season
        # xwOBA, weighted by recent sample size. Only fires when the rich
        # layer provides actual rolling data (roll15_xwoba + recent_pa_15).
        rolling_quality = 0.0
        roll_xwoba = rich.get("roll15_xwoba")
        if roll_xwoba is not None:
            recent_pa = float(rich.get("recent_pa_15") or 0.0)
            ramp = min(1.0, recent_pa / max(cfg.rolling_pa_ramp, 1.0))
            rolling_quality = (
                (float(roll_xwoba) - xwoba) * cfg.xwoba_scale
                * cfg.rolling_quality_weight * ramp
            )

        return {
            "H_contact": h_contact,
            "H_power": h_power + power_boost + barrel_boost,
            "H_speed": h_speed,
            "H_quality": xwoba_boost + hard_hit_boost + rolling_quality,
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

    def _calculate_hr_prob(
        self,
        latent: dict[str, float],
        park_hr_factor: float,
        statcast: Optional[StatcastProfile] = None,
        pitcher_hr_per_9: Optional[float] = None,
        bvp_hr_factor: float = 1.0,
    ) -> float:
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

        if bvp_hr_factor != 1.0:
            hr_logit += cfg.bvp_hr_weight * math.log(
                max(0.6, min(1.5, bvp_hr_factor))
            )

        if pitcher_hr_per_9 is not None:
            pitcher_hr_skill = (pitcher_hr_per_9 - self.league.hr_per_9) / max(
                self.league.hr_per_9, 0.5
            )
            hr_logit -= cfg.hr_pitcher_miss * pitcher_hr_skill

        # NOTE: no standalone barrel term here. Barrel rate already reaches
        # this logit through latent["H_power"] (hitter_power + barrel_boost).
        # A previous fix removed the double-count; keep it removed.

        dist = statcast.distribution if statcast and statcast.distribution else None
        if dist and dist.sample_bip > 0:
            quality = dist.quality_score()
            hr_logit += cfg.hr_quality * quality * 1.3

            ev_boost = max(0.0, (dist.exit_velocity_mean - 88.0) / 12.0)
            la_penalty = abs(dist.launch_angle_mean - 20.0) / 30.0
            hr_logit += cfg.hr_hitter_power * (ev_boost - la_penalty * 0.28)

        return self._clamp(self._sigmoid(hr_logit), cfg.hr_min, cfg.hr_max)

    def _hit_type_weights(
        self,
        latent: dict[str, float],
        park_hits_factor: float = 1.0,
        bvp_ops_factor: float = 1.0,
    ) -> tuple[float, float, float, float]:
        cfg = self.config
        power = latent["H_power"]
        speed = latent["H_speed"]
        contact = latent["H_contact"]

        hit_scale = self._context_hit_scale(park_hits_factor, bvp_ops_factor)

        single_w = max(
            cfg.single_weight_floor,
            cfg.single_base_weight
            - power * (cfg.single_power_penalty * 0.45)
            + contact * cfg.single_contact_bonus,
        ) * hit_scale

        double_w = (
            max(
                cfg.double_weight_floor,
                cfg.double_base_weight + power * (cfg.double_power_bonus * 0.75),
            )
            * hit_scale
        )

        triple_w = (
            max(cfg.triple_weight_floor, cfg.triple_base_weight + speed * cfg.triple_speed_bonus)
            * hit_scale
        )

        out_w = max(
            cfg.out_weight_floor,
            cfg.bip_out_base_weight
            - contact * cfg.out_contact_bonus
            - power * cfg.out_power_bonus,
        ) / hit_scale

        return out_w, single_w, double_w, triple_w

    def _apply_hit_prob_cap(self, probs: dict[str, float]) -> dict[str, float]:
        """Redistribute excess hit probability to BIP outs — prevents skill-stacking overshoot."""
        cfg = self.config
        hit_total = (
            probs["single"] + probs["double"] + probs["triple"] + probs["home_run"]
        )
        if hit_total <= cfg.hit_prob_cap:
            return probs

        scale = cfg.hit_prob_cap / max(hit_total, 0.0001)
        excess = hit_total - cfg.hit_prob_cap
        return {
            **probs,
            "home_run": probs["home_run"] * scale,
            "single": probs["single"] * scale,
            "double": probs["double"] * scale,
            "triple": probs["triple"] * scale,
            "out_on_bip": probs["out_on_bip"] + excess,
        }

    def _context_hit_scale(self, park_hits_factor: float, bvp_ops_factor: float) -> float:
        cfg = self.config
        park_adj = 1.0 + cfg.park_hits_weight * (park_hits_factor - 1.0)
        bvp_adj = 1.0 + cfg.bvp_hit_weight * (bvp_ops_factor - 1.0)
        combined = math.sqrt(max(0.01, park_adj * bvp_adj))
        return max(cfg.context_hit_scale_min, min(cfg.context_hit_scale_max, combined))

    def _sample_hit_type(
        self,
        latent: dict[str, float],
        park_hits_factor: float = 1.0,
        bvp_ops_factor: float = 1.0,
    ) -> PAOutcome:
        out_w, single_w, double_w, triple_w = self._hit_type_weights(
            latent,
            park_hits_factor=park_hits_factor,
            bvp_ops_factor=bvp_ops_factor,
        )

        total = out_w + single_w + double_w + triple_w
        p_out = out_w / total
        p_single = single_w / total
        p_double = double_w / total
        p_triple = triple_w / total

        r = self.rng.random()
        if r < p_out:
            return PAOutcome("out", "Ball in play out")
        if r < p_out + p_single:
            return PAOutcome("single", "Single")
        if r < p_out + p_single + p_double:
            return PAOutcome("double", "Double")
        if r < p_out + p_single + p_double + p_triple:
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
        return 0.10

    @staticmethod
    def _sigmoid(x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x))

    @staticmethod
    def _clamp(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))


def _logit(p: float) -> float:
    p = max(0.001, min(0.999, p))
    return math.log(p / (1.0 - p))


# Backward compatibility
HybridPASimulatorV2 = HybridPASimulator
