"""
Feature storage and retrieval for PlayerFeatureBundle objects.

Supports JSON (full fidelity) and Parquet (flattened + round-trip JSON column).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import asdict
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd

from src.models.dataclasses import (
    FeatureVector,
    GameContext,
    HitterGameContext,
    InjuryStatus,
    MatchupContext,
    ParkFactors,
    PitcherStatcastProfile,
    PlayerFeatureBundle,
    PlayerIdentity,
    StatcastDistributionProfile,
    StatcastProfile,
    UmpireContext,
    WeatherContext,
)
from src.utils.logging import get_logger
from src.utils.provenance import sha256_file
from src.data.statcast_integrity import validate_profile_and_rich_features
from src.data.statcast_source_contract import validate_statcast_source_lineage

logger = get_logger(__name__)

FEATURE_MANIFEST_SCHEMA_VERSION = "feature-bundle-manifest-v1"


class FeatureManifestError(ValueError):
    """Raised when a persisted feature-bundle manifest is missing or tampered."""


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
        provenance: Optional[dict[str, Any]] = None,
    ) -> dict[str, Path]:
        """Save bundles and a content-hashed provenance manifest for a date.

        The manifest is written only after every requested bundle artifact has
        been successfully written.  It describes the exact files produced by
        this save call; it does not alter any feature values.
        """
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

        manifest_path = self._write_manifest(
            out_dir=out_dir,
            game_date=game_date,
            bundle_count=len(bundles),
            artifacts=written,
            provenance=provenance,
        )
        written["manifest"] = manifest_path

        return written

    def load(self, game_date: str, prefer: str = "json") -> list[PlayerFeatureBundle]:
        """
        Load bundles for a date.

        prefer: "json" (default, full fidelity) or "parquet"
        """
        out_dir = self.root / game_date
        json_path = out_dir / "bundles.json"
        parquet_path = out_dir / "bundles.parquet"

        manifest = self.verify_manifest(game_date)
        manifested_paths: set[str] | None = None
        if manifest is not None:
            manifested_paths = {
                str(evidence["path"])
                for evidence in manifest["artifacts"].values()
            }
        else:
            logger.warning(
                "Feature bundles for %s have no manifest and remain legacy-unverified",
                game_date,
            )

        def _eligible(path: Path) -> bool:
            return path.exists() and (
                manifested_paths is None or path.name in manifested_paths
            )

        bundles: list[PlayerFeatureBundle] | None = None

        if prefer == "json" and _eligible(json_path):
            data = json.loads(json_path.read_text(encoding="utf-8"))
            bundles = [bundle_from_dict(row) for row in data]

        elif _eligible(parquet_path):
            df = pd.read_parquet(parquet_path)
            bundles = dataframe_to_bundles(df)

        elif _eligible(json_path):
            data = json.loads(json_path.read_text(encoding="utf-8"))
            bundles = [bundle_from_dict(row) for row in data]

        if bundles is not None:
            if manifest is not None:
                expected = manifest.get("bundle_count")
                if not isinstance(expected, int) or expected < 0:
                    raise FeatureManifestError(
                        f"Feature manifest has invalid bundle_count: {self.manifest_path(game_date)}"
                    )
                if len(bundles) != expected:
                    raise FeatureManifestError(
                        f"Feature bundle count mismatch for {game_date}: "
                        f"manifest says {expected}, loaded {len(bundles)}"
                    )
            return bundles

        if manifest is not None:
            raise FeatureManifestError(
                f"Verified feature manifest has no loadable declared artifact for {game_date}"
            )

        logger.warning("No feature bundles found for %s", game_date)
        return []

    def exists(self, game_date: str) -> bool:
        out_dir = self.root / game_date
        return (out_dir / "bundles.json").exists() or (out_dir / "bundles.parquet").exists()

    def manifest_path(self, game_date: str) -> Path:
        return self.root / game_date / "manifest.json"

    def verify_manifest(self, game_date: str) -> dict[str, Any] | None:
        """Verify a saved manifest's exact artifact hashes.

        ``None`` means this is a legacy snapshot from before manifests existed;
        callers must label it as such rather than infer provenance.  A present
        but malformed/tampered manifest is a hard failure.
        """

        path = self.manifest_path(game_date)
        if not path.exists():
            return None
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise FeatureManifestError(f"Invalid feature manifest: {path}") from exc

        if manifest.get("schema_version") != FEATURE_MANIFEST_SCHEMA_VERSION:
            raise FeatureManifestError(
                f"Unknown feature manifest schema in {path}: {manifest.get('schema_version')!r}"
            )
        if manifest.get("game_date") != game_date:
            raise FeatureManifestError(
                f"Feature manifest date mismatch: requested {game_date}, "
                f"manifest says {manifest.get('game_date')!r}"
            )
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict) or not artifacts:
            raise FeatureManifestError(f"Feature manifest has no artifacts: {path}")

        for name, evidence in artifacts.items():
            if not isinstance(evidence, dict):
                raise FeatureManifestError(f"Feature manifest artifact {name!r} is malformed")
            rel = evidence.get("path")
            expected = evidence.get("sha256")
            if not isinstance(rel, str) or not isinstance(expected, str):
                raise FeatureManifestError(f"Feature manifest artifact {name!r} lacks path/hash")
            candidate = path.parent / rel
            if not candidate.is_file():
                raise FeatureManifestError(f"Feature artifact missing: {candidate}")
            actual = sha256_file(candidate)
            if actual != expected:
                raise FeatureManifestError(
                    f"Feature artifact hash mismatch for {candidate}: expected {expected}, got {actual}"
                )
        return manifest

    def _write_manifest(
        self,
        *,
        out_dir: Path,
        game_date: str,
        bundle_count: int,
        artifacts: dict[str, Path],
        provenance: Optional[dict[str, Any]],
    ) -> Path:
        evidence = {
            name: {"path": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for name, path in sorted(artifacts.items())
        }
        manifest = {
            "schema_version": FEATURE_MANIFEST_SCHEMA_VERSION,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "game_date": game_date,
            "bundle_count": bundle_count,
            "artifacts": evidence,
            "provenance": provenance if provenance is not None else {"status": "not_supplied"},
        }
        path = out_dir / "manifest.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        logger.info("Saved verified feature manifest to %s", path)
        return path


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def bundle_to_dict(bundle: PlayerFeatureBundle) -> dict[str, Any]:
    validate_profile_and_rich_features(
        bundle.statcast,
        bundle.rich_features,
        context=f"FeatureStore.serialize[{bundle.hitter.player.mlb_id}]",
    )
    validate_statcast_source_lineage(
        bundle.statcast,
        context=f"FeatureStore.serialize.source[{bundle.hitter.player.mlb_id}]",
    )
    return _to_plain(asdict(bundle))


def bundle_from_dict(data: dict[str, Any]) -> PlayerFeatureBundle:
    bundle = PlayerFeatureBundle(
        hitter=_hitter_from_dict(data["hitter"]),
        statcast=_statcast_from_dict(data["statcast"]),
        park=ParkFactors(**data["park"]),
        weather=WeatherContext(**data["weather"]),
        matchup=MatchupContext(**data["matchup"]),
        umpire=_umpire_from_dict(data.get("umpire")),
        injury=_injury_from_dict(data.get("injury")),
        pitcher_statcast=_pitcher_statcast_from_dict(data.get("pitcher_statcast")),
        expected_pa=float(data.get("expected_pa", 4.05)),
        features=_feature_vector_from_dict(data.get("features")),
        metadata=dict(data.get("metadata", {})),
    )
    validate_profile_and_rich_features(
        bundle.statcast,
        bundle.rich_features,
        context=f"FeatureStore.deserialize[{bundle.hitter.player.mlb_id}]",
    )
    validate_statcast_source_lineage(
        bundle.statcast,
        context=f"FeatureStore.deserialize.source[{bundle.hitter.player.mlb_id}]",
    )
    return bundle


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
            "statcast_source_kind": bundle.statcast.source_kind,
            "statcast_source_status": bundle.statcast.source_status,
            "statcast_source_window_end": bundle.statcast.source_window_end,
            "statcast_source_row_count": bundle.statcast.source_row_count,
            "statcast_source_content_sha256": bundle.statcast.source_content_sha256,
            "statcast_fallback_fields": json.dumps(list(bundle.statcast.fallback_fields)),
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
        opposing_pitcher_throws=data.get("opposing_pitcher_throws", "U"),
    )


def _statcast_from_dict(data: dict[str, Any]) -> StatcastProfile:
    payload = dict(data)
    distribution = payload.get("distribution")
    if isinstance(distribution, dict):
        payload["distribution"] = StatcastDistributionProfile(**distribution)
    payload["fallback_fields"] = tuple(payload.get("fallback_fields", ()))
    return StatcastProfile(**payload)


def _umpire_from_dict(data: Optional[dict[str, Any]]) -> Optional[UmpireContext]:
    return UmpireContext(**data) if data else None


def _injury_from_dict(data: Optional[dict[str, Any]]) -> Optional[InjuryStatus]:
    return InjuryStatus(**data) if data else None


def _pitcher_statcast_from_dict(
    data: Optional[dict[str, Any]],
) -> Optional[PitcherStatcastProfile]:
    return PitcherStatcastProfile(**data) if data else None


def _feature_vector_from_dict(data: Optional[dict[str, Any]]) -> Optional[FeatureVector]:
    if not data:
        return None
    return FeatureVector(
        values={k: float(v) for k, v in data.get("values", {}).items()},
        groups={k: list(v) for k, v in data.get("groups", {}).items()},
    )


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
