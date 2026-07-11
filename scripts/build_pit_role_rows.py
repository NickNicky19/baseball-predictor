"""
B4 gate prep — build POINT-IN-TIME role labels + realized innings for the
historical fit (2023), matching the ruler the GATE actually uses.

WHY THIS EXISTS (read before changing anything)
-----------------------------------------------
fit_role_innings.py needs two things per row:
  role       -- which bucket (opener / bulk / starter) the pitcher was in
  actual_ip  -- how many innings he ACTUALLY threw that game (the fit target)

The obvious sources are both wrong, for reasons worth recording:

1. The forward pairs CSV (scripts/build_pitcher_roster.py's --pairs) has only
   ~56 graded K rows (opener n=3, bulk n=6) -> DO-NOT-GATE-YET. Not fittable.

2. The A3 training set (data/training/training_pitchers_*.csv.gz) has 14,816
   rows with real out_ip, but its `pit_gs` column COUNTS APPEARANCES, NOT
   STARTS -- the exact games_started=len(rows) conflation that this session
   fixed in point_in_time._aggregate_pitching. Verified on Tyler Holton
   (663947, a reliever): pit_gs climbs 17 -> 28 -> 39 -> 41 while he throws
   1-2 IP per outing, and pit_recent_gs=5 claims he "started" all 5 recent
   games. Deriving start_ratio from pit_gs would label every reliever a
   STARTER and fit opener_innings from a population containing no openers.
   The training set carries the bug; do not fit from its pit_gs.

   The training set also cannot supply `games` (appearances) at all: it holds
   one row per game a pitcher STARTED, so len(rows) is his starts, not his
   appearances (Holton: 9 rows for 2024, but ~70 real appearances).

So role MUST come from the MLB game log, through the FIXED point-in-time path.

THE RULER (this is the whole point)
-----------------------------------
The gate does NOT label roles from season lines. In production,
MLBStatsAPI.get_pitchers_for_date does:

    _, recent = self.get_pitching_stats(pitcher_id)     # AsOfMLBAPI -> as-of
    expected_ip = self._innings_estimator.estimate(recent)

and AsOfMLBAPI.get_pitching_stats delegates to
PointInTimeStats.get_pitching_stats_as_of(pid, as_of), whose pitching
`recent` window is the LAST 5 APPEARANCES STRICTLY BEFORE the as-of date
(recent_games=5). So the estimator sees a 5-game trailing point-in-time
snapshot -- NOT a season line.

This builder therefore labels each (pitcher, game_date) with the SAME
5-game point-in-time snapshot, produced by the SAME production code
(PointInTimeStats + RoleAwareInningsEstimator, imported, never
reimplemented). Fit constants and gate constants then describe the same
population under the same ruler. A season-level roster (the A6 default)
would fit a DIFFERENT population than the gate applies them to -- one label
per pitcher-season instead of one per pitcher-game -- and would, e.g., call
an April opener a September starter.

OUTPUT
------
A K-rows CSV in exactly the schema run_analyze_k_error.load_k_rows expects:
    category, player_id, game_date, actual_ip, actual_strikeouts,
    predicted_value, games, games_started, innings_pitched, role, start_ratio

`games` / `games_started` / `innings_pitched` are the POINT-IN-TIME recent
snapshot, so this file doubles as its own --roster (load_roster's required
columns are a subset). fit_role_innings can then join it to itself and every
row carries the role the gate would have assigned that day.

predicted_value is a SENTINEL (1.0). load_k_rows requires it to be numeric,
but fit_constants NEVER reads it -- it fits only actual_ip grouped by role.
It exists to satisfy the loader, and the A6 before/after rescale must NOT be
read off a historical run. Flagged loudly in the output.

READ-ONLY / ADDITIVE: GET-only against the MLB Stats API (through the
project's own MLBStatsAPI, so the http_cache is reused -- a re-run is nearly
free). Sole write is --out. Never touches config.json, never forks
model_version.

Usage (from repo root, network required on first run):
    python scripts/build_pit_role_rows.py \
        --training data/training/training_pitchers_2023_2025.csv.gz \
        --season 2023 \
        --out data/analysis/b4/k_rows_pit_2023.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from src.prediction.role_innings import RoleAwareInningsEstimator  # noqa: E402

# The placeholder cuts. Role ASSIGNMENT is structural and must not be re-fit
# from the innings it produces (fit_role_innings' own docstring), so the cuts
# used to LABEL here are the shipped ones, and only the innings MAGNITUDES
# (opener_innings / bulk_innings) get fitted downstream.
from run_analyze_k_error import DEFAULT_ROLE_INNINGS_BLOCK  # noqa: E402


def mlb_ip_to_float(value: Any) -> float:
    """MLB innings notation -> real innings. '5.2' == 5 and 2/3, NOT 5.2.

    The A3 training set's out_ip is in MLB notation: its decimal digit is only
    ever 0, 1 or 2 (verified across all 14,816 rows -- .3-.9 never occur).
    Averaging it raw would systematically UNDERSTATE innings and silently skew
    the fitted constants. This mirrors point_in_time._ip, deliberately.
    """
    try:
        text = str(value)
        if "." in text:
            whole, outs = text.split(".", 1)
            return int(whole) + int(outs[0]) / 3.0
        return float(text)
    except (TypeError, ValueError, IndexError):
        return 0.0


def load_training_rows(path: Path, season: int) -> pd.DataFrame:
    """The (pitcher, game_date, actual_ip, actual_k) rows we will label."""
    if not path.exists():
        raise FileNotFoundError(f"training file not found: {path}")
    df = pd.read_csv(path, low_memory=False)
    need = {"season", "game_date", "player_id", "out_ip", "out_k"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing columns {sorted(missing)}")

    df = df[df["season"] == season].copy()
    if df.empty:
        raise ValueError(f"no rows for season={season} in {path}")

    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce")
    df = df.dropna(subset=["player_id"])
    df["player_id"] = df["player_id"].astype(int)
    df["game_date"] = df["game_date"].astype(str).str[:10]

    # THE FIT TARGET. Convert out of MLB notation.
    df["actual_ip"] = df["out_ip"].apply(mlb_ip_to_float)
    df["actual_strikeouts"] = pd.to_numeric(df["out_k"], errors="coerce")

    df = df.dropna(subset=["actual_strikeouts"])
    n_before = len(df)
    # load_k_rows requires actual_ip > 0; a 0-IP start (yanked before an out)
    # carries no innings signal for a role-innings fit.
    df = df[df["actual_ip"] > 0]
    if len(df) != n_before:
        print(f"[rows] dropped {n_before - len(df)} row(s) with actual_ip <= 0")

    return df[["player_id", "game_date", "actual_ip", "actual_strikeouts"]].reset_index(drop=True)


def label_rows_point_in_time(
    rows: pd.DataFrame,
    pit: Any,
    estimator: RoleAwareInningsEstimator,
    recent_games: int = 5,
    model_version_tag: str = "historical",
) -> pd.DataFrame:
    """
    For each (pitcher, game_date), rebuild the POINT-IN-TIME recent snapshot
    the gate would have seen, and label the role with the REAL estimator.

    `pit` is a PointInTimeStats (or anything with the same
    get_pitching_stats_as_of signature -- the offline harness stubs it).

    recent_games=5 is not a free parameter: it is
    PointInTimeStats.get_pitching_stats_as_of's pitching default, which is
    what AsOfMLBAPI (and therefore the gate) uses. Changing it here would fit
    constants under a different ruler than the gate applies them with.
    """
    out: list[dict[str, Any]] = []
    n_players = rows["player_id"].nunique()
    seen = 0
    last_pid: Optional[int] = None

    # Sorted by player so each pitcher's game log is fetched exactly once and
    # then reused across all his dates (PointInTimeStats caches per player).
    for _, r in rows.sort_values(["player_id", "game_date"]).iterrows():
        pid = int(r["player_id"])
        if pid != last_pid:
            seen += 1
            last_pid = pid
            if seen % 25 == 0 or seen == 1:
                print(f"  [{seen}/{n_players}] pitchers processed...", flush=True)

        try:
            _season_snap, recent = pit.get_pitching_stats_as_of(
                pid, str(r["game_date"]), recent_games=recent_games
            )
        except Exception as exc:  # noqa: BLE001 — one bad player must not kill the build
            print(f"  WARN {pid} @ {r['game_date']}: {exc}", file=sys.stderr)
            continue

        res = estimator.estimate_detailed(recent)
        out.append({
            "category": "strikeouts",          # load_k_rows filters on this
            "player_id": pid,
            "game_date": str(r["game_date"]),
            "actual_ip": float(r["actual_ip"]),
            "actual_strikeouts": float(r["actual_strikeouts"]),
            # SENTINELS. load_k_rows requires these to exist and be numeric /
            # non-empty, but fit_constants NEVER reads them -- it fits only
            # actual_ip grouped by role. The simulator never predicted these
            # historical games, so there is no real predicted_value or
            # confidence to record. They exist to satisfy the loader.
            #
            # Do NOT read A6's before/after rescale off a file built from this:
            # k_error = actual_strikeouts - predicted_value would be measured
            # against the sentinel, i.e. meaningless.
            "predicted_value": 1.0,
            "confidence": 1.0,
            # model_version: these rows are DESCRIPTIVE historical facts (how
            # many innings each role actually threw), not predictions of any
            # model. Stamped with the frozen hash because that is the baseline
            # the gate compares against, and because load_k_rows warns when
            # rows span multiple versions -- a single consistent value keeps
            # that warning honest rather than firing on an empty string.
            "model_version": model_version_tag,
            # Point-in-time recent snapshot == what the gate's estimator sees.
            # Also makes this file usable as its own --roster (load_roster
            # requires player_id/games/games_started, + innings_pitched).
            "games": int(getattr(recent, "games", 0) or 0),
            "games_started": int(getattr(recent, "games_started", 0) or 0),
            "innings_pitched": float(getattr(recent, "innings_pitched", 0.0) or 0.0),
            "role": res.role,
            "start_ratio": res.start_ratio,
            "b4_expected_innings": res.expected_innings,
        })

    return pd.DataFrame(out)


def summarize(df: pd.DataFrame) -> None:
    print("\nROLE DISTRIBUTION (point-in-time, 5-game trailing window — the gate's ruler)")
    print("-" * 72)
    if df.empty:
        print("  (no rows)")
        return
    grp = df.groupby("role", observed=True).agg(
        n=("actual_ip", "size"),
        mean_actual_ip=("actual_ip", "mean"),
        median_actual_ip=("actual_ip", "median"),
        mean_start_ratio=("start_ratio", "mean"),
    ).round(3)
    print(grp.to_string())

    n_unknown = int((df["role"] == "unknown").sum())
    if n_unknown:
        pct = 100.0 * n_unknown / len(df)
        print(f"\n  role='unknown': {n_unknown} rows ({pct:.1f}%) — pitchers with fewer than "
              f"min_games_for_role appearances before that date (early-season, call-ups). "
              f"This is the honest fallback, not a bug; they are excluded from the fit.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Build point-in-time role + actual_ip rows for the B4 historical fit.")
    ap.add_argument("--training", default="data/training/training_pitchers_2023_2025.csv.gz",
                    help="A3 training set (source of actual_ip / actual_k)")
    ap.add_argument("--season", type=int, default=2023,
                    help="fit season (default 2023 — keeps 2024-25 a clean gate holdout)")
    ap.add_argument("--out", default="data/analysis/b4/k_rows_pit_2023.csv")
    ap.add_argument("--recent-games", type=int, default=5,
                    help="trailing window for the point-in-time snapshot. Default 5 = the "
                         "pitching default in PointInTimeStats.get_pitching_stats_as_of, "
                         "i.e. exactly what the gate sees. Change only with a reason.")
    ap.add_argument("--limit-pitchers", type=int, default=None,
                    help="smoke-test knob: only process the first N pitchers")
    ap.add_argument("--config", default="config/config.json",
                    help="real config; used ONLY to stamp the frozen model_version on the "
                         "output rows (never modified)")
    args = ap.parse_args(argv)

    # Stamp rows with the FROZEN hash. These are descriptive historical facts,
    # not predictions -- but load_k_rows needs a consistent, non-empty
    # model_version, and the frozen hash is the honest label for "the baseline
    # this fit is being prepared against".
    mv_tag = "historical"
    cfg_path = Path(args.config)
    if cfg_path.exists():
        import json
        from src.utils.model_version import model_version
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if "role_innings" in cfg:
            print("WARNING: config.json already has a role_innings block — the live model "
                  "may no longer be the frozen baseline. Investigate before fitting.",
                  file=sys.stderr)
        mv_tag = model_version(cfg)
        print(f"[rows] stamping rows with frozen model_version={mv_tag}")
    else:
        print(f"[rows] {cfg_path} not found — stamping model_version='{mv_tag}'")

    rows = load_training_rows(Path(args.training), args.season)
    if args.limit_pitchers:
        keep = sorted(rows["player_id"].unique())[: args.limit_pitchers]
        rows = rows[rows["player_id"].isin(keep)]
        print(f"[rows] --limit-pitchers {args.limit_pitchers} -> {len(rows)} rows")

    print(f"[rows] season={args.season}: {len(rows)} pitcher-games, "
          f"{rows['player_id'].nunique()} unique pitchers, "
          f"{rows['game_date'].nunique()} dates")
    print(f"[rows] one gameLog fetch per pitcher (cached by http_cache; a re-run is ~free)")

    # Lazy import: only pull the real network/package graph when actually
    # building, so the offline harness can exercise the pure logic above.
    from src.data.mlb_api import MLBStatsAPI  # noqa: E402
    from src.data.point_in_time import PointInTimeStats  # noqa: E402

    pit = PointInTimeStats(
        mlb_api=MLBStatsAPI(season=args.season), season=args.season
    )
    # Label with the SHIPPED cuts (structural), enabled so estimate_detailed
    # actually routes. The MAGNITUDES it returns are irrelevant here -- we only
    # use .role / .start_ratio. fit_role_innings fits the magnitudes.
    estimator = RoleAwareInningsEstimator(
        {"role_innings": dict(DEFAULT_ROLE_INNINGS_BLOCK, enabled=True)}
    )

    labelled = label_rows_point_in_time(
        rows, pit, estimator, recent_games=args.recent_games, model_version_tag=mv_tag
    )
    if labelled.empty:
        print("[rows] nothing labelled — check the game-log fetches", file=sys.stderr)
        return 2

    summarize(labelled)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    labelled.to_csv(out, index=False)

    print(f"\n[rows] wrote {len(labelled)} rows -> {out}")
    print("\nNOTE: predicted_value in this file is a SENTINEL (1.0). The fit reads ONLY")
    print("      `role` and `actual_ip`; it never reads predicted_value. Do NOT read")
    print("      A6's before/after rescale off a historical run.")
    print("\nNOTE: this file carries ONE ROLE PER (pitcher, game_date) -- the point-in-time")
    print("      label the gate would have assigned that day. Do NOT feed it to")
    print("      run_analyze_k_error.add_role_and_recompute: that merges a roster on")
    print("      player_id ALONE and would collapse every pitcher to a single")
    print("      season-level role, destroying the point-in-time labelling this file")
    print("      exists to provide. Use scripts/fit_role_innings_pit.py instead.")
    print(f"\nNext: python scripts/fit_role_innings_pit.py --rows {out} "
          f"--out data/analysis/b4/candidate_role_innings.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
