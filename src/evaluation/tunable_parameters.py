"""
Tunable parameter inventory and classification for statistical purification.

Documents every coefficient that affects HR/HRR predictions and whether it is
data-derived, shrinkage-calibrated, or requires empirical fitting from backtests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from src.simulation.pa_simulator import PASimulatorConfig


class ParameterClass(str, Enum):
    DATA_DERIVED = "data_derived"
    SHRINKAGE = "shrinkage"
    FIT_FROM_BACKTEST = "fit_from_backtest"
    OPERATIONAL = "operational"


@dataclass(frozen=True)
class TunableParameter:
    name: str
    location: str
    classification: ParameterClass
    description: str
    default_source: str
    purification_method: str = ""


@dataclass
class ParameterInventoryReport:
    parameters: list[TunableParameter] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "notes": self.notes,
            "parameters": [
                {
                    "name": p.name,
                    "location": p.location,
                    "classification": p.classification.value,
                    "description": p.description,
                    "default_source": p.default_source,
                    "purification_method": p.purification_method,
                }
                for p in self.parameters
            ],
        }


def build_parameter_inventory(config: Optional[dict[str, Any]] = None) -> ParameterInventoryReport:
    """
    Return the full inventory of tunable model parameters.

    Pass ``config`` to include validation-block keys when present (optional).
    """
    params: list[TunableParameter] = []

    pa_fields = PASimulatorConfig.__dataclass_fields__
    for name in pa_fields:
        params.append(
            TunableParameter(
                name=name,
                location="PASimulatorConfig",
                classification=_classify_pa_field(name),
                description=f"PA simulator coefficient: {name}",
                default_source="PASimulatorConfig.from_league()",
                purification_method=_purification_method_for_pa(name),
            )
        )

    config_blocks = [
        ("league_avg.*", "config.json", ParameterClass.DATA_DERIVED, "League rate anchors", "MLB aggregate / Savant"),
        ("park_factors.*", "config.json", ParameterClass.FIT_FROM_BACKTEST, "Venue HR/hit/run multipliers", "ParkFactorEstimator"),
        ("simulation_slot_pa.*", "config.json", ParameterClass.FIT_FROM_BACKTEST, "Batting-order PA opportunity", "SlotPAEstimator"),
        ("lineup_slot_runs_rbi", "config.json", ParameterClass.FIT_FROM_BACKTEST, "DEPRECATED — use simulation_slot_pa", "SlotPAEstimator"),
        ("calibration.league_shrinkage", "config.json", ParameterClass.SHRINKAGE, "EB shrinkage for league rates", "Fixed meta-prior"),
        ("calibration.pa_intercept_shrinkage", "config.json", ParameterClass.SHRINKAGE, "EB shrinkage for PA intercepts", "Fixed meta-prior"),
        ("calibration.bias_shrinkage", "config.json", ParameterClass.SHRINKAGE, "EB shrinkage for category biases", "Fixed meta-prior"),
        ("calibration.coefficient_shrinkage", "config.json", ParameterClass.SHRINKAGE, "EB shrinkage for PA logit weights", "Fixed meta-prior"),
        ("pitcher_matchup.platoon_weight", "config.json", ParameterClass.FIT_FROM_BACKTEST, "Platoon logit shift", "Platoon calibration from pairs"),
        ("weights.season/recent", "config.json", ParameterClass.FIT_FROM_BACKTEST, "Pitcher K blend weights", "Walk-forward on strikeouts"),
        ("lineup_intelligence.*", "config.json", ParameterClass.OPERATIONAL, "Lineup certainty UX thresholds", "Segmented backtest tuning"),
        ("edge.*", "config.json", ParameterClass.OPERATIONAL, "+EV classification thresholds", "ROI simulator calibration"),
        ("simulation.n_sims", "config.json", ParameterClass.OPERATIONAL, "Monte Carlo sample size", "Stability vs runtime tradeoff"),
        ("validation.walk_forward_folds", "config.json", ParameterClass.OPERATIONAL, "Walk-forward fold count", "WalkForwardValidator"),
        ("validation.champion_improvement_threshold", "config.json", ParameterClass.OPERATIONAL, "Min MAE gain to promote challenger", "ChampionChallengerTester"),
    ]
    for name, location, cls, desc, source in config_blocks:
        params.append(
            TunableParameter(
                name=name,
                location=location,
                classification=cls,
                description=desc,
                default_source=source,
                purification_method=source,
            )
        )

    summary: dict[str, int] = {}
    for p in params:
        key = p.classification.value
        summary[key] = summary.get(key, 0) + 1

    return ParameterInventoryReport(
        parameters=params,
        summary=summary,
        notes=(
            "Phase 9 purification: PASimulatorConfig intercepts and scales are derived from "
            "LeagueBaselines; park factors and slot PA factors are fit from historical pairs "
            "with empirical-Bayes shrinkage. lineup_slot_runs_rbi is deprecated."
        ),
    )


def _classify_pa_field(name: str) -> ParameterClass:
    if name.endswith("_intercept"):
        return ParameterClass.DATA_DERIVED
    if name.endswith("_scale") or name in {
        "contact_scale",
        "power_scale",
        "speed_scale",
        "pitcher_k_scale",
        "pitcher_bb_scale",
        "xwoba_scale",
        "xslg_scale",
        "barrel_scale",
        "hard_hit_scale",
    }:
        return ParameterClass.DATA_DERIVED
    if name.endswith("_weight") or "bonus" in name or "penalty" in name:
        return ParameterClass.FIT_FROM_BACKTEST
    if name.endswith("_min") or name.endswith("_max") or name.endswith("_floor"):
        return ParameterClass.DATA_DERIVED
    if name.startswith(("k_", "bb_", "hr_")) and not name.endswith("_intercept"):
        return ParameterClass.FIT_FROM_BACKTEST
    return ParameterClass.SHRINKAGE


def _purification_method_for_pa(name: str) -> str:
    if name.endswith("_intercept"):
        return "Inverse logit of league-implied PA rates at latent=0"
    if "scale" in name:
        return "Reciprocal of league anchor magnitude (z-score normalization)"
    if "weight" in name or "bonus" in name or "penalty" in name:
        return "CalibrationEngine.calibrate_hit_type_weights()"
    if name.startswith("hr_"):
        return "CalibrationEngine.calibrate_hr_coefficients()"
    if name.startswith(("k_", "bb_")):
        return "CalibrationEngine.calibrate_pa_coefficients()"
    return "League-bounded clamps from observed rate ranges"