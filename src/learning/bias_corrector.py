"""
Bias corrector — applies learned adjustments to projections and model parameters.

Consumes RetrainResult or CalibrationResult and applies data-driven corrections
to PropProjection, LeagueBaselines, and PASimulatorConfig.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, replace
import math
from pathlib import Path
from typing import Any, Optional

from src.evaluation.calibration import CalibrationResult
from src.learning.outcome_retrainer import RetrainResult
from src.models.dataclasses import LeagueBaselines, PropCategory, PropProjection
from src.simulation.pa_simulator import PASimulatorConfig
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BiasCorrectionState:
    """Persisted correction state applied at prediction time."""

    category_offsets: dict[PropCategory, float] = field(default_factory=dict)
    league_overrides: dict[str, float] = field(default_factory=dict)
    pa_config_overrides: dict[str, float] = field(default_factory=dict)
    sample_sizes: dict[str, int] = field(default_factory=dict)
    confidence: float = 0.0
    version: str = "1.0"
    notes: str = ""

    _REQUIRED_KEYS = frozenset(
        {
            "category_offsets",
            "league_overrides",
            "pa_config_overrides",
            "sample_sizes",
            "confidence",
            "version",
            "notes",
        }
    )
    _CATEGORIES = frozenset(
        {"hits", "hrr", "home_runs", "fantasy", "strikeouts", "total_bases"}
    )

    @staticmethod
    def _finite_mapping(raw: Any, label: str) -> dict[str, float]:
        if not isinstance(raw, dict):
            raise ValueError(f"{label} must be an object")
        result: dict[str, float] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{label} contains an invalid field name")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{label}.{key} must be numeric")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"{label}.{key} must be finite")
            result[key] = number
        return result

    @classmethod
    def _validated_payload(cls, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("correction state root must be an object")
        keys = frozenset(data)
        missing = sorted(cls._REQUIRED_KEYS - keys)
        unknown = sorted(keys - cls._REQUIRED_KEYS)
        if missing or unknown:
            raise ValueError(
                f"correction state schema mismatch: missing={missing}, unknown={unknown}"
            )

        category_offsets = cls._finite_mapping(data["category_offsets"], "category_offsets")
        unknown_categories = sorted(set(category_offsets) - cls._CATEGORIES)
        if unknown_categories:
            raise ValueError(f"unknown correction categories: {unknown_categories}")

        league_overrides = cls._finite_mapping(data["league_overrides"], "league_overrides")
        valid_league = {item.name for item in fields(LeagueBaselines)}
        unknown_league = sorted(set(league_overrides) - valid_league)
        if unknown_league:
            raise ValueError(f"unknown league correction fields: {unknown_league}")

        pa_overrides = cls._finite_mapping(data["pa_config_overrides"], "pa_config_overrides")
        valid_pa = {item.name for item in fields(PASimulatorConfig)}
        unknown_pa = sorted(set(pa_overrides) - valid_pa)
        if unknown_pa:
            raise ValueError(f"unknown PA correction fields: {unknown_pa}")

        sample_sizes_raw = data["sample_sizes"]
        if not isinstance(sample_sizes_raw, dict):
            raise ValueError("sample_sizes must be an object")
        sample_sizes: dict[str, int] = {}
        for key, value in sample_sizes_raw.items():
            if not isinstance(key, str) or not key:
                raise ValueError("sample_sizes contains an invalid field name")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"sample_sizes.{key} must be a non-negative integer")
            sample_sizes[key] = value

        confidence_raw = data["confidence"]
        if isinstance(confidence_raw, bool) or not isinstance(confidence_raw, (int, float)):
            raise ValueError("confidence must be numeric")
        confidence = float(confidence_raw)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be finite and in [0, 1]")
        version = data["version"]
        notes = data["notes"]
        if not isinstance(version, str) or not version.strip():
            raise ValueError("correction state version must be a non-empty string")
        if not isinstance(notes, str):
            raise ValueError("correction state notes must be a string")
        return {
            "category_offsets": category_offsets,
            "league_overrides": league_overrides,
            "pa_config_overrides": pa_overrides,
            "sample_sizes": sample_sizes,
            "confidence": confidence,
            "version": version,
            "notes": notes,
        }

    def validate(self) -> None:
        self._validated_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_offsets": dict(self.category_offsets),
            "league_overrides": self.league_overrides,
            "pa_config_overrides": self.pa_config_overrides,
            "sample_sizes": self.sample_sizes,
            "confidence": self.confidence,
            "version": self.version,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BiasCorrectionState:
        return cls(**cls._validated_payload(data))


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
        # Preserve every probability, identity and lineage field.  Rebuilding
        # the dataclass by hand previously dropped outcome_probs, mlb_game_pk,
        # and input_health_flags whenever an offset was applied.
        return replace(
            projection,
            projected_value=round(projection.projected_value + scaled, 3),
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

        self.state.validate()
        overrides = dict(self.state.league_overrides)

        scaled = {
            k: _blend(getattr(league, k), v, self.state.confidence)
            for k, v in overrides.items()
        }
        return replace(league, **scaled)

    def apply_pa_config(self, config: PASimulatorConfig) -> PASimulatorConfig:
        """Merge learned PA simulator coefficient overrides."""
        if not self.state.pa_config_overrides:
            return config

        self.state.validate()
        current = asdict(config)
        merged = dict(current)
        for key, target in self.state.pa_config_overrides.items():
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
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.state.to_dict(), indent=2), encoding="utf-8")
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
