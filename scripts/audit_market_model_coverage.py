#!/usr/bin/env python3
"""Diagnose strict-market model coverage without changing either universe.

This is deliberately an AUDIT, not an intersection builder.  A missing model
row is evidence about the reconstruction boundary; it must not silently shrink
the market artifact or turn into a post-hoc coverage rule.

For historical smoke artifacts, official starter/PA facts come from the frozen
game-keyed eligibility bridge.  The audit must not re-fetch a mutable API
response simply to explain a frozen experiment.  These facts are never fed
back into a probability reconstruction, where they would be outcome leakage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_gate_reconstruct import load_strict_market_manifest  # noqa: E402
from src.evaluation.identity_keys import MODEL_KEY  # noqa: E402
from src.evaluation.market_model_coverage import hitter_key_gap  # noqa: E402
from src.evaluation.official_hitter_eligibility import validate_eligibility  # noqa: E402
from src.evaluation.strict_market_artifact import artifact_path_for_manifest  # noqa: E402


def _normalise_date(value: str) -> str:
    parsed = pd.Timestamp(value)
    if pd.isna(parsed) or str(value)[:10] != parsed.strftime("%Y-%m-%d"):
        raise ValueError(f"invalid YYYY-MM-DD date {value!r}")
    return parsed.strftime("%Y-%m-%d")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict-manifest", required=True)
    ap.add_argument("--model", required=True, help="frozen or candidate sim-probability CSV")
    ap.add_argument("--date", required=True,
                    help="one canonical official game date present in the model smoke")
    ap.add_argument("--eligibility", default="data/market/v3/official_hitter_eligibility.csv",
                    help="frozen game-keyed official starter/PA bridge")
    ap.add_argument("--out", default=None,
                    help="CSV evidence output; default is beside --model")
    args = ap.parse_args(argv)

    date = _normalise_date(args.date)
    # This verifies artifact hash, key uniqueness, and that the requested smoke
    # date belongs to the canonical (not vendor-labelled) date universe.
    load_strict_market_manifest(args.strict_manifest, smoke_date=date)
    artifact = artifact_path_for_manifest(args.strict_manifest)
    market = pd.read_csv(artifact)
    needed_market = [*MODEL_KEY, "official_game_date"]
    missing = [c for c in needed_market if c not in market.columns]
    if missing:
        raise ValueError(
            f"{artifact}: missing {missing}. Rebuild the strict artifact with "
            "canonical official_game_date before auditing model coverage."
        )
    model = pd.read_csv(args.model)
    (scoped_market, hit_model, market_keys, model_keys,
     missing_keys) = hitter_key_gap(market, model, date)

    bridge = pd.read_csv(args.eligibility)
    validate_eligibility(bridge)
    bridge["official_game_date"] = pd.to_datetime(
        bridge.official_game_date, errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    role_join = scoped_market[[*MODEL_KEY, "official_game_date"]].merge(
        bridge[["mlb_game_pk", "player_id", "official_game_date", "is_starter",
                "official_pa", "official_hits"]],
        on=["mlb_game_pk", "player_id", "official_game_date"],
        how="left", validate="many_to_one",
    )
    if role_join[["is_starter", "official_pa", "official_hits"]].isna().any().any():
        bad = role_join[role_join.is_starter.isna()]
        raise ValueError(
            "strict market has no frozen official role/target for one or more "
            "rows; this artifact cannot be scored:\n"
            f"{bad.head(20).to_string(index=False)}"
        )

    missing = pd.DataFrame(sorted(missing_keys), columns=MODEL_KEY)
    evidence = missing.merge(
        bridge[["mlb_game_pk", "player_id", "is_starter", "official_pa", "official_hits"]],
        on=["mlb_game_pk", "player_id"], how="left", validate="many_to_one",
    )
    if len(evidence):
        evidence = evidence.rename(columns={
            "is_starter": "official_starter",
            "official_pa": "pa",
            "official_hits": "hits",
        })
        evidence["official_boxscore_player"] = evidence.official_starter.notna()
    games = sorted(set(scoped_market.mlb_game_pk.astype(int)))
    roster_rows: list[dict] = []
    for game_pk in games:
        game_bridge = bridge[bridge.mlb_game_pk.eq(game_pk)]
        starters = set(game_bridge.loc[game_bridge.is_starter.astype(bool), "player_id"].astype(int))
        hitters = set(game_bridge.player_id.astype(int))
        reconstructed = set(
            hit_model.loc[hit_model.mlb_game_pk.eq(game_pk), "player_id"].astype(int)
        )
        roster_rows.append(dict(
            mlb_game_pk=game_pk,
            official_starters=len(starters),
            modelled_hitters=len(reconstructed),
            official_boxscore_hitters=len(hitters),
            missing_official_starters=len(starters - reconstructed),
            modelled_nonstarters=len(reconstructed - starters),
        ))
    roster = pd.DataFrame(roster_rows)

    print("=" * 96)
    print(f"STRICT-MARKET MODEL COVERAGE — canonical official date {date}")
    print("=" * 96)
    print("  FACTS ONLY. This audit never intersects, drops, or rebuilds either universe.")
    print(f"  strict MARKET_KEYs : {len(market_keys):,}")
    print(f"  model hits keys    : {len(model_keys):,}")
    print(f"  missing model keys : {len(missing_keys):,}")
    print()
    print("  per-game roster relation (hits only; pitchers excluded):")
    print("  " + roster.to_string(index=False).replace("\n", "\n  "))
    if len(evidence):
        print()
        print("  missing strict keys by official role:")
        print("  " + evidence.groupby(
            ["official_starter", "official_boxscore_player"], dropna=False
        ).size().rename("keys").to_string().replace("\n", "\n  "))
        print()
        print(evidence.to_string(index=False))

    out = Path(args.out) if args.out else Path(args.model).with_name(
        Path(args.model).stem + "_strict_coverage.csv"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    evidence.to_csv(out, index=False)
    roster.to_csv(out.with_name(out.stem + "_roster.csv"), index=False)
    summary = dict(
        date=date,
        strict_market_keys=len(market_keys),
        model_hits_keys=len(model_keys),
        missing_model_keys=len(missing_keys),
        missing_official_starter_keys=int(evidence.official_starter.sum()) if len(evidence) else 0,
        missing_official_nonstarter_keys=int((~evidence.official_starter).sum()) if len(evidence) else 0,
    )
    out.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}\n      {out.with_name(out.stem + '_roster.csv')}\n      {out.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
