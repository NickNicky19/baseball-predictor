"""Chronology- and receipt-bound plate-appearance volume consumption.

The frozen simulator may retain its historical two-point/unverified lineup
behavior only as a comparator.  A new candidate must consume a hash-bound PA
distribution fit on 2023 alone.  It may condition on lineup slot only when a
T-minus-4-or-earlier receipt proves that slot; otherwise it uses the artifact's
pooled distribution.  Missing receipt evidence never licenses a postgame slot.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from src.models.dataclasses import HitterGameContext, PlayerFeatureBundle


ARTIFACT_SCHEMA = "pa-volume-distribution-v2"
AUTHORIZATION_SCHEMA = "pa-volume-authorization-v1"
STRICT_MODE = "receipt_required_or_pooled"
LEGACY_MODE = "legacy_frozen"
ALLOWED_MODES = frozenset({LEGACY_MODE, STRICT_MODE})
ALLOWED_LINEUP_STATES = frozenset({"confirmed", "projected", "unknown"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PAVolumeGateError(ValueError):
    """PA volume evidence is missing, contradictory, or chronologically unsafe."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _hash(value: object, label: str) -> str:
    output = str(value).strip().lower()
    if not _SHA256.fullmatch(output):
        raise PAVolumeGateError(f"{label} must be a lowercase SHA-256 digest")
    return output


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise PAVolumeGateError(f"{label} must be a positive integer")
    try:
        output = int(value)
    except (TypeError, ValueError) as exc:
        raise PAVolumeGateError(f"{label} must be a positive integer") from exc
    if output <= 0:
        raise PAVolumeGateError(f"{label} must be a positive integer")
    return output


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise PAVolumeGateError(f"{label} must be a nonnegative integer")
    try:
        output = int(value)
    except (TypeError, ValueError) as exc:
        raise PAVolumeGateError(f"{label} must be a nonnegative integer") from exc
    if output < 0:
        raise PAVolumeGateError(f"{label} must be a nonnegative integer")
    return output


