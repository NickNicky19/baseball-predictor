#!/usr/bin/env python3
"""
Recon on SmartStake/mlb-player-props BEFORE writing any CLV code.

Answers:
  1. What markets exist? (does hrr exist, or must we synthesize it?)
  2. What books? (is Pinnacle there -- the benchmark we must beat)
  3. Which lines are actually posted?
  4. Date range + graded coverage.

Scoped to ONE MONTH: enough to answer the schema questions at a fraction of
the HTTP requests. HF rate-limits (429) on anonymous globs across partitions.

Rule 1: verify, don't defer. Rule 6: cheap test before expensive build.
"""
import duckdb

HF = "'hf://datasets/SmartStake/mlb-player-props/mon=2026-05/*.parquet'"

duckdb.sql("INSTALL httpfs; LOAD httpfs;")
duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

print("=" * 70)
print("1. MARKETS  (does hrr exist?)")
print("=" * 70)
print(duckdb.sql(f"""
    SELECT market,
           count(*)                AS n_rows,
           count(DISTINCT game_id) AS n_games,
           count(DISTINCT player)  AS n_players,
           sum(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) AS n_graded
    FROM {HF}
    GROUP BY 1 ORDER BY n_rows DESC
""").df().to_string(index=False))

print()
print("=" * 70)
print("2. BOOKS  (is Pinnacle present? how deep?)")
print("=" * 70)
print(duckdb.sql(f"""
    SELECT book, count(*) AS n_rows, count(DISTINCT game_id) AS n_games
    FROM {HF}
    GROUP BY 1 ORDER BY n_rows DESC LIMIT 25
""").df().to_string(index=False))

print()
print("=" * 70)
print("3. LINES PER MARKET")
print("=" * 70)
print(duckdb.sql(f"""
    SELECT market, line, count(*) AS n
    FROM {HF}
    GROUP BY 1, 2
    HAVING n > 20000
    ORDER BY market, line
""").df().to_string(index=False))

print()
print("=" * 70)
print("4. DATE RANGE + GRADED COVERAGE")
print("=" * 70)
print(duckdb.sql(f"""
    SELECT min(start_time) AS first_game,
           max(start_time) AS last_game,
           count(DISTINCT game_id) AS games,
           sum(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) AS graded_rows,
           sum(CASE WHEN result IS NULL     THEN 1 ELSE 0 END) AS ungraded_rows
    FROM {HF}
""").df().to_string(index=False))
