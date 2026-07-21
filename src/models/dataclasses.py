"""
Shared domain types for the MLB prediction system.

All layers (data, features, simulation, prediction, evaluation, learning)
import from here to avoid circular dependencies and keep contracts stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Literal, Optional


# ---------------------------------------------------------------------------
# League baselines (configurable; defaults approximate recent MLB averages)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeagueBaselines:
    """
    League-average reference rates used to center latent skill models.
    Values should be loaded from config or recalibrated by the evaluation layer.
    """

    season: int = 2025
    k_pct: float = 22.5
    bb_pct: float = 8.5
    xwoba: float = 0.320
    xslg: float = 0.410
    # Contact-conditional baselines: the average of Statcast's
    # estimated_woba/slg_using_speedangle over BATTED-BALL events only (these
    # columns are NaN on non-contact pitches, so a per-BBE mean is contact-
    # conditional and sits well above the season-level xwoba/xslg above).
    # StatcastProfile.xwoba/xslg are built this way, so the simulator must
    # center them against THESE, not the season values.
    xwoba_on_contact: float = 0.370
    xslg_on_contact: float = 0.620
    # League average of estimated_ba_using_speedangle over batted balls.
    # Primary driver of the hit model; self-calibrated from each Statcast pull.
    xba_on_contact: float = 0.320
    barrel_rate: float = 0.085
    hard_hit_rate: float = 0.390
    sweet_spot_rate: float = 0.340
    whiff_rate: float = 0.245
    chase_rate: float = 0.285
    contact_rate: float = 0.755
    swing_rate: float = 0.470
    zone_rate: float = 0.480
    hr_per_9: float = 1.10
    hits_per_game: float = 0.85
    pa_per_game: float = 4.05

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> LeagueBaselines:
        league = config.get("league_avg", {})
        season = int(config.get("season", 2025))
        return cls(
            season=season,
            k_pct=float(league.get("k_pct", 22.5)),
            bb_pct=float(league.get("bb_pct", 8.5)),
            xwoba=float(league.get("xwoba", 0.320)),
            xslg=float(league.get("xslg", 0.410)),
            xwoba_on_contact=float(league.get("xwoba_on_contact", 0.370)),
            xslg_on_contact=float(league.get("xslg_on_contact", 0.620)),
            xba_on_contact=float(league.get("xba_on_contact", 0.320)),
            barrel_rate=float(league.get("barrel_rate", 0.085)),
            hard_hit_rate=float(league.get("hard_hit_rate", 0.390)),
            sweet_spot_rate=float(league.get("sweet_spot_rate", 0.340)),
            whiff_rate=float(league.get("whiff_rate", 0.245)),
            chase_rate=float(league.get("chase_rate", 0.285)),
            contact_rate=float(league.get("contact_rate", 0.755)),
            swing_rate=float(league.get("swing_rate", 0.470)),
            zone_rate=float(league.get("zone_rate", 0.480)),
            hr_per_9=float(league.get("hr_per_9", 1.1)),
            hits_per_game=float(league.get("hits_per_game", 0.85)),
            pa_per_game=float(league.get("pa_per_game", 4.05)),
        )


# ---------------------------------------------------------------------------
# Identity & schedule context
# ---------------------------------------------------------------------------


Handedness = Literal["L", "R", "S", "U"]
LineupStatus = Literal["confirmed", "projected", "unknown"]
PropCategory = Literal[
    "hits",
    "hrr",
    "home_runs",
    "fantasy",
    "strikeouts",
    "total_bases",
]


@dataclass(frozen=True)
class PlayerIdentity:
    mlb_id: int
    name: str
    team: str
    bats: Handedness = "U"
    throws: Handedness = "U"


@dataclass(frozen=True)
class GameContext:
    game_pk: int
    game_date: str
    venue: str
    is_home: bool
    opponent: str
    lineup_status: LineupStatus = "unknown"


@dataclass
class HitterGameContext:
    player: PlayerIdentity
    game: GameContext
    lineup_slot: int
    opposing_pitcher_id: Optional[int] = None
    opposing_pitcher_name: str = ""
    opposing_pitcher_throws: Handedness = "U"


@dataclass
class PitcherGameContext:
    player: PlayerIdentity
    game: GameContext
    expected_innings: float = 5.5


# ---------------------------------------------------------------------------
# Statcast / Savant feature bundle
# ---------------------------------------------------------------------------


@dataclass
class StatcastDistributionProfile:
    """
    Batted-ball distribution features for non-linear HR/hit modeling.

    Built from pitch-level Statcast; used by HybridPASimulator for quality-based
    power adjustments beyond scalar rate averages.
    """

    launch_angle_mean: float = 12.0
    launch_angle_std: float = 18.0
    exit_velocity_mean: float = 88.0
    exit_velocity_std: float = 10.0
    max_exit_velocity: float = 105.0
    sweet_spot_rate: float = 0.34
    barrel_rate: float = 0.085
    hard_hit_rate: float = 0.39
    sample_bip: int = 0
    batted_ball_denominator: Optional[int] = None
    barrel_count: Optional[int] = None
    hard_hit_count: Optional[int] = None
    batted_ball_rate_definition: Optional[str] = None

    def quality_score(self) -> float:
        """Composite batted-ball quality in [0, ~1] from distribution features."""
        if self.sample_bip <= 0:
            return 0.0
        ev_signal = max(0.0, (self.exit_velocity_mean - 85.0) / 20.0)
        sweet = min(1.0, self.sweet_spot_rate / 0.40)
        barrel = min(1.0, self.barrel_rate / 0.15)
        hard = min(1.0, self.hard_hit_rate / 0.50)
        return round(0.35 * barrel + 0.30 * sweet + 0.20 * hard + 0.15 * ev_signal, 4)


@dataclass
class StatcastProfile:
    """Per-player advanced metrics; rates are fractions (0–1) unless noted."""

    player_id: int
    player_name: str
    sample_pa: int = 0
    xwoba: Optional[float] = None
    xba: Optional[float] = None
    xslg: Optional[float] = None
    barrel_rate: Optional[float] = None
    sweet_spot_rate: Optional[float] = None
    hard_hit_rate: Optional[float] = None
    batted_ball_denominator: Optional[int] = None
    barrel_count: Optional[int] = None
    hard_hit_count: Optional[int] = None
    batted_ball_rate_definition: Optional[str] = None
    avg_exit_velocity: Optional[float] = None
    avg_launch_angle: Optional[float] = None
    chase_rate: Optional[float] = None
    contact_rate: Optional[float] = None
    whiff_rate: Optional[float] = None
    swing_rate: Optional[float] = None
    zone_rate: Optional[float] = None
    k_rate: Optional[float] = None
    bb_rate: Optional[float] = None
    # RECENT (lastXGames) K/BB rates. ADDITIVE -- default None, so an existing
    # caller that does not set them is unaffected and the simulator's fitted
    # K/BB path stays inert.
    #
    # These exist because the FITTED K/BB model is a BLEND of season-to-date and
    # recent form, and the blend BEAT season-alone out-of-sample by +174 logL (K)
    # and +47 (BB) on 141,731 starter-games. Season is stable but STALE (it
    # includes April, when the hitter may have been a different player); recent is
    # current but NOISY (51 PA vs 208). The data chose the blend.
    k_rate_recent: Optional[float] = None
    bb_rate_recent: Optional[float] = None
    distribution: Optional[StatcastDistributionProfile] = None

    def has_advanced_data(self) -> bool:
        return self.sample_pa > 0 and self.xwoba is not None


@dataclass
class PitcherStatcastProfile:
    player_id: int
    player_name: str
    sample_pa: int = 0
    k_rate: Optional[float] = None
    bb_rate: Optional[float] = None
    whiff_rate: Optional[float] = None
    chase_rate: Optional[float] = None
    barrel_rate_allowed: Optional[float] = None
    xwoba_allowed: Optional[float] = None
    hr_per_9: Optional[float] = None


# ---------------------------------------------------------------------------
# Context features (weather, park, umpire — populated in Phase 2+)
# ---------------------------------------------------------------------------


@dataclass
class ParkFactors:
    venue: str
    hits_factor: float = 1.0
    hr_factor: float = 1.0
    runs_factor: float = 1.0
    k_factor: float = 1.0


@dataclass
class WeatherContext:
    venue: str
    game_date: str
    temperature_f: float = 72.0
    wind_mph: float = 5.0
    wind_direction_deg: Optional[float] = None
    precip_probability: float = 0.0
    is_dome: bool = False


@dataclass
class UmpireContext:
    umpire_id: Optional[int] = None
    name: str = ""
    k_bias: float = 0.0
    runs_bias: float = 0.0


@dataclass
class InjuryStatus:
    player_id: int
    status: str
    is_active: bool = True
    note: str = ""


@dataclass
class MatchupContext:
    """Platoon, BvP, and handedness context for a hitter–pitcher pair."""

    platoon_advantage: float = 0.0
    bvp_pa: int = 0
    bvp_ops_factor: float = 1.0
    recent_form_multiplier: float = 1.0
    platoon_ops_z: float = 0.0
    platoon_slg_z: float = 0.0
    bvp_hr_factor: float = 1.0
    pitcher_archetype_similarity: float = 0.0
    handedness_split_ops: float = 0.0


@dataclass
class FeatureVector:
    """
    Rich engineered feature set for modeling and future explainability.

    Values are league-centered z-scores, ratios, and interaction terms.
    Groups map feature names to semantic categories for contribution breakdowns.
    """

    values: dict[str, float] = field(default_factory=dict)
    groups: dict[str, list[str]] = field(default_factory=dict)

    def count(self) -> int:
        return len(self.values)

    def group_values(self, group: str) -> dict[str, float]:
        keys = self.groups.get(group, [])
        return {k: self.values[k] for k in keys if k in self.values}


@dataclass
class OutcomeProbabilities:
    """Explicit per-PA outcome probabilities (sum ≈ 1.0)."""

    strikeout: float
    walk: float
    home_run: float
    single: float
    double: float
    triple: float
    out_on_bip: float

    def total(self) -> float:
        return (
            self.strikeout
            + self.walk
            + self.home_run
            + self.single
            + self.double
            + self.triple
            + self.out_on_bip
        )

    def is_valid(self, tolerance: float = 0.02) -> bool:
        return abs(self.total() - 1.0) <= tolerance

    @property
    def hit_prob(self) -> float:
        return self.single + self.double + self.triple + self.home_run

    @property
    def hrr_prob(self) -> float:
        return self.hit_prob + self.walk

    def to_dict(self) -> dict[str, float]:
        return {
            "strikeout": self.strikeout,
            "walk": self.walk,
            "home_run": self.home_run,
            "single": self.single,
            "double": self.double,
            "triple": self.triple,
            "out_on_bip": self.out_on_bip,
            "hit_prob": self.hit_prob,
            "hrr_prob": self.hrr_prob,
        }


@dataclass
class PlayerFeatureBundle:
    """Unified feature row consumed by prediction and simulation layers."""

    hitter: HitterGameContext
    statcast: StatcastProfile
    park: ParkFactors
    weather: WeatherContext
    matchup: MatchupContext
    umpire: Optional[UmpireContext] = None
    injury: Optional[InjuryStatus] = None
    pitcher_statcast: Optional[PitcherStatcastProfile] = None
    expected_pa: float = 4.05
    features: Optional[FeatureVector] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # === NEW: Rich features from src/features/ml/ ===
    rich_features: Optional[dict[str, Any]] = None

    def __post_init__(self):
        # Backward compatible: if rich_features not passed directly,
        # try to pull it from metadata (supports current integration)
        if self.rich_features is None:
            self.rich_features = self.metadata.get("rich_features")


# ---------------------------------------------------------------------------
# Simulation inputs & outputs
# ---------------------------------------------------------------------------


PAOutcomeType = Literal["out", "walk", "single", "double", "triple", "home_run"]


@dataclass(frozen=True)
class PAOutcome:
    outcome: PAOutcomeType
    description: str = ""

    @property
    def is_strikeout(self) -> bool:
        return self.outcome == "out" and "strikeout" in self.description.lower()


@dataclass
class PlateAppearanceRates:
    """Observable rate targets for a single PA (sums to ~1 before BIP split)."""

    k_prob: float
    bb_prob: float
    hr_prob_on_contact: float
    bip_prob: float


@dataclass
class GameSimulationResult:
    """Single simulated game line for one hitter."""

    plate_appearances: int
    hits: int
    singles: int
    doubles: int
    triples: int
    home_runs: int
    runs: int
    rbi: int
    walks: int
    strikeouts: int

    @property
    def hrr(self) -> int:
        return self.hits + self.runs + self.rbi

    @property
    def total_bases(self) -> int:
        """Official batting total bases: 1B + 2*2B + 3*3B + 4*HR."""
        return self.singles + 2 * self.doubles + 3 * self.triples + 4 * self.home_runs


@dataclass
class MonteCarloResult:
    """Aggregated distribution from N game simulations."""

    n_sims: int
    category: PropCategory
    mean: float
    median: float
    p10: float
    p90: float
    p_ge_threshold: dict[float, float] = field(default_factory=dict)
    per_game_samples: Optional[list[GameSimulationResult]] = None


# ---------------------------------------------------------------------------
# Prediction & +EV edge
# ---------------------------------------------------------------------------


class EdgeRecommendation(str, Enum):
    STRONG_OVER = "strong_over"
    LEAN_OVER = "lean_over"
    PASS = "pass"
    LEAN_UNDER = "lean_under"
    STRONG_UNDER = "strong_under"


@dataclass
class PropProjection:
    player_id: int
    player_name: str
    category: PropCategory
    game_date: str
    projected_value: float
    confidence: float
    simulation: Optional[MonteCarloResult] = None
    outcome_probs: Optional[OutcomeProbabilities] = None
    team: str = ""
    opponent: str = ""
    opposing_pitcher: str = ""
    lineup_status: LineupStatus = "unknown"
    # MLB's stable game identity.  Optional only so generic/live callers that
    # construct projections without a historical game context remain compatible.
    # Historical scoring requires this to be populated.
    mlb_game_pk: Optional[int] = None
    # Observable feature/fallback facts, copied from the exact bundle consumed
    # by the hitter simulator.  This is intentionally not a confidence score.
    input_health_flags: tuple[str, ...] = ()


@dataclass
class OddsLine:
    player_name: str
    category: PropCategory
    line: float
    over_odds_american: int
    under_odds_american: int
    sportsbook: str = ""


@dataclass
class EdgeResult:
    player_name: str
    category: PropCategory
    line: float
    projected_value: float
    implied_prob_over: float
    model_prob_over: float
    edge_pct: float
    recommendation: EdgeRecommendation
    confidence: float
    # De-vig + staking (added for the value engine). Defaulted so any existing
    # construction/deserialization keeps working.
    fair_prob_over: float = 0.0        # book's no-vig implied prob for the over
    vig_pct: float = 0.0               # book's margin on this market
    edge_side: str = ""                # "over" or "under" — the side with +edge
    model_prob_side: float = 0.0       # model prob on the edge side
    fair_prob_side: float = 0.0        # no-vig implied prob on the edge side
    payout_odds_american: int = 0      # odds you'd actually bet at (the edge side)
    # Expected PROFIT per unit at the actual posted price.  This is distinct
    # from the de-vigged model-vs-market gap used for capture measurement.
    expected_profit_per_unit: float = 0.0
    sportsbook: str = ""               # price venue; required for scoped authorization
    kelly_fraction: float = 0.0        # fractional-Kelly stake (bankroll fraction)
    # A research signal is not a betting instruction.  The daily pipeline
    # pins these fields to the market-policy artifact before anything reaches
    # the CLI, archive, or GUI.  Defaults fail closed for manually constructed
    # legacy results too.
    actionable: bool = False
    market_status: str = "RESEARCH_ONLY"
    notes: list[str] = field(default_factory=list)


@dataclass
class DailyPrediction:
    game_date: date
    hitter_projections: list[PropProjection] = field(default_factory=list)
    pitcher_projections: list[PropProjection] = field(default_factory=list)
    value_plays: list[EdgeResult] = field(default_factory=list)
    market_status: str = "RESEARCH_ONLY"
    market_policy_sha256: Optional[str] = None
    market_policy_reason: str = "No market authorization was evaluated."
    # Optional because older archives predate the hard-keyed forward-shadow
    # contract. A missing record is deliberately NOT reconstructed later: a
    # ledger needs decision-time provenance, not a hash of today's code.
    prediction_provenance: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize prediction output for JSON export."""
        return {
            "game_date": self.game_date.isoformat(),
            "hitter_projections": [_projection_to_dict(p) for p in self.hitter_projections],
            "pitcher_projections": [_projection_to_dict(p) for p in self.pitcher_projections],
            "value_plays": [_edge_to_dict(e) for e in self.value_plays],
            "market_status": self.market_status,
            "market_policy_sha256": self.market_policy_sha256,
            "market_policy_reason": self.market_policy_reason,
            "prediction_provenance": self.prediction_provenance,
        }


