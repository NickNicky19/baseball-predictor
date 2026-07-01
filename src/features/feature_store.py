"""
Feature storage and retrieval for PlayerFeatureBundle objects.

Supports JSON (full fidelity) and Parquet (flattened + round-trip JSON column).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd

from src.models.dataclasses import (
    GameContext,
    HitterGameContext,
    InjuryStatus,
    MatchupContext,
    ParkFactors,
    PitcherStatcastProfile,
    PlayerFeatureBundle,
    PlayerIdentity,
    StatcastProfile,
    UmpireContext,
    WeatherContext,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


class FeatureStore:
    """
    Persists and loads daily PlayerFeatureBundle collections.

    Layout:
        {root}/{game_date}/bundles.json
        {root}/{game_date}/bundles.parquet
    """

    def __init__(self, root: Union[str, Path] = "data/features"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        bundles: list[PlayerFeatureBundle],
        game_date: str,
        write_json: bool = True,
        write_parquet: bool = True,
    ) -> dict[str, Path]:
        """Save bundles for a date in JSON and/or Parquet format."""
        out_dir = self.root / game_date
        out_dir.mkdir(parents=True, exist_ok=True)
        written: dict[str, Path] = {}

        if write_json:
            json_path = out_dir / "bundles.json"
            payload = [bundle_to_dict(b) for b in bundles]
            json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            written["json"] = json_path
            logger.info("Saved %d bundles to %s", len(bundles), json_path)

        if write_parquet:
            parquet_path = out_dir / "bundles.parquet"
            df = bundles_to_dataframe(bundles)
            df.to_parquet(parquet_path, index=False)
            written["parquet"] = parquet_path
            logger.info("Saved %d bundles to %s", len(bundles), parquet_path)

        return written

    def load(self, game_date: str, prefer: str = "json") -> list[PlayerFeatureBundle]:
        """
        Load bundles for a date.

        prefer: "json" (default, full fidelity) or "parquet"
        """
        out_dir = self.root / game_date
        json_path = out_dir / "bundles.json"
        parquet_path = out_dir / "bundles.parquet"

        if prefer == "json" and json_path.exists():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return [bundle_from_dict(row) for row in data]

        if parquet_path.exists():
            df = pd.read_parquet(parquet_path)
            return dataframe_to_bundles(df)

        if json_path.exists():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return [bundle_from_dict(row) for row in data]

        logger.warning("No feature bundles found for %s", game_date)
        return []

    def exists(self, game_date: str) -> bool:
        out_dir = self.root / game_date
        return (out_dir / "bundles.json").exists() or (out_dir / "bundles.parquet").exists()


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def bundle_to_dict(bundle: PlayerFeatureBundle) -> dict[str, Any]:
    return _to_plain(asdict(bundle))


def bundle_from_dict(data: dict[str, Any]) -> PlayerFeatureBundle:
    return PlayerFeatureBundle(
        hitter=_hitter_from_dict(data["hitter"]),
        statcast=_statcast_from_dict(data["statcast"]),
        park=ParkFactors(**data["park"]),
        weather=WeatherContext(**data["weather"]),
        matchup=MatchupContext(**data["matchup"]),
        umpire=_umpire_from_dict(data.get("umpire")),
        injury=_injury_from_dict(data.get("injury")),
        pitcher_statcast=_pitcher_statcast_from_dict(data.get("pitcher_statcast")),
        expected_pa=float(data.get("expected_pa", 4.05)),
        metadata=dict(data.get("metadata", {})),
    )


def bundles_to_dataframe(bundles: list[PlayerFeatureBundle]) -> pd.DataFrame:
    rows = []
    for bundle in bundles:
        row = {
            "player_id": bundle.hitter.player.mlb_id,
            "player_name": bundle.hitter.player.name,
            "team": bundle.hitter.player.team,
            "game_date": bundle.hitter.game.game_date,
            "game_pk": bundle.hitter.game.game_pk,
            "lineup_slot": bundle.hitter.lineup_slot,
            "opponent": bundle.hitter.game.opponent,
            "venue": bundle.hitter.game.venue,
            "expected_pa": bundle.expected_pa,
            "xwoba": bundle.statcast.xwoba,
            "xslg": bundle.statcast.xslg,
            "barrel_rate": bundle.statcast.barrel_rate,
            "hard_hit_rate": bundle.statcast.hard_hit_rate,
            "contact_rate": bundle.statcast.contact_rate,
            "whiff_rate": bundle.statcast.whiff_rate,
            "has_advanced_data": bundle.statcast.has_advanced_data(),
            "bundle_json": json.dumps(bundle_to_dict(bundle)),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def dataframe_to_bundles(df: pd.DataFrame) -> list[PlayerFeatureBundle]:
    if "bundle_json" not in df.columns:
        raise ValueError("Parquet file missing bundle_json column for round-trip load.")
    return [bundle_from_dict(json.loads(row)) for row in df["bundle_json"].tolist()]


def _hitter_from_dict(data: dict[str, Any]) -> HitterGameContext:
    return HitterGameContext(
        player=PlayerIdentity(**data["player"]),
        game=GameContext(**data["game"]),
        lineup_slot=int(data["lineup_slot"]),
        opposing_pitcher_id=data.get("opposing_pitcher_id"),
        opposing_pitcher_name=data.get("opposing_pitcher_name", ""),
        opposing_pitcher_throws=data.get("opposing_pitcher_throws", "R"),
    )


def _statcast_from_dict(data: dict[str, Any]) -> StatcastProfile:
    return StatcastProfile(**data)


def _umpire_from_dict(data: Optional[dict[str, Any]]) -> Optional[UmpireContext]:
    return UmpireContext(**data) if data else None


def _injury_from_dict(data: Optional[dict[str, Any]]) -> Optional[InjuryStatus]:
    return InjuryStatus(**data) if data else None


def _pitcher_statcast_from_dict(
    data: Optional[dict[str, Any]],
) -> Optional[PitcherStatcastProfile]:
    return PitcherStatcastProfile(**data) if data else None


def _to_plain(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj