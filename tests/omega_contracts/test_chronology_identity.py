from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.omega_contracts.chronology import (
    assert_may_safe_path,
    parse_aware_utc,
    parse_date,
    require_before,
    require_ordered_dates,
)
from src.omega_contracts.errors import ContractError
from src.omega_contracts.identity import (
    GameIdentity,
    Market,
    ObservationKey,
    positive_int,
)


@pytest.mark.parametrize(
    "value",
    ["2026-05-12", date(2026, 5, 12)],
)
def test_may_date_is_sealed(value):
    with pytest.raises(ContractError, match="sealed May"):
        parse_date(value)


@pytest.mark.parametrize(
    "value", ["2026/05/12", "2026_05_12", "20260512", "2026-99-99"]
)
def test_noncanonical_or_invalid_date_is_rejected(value):
    with pytest.raises(ContractError):
        parse_date(value)


@pytest.mark.parametrize(
    "name",
    [
        "2026-05-12.json",
        "2026_05_12.json",
        "2026/05/12.json",
        "20260512.json",
        "2026%2F05%2F12.json",
    ],
)
def test_may_path_representation_is_rejected_without_opening(tmp_path: Path, name: str):
    with pytest.raises(ContractError, match="sealed May"):
        assert_may_safe_path(tmp_path / name, allowed_root=tmp_path)


def test_path_escape_is_rejected(tmp_path: Path):
    with pytest.raises(ContractError, match="escapes"):
        assert_may_safe_path(tmp_path / ".." / "outside.json", allowed_root=tmp_path)


def test_naive_time_and_horizon_equality_are_rejected():
    with pytest.raises(ContractError, match="timezone-aware"):
        parse_aware_utc(datetime(2026, 7, 26, 12, 0))
    horizon = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ContractError, match="strictly before"):
        require_before(horizon, horizon, label="receipt")


def test_time_is_normalized_and_windows_are_strict():
    parsed = parse_aware_utc("2026-07-26T07:00:00-05:00")
    assert parsed == datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    require_ordered_dates(fit_end="2023-12-31", selection_start="2024-01-01")
    with pytest.raises(ContractError):
        require_ordered_dates(fit_end="2024-01-01", selection_start="2024-01-01")


@pytest.mark.parametrize(
    "value",
    [
        "20260726T070000-05:00",
        "2026-07-26 07:00:00-05:00",
        "2026-07-26t07:00:00-05:00",
        "2026-07-26T07:00:00",
    ],
)
def test_noncanonical_or_naive_timestamp_text_is_rejected(value: str):
    with pytest.raises(ContractError, match="RFC 3339"):
        parse_aware_utc(value)


@pytest.mark.parametrize(
    "name",
    [
        "2026-05",
        "2026_05",
        "2026%2D05",
        "202605",
        "May-2026",
        "2026-May",
        "May 2026",
        "2026 May",
    ],
)
def test_month_level_may_path_tokens_are_rejected_without_opening(
    tmp_path: Path, name: str
):
    with pytest.raises(ContractError, match="sealed May"):
        assert_may_safe_path(tmp_path / name, allowed_root=tmp_path)


def test_bool_identity_is_rejected_and_hard_key_is_canonical():
    with pytest.raises(ContractError):
        positive_int(True, label="game")
    key = ObservationKey.create(
        official_date="2026-07-26",
        mlb_game_pk=1,
        player_id=2,
        team_side="away",
        market=Market.HR,
        line="0.50",
        arm_id="candidate",
    )
    assert key.line == "0.5"
    assert key.market == "hr_over_0_5"


@pytest.mark.parametrize("line", [0, 1, 1.5, 999])
def test_hr_market_identity_requires_exact_over_half_line(line):
    with pytest.raises(ContractError, match="exact line 0.5"):
        ObservationKey.create(
            official_date="2026-07-26",
            mlb_game_pk=1,
            player_id=2,
            team_side="away",
            market=Market.HR,
            line=line,
            arm_id="candidate",
        )


def test_count_market_lines_are_integer_or_half_integer():
    with pytest.raises(ContractError, match="half-integer"):
        ObservationKey.create(
            official_date="2026-07-26",
            mlb_game_pk=1,
            player_id=2,
            team_side="away",
            market=Market.HITS,
            line="0.25",
            arm_id="candidate",
        )


def test_game_identity_rejects_same_team_and_requires_schedule_day():
    with pytest.raises(ContractError, match="must differ"):
        GameIdentity.create(
            official_date="2026-07-26",
            mlb_game_pk=1,
            scheduled_start_utc="2026-07-26T20:00:00Z",
            home_team_id=10,
            away_team_id=10,
        )
    # The current candidate intentionally rejects a UTC date different from the
    # official date until an official schedule-receipt resolver owns this join.
    with pytest.raises(ContractError, match="must match"):
        GameIdentity.create(
            official_date="2026-07-26",
            mlb_game_pk=1,
            scheduled_start_utc="2026-07-27T01:00:00Z",
            home_team_id=10,
            away_team_id=11,
        )
