"""
Bias corrector — applies learned adjustments to projections and model parameters.

Consumes RetrainResult or CalibrationResult and applies data-driven corrections
to PropProjection, LeagueBaselines, and PASimulatorConfig.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Optional

from src.evaluation.calibration import CalibrationResult
from src.learning.outcome_retrainer import RetrainResult
from src.models.dataclasses import LeagueBaselines, PropCategory, PropProjection
from src.simulation.pa_simulator import PASimulatorConfig
from src.utils.logging import get_logger

logger = get_logger(__name__)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_MARKETS = {"hits", "home_runs", "total_bases", "hrr", "fantasy", "strikeouts"}
_PROVENANCE_HASH_FIELDS = (
    "source_sha256",
    "protocol_sha256",
    "code_sha256",
    "config_sha256",
    "test_sha256",
)


@dataclass
class BiasCorrectionState:
    """Persisted correction state applied at prediction time."""

    category_offsets: dict[PropCategory, float] = field(default_factory=dict)
    league_overrides: dict[str, float] = field(default_factory=dict)
    pa_config_overrides: dict[str, float] = field(default_factory=dict)
    sample_sizes: dict[str, int] = field(default_factory=dict)
    confidence: float = 0.0
    version: str = "2.0"
    notes: str = ""
    training_cutoff: str = ""
    source_sha256: str = ""
    protocol_sha256: str = ""
    code_sha256: str = ""
    config_sha256: str = ""
    test_sha256: str = ""
    market_scope: tuple[str, ...] = ()
    promotion_status: str = "RESEARCH_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_offsets": dict(self.category_offsets),
            "league_overrides": self.league_overrides,
            "pa_config_overrides": self.pa_config_overrides,
            "sample_sizes": self.sample_sizes,
            "confidence": self.confidence,
            "version": self.version,
            "notes": self.notes,
            "training_cutoff": self.training_cutoff,
            "source_sha256": self.source_sha256,
            "protocol_sha256": self.protocol_sha256,
            "code_sha256": self.code_sha256,
            "config_sha256": self.config_sha256,
            "test_sha256": self.test_sha256,
            "market_scope": list(self.market_scope),
            "promotion_status": self.promotion_status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BiasCorrectionState:
        if not isinstance(data, dict):
            raise ValueError("correction state must be a JSON object")
        expected = set(cls().to_dict())
        missing = expected - set(data)
        extra = set(data) - expected
        if missing or extra:
            raise ValueError(
                "correction state schema mismatch "
                f"(missing={sorted(missing)}, extra={sorted(extra)})"
            )
        state = cls(
            category_offsets=data.get("category_offsets", {}),
            league_overrides=data.get("league_overrides", {}),
            pa_config_overrides=data.get("pa_config_overrides", {}),
            sample_sizes=data.get("sample_sizes", {}),
            confidence=float(data.get("confidence", 0.0)),
            version=str(data.get("version", "")),
            notes=str(data.get("notes", "")),
            training_cutoff=str(data.get("training_cutoff", "")),
            source_sha256=str(data.get("source_sha256", "")),
            protocol_sha256=str(data.get("protocol_sha256", "")),
            code_sha256=str(data.get("code_sha256", "")),
            config_sha256=str(data.get("config_sha256", "")),
            test_sha256=str(data.get("test_sha256", "")),
            market_scope=tuple(data.get("market_scope", ())),
            promotion_status=str(data.get("promotion_status", "")),
        )
        state.validate_persisted()
        return state

    def validate_persisted(self) -> None:
        """Validate the complete persisted-state contract without repairing it."""
        if self.version != "2.0":
            raise ValueError("legacy or unknown correction-state schema is not consumable")
        for label, mapping in (
            ("category_offsets", self.category_offsets),
            ("league_overrides", self.league_overrides),
            ("pa_config_overrides", self.pa_config_overrides),
            ("sample_sizes", self.sample_sizes),
        ):
            if not isinstance(mapping, dict):
                raise ValueError(f"{label} must be an object")
        numeric = {
            **self.category_offsets,
            **self.league_overrides,
            **self.pa_config_overrides,
        }
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(float(value)) for value in numeric.values()):
            raise ValueError("correction values must be finite numbers")
        if any(category not in _ALLOWED_MARKETS for category in self.category_offsets):
            raise ValueError("category_offsets contains an unsupported market")
        league_defaults = LeagueBaselines()
        league_fields = {
            name for name in league_defaults.__dataclass_fields__
            if type(getattr(league_defaults, name)) is float
        }
        if set(self.league_overrides) - league_fields:
            raise ValueError("league_overrides contains an unknown or non-rate field")
        pa_defaults = PASimulatorConfig()
        pa_fields = {
            name for name in pa_defaults.__dataclass_fields__
            if type(getattr(pa_defaults, name)) is float
        }
        if set(self.pa_config_overrides) - pa_fields:
            raise ValueError("pa_config_overrides contains an unknown or nonnumeric field")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("correction confidence must be finite and within [0,1]")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in self.sample_sizes.values()):
            raise ValueError("correction sample sizes must be nonnegative integers")
        try:
            date.fromisoformat(self.training_cutoff)
        except ValueError as exc:
            raise ValueError("correction training_cutoff must be an ISO date") from exc
        for field_name in _PROVENANCE_HASH_FIELDS:
            if not _SHA256_RE.fullmatch(getattr(self, field_name)):
                raise ValueError(f"correction {field_name} must be a lowercase SHA-256")
        if not self.market_scope or len(set(self.market_scope)) != len(self.market_scope):
            raise ValueError("correction market_scope must be nonempty and unique")
        if any(market not in _ALLOWED_MARKETS for market in self.market_scope):
            raise ValueError("correction market_scope contains an unsupported market")
        if self.promotion_status not in {"RESEARCH_ONLY", "PROMOTED"}:
            raise ValueError("correction promotion_status is invalid")

    def assert_runtime_eligible(self) -> None:
        self.validate_persisted()
        if self.promotion_status != "PROMOTED":
            raise ValueError("research-only correction state cannot enter runtime probabilities")
        if self.category_offsets:
            raise ValueError(
                "output-only category offsets cannot enter runtime probabilities; "
                "a coherent distribution must be regenerated"
            )


class BiasCorrector:
    """
    Applies learned bias corrections without hardcoded per-player rules.

    Corrections scale with confidence and can be disabled per category.
    """

    def __init__(self, state: Optional[BiasCorrectionState] = None):
        self.state = state or BiasCorrectionState()

    @classmethod
    def from_retrain_result(cls, result: RetrainResult) -> BiasCorrector:
        """Build corrector from OutcomeRetrainer output."""
        state = BiasCorrectionState(
            category_offsets=result.category_bias_offsets,
            league_overrides=result.league_field_updates,
            pa_config_overrides=result.pa_config_updates,
            sample_sizes={"total": result.sample_size},
            confidence=result.confidence,
            notes=result.notes,
        )
        return cls(state)

    @classmethod
    def from_calibration_result(
        cls,
        result: CalibrationResult,
        baseline_league: Optional[LeagueBaselines] = None,
        baseline_pa: Optional[PASimulatorConfig] = None,
    ) -> BiasCorrector:
        """Build corrector from CalibrationEngine output (stores deltas vs baseline)."""
        base_league = baseline_league or LeagueBaselines()
        base_pa = baseline_pa or PASimulatorConfig.from_league(base_league)

        league_overrides = {
            k: getattr(result.league_baselines, k)
            for k in base_league.__dataclass_fields__
            if getattr(base_league, k) != getattr(result.league_baselines, k)
        }
        pa_overrides = {
            k: v
            for k, v in asdict(result.pa_config).items()
            if k in asdict(base_pa) and asdict(base_pa)[k] != v
        }

        state = BiasCorrectionState(
            category_offsets=result.category_bias_offsets,
            league_overrides=league_overrides,
            pa_config_overrides=pa_overrides,
            sample_sizes=result.sample_sizes,
            notes=result.notes,
        )
        return cls(state)

    def apply_projection(self, projection: PropProjection) -> PropProjection:
        """Apply category bias offset scaled by confidence."""
        offset = self.state.category_offsets.get(projection.category, 0.0)
        if offset == 0.0:
            return projection

        scaled = offset * self.state.confidence
        return PropProjection(
            player_id=projection.player_id,
            player_name=projection.player_name,
            category=projection.category,
            game_date=projection.game_date,
            projected_value=round(projection.projected_value + scaled, 3),
            confidence=projection.confidence,
            simulation=projection.simulation,
            team=projection.team,
            opponent=projection.opponent,
            opposing_pitcher=projection.opposing_pitcher,
            lineup_status=projection.lineup_status,
        )

    def apply_projections(self, projections: list[PropProjection]) -> list[PropProjection]:
        return [self.apply_projection(p) for p in projections]

    def apply_league_baselines(self, league: LeagueBaselines) -> LeagueBaselines:
        """
        Merge learned league overrides.

        When overrides are absolute field values (from RetrainResult), only
        fields present in league_overrides that differ from defaults are merged.
        """
        if not self.state.league_overrides:
            return league

        overrides = {
            k: v
            for k, v in self.state.league_overrides.items()
            if k in league.__dataclass_fields__
        }
        if not overrides:
            return league

        scaled = {
            k: _blend(getattr(league, k), v, self.state.confidence)
            for k, v in overrides.items()
        }
        return replace(league, **scaled)

    def apply_pa_config(self, config: PASimulatorConfig) -> PASimulatorConfig:
        """Merge learned PA simulator coefficient overrides."""
        if not self.state.pa_config_overrides:
            return config

        current = asdict(config)
        merged = dict(current)
        for key, target in self.state.pa_config_overrides.items():
            if key not in merged:
                continue
            merged[key] = _blend(float(merged[key]), float(target), self.state.confidence)
        return PASimulatorConfig(**merged)

    def update_state(self, result: RetrainResult | CalibrationResult) -> None:
        """Replace state from a new learning/calibration result."""
        if isinstance(result, RetrainResult):
            self.state = BiasCorrector.from_retrain_result(result).state
        else:
            self.state = BiasCorrector.from_calibration_result(result).state
        logger.info("BiasCorrectionState updated (confidence=%.2f)", self.state.confidence)

    def save(self, path: str | Path) -> Path:
        self.state.validate_persisted()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.state.to_dict(), indent=2, sort_keys=True) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        logger.info("Saved bias correction state to %s", path)
        return path

    def load(self, path: str | Path) -> BiasCorrectionState:
        # utf-8-sig transparently strips a UTF-8/UTF-16 BOM if an editor wrote
        # one (a stray 0xff/0xfe prefix would otherwise crash a plain utf-8
        # read). Falls back to latin-1 only to surface a clean JSON error
        # rather than an opaque decode error on a truly corrupt file.
        raw = Path(path)
        try:
            text = raw.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = raw.read_text(encoding="latin-1")
        data = json.loads(text)
        self.state = BiasCorrectionState.from_dict(data)
        return self.state

    def is_active(self) -> bool:
        return (
            bool(self.state.category_offsets)
            or bool(self.state.league_overrides)
            or bool(self.state.pa_config_overrides)
        )


def _blend(current: float, target: float, confidence: float) -> float:
    """Blend current value toward learned target by confidence weight."""
    confidence = max(0.0, min(1.0, confidence))
    return round(current + (target - current) * confidence, 6)
