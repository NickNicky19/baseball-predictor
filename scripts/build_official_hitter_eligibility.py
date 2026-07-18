#!/usr/bin/env python3
"""Build game-keyed official hitter targets and starter/PA eligibility facts.

This artifact is for historical market grading only.  It uses final box scores
after the game to supply the scored target and official starter role; it must
never be fed into a forward-looking probability reconstruction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_gate_reconstruct import load_strict_market_manifest  # noqa: E402
from src.data.mlb_api import MLBStatsAPI  # noqa: E402
from src.evaluation.official_hitter_eligibility import (  # noqa: E402
    ELIGIBILITY_KEY,
    canonical_game_dates,
    game_eligibility_rows,
    official_hits_actuals,
    validate_eligibility,
)
from src.evaluation.official_game_completion import (  # noqa: E402
    game_completion_row,
    validate_game_completion,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def write_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", delete=False,
        dir=path.parent, suffix=".tmp",
    )
    temp = Path(handle.name)
    try:
        with handle:
            frame.to_csv(handle, index=False)
        pd.read_csv(temp)  # atomic publication only after an independent readback
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--strict-manifest",
                        help="build only the strict artifact's certified game universe")
    source.add_argument("--crosswalk-fragments",
                        help="build every hard-mapped game in crosswalk_fragments.csv")
    source.add_argument("--market-source",
                        help="build the exact canonical games named by a market-source CSV")
    ap.add_argument("--out", default="data/market/v3/official_hitter_eligibility.csv")
    ap.add_argument("--actuals-out", default="data/market/v3/official_hits_actuals.csv")
    ap.add_argument("--game-completion-out", default=None,
                    help="optional official final/scheduled-inning grading facts")
    ap.add_argument("--manifest-out", default=None)
    args = ap.parse_args(argv)

    if args.strict_manifest:
        dates = load_strict_market_manifest(args.strict_manifest)
        strict_path = Path(args.strict_manifest).with_name("strict_unique_hits.csv")
        strict = pd.read_csv(strict_path)
        required = ["mlb_game_pk", "official_game_date"]
        missing = [column for column in required if column not in strict.columns]
        if missing:
            raise ValueError(f"{strict_path}: missing {missing}")
        strict["official_game_date"] = pd.to_datetime(
            strict["official_game_date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        if strict["official_game_date"].isna().any():
            raise ValueError(f"{strict_path}: invalid official_game_date")
        dates_in_artifact = sorted(strict.official_game_date.unique().tolist())
        if dates_in_artifact != dates:
            raise ValueError("strict artifact/manifest canonical date mismatch")
        # Many strict selections share one official game.  Collapse only this
        # verified (game_pk, official_date) relationship; never pick a market
        # row to represent it.
        game_dates = canonical_game_dates(
            strict, "mlb_game_pk", "official_game_date", str(strict_path)
        )
        source_path = strict_path
        source_label = "strict_artifact"
    elif args.crosswalk_fragments:
        source_path = Path(args.crosswalk_fragments)
        fragments = pd.read_csv(source_path)
        required = ["mlb_game_pk", "official_date", "outcome"]
        missing = [column for column in required if column not in fragments.columns]
        if missing:
            raise ValueError(f"{source_path}: missing {missing}")
        game_dates = canonical_game_dates(
            fragments.loc[fragments.outcome.eq("mapped")],
            "mlb_game_pk", "official_date", str(source_path),
        )
        dates = sorted(game_dates.official_game_date.unique().tolist())
        source_label = "crosswalk_fragments"
    else:
        source_path = Path(args.market_source)
        market = pd.read_csv(source_path)
        game_dates = canonical_game_dates(
            market, "mlb_game_pk", "official_game_date", str(source_path),
        )
        dates = sorted(game_dates.official_game_date.unique().tolist())
        source_label = "market_source"

    api = MLBStatsAPI(season=int(dates[0][:4]))
    parts: list[pd.DataFrame] = []
    game_completion_parts: list[pd.DataFrame] = []
    print(f"building official hitter eligibility for {len(game_dates):,} games across "
          f"{len(dates):,} canonical dates")
    for i, row in enumerate(game_dates.itertuples(index=False), start=1):
        game_pk = int(row.mlb_game_pk)
        game_date = str(row.official_game_date)
        feed = api._get_game_feed(game_pk)
        game_completion_parts.append(game_completion_row(game_pk, game_date, feed))
        hitters, _ = api.get_game_boxscore_stats(game_pk)
        roles = api.get_completed_game_batting_roles(game_pk)
        starters = {
            player_id for player_id, role in roles.items() if role["is_starter"]
        }
        starters_per_side = {
            side: sum(
                bool(role["is_starter"] and role["team_side"] == side)
                for role in roles.values()
            )
            for side in ("away", "home")
        }
        if starters_per_side != {"away": 9, "home": 9}:
            raise ValueError(
                f"game_pk {game_pk}: completed box score does not prove exactly "
                f"nine original starters per side: {starters_per_side}"
            )
        parts.append(game_eligibility_rows(
            game_pk, game_date, hitters, starters, batting_roles=roles,
        ))
        if i % 25 == 0 or i == len(game_dates):
            print(f"  [{i}/{len(game_dates)}] games")

    bridge = pd.concat(parts, ignore_index=True)
    validate_eligibility(bridge)
    game_completion = pd.concat(game_completion_parts, ignore_index=True)
    validate_game_completion(game_completion)
    actuals = official_hits_actuals(bridge)
    out = Path(args.out)
    actuals_out = Path(args.actuals_out)
    write_csv_atomic(bridge, out)
    write_csv_atomic(actuals, actuals_out)
    completion_out = Path(args.game_completion_out) if args.game_completion_out else None
    if completion_out is not None:
        write_csv_atomic(game_completion, completion_out)
    manifest = dict(
        _comment=(
            "Official postgame hitter eligibility for historical grading only. "
            "Starter role comes from MLB's official completed-game batting order; "
            "PA and hits come from the official box score. These facts must never "
            "be used as forward model inputs."
        ),
        built_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_type=source_label,
        source_path=str(source_path.resolve()),
        source_sha256=sha256(source_path),
        official_date_universe=dates,
        games=int(len(game_dates)),
        hitter_rows=int(len(bridge)),
        starters=int(bridge.is_starter.sum()),
        substitutes=int((~bridge.is_starter).sum()),
        artifacts=dict(
            eligibility=str(out.resolve()), eligibility_sha256=sha256(out),
            hits_actuals=str(actuals_out.resolve()), hits_actuals_sha256=sha256(actuals_out),
            **({
                "game_completion": str(completion_out.resolve()),
                "game_completion_sha256": sha256(completion_out),
            } if completion_out is not None else {}),
        ),
    )
    manifest_path = (Path(args.manifest_out) if args.manifest_out else
                     out.with_name(out.stem + "_manifest.json"))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(bridge):,} official hitter rows)")
    print(f"      {actuals_out} ({len(actuals):,} official hits targets)")
    if completion_out is not None:
        print(f"      {completion_out} ({len(game_completion):,} official game rows)")
    print(f"      {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
