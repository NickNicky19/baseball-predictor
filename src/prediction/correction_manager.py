"""
Correction manager — optional bridge between learning layer and daily predictions.

Loads RetrainResult / CalibrationResult / persisted state and applies
data-driven corrections to model parameters and output projections.
Corrections are off by default and must be enabled explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from src.evaluation.calibration import CalibrationResult
from src.learning.bias_corrector import BiasCorrectionState, BiasCorrector
from src.learning.outcome_retrainer import RetrainResult
from src.models.dataclasses import LeagueBaselines, PropProjection
from src.prediction.prop_engine import PropEngine
from src.simulation.pa_simulator import PASimulatorConfig
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CorrectionSettings:
    """Configuration for correction behavior (loaded from config.json)."""

    enabled: bool = False
    state_path: str = "data/learning/bias_corrections.json"
    apply_model_parameters: bool = True
    apply_projection_offsets: bool = True

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> CorrectionSettings:
        learning = config.get("learning", {})
        return cls(
            enabled=bool(learning.get("apply_corrections", False)),
            state_path=str(learning.get("correction_state_path", "data/learning/bias_corrections.json")),
            apply_model_parameters=bool(learning.get("apply_model_parameters", True)),
            apply_projection_offsets=bool(learning.get("apply_projection_offsets", True)),
        )


class CorrectionManager:
    """
    Orchestrates optional application of learned corrections.

    Usage:
        manager = CorrectionManager.from_config(config)
        manager.load_state()          # optional, if persisted
        manager.enable()
        effective_league, effective_pa = manager.prepare(league, pa_config, prop_engine)
        projections = prop_engine.project_hitter(bundle)
        corrected = manager.apply_projections(projections)
    """

    def __init__(
        self,
        corrector: Optional[BiasCorrector] = None,
        settings: Optional[CorrectionSettings] = None,
        project_root: Optional[Path] = None,
    ):
        self.corrector = corrector or BiasCorrector()
        self.settings = settings or CorrectionSettings()
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        self._enabled = self.settings.enabled
        self._loaded_state_path: Optional[Path] = None
        self._loaded_state_source_sha256: Optional[str] = None

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        project_root: Optional[Path] = None,
    ) -> CorrectionManager:
        manager = cls(settings=CorrectionSettings.from_config(config), project_root=project_root)
        if manager.settings.enabled:
            manager.require_active_state()
        return manager

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True
        logger.info("Corrections enabled")

    def disable(self) -> None:
        self._enabled = False
        logger.info("Corrections disabled")

    def is_active(self) -> bool:
        return self._enabled and self.corrector.is_active()

    def load_retrain_result(self, result: RetrainResult) -> None:
        """Load corrections from an OutcomeRetrainer fit result."""
        self.corrector = BiasCorrector.from_retrain_result(result)
        logger.info(
            "Loaded retrain result (samples=%d, confidence=%.2f)",
            result.sample_size,
            result.confidence,
        )

    def load_calibration_result(
        self,
        result: CalibrationResult,
        baseline_league: Optional[LeagueBaselines] = None,
        baseline_pa: Optional[PASimulatorConfig] = None,
    ) -> None:
        """Load corrections from a CalibrationEngine result."""
        self.corrector = BiasCorrector.from_calibration_result(
            result,
            baseline_league=baseline_league,
            baseline_pa=baseline_pa,
        )
        logger.info("Loaded calibration result")

    def load_state(self, path: Optional[str | Path] = None) -> bool:
        """Load persisted BiasCorrectionState from disk."""
        resolved = self._resolve_path(path or self.settings.state_path)
        if not resolved.exists():
            raise FileNotFoundError(f"correction state not found: {resolved}")
        source_digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        self.corrector.load(resolved)
        self.corrector.state.validate()
        self._loaded_state_path = resolved.resolve()
        self._loaded_state_source_sha256 = source_digest
        logger.info("Loaded correction state from %s", resolved)
        return True

    def load_state_if_exists(self) -> bool:
        """Load an optional state when present; malformed state is terminal."""
        resolved = self._resolve_path(self.settings.state_path)
        if not resolved.exists():
            return False
        return self.load_state(resolved)

    def require_active_state(self) -> dict[str, str]:
        """Require one valid, active, hash-bound correction state.

        Explicitly requesting corrections must never degrade silently to the
        frozen model.  Output-level offsets are also rejected: changing only a
        displayed mean while retaining the original probability distribution
        is not a coherent probability correction.
        """

        if not self.corrector.is_active() or self._loaded_state_path is None:
            self.load_state(self.settings.state_path)
        self.corrector.state.validate()
        if not self.corrector.is_active():
            raise ValueError("corrections requested but the correction state is inactive")
        has_offsets = bool(self.corrector.state.category_offsets)
        has_parameter_overrides = bool(
            self.corrector.state.league_overrides
            or self.corrector.state.pa_config_overrides
        )
        if has_offsets and not self.settings.apply_projection_offsets:
            raise ValueError(
                "category offsets are present but apply_projection_offsets is false; "
                "refusing unconsumed correction provenance"
            )
        if has_parameter_overrides and not self.settings.apply_model_parameters:
            raise ValueError(
                "model-parameter overrides are present but apply_model_parameters is false; "
                "refusing unconsumed correction provenance"
            )
        if self.settings.apply_projection_offsets and has_offsets:
            raise ValueError(
                "output-level category offsets are not probability-consistent; "
                "refit a hash-bound model-parameter correction before enabling corrections"
            )
        return self.correction_identity()

    def correction_identity(self) -> dict[str, str]:
        if self._loaded_state_path is None or self._loaded_state_source_sha256 is None:
            raise ValueError("correction state has not been loaded from a source artifact")
        self.corrector.state.validate()
        canonical = json.dumps(
            self.corrector.state.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return {
            "source_path": str(self._loaded_state_path),
            "source_sha256": self._loaded_state_source_sha256,
            "effective_state_sha256": hashlib.sha256(canonical).hexdigest(),
        }

    def save_state(self, path: Optional[str | Path] = None) -> Path:
        """Persist current correction state."""
        resolved = self._resolve_path(path or self.settings.state_path)
        return self.corrector.save(resolved)

    def prepare(
        self,
        league: LeagueBaselines,
        pa_config: PASimulatorConfig,
        prop_engine: PropEngine,
        force: bool = False,
    ) -> tuple[LeagueBaselines, PASimulatorConfig]:
        """
        Apply model-parameter corrections before simulation.

        Updates PropEngine's simulation layer with effective league/PA config.
        Returns the effective parameters for downstream sync (e.g. Statcast engine).

        force: apply when corrector has state even if manager.enabled is False
               (used for per-call apply_corrections on predict()).
        """
        if not self._should_apply(force):
            return league, pa_config

        self.require_active_state()
        if not self.settings.apply_model_parameters:
            return league, pa_config

        effective_league = self.corrector.apply_league_baselines(league)
        effective_pa = self.corrector.apply_pa_config(pa_config)
        prop_engine.configure_simulation(effective_league, effective_pa)

        logger.debug(
            "Applied model corrections (confidence=%.2f)",
            self.corrector.state.confidence,
        )
        return effective_league, effective_pa

    def apply_projections(
        self,
        projections: list[PropProjection],
        force: bool = False,
    ) -> list[PropProjection]:
        """Apply output-level category bias corrections after simulation."""
        if not self._should_apply(force):
            return projections
        self.require_active_state()
        if not self.settings.apply_projection_offsets:
            return projections
        return self.corrector.apply_projections(projections)

    def _should_apply(self, force: bool = False) -> bool:
        return (self._enabled or force) and self.corrector.is_active()

    def status(self) -> dict[str, Any]:
        """Return a summary of correction state for logging/debugging."""
        state = self.corrector.state
        return {
            "enabled": self._enabled,
            "active": self.is_active(),
            "confidence": state.confidence,
            "category_offsets": dict(state.category_offsets),
            "league_override_count": len(state.league_overrides),
            "pa_override_count": len(state.pa_config_overrides),
            "notes": state.notes,
        }

    def _resolve_path(self, path: str | Path) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.project_root / p
        return p
