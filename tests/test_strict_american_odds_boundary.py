"""Regression and mutation guards for exact American-price consumption."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.odds.base import OddsLoadError, parse_odds_row
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
