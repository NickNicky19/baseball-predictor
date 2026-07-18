import duckdb

SRC = "data/market/smartstake/mon=2026-06/*.parquet"

print("=" * 84)
print("IS `game_id` A STABLE GAME KEY?  The vendor says yes; we measured otherwise.")
print("=" * 84)
print("  README: `game_id` = 'Stable per-game identity. Group and grade on this.'")
print("  MEASURED (June): 243 game_ids vs ~218 real games; (game_id, start_time) -> 493")
print()
print("  BOTH CANNOT BE TRUE. Why does a game_id carry more than one start_time?")
print("    DRIFT        spans a minute or two  => game_id IS stable, and v2's exact")
print("                 grain is MANUFACTURING fragments the vendor never intended.")
print("                 The four 'collisions' would be OUR artifact.")
print("    DOUBLEHEADER spans HOURS            => game_id genuinely collides across")
print("                 two real games, and start_time belongs in the key FOR THOSE")
print("                 -- which is what ctrl4 caught (entry prices 23h after first")
print("                 pitch).")
print("  OPPOSITE implications. Do not average them.")
print()

duckdb.sql(f"""
CREATE OR REPLACE TEMP TABLE spans AS
SELECT game_id,
       count(DISTINCT start_time)                            AS n_starts,
       date_diff('minute', min(start_time), max(start_time)) AS span_min,
       min(start_time)                                       AS first_start,
       max(start_time)                                       AS last_start,
       count(*)                                              AS n_rows
FROM read_parquet('{SRC}')
GROUP BY 1
""")

print("DISTRIBUTION OF start_time SPAN, PER game_id")
duckdb.sql("""
SELECT CASE
         WHEN n_starts = 1   THEN 'a. one start_time (clean)'
         WHEN span_min <= 5  THEN 'b. <= 5 min    (DRIFT)'
         WHEN span_min <= 90 THEN 'c. 6-90 min    (?)'
         WHEN span_min <= 400 THEN 'd. 91-400 min (DOUBLEHEADER?)'
         ELSE                     'e. > 400 min   (??)'
       END           AS bucket,
       count(*)      AS game_ids,
       min(span_min) AS min_span,
       max(span_min) AS max_span,
       sum(n_starts) AS total_identities
FROM spans GROUP BY 1 ORDER BY 1
""").show()

print("EVERY game_id WITH MORE THAN ONE start_time (June):")
duckdb.sql("""
SELECT game_id, n_starts, span_min, first_start, last_start, n_rows
FROM spans WHERE n_starts > 1 ORDER BY span_min DESC, game_id
""").show(max_rows=60)
