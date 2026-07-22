"""Factual input-health records for hitter projections.

This module intentionally does *not* calculate an uncertainty score or decide
whether a player is bettable.  Those require calibrated, market-scored
evidence.  Its job is narrower: preserve the observable input/fallback state
that affects a projection so fallback rates can be measured by date and
projection category and shown honestly in the GUI later.

The records are derived from the exact ``PlayerFeatureBundle`` consumed by the
simulation.  No simulation values, probabilities, or ranking logic are
changed here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

from src.models.dataclasses import PlayerFeatureBundle, PropCategory
from src.data.statcast_integrity import (
    RICH_ADAPTER_PROBABILITY_FIELDS,
    RICH_FEATURE_LINEAGE_KEY,
    RICH_PROFILE_OVERRIDE_FIELDS,
    RICH_ROLLING_PROBABILITY_FIELDS,
)


HEALTH_SCHEMA_VERSION = "prediction-input-health-v3"


@dataclass(frozen=True)
class PredictionInputHealth:
    """Observable source/fallback facts for one hitter-game feature bundle.

    ``flags`` deliberately contains facts rather than a weighted grade.  For
    example, ``opposing_pitcher_kbb_league_fallback`` means the simulation will
    use league K/BB rates for the missing component; it does not assert that
    the projection is poor or unbettable.
    """

    schema_version: str
    game_date: str
    mlb_game_pk: int
    player_id: int
    player_name: str
    lineup_status: str
    lineup_slot: int | None
    expected_pa: float
    statcast_sample_pa: int
    hitter_has_advanced_statcast: bool
    hitter_statcast_source_status: str
    hitter_statcast_fallback_fields: tuple[str, ...]
    hitter_rate_lineage_fields: tuple[str, ...]
    hitter_rate_lineage_complete: bool
    opposing_pitcher_id: int | None
    opposing_pitcher_payload_present: bool
    opposing_pitcher_sample_pa: int
    opposing_pitcher_profile_present: bool
    opposing_pitcher_k_rate_present: bool
    opposing_pitcher_bb_rate_present: bool
    opposing_pitcher_hr_rate_present: bool
    pitcher_matchup_status: str
    rich_features_payload_present: bool
    rich_feature_lineage_present: bool
    rich_probability_lineage_complete: bool
    rolling_features_present: bool
    flags: tuple[str, ...]

    def to_dict(self) -> dict:
        row = asdict(self)
        row["flags"] = list(self.flags)
        return row


def health_for_bundle(bundle: PlayerFeatureBundle) -> PredictionInputHealth:
    """Return only facts that are visible at the simulation boundary.

    This mirrors :meth:`PropEngine._bundle_to_sim_input` for missing opponent
    pitcher K/BB inputs and lineup-slot handling.  It must remain a factual
    description of the live boundary, not a second simulation implementation.
    """

    pitcher = bundle.pitcher_statcast
    raw_slot = bundle.hitter.lineup_slot
    # This must use the exact coercion contract at PropEngine's simulation
    # boundary.  A health audit that labels ``"3"`` as a legacy fallback while
    # the engine uses slot 3 would be a metric blind to the code path it claims
    # to observe.
    try:
        slot = int(raw_slot) if raw_slot is not None and 1 <= int(raw_slot) <= 9 else None
    except (TypeError, ValueError):
        slot = None
    slot_is_valid = slot is not None
    has_advanced_statcast = bundle.statcast.has_advanced_data()
    rich_features = bundle.metadata.get("rich_features")
    rich_features_payload_present = isinstance(rich_features, dict) and bool(rich_features)
    lineage = (
        rich_features.get(RICH_FEATURE_LINEAGE_KEY)
        if isinstance(rich_features, dict)
        else None
    )
    lineage_fields = lineage.get("fields") if isinstance(lineage, dict) else None
    rich_feature_lineage_present = isinstance(lineage_fields, dict)
    active_rich_probability_fields = {
        field
        for field in (RICH_PROFILE_OVERRIDE_FIELDS | RICH_ADAPTER_PROBABILITY_FIELDS)
        if isinstance(rich_features, dict) and rich_features.get(field) is not None
    }
    if (
        isinstance(rich_features, dict)
        and rich_features.get("roll15_xwoba") is not None
        and float(rich_features.get("recent_pa_15") or 0.0) > 0.0
    ):
        active_rich_probability_fields.update(RICH_ROLLING_PROBABILITY_FIELDS)
    rich_probability_lineage_complete = bool(
        rich_feature_lineage_present
        and active_rich_probability_fields.issubset(lineage_fields)
    )
    rolling_features_present = bool(
        rich_features_payload_present
        and any(
            float(rich_features.get(key) or 0) > 0
            for key in ("recent_pa_15", "recent_pa_30")
        )
        and any(
            value is not None
            for key, value in rich_features.items()
            if key.startswith("roll") and key not in ("recent_pa_15", "recent_pa_30")
        )
    )
    pitcher_payload_present = pitcher is not None
    pitcher_sample_pa = int(pitcher.sample_pa) if pitcher is not None else 0
    pitcher_profile_present = pitcher_payload_present and pitcher_sample_pa > 0
    pitcher_matchup_status = str(
        bundle.metadata.get("pitcher_matchup_status", "legacy_unverified")
    )
    if pitcher_matchup_status not in {
        "legacy_unverified",
        "excluded_missing_receipt",
        "excluded_profile_unavailable",
        "receipt_and_profile_verified",
    }:
        pitcher_matchup_status = "invalid_status"

    flags: list[str] = [f"lineup_{bundle.hitter.game.lineup_status}"]
    if slot_is_valid:
        flags.append("fitted_lineup_slot_pa")
    else:
        # PropEngine passes None for an invalid slot, which activates its
        # legacy PA path.  This is an observable code-path fact, not an
        # assertion about why the slot was unavailable.
        flags.append("legacy_lineup_slot_pa_fallback")

    if has_advanced_statcast:
        flags.append("hitter_advanced_statcast")
    else:
        flags.append("hitter_statcast_league_fallback")
    source_status = bundle.statcast.source_status
    fallback_fields = tuple(bundle.statcast.fallback_fields)
    hitter_rate_lineage_fields = tuple(sorted(bundle.statcast.field_lineage))
    active_hitter_rate_fields = {
        field
        for field in ("k_rate", "bb_rate", "k_rate_recent", "bb_rate_recent")
        if getattr(bundle.statcast, field, None) is not None
    }
    hitter_rate_lineage_complete = bool(
        active_hitter_rate_fields
        and active_hitter_rate_fields.issubset(bundle.statcast.field_lineage)
    )
    flags.append(f"hitter_statcast_source_{source_status}")
    flags.extend(f"hitter_statcast_fallback_{field}" for field in fallback_fields)
    flags.append(
        "hitter_rate_lineage_complete"
        if hitter_rate_lineage_complete
        else "hitter_rate_lineage_unavailable"
    )
    flags.append(f"pitcher_matchup_{pitcher_matchup_status}")

    if not pitcher_payload_present:
        flags.extend(
            (
                "opposing_pitcher_payload_missing",
                "opposing_pitcher_kbb_league_fallback",
                "opposing_pitcher_hr_rate_missing",
            )
        )
    else:
        flags.append("opposing_pitcher_payload_present")
        if pitcher_profile_present:
            flags.append("opposing_pitcher_observed_profile")
        else:
            flags.append("opposing_pitcher_league_fallback_profile")
        if not pitcher_profile_present or pitcher.k_rate is None or pitcher.bb_rate is None:
            flags.append("opposing_pitcher_kbb_league_fallback")
        else:
            flags.append("opposing_pitcher_kbb_observed_profile")
        if not pitcher_profile_present:
            flags.append("opposing_pitcher_hr_league_fallback")
        elif pitcher.hr_per_9 is None:
            flags.append("opposing_pitcher_hr_rate_missing")
        else:
            flags.append("opposing_pitcher_hr_observed_profile")

    flags.append(
        "rich_features_payload_present"
        if rich_features_payload_present
        else "rich_features_payload_missing"
    )
    flags.append(
        "rich_probability_lineage_complete"
        if rich_probability_lineage_complete
        else "rich_probability_lineage_unavailable"
    )
    flags.append(
        "rolling_features_observed"
        if rolling_features_present
        else "rolling_features_unavailable"
    )

    return PredictionInputHealth(
        schema_version=HEALTH_SCHEMA_VERSION,
        game_date=bundle.hitter.game.game_date,
        mlb_game_pk=bundle.hitter.game.game_pk,
        player_id=bundle.hitter.player.mlb_id,
        player_name=bundle.hitter.player.name,
        lineup_status=bundle.hitter.game.lineup_status,
        lineup_slot=slot,
        expected_pa=bundle.expected_pa,
        statcast_sample_pa=bundle.statcast.sample_pa,
        hitter_has_advanced_statcast=has_advanced_statcast,
        hitter_statcast_source_status=source_status,
        hitter_statcast_fallback_fields=fallback_fields,
        hitter_rate_lineage_fields=hitter_rate_lineage_fields,
        hitter_rate_lineage_complete=hitter_rate_lineage_complete,
        opposing_pitcher_id=bundle.hitter.opposing_pitcher_id,
        opposing_pitcher_payload_present=pitcher_payload_present,
        opposing_pitcher_sample_pa=pitcher_sample_pa,
        opposing_pitcher_profile_present=pitcher_profile_present,
        opposing_pitcher_k_rate_present=bool(
            pitcher_profile_present and pitcher and pitcher.k_rate is not None
        ),
        opposing_pitcher_bb_rate_present=bool(
            pitcher_profile_present and pitcher and pitcher.bb_rate is not None
        ),
        opposing_pitcher_hr_rate_present=bool(
            pitcher_profile_present and pitcher and pitcher.hr_per_9 is not None
        ),
        pitcher_matchup_status=pitcher_matchup_status,
        rich_features_payload_present=rich_features_payload_present,
        rich_feature_lineage_present=rich_feature_lineage_present,
        rich_probability_lineage_complete=rich_probability_lineage_complete,
        rolling_features_present=rolling_features_present,
        flags=tuple(sorted(flags)),
    )


def health_rows(
    bundles: Iterable[PlayerFeatureBundle],
    *,
    categories: Sequence[PropCategory],
) -> list[dict]:
    """Expand bundle health to the explicitly requested projection universe.

    Category is a reporting scope, not a claim that feature availability differs
    between categories.  The same bundle powers each requested hitter category,
    so per-category fallback rates must agree unless a future category has a
    genuinely different input contract.
    """

    if not categories:
        raise ValueError("categories must be explicit; no projection universe may be inferred")
    if len(set(categories)) != len(categories):
        raise ValueError("categories must be unique")

    rows: list[dict] = []
    for bundle in bundles:
        record = health_for_bundle(bundle).to_dict()
        for category in categories:
            rows.append({**record, "category": category})
    return rows


def summarize_health(rows: Iterable[dict]) -> dict:
    """Return count-preserving, threshold-free summaries for an audit report."""

    materialized = list(rows)
    by_category: dict[str, dict] = {}
    for category in sorted({str(row["category"]) for row in materialized}):
        scoped = [row for row in materialized if str(row["category"]) == category]
        flags = Counter(flag for row in scoped for flag in row["flags"])
        by_category[category] = {
            "projection_rows": len(scoped),
            "unique_player_games": len({(row["mlb_game_pk"], row["player_id"]) for row in scoped}),
            "flag_counts": dict(sorted(flags.items())),
        }

    return {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "projection_rows": len(materialized),
        "unique_player_games": len({(row["mlb_game_pk"], row["player_id"]) for row in materialized}),
        "by_category": by_category,
        "note": (
            "These are observed input/fallback facts, not calibrated uncertainty, "
            "a confidence score, a market edge, or a betting authorization."
        ),
    }


def display_health_flags(flags: Sequence[str]) -> str:
    """Render factual flags without turning them into a confidence score.

    The display is deliberately a lossless, stable join.  It does not rank
    flags, hide a fallback behind a green label, or infer a betting verdict.
    """

    return "; ".join(sorted(str(flag) for flag in flags)) if flags else "not_assessed"
