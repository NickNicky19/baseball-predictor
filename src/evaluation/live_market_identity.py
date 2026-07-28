"""Hard identity contract between a live odds collector and the shadow ledger.

``OddsLine`` is a display-era object: it can carry a player name and a pair of
prices, but it does not prove which MLB game, player, event, or timestamp the
prices belong to.  It must never be used directly to create forward evidence.

This module defines the narrower, fail-closed record required before a quote
can be selected for the ledger.  Fuzzy or nearest-event selection, inferred
start times, and fabricated sides belong outside this boundary and are
rejected here by omission: every identity field is required. A separately
measured start-time delta may reject an otherwise exact, bijective mapping;
it must never choose one.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from src.evaluation.market_economics import MarketEconomicsError, american_odds


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LiveMarketIdentityError(ValueError):
    """Raised when a collector tries to publish an unverifiable market quote."""


def _nonempty(value: Any, label: str) -> str:
    out = str(value).strip()
    if not out:
        raise LiveMarketIdentityError(f"{label} cannot be blank")
    return out


def _utc(value: Any, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise LiveMarketIdentityError(f"{label} must be ISO-8601 with timezone") from exc
    if parsed.tzinfo is None:
        raise LiveMarketIdentityError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso_date(value: Any, label: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise LiveMarketIdentityError(f"{label} must be YYYY-MM-DD") from exc


def _positive_int(value: Any, label: str) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise LiveMarketIdentityError(f"{label} must be an integer") from exc
    if out <= 0:
        raise LiveMarketIdentityError(f"{label} must be positive")
    return out


def _american_odds(value: Any, label: str) -> int:
    try:
        return american_odds(value, label)
    except MarketEconomicsError as exc:
        raise LiveMarketIdentityError(str(exc)) from exc


def _line(value: Any) -> str:
    try:
        out = Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError) as exc:
        raise LiveMarketIdentityError("line must be a finite decimal") from exc
    if not out.is_finite() or out < 0:
        raise LiveMarketIdentityError("line must be finite and non-negative")
    # Decimal('0.50') and Decimal('0.5') must denote the same market key.
    return format(out, "f")


def _sha256(value: Any, label: str) -> str:
    out = str(value).strip().lower()
    if not _SHA256.fullmatch(out):
        raise LiveMarketIdentityError(f"{label} must be a SHA-256 hex digest")
    return out


@dataclass(frozen=True)
class ResolvedLiveMarketQuote:
    """A two-sided live quote after hard source-to-MLB identity resolution.

    ``source_event_id`` and ``source_event_start_time_utc`` preserve the
    vendor identity that was resolved.  ``mlb_game_pk`` and ``player_id`` are
    the independently verified canonical identities.  Neither is inferred
    from a display name at this boundary.
    """

    source_name: str
    source_event_id: str
    source_player_id: str
    source_over_outcome_id: str
    source_under_outcome_id: str
    source_event_start_time_utc: str
    source_quote_at_utc: str
    source_payload_sha256: str
    game_identity_artifact_sha256: str
    player_identity_artifact_sha256: str
    mlb_game_pk: int
    player_id: int
    game_date: str
    official_start_time_utc: str
    sportsbook: str
    category: str
    line: str
    over_odds_american: int
    under_odds_american: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_name", _nonempty(self.source_name, "source_name").lower())
        object.__setattr__(self, "source_event_id", _nonempty(self.source_event_id, "source_event_id"))
        object.__setattr__(self, "source_player_id", _nonempty(self.source_player_id, "source_player_id"))
        object.__setattr__(self, "source_over_outcome_id", _nonempty(self.source_over_outcome_id, "source_over_outcome_id"))
        object.__setattr__(self, "source_under_outcome_id", _nonempty(self.source_under_outcome_id, "source_under_outcome_id"))
        if self.source_over_outcome_id == self.source_under_outcome_id:
            raise LiveMarketIdentityError("Over and Under must have distinct source outcome identities")
        object.__setattr__(self, "source_event_start_time_utc", _utc(self.source_event_start_time_utc, "source_event_start_time_utc"))
        object.__setattr__(self, "source_quote_at_utc", _utc(self.source_quote_at_utc, "source_quote_at_utc"))
        object.__setattr__(self, "source_payload_sha256", _sha256(self.source_payload_sha256, "source_payload_sha256"))
        object.__setattr__(self, "game_identity_artifact_sha256", _sha256(self.game_identity_artifact_sha256, "game_identity_artifact_sha256"))
        object.__setattr__(self, "player_identity_artifact_sha256", _sha256(self.player_identity_artifact_sha256, "player_identity_artifact_sha256"))
        object.__setattr__(self, "mlb_game_pk", _positive_int(self.mlb_game_pk, "mlb_game_pk"))
        object.__setattr__(self, "player_id", _positive_int(self.player_id, "player_id"))
        object.__setattr__(self, "game_date", _iso_date(self.game_date, "game_date"))
        object.__setattr__(self, "official_start_time_utc", _utc(self.official_start_time_utc, "official_start_time_utc"))
        object.__setattr__(self, "sportsbook", _nonempty(self.sportsbook, "sportsbook").lower())
        object.__setattr__(self, "category", _nonempty(self.category, "category"))
        object.__setattr__(self, "line", _line(self.line))
        object.__setattr__(self, "over_odds_american", _american_odds(self.over_odds_american, "over_odds_american"))
        object.__setattr__(self, "under_odds_american", _american_odds(self.under_odds_american, "under_odds_american"))

        if _utc_dt(self.source_quote_at_utc) >= _utc_dt(self.official_start_time_utc):
            raise LiveMarketIdentityError(
                "source_quote_at_utc must precede official_start_time_utc; "
                "post-start prices are not pre-game evidence"
            )

    @property
    def market_key(self) -> tuple[int, int, str, str, str]:
        """The final two-sided market identity after a quote is selected."""

        return (
            self.mlb_game_pk,
            self.player_id,
            self.category,
            self.line,
            self.sportsbook,
        )

    @property
    def source_quote_key(self) -> tuple[str, str, str, str, str, str]:
        """Vendor-level identity; separate from the final canonical market key."""

        return (
            self.source_name,
            self.source_event_id,
            self.source_quote_at_utc,
            self.sportsbook,
            self.category,
            self.line,
        )

    @property
    def quote_sha256(self) -> str:
        """Stable identity for this immutable resolved quote record."""

        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_unique_selected_market_keys(quotes: Iterable[ResolvedLiveMarketQuote]) -> None:
    """Fail on duplicate final markets after a collector selects entry quotes.

    Raw snapshot history legitimately has many quotes for one market.  This
    guard deliberately applies *after* a declared entry-horizon selector has
    chosen the one snapshot intended for the ledger.  Selecting two different
    vendor identities for the same final market is an ambiguity, not a reason
    to call ``drop_duplicates``.
    """

    by_key: dict[tuple[int, int, str, str, str], list[ResolvedLiveMarketQuote]] = {}
    for quote in quotes:
        by_key.setdefault(quote.market_key, []).append(quote)
    duplicates = {key: values for key, values in by_key.items() if len(values) > 1}
    if not duplicates:
        return
    examples: list[str] = []
    for key, values in list(duplicates.items())[:3]:
        sources = ", ".join(
            f"{quote.source_name}:{quote.source_event_id}@{quote.source_quote_at_utc}"
            for quote in values
        )
        examples.append(f"{key}: {sources}")
    raise LiveMarketIdentityError(
        f"duplicate selected MARKET_KEY(s): {len(duplicates)}; " + " | ".join(examples)
    )
