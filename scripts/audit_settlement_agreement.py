#!/usr/bin/env python3
"""Audit SmartStake settlement presence against DK's published *base* rule.

The vendor's numeric ``result`` is never truth.  Its null/non-null state is a
candidate settlement-presence proxy only.  This script measures that proxy
against official starter and PA facts, at the same two-sided market-selection
grain used by the A/B eligibility module.

Published DK base MLB pregame rule (checked 2026-07-13): a hitter selection
must start and record at least one plate appearance; a substitute selection is
void.  Conditional Early Exit protection is intentionally OUT OF SCOPE: its
product and event eligibility cannot be inferred from a completed box score.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.market_eligibility import (  # noqa: E402
    MARKET_MAP, eligibility_sql, load_policy, parquet_source,
)
from src.evaluation.official_hitter_eligibility import (  # noqa: E402
    ELIGIBILITY_KEY, base_settlement_cell, validate_eligibility,
)


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def norm(value: object) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", str(value))
                   if not unicodedata.combining(c))
    text = text.lower().strip()
    for character in ".'`-":
        text = text.replace(character, "")
    return " ".join(part for part in text.split()
                    if part not in ("jr", "sr", "ii", "iii", "iv", "v"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/market/smartstake")
    ap.add_argument("--month", required=True)
    ap.add_argument("--policy", default="config/ab_policy.json")
    ap.add_argument("--crosswalk", default="data/market/v3/crosswalk_players.csv")
    ap.add_argument("--eligibility", required=True,
                    help="official_hitter_eligibility.csv from the strict date universe")
    ap.add_argument("--out", default="data/market/v3/settlement_agreement.json")
    args = ap.parse_args(argv)

    if not list(Path(args.root).glob(f"mon={args.month}/*.parquet")):
        print(f"FATAL: no local market parquet for {args.month}", file=sys.stderr)
        return 2
    values, policy_sha, _, _ = load_policy(args.policy)
    source = parquet_source(args.root, [args.month])
    raw = duckdb.sql(eligibility_sql(
        source, values["book"], values["entry_hours"], values["max_quote_age"]
    )).df()
    raw["start_time"] = pd.to_datetime(raw.start_time, utc=True)
    raw["player_key"] = raw.player.map(norm)
    raw["category"] = raw.market.map(MARKET_MAP)

    xw = pd.read_csv(args.crosswalk)
    xw["start_time"] = pd.to_datetime(xw.start_time, utc=True)
    crosswalk_key = ["vendor_game_id", "start_time", "player_key"]
    if xw.duplicated(crosswalk_key).any():
        print("FATAL: crosswalk consumer key is not unique", file=sys.stderr)
        return 2
    mapped = raw.merge(xw, on=crosswalk_key, how="left", validate="many_to_one")
    mapped = mapped[mapped.mlb_game_pk.notna() & mapped.player_id.notna()].copy()
    mapped["mlb_game_pk"] = mapped.mlb_game_pk.astype(int)
    mapped["player_id"] = mapped.player_id.astype(int)

    bridge = pd.read_csv(args.eligibility)
    validate_eligibility(bridge)
    bridge = bridge[[*ELIGIBILITY_KEY, "is_starter", "official_pa"]].copy()
    bridge["mlb_game_pk"] = bridge.mlb_game_pk.astype(int)
    bridge["player_id"] = bridge.player_id.astype(int)
    joined = mapped.merge(bridge, on=ELIGIBILITY_KEY, how="left", validate="many_to_one")
    joined["cell"] = [
        base_settlement_cell(bool(row.settlement_present),
                             None if pd.isna(row.is_starter) else bool(row.is_starter),
                             None if pd.isna(row.official_pa) else float(row.official_pa))
        for row in joined.itertuples()
    ]

    comparable = joined[joined.cell.ne("UNRESOLVED_ROLE")].copy()
    unresolved = joined[joined.cell.eq("UNRESOLVED_ROLE")].copy()
    counts = comparable.cell.value_counts()
    agreement = int(counts.get("AGREE_excluded", 0) + counts.get("AGREE_scored", 0))
    false_inclusion = int(counts.get("FALSE_INCLUSION", 0))
    false_exclusion = int(counts.get("FALSE_EXCLUSION", 0))

    print("=" * 96)
    print("SETTLEMENT-PRESENCE AUDIT — SmartStake proxy vs DK published BASE rule")
    print("=" * 96)
    print("  Base rule: pregame hitter must start and record >=1 PA; substitutes void.")
    print("  Conditional Early Exit treatment is NOT inferred here.")
    print("  Vendor numeric result is never read; only selection-level null presence is used.")
    print()
    print(f"  two-sided market selections       {len(raw):,}")
    print(f"  hard-mapped selections            {len(mapped):,}")
    print(f"  role/PA comparable                {len(comparable):,}")
    print(f"  official role unresolved          {len(unresolved):,}")
    print()
    for name in ("AGREE_scored", "AGREE_excluded", "FALSE_INCLUSION", "FALSE_EXCLUSION"):
        n = int(counts.get(name, 0))
        print(f"  {name:18s} {n:7,d}  ({n / max(len(comparable), 1):6.2%})")
    print()
    print("  FALSE_INCLUSION means vendor marked a selection settled although the")
    print("  official base rule would void it. This is the dangerous cell.")
    if len(unresolved):
        print("  UNRESOLVED ROLE is not called agreement or disagreement; it remains")
        print("  evidence the bridge cannot answer, never an assumed void.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    joined.to_csv(out.with_suffix(".csv"), index=False)
    payload = dict(
        _comment=(
            "SmartStake settlement-presence proxy measured against DraftKings' "
            "published BASE MLB pregame rule: starter plus >=1 PA. Conditional "
            "Early Exit rules are not inferred. This is evidence, never a policy "
            "promotion or historical DK settlement log."
        ),
        month=args.month,
        policy_sha256=policy_sha,
        crosswalk_sha256=sha256(args.crosswalk),
        eligibility_sha256=sha256(args.eligibility),
        counts=dict(two_sided=int(len(raw)), hard_mapped=int(len(mapped)),
                    comparable=int(len(comparable)), unresolved_role=int(len(unresolved)),
                    agreement=agreement, false_inclusion=false_inclusion,
                    false_exclusion=false_exclusion),
        cells={key: int(value) for key, value in counts.items()},
        verdict=("UNRESOLVED" if len(unresolved) else
                 "FALSE_INCLUSION" if false_inclusion else "NO_FALSE_INCLUSION_OBSERVED"),
    )
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}\n      {out.with_suffix('.csv')}")

    # Diagnostic evidence must not be misread as a promotion.  A nonzero false
    # inclusion or unresolved official role is a hard failure for any attempt to
    # replace the research proxy with this base-rule universe.
    return 1 if false_inclusion or len(unresolved) else 0


if __name__ == "__main__":
    raise SystemExit(main())
