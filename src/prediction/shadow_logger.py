"""
Legacy shadow-play logger.

During the data-collection window the edge engine runs in READ-ONLY shadow
mode: every day it records the value plays it *would* have recommended, but
nothing is bet. This builds a dated, append-only record so that once outcomes
are known, the would-be plays can be graded — an independent check on the
betting logic, separate from projection calibration.

This CSV is a name-keyed watch trail only. It lacks game/player identity,
quote timestamps, source hashes, closes, and void status, so it must never be
used as CLV/capture evidence. The evaluable replacement is
``src.evaluation.shadow_ledger.ForwardShadowLedger``.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Iterable

from src.models.dataclasses import EdgeResult
from src.utils.logging import get_logger

logger = get_logger(__name__)


SHADOW_FIELDS = [
    "game_date",
    "player_name",
    "category",
    "line",
    "edge_side",
    "model_prob_side",
    "fair_prob_side",
    "edge_pct",
    "vig_pct",
    "kelly_fraction",
    "payout_odds_american",
    "recommendation",
    "confidence",
    "logged_at",
]


class ShadowPlayLogger:
    """Append a legacy, non-evaluable name-keyed watch trail."""

    def __init__(self, path: str | Path = "data/learning/shadow_plays.csv"):
        self.path = Path(path)

    def log(self, game_date: str, value_plays: Iterable[EdgeResult]) -> int:
        """Append value plays for a date. Returns the number of rows written.

        Idempotency: if rows for game_date already exist, this appends anyway —
        callers should log once per prediction run. (Dedup on read is cheap and
        avoids silent data loss from a partial earlier run.)
        """
        plays = list(value_plays)
        if not plays:
            logger.info("No shadow plays to log for %s", game_date)
            return 0

        logger.warning(
            "ShadowPlayLogger writes a legacy name-keyed watch CSV, not a hard-keyed "
            "forward ledger. Do not use %s for CLV/capture evidence.", self.path
        )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.path.exists()
        logged_at = date.today().isoformat()

        with self.path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SHADOW_FIELDS)
            if new_file:
                writer.writeheader()
            for p in plays:
                writer.writerow(
                    {
                        "game_date": game_date,
                        "player_name": p.player_name,
                        "category": p.category,
                        "line": p.line,
                        "edge_side": p.edge_side,
                        "model_prob_side": p.model_prob_side,
                        "fair_prob_side": p.fair_prob_side,
                        "edge_pct": p.edge_pct,
                        "vig_pct": p.vig_pct,
                        "kelly_fraction": p.kelly_fraction,
                        "payout_odds_american": p.payout_odds_american,
                        "recommendation": getattr(p.recommendation, "value", str(p.recommendation)),
                        "confidence": p.confidence,
                        "logged_at": logged_at,
                    }
                )

        logger.info("Logged %d shadow plays for %s to %s", len(plays), game_date, self.path)
        return len(plays)
