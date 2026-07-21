"""Scope contract for permissible historical baseball/statistical backfills."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd


class BackfillContractError(ValueError):
    """A requested read/write would cross the research-only backfill boundary."""


SEALED_START = date(2026, 5, 1)
SEALED_END = date(2026, 5, 31)

FORBIDDEN_PROSPECTIVE_CLASSES = frozenset(
    {
        "t4_starter_receipt",
        "live_lineup",
        "historical_price",
        "executable_availability",
        "decision_time_prediction",
        "settlement",
        "execution_observation",
    }
)

# A 2026 repair extract may carry only source identity, chronology, and
# pre-target physical/statistical inputs. Outcome/result fields are never
# projected into the repair workflow.
ALLOWED_2026_INPUT_FIELDS = frozenset(
    {
        "game_date",
        "batter",
        "pitcher",
        "pitch_type",
        "description",
        "type",
        "launch_speed",
        "launch_angle",
        "launch_speed_angle",
        "estimated_woba_using_speedangle",
        "estimated_ba_using_speedangle",
        "estimated_slg_using_speedangle",
        "zone",
        "plate_x",
        "plate_z",
        "release_speed",
        "pfx_x",
        "pfx_z",
        "stand",
        "p_throws",
        "at_bat_number",
        "pitch_number",
        "sv_id",
    }
)


def assert_not_may_2026(value: date | str, *, context: str) -> date:
    parsed = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    if SEALED_START <= parsed <= SEALED_END:
        raise BackfillContractError(f"{context}: May 2026 is sealed")
    return parsed


def assert_permissible_artifact_class(artifact_class: str) -> None:
    if artifact_class in FORBIDDEN_PROSPECTIVE_CLASSES:
        raise BackfillContractError(
            f"prospective evidence cannot be backfilled: {artifact_class}"
        )


def assert_path_outside_sealed_month(path: str | Path) -> None:
    normalized = str(Path(path)).replace("\\", "/").lower()
    sealed_tokens = ("2026-05", "2026/05", "may_2026", "2026_may")
    if any(token in normalized for token in sealed_tokens):
        raise BackfillContractError(f"path addresses sealed May 2026 content: {path}")


def project_2026_point_in_time_inputs(
    frame: pd.DataFrame,
    *,
    source_date_column: str = "game_date",
    required_fields: Iterable[str] = ("game_date", "batter"),
) -> pd.DataFrame:
    """Return an outcome-blind 2026 audit projection, rejecting sealed rows."""

    missing = sorted(set(required_fields).difference(frame.columns))
    if missing:
        raise BackfillContractError(f"required input fields missing: {missing}")
    source_dates = pd.to_datetime(frame[source_date_column], errors="raise").dt.date
    if any(SEALED_START <= value <= SEALED_END for value in source_dates):
        raise BackfillContractError("source frame contains sealed May 2026 rows")
    projected = [column for column in frame.columns if column in ALLOWED_2026_INPUT_FIELDS]
    if not projected:
        raise BackfillContractError("source frame contains no permissible input fields")
    return frame.loc[:, projected].copy()
