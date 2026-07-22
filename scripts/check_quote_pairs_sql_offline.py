#!/usr/bin/env python3
"""Mutation checks for the outcome-blind quote-pair boundary."""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.market_eligibility import quote_pairs_sql  # noqa: E402


def fetch(con):
    return con.execute(quote_pairs_sql(
        "SELECT * FROM fx", "draftkings", 4, "'player hits'"
    )).df().sort_values("player").reset_index(drop=True)


def main() -> int:
    con = duckdb.connect()
    con.execute("""CREATE TABLE fx (
        game_id VARCHAR, start_time TIMESTAMP, player VARCHAR, market VARCHAR,
        line DOUBLE, side VARCHAR, book VARCHAR, ts TIMESTAMP, odds DOUBLE,
        result DOUBLE)""")

    # alpha is fresh; beta is intentionally 10 hours old.  Both are legitimate
    # quote pairs at this layer: the audit must observe beta rather than silently
    # applying a freshness policy of its own.
    rows = []
    for player, ts, result in (
        ("alpha", "2026-06-01 18:00:00", 1.0),
        ("beta", "2026-06-01 08:00:00", None),
    ):
        for side, odds in (("over", 2.0), ("under", 1.8)):
            rows.append(("g1", "2026-06-01 23:05:00", player, "player hits",
                         0.5, side, "draftkings", ts, odds, result))
    con.executemany("INSERT INTO fx VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

    original = fetch(con)
    assert set(original.player) == {"alpha", "beta"}
    assert original.entry_age_min.min() >= 0
    assert int(original.loc[original.player.eq("beta"), "entry_age_min"].iloc[0]) > 90
    assert "result" not in original.columns
    print("[OK] fresh and stale quote pairs both reach the outcome-blind audit")
    print("[OK] vendor numeric result is not an audit output")

    # M1: numeric result values are not allowed to re-price or remove a pair.
    con.execute("UPDATE fx SET result = result + 99 WHERE result IS NOT NULL")
    mutated_values = fetch(con)
    assert list(mutated_values.player) == list(original.player)
    assert np.allclose(mutated_values.entry_p_over, original.entry_p_over)
    assert np.array_equal(mutated_values.entry_age_min, original.entry_age_min)
    print("[OK] MUTATION vendor result values cannot change prices or ages")

    # M2: quote age is not a hidden cutoff in the quote-pair query.  If a later
    # edit filters beta here, this assertion fails even though alpha still exists.
    assert "beta" in set(mutated_values.player)
    print("[OK] MUTATION stale pair is retained for measurement (no hidden cutoff)")
    print("4/4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
