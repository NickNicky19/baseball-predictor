"""Schema and accounting gates for corrected shared-PA training rows."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


IDENTITY = ["game_pk", "player_id"]
OUTCOME_COLUMNS = [
    "out_pa", "out_ab", "out_hits", "out_doubles", "out_triples",
    "out_hr", "out_rbi", "out_runs", "out_bb", "out_k",
]


def outcome_counts(frame: pd.DataFrame) -> pd.DataFrame:
    """Return exhaustive mutually exclusive PA counts for each player-game."""
    out = pd.DataFrame(index=frame.index)
    out["strikeout"] = frame["out_k"]
    out["walk"] = frame["out_bb"]
    out["single"] = (
        frame["out_hits"] - frame["out_doubles"]
        - frame["out_triples"] - frame["out_hr"]
    )
    out["double"] = frame["out_doubles"]
    out["triple"] = frame["out_triples"]
    out["home_run"] = frame["out_hr"]
    out["bip_out"] = frame["out_ab"] - frame["out_k"] - frame["out_hits"]
    # Includes HBP, sacrifice events, interference, and any other official PA
    # not represented by AB or BB.  Keeping it explicit makes the classes sum
    # to PA without inventing an unavailable historical HBP label.
    out["other_non_ab"] = frame["out_pa"] - frame["out_ab"] - frame["out_bb"]
    return out


def feature_columns(protocol: dict[str, Any], variant_id: str) -> list[str]:
    variants = {item["id"]: item for item in protocol["sequential_feature_variants"]}
    if variant_id not in variants:
        raise ValueError(f"unknown feature variant: {variant_id}")
    groups = protocol["feature_group_contract"]
    columns: list[str] = []
    for group in variants[variant_id]["groups"]:
        for column in groups[group]:
            if column not in columns:
                columns.append(column)
    forbidden = set(protocol["forbidden_features"])
    quarantined = set(protocol["quarantined_until_availability_proven"])
    overlap = set(columns) & (forbidden | quarantined)
    if overlap:
        raise ValueError(f"leaking or quarantined feature selected: {sorted(overlap)}")
    return columns


def validate_hitter_frame(frame: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, Any]:
    required = {
        "builder_schema", "roller_schema", "season", "game_date", *IDENTITY,
        "lineup_slot", *OUTCOME_COLUMNS,
    }
    for variant in protocol["sequential_feature_variants"]:
        required.update(feature_columns(protocol, variant["id"]))
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"corrected hitter frame missing columns: {missing}")
    if frame.columns.duplicated().any():
        raise ValueError("corrected hitter frame has duplicate column names")
    if set(frame["builder_schema"].dropna().unique()) != {"a3.2"}:
        raise ValueError("retired or mixed builder schema entered shared PA data")
    if set(frame["roller_schema"].dropna().unique()) != {"a4.1"}:
        raise ValueError("missing or mixed Statcast roller schema")
    if set(pd.to_numeric(frame["season"], errors="coerce").dropna().astype(int)) != {2023, 2024, 2025}:
        raise ValueError("shared PA seasons changed")
    dates = pd.to_datetime(frame["game_date"], errors="coerce")
    if dates.isna().any() or dates.min().year != 2023 or dates.max().year != 2025:
        raise ValueError("invalid or out-of-contract game date")
    if frame[IDENTITY].isna().any().any() or frame.duplicated(IDENTITY).any():
        raise ValueError("game/player identity is null or duplicated")
    game_sizes = frame.groupby("game_pk", sort=False).size()
    if not game_sizes.eq(18).all():
        raise ValueError("corrected original-starter population is not 18 hitters per game")
    slots = frame.groupby("game_pk", sort=False)["lineup_slot"].agg(lambda x: sorted(x.astype(int)))
    expected_slots = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9]
    if not slots.map(lambda value: value == expected_slots).all():
        raise ValueError("game lineup-slot population changed")

    numeric = frame[OUTCOME_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or (numeric < 0).any().any():
        raise ValueError("official outcome counts are missing or negative")
    if not np.equal(numeric.to_numpy(), np.floor(numeric.to_numpy())).all():
        raise ValueError("official outcome counts are not integers")
    counts = outcome_counts(numeric)
    if (counts < 0).any().any():
        raise ValueError("official PA outcome decomposition is negative")
    if not counts.sum(axis=1).eq(numeric["out_pa"]).all():
        raise ValueError("official PA outcome classes do not sum to PA")
    if not numeric["out_hr"].le(numeric["out_hits"]).all():
        raise ValueError("official HR exceeds official hits")

    selected = feature_columns(protocol, "core_statcast_pitcher_context")
    missingness = {
        column: float(frame[column].isna().mean()) for column in selected
    }
    return {
        "status": "VALID_CORRECTED_A3_2_A4_1_PA_INPUT",
        "rows": int(len(frame)),
        "games": int(frame["game_pk"].nunique()),
        "rows_by_season": {
            str(int(key)): int(value)
            for key, value in frame.groupby("season").size().items()
        },
        "date_min": dates.min().date().isoformat(),
        "date_max": dates.max().date().isoformat(),
        "identity_key": IDENTITY,
        "duplicate_identity_rows": 0,
        "hitters_per_game": 18,
        "outcome_accounting_valid": True,
        "feature_missing_rate": missingness,
        "confirmation_2025_outcomes_summarized": False,
        "may_2026_read": False,
        "betting_authorized": False,
    }
