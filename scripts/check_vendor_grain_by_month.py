import duckdb
duckdb.sql("""
WITH src AS (
    SELECT *, regexp_extract(filename,'mon=([0-9-]+)',1) AS mon,
           CAST(start_time AT TIME ZONE 'UTC'
                           AT TIME ZONE 'America/New_York' AS DATE) AS slate_date
    FROM read_parquet('data/market/smartstake/**/*.parquet', filename=true)
)
SELECT mon,
       count(DISTINCT slate_date)                        AS dates,
       count(DISTINCT game_id)                           AS n_game_ids,
       count(DISTINCT (game_id, start_time))             AS n_exact_identities,
       round(count(DISTINCT (game_id, start_time))
             * 1.0 / count(DISTINCT game_id), 3)         AS frag_ratio,
       round(count(DISTINCT game_id)
             * 1.0 / count(DISTINCT slate_date), 1)      AS game_ids_per_date,
       count(*) FILTER (WHERE result IS NOT NULL)        AS graded_rows,
       round(count(*) FILTER (WHERE result IS NOT NULL)
             * 100.0 / count(*), 1)                      AS pct_graded
FROM src GROUP BY 1 ORDER BY 1
""").show()
