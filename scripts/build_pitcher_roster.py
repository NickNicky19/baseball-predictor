"""
B4 gate prep — build the --roster CSV for run_analyze_k_error.py (A6 layer 2).

A6's role-aware before/after needs player_id -> (games, games_started,
innings_pitched). This pulls SEASON pitching lines for every pitcher that
appears in the outcome pairs' strikeouts rows, through the project's OWN
MLBStatsAPI.get_pitching_stats (season snapshot) — the same _parse_pitching
the live path uses, so the roster carries EXACTLY what the estimator would
see in production (games UNFLOORED, games_started floored to >=1; both are
settled [B4] decisions — do not "fix" here). Season-level signal is correct
for A6's descriptive fit per the [A6] key decision; the gate reconstruction
itself uses point-in-time snapshots as usual.

READ-ONLY / ADDITIVE: GET-only against the MLB Stats API; sole write is the
--out CSV. Never touches config.json, never forks model_version.

Usage (from repo root, network required):
    python scripts/build_pitcher_roster.py \
        --pairs data/learning/prediction_outcomes.csv \
        --out data/analysis/pitcher_roles.csv
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402


def extract_pitcher_ids(pairs_path: Path) -> list[int]:
    """Unique pitcher IDs from the pairs CSV's strikeouts rows (sorted)."""
    df = pd.read_csv(pairs_path, dtype=str, keep_default_na=False, low_memory=False)
    if "category" not in df.columns or "player_id" not in df.columns:
        raise ValueError(f"{pairs_path} missing category/player_id columns")
    k = df[df["category"] == "strikeouts"]
    ids = pd.to_numeric(k["player_id"], errors="coerce").dropna().astype(int)
    return sorted(ids.unique().tolist())


def build_roster(player_ids: list[int], client, sleep_seconds: float = 0.2) -> pd.DataFrame:
    """
    One roster row per pitcher from the client's SEASON pitching snapshot.

    `client` needs only .get_pitching_stats(pid) -> (season, recent); the real
    MLBStatsAPI satisfies this, and the offline harness stubs it.
    """
    rows: list[dict] = []
    n_fail = 0
    n_empty = 0
    for i, pid in enumerate(player_ids, 1):
        try:
            season, _recent = client.get_pitching_stats(pid)
        except Exception as exc:  # noqa: BLE001 — one bad player shouldn't kill the pull
            n_fail += 1
            print(f"  [{i}/{len(player_ids)}] {pid}  FAILED: {exc}", file=sys.stderr)
            continue
        games = int(getattr(season, "games", 0) or 0)
        if games <= 0:
            n_empty += 1
        rows.append({
            "player_id": pid,
            "games": games,
            "games_started": int(getattr(season, "games_started", 0) or 0),
            "innings_pitched": float(getattr(season, "innings_pitched", 0.0) or 0.0),
        })
        if sleep_seconds and i < len(player_ids):
            time.sleep(sleep_seconds)

    roster = pd.DataFrame(rows, columns=["player_id", "games", "games_started", "innings_pitched"])
    print(f"[roster] {len(roster)} rows written for {len(player_ids)} ids "
          f"({n_fail} fetch failures, {n_empty} with games=0 — those classify as "
          f"role='unknown' downstream, which is the honest fallback, not a bug)")
    return roster


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build A6's --roster CSV from the K pairs' pitcher ids.")
    ap.add_argument("--pairs", default="data/learning/prediction_outcomes.csv")
    ap.add_argument("--out", default="data/analysis/pitcher_roles.csv")
    ap.add_argument("--season", type=int, default=None,
                    help="Season for the stats pull (default: config/config.json's season)")
    ap.add_argument("--config", default="config/config.json",
                    help="Only used to default --season")
    ap.add_argument("--sleep", type=float, default=0.2,
                    help="Seconds between MLB API calls (politeness; default 0.2)")
    args = ap.parse_args(argv)

    season = args.season
    if season is None:
        import json
        season = int(json.loads(Path(args.config).read_text(encoding="utf-8"))["season"])

    ids = extract_pitcher_ids(Path(args.pairs))
    print(f"[roster] {len(ids)} unique pitcher ids in {args.pairs} (strikeouts rows), season={season}")
    if not ids:
        print("[roster] nothing to do — no strikeouts rows yet", file=sys.stderr)
        return 2

    # Lazy import: pulls the real package graph (requests etc.) only when
    # actually fetching, so the offline harness can test the pure logic above.
    from src.data.mlb_api import MLBStatsAPI  # noqa: E402
    client = MLBStatsAPI(season=season)

    roster = build_roster(ids, client, sleep_seconds=args.sleep)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    roster.to_csv(out, index=False)
    print(f"[roster] wrote {out}")
    print("Next: python run_analyze_k_error.py --pairs {p} --roster {r}".format(p=args.pairs, r=out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
