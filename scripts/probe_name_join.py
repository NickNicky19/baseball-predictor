#!/usr/bin/env python3
"""
Can we join SmartStake's `player` string to an MLB player_id?

CORRECTION (v2): v1 joined against data/learning/prediction_outcomes.csv, which
is the LIVE FORWARD LOG -- six days (2026-07-05..07-10), 517 players. That is
not a player universe; it's whoever happened to be in six slates. The 65.3%
"match rate" was measuring window overlap, not name matching.

The right bridge is the MLB ROSTER: SmartStake name -> roster (name+id) -> our
player_id. Both sides are drawn from the same population, so the expected match
rate is ~100% and anything less is a real defect.

Rule 1: verify, don't defer. (This probe is itself the correction of a v1 that
deferred to the wrong table.)
"""
import unicodedata
import duckdb
import pandas as pd

from src.data.mlb_api import MLBStatsAPI

HF = "'hf://datasets/SmartStake/mlb-player-props/mon=2026-05/*.parquet'"

duckdb.sql("INSTALL httpfs; LOAD httpfs;")
duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")


def norm(s: pd.Series) -> pd.Series:
    """Normalize a name for joining. Strips accents, punctuation, suffixes."""
    def strip_accents(x):
        return "".join(c for c in unicodedata.normalize("NFKD", str(x))
                       if not unicodedata.combining(c))
    return (s.map(strip_accents).str.lower().str.strip()
             .str.replace(r"[.'`\-]", "", regex=True)
             .str.replace(r"\s+", " ", regex=True)
             .str.replace(r"\s+(jr|sr|ii|iii|iv|v)$", "", regex=True)
             .str.strip())


# 1. market side: distinct HITTERS in May 2026
mkt = duckdb.sql(f"""
    SELECT DISTINCT player
    FROM {HF}
    WHERE market IN ('player hits', 'player home runs', 'player bases')
""").df()
print(f"SmartStake distinct hitters (May 2026): {len(mkt)}")

# 2. roster side: every hitter the MLB API knows about for a May 2026 date
api = MLBStatsAPI(season=2026)
hitters = api.get_hitters_for_date("2026-05-15")   # any mid-May date
roster = pd.DataFrame([
    {"player_id": h.player.mlb_id, "player_name": h.player.name}
    for h in hitters
]).drop_duplicates()
print(f"MLB roster hitters for 2026-05-15: {len(roster)}")

mkt["k"] = norm(mkt["player"])
roster["k"] = norm(roster["player_name"])

m = mkt.merge(roster, on="k", how="left", indicator=True)
hit = (m["_merge"] == "both").sum()
print(f"\n{'='*60}")
print(f"  matched: {hit}/{len(mkt)} ({100*hit/len(mkt):.1f}%)")
print(f"{'='*60}")

miss = sorted(m[m["_merge"] == "left_only"]["player"].tolist())
print(f"\n  UNMATCHED ({len(miss)}):")
for n_ in miss[:50]:
    print(f"    {n_}")
