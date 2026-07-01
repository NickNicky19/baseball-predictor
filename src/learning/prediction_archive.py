"""
Prediction archive — persists daily projections for later outcome pairing.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from src.models.dataclasses import DailyPrediction, PropProjection
from src.utils.errors import ConfigError
from src.utils.logging import get_logger

logger = get_logger(__name__)


class PredictionArchive:
    """Saves and loads DailyPrediction snapshots by game date."""

    def __init__(
        self,
        archive_dir: str = "data/learning/predictions",
        project_root: Optional[Path] = None,
    ):
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        self.archive_dir = self.project_root / archive_dir

    @classmethod
    def from_config(cls, config: dict[str, Any], project_root: Optional[Path] = None) -> PredictionArchive:
        block = config.get("outcome_recording", {})
        return cls(
            archive_dir=str(block.get("predictions_dir", "data/learning/predictions")),
            project_root=project_root,
        )

    def save(self, prediction: DailyPrediction) -> Path:
        """Persist a DailyPrediction snapshot."""
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        path = self.archive_dir / f"predictions_{prediction.game_date.isoformat()}.json"
        path.write_text(json.dumps(prediction.to_dict(), indent=2), encoding="utf-8")
        logger.info("Archived predictions for %s to %s", prediction.game_date, path.name)
        return path

    def load(self, game_date: str) -> Optional[DailyPrediction]:
        """Load an archived prediction if it exists."""
        path = self.archive_dir / f"predictions_{game_date}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return _dict_to_prediction(data)

    def exists(self, game_date: str) -> bool:
        return (self.archive_dir / f"predictions_{game_date}.json").exists()

    def list_dates(self) -> list[str]:
        if not self.archive_dir.exists():
            return []
        dates: list[str] = []
        for path in sorted(self.archive_dir.glob("predictions_*.json")):
            dates.append(path.stem.replace("predictions_", ""))
        return dates


def _dict_to_prediction(data: dict[str, Any]) -> DailyPrediction:
    from datetime import date as date_cls

    hitter = [_dict_to_projection(p) for p in data.get("hitter_projections", [])]
    pitcher = [_dict_to_projection(p) for p in data.get("pitcher_projections", [])]
    return DailyPrediction(
        game_date=date_cls.fromisoformat(data["game_date"]),
        hitter_projections=hitter,
        pitcher_projections=pitcher,
        value_plays=[],
    )


def _dict_to_projection(data: dict[str, Any]) -> PropProjection:
    return PropProjection(
        player_id=int(data["player_id"]),
        player_name=str(data["player_name"]),
        category=data["category"],
        game_date=str(data["game_date"]),
        projected_value=float(data["projected_value"]),
        confidence=float(data.get("confidence", 0.5)),
        simulation=None,
    )