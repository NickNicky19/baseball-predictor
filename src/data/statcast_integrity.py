"""Fail-closed contracts for Statcast batted-ball evidence.

The two public rates in this module deliberately share the same measured-exit-
velocity denominator.  A Statcast barrel (launch_speed_angle == 6) is a subset
of hard-hit batted balls, so a source row or aggregate that violates that set
relationship is not safe to consume as a probability input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import re
from typing import Any, Mapping, Optional

import pandas as pd


class StatcastIntegrityError(ValueError):
    """Raised when batted-ball evidence is contradictory or unprovable."""


_VALID_SOURCE_STATUSES = {
    "unverified",
    "observed",
    "partial_league_fallback",
    "league_fallback",
}

STATCAST_LEAGUE_FALLBACK_FIELDS = frozenset(
    {
        "xwoba",
        "xslg",
        "barrel_rate",
        "sweet_spot_rate",
        "hard_hit_rate",
        "chase_rate",
        "contact_rate",
        "whiff_rate",
        "swing_rate",
        "zone_rate",
        "k_rate",
        "bb_rate",
    }
)

RICH_FEATURE_LINEAGE_KEY = "__lineage__"
RICH_FEATURE_LINEAGE_SCHEMA = "rich-feature-lineage-v1"
RICH_PROFILE_OVERRIDE_FIELDS = frozenset(
    {"xwoba", "xslg", "xba", "barrel_rate", "hard_hit_rate", "contact_rate"}
)
RICH_ROLLING_PROBABILITY_FIELDS = frozenset({"roll15_xwoba", "recent_pa_15"})
RICH_ADAPTER_PROBABILITY_FIELDS = frozenset({"contact_xba_fitted"})
RICH_PROBABILITY_FIELDS = frozenset(
    RICH_PROFILE_OVERRIDE_FIELDS
    | RICH_ROLLING_PROBABILITY_FIELDS
    | RICH_ADAPTER_PROBABILITY_FIELDS
)
HITTER_RATE_LINEAGE_SCHEMA = "hitter-rate-lineage-v1"
HITTER_RATE_FIELDS = ("k_rate", "bb_rate", "k_rate_recent", "bb_rate_recent")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class BattedBallEvidence:
    measured_batted_balls: int
    barrel_count: int
    hard_hit_count: int
    barrel_rate: float
    hard_hit_rate: float
    classification: str


def derive_batted_ball_evidence(frame: pd.DataFrame) -> Optional[BattedBallEvidence]:
    """Derive coherent barrel/hard-hit counts over one common population.

    Only Statcast's launch-speed-angle bucket is accepted as the barrel label.
    The old path averaged a sparse ``barrel`` column independently from the
    ``hard_hit`` column, allowing incompatible denominators to reach the model.
    Rows without measured exit velocity are outside both rate denominators.
    """

    if frame.empty or "launch_speed" not in frame.columns:
        return None
    if "launch_speed_angle" not in frame.columns:
        return None

    # Statcast can expose contact speed on foul balls. Barrel and hard-hit
    # percentages are BBE metrics, so their population is balls put in play,
    # represented by pitch ``type == X`` when that source field is available.
    work = frame
    if "type" in frame.columns:
        work = frame.loc[frame["type"].astype("string").eq("X")]
    if work.empty:
        return None

    exit_velocity = pd.to_numeric(work["launch_speed"], errors="coerce")
    speed_angle = pd.to_numeric(work["launch_speed_angle"], errors="coerce")
    measured = exit_velocity.notna() & (exit_velocity > 0.0)
    denominator = int(measured.sum())
    if denominator == 0:
        return None

    barrel_mask = speed_angle.eq(6)
    if bool((barrel_mask & ~measured).any()):
        raise StatcastIntegrityError(
            "Statcast barrel classification exists without measured exit velocity"
        )
    if bool((barrel_mask & (exit_velocity < 95.0)).any()):
        raise StatcastIntegrityError(
            "Statcast barrel classification contradicts the hard-hit threshold"
        )

    barrel_count = int((barrel_mask & measured).sum())
    hard_hit_count = int((measured & exit_velocity.ge(95.0)).sum())
    if barrel_count > hard_hit_count:
        raise StatcastIntegrityError("barrel_count exceeds hard_hit_count")

    return BattedBallEvidence(
        measured_batted_balls=denominator,
        barrel_count=barrel_count,
        hard_hit_count=hard_hit_count,
        barrel_rate=barrel_count / denominator,
        hard_hit_rate=hard_hit_count / denominator,
        classification="statcast_type_x_launch_speed_angle_6_over_measured_ev",
    )


def validate_rate_pair(
    barrel_rate: Any,
    hard_hit_rate: Any,
    *,
    context: str,
    allow_both_missing: bool = True,
) -> None:
    """Reject invalid or internally contradictory aggregate rate pairs."""

    if barrel_rate is None and hard_hit_rate is None and allow_both_missing:
        return
    if barrel_rate is None or hard_hit_rate is None:
        raise StatcastIntegrityError(
            f"{context}: barrel_rate and hard_hit_rate must be present together"
        )
    try:
        barrel = float(barrel_rate)
        hard_hit = float(hard_hit_rate)
    except (TypeError, ValueError) as exc:
        raise StatcastIntegrityError(f"{context}: batted-ball rates are not numeric") from exc
    if not math.isfinite(barrel) or not math.isfinite(hard_hit):
        raise StatcastIntegrityError(f"{context}: batted-ball rates are non-finite")
    if not 0.0 <= barrel <= 1.0 or not 0.0 <= hard_hit <= 1.0:
        raise StatcastIntegrityError(f"{context}: batted-ball rates are outside [0,1]")
    if barrel > hard_hit + 1e-12:
        raise StatcastIntegrityError(
            f"{context}: barrel_rate {barrel:.12g} exceeds hard_hit_rate {hard_hit:.12g}"
        )


def validate_profile_and_rich_features(
    profile: Any,
    rich: Optional[Mapping[str, Any]],
    *,
    context: str,
) -> None:
    """Validate both profile values and the effective rich-feature overrides."""

    source_status = getattr(profile, "source_status", "unverified")
    fallback_fields = tuple(getattr(profile, "fallback_fields", ()) or ())
    if source_status not in _VALID_SOURCE_STATUSES:
        raise StatcastIntegrityError(
            f"{context}.statcast: invalid source_status {source_status!r}"
        )
    if len(fallback_fields) != len(set(fallback_fields)) or any(
        not isinstance(field, str) or not field for field in fallback_fields
    ):
        raise StatcastIntegrityError(
            f"{context}.statcast: invalid fallback field lineage"
        )
    unknown_fallback_fields = sorted(
        set(fallback_fields).difference(STATCAST_LEAGUE_FALLBACK_FIELDS)
    )
    if unknown_fallback_fields:
        raise StatcastIntegrityError(
            f"{context}.statcast: unknown fallback fields {unknown_fallback_fields}"
        )
    sample_pa = getattr(profile, "sample_pa", 0)
    if source_status == "observed" and fallback_fields:
        raise StatcastIntegrityError(
            f"{context}.statcast: observed profile cannot carry fallback fields"
        )
    if source_status == "partial_league_fallback" and (
        sample_pa <= 0 or not fallback_fields
    ):
        raise StatcastIntegrityError(
            f"{context}.statcast: invalid partial fallback lineage"
        )
    if source_status == "league_fallback" and (
        sample_pa != 0 or not fallback_fields
    ):
        raise StatcastIntegrityError(
            f"{context}.statcast: invalid league fallback lineage"
        )
    if source_status == "unverified" and fallback_fields:
        raise StatcastIntegrityError(
            f"{context}.statcast: unverified profile cannot claim fallback lineage"
        )
    source_hash = getattr(profile, "source_hash", None)
    source_max_value = getattr(profile, "source_max_game_date", None)
    source_cutoff_value = getattr(profile, "source_cutoff_date", None)
    if source_status in {"observed", "partial_league_fallback"}:
        if not isinstance(source_hash, str) or _SHA256_RE.fullmatch(source_hash) is None:
            raise StatcastIntegrityError(
                f"{context}.statcast: source-built profile lacks a valid source hash"
            )
        if source_max_value is None or source_cutoff_value is None:
            raise StatcastIntegrityError(
                f"{context}.statcast: source-built profile lacks date lineage"
            )
        source_max = _lineage_date(
            source_max_value, context, "statcast", "source_max_game_date"
        )
        source_cutoff = _lineage_date(
            source_cutoff_value, context, "statcast", "source_cutoff_date"
        )
        if source_max > source_cutoff:
            raise StatcastIntegrityError(
                f"{context}.statcast: source rows exceed the bound cutoff"
            )
        for parsed, label in (
            (source_max, "source_max_game_date"),
            (source_cutoff, "source_cutoff_date"),
        ):
            if date(2026, 5, 1) <= parsed <= date(2026, 5, 31):
                raise StatcastIntegrityError(
                    f"{context}.statcast: {label} enters sealed May 2026"
                )
    elif source_hash is not None and (
        not isinstance(source_hash, str) or _SHA256_RE.fullmatch(source_hash) is None
    ):
        raise StatcastIntegrityError(
            f"{context}.statcast: invalid optional source hash"
        )

    validate_rate_pair(
        getattr(profile, "barrel_rate", None),
        getattr(profile, "hard_hit_rate", None),
        context=f"{context}.statcast",
    )
    denominator = getattr(profile, "batted_ball_denominator", None)
    barrel_count = getattr(profile, "barrel_count", None)
    hard_hit_count = getattr(profile, "hard_hit_count", None)
    count_values = (denominator, barrel_count, hard_hit_count)
    if any(value is not None for value in count_values):
        if any(value is None for value in count_values):
            raise StatcastIntegrityError(
                f"{context}.statcast: partial batted-ball count lineage"
            )
        if any(not isinstance(value, int) or value < 0 for value in count_values):
            raise StatcastIntegrityError(
                f"{context}.statcast: invalid batted-ball count lineage"
            )
        if barrel_count > hard_hit_count or hard_hit_count > denominator:
            raise StatcastIntegrityError(
                f"{context}.statcast: impossible batted-ball count ordering"
            )
        if denominator == 0:
            raise StatcastIntegrityError(
                f"{context}.statcast: zero denominator cannot carry rates"
            )
        if not math.isclose(
            float(getattr(profile, "barrel_rate")),
            barrel_count / denominator,
            rel_tol=0.0,
            abs_tol=1e-12,
        ) or not math.isclose(
            float(getattr(profile, "hard_hit_rate")),
            hard_hit_count / denominator,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise StatcastIntegrityError(
                f"{context}.statcast: count/rate serialization mismatch"
            )
    distribution = getattr(profile, "distribution", None)
    if distribution is not None:
        validate_rate_pair(
            getattr(distribution, "barrel_rate", None),
            getattr(distribution, "hard_hit_rate", None),
            context=f"{context}.distribution",
        )
    if rich is None:
        return
    _validate_rich_feature_lineage(profile, rich, context=context)
    effective_barrel = rich.get("barrel_rate", getattr(profile, "barrel_rate", None))
    effective_hard_hit = rich.get(
        "hard_hit_rate", getattr(profile, "hard_hit_rate", None)
    )
    validate_rate_pair(
        effective_barrel,
        effective_hard_hit,
        context=f"{context}.effective_rich_features",
    )


def sha256_feature_value(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_hitter_rate_lineage(profile: Any, *, context: str) -> None:
    lineage = getattr(profile, "field_lineage", None)
    if not isinstance(lineage, Mapping):
        raise StatcastIntegrityError(f"{context}: hitter-rate lineage is missing")
    player_id = int(getattr(profile, "player_id"))
    for field in HITTER_RATE_FIELDS:
        value = getattr(profile, field, None)
        if value is None:
            continue
        entry = lineage.get(field)
        if not isinstance(entry, Mapping):
            raise StatcastIntegrityError(
                f"{context}.{field}: point-in-time lineage is missing"
            )
        if entry.get("source") != "mlb_game_log_point_in_time":
            raise StatcastIntegrityError(
                f"{context}.{field}: unapproved hitter-rate source"
            )
        if entry.get("player_id") != player_id:
            raise StatcastIntegrityError(
                f"{context}.{field}: player identity mismatch"
            )
        if entry.get("value_sha256") != sha256_feature_value(value):
            raise StatcastIntegrityError(
                f"{context}.{field}: value/lineage hash mismatch"
            )
        target = _lineage_date(entry.get("target_date"), context, field, "target_date")
        cutoff = _lineage_date(
            entry.get("source_cutoff_date"), context, field, "source_cutoff_date"
        )
        if cutoff >= target:
            raise StatcastIntegrityError(
                f"{context}.{field}: source cutoff is not pregame"
            )
        source_max_value = entry.get("source_max_game_date")
        if source_max_value is not None:
            source_max = _lineage_date(
                source_max_value, context, field, "source_max_game_date"
            )
            if source_max > cutoff:
                raise StatcastIntegrityError(
                    f"{context}.{field}: source rows exceed the cutoff"
                )
        for parsed in (target, cutoff):
            if date(2026, 5, 1) <= parsed <= date(2026, 5, 31):
                raise StatcastIntegrityError(
                    f"{context}.{field}: hitter-rate lineage enters sealed May 2026"
                )
        source_hash = entry.get("source_hash")
        if not isinstance(source_hash, str) or _SHA256_RE.fullmatch(source_hash) is None:
            raise StatcastIntegrityError(
                f"{context}.{field}: invalid source hash"
            )
        denominator = entry.get("denominator")
        numerator = entry.get("numerator")
        if (
            not isinstance(denominator, int)
            or isinstance(denominator, bool)
            or denominator <= 0
            or not isinstance(numerator, int)
            or isinstance(numerator, bool)
            or not 0 <= numerator <= denominator
        ):
            raise StatcastIntegrityError(
                f"{context}.{field}: invalid count/denominator lineage"
            )
        if not math.isclose(
            float(value), numerator / denominator, rel_tol=0.0, abs_tol=1e-12
        ):
            raise StatcastIntegrityError(
                f"{context}.{field}: count/rate mismatch"
            )


def _validate_rich_feature_lineage(
    profile: Any,
    rich: Mapping[str, Any],
    *,
    context: str,
) -> None:
    active = {
        field
        for field in (RICH_PROFILE_OVERRIDE_FIELDS | RICH_ADAPTER_PROBABILITY_FIELDS)
        if rich.get(field) is not None
    }
    if rich.get("roll15_xwoba") is not None and float(
        rich.get("recent_pa_15") or 0.0
    ) > 0.0:
        active.update(RICH_ROLLING_PROBABILITY_FIELDS)
    # Frozen/legacy artifacts predate the lineage schema and remain valid
    # comparators. Every new source-built profile is strict.
    strict = getattr(profile, "source_status", "unverified") != "unverified"
    if not active and RICH_FEATURE_LINEAGE_KEY not in rich:
        return
    block = rich.get(RICH_FEATURE_LINEAGE_KEY)
    if not isinstance(block, Mapping):
        if strict and active:
            raise StatcastIntegrityError(
                f"{context}.rich: probability override lacks lineage"
            )
        return
    if block.get("schema_version") != RICH_FEATURE_LINEAGE_SCHEMA:
        raise StatcastIntegrityError(f"{context}.rich: invalid lineage schema")
    fields = block.get("fields")
    if not isinstance(fields, Mapping):
        raise StatcastIntegrityError(f"{context}.rich: lineage fields are missing")
    missing = sorted(active.difference(fields))
    if missing:
        raise StatcastIntegrityError(
            f"{context}.rich: probability overrides lack lineage: {missing}"
        )

    player_id = int(getattr(profile, "player_id"))
    for field in sorted(active):
        entry = fields[field]
        if not isinstance(entry, Mapping):
            raise StatcastIntegrityError(
                f"{context}.rich.{field}: lineage entry is not an object"
            )
        if entry.get("player_id") != player_id:
            raise StatcastIntegrityError(
                f"{context}.rich.{field}: player identity mismatch"
            )
        if entry.get("value_sha256") != sha256_feature_value(rich[field]):
            raise StatcastIntegrityError(
                f"{context}.rich.{field}: value/lineage hash mismatch"
            )
        target = _lineage_date(entry.get("target_date"), context, field, "target_date")
        cutoff = _lineage_date(
            entry.get("source_cutoff_date"), context, field, "source_cutoff_date"
        )
        if cutoff >= target:
            raise StatcastIntegrityError(
                f"{context}.rich.{field}: source cutoff is not pregame"
            )
        for parsed, label in ((target, "target_date"), (cutoff, "source_cutoff_date")):
            if date(2026, 5, 1) <= parsed <= date(2026, 5, 31):
                raise StatcastIntegrityError(
                    f"{context}.rich.{field}: {label} enters sealed May 2026"
                )
        max_date_value = entry.get("source_max_game_date")
        if max_date_value is not None:
            source_max = _lineage_date(
                max_date_value, context, field, "source_max_game_date"
            )
            if source_max > cutoff:
                raise StatcastIntegrityError(
                    f"{context}.rich.{field}: source rows exceed the cutoff"
                )
            if date(2026, 5, 1) <= source_max <= date(2026, 5, 31):
                raise StatcastIntegrityError(
                    f"{context}.rich.{field}: source rows enter sealed May 2026"
                )
        sample_count = entry.get("sample_count")
        if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 0:
            raise StatcastIntegrityError(
                f"{context}.rich.{field}: invalid sample count"
            )
        fallback_reason = entry.get("fallback_reason")
        source_hash = entry.get("source_hash")
        if source_hash is None:
            if not isinstance(fallback_reason, str) or not fallback_reason:
                raise StatcastIntegrityError(
                    f"{context}.rich.{field}: missing source hash without fallback reason"
                )
        elif not isinstance(source_hash, str) or _SHA256_RE.fullmatch(source_hash) is None:
            raise StatcastIntegrityError(
                f"{context}.rich.{field}: invalid source hash"
            )
        if field in RICH_PROFILE_OVERRIDE_FIELDS:
            profile_value = getattr(profile, field, None)
            if profile_value is None or float(rich[field]) != float(profile_value):
                raise StatcastIntegrityError(
                    f"{context}.rich.{field}: override differs from bound profile value"
                )
            if sample_count != int(getattr(profile, "sample_pa", 0)):
                raise StatcastIntegrityError(
                    f"{context}.rich.{field}: profile sample count mismatch"
                )
            profile_hash = getattr(profile, "source_hash", None)
            if profile_hash is not None and source_hash != profile_hash:
                raise StatcastIntegrityError(
                    f"{context}.rich.{field}: profile source hash mismatch"
                )
            if entry.get("source_cutoff_date") != getattr(
                profile, "source_cutoff_date", None
            ):
                raise StatcastIntegrityError(
                    f"{context}.rich.{field}: profile cutoff mismatch"
                )
        if field in {"barrel_rate", "hard_hit_rate"} and fallback_reason is None:
            denominator = getattr(profile, "batted_ball_denominator", None)
            if entry.get("denominator") != denominator or not isinstance(denominator, int):
                raise StatcastIntegrityError(
                    f"{context}.rich.{field}: batted-ball denominator mismatch"
                )


def _lineage_date(value: Any, context: str, field: str, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise StatcastIntegrityError(
            f"{context}.rich.{field}: invalid {label}"
        ) from exc
