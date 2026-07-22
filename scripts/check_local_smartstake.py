import duckdb
duckdb.sql("""
SELECT regexp_extract(filename, 'mon=([0-9-]+)', 1) AS mon,
       count(*)               AS rows,
       count(DISTINCT game_id) AS games,
       min(start_time)        AS first_start,
       max(start_time)        AS last_start
FROM read_parquet('data/market/smartstake/**/*.parquet', filename=true)
GROUP BY 1 ORDER BY 1
""").show()
