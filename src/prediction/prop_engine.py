"""
Prop projection engine (Final Form).

Converts PlayerFeatureBundle inputs into PropProjection outputs using a
consistent Probability Engine + Monte Carlo approach with strong output
safeguards. Designed for high precision on HR/HRR and future explainability.

FIX in this revision — pitcher K%/BB% unit scale:
project_pitcher_strikeouts() previously computed
    pitcher_k_pct = (k9 / 9.0) * 4.2 * 100.0
which MULTIPLIES by PA-per-inning instead of dividing — producing K% values
of 280–550% that pinned every pitcher at the simulator's k_max clamp, so
every starter projected the identical strikeout total regardless of skill
(verified: 5.0 K/9 and 13.0 K/9 both projected 9.62 K).
Correct conversion: K% = K per PA = (K/9 innings) / (PA/9 innings)
                       = (k9 / 9.0) / PA_PER_INNING * 100.

B3 (distributional pitcher K) — ADDITIVE:
project_pitcher_strikeouts() now attaches a MonteCarloResult around the
EXISTING point estimate instead of shipping simulation=None. The point value
the live model ships (projected_value) is UNCHANGED — the distribution's mean
is pinned to it by identity, so this is collection-safe and does NOT fork
model_version. Under the model's own logic each of the (fixed) batters_faced
is an i.i.d. Bernoulli(k_prob) draw, i.e. K ~ Binomial(n, p); we ship the
Poisson/NB survival with mean = projected_k as the analytic distribution and
expose a dispersion knob (pitcher_k_dispersion, default 0.0 = Poisson limit).
Genuine batters-faced variance (short-outing bias) is B4's role-aware
expected_innings — a GATED live-model change — not this wrap. The fitted
dispersion of the simulator's own K residuals is a B4 MOTIVATOR, not a knob to
turn here; keep the shipped default at the Poisson limit.
"""

from __future__ import annotations

from dataclasses import fields, replace

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Optional

from src.data.mlb_api import PitchingStatsSnapshot
from src.models.dataclasses import (
    LeagueBaselines,
    MonteCarloResult,
    PitcherGameContext,
    PlayerFeatureBundle,
    PropCategory,
    PropProjection,
)
from src.evaluation.output_safeguards import OutputSafeguards
from src.features.pitcher_matchup_gate import resolve_pitcher_probability_inputs
from src.features.pa_volume_gate import resolve_pa_volume_inputs
from src.simulation.game_simulator import GameSimulator, GameSimulatorInput
from src.simulation.monte_carlo import FantasyScoring, MonteCarloEngine
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig
from src.simulation.probability_engine import ProbabilityEngine
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Average plate appearances per inning (≈ team PA per game / 9).
PA_PER_INNING = 4.2

# Strikeout prop lines the gate scores against (B3 KICKOFF: 4.5 / 5.5 / 6.5).
# Half-integer by construction so P(K >= line) is unambiguous (no push).
K_GATE_LINES: tuple[float, ...] = (4.5, 5.5, 6.5)


def rate_per_9_to_pct(rate_per_9: float) -> float:
    """Convert a per-9-innings rate (K/9, BB/9) to a per-PA percentage."""
    return (rate_per_9 / 9.0) / PA_PER_INNING * 100.0


_KBB_COEFFICIENT_FIELDS = {
    "kbb_k_intercept",
    "kbb_k_hitter_season",
    "kbb_k_hitter_recent",
    "kbb_k_pitcher",
    "kbb_bb_intercept",
    "kbb_bb_hitter_season",
    "kbb_bb_hitter_recent",
    "kbb_bb_pitcher",
}