def _utc(value: object, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PAVolumeGateError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise PAVolumeGateError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_mode(value: object) -> str:
    mode = str(value).strip()
    if mode not in ALLOWED_MODES:
        raise PAVolumeGateError(
            f"pa_volume_identity_mode must be one of {sorted(ALLOWED_MODES)}"
        )
    return mode


def _distribution(value: object, label: str) -> dict[int, float]:
    if not isinstance(value, Mapping) or not value:
        raise PAVolumeGateError(f"{label} must be a nonempty distribution")
    output: dict[int, float] = {}
    for raw_support, raw_weight in value.items():
        support = _nonnegative_int(raw_support, f"{label} support")
        if support > 12 or support in output:
            raise PAVolumeGateError(f"{label} has invalid or duplicate support")
        if isinstance(raw_weight, bool):
            raise PAVolumeGateError(f"{label} weights must be finite and nonnegative")
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError) as exc:
            raise PAVolumeGateError(
                f"{label} weights must be finite and nonnegative"
            ) from exc
        if not math.isfinite(weight) or weight < 0.0:
            raise PAVolumeGateError(f"{label} weights must be finite and nonnegative")
        output[support] = weight
    if not math.isclose(sum(output.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise PAVolumeGateError(f"{label} probabilities must sum exactly to one")
    return dict(sorted(output.items()))


@dataclass(frozen=True)
class PAVolumeDistributionArtifact:
    candidate_id: str
    fit_season: int
    selection_season: int
    population: str
    source_path: str
    source_sha256: str
    source_projection_sha256: str
    source_columns: tuple[str, ...]
    fit_rows: int
    fit_games: int
    date_min: str
    date_max: str
    rows_by_lineup_slot: dict[int, int]
    by_lineup_slot: dict[int, dict[int, float]]
    pooled: dict[int, float]

    def __post_init__(self) -> None:
        if str(self.candidate_id).strip() != "pa_volume_2023_only_v1":
            raise PAVolumeGateError("unknown PA-volume candidate identity")
        if self.fit_season != 2023 or self.selection_season != 2024:
            raise PAVolumeGateError("PA-volume chronology must fit 2023 and select 2024")
        if self.population != "original_sequence_zero_starters":
            raise PAVolumeGateError("PA-volume population changed")
        if not str(self.source_path).strip():
            raise PAVolumeGateError("PA-volume source_path cannot be blank")
        object.__setattr__(self, "source_sha256", _hash(self.source_sha256, "source_sha256"))
        object.__setattr__(
            self,
            "source_projection_sha256",
            _hash(self.source_projection_sha256, "source_projection_sha256"),
        )
        expected_columns = ("game_date", "game_pk", "player_id", "lineup_slot", "out_pa")
        if tuple(self.source_columns) != expected_columns:
            raise PAVolumeGateError("PA-volume source projection columns changed")
        object.__setattr__(self, "fit_rows", _positive_int(self.fit_rows, "fit_rows"))
        object.__setattr__(self, "fit_games", _positive_int(self.fit_games, "fit_games"))
        if self.fit_rows != self.fit_games * 18:
            raise PAVolumeGateError("PA-volume population is not 18 original starters per game")
        try:
            minimum = date.fromisoformat(self.date_min)
            maximum = date.fromisoformat(self.date_max)
        except ValueError as exc:
            raise PAVolumeGateError("PA-volume date bounds must be YYYY-MM-DD") from exc
        if minimum.year != 2023 or maximum.year != 2023 or maximum < minimum:
            raise PAVolumeGateError("PA-volume fit contains a non-2023 date")

        row_counts = {int(key): _positive_int(value, "rows_by_lineup_slot") for key, value in self.rows_by_lineup_slot.items()}
        if set(row_counts) != set(range(1, 10)) or sum(row_counts.values()) != self.fit_rows:
            raise PAVolumeGateError("PA-volume lineup-slot row counts are incomplete")
        by_slot = {
            int(key): _distribution(value, f"lineup slot {key}")
            for key, value in self.by_lineup_slot.items()
        }
        if set(by_slot) != set(range(1, 10)):
            raise PAVolumeGateError("PA-volume lineup-slot distributions are incomplete")
        pooled = _distribution(self.pooled, "pooled PA volume")

        mixture: dict[int, float] = {}
        for slot, distribution in by_slot.items():
            share = row_counts[slot] / self.fit_rows
            for support, probability in distribution.items():
                mixture[support] = mixture.get(support, 0.0) + share * probability
        if set(mixture) != set(pooled) or any(
            not math.isclose(mixture[key], pooled[key], rel_tol=0.0, abs_tol=1e-12)
            for key in pooled
        ):
            raise PAVolumeGateError("pooled PA volume contradicts the weighted slot mixture")
        object.__setattr__(self, "rows_by_lineup_slot", row_counts)
        object.__setattr__(self, "by_lineup_slot", by_slot)
        object.__setattr__(self, "pooled", pooled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ARTIFACT_SCHEMA,
            "candidate_id": self.candidate_id,
            "chronology": {
                "fit_season": self.fit_season,
                "selection_season": self.selection_season,
            },
            "population": self.population,
            "source": {
                "path": self.source_path,
                "sha256": self.source_sha256,
                "projection_sha256": self.source_projection_sha256,
                "columns": list(self.source_columns),
                "fit_rows": self.fit_rows,
                "fit_games": self.fit_games,
                "date_min": self.date_min,
                "date_max": self.date_max,
                "rows_by_lineup_slot": {
                    str(key): value for key, value in self.rows_by_lineup_slot.items()
                },
            },
            "by_lineup_slot": {
                str(slot): {str(pa): probability for pa, probability in distribution.items()}
                for slot, distribution in self.by_lineup_slot.items()
            },
            "pooled": {str(pa): probability for pa, probability in self.pooled.items()},
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PAVolumeDistributionArtifact":
        if set(raw) != {
            "schema_version", "candidate_id", "chronology", "population",
            "source", "by_lineup_slot", "pooled",
        } or raw.get("schema_version") != ARTIFACT_SCHEMA:
            raise PAVolumeGateError("PA-volume artifact schema is incomplete or unknown")
        chronology = raw.get("chronology")
        source = raw.get("source")
        if not isinstance(chronology, Mapping) or set(chronology) != {"fit_season", "selection_season"}:
            raise PAVolumeGateError("PA-volume chronology schema is incomplete")
        if not isinstance(source, Mapping) or set(source) != {
            "path", "sha256", "projection_sha256", "columns", "fit_rows",
            "fit_games", "date_min", "date_max", "rows_by_lineup_slot",
        }:
            raise PAVolumeGateError("PA-volume source schema is incomplete")
        if not isinstance(source.get("columns"), list):
            raise PAVolumeGateError("PA-volume source columns must be a list")
        if not isinstance(source.get("rows_by_lineup_slot"), Mapping):
            raise PAVolumeGateError("PA-volume slot row counts must be an object")
        if not isinstance(raw.get("by_lineup_slot"), Mapping):
            raise PAVolumeGateError("PA-volume by-slot payload must be an object")
        return cls(
            candidate_id=raw["candidate_id"],
            fit_season=chronology["fit_season"],
            selection_season=chronology["selection_season"],
            population=raw["population"],
            source_path=source["path"],
            source_sha256=source["sha256"],
            source_projection_sha256=source["projection_sha256"],
            source_columns=tuple(source["columns"]),
            fit_rows=source["fit_rows"],
            fit_games=source["fit_games"],
            date_min=source["date_min"],
            date_max=source["date_max"],
            rows_by_lineup_slot=dict(source["rows_by_lineup_slot"]),
            by_lineup_slot=dict(raw["by_lineup_slot"]),
            pooled=dict(raw["pooled"]),
        )


def load_pa_volume_artifact(
    path: str | Path, expected_sha256: object
) -> PAVolumeDistributionArtifact:
    expected = _hash(expected_sha256, "pa_distribution_sha256")
    source = Path(path)
    if not source.is_file():
        raise PAVolumeGateError(f"strict PA-volume artifact does not exist: {source}")
    payload = source.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise PAVolumeGateError("strict PA-volume artifact hash mismatch")
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PAVolumeGateError("strict PA-volume artifact is not valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise PAVolumeGateError("strict PA-volume artifact must be an object")
    return PAVolumeDistributionArtifact.from_mapping(raw)


@dataclass(frozen=True)
class PAVolumeAuthorization:
    mlb_game_pk: int
    official_game_date: str
    player_id: int
    hitter_team: str
    opponent_team: str
    hitter_is_home: bool
    official_start_utc: str
    target_horizon_utc: str
    receipt_utc: str
    lineup_state: str
    lineup_slot: int | None
    raw_source_payload_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "mlb_game_pk", _positive_int(self.mlb_game_pk, "mlb_game_pk"))
        object.__setattr__(self, "player_id", _positive_int(self.player_id, "player_id"))
        for name in ("hitter_team", "opponent_team"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise PAVolumeGateError(f"{name} cannot be blank")
            object.__setattr__(self, name, value)
        if not isinstance(self.hitter_is_home, bool):
            raise PAVolumeGateError("hitter_is_home must be boolean")
        try:
            target_date = date.fromisoformat(str(self.official_game_date))
        except ValueError as exc:
            raise PAVolumeGateError("official_game_date must be YYYY-MM-DD") from exc
        if target_date.year == 2026 and target_date.month == 5:
            raise PAVolumeGateError("May 2026 PA-volume receipts are sealed")
        for name in ("official_start_utc", "target_horizon_utc", "receipt_utc"):
            object.__setattr__(self, name, _utc(getattr(self, name), name))
        if _utc_dt(self.target_horizon_utc) != _utc_dt(self.official_start_utc) - timedelta(hours=4):
            raise PAVolumeGateError("PA-volume target horizon must be exact T-minus-4")
        if _utc_dt(self.receipt_utc) > _utc_dt(self.target_horizon_utc):
            raise PAVolumeGateError("PA-volume receipt arrived after T-minus-4")
        state = str(self.lineup_state).strip()
        if state not in ALLOWED_LINEUP_STATES:
            raise PAVolumeGateError("lineup_state is invalid")
        object.__setattr__(self, "lineup_state", state)
        if state == "confirmed":
            slot = _positive_int(self.lineup_slot, "confirmed lineup_slot")
            if slot > 9:
                raise PAVolumeGateError("confirmed lineup_slot must be 1..9")
            object.__setattr__(self, "lineup_slot", slot)
        elif self.lineup_slot is not None:
            raise PAVolumeGateError("projected or unknown lineup cannot claim a confirmed slot")
        object.__setattr__(
            self,
            "raw_source_payload_sha256",
            _hash(self.raw_source_payload_sha256, "raw_source_payload_sha256"),
        )

    @property
    def authorization_sha256(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict(include_hash=False))).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = {"schema_version": AUTHORIZATION_SCHEMA, **asdict(self)}
        if include_hash:
            payload["authorization_sha256"] = self.authorization_sha256
        return payload

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PAVolumeAuthorization":
        required = {"schema_version", "authorization_sha256", *cls.__dataclass_fields__}
        if set(raw) != required or raw.get("schema_version") != AUTHORIZATION_SCHEMA:
            raise PAVolumeGateError("PA-volume authorization schema is incomplete or unknown")
        authorization = cls(**{name: raw[name] for name in cls.__dataclass_fields__})
        if _hash(raw["authorization_sha256"], "authorization_sha256") != authorization.authorization_sha256:
            raise PAVolumeGateError("PA-volume authorization hash mismatch")
        return authorization

    def validate_hitter(self, hitter: HitterGameContext) -> None:
        if (
            hitter.game.game_pk != self.mlb_game_pk
            or hitter.game.game_date != self.official_game_date
            or hitter.player.mlb_id != self.player_id
            or hitter.player.team != self.hitter_team
            or hitter.game.opponent != self.opponent_team
            or hitter.game.is_home != self.hitter_is_home
        ):
            raise PAVolumeGateError("PA-volume authorization does not match hitter identity")


@dataclass(frozen=True)
class ResolvedPAVolumeInputs:
    lineup_slot: int | None
    status: str
    authorization_sha256: str | None


def resolve_pa_volume_inputs(
    bundle: PlayerFeatureBundle, *, mode: str
) -> ResolvedPAVolumeInputs:
    selected = validate_mode(mode)
    if selected == LEGACY_MODE:
        slot = bundle.hitter.lineup_slot
        try:
            slot = int(slot) if slot is not None and 1 <= int(slot) <= 9 else None
        except (TypeError, ValueError):
            slot = None
        return ResolvedPAVolumeInputs(slot, "legacy_frozen", None)
    raw = bundle.metadata.get("pa_volume_authorization")
    if raw is None:
        return ResolvedPAVolumeInputs(None, "pooled_missing_receipt", None)
    if not isinstance(raw, Mapping):
        raise PAVolumeGateError("PA-volume authorization metadata must be an object")
    authorization = PAVolumeAuthorization.from_mapping(raw)
    authorization.validate_hitter(bundle.hitter)
    if authorization.lineup_state != "confirmed":
        return ResolvedPAVolumeInputs(
            None,
            f"pooled_lineup_{authorization.lineup_state}",
            authorization.authorization_sha256,
        )
    if bundle.hitter.lineup_slot != authorization.lineup_slot:
        raise PAVolumeGateError("bundle lineup slot contradicts its T-minus-4 receipt")
    return ResolvedPAVolumeInputs(
        authorization.lineup_slot,
        "receipt_confirmed_slot",
        authorization.authorization_sha256,
    )
