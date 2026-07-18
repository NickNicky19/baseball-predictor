"""Exact, payout-aware market arithmetic.

This module intentionally contains no model, calibration, or policy choice.
It answers only the mechanical question that must be true before any row can
represent a wager: given a model win probability and the *posted* price, what
is the expected profit for one unit staked?

The de-vigged market probability remains useful for measuring whether the
market moves toward the model (capture).  It is not the same quantity as the
break-even probability of a bet.  A model can be above the de-vigged fair
probability yet still below the posted-price break-even probability because
the book's margin has to be paid.  Treating those as interchangeable would
let a negative-EV wager become a ``value play``.
"""

from __future__ import annotations

import math
from typing import Any


class MarketEconomicsError(ValueError):
    """Raised when a probability or posted American price is invalid."""


def american_odds(value: Any, label: str = "american_odds") -> int:
    """Validate and return a non-zero integral American price."""
    # ``bool`` is an ``int`` subclass, but a boolean is never a price.
    if isinstance(value, bool):
        raise MarketEconomicsError(f"{label} must be an integer American price")
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise MarketEconomicsError(f"{label} must be an integer American price") from exc
    if out == 0:
        raise MarketEconomicsError(f"{label} cannot be 0")
    return out


def probability(value: Any, label: str = "probability") -> float:
    """Validate and return a finite probability in the closed unit interval."""
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketEconomicsError(f"{label} must be numeric") from exc
    if not math.isfinite(out) or not 0.0 <= out <= 1.0:
        raise MarketEconomicsError(f"{label} must be finite and in [0, 1]")
    return out


def implied_probability(odds_american: Any) -> float:
    """Return the posted-price break-even probability before de-vigging."""
    odds = american_odds(odds_american)
    return 100.0 / (odds + 100.0) if odds > 0 else -odds / (-odds + 100.0)


def net_payout_multiple(odds_american: Any) -> float:
    """Profit returned per unit staked when the wager wins (``b`` in Kelly)."""
    odds = american_odds(odds_american)
    return odds / 100.0 if odds > 0 else 100.0 / abs(odds)


def decimal_odds(value: Any, label: str = "decimal_odds") -> float:
    """Validate a decimal quote and return it unchanged.

    SmartStake's historical parquet uses decimal odds, whereas the live edge
    engine consumes American odds.  They are both posted-price representations,
    but silently treating one as the other would corrupt the wager economics.
    Decimal odds must be finite and strictly greater than one: a one-unit stake
    must return stake plus a non-negative profit when it wins.
    """
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketEconomicsError(f"{label} must be numeric decimal odds") from exc
    if not math.isfinite(out) or out <= 1.0:
        raise MarketEconomicsError(
            f"{label} must be finite decimal odds strictly greater than 1"
        )
    return out


def net_payout_multiple_decimal(odds_decimal: Any) -> float:
    """Profit returned per unit staked at a decimal quote."""
    return decimal_odds(odds_decimal) - 1.0


def expected_profit_per_unit_decimal(
    win_probability: Any, odds_decimal: Any
) -> float:
    """Expected profit for one unit at an exact posted decimal quote.

    This is the historical-artifact counterpart of
    :func:`expected_profit_per_unit`.  It is a payout identity, not a fitted
    bet-selection threshold and cannot by itself authorize a wager.
    """
    p = probability(win_probability, "win_probability")
    b = net_payout_multiple_decimal(odds_decimal)
    return p * b - (1.0 - p)


def expected_profit_per_unit(win_probability: Any, odds_american: Any) -> float:
    """Expected profit for one unit staked at the exact posted price.

    ``p * b - (1 - p)`` is a structural payout identity, not a fitted
    threshold.  A strictly positive result is the minimum mathematical
    condition for a wager candidate; it does *not* authorize betting or
    determine stake size.
    """
    p = probability(win_probability, "win_probability")
    b = net_payout_multiple(odds_american)
    return p * b - (1.0 - p)


def fair_over_probability(over_odds_american: Any, under_odds_american: Any) -> float:
    """Proportionally de-vig a two-sided market into P(Over)."""
    over = implied_probability(over_odds_american)
    under = implied_probability(under_odds_american)
    total = over + under
    if not math.isfinite(total) or total <= 0.0:
        raise MarketEconomicsError("two-sided odds cannot be de-vigged")
    return over / total
