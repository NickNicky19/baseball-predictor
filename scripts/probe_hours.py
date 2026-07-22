"""
UTC hour distribution + slate-date mapping check for SmartStake.

RESULT (already run): the date mapping was never broken. All three mappings
agree (~12.1-12.5 games/date) and May 2026 has 376 distinct games, not the ~465
a full MLB month would have. SmartStake covers ~80% of games. The earlier -8h
"fix" was chasing a bug that did not exist -- the premise (that we should see
~15 games/date) was wrong.

Kept for reference / re-running on other months.
"""
import duckdb

HF = "'hf://datasets/SmartStake/mlb-player-props/mon=2026-05/*.parquet'"

duckdb.sql("INSTALL httpfs; LOAD httpfs;")
duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

print("UTC hour of first pitch -> distinct games")
print("=" * 45)
print(duckdb.sql(f"""
    SELECT hour(start_time) AS utc_hour,
           count(DISTINCT game_id) AS games
    FROM {HF}
    GROUP BY 1 ORDER BY 1
""").df().to_string(index=False))

print()
print("total distinct games:")
print(duckdb.sql(f"""
    SELECT count(DISTINCT game_id) AS games FROM {HF}
""").df().to_string(index=False))

print()
print("slate date under three candidate mappings:")
print(duckdb.sql(f"""
    WITH g AS (
        SELECT game_id, any_value(start_time) AS st
        FROM {HF}
        GROUP BY game_id
    )
    SELECT
        'raw UTC' AS mapping,
        count(DISTINCT CAST(st AS DATE)) AS n_dates,
        round(count(*)::DOUBLE
              / count(DISTINCT CAST(st AS DATE)), 1) AS games_per_date
    FROM g
    UNION ALL
    SELECT
        'minus 8h',
        count(DISTINCT CAST(st - INTERVAL 8 HOUR AS DATE)),
        round(count(*)::DOUBLE
              / count(DISTINCT CAST(st - INTERVAL 8 HOUR AS DATE)), 1)
    FROM g
    UNION ALL
    SELECT
        'US/Eastern',
        count(DISTINCT CAST(st AT TIME ZONE 'UTC'
                               AT TIME ZONE 'America/New_York' AS DATE)),
        round(count(*)::DOUBLE
              / count(DISTINCT CAST(st AT TIME ZONE 'UTC'
                                       AT TIME ZONE 'America/New_York' AS DATE)), 1)
    FROM g
""").df().to_string(index=False))
