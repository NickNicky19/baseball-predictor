"""
Lineup intelligence — certainty scoring and stats-driven projection adjustments.

Evaluates whether a hitter's lineup slot is confirmed, projected, or unknown and
applies configurable adjustments to expected PA, confidence, and simulation load.
Designed for backtest segmentation (confirmed vs projected slates) and future learning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.models.dataclasses import HitterGameContext, LeagueBaselines, LineupStatus, PlayerFeatureBundle
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class LineupCertainty:
    """Quantified lineup confidence for a hitter-game context."""

    status: LineupStatus
    certainty_score: float
    order_length: int
    lineup_slot: int


@dataclass(frozen=True)
class LineupAdjustment:
    """
    Stats-driven adjustments applied before simulation.

    certainty_score: 0–1 weight for downstream analytics and future learning.
    confidence_multiplier: scales PropEngine confidence after simulation.
    simulation_n_sims_scale: fraction of base Monte Carlo runs (wider tails when < 1).
    variance_inflation: widens stability penalty in confidence when lineup is uncertain.
    """

    certainty: LineupCertainty
    expected_pa: float
    confidence_multiplier: float
    simulation_n_sims_scale: float
    variance_inflation: float

    def to_metadata(self) -> dict[str, Any]:
        return {
            "lineup_status": self.certainty.status,
            "lineup_certainty_score": round(self.certainty.certainty_score, 4),
            "lineup_order_length": self.certainty.order_length,
            "lineup_slot": self.certainty.lineup_slot,
            "lineup_confidence_multiplier": self.confidence_multiplier,
            "lineup_simulation_n_sims_scale": self.simulation_n_sims_scale,
            "lineup_variance_inflation": self.variance_inflation,
            "lineup_expected_pa": round(self.expected_pa, 3),
        }


@dataclass
class LineupIntelligenceSettings:
    """Configurable certainty and adjustment tables (config.json lineup_intelligence block)."""

    certainty_scores: dict[str, float]
    confidence_scale: dict[str, float]
    expected_pa_scale: dict[str, float]
    simulation_n_sims_scale: dict[str, float]
    variance_inflation: dict[str, float]
    use_slot_pa_factors: bool = True
    min_order_length_confirmed: int = 9
    min_order_length_projected: int = 7

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> LineupIntelligenceSettings:
        block = config.get("lineup_intelligence", {})
        return cls(
            certainty_scores={
                "confirmed": float(block.get("certainty_scores", {}).get("confirmed", 1.0)),
                "projected": float(block.get("certainty_scores", {}).get("projected", 0.78)),
                "unknown": float(block.get("certainty_scores", {}).get("unknown", 0.62)),
            },
            confidence_scale={
                "confirmed": float(block.get("confidence_scale", {}).get("confirmed", 1.0)),
                "projected": float(block.get("confidence_scale", {}).get("projected", 0.88)),
                "unknown": float(block.get("confidence_scale", {}).get("unknown", 0.75)),
            },
            expected_pa_scale={
                "confirmed": float(block.get("expected_pa_scale", {}).get("confirmed", 1.0)),
                "projected": float(block.get("expected_pa_scale", {}).get("projected", 0.94)),
                "unknown": float(block.get("expected_pa_scale", {}).get("unknown", 0.88)),
            },
            simulation_n_sims_scale={
                "confirmed": float(block.get("simulation_n_sims_scale", {}).get("confirmed", 1.0)),
                "projected": float(block.get("simulation_n_sims_scale", {}).get("projected", 0.90)),
                "unknown": float(block.get("simulation_n_sims_scale", {}).get("unknown", 0.82)),
            },
            variance_inflation={
                "confirmed": float(block.get("variance_inflation", {}).get("confirmed", 1.0)),
                "projected": float(block.get("variance_inflation", {}).get("projected", 1.12)),
                "unknown": float(block.get("variance_inflation", {}).get("unknown", 1.25)),
            },
            use_slot_pa_factors=bool(block.get("use_slot_pa_factors", True)),
            min_order_length_confirmed=int(block.get("min_order_length_confirmed", 9)),
            min_order_length_projected=int(block.get("min_order_length_projected", 7)),
        )


class LineupIntelligence:
    """
    Evaluates lineup certainty and produces adjustment parameters for bundles.

    Slot-based expected PA uses lineup_slot_runs_rbi run multipliers as a
    data-driven proxy for batting-order PA opportunity (leadoff > cleanup > 9-hole).
    """

    def __init__(
        self,
        settings: Optional[LineupIntelligenceSettings] = None,
        league_baselines: Optional[LeagueBaselines] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        self.config = config or {}
        self.settings = settings or LineupIntelligenceSettings.from_config(self.config)
        self.league = league_baselines or LeagueBaselines.from_config(self.config)

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        league_baselines: Optional[LeagueBaselines] = None,
    ) -> LineupIntelligence:
        return cls(
            settings=LineupIntelligenceSettings.from_config(config),
            league_baselines=league_baselines,
            config=config,
        )

    def evaluate(
        self,
        hitter: HitterGameContext,
        order_length: Optional[int] = None,
    ) -> LineupCertainty:
        """Score lineup certainty from game context and posted order depth."""
        status = hitter.game.lineup_status
        length = order_length if order_length is not None else self._infer_order_length(status)
        score = self._certainty_score(status, length)
        return LineupCertainty(
            status=status,
            certainty_score=score,
            order_length=length,
            lineup_slot=hitter.lineup_slot,
        )

    def build_adjustment(
        self,
        hitter: HitterGameContext,
        order_length: Optional[int] = None,
    ) -> LineupAdjustment:
        """Compute full adjustment set for a hitter before simulation."""
        certainty = self.evaluate(hitter, order_length=order_length)
        status = certainty.status

        base_pa = self.league.pa_per_game
        slot_factor = self._slot_pa_factor(hitter.lineup_slot) if self.settings.use_slot_pa_factors else 1.0
        pa_scale = self.settings.expected_pa_scale.get(status, 1.0)
        expected_pa = base_pa * slot_factor * pa_scale

        return LineupAdjustment(
            certainty=certainty,
            expected_pa=round(expected_pa, 3),
            confidence_multiplier=self.settings.confidence_scale.get(status, 1.0),
            simulation_n_sims_scale=self.settings.simulation_n_sims_scale.get(status, 1.0),
            variance_inflation=self.settings.variance_inflation.get(status, 1.0),
        )

    def apply_to_bundle(
        self,
        bundle: PlayerFeatureBundle,
        order_length: Optional[int] = None,
    ) -> PlayerFeatureBundle:
        """Return a bundle with lineup-driven expected_pa and metadata adjustments."""
        adjustment = self.build_adjustment(bundle.hitter, order_length=order_length)
        metadata = dict(bundle.metadata)
        metadata.update(adjustment.to_metadata())

        return PlayerFeatureBundle(
            hitter=bundle.hitter,
            statcast=bundle.statcast,
            park=bundle.park,
            weather=bundle.weather,
            matchup=bundle.matchup,
            umpire=bundle.umpire,
            injury=bundle.injury,
            pitcher_statcast=bundle.pitcher_statcast,
            expected_pa=adjustment.expected_pa,
            metadata=metadata,
        )

    def _certainty_score(self, status: LineupStatus, order_length: int) -> float:
        base = self.settings.certainty_scores.get(status, 0.5)
        if status == "confirmed" and order_length >= self.settings.min_order_length_confirmed:
            return base
        if status == "projected" and order_length >= self.settings.min_order_length_projected:
            return base * min(1.0, order_length / self.settings.min_order_length_projected)
        if status == "unknown":
            depth_factor = min(1.0, order_length / max(self.settings.min_order_length_projected, 1))
            return base * depth_factor
        return base * min(1.0, order_length / max(self.settings.min_order_length_confirmed, 1))

    def _slot_pa_factor(self, lineup_slot: int) -> float:
        """Derive PA opportunity from config lineup_slot_runs_rbi run weights."""
        slot_cfg = self.config.get("lineup_slot_runs_rbi", {})
        runs_by_slot: list[float] = []
        for slot in range(1, 10):
            entry = slot_cfg.get(str(slot), {})
            runs_by_slot.append(float(entry.get("runs", 1.0)))
        if not runs_by_slot:
            return 1.0
        avg_runs = sum(runs_by_slot) / len(runs_by_slot)
        if avg_runs <= 0:
            return 1.0
        idx = max(1, min(9, lineup_slot)) - 1
        return runs_by_slot[idx] / avg_runs

    @staticmethod
    def _infer_order_length(status: LineupStatus) -> int:
        if status == "confirmed":
            return 9
        if status == "projected":
            return 9
        return 5