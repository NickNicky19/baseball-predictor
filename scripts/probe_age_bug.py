"""
entry_age_min came back -89.99 at EVERY horizon -- a CONSTANT, not noise.

date_diff and the timestamps both check out (probe_datediff.py):
    date_diff(part, A, B) = B - A            confirmed (+120 for a 2h gap)
    ts < start_time on every row             confirmed, positive minutes

So the formula looks right and something in the GROUPING is wrong. A constant
offset across all five horizons is a fingerprint, not randomness.

WHY THIS BLOCKS EVERYTHING
  --max-quote-age 90 compared against that number and dropped a THIRD of the
  rows (1,514 of 4,507 at the close). If the sign is inverted, the filter kept
  the STALE quotes and dropped the FRESH ones -- and we do not know what price
  the backtest actually used.

  Worse: if max(ts) can exceed start_time, the "entry price" would contain
  POST-FIRST-PITCH quotes. That is outcome leakage and it would invalidate the
  entire CLV result.

Do not read another CLV number until this is settled.
"""
import duckdb

duckdb.sql("INSTALL httpfs; LOAD httpfs;")
duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

HF = "'hf://datasets/SmartStake/mlb-player-props/mon=2026-06/*.parquet'"

print("=" * 74)
print("1. Reproduce the entry CTE at hours=0 and inspect the RAW pieces")
print("=" * 74)
print(duckdb.sql(f"""
    WITH src AS (
        SELECT * FROM {HF}
        WHERE book='draftkings' AND market='player hits'
          AND result IS NOT NULL AND ts < start_time
    ),
    entry AS (
        SELECT game_id, player, market, line, side,
               arg_max(odds, ts)          AS odds,
               max(ts)                    AS q_ts,
               any_value(start_time)      AS start_time,
               count(DISTINCT start_time) AS n_start_times
        FROM src
        WHERE ts <= start_time - INTERVAL 0 HOUR
        GROUP BY 1,2,3,4,5
    )
    SELECT q_ts, start_time, n_start_times,
           date_diff('minute', q_ts, start_time) AS age_should_be_positive
    FROM entry
    ORDER BY age_should_be_positive
    LIMIT 10
""").df().to_string(index=False))
print()
print("  age_should_be_positive MUST be > 0 on every row.")
print("  If it is NEGATIVE, max(ts) exceeded start_time -- the 'entry price'")
print("  would contain POST-FIRST-PITCH quotes. That is outcome leakage.")

print()
print("=" * 74)
print("2. Does any group carry MORE THAN ONE start_time?")
print("   any_value() would then pick an arbitrary one (e.g. a doubleheader,")
print("   where the same player has two games on the same date).")
print("=" * 74)
print(duckdb.sql(f"""
    WITH src AS (
        SELECT * FROM {HF}
        WHERE book='draftkings' AND market='player hits'
          AND result IS NOT NULL AND ts < start_time
    )
    SELECT count(*) AS groups_with_multiple_start_times
    FROM (
        SELECT game_id, player, market, line, side,
               count(DISTINCT start_time) AS n
        FROM src
        GROUP BY 1, 2, 3, 4, 5
    )
    WHERE n > 1
""").df().to_string(index=False))

print()
print("=" * 74)
print("3. The age formula EXACTLY as run_clv_backtest.py computes it, at hours=4")
print("=" * 74)
print(duckdb.sql(f"""
    WITH src AS (
        SELECT * FROM {HF}
        WHERE book='draftkings' AND market='player hits'
          AND result IS NOT NULL AND ts < start_time
    ),
    entry AS (
        SELECT game_id, player, market, line, side,
               max(ts)               AS q_ts,
               any_value(start_time) AS start_time
        FROM src
        WHERE ts <= start_time - INTERVAL 4 HOUR
        GROUP BY 1, 2, 3, 4, 5
    )
    SELECT
        round(avg(date_diff('minute', q_ts,
                            start_time - INTERVAL 4 HOUR)), 2) AS mean_age_as_coded,
        round(min(date_diff('minute', q_ts,
                            start_time - INTERVAL 4 HOUR)), 2) AS min_age,
        round(max(date_diff('minute', q_ts,
                            start_time - INTERVAL 4 HOUR)), 2) AS max_age,
        count(*) AS n
    FROM entry
""").df().to_string(index=False))
print()
print("  If mean_age_as_coded is NEGATIVE here, the WHERE clause and the age")
print("  formula disagree, and I need to see which one is lying before any CLV")
print("  number can be trusted.")
