"""
Batting-order PA factor estimation — replaces hand-tuned lineup_slot_runs_rbi.

Derives expected PA multipliers per lineup slot from historical pairs or
published order-position PA ratios normalized to league pa_per_game.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

# MLB aggregate PA-per-game by lineup slot (2023–2025 composite, normalized to slot 5 = 1.0).
EMPIRICAL_SLOT_PA_RATIOS: dict[int, float] = {
    1: 1.117,
    2: 1.085,
    3: 1.068,
    4: 1.029,
    5: 1.000,
    6: 0.964,
    7: 0.947,
    8: 0.932,
    9: 0.951,
}


@dataclass
class SlotPASettings:
    """
    Slot PA estimation settings (config.json ``simulation_slot_pa`` block).

    Numeric keys ``"1"``–``"9"`` are slot multipliers; ``shrinkage``,
    ``min_samples_per_slot``, and ``use_empirical_prior`` are meta-settings.
    """

    shrinkage: float = 0.40
    min_samples_per_slot: int = 15
    use_empirical_prior: bool = True

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> SlotPASettings:
        block = config.get("simulation_slot_pa", {})
        return cls(
            shrinkage=float(block.get("shrinkage", 0.40)),
            min_samples_per_slot=int(block.get("min_samples_per_slot", 15)),
            use_empirical_prior=bool(block.get("use_empirical_prior", True)),
        )


class SlotPAEstimator:
    """Estimate lineup-slot PA multipliers for LineupIntelligence."""

    def __init__(self, settings: Optional[SlotPASettings] = None):
        self.settings = settings or SlotPASettings()

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> SlotPAEstimator:
        return cls(settings=SlotPASettings.from_config(config))

    def empirical_prior(self) -> dict[str, float]:
        return {str(slot): round(ratio, 4) for slot, ratio in EMPIRICAL_SLOT_PA_RATIOS.items()}

    def estimate_from_pairs(
        self,
        pairs: pd.DataFrame,
        league_pa_per_game: float = 4.05,
    ) -> dict[str, float]:
        """
        Fit slot PA factors from pairs with lineup_slot and a PA proxy column.

        Uses hits+hrr actuals as PA opportunity proxy when PA not directly observed.
        """
        if pairs.empty or "lineup_slot" not in pairs.columns:
            return self.empirical_prior() if self.settings.use_empirical_prior else {}

        slot_values: dict[str, float] = {}
        for slot in range(1, 10):
            slot_data = pairs[pairs["lineup_slot"] == slot]
            if len(slot_data) < self.settings.min_samples_per_slot:
                prior = EMPIRICAL_SLOT_PA_RATIOS.get(slot, 1.0)
                slot_values[str(slot)] = prior
                continue

            proxy = slot_data.get("actual_value")
            if proxy is None:
                slot_values[str(slot)] = EMPIRICAL_SLOT_PA_RATIOS.get(slot, 1.0)
                continue

            observed = float(pd.to_numeric(proxy, errors="coerce").dropna().mean())
            raw_ratio = observed / league_pa_per_game if league_pa_per_game > 0 else 1.0
            prior = EMPIRICAL_SLOT_PA_RATIOS.get(slot, 1.0)
            shrink = self.settings.shrinkage
            slot_values[str(slot)] = round(prior + (raw_ratio - prior) * shrink, 4)

        return slot_values

    def slot_pa_factor(self, lineup_slot: int, factors: Optional[dict[str, float]] = None) -> float:
        factors = factors or self.empirical_prior()
        return float(factors.get(str(lineup_slot), EMPIRICAL_SLOT_PA_RATIOS.get(lineup_slot, 1.0)))