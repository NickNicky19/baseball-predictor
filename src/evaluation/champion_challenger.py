"""
Champion vs challenger testing — promote model changes only when validated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

from src.evaluation.walk_forward_validator import WalkForwardReport, WalkForwardValidator
from src.models.dataclasses import PropProjection


@dataclass
class ChallengerResult:
    name: str
    walk_forward: WalkForwardReport
    combined_score: float = 0.0
    promoted: bool = False
    improvement_pct: float = 0.0
    notes: str = ""


@dataclass
class ChampionChallengerReport:
    champion_name: str
    champion_score: float
    challengers: list[ChallengerResult] = field(default_factory=list)
    winner: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "champion_name": self.champion_name,
            "champion_score": self.champion_score,
            "winner": self.winner,
            "challengers": [
                {
                    "name": c.name,
                    "combined_score": c.combined_score,
                    "promoted": c.promoted,
                    "improvement_pct": c.improvement_pct,
                    "notes": c.notes,
                }
                for c in self.challengers
            ],
            "notes": self.notes,
        }


class ChampionChallengerTester:
    """
    Compare champion and challenger predictors on walk-forward validation.

    ``improvement_threshold`` defaults from config ``validation.champion_improvement_threshold``.
    """

    def __init__(
        self,
        improvement_threshold: float = 0.02,
        validator: Optional[WalkForwardValidator] = None,
    ):
        self.improvement_threshold = improvement_threshold
        self.validator = validator or WalkForwardValidator()

    def compare(
        self,
        pairs: pd.DataFrame,
        champion_fn: Callable[[pd.DataFrame], list[PropProjection]],
        challengers: dict[str, Callable[[pd.DataFrame], list[PropProjection]]],
        n_folds: int = 5,
    ) -> ChampionChallengerReport:
        champion_wf = self.validator.validate(pairs, champion_fn, n_folds=n_folds)
        champion_score = champion_wf.mean_combined_score

        results: list[ChallengerResult] = []
        winner = "champion"
        best_score = champion_score

        for name, fn in challengers.items():
            wf = self.validator.validate(pairs, fn, n_folds=n_folds)
            score = wf.mean_combined_score
            improvement = (champion_score - score) / champion_score if champion_score > 0 else 0.0
            promoted = score < champion_score * (1.0 - self.improvement_threshold)

            if score < best_score:
                best_score = score
                winner = name

            results.append(
                ChallengerResult(
                    name=name,
                    walk_forward=wf,
                    combined_score=score,
                    promoted=promoted,
                    improvement_pct=round(improvement * 100, 2),
                    notes=f"{'PROMOTED' if promoted else 'rejected'} vs champion",
                )
            )

        return ChampionChallengerReport(
            champion_name="champion",
            champion_score=round(champion_score, 4),
            challengers=results,
            winner=winner,
            notes=f"Threshold: {self.improvement_threshold * 100:.1f}% MAE improvement required.",
        )