"""Hard game, player, and evaluation identity contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum

from .canonical import require_nonempty_text
from .chronology import parse_aware_utc, parse_date
from .errors import ContractError


def positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"{label} must be a positive integer")
    return value


class Market(str, Enum):
    HITS = "hits"
    HR = "hr_over_0_5"
    TOTAL_BASES = "total_bases"
    HRR = "hrr"
    PITCHER_STRIKEOUTS = "pitcher_strikeouts"


@dataclass(frozen=True)
class GameIdentity:
    official_date: date
    mlb_game_pk: int
    scheduled_start_utc: datetime
    home_team_id: int
    away_team_id: int

    @classmethod
    def create(
        cls,
        *,
        official_date: date | str,
        mlb_game_pk: object,
        scheduled_start_utc: datetime | str,
        home_team_id: object,
        away_team_id: object,
    ) -> "GameIdentity":
        parsed_date = parse_date(official_date, label="official_date")
        start = parse_aware_utc(scheduled_start_utc, label="scheduled_start_utc")
        if start.date() != parsed_date:
            raise ContractError("scheduled start UTC date must match official_date")
        home = positive_int(home_team_id, label="home_team_id")
        away = positive_int(away_team_id, label="away_team_id")
        if home == away:
            raise ContractError("home and away team ids must differ")
        return cls(
            official_date=parsed_date,
            mlb_game_pk=positive_int(mlb_game_pk, label="mlb_game_pk"),
            scheduled_start_utc=start,
            home_team_id=home,
            away_team_id=away,
        )


@dataclass(frozen=True, order=True)
class ObservationKey:
    official_date: str
    mlb_game_pk: int
    player_id: int
    team_side: str
    market: str
    line: str
    arm_id: str

    @classmethod
    def create(
        cls,
        *,
        official_date: date | str,
        mlb_game_pk: object,
        player_id: object,
        team_side: str,
        market: Market | str,
        line: object,
        arm_id: str,
    ) -> "ObservationKey":
        side = require_nonempty_text(team_side, label="team_side")
        if side not in {"home", "away"}:
            raise ContractError("team_side must be home or away")
        try:
            market_value = (
                market.value if isinstance(market, Market) else Market(market).value
            )
        except (ValueError, TypeError) as exc:
            raise ContractError("unsupported market") from exc
        if isinstance(line, bool):
            raise ContractError("line must be numeric")
        try:
            decimal_line = Decimal(str(line))
        except (InvalidOperation, ValueError) as exc:
            raise ContractError("line must be numeric") from exc
        if not decimal_line.is_finite() or decimal_line < 0:
            raise ContractError("line must be finite and non-negative")
        if market_value == Market.HR.value and decimal_line != Decimal("0.5"):
            raise ContractError("HR over 0.5 requires the exact line 0.5")
        doubled = decimal_line * 2
        if doubled != doubled.to_integral_value():
            raise ContractError(
                "count-market line must use an integer or half-integer threshold"
            )
        return cls(
            official_date=parse_date(official_date).isoformat(),
            mlb_game_pk=positive_int(mlb_game_pk, label="mlb_game_pk"),
            player_id=positive_int(player_id, label="player_id"),
            team_side=side,
            market=market_value,
            line=format(decimal_line.normalize(), "f"),
            arm_id=require_nonempty_text(arm_id, label="arm_id"),
        )
