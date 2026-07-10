# APPLY A2 — point-in-time reconstruction of one historical date

Two new files, zero modified files, zero live-model contact. Nothing here
writes to archives or the pairs CSV — collection stays clean.

## What's in a2_files.zip (paths relative to repo root)

    run_reconstruct_date.py                NEW  the A2 pipeline + CLI
    scripts/check_reconstruct_offline.py   NEW  no-network validation harness

## What it does

`run_reconstruct_date.py --date 2025-06-15` rebuilds that slate as it was
knowable pre-game: PointInTimeStats (first real wiring — game logs strictly
before the date), actual persisted lineups as confirmed orders, bundles via
the unmodified FeatureFactory, projections via the unmodified PropEngine,
scored against real boxscore actuals via evaluate_bundles (its first real
caller). Pitcher strikeouts included by default — the as-of stats feed both
the K rate and expected_innings, so the IP opportunity input is leakage-safe
too.

The per-feature RESOLUTION TABLE is the real A2 deliverable — it is the A3
scoping evidence, printed to console and saved in the JSON report:

    resolved      as-of stats, lineups, opposing pitcher, park, umpire id
    empty         players with no pre-date games (April call-ups)
    default       weather/umpire when the feed lacks them
    neutralized   platoon splits + BvP (season-to-NOW = leaky, by policy)
    bypassed      injury feed (actual lineup already proves availability)

Statcast profiles are forced to league baselines (`savant_csv=None`): a
current-season Savant CSV is an as-of-NOW snapshot and would leak into any
past date. Expect hitter MAE visibly worse than live because of this — that
is the honest historical floor, it is exactly what A4's per-game Statcast
features exist to improve, and it is the simulator baseline Phase-B models
must beat. Do not be discouraged by the first numbers.

## Steps

1. Extract a2_files.zip over C:\Projects\baseball_predictor.
2. Validate offline first (no API calls, ~1 min):

       python scripts/check_reconstruct_offline.py

   Expect "23 passed, 0 failed". This runs the REAL pipeline on fixtures
   with leakage canaries — absurd stat lines placed ON the target date that
   must be excluded from as-of features.
3. Run one real date (any completed 2025 slate is a good first pick):

       python run_reconstruct_date.py --date 2025-06-15

   Output: console resolution table + metrics, and
   reports/reconstruction_2025-06-15.json (tagged with model_version).
4. Commit both files plus the report:

       git add run_reconstruct_date.py scripts/check_reconstruct_offline.py reports/reconstruction_*.json
       git commit -m "A2: point-in-time reconstruction of one historical date"
       git push

## What to look at in the first real report (this IS the A2 scoping)

- lineup: how many status_confirmed? (Persisted battingOrder should make
  most/all confirmed — a genuinely good surprise vs the roadmap's
  assumption that historical lineups would be a fallback.)
- opposing_pitcher: resolved vs missing tells you whether probablePitcher
  persists on historical schedules — this was unverifiable offline.
- weather: resolved vs default tells you whether the game feed keeps the
  weather block for completed games — also unverifiable offline.
- hitting/pitching_stats_as_of "empty" counts: how many call-ups/debuts a
  typical date has (these become league-baseline rows in A3).
- pitcher_actuals "missing": scheduled probables who never pitched.

Try one date each from April, June, and September to see how "empty" counts
shrink as the season matures — that directly informs A3's date range.

## API cost note (relevant for A3, fine for A2)

One date ≈ 1 schedule call + 1 feed call per game + 1 identity + 1-2 game-log
calls per player (~350-450 total, cached in-memory per run). Fine for single
dates; A3's ~500k-row builder is where disk caching + rate limiting get built.

## Notes / limitations (known, deliberate)

- Umpire identity resolves from the boxscore, but tendencies are neutral
  (0.0) unless load_tendencies_from_pairs is ever wired — identity without
  signal.
- --allow-leaky-splits exists to MEASURE how much platoon/BvP would matter,
  never for training data.
- If a very old game's live feed is missing, get_batting_order only catches
  HTTPError; a DataFetchError would surface as a failed run. Not observed
  for 2023+ dates; flag it if you hit one.

## Paste into project_context (A2 status update)

- [x] A2 DONE (2026-07-08). run_reconstruct_date.py: PointInTimeStats (now
      wired, first use) -> bundles -> evaluate_bundles (first caller) for
      one historical date, pitchers included (as-of K rate AND as-of
      expected_ip). Per-feature resolution table = A3 scoping, in
      reports/reconstruction_<date>.json + console. Leakage policy:
      Statcast->baselines (A4 fixes), splits/BvP neutralized, injuries
      bypassed, strict-< stats (canary-tested offline, 23/23:
      scripts/check_reconstruct_offline.py). Additive only; nothing writes
      to archives/pairs. OPEN QUESTIONS for first real runs: do historical
      probablePitcher + feed weather persist (log will say); April "empty"
      rates -> informs A3 date range.
