#!/usr/bin/env python3
"""Offline mutation checks for the future hard-keyed quote boundary."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.live_market_identity import (  # noqa: E402
    LiveMarketIdentityError,
    ResolvedLiveMarketQuote,
    require_unique_selected_market_keys,
)


def base(**overrides) -> ResolvedLiveMarketQuote:
    row = dict(
        source_name="odds_api",
        source_event_id="vendor-event-7",
        source_player_id="vendor-player-123",
        source_over_outcome_id="over-123",
        source_under_outcome_id="under-123",
        source_event_start_time_utc="2026-07-15T23:10:00Z",
        source_quote_at_utc="2026-07-15T19:05:00Z",
        source_payload_sha256="a" * 64,
        game_identity_artifact_sha256="b" * 64,
        player_identity_artifact_sha256="c" * 64,
        mlb_game_pk=900001,
        player_id=123,
        game_date="2026-07-15",
        official_start_time_utc="2026-07-15T23:10:00Z",
        sportsbook="DraftKings",
        category="hits",
        line="0.5",
        over_odds_american=-115,
        under_odds_american=-105,
    )
    row.update(overrides)
    return ResolvedLiveMarketQuote(**row)


def main() -> int:
    quote = base()
    assert quote.market_key == (900001, 123, "hits", "0.5", "draftkings")
    require_unique_selected_market_keys([quote])
    print("[OK] hard game/player/book/line identity is accepted")

    # Mutation: a post-start quote must never become pre-game evidence.
    try:
        base(source_quote_at_utc="2026-07-15T23:10:00Z")
    except LiveMarketIdentityError as exc:
        assert "post-start" in str(exc)
    else:
        raise AssertionError("post-start mutation was accepted")
    print("[OK] mutation: post-start quote fails")

    # Same player/category/line in a second game is a different market.  This
    # is what a name-keyed matcher cannot represent safely.
    doubleheader = base(mlb_game_pk=900002, source_event_id="vendor-event-8")
    require_unique_selected_market_keys([quote, doubleheader])
    print("[OK] same player on a second game remains a distinct market")

    # Mutation: two selected vendor paths for the same final market must fail;
    # neither may be picked by recency, name, or row order.
    conflicting = base(source_event_id="vendor-event-duplicate", source_quote_at_utc="2026-07-15T18:55:00Z")
    try:
        require_unique_selected_market_keys([quote, conflicting])
    except LiveMarketIdentityError as exc:
        assert "duplicate selected MARKET_KEY" in str(exc)
        assert "vendor-event-7" in str(exc) and "vendor-event-duplicate" in str(exc)
    else:
        raise AssertionError("duplicate market mutation was accepted")
    print("[OK] mutation: duplicate selected market fails with both source identities")

    # A name-only/mapping-less quote has no route through this constructor:
    # removing player_id is a direct proof that display identity cannot enter.
    try:
        base(player_id=None)
    except LiveMarketIdentityError as exc:
        assert "player_id" in str(exc)
    else:
        raise AssertionError("missing player id mutation was accepted")
    print("[OK] mutation: name-only / missing player identity fails")

    try:
        base(game_identity_artifact_sha256="missing")
    except LiveMarketIdentityError as exc:
        assert "game_identity_artifact_sha256" in str(exc)
    else:
        raise AssertionError("unbound game identity mutation was accepted")
    print("[OK] mutation: game resolution requires a hash-bound evidence artifact")

    try:
        base(source_under_outcome_id="over-123")
    except LiveMarketIdentityError as exc:
        assert "distinct source outcome" in str(exc)
    else:
        raise AssertionError("same source outcome used as both sides")
    print("[OK] mutation: one source outcome cannot fabricate a two-sided quote")
    print("7/7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
