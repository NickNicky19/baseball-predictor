"""Regression and mutation guards for exact American-price consumption."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.odds.base import OddsAPISettings, OddsLoadError, parse_odds_row
from src.data.odds.odds_api_provider import OddsAPIProvider
from src.evaluation.live_market_identity import LiveMarketIdentityError, _american_odds as live_price
from src.evaluation.market_economics import MarketEconomicsError, american_odds
from src.evaluation.shadow_ledger import ShadowLedgerError, _american_odds as ledger_price


@pytest.mark.parametrize("value", [True, False, 105.9, -110.0, Decimal("-110"), None, 0, 99, -99, "-110.5", "abc"])
def test_noncanonical_or_invalid_american_prices_fail_closed_everywhere(value: object) -> None:
    with pytest.raises(MarketEconomicsError):
        american_odds(value)
    with pytest.raises(LiveMarketIdentityError):
        live_price(value, "price")
    with pytest.raises(ShadowLedgerError):
        ledger_price(value, "price")


@pytest.mark.parametrize("value, expected", [(-110, -110), (125, 125), ("-110", -110), ("+125", 125)])
def test_canonical_american_prices_preserve_exact_value(value: object, expected: int) -> None:
    assert american_odds(value) == expected
    assert live_price(value, "price") == expected
    assert ledger_price(value, "price") == expected


def test_old_truncating_mutation_is_detected() -> None:
    def mutated_parser(value: object) -> int:
        return int(value)

    assert mutated_parser(105.9) == 105
    with pytest.raises(MarketEconomicsError):
        american_odds(105.9)


def test_raw_odds_row_cannot_reintroduce_float_truncation() -> None:
    row = {
        "player_name": "Test Player",
        "category": "hits",
        "line": 1.5,
        "over_odds": 105.9,
        "under_odds": -120,
    }
    with pytest.raises(OddsLoadError, match="Invalid odds values"):
        parse_odds_row(row, "synthetic.json")


@pytest.mark.parametrize("field", ["over_odds", "under_odds"])
@pytest.mark.parametrize("value", [True, False, 0.0, -0.0, Decimal("0")])
def test_raw_odds_row_rejects_values_equal_to_the_exact_zero_sentinel(
    field: str, value: object
) -> None:
    row = {
        "player_name": "Test Player",
        "category": "hits",
        "line": 1.5,
        "over_odds": -115,
        "under_odds": -120,
    }
    row[field] = value
    with pytest.raises(OddsLoadError, match="Invalid odds values"):
        parse_odds_row(row, "synthetic.json")


def test_raw_odds_row_preserves_exact_integer_zero_as_an_absent_side() -> None:
    row = {
        "player_name": "Test Player",
        "category": "hits",
        "line": 1.5,
        "over_odds": 0,
        "under_odds": -120,
    }
    parsed = parse_odds_row(row, "synthetic.json")
    assert parsed.over_odds_american == 0
    assert parsed.under_odds_american == -120


def _odds_api_payload(over_price: object, under_price: object = -120) -> dict:
    return {
        "bookmakers": [{
            "key": "synthetic_book",
            "title": "Synthetic Book",
            "markets": [{
                "key": "batter_hits",
                "outcomes": [
                    {
                        "name": "Over",
                        "description": "Test Player",
                        "point": 1.5,
                        "price": over_price,
                    },
                    {
                        "name": "Under",
                        "description": "Test Player",
                        "point": 1.5,
                        "price": under_price,
                    },
                ],
            }],
        }]
    }


@pytest.mark.parametrize("value", [105.9, -110.0, True, False, 0, 0.0])
def test_live_odds_api_rejects_noncanonical_prices_without_truncation(value: object) -> None:
    provider = OddsAPIProvider(
        settings=OddsAPISettings(
            enabled=True,
            api_key="synthetic",
            market_category_map={"batter_hits": "hits"},
        )
    )
    with pytest.raises(OddsLoadError, match="Invalid American price"):
        provider._parse_event_odds(_odds_api_payload(value))


def test_live_odds_api_preserves_exact_integer_prices() -> None:
    provider = OddsAPIProvider(
        settings=OddsAPISettings(
            enabled=True,
            api_key="synthetic",
            market_category_map={"batter_hits": "hits"},
        )
    )
    lines = provider._parse_event_odds(_odds_api_payload(125, -120))
    assert len(lines) == 1
    assert lines[0].over_odds_american == 125
    assert lines[0].under_odds_american == -120
