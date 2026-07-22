"""
Does SmartStake carry TEAMS?

The strict market crosswalk needs:
    vendor_game_id -> mlb_game_pk    verified by start_time AND teams

If teams are ABSENT, start_time alone must carry the mapping -- and start_time
DRIFTS (rain delays, TV moves). A tolerant time window would reintroduce exactly
the soft join this migration exists to eliminate.

VERIFY BEFORE CODEX BUILDS AROUND IT.
"""
import duckdb

HF = "'hf://datasets/SmartStake/mlb-player-props/mon=2026-06/*.parquet'"
duckdb.sql("INSTALL httpfs; LOAD httpfs;")
duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

print("=" * 70)
print("1. THE SCHEMA  (DESCRIBE -- a defined statement, not an inference)")
print("=" * 70)
print(duckdb.sql(f"DESCRIBE SELECT * FROM {HF}").df().to_string(index=False))

print()
print("=" * 70)
print("2. ONE ROW, every column")
print("=" * 70)
print(duckdb.sql(f"SELECT * FROM {HF} LIMIT 1").df().T.to_string())

print()
print("=" * 70)
print("3. IS (game_id, start_time) UNIQUE PER GAME?")
print("=" * 70)
print(duckdb.sql(f"""
    SELECT
        count(DISTINCT game_id)                          AS n_game_ids,
        count(DISTINCT (game_id, start_time))            AS n_game_id_starttime,
        count(DISTINCT start_time)                       AS n_start_times
    FROM {HF}
""").df().to_string(index=False))
print()
print("  MEASURED EARLIER: 7,455 groups had TWO start_times for one game_id,")
print("  so game_id is a MATCHUP key, not a game key. If")
print("  n_game_id_starttime > n_game_ids, that is confirmed -- and")
print("  (game_id, start_time) is the vendor's true game identity.")
