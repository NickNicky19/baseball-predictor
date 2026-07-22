"""mean_entry_age_min came back at -90 minutes at EVERY horizon. An age cannot be
negative, and a CONSTANT offset that does not vary with the horizon smells like a
timezone or an argument-order bug -- not real staleness.

This matters: --max-quote-age 90 compared against that number and dropped a THIRD
of the rows. If the sign is wrong, it kept the stale quotes and dropped the fresh
ones, and we do not know what price the backtest actually used.
"""
import duckdb

duckdb.sql("INSTALL httpfs; LOAD httpfs;")

print("1. date_diff argument order")
print(duckdb.sql("""
    SELECT date_diff('minute', TIMESTAMP '2026-06-01 10:00',
                               TIMESTAMP '2026-06-01 12:00') AS a_then_b
""").df().to_string(index=False))
print("   if a_then_b = +120, then date_diff(part, A, B) = B - A")

print()
print("2. the REAL thing: are ts and start_time comparable?")
HF = "'hf://datasets/SmartStake/mlb-player-props/mon=2026-06/*.parquet'"
duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")
print(duckdb.sql(f"""
    SELECT ts, start_time,
           date_diff('minute', ts, start_time) AS mins_before_first_pitch
    FROM {HF}
    WHERE book='draftkings' AND market='player hits' AND ts < start_time
    LIMIT 5
""").df().to_string(index=False))
print("   mins_before_first_pitch MUST be positive for every row (ts < start_time)")
