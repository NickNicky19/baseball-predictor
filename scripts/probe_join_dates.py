#!/usr/bin/env python3
"""
Does the SmartStake game_date actually line up with our slate date?

SmartStake start_time is UTC. A 10:10pm PT first pitch is 05:10 UTC the NEXT
day, so CAST(start_time AS DATE) puts West Coast night games on the wrong date.
Our game_date is the US slate date. If this is broken, every late West Coast
game silently drops out of the join -- a systematically biased subsample
(Dodgers, Padres, Giants, Mariners, A's), and the Brier comparison would be
computed on a skewed slice while LOOKING complete.

Rule 1: verify, don't defer.
"""
import pandas as pd

df = pd.read_parquet("data/market/closes_2026-05.parquet")

print("start_time -> game_date, by hour of day (UTC)")
print("=" * 60)
print(df.groupby("game_date").size().head(10).to_string())
print()
print(f"distinct game_dates: {df.game_date.nunique()}")
print(f"range: {df.game_date.min()} .. {df.game_date.max()}")
print()

# how many games per date? A real MLB slate is ~15. If some dates show 3-5,
# those are the UTC-spillover dates and the mapping is wrong.
g = df.groupby("game_date")["game_id"].nunique().sort_values()
print("games per date (ascending) -- a real slate is ~15;")
print("dates with 1-5 games are UTC spillover from the night before:")
print(g.head(12).to_string())
print("...")
print(g.tail(5).to_string())
print()
print(f"total distinct games: {df.game_id.nunique()}")
print(f"mean games/date: {g.mean():.1f}   (MLB is ~15/day)")

print()
print("home_runs lines present:")
print(df[df.category == "home_runs"].groupby("line").size().to_string())
