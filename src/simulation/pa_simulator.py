"""
Hybrid Plate Appearance Simulator (xBA-driven hit model).

Logit-based hierarchical PA model: K/BB logits -> HR-on-BIP logit -> hit
outcome on remaining balls in play.

STRUCTURAL CHANGE (xBA hit model):
- P(hit | ball in play) is now set DIRECTLY from the player's measured xBA
  (Statcast estimated_ba_using_speedangle averaged over batted balls), shrunk
  toward the league contact-conditional baseline by sample size. The previous
  latent-weight arithmetic (contact/power coupled into single/double/out
  weights) is gone: it triple-counted contact, let sluggers gut their own out
  rate, and saturated everyone at the hit cap. Constructing the hit rate from
  a measured quantity deletes five coupling coefficients instead of tuning
  them, per the project principle of minimizing hand-picked numbers.
- H_power / H_speed now shape only the MIX of extra-base hits among hits,
  never the total hit rate. Total hits come from xBA; power raises doubles
  share and HR (via its own logit); speed raises triples share.
- CONSISTENCY FIX: _calculate_hr_prob returns P(HR | BIP) by construction
  (its intercept is logit(hr_on_bip)). The sampling path always used it that
  way, but expected_outcome_probabilities() previously reported it as an
  absolute per-PA probability — overstating HR by ~1/bip (~45%) relative to
  what the Monte Carlo actually simulates. Both paths now share one
  computation and agree by construction (regression-tested).
- The hits self-calibration lever is now a single knob (hit_rate_scale)
  instead of four interacting weight fields.

League anchor (must always hold): a no-profile league-average hitter produces
hit/PA = bip_rate * xba_on_contact ~= 0.21 and per-PA HR ~= (hr_per_9/9)/4.2.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Optional, Any

from src.evaluation.hits_contact_adapter import consume_fitted_contact_xba
from src.data.statcast_integrity import validate_profile_and_rich_features
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
    k_quality: float = 0.08

    # BB rate logit
    bb_intercept: float = -2.30
    bb_pitcher_control: float = 0.90
    bb_hitter_contact: float = -0.50
    bb_handedness: float = 0.20

    # HR rate logit (probability is conditional on ball in play)
    hr_intercept: float = -3.28
    hr_hitter_power: float = 0.60
    hr_pitcher_miss: float = 0.20
    hr_park: float = 0.80
    hr_form: float = 0.28
    hr_handedness: float = 0.15
    hr_quality: float = 0.16

    # Frozen production historically applied the pitcher HR/9 term with the
    # wrong sign. Preserve it only as an explicit comparator. Repaired
    # research candidates must select ``corrected`` or omit pitcher HR/9.
    pitcher_hr9_effect_mode: str = "legacy_frozen"

    # Latent skill scaling
    contact_scale: float = 8.0
    power_scale: float = 2.7
    speed_scale: float = 1.5
    pitcher_k_scale: float = 9.5
    pitcher_bb_scale: float = 7.5

    # Statcast feature scaling
    xwoba_scale: float = 3.2
    xslg_scale: float = 2.0
    barrel_scale: float = 1.6
    hard_hit_scale: float = 1.3

    # Rolling-form blending (rich feature layer)
    rolling_quality_weight: float = 0.35
    rolling_pa_ramp: float = 60.0

    # === xBA-driven hit model ===
    # Empirical-Bayes shrinkage: weight on the player's own xBA is
    # sample_pa / (sample_pa + xba_shrinkage_pa). One principled parameter
    # instead of a fixed blend fraction.
    xba_shrinkage_pa: float = 120.0
    # Bounds on the (context-scaled) target hit-on-contact rate.
    hit_on_contact_min: float = 0.18
    hit_on_contact_max: float = 0.45
    # Self-calibration lever for the hits category (learning layer tunes THIS,
    # replacing the four interacting weight fields it used to touch).
    hit_rate_scale: float = 1.0

    # ========================================================================
    # FITTED K/BB MODEL — the hitter's OWN strikeout and walk rates
    # ========================================================================
    # ABSENT / False -> the legacy H_contact path, BYTE-IDENTICAL. Threading this
    # through is INERT until a config sets pa_simulator.use_fitted_kbb.
    #
    # THE BUG THIS REPLACES (all measured, none assumed):
    #
    #   StatcastProfile.k_rate was NEVER POPULATED. Savant's pull filters to
    #   `events.notna()` -- BATTED BALLS. A strikeout is not a batted ball, so K
    #   is unobtainable there. Every hitter fell back to the league constant:
    #   MEASURED sd = 0.0000 across 270 hitters.
    #
    #   So the K logit had no direct signal and used `contact_rate`, a per-swing
    #   whiff PROXY. And league.contact_rate (0.7550) is the average over ALL
    #   batters, while the model runs on STARTING LINEUPS, which average 0.8178.
    #   H_contact therefore centres on +0.52 instead of 0, and
    #   k_hitter_contact (-1.05) turns that into -0.55 logits FOR EVERY HITTER.
    #
    #   Result (trace_k_bb_logits.py, logit partitioned EXACTLY -- a logit is a
    #   sum, so the attribution is arithmetic, not an estimate):
    #       k_hitter_contact carried 89.9% of the shift off the intercept
    #       a league-average starter produced K = 0.1365 vs a realized 0.2221
    #       the K floor then bound on 46.3% of the slate, HIDING how bad it was
    #       bip_prob was inflated 9.9%
    #       -> which carried 91.7% of the model's ENTIRE hits excess
    #          (trace_hit_rate.py, reconciled to -0.00036)
    #
    #   And it broke the RANKING: sd(H_contact) = 0.62 meant contact rate
    #   DOMINATED the K logit, so a high-contact rookie outranked Murakami in
    #   production.
    #
    # THE FIT (scripts/fit_k_bb_final.py -- binomial GLM, temporal split,
    # scored out-of-sample). ALL COEFFICIENTS FITTED (rule 2):
    #
    # Coefficients are intentionally NOT embedded here. The production bridge
    # loads them from a hash-verified artifact when use_fitted_kbb is true.
    # This prevents documentation, runtime defaults, and the recorded fit from
    # becoming three different coefficient sources.
    #
    # Exact row counts, date bounds, calibration, and held-out log-likelihood
    # gains live in the bound artifact rather than in this source comment.
    #
    # *** THE PITCHER IS IN THE FIT, NOT ADDED ON TOP. ***
    #   An earlier attempt fitted a HITTER-ONLY model and then added a pitcher
    #   term to the logit. That DOUBLE-COUNTS the pitcher: the GLM was fitted on
    #   real games against real pitchers, so its intercept ALREADY ABSORBS the
    #   average pitcher effect. It produced K = 0.1785 against a realized 0.2221
    #   and was caught by the calibration gate. Validated on synthetic data: a
    #   bolted-on pitcher term produces an error of +0.155 where the tolerance
    #   is 0.005.
    #
    # *** UNITS. *** pitcher_*_pct is per BATTER FACED, 0-100 -- the SAME
    #   quantity expected_outcome_probabilities already receives as
    #   pitcher_k_pct. It is NOT P_miss and NOT K/9. The fit converted the
    #   training set's per-9 rates with the SAME formula the model uses
    #   (PA = IP * 4.2), so the coefficient transfers EXACTLY, with zero
    #   transformations in between. A coefficient fitted on K/9 and applied to
    #   K% would be wrong by a factor of 2.6 -- silently.
    #
    # *** RECENT FORM IS SMALL, AND THE FIT SAYS SO. ***
    #   The bound artifact reports its exact share of the hitter signal. An
    #   earlier HITTER-ONLY fit found "the blend beats season-alone by +174
    #   logL" -- that was an ARTEFACT of the missing pitcher term. `recent` was
    #   standing in for variance the PITCHER explains. season and recent are
    #   ~0.98 correlated. The term is kept because it is fitted and costs
    #   nothing, but it is not a meaningful signal and is not claimed as one.
    use_fitted_kbb: bool = False
    kbb_k_intercept: Optional[float] = None
    kbb_k_hitter_season: Optional[float] = None
    kbb_k_hitter_recent: Optional[float] = None
    kbb_k_pitcher: Optional[float] = None
    kbb_bb_intercept: Optional[float] = None
    kbb_bb_hitter_season: Optional[float] = None
    kbb_bb_hitter_recent: Optional[float] = None
    kbb_bb_pitcher: Optional[float] = None

    # XBH mix among non-HR hits: league base shares (measured league values,
    # ~74% singles / 23.5% doubles / 2.5% triples of non-HR hits) tilted by
    # power (doubles) and speed (triples). These shape the mix ONLY — total
    # hit rate is fixed by xBA regardless of these values.
    xbh_double_share: float = 0.235
    xbh_triple_share: float = 0.025
    xbh_power_tilt: float = 0.12
    xbh_speed_tilt: float = 0.10

    # Context
    form_log_min: float = 0.75
    form_log_max: float = 1.30
    handedness_scale: float = 0.85
    park_hr_log_floor: float = 0.72

    # --- LEGACY fields (inert under the xBA hit model) -------------------
    # Retained so PASimulatorConfig(**asdict(cfg)) round-trips and stored
    # calibration state / overrides referencing them cannot crash. They no
    # longer influence output.
    bip_out_base_weight: float = 2.09
    single_base_weight: float = 0.60
    double_base_weight: float = 0.225
    triple_base_weight: float = 0.028
    out_weight_floor: float = 0.50
    out_contact_bonus: float = 0.03
    out_power_bonus: float = 0.03
    single_power_penalty: float = 0.55
    single_contact_bonus: float = 0.05
    double_power_bonus: float = 0.20
    triple_speed_bonus: float = 0.14
    single_weight_floor: float = 0.38
    double_weight_floor: float = 0.135
    triple_weight_floor: float = 0.008
    # ---------------------------------------------------------------------

    # Context scaling on BIP
    bvp_hr_weight: float = 0.26
    bvp_hit_weight: float = 0.21
    park_hits_weight: float = 0.35
    context_hit_scale_max: float = 1.12
    context_hit_scale_min: float = 0.88
    # Safety net only; under the xBA model the bounded target hit rate keeps
    # hit/PA below this by construction, so the cap should never fire.
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

        derived = cls(
            k_intercept=_logit(k_rate),
            bb_intercept=_logit(bb_rate),
            hr_intercept=_logit(hr_on_bip),
            bvp_hr_weight=1.30 * 0.20,
            bvp_hit_weight=0.26 * 0.80,
            hit_prob_cap=min(0.38, hit_rate_pa * 1.65),
            # contact_scale drives the K/BB logits only under the xBA model.
            contact_scale=1.0 / max(contact_anchor * 0.16, 0.01),
            # power drives HR and the XBH mix; it no longer touches hit rate,
            # so its scale can stay expressive without saturation risk.
            power_scale=1.0 / max(barrel_anchor * 4.0, 0.01),
            speed_scale=1.0 / max(barrel_anchor * 0.6, 0.01),
            pitcher_k_scale=max(league.k_pct, 1.0),
            pitcher_bb_scale=max(league.bb_pct, 1.0),
            # Feature scales map the realistic cross-player spread of each
            # metric to a latent of roughly +/-1, so the logit coefficients
            # (hr_hitter_power, k_quality, ...) act at their designed
            # magnitude. The previous 0.08 divisors produced latents of 8-9
            # for elite hitters, pinning their K at k_max and HR at hr_max
            # (Judge simulated at 41.6% K vs his real ~27%).
            xwoba_scale=1.0 / max(xwoba_anchor * 0.40, 0.01),
            xslg_scale=1.0 / max(xslg_anchor * 0.60, 0.01),
            barrel_scale=1.0 / max(barrel_anchor * 3.5, 0.01),
            hard_hit_scale=1.0 / max(league.hard_hit_rate * 0.50, 0.01),
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
    Hybrid PA simulator with an xBA-driven hit model.

    The sampling path (simulate) and the explicit path
    (expected_outcome_probabilities) share the same probability computation
    and agree by construction.
    """

    def __init__(
        self,
        config: Optional[PASimulatorConfig] = None,
        league_baselines: Optional[LeagueBaselines] = None,
        random_seed: Optional[int] = None,
    ):
        self.league = league_baselines or LeagueBaselines()
        self.config = config or PASimulatorConfig.from_league(self.league)
        if self.config.use_fitted_kbb:
            names = (
                "kbb_k_intercept", "kbb_k_hitter_season",
                "kbb_k_hitter_recent", "kbb_k_pitcher",
                "kbb_bb_intercept", "kbb_bb_hitter_season",
                "kbb_bb_hitter_recent", "kbb_bb_pitcher",
            )
            missing = [name for name in names
                       if getattr(self.config, name) is None]
            if missing:
                raise ValueError(
                    "use_fitted_kbb is enabled without artifact-loaded "
                    f"coefficients: {missing}"
                )
        if self.config.pitcher_hr9_effect_mode not in {
            "legacy_frozen", "corrected"
        }:
            raise ValueError(
                "pitcher_hr9_effect_mode must be 'legacy_frozen' or 'corrected'"
            )
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
        """Simulate one plate appearance by sampling the shared distribution."""
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
            rich_features=rich_features,
        )

        r = self.rng.random()
        cumulative = 0.0
        for outcome_key, description, kind in (
            ("strikeout", "Strikeout", "out"),
            ("walk", "Walk", "walk"),
            ("home_run", "Home Run", "home_run"),
            ("single", "Single", "single"),
            ("double", "Double", "double"),
            ("triple", "Triple", "triple"),
        ):
            cumulative += probs[outcome_key]
            if r < cumulative:
                return PAOutcome(kind, description)
        return PAOutcome("out", "Ball in play out")

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
        """Return explicit per-PA outcome probabilities (sums to 1)."""
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

        # P(HR | BIP): the logit's intercept is logit(hr_on_bip), so this is
        # conditional on ball in play by construction.
        hr_on_bip = self._calculate_hr_prob(
            latent,
            park_hr_factor,
            statcast=statcast,
            pitcher_hr_per_9=pitcher_hr_per_9,
            bvp_hr_factor=bvp_hr_factor,
        )
        # CONSISTENCY FIX: report per-PA HR as bip * P(HR|BIP) — matching what
        # sampling produces — instead of reporting the conditional as absolute.
        hr_prob = bip_prob * hr_on_bip
        non_hr_bip = max(0.0, bip_prob - hr_prob)

        p_out, p_single, p_double, p_triple = self._bip_outcome_distribution(
            latent,
            hr_on_bip=hr_on_bip,
            park_hits_factor=park_hits_factor,
            bvp_ops_factor=bvp_ops_factor,
        )

        result = {
            "strikeout": k_prob,
            "walk": bb_prob,
            "home_run": hr_prob,
            "single": non_hr_bip * p_single,
            "double": non_hr_bip * p_double,
            "triple": non_hr_bip * p_triple,
            "out_on_bip": non_hr_bip * p_out,
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
            rich_features=rich_features,
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

        if statcast is not None:
            validate_profile_and_rich_features(
                statcast,
                rich,
                context=f"HybridPASimulator[{statcast.player_id}]",
            )

        def _pick(key: str, statcast_value: Optional[float], league_value: float) -> float:
            """Prefer rich value, then statcast profile, then league anchor."""
            rich_value = rich.get(key)
            if rich_value is not None:
                return float(rich_value)
            if statcast_value is not None:
                return float(statcast_value)
            return league_value

        # Contact-conditional Statcast values center against the
        # contact-conditional league baselines (they are batted-ball averages).
        xwoba = _pick("xwoba", statcast.xwoba if statcast else None, lg.xwoba_on_contact)
        xslg = _pick("xslg", statcast.xslg if statcast else None, lg.xslg_on_contact)
        xba = _pick("xba", statcast.xba if statcast else None, lg.xba_on_contact)
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

        xwoba_boost = (xwoba - lg.xwoba_on_contact) * cfg.xwoba_scale
        power_boost = (xslg - lg.xslg_on_contact) * cfg.xslg_scale
        barrel_boost = (barrel - lg.barrel_rate) * cfg.barrel_scale
        hard_hit_boost = (hard_hit - lg.hard_hit_rate) * cfg.hard_hit_scale

        rolling_quality = 0.0
        roll_xwoba = rich.get("roll15_xwoba")
        if roll_xwoba is not None:
            recent_pa = float(rich.get("recent_pa_15") or 0.0)
            ramp = min(1.0, recent_pa / max(cfg.rolling_pa_ramp, 1.0))
            rolling_quality = (
                (float(roll_xwoba) - xwoba) * cfg.xwoba_scale
                * cfg.rolling_quality_weight * ramp
            )

        # Sample-size-aware shrinkage of the player's xBA toward the league
        # contact-conditional baseline (empirical-Bayes weight).
        sample_pa = float(statcast.sample_pa) if statcast and statcast.sample_pa else 0.0
        weight = sample_pa / (sample_pa + max(cfg.xba_shrinkage_pa, 1.0))
        legacy_xba_shrunk = weight * xba + (1.0 - weight) * lg.xba_on_contact
        xba_shrunk = consume_fitted_contact_xba(
            fitted_contact_xba=rich.get("contact_xba_fitted"),
            legacy_xba_shrunk=legacy_xba_shrunk,
        )

        # ------------------------------------------------------------------
        # The hitter's OWN K/BB rates, for the FITTED path.
        # ------------------------------------------------------------------
        # NaN when absent -> _calculate_k_bb_probs takes the LEGACY H_contact
        # path, byte-identically. Nothing changes unless BOTH use_fitted_kbb is
        # on AND the rates are present.
        def _rate01(v: Any, name: str) -> float:
            """Return a valid rate, NaN only when absent, and reject corruption."""
            if v is None or (isinstance(v, str) and not v.strip()):
                return float("nan")
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be numeric or absent; got {v!r}")
            if not math.isfinite(f) or not (0.0 <= f <= 1.0):
                raise ValueError(f"{name} must be finite and in [0, 1]; got {v!r}")
            return f

        k_season = _rate01(getattr(statcast, "k_rate", None), "k_rate") if statcast else float("nan")
        bb_season = _rate01(getattr(statcast, "bb_rate", None), "bb_rate") if statcast else float("nan")
        k_recent = _rate01(getattr(statcast, "k_rate_recent", None), "k_rate_recent") if statcast else float("nan")
        bb_recent = _rate01(getattr(statcast, "bb_rate_recent", None), "bb_rate_recent") if statcast else float("nan")

        # A missing RECENT rate falls back to the SEASON rate. That is not a
        # fudge and it is not arbitrary: the fitted model is
        #     a + b_s*season + b_r*recent
        # so setting recent := season gives  a + (b_s + b_r)*season -- a
        # well-defined single-predictor model with the SAME intercept. DROPPING
        # the recent term instead would silently shift the intercept and
        # mis-calibrate. And since b_r carries only 8% of the hitter signal
        # (measured), the substitution is nearly free.
        if math.isnan(k_recent):
            k_recent = k_season
        if math.isnan(bb_recent):
            bb_recent = bb_season

        if self.config.use_fitted_kbb:
            missing = [
                name for name, value in (
                    ("k_rate", k_season), ("bb_rate", bb_season),
                    ("k_rate_recent", k_recent), ("bb_rate_recent", bb_recent),
                ) if math.isnan(value)
            ]
            if missing:
                raise ValueError(
                    "fitted K/BB path requires hitter season rates; missing "
                    f"or unavailable: {missing}"
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
            "xba_contact": xba_shrunk,
            # FITTED-path inputs. The pitcher rates are the RAW percentages
            # (0-100) the caller passed in -- the SAME quantity the fit used.
            # NOT P_miss (a scaled latent) and NOT K/9.
            "k_season": k_season,
            "k_recent": k_recent,
            "bb_season": bb_season,
            "bb_recent": bb_recent,
            "pitcher_k_pct": float(pitcher_k_pct),
            "pitcher_bb_pct": float(pitcher_bb_pct),
        }

    def _calculate_k_bb_probs(self, latent: dict[str, float]) -> tuple[float, float]:
        """P(strikeout) and P(walk) for one plate appearance.

        FITTED PATH  (use_fitted_kbb AND the hitter's own rates are present):

            logit(K)  = kbb_k_intercept
                      + kbb_k_hitter_season  * k_season
                      + kbb_k_hitter_recent  * k_recent
                      + kbb_k_pitcher        * pitcher_k_pct
            logit(BB) = kbb_bb_intercept
                      + kbb_bb_hitter_season * bb_season
                      + kbb_bb_hitter_recent * bb_recent
                      + kbb_bb_pitcher       * pitcher_bb_pct

        The pitcher is IN the fitted coefficients -- NOT added on top of a
        hitter-only model, which would double-count him (the GLM's intercept
        already absorbs the average pitcher). See the note on PASimulatorConfig.

        NOTE what the fitted path does NOT use: H_contact, P_miss, form,
        H_quality, handedness. It is a REPLACEMENT, not an adjustment. Those
        latents remain for the HR model and for the legacy path.

        LEGACY PATH: the original H_contact formula, BYTE-IDENTICAL.
        """
        cfg = self.config

        use_fitted = bool(getattr(cfg, "use_fitted_kbb", False))

        k_s = latent.get("k_season", float("nan"))
        k_r = latent.get("k_recent", float("nan"))
        b_s = latent.get("bb_season", float("nan"))
        b_r = latent.get("bb_recent", float("nan"))
        p_k = latent.get("pitcher_k_pct", float("nan"))
        p_bb = latent.get("pitcher_bb_pct", float("nan"))

        have_k = use_fitted and not (math.isnan(k_s) or math.isnan(k_r)
                                     or math.isnan(p_k))
        have_bb = use_fitted and not (math.isnan(b_s) or math.isnan(b_r)
                                      or math.isnan(p_bb))
        if use_fitted and not (have_k and have_bb):
            raise ValueError(
                "fitted K/BB probability consumption received incomplete rates"
            )

        if have_k:
            k_logit = (
                cfg.kbb_k_intercept
                + cfg.kbb_k_hitter_season * k_s
                + cfg.kbb_k_hitter_recent * k_r
                + cfg.kbb_k_pitcher * p_k
            )
        else:
            k_logit = (
                cfg.k_intercept
                + cfg.k_pitcher_miss * latent["P_miss"]
                + cfg.k_hitter_contact * latent["H_contact"]
                + cfg.k_form * latent["form"]
                + cfg.k_quality * latent["H_quality"]
            )
        k_prob = self._clamp(self._sigmoid(k_logit), cfg.k_min, cfg.k_max)

        if have_bb:
            bb_logit = (
                cfg.kbb_bb_intercept
                + cfg.kbb_bb_hitter_season * b_s
                + cfg.kbb_bb_hitter_recent * b_r
                + cfg.kbb_bb_pitcher * p_bb
            )
        else:
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
        """P(home run | ball in play)."""
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
            if cfg.pitcher_hr9_effect_mode == "corrected":
                hr_logit += cfg.hr_pitcher_miss * pitcher_hr_skill
            else:
                # Frozen comparator behavior. New research candidates may not
                # claim this as a repaired pitcher-matchup feature.
                hr_logit -= cfg.hr_pitcher_miss * pitcher_hr_skill

        dist = statcast.distribution if statcast and statcast.distribution else None
        if dist and dist.sample_bip > 0:
            quality = dist.quality_score()
            hr_logit += cfg.hr_quality * quality * 1.3

            ev_boost = max(0.0, (dist.exit_velocity_mean - 88.0) / 12.0)
            la_penalty = abs(dist.launch_angle_mean - 20.0) / 30.0
            hr_logit += cfg.hr_hitter_power * (ev_boost - la_penalty * 0.28)

        return self._clamp(self._sigmoid(hr_logit), cfg.hr_min, cfg.hr_max)

    def _bip_outcome_distribution(
        self,
        latent: dict[str, float],
        hr_on_bip: float,
        park_hits_factor: float = 1.0,
        bvp_ops_factor: float = 1.0,
    ) -> tuple[float, float, float, float]:
        """
        (p_out, p_single, p_double, p_triple) conditional on a non-HR ball in
        play.

        The total hit rate on contact is the player's shrunk xBA, scaled by
        park/BvP context and the calibration lever, then bounded. Because xBA
        counts HR as hits, the non-HR hit rate is backed out of it. Power and
        speed shape only the mix among hits.
        """
        cfg = self.config

        hit_scale = self._context_hit_scale(park_hits_factor, bvp_ops_factor)
        target_hit_on_contact = self._clamp(
            latent["xba_contact"] * hit_scale * cfg.hit_rate_scale,
            cfg.hit_on_contact_min,
            cfg.hit_on_contact_max,
        )

        # xBA includes home runs; back out the non-HR hit rate.
        denom = max(1e-6, 1.0 - hr_on_bip)
        p_hit_non_hr = self._clamp(
            (target_hit_on_contact - hr_on_bip) / denom, 0.05, 0.45
        )

        # XBH mix among non-HR hits: power tilts doubles, speed tilts triples.
        double_share = cfg.xbh_double_share * (
            1.0 + self._clamp(latent["H_power"] * cfg.xbh_power_tilt, -0.5, 0.8)
        )
        triple_share = cfg.xbh_triple_share * (
            1.0 + self._clamp(latent["H_speed"] * cfg.xbh_speed_tilt, -0.5, 1.0)
        )
        double_share = min(double_share, 0.45)
        triple_share = min(triple_share, 0.08)
        single_share = max(0.30, 1.0 - double_share - triple_share)
        share_total = single_share + double_share + triple_share

        p_single = p_hit_non_hr * single_share / share_total
        p_double = p_hit_non_hr * double_share / share_total
        p_triple = p_hit_non_hr * triple_share / share_total
        p_out = max(0.0, 1.0 - p_hit_non_hr)

        return p_out, p_single, p_double, p_triple

    def _apply_hit_prob_cap(self, probs: dict[str, float]) -> dict[str, float]:
        """Safety net; the bounded xBA target keeps totals below the cap."""
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