def _load_hash_bound_kbb(block: dict[str, Any]) -> dict[str, float]:
    """Load the only coefficient source permitted by the fitted K/BB path."""

    path_raw = block.get("kbb_artifact_path")
    expected = str(block.get("kbb_artifact_sha256") or "").lower()
    if not path_raw or len(expected) != 64:
        raise ValueError(
            "pa_simulator.use_fitted_kbb requires kbb_artifact_path and a "
            "64-character kbb_artifact_sha256; embedded defaults are not an "
            "acceptable fitted-model provenance source"
        )
    path = Path(str(path_raw))
    if not path.exists():
        raise ValueError(f"K/BB artifact does not exist: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(
            f"K/BB artifact hash mismatch for {path}: expected {expected}, got {actual}"
        )
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    fits = raw.get("fits") or {}
    k = fits.get("K") or {}
    bb = fits.get("BB") or {}
    mapped = {
        "kbb_k_intercept": k.get("intercept"),
        "kbb_k_hitter_season": k.get("hitter_season"),
        "kbb_k_hitter_recent": k.get("hitter_recent"),
        "kbb_k_pitcher": k.get("pitcher"),
        "kbb_bb_intercept": bb.get("intercept"),
        "kbb_bb_hitter_season": bb.get("hitter_season"),
        "kbb_bb_hitter_recent": bb.get("hitter_recent"),
        "kbb_bb_pitcher": bb.get("pitcher"),
    }
    invalid = [name for name, value in mapped.items()
               if isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(float(value))]
    if invalid:
        raise ValueError(f"K/BB artifact has missing/non-finite coefficients: {invalid}")
    return {name: float(value) for name, value in mapped.items()}


# ---------------------------------------------------------------------------
# Analytic count survival (pure-Python; no scipy dependency).
#
# The simulator treats each of n = batters_faced as an i.i.d. Bernoulli(k_prob)
# draw with FIXED n, so K ~ Binomial(n, p) under the model's own assumptions.
# We ship the Poisson limit (a -> 0) by default; a > 0 gives a Negative-
# Binomial with the SAME mean and Var = mean + a * mean^2, as a config knob
# for B4 (random batters-faced) without changing the shipped point value.
# ---------------------------------------------------------------------------


def _poisson_pmf(k: int, mu: float) -> float:
    if mu <= 0.0:
        return 1.0 if k == 0 else 0.0
    return math.exp(k * math.log(mu) - mu - math.lgamma(k + 1))


def _poisson_cdf(k: int, mu: float) -> float:
    """P(K <= k) for integer k >= 0."""
    if k < 0:
        return 0.0
    total = 0.0
    for i in range(0, k + 1):
        total += _poisson_pmf(i, mu)
    return min(1.0, total)


def _nbinom_pmf(k: int, r: float, p: float) -> float:
    # P(K = k) = C(k + r - 1, k) * p^r * (1 - p)^k, mean = r (1 - p) / p.
    if k < 0:
        return 0.0
    log_coeff = math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
    return math.exp(log_coeff + r * math.log(p) + k * math.log1p(-p))


def _nbinom_cdf(k: int, r: float, p: float) -> float:
    if k < 0:
        return 0.0
    total = 0.0
    for i in range(0, k + 1):
        total += _nbinom_pmf(i, r, p)
    return min(1.0, total)


