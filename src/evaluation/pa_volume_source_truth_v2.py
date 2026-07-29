"""Deterministic, source-bound 2023 plate-appearance distribution builder.

This module has no network, fitting-selection, prediction, or deployment
capability.  It accepts only the positive official-game projection produced by
an independently verified historical source release and builds the empirical
P(PA | original lineup slot) artifact used by a later candidate version.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "pa-volume-distribution-v3"
CANDIDATE_ID = "pa_volume_2023_source_bound_v2"
PROJECTION_SCHEMA_VERSION = "pa-volume-official-starter-projection-v2"
ROW_FIELDS = {
    "game_pk",
    "official_date",
    "side",
    "team_id",
    "player_id",
    "lineup_slot",
    "out_pa",
}
SIDES = ("away", "home")
SLOTS = tuple(range(1, 10))
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class PAVolumeSourceTruthError(ValueError):
    """The official 2023 projection cannot support a truthful PA artifact."""


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise PAVolumeSourceTruthError(f"{label} must be a lowercase SHA-256")
    return value


def _canonical_positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PAVolumeSourceTruthError(f"{label} must be a positive integer")
    return value


def _canonical_nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PAVolumeSourceTruthError(f"{label} must be a non-negative integer")
    return value


def _canonical_2023_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise PAVolumeSourceTruthError(f"{label} must be a canonical ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PAVolumeSourceTruthError(f"{label} must be a canonical ISO date") from exc
    if parsed.isoformat() != value or parsed.year != 2023:
        raise PAVolumeSourceTruthError(f"{label} must be a canonical 2023 date")
    return value


def _normalize_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    # Team scope is part of identity.  Suspended/resumed games can legitimately
    # contain the same player on both teams; collapsing to (game_pk, player_id)
    # would silently discard one official role.
    identities: set[tuple[int, str, int]] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping) or set(raw) != ROW_FIELDS:
            raise PAVolumeSourceTruthError(
                f"projection row {index} does not match the positive schema"
            )
        game_pk = _canonical_positive_int(raw["game_pk"], "game_pk")
        official_date = _canonical_2023_date(raw["official_date"], "official_date")
        side = raw["side"]
        if side not in SIDES:
            raise PAVolumeSourceTruthError("side must be away or home")
        team_id = _canonical_positive_int(raw["team_id"], "team_id")
        player_id = _canonical_positive_int(raw["player_id"], "player_id")
        lineup_slot = _canonical_positive_int(raw["lineup_slot"], "lineup_slot")
        if lineup_slot not in SLOTS:
            raise PAVolumeSourceTruthError("lineup_slot must be in 1..9")
        out_pa = _canonical_nonnegative_int(raw["out_pa"], "out_pa")
        identity = (game_pk, side, player_id)
        if identity in identities:
            raise PAVolumeSourceTruthError("game/player identity is duplicated")
        identities.add(identity)
        normalized.append(
            {
                "game_pk": game_pk,
                "official_date": official_date,
                "side": side,
                "team_id": team_id,
                "player_id": player_id,
                "lineup_slot": lineup_slot,
                "out_pa": out_pa,
            }
        )
    if not normalized:
        raise PAVolumeSourceTruthError("official starter projection is empty")
    normalized.sort(
        key=lambda row: (
            row["official_date"],
            row["game_pk"],
            SIDES.index(row["side"]),
            row["lineup_slot"],
            row["player_id"],
        )
    )
    return normalized


def _validate_game_shape(rows: list[dict[str, Any]]) -> None:
    games: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        games[row["game_pk"]].append(row)
    for game_pk, game_rows in games.items():
        if len(game_rows) != 18:
            raise PAVolumeSourceTruthError(
                f"game {game_pk} does not contain exactly 18 original starters"
            )
        dates = {row["official_date"] for row in game_rows}
        if len(dates) != 1:
            raise PAVolumeSourceTruthError(f"game {game_pk} has conflicting dates")
        teams = {row["team_id"] for row in game_rows}
        if len(teams) != 2:
            raise PAVolumeSourceTruthError(f"game {game_pk} does not bind two teams")
        for side in SIDES:
            side_rows = [row for row in game_rows if row["side"] == side]
            if len(side_rows) != 9:
                raise PAVolumeSourceTruthError(
                    f"game {game_pk} {side} does not contain nine starters"
                )
            if {row["lineup_slot"] for row in side_rows} != set(SLOTS):
                raise PAVolumeSourceTruthError(
                    f"game {game_pk} {side} starter slots are incomplete or duplicated"
                )
            if len({row["team_id"] for row in side_rows}) != 1:
                raise PAVolumeSourceTruthError(
                    f"game {game_pk} {side} has conflicting team identity"
                )
        if {
            row["team_id"] for row in game_rows if row["side"] == "away"
        } & {row["team_id"] for row in game_rows if row["side"] == "home"}:
            raise PAVolumeSourceTruthError(
                f"game {game_pk} home and away team identities collide"
            )


def _distribution(counts: Counter[int]) -> dict[str, float]:
    total = sum(counts.values())
    if total <= 0:
        raise PAVolumeSourceTruthError("PA distribution has no support")
    return {str(pa): counts[pa] / total for pa in sorted(counts)}


def _counts(counts: Counter[int]) -> dict[str, int]:
    return {str(pa): counts[pa] for pa in sorted(counts)}


def build_pa_volume_artifact(
    *,
    rows: Iterable[Mapping[str, Any]],
    source_release_manifest_sha256: str,
    source_release_external_verification_sha256: str,
    dependency_lock_sha256: str,
    builder_source_sha256: str,
) -> dict[str, Any]:
    """Build a deterministic empirical artifact from an approved projection."""

    bindings = {
        "source_release_manifest_sha256": _sha(
            source_release_manifest_sha256, "source_release_manifest_sha256"
        ),
        "source_release_external_verification_sha256": _sha(
            source_release_external_verification_sha256,
            "source_release_external_verification_sha256",
        ),
        "dependency_lock_sha256": _sha(
            dependency_lock_sha256, "dependency_lock_sha256"
        ),
        "builder_source_sha256": _sha(builder_source_sha256, "builder_source_sha256"),
    }
    projection_rows = _normalize_rows(rows)
    _validate_game_shape(projection_rows)
    projection = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "season": 2023,
        "fields": sorted(ROW_FIELDS),
        "rows": projection_rows,
    }
    projection_sha256 = sha256_bytes(canonical_json_bytes(projection))
    by_slot_counts: dict[str, Counter[int]] = {
        str(slot): Counter() for slot in SLOTS
    }
    pooled_counts: Counter[int] = Counter()
    for row in projection_rows:
        pa = row["out_pa"]
        by_slot_counts[str(row["lineup_slot"])][pa] += 1
        pooled_counts[pa] += 1
    rows_by_slot = {
        slot: sum(counts.values()) for slot, counts in by_slot_counts.items()
    }
    if len(set(rows_by_slot.values())) != 1:
        raise PAVolumeSourceTruthError("lineup-slot row counts are inconsistent")
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": CANDIDATE_ID,
        "population": "original_sequence_zero_starters",
        "chronology": {
            "source_fit_seasons": [2023],
            "selection_performed": False,
            "selection_seasons": [],
            "outcome_scoring_performed": False,
        },
        "source": {
            "schema_version": PROJECTION_SCHEMA_VERSION,
            "season": 2023,
            "fields": sorted(ROW_FIELDS),
            "projection_sha256": projection_sha256,
            "fit_rows": len(projection_rows),
            "fit_games": len({row["game_pk"] for row in projection_rows}),
            "date_min": min(row["official_date"] for row in projection_rows),
            "date_max": max(row["official_date"] for row in projection_rows),
            "rows_by_lineup_slot": rows_by_slot,
            "bindings": bindings,
        },
        "counts_by_lineup_slot": {
            slot: _counts(counts) for slot, counts in by_slot_counts.items()
        },
        "pooled_counts": _counts(pooled_counts),
        "by_lineup_slot": {
            slot: _distribution(counts) for slot, counts in by_slot_counts.items()
        },
        "pooled": _distribution(pooled_counts),
        "research_only": True,
        "betting_authorized": False,
    }
    validate_pa_volume_artifact(artifact)
    return artifact


def _validate_probability_map(
    value: Any, counts: Mapping[str, int], label: str
) -> None:
    if not isinstance(value, Mapping) or set(value) != set(counts):
        raise PAVolumeSourceTruthError(f"{label} support differs from counts")
    total_counts = sum(counts.values())
    observed_total = 0.0
    for state, count in counts.items():
        if not isinstance(state, str) or not state.isdigit():
            raise PAVolumeSourceTruthError(f"{label} state is noncanonical")
        probability = value[state]
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(probability)
            or probability < 0.0
        ):
            raise PAVolumeSourceTruthError(f"{label} probability is invalid")
        expected = count / total_counts
        if probability != expected:
            raise PAVolumeSourceTruthError(f"{label} probability differs from counts")
        observed_total += float(probability)
    if not math.isclose(observed_total, 1.0, rel_tol=0.0, abs_tol=1e-15):
        raise PAVolumeSourceTruthError(f"{label} probabilities do not sum to one")


def validate_pa_volume_artifact(value: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "candidate_id",
        "population",
        "chronology",
        "source",
        "counts_by_lineup_slot",
        "pooled_counts",
        "by_lineup_slot",
        "pooled",
        "research_only",
        "betting_authorized",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise PAVolumeSourceTruthError("PA-volume artifact schema changed")
    if value["schema_version"] != SCHEMA_VERSION or value["candidate_id"] != CANDIDATE_ID:
        raise PAVolumeSourceTruthError("PA-volume artifact identity changed")
    if value["population"] != "original_sequence_zero_starters":
        raise PAVolumeSourceTruthError("PA-volume population changed")
    if value["chronology"] != {
        "source_fit_seasons": [2023],
        "selection_performed": False,
        "selection_seasons": [],
        "outcome_scoring_performed": False,
    }:
        raise PAVolumeSourceTruthError("PA-volume chronology changed")
    if value["research_only"] is not True or value["betting_authorized"] is not False:
        raise PAVolumeSourceTruthError("PA-volume safety state changed")
    source = value["source"]
    source_fields = {
        "schema_version",
        "season",
        "fields",
        "projection_sha256",
        "fit_rows",
        "fit_games",
        "date_min",
        "date_max",
        "rows_by_lineup_slot",
        "bindings",
    }
    if not isinstance(source, Mapping) or set(source) != source_fields:
        raise PAVolumeSourceTruthError("PA-volume source schema changed")
    if source["schema_version"] != PROJECTION_SCHEMA_VERSION or source["season"] != 2023:
        raise PAVolumeSourceTruthError("PA-volume source identity changed")
    if source["fields"] != sorted(ROW_FIELDS):
        raise PAVolumeSourceTruthError("PA-volume source fields changed")
    _sha(source["projection_sha256"], "projection_sha256")
    _canonical_positive_int(source["fit_rows"], "fit_rows")
    _canonical_positive_int(source["fit_games"], "fit_games")
    _canonical_2023_date(source["date_min"], "date_min")
    _canonical_2023_date(source["date_max"], "date_max")
    if source["date_min"] > source["date_max"]:
        raise PAVolumeSourceTruthError("PA-volume source dates are reversed")
    bindings = source["bindings"]
    expected_bindings = {
        "source_release_manifest_sha256",
        "source_release_external_verification_sha256",
        "dependency_lock_sha256",
        "builder_source_sha256",
    }
    if not isinstance(bindings, Mapping) or set(bindings) != expected_bindings:
        raise PAVolumeSourceTruthError("PA-volume source bindings changed")
    for key in expected_bindings:
        _sha(bindings[key], key)
    raw_counts = value["counts_by_lineup_slot"]
    raw_probs = value["by_lineup_slot"]
    expected_slots = {str(slot) for slot in SLOTS}
    if (
        not isinstance(raw_counts, Mapping)
        or set(raw_counts) != expected_slots
        or not isinstance(raw_probs, Mapping)
        or set(raw_probs) != expected_slots
    ):
        raise PAVolumeSourceTruthError("PA-volume lineup-slot coverage changed")
    rows_by_slot: dict[str, int] = {}
    for slot in sorted(expected_slots, key=int):
        counts = raw_counts[slot]
        if not isinstance(counts, Mapping) or not counts:
            raise PAVolumeSourceTruthError("PA-volume slot counts are missing")
        canonical_counts: dict[str, int] = {}
        for state, count in counts.items():
            if not isinstance(state, str) or not state.isdigit() or str(int(state)) != state:
                raise PAVolumeSourceTruthError("PA-volume count state is noncanonical")
            canonical_counts[state] = _canonical_positive_int(count, "PA count")
        _validate_probability_map(raw_probs[slot], canonical_counts, f"slot {slot}")
        rows_by_slot[slot] = sum(canonical_counts.values())
    if source["rows_by_lineup_slot"] != rows_by_slot:
        raise PAVolumeSourceTruthError("source lineup-slot counts differ from artifact")
    if sum(rows_by_slot.values()) != source["fit_rows"]:
        raise PAVolumeSourceTruthError("source fit_rows differs from artifact counts")
    pooled_counts = value["pooled_counts"]
    if not isinstance(pooled_counts, Mapping) or not pooled_counts:
        raise PAVolumeSourceTruthError("pooled PA counts are missing")
    canonical_pooled: dict[str, int] = {}
    for state, count in pooled_counts.items():
        if not isinstance(state, str) or not state.isdigit() or str(int(state)) != state:
            raise PAVolumeSourceTruthError("pooled count state is noncanonical")
        canonical_pooled[state] = _canonical_positive_int(count, "pooled PA count")
    recomputed: Counter[int] = Counter()
    for counts in raw_counts.values():
        recomputed.update({int(state): count for state, count in counts.items()})
    if canonical_pooled != _counts(recomputed):
        raise PAVolumeSourceTruthError("pooled PA counts differ from slot counts")
    _validate_probability_map(value["pooled"], canonical_pooled, "pooled")
