from __future__ import annotations

import pandas as pd
import pytest

from src.data.statcast_source_contract import (
    StatcastSourceSchemaError,
    statcast_frame_content_sha256,
    validate_statcast_source_lineage,
)
from src.models.dataclasses import StatcastProfile


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"batter": 7, "game_date": "2024-04-01", "zone": 5.0},
            {"batter": 7, "game_date": "2024-04-01", "zone": None},
        ]
    )


def test_content_hash_is_row_and_column_order_independent() -> None:
    frame = _frame()
    reordered = frame.loc[::-1, ["zone", "game_date", "batter"]]
    assert statcast_frame_content_sha256(frame) == statcast_frame_content_sha256(
        reordered
    )


def test_content_hash_changes_with_value_or_duplicate_population() -> None:
    frame = _frame()
    changed = frame.copy()
    changed.loc[0, "zone"] = 6.0
    duplicated = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)

    original = statcast_frame_content_sha256(frame)
    assert statcast_frame_content_sha256(changed) != original
    assert statcast_frame_content_sha256(duplicated) != original


@pytest.mark.parametrize("bad_value", [float("inf"), [1, 2]])
def test_content_hash_rejects_nonfinite_or_unsupported_cells(bad_value) -> None:
    frame = _frame().astype({"zone": "object"})
    frame.at[0, "zone"] = bad_value
    with pytest.raises(StatcastSourceSchemaError, match="non-finite|unsupported"):
        statcast_frame_content_sha256(frame)


@pytest.mark.parametrize("digest", [None, "A" * 64, "a" * 63, "not-a-hash"])
def test_source_bound_profile_requires_canonical_sha256(digest) -> None:
    profile = StatcastProfile(
        player_id=7,
        player_name="Hash Bound",
        sample_pa=1,
        source_kind="pybaseball_statcast",
        source_status="observed_complete",
        source_row_count=1,
        source_content_sha256=digest,
    )
    with pytest.raises(StatcastSourceSchemaError, match="source_content_sha256"):
        validate_statcast_source_lineage(profile, context="hash mutation")


def test_untracked_legacy_profile_cannot_claim_source_hash() -> None:
    profile = StatcastProfile(
        player_id=7,
        player_name="Legacy",
        source_content_sha256="a" * 64,
    )
    with pytest.raises(StatcastSourceSchemaError, match="cannot claim"):
        validate_statcast_source_lineage(profile, context="legacy mutation")
