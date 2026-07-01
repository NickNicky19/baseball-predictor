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


Handedness = Literal["L", "R", "S"]
LineupStatus = Literal["confirmed", "projected", "unknown"]
PropCategory = Literal["hits", "hrr", "home_runs", "fantasy", "strikeouts"]


@dataclass(frozen=True)
class PlayerIdentity:
    mlb_id: int
    name: str
    team: str
    bats: Handedness = "R"
    throws: Handedness = "R"


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
    opposing_pitcher_throws: Handedness = "R"


@dataclass
class PitcherGameContext:
    player: PlayerIdentity
    game: GameContext
    expected_innings: float = 5.5


# ---------------------------------------------------------------------------
# Statcast / Savant feature bundle
# ---------------------------------------------------------------------------


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
    avg_exit_velocity: Optional[float] = None
    avg_launch_angle: Optional[float] = None
    chase_rate: Optional[float] = None
    contact_rate: Optional[float] = None
    whiff_rate: Optional[float] = None
    swing_rate: Optional[float] = None
    zone_rate: Optional[float] = None
    k_rate: Optional[float] = None
    bb_rate: Optional[float] = None

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
    metadata: dict[str, Any] = field(default_factory=dict)


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
    team: str = ""
    opponent: str = ""
    opposing_pitcher: str = ""
    lineup_status: LineupStatus = "unknown"


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
    notes: list[str] = field(default_factory=list)


@dataclass
class DailyPrediction:
    game_date: date
    hitter_projections: list[PropProjection] = field(default_factory=list)
    pitcher_projections: list[PropProjection] = field(default_factory=list)
    value_plays: list[EdgeResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize prediction output for JSON export."""
        return {
            "game_date": self.game_date.isoformat(),
            "hitter_projections": [_projection_to_dict(p) for p in self.hitter_projections],
            "pitcher_projections": [_projection_to_dict(p) for p in self.pitcher_projections],
            "value_plays": [_edge_to_dict(e) for e in self.value_plays],
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
        "recommendation": edge.recommendation.value,
        "confidence": edge.confidence,
        "notes": edge.notes,
    }