def _projection_to_dict(projection: PropProjection) -> dict[str, Any]:
    sim = projection.simulation
    return {
        "player_id": projection.player_id,
        "player_name": projection.player_name,
        "category": projection.category,
        "game_date": projection.game_date,
        "projected_value": projection.projected_value,
        "confidence": projection.confidence,
        "team": projection.team,
        "opponent": projection.opponent,
        "opposing_pitcher": projection.opposing_pitcher,
        "lineup_status": projection.lineup_status,
        "mlb_game_pk": projection.mlb_game_pk,
        "input_health_flags": list(projection.input_health_flags),
        "simulation": {
            "n_sims": sim.n_sims,
            "mean": sim.mean,
            "median": sim.median,
            "p10": sim.p10,
            "p90": sim.p90,
            "p_ge_threshold": dict(sim.p_ge_threshold),
        }
        if sim
        else None,
    }


def _edge_to_dict(edge: EdgeResult) -> dict[str, Any]:
    return {
        "player_name": edge.player_name,
        "category": edge.category,
        "line": edge.line,
        "projected_value": edge.projected_value,
        "implied_prob_over": edge.implied_prob_over,
        "model_prob_over": edge.model_prob_over,
        "edge_pct": edge.edge_pct,
        "sportsbook": edge.sportsbook,
        "recommendation": edge.recommendation.value,
        "confidence": edge.confidence,
        "actionable": edge.actionable,
        "market_status": edge.market_status,
        "notes": edge.notes,
    }
