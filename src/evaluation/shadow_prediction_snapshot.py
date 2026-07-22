"""Validated decision-time model snapshot for the forward shadow ledger.

The raw daily archive is useful for outcome recording, but it becomes
forward-shadow evidence only when it was captured with immutable run
provenance and hard game/player identity. This adapter deliberately does not
repair old archives, infer a missing game key, or approximate an unavailable
tail probability. A ledger entry must be able to prove exactly which archived
model probability it used at decision time.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
from typing import Any


PROVENANCE_SCHEMA = "daily-prediction-provenance-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ShadowPredictionSnapshotError(ValueError):
    """Raised when an archive cannot prove decision-time model provenance."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: Any, label: str) -> str:
    text = str(value).strip().lower()
    if not _SHA256.fullmatch(text):
        raise ShadowPredictionSnapshotError(f"{label} must be a SHA-256 digest")
    return text


def _positive_int(value: Any, label: str) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise ShadowPredictionSnapshotError(f"{label} must be an integer") from exc
    if out <= 0:
        raise ShadowPredictionSnapshotError(f"{label} must be positive")
    return out


def _iso_date(value: Any, label: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ShadowPredictionSnapshotError(f"{label} must be YYYY-MM-DD") from exc


def _utc(value: Any, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ShadowPredictionSnapshotError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ShadowPredictionSnapshotError(f"{label} must include a timezone")
    return parsed.isoformat()


def _line_threshold(line: Any) -> int:
    """Return the integer count that wins an OVER at ``line``.

    Archived simulations expose P(actual >= integer count). The contract is
    therefore an OVER-only conversion: over ``1.5`` needs at least two, over
    ``1.0`` needs at least two after a push, and so on. A missing matching tail
    remains missing; this function never substitutes a normal approximation.
    """

    try:
        value = Decimal(str(line))
    except (InvalidOperation, ValueError) as exc:
        raise ShadowPredictionSnapshotError("line must be a finite decimal") from exc
    if not value.is_finite() or value < 0:
        raise ShadowPredictionSnapshotError("line must be finite and non-negative")
    return int(value.to_integral_value(rounding=ROUND_FLOOR)) + 1


def _probabilities(raw: Any, label: str) -> dict[int, float]:
    if not isinstance(raw, dict) or not raw:
        raise ShadowPredictionSnapshotError(f"{label}.p_ge_threshold must be a non-empty object")
    out: dict[int, float] = {}
    for key, value in raw.items():
        try:
            threshold = Decimal(str(key))
            probability = float(value)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ShadowPredictionSnapshotError(f"{label}.p_ge_threshold is malformed") from exc
        if not threshold.is_finite() or threshold != threshold.to_integral_value() or threshold <= 0:
            raise ShadowPredictionSnapshotError(f"{label}.p_ge_threshold has an invalid threshold")
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ShadowPredictionSnapshotError(f"{label}.p_ge_threshold has an invalid probability")
        integer = int(threshold)
        if integer in out:
            raise ShadowPredictionSnapshotError(f"{label}.p_ge_threshold repeats threshold {integer}")
        out[integer] = probability
    return out


@dataclass(frozen=True)
class ShadowPrediction:
    mlb_game_pk: int
    player_id: int
    category: str
    game_date: str
    p_ge_threshold: dict[int, float]

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.mlb_game_pk, self.player_id, self.category)

    def p_over(self, line: Any) -> float:
        threshold = _line_threshold(line)
        try:
            return self.p_ge_threshold[threshold]
        except KeyError as exc:
            raise ShadowPredictionSnapshotError(
                f"archive has no exact P(actual >= {threshold}) for {self.key}; "
                "do not approximate a market-line tail"
            ) from exc


@dataclass(frozen=True)
class ShadowPredictionSnapshot:
    archive_path: Path
    prediction_artifact_sha256: str
    game_date: str
    model_version: str
    effective_config_sha256: str
    code_snapshot_sha256: str
    captured_at_utc: str
    predictions: tuple[ShadowPrediction, ...]

    def probability_for(self, mlb_game_pk: Any, player_id: Any, category: Any, line: Any) -> float:
        key = (_positive_int(mlb_game_pk, "mlb_game_pk"), _positive_int(player_id, "player_id"), str(category).strip())
        matches = [row for row in self.predictions if row.key == key]
        if not matches:
            raise ShadowPredictionSnapshotError(
                f"archive has no hard-keyed prediction for MARKET key {key}; do not name-match"
            )
        if len(matches) != 1:  # Defensive: loader rejects this too.
            raise ShadowPredictionSnapshotError(f"archive ambiguously repeats prediction key {key}")
        return matches[0].p_over(line)


def load_shadow_prediction_snapshot(path: str | Path) -> ShadowPredictionSnapshot:
    """Load one eligible archive; reject stale, anonymous, or ambiguous input."""

    archive_path = Path(path)
    if not archive_path.is_file():
        raise ShadowPredictionSnapshotError(f"prediction archive does not exist: {archive_path}")
    try:
        raw = json.loads(archive_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ShadowPredictionSnapshotError("prediction archive is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise ShadowPredictionSnapshotError("prediction archive root is not an object")

    game_date = _iso_date(raw.get("game_date"), "archive game_date")
    provenance = raw.get("prediction_provenance")
    if not isinstance(provenance, dict):
        raise ShadowPredictionSnapshotError(
            "archive lacks decision-time prediction_provenance; old archives cannot be backfilled"
        )
    if provenance.get("schema_version") != PROVENANCE_SCHEMA:
        raise ShadowPredictionSnapshotError("archive provenance has an unknown schema")
    captured_at = _utc(provenance.get("captured_at_utc"), "archive provenance captured_at_utc")
    model_version = str(provenance.get("model_version", "")).strip()
    if not model_version:
        raise ShadowPredictionSnapshotError("archive provenance lacks model_version")
    config_sha = _hash(provenance.get("effective_config_sha256"), "effective_config_sha256")
    code = provenance.get("code")
    if not isinstance(code, dict) or code.get("status") != "available":
        raise ShadowPredictionSnapshotError("archive provenance lacks an available code snapshot")
    code_sha = _hash(code.get("snapshot_sha256"), "code.snapshot_sha256")

    projections: list[ShadowPrediction] = []
    seen: set[tuple[int, int, str]] = set()
    for section in ("hitter_projections", "pitcher_projections"):
        values = raw.get(section, [])
        if not isinstance(values, list):
            raise ShadowPredictionSnapshotError(f"archive {section} is not a list")
        for index, row in enumerate(values):
            label = f"{section}[{index}]"
            if not isinstance(row, dict):
                raise ShadowPredictionSnapshotError(f"{label} is not an object")
            projection_date = _iso_date(row.get("game_date"), f"{label}.game_date")
            if projection_date != game_date:
                raise ShadowPredictionSnapshotError(f"{label}.game_date differs from archive game_date")
            category = str(row.get("category", "")).strip()
            if not category:
                raise ShadowPredictionSnapshotError(f"{label}.category is blank")
            simulation = row.get("simulation")
            if not isinstance(simulation, dict):
                raise ShadowPredictionSnapshotError(f"{label}.simulation is missing")
            prediction = ShadowPrediction(
                mlb_game_pk=_positive_int(row.get("mlb_game_pk"), f"{label}.mlb_game_pk"),
                player_id=_positive_int(row.get("player_id"), f"{label}.player_id"),
                category=category,
                game_date=projection_date,
                p_ge_threshold=_probabilities(simulation.get("p_ge_threshold"), label),
            )
            if prediction.key in seen:
                raise ShadowPredictionSnapshotError(f"archive repeats hard prediction key {prediction.key}")
            seen.add(prediction.key)
            projections.append(prediction)
    if not projections:
        raise ShadowPredictionSnapshotError("archive has no projections")

    return ShadowPredictionSnapshot(
        archive_path=archive_path,
        prediction_artifact_sha256=_sha256(archive_path),
        game_date=game_date,
        model_version=model_version,
        effective_config_sha256=config_sha,
        code_snapshot_sha256=code_sha,
        captured_at_utc=captured_at,
        predictions=tuple(projections),
    )