def k_count_distribution(
    mean_k: float,
    lines: tuple[float, ...] = K_GATE_LINES,
    dispersion: float = 0.0,
) -> tuple[dict[float, float], float, float, float, float]:
    """
    Analytic strikeout-count distribution with mean pinned to `mean_k`.

    dispersion (a): Var = mean + a * mean^2. a <= 0 is the Poisson limit
    (the fixed-batters-faced baseline the live model implies). Returns
    (p_ge_threshold, median, p10, p90, mean).

    KEY CONVENTION (matches the hitter MonteCarloEngine + gate harness):
    p_ge_threshold is keyed by the INTEGER count threshold as a float —
    float(ceil(line)) — NOT by the half-integer betting line. Betting line
    4.5 -> key 5.0 = P(K >= 5). This is exactly how hitter categories store
    thresholds ({1.0: ..., 2.0: ...}), so run_gate_reconstruct.py's
    _p_over_from_projection (which probes p_ge_threshold[float(ceil(L))])
    works for K with zero harness changes. One convention, one ruler.
    """
    mean_k = max(0.0, float(mean_k))

    if dispersion <= 1e-9 or mean_k <= 0.0:
        cdf = lambda k: _poisson_cdf(k, mean_k)  # noqa: E731
    else:
        r = 1.0 / dispersion
        p = r / (r + mean_k)
        cdf = lambda k: _nbinom_cdf(k, r, p)  # noqa: E731

    p_ge: dict[float, float] = {}
    for ln in lines:
        need = math.ceil(ln)  # P(K >= 4.5) == P(K >= 5) == 1 - P(K <= 4)
        p_ge[float(need)] = float(max(0.0, min(1.0, 1.0 - cdf(need - 1))))

    def quantile(q: float) -> float:
        # Smallest integer k with CDF(k) >= q. Bounded search; K counts are small.
        k = 0
        cap = int(mean_k * 6) + 50
        while k < cap and cdf(k) < q:
            k += 1
        return float(k)

    median = quantile(0.50)
    p10 = quantile(0.10)
    p90 = quantile(0.90)
    return p_ge, median, p10, p90, mean_k


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
        self._pa_config = self._build_pa_config()

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
            # Rebuild from the new league anchor *and* re-consume the exact
            # hash-bound config block.  Rebuilding from league alone silently
            # discarded fitted K/BB and every other pa_simulator override when
            # the Statcast layer refreshed xwOBA/xSLG anchors.
            self._pa_config = self._build_pa_config()

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

    def _build_pa_config(self) -> PASimulatorConfig:
        """PASimulatorConfig from the league baselines, THEN overlaid with any
        `pa_simulator` block in the config dict.

        *** THIS BRIDGE DID NOT EXIST, AND ITS ABSENCE IS THE B4 FAILURE MODE. ***

        PropEngine built the PA config with PASimulatorConfig.from_league(league)
        -- league baselines ONLY -- and handed it straight to HybridPASimulator.
        So the `pa_simulator` block in config.json WAS NEVER READ. A config could
        set pa_simulator.use_fitted_kbb = True, fork the model_version hash, pass
        every offline check, and change NOTHING in the simulator.

        MEASURED, and this is exactly what happened: with config.kbb.json loaded,
        `use_fitted_kbb` came back FALSE on the live PASimulatorConfig while the
        StatcastProfile rates were populated correctly (sd 0.0703, up from
        0.0000). The simulator had the data and ignored it. The gate smoke showed
        `hits` drift of 0.00566 against a measured noise floor of 0.00563 -- i.e.
        EXACTLY ZERO effect, dressed up as a tiny one.

        That is not a tie. It is a plumbing failure, and it is the same seam that
        silently nulled the B4 gate.

        DEGENERATE WHEN ABSENT: with no `pa_simulator` block, this returns
        exactly PASimulatorConfig.from_league(league) -- byte-identical to the
        previous behaviour. Unknown output-affecting keys fail closed; a typo
        must not be allowed to create decorative configuration provenance.
        """
        base = PASimulatorConfig.from_league(self.league)
        block = self.config.get("pa_simulator") or {}
        if not isinstance(block, dict) or not block:
            return base

        use_fitted_kbb = bool(block.get("use_fitted_kbb", False))
        artifact_keys = {"kbb_artifact_path", "kbb_artifact_sha256"}
        direct_coefficients = sorted(_KBB_COEFFICIENT_FIELDS.intersection(block))
        if use_fitted_kbb and direct_coefficients:
            raise ValueError(
                "fitted K/BB coefficients may not be supplied directly in config; "
                f"use the hash-bound artifact only (found {direct_coefficients})"
            )
        if not use_fitted_kbb and artifact_keys.intersection(block):
            raise ValueError(
                "K/BB artifact metadata is present while use_fitted_kbb is false; "
                "refusing decorative provenance that the simulator would not consume"
            )

        valid = {f.name for f in fields(PASimulatorConfig)}
        overrides: dict[str, Any] = {}
        unknown: list[str] = []
        for k, v in block.items():
            if k.startswith("_"):          # _comment, _note, ...
                continue
            if k in artifact_keys:
                continue
            if k in valid:
                overrides[k] = v
            else:
                unknown.append(k)

        if unknown:
            raise ValueError(
                "config['pa_simulator'] contains unknown probability fields: "
                f"{sorted(unknown)}"
            )
        if use_fitted_kbb:
            overrides.update(_load_hash_bound_kbb(block))
        if not overrides:
            return base

        logger.info(
            "PASimulatorConfig overridden from config['pa_simulator']: %s",
            {k: overrides[k] for k in sorted(overrides)},
        )
        return replace(base, **overrides)

    def _build_monte_carlo(self) -> MonteCarloEngine:
        # A seed is structural artifact provenance, not a fitted model knob.
        # When absent, preserve the live model's historical stochastic behavior;
        # when supplied by a gate, make its probability artifact reproducible.
        raw_seed = (self.config.get("simulation", {}) or {}).get("random_seed")
        if raw_seed is None:
            random_seed = None
        else:
            if isinstance(raw_seed, bool):
                raise ValueError("simulation.random_seed must be an integer, not a boolean")
            try:
                random_seed = int(raw_seed)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"simulation.random_seed must be an integer, got {raw_seed!r}"
                ) from exc
        game_simulator = GameSimulator(
            pa_simulator=HybridPASimulator(
                config=self._pa_config,
                league_baselines=self.league,
            ),
            league_baselines=self.league,
            config=self.config,
        )
        return MonteCarloEngine(
            game_simulator=game_simulator,
            league_baselines=self.league,
            fantasy_scoring=self._fantasy_scoring,
            random_seed=random_seed,
        )

    def project_hitter(
        self,
        bundle: PlayerFeatureBundle,
        categories: Optional[tuple[PropCategory, ...]] = None,
    ) -> list[PropProjection]:
        """Return one PropProjection per requested category for a hitter."""
        cats = categories or self.HITTER_CATEGORIES
        if "total_bases" in cats:
            status = (self.config.get("total_bases") or {}).get("status")
            if status not in {"candidate_unpromoted", "promoted"}:
                raise ValueError(
                    "total_bases is candidate-only: pass an explicit provenance "
                    "config with total_bases.status='candidate_unpromoted' to a gate, "
                    "or a future promoted status after its market gate passes"
                )

        rich_features = bundle.metadata.get("rich_features", {})
        # This is a factual record of the exact feature bundle consumed below.
        # It must not affect rates, PA sampling, ranking, or simulation seeds.
        from src.evaluation.prediction_health import health_for_bundle

        input_health = health_for_bundle(bundle)

        sim_input = self._bundle_to_sim_input(bundle, rich_features=rich_features)
        projections: list[PropProjection] = []

        outcome_probs = self.probability_engine.from_bundle(bundle, rich_features=rich_features)

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
                mlb_game_pk=bundle.hitter.game.game_pk,
                input_health_flags=input_health.flags,
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

        B3: attaches an analytic strikeout distribution (simulation != None)
        whose mean is pinned to the existing point estimate. ADDITIVE — the
        shipped projected_value is unchanged; model_version does not fork.
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

        # FIX: per-9 rate -> per-PA percentage (divide by PA/inning, don't multiply).
        pitcher_k_pct = rate_per_9_to_pct(regressed_k9)
        bb_per_9 = recent_stats.bb_per_9 or season_stats.bb_per_9
        if bb_per_9 is not None:
            pitcher_bb_pct = rate_per_9_to_pct(bb_per_9)
        else:
            pitcher_bb_pct = self.league.bb_pct

        expected_ip = pitcher.expected_innings
        batters_faced = expected_ip * PA_PER_INNING

        pa_sim = HybridPASimulator(
            config=self.pa_config,
            league_baselines=self.league,
        )
        rates = pa_sim.expected_rates(
            pitcher_k_pct=pitcher_k_pct,
            pitcher_bb_pct=pitcher_bb_pct,
            park_hr_factor=1.0,
        )
        # projected_k uses the (already-clamped) k_prob returned by the PA sim;
        # the distribution mean is pinned to THIS value, clamp and all, so the
        # distribution is consistent with the exact point the live model ships.
        projected_k = rates["k_prob"] * batters_faced

        confidence = self._pitcher_confidence(recent_stats, season_stats)

        simulation = self._build_k_simulation(projected_k)

        return PropProjection(
            player_id=pitcher.player.mlb_id,
            player_name=pitcher.player.name,
            category="strikeouts",
            game_date=pitcher.game.game_date,
            projected_value=round(projected_k, 2),
            confidence=confidence,
            simulation=simulation,
            mlb_game_pk=pitcher.game.game_pk,
            input_health_flags=("not_assessed_for_pitcher",),
        )

    def _build_k_simulation(self, projected_k: float) -> MonteCarloResult:
        """
        Analytic strikeout distribution pinned to the point estimate.

        n_sims=0 flags this as analytic (not Monte-Carlo sampled). The mean
        equals projected_k by identity, keeping the wrap additive. dispersion
        defaults to the Poisson limit; pitcher_k_dispersion in config can widen
        it (reserved for B4's random batters-faced model — leave at 0.0 during
        collection).

        p_ge_threshold keys are float(ceil(line)) — {5.0, 6.0, 7.0} for the
        gate lines 4.5/5.5/6.5 — matching the hitter/gate integer-threshold
        convention (see k_count_distribution docstring).
        """
        dispersion = float(self.config.get("pitcher_k_dispersion", 0.0))
        p_ge, median, p10, p90, mean_k = k_count_distribution(
            projected_k,
            lines=K_GATE_LINES,
            dispersion=dispersion,
        )
        return MonteCarloResult(
            n_sims=0,
            category="strikeouts",
            mean=mean_k,
            median=median,
            p10=p10,
            p90=p90,
            p_ge_threshold=p_ge,
        )

    def _bundle_to_sim_input(
        self,
        bundle: PlayerFeatureBundle,
        rich_features: Optional[dict] = None,
    ) -> GameSimulatorInput:
        """Build simulation input, including rich features when available."""
        pitcher = resolve_pitcher_probability_inputs(
            bundle,
            mode=self.pa_config.pitcher_context_identity_mode,
            league=self.league,
        )

        weather_hr = float(bundle.metadata.get("weather_hr_factor", 1.0))
        umpire_k_bias = 0.0
        if bundle.umpire:
            umpire_k_bias = bundle.umpire.k_bias

        # Clamp game-level multipliers to realistic ranges. Small-sample BvP
        # and streaky recent-form values can arrive far from 1.0 and stack
        # multiplicatively, inflating HRR well past any real expectation.
        # (The PA simulator already clamps its own latent inputs; these bound
        # the game-level knobs the simulator trusts as pre-vetted.)
        def _clamp(x: float, lo: float, hi: float) -> float:
            return max(lo, min(hi, x))

        bvp_ops = _clamp(pitcher.bvp_ops_factor, 0.80, 1.25)
        bvp_hr = _clamp(pitcher.bvp_hr_factor, 0.70, 1.40)
        form_mult = _clamp(bundle.matchup.recent_form_multiplier, 0.85, 1.18)

        pa_volume = resolve_pa_volume_inputs(
            bundle,
            mode=(self.config.get("base_running") or {}).get(
                "pa_volume_identity_mode", "legacy_frozen"
            ),
        )

        return GameSimulatorInput(
            expected_pa=bundle.expected_pa,
            lineup_slot=pa_volume.lineup_slot,
            pitcher_k_pct=pitcher.pitcher_k_pct,
            pitcher_bb_pct=pitcher.pitcher_bb_pct,
            park_hr_factor=bundle.park.hr_factor,
            park_hits_factor=bundle.park.hits_factor,
            weather_hr_factor=weather_hr,
            umpire_k_bias=umpire_k_bias,
            handedness_advantage=pitcher.handedness_advantage,
            recent_form_mult=form_mult,
            bvp_ops_factor=bvp_ops,
            bvp_hr_factor=bvp_hr,
            statcast=bundle.statcast,
            pitcher_hr_per_9=pitcher.pitcher_hr_per_9,
            rich_features=rich_features,
            pa_volume_status=pa_volume.status,
            pa_volume_authorization_sha256=pa_volume.authorization_sha256,
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
