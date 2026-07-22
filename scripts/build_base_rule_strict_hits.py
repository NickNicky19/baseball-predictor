#!/usr/bin/env python3
"""Build a strict June hits research universe using official DK base eligibility.

This is intentionally separate from ``strict_unique_hits``.  The old artifact
uses SmartStake settlement presence; this artifact uses only observable quote
availability plus the published *base* DK rule (official starter and >=1 PA).
Neither claims conditional Early Exit treatment, and the resulting evaluation
remains research-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_strict_unique_hits import attach_canonical_dates  # noqa: E402
from src.evaluation.identity_keys import MARKET_KEY  # noqa: E402
from src.evaluation.hits_policy_source import (  # noqa: E402
    DEFERRED_FRESHNESS_RULE,
    POLICY_SOURCE_KEY,
    POLICY_SOURCE_KIND,
)
from src.evaluation.market_eligibility import (  # noqa: E402
    MARKET_MAP, STATUS_ABSENT, assert_no_leakage, eligibility_sql, fresh_quote_pairs,
    load_policy, parquet_source, policy_source_quote_pairs,
    RAW_DECIMAL_ODDS_FIELDS, RAW_QUOTE_TIMESTAMP_FIELDS,
)
from src.evaluation.official_hitter_eligibility import (  # noqa: E402
    ELIGIBILITY_KEY, apply_base_hitter_rule, assert_official_date_agreement,
    partition_official_role_resolution, validate_eligibility,
)


CONSUMER_KEY = ["vendor_game_id", "start_time", "player_key"]
EVAL_FIELDS = ["entry_p_over", "close_p_over", "entry_overround",
               "entry_over_age_min", "entry_under_age_min", "entry_age_min",
               "settlement_present"]
SELECTION_RULE = "dk_base_pregame_starter_and_pa_v1"
PRICE_FRESHNESS_RULE = "both_entry_sides_fresh_v1"


def sha(path: str | Path) -> str:
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
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--policy", default="config/ab_policy.json")
    ap.add_argument("--crosswalk", default="data/market/v3/crosswalk_players.csv")
    ap.add_argument("--fragments", default="data/market/v3/crosswalk_fragments.csv")
    ap.add_argument("--eligibility", default="data/market/v3/official_hitter_eligibility.csv")
    ap.add_argument("--out", default="data/market/v3/base_rule_strict_hits")
    ap.add_argument("--include-raw-odds", action="store_true",
                    help=("emit exact decimal entry/close odds and quote timestamps "
                          "in a NEW enriched artifact. The default schema remains "
                          "the established research baseline."))
    ap.add_argument(
        "--defer-freshness",
        action="store_true",
        help=(
            "build an uncensored fragment-grain POLICY SOURCE. No max quote age "
            "is applied and duplicate MARKET_KEYs are retained so freshness can "
            "be fitted before duplicate resolution. Requires --include-raw-odds."
        ),
    )
    args = ap.parse_args(argv)

    values, policy_sha, research_only, unapproved = load_policy(args.policy)
    if args.defer_freshness and not args.include_raw_odds:
        raise ValueError("--defer-freshness requires --include-raw-odds")
    if values["base_pregame_hitter_eligibility_rule"] != "official_starter AND official_pa >= 1":
        raise ValueError(
            "This builder knows only the explicitly documented base hitter rule. "
            "A policy change needs a new builder/version, never best-effort parsing."
        )
    if not list(Path(args.root).glob(f"mon={args.months[0]}/*.parquet")):
        raise ValueError(f"no local market parquet for {args.months[0]}")

    raw = duckdb.sql(eligibility_sql(
        parquet_source(args.root, args.months), values["book"],
        values["entry_hours"], values["max_quote_age"],
    )).df()
    assert_no_leakage(raw)
    raw["start_time"] = pd.to_datetime(raw.start_time, utc=True)
    raw["player_key"] = raw.player.map(norm)
    raw["category"] = raw.market.map(MARKET_MAP)

    # Vendor result presence is an OBSERVATION only.  Fresh two-sided price
    # availability is the quote boundary; official starter+PA decides grading.
    selected_quotes = (
        policy_source_quote_pairs(raw)
        if args.defer_freshness
        else fresh_quote_pairs(raw, values["max_quote_age"])
    )
    n_two_sided = len(raw)
    n_vendor_absent = int((raw.status == STATUS_ABSENT).sum())
    n_vendor_present = n_two_sided - n_vendor_absent
    n_selected_quotes = len(selected_quotes)

    xw = pd.read_csv(args.crosswalk)
    xw["start_time"] = pd.to_datetime(xw.start_time, utc=True)
    if xw.duplicated(CONSUMER_KEY).any():
        raise ValueError("crosswalk consumer key is not unique")
    mapped = selected_quotes.merge(
        xw, on=CONSUMER_KEY, how="inner", validate="many_to_one"
    )
    n_unmapped = n_selected_quotes - len(mapped)
    mapped = attach_canonical_dates(mapped, args.fragments)

    bridge = pd.read_csv(args.eligibility)
    validate_eligibility(bridge)
    bridge = bridge[[
        *ELIGIBILITY_KEY,
        "official_game_date",
        "is_starter",
        "official_lineup_slot",
        "starter_replaced_in_slot",
        "official_pa",
    ]]
    mapped = mapped.merge(bridge, on=ELIGIBILITY_KEY, how="left", validate="many_to_one",
                          suffixes=("", "_bridge"))
    role_resolved, role_unresolved = partition_official_role_resolution(mapped)
    try:
        assert_official_date_agreement(role_resolved)
    except ValueError as exc:
        raise ValueError(
            "crosswalk canonical date disagrees with official eligibility bridge:\n"
            f"{role_resolved[[*MARKET_KEY, 'official_game_date', 'official_game_date_bridge']].head(20).to_string(index=False)}"
        ) from exc
    role_resolved = role_resolved.drop(columns="official_game_date_bridge")
    role_unresolved = role_unresolved.drop(columns="official_game_date_bridge")
    role_resolved["base_rule_eligible"] = apply_base_hitter_rule(role_resolved)
    voided = role_resolved[~role_resolved.base_rule_eligible].copy()
    base = role_resolved[role_resolved.base_rule_eligible].copy()

    dup_mask = base.duplicated(MARKET_KEY, keep=False)
    duplicate_observed = base[dup_mask].copy()
    if args.defer_freshness:
        excluded = base.iloc[0:0].copy()
        artifact_frame = base.copy()
        if artifact_frame[POLICY_SOURCE_KEY].isna().any().any():
            raise ValueError(
                "uncensored policy source has a null fragment-grain source key"
            )
        duplicate_source = artifact_frame.duplicated(POLICY_SOURCE_KEY, keep=False)
        if duplicate_source.any():
            evidence = [
                *POLICY_SOURCE_KEY, "player", *EVAL_FIELDS,
                *RAW_QUOTE_TIMESTAMP_FIELDS, *RAW_DECIMAL_ODDS_FIELDS,
            ]
            raise ValueError(
                "uncensored policy source has "
                f"{int(duplicate_source.sum())} rows on duplicate fragment-grain "
                "source keys; no row was selected or discarded:\n"
                f"{artifact_frame.loc[duplicate_source, evidence].head(40).to_string(index=False)}"
            )
    else:
        excluded = duplicate_observed.copy()
        artifact_frame = base[~dup_mask].copy()
        if (artifact_frame.duplicated(MARKET_KEY).any()
                or artifact_frame[MARKET_KEY].isna().any().any()):
            raise ValueError("base-rule strict artifact violates MARKET_KEY identity")

    print("=" * 96)
    print(
        "BASE-RULE HITS POLICY SOURCE — RESEARCH ONLY"
        if args.defer_freshness else
        "BASE-RULE STRICT HITS — RESEARCH ONLY"
    )
    print("=" * 96)
    if args.defer_freshness:
        print("  Price boundary: two-sided and pregame; freshness is DEFERRED to fitting.")
        print("  Duplicate MARKET_KEYs are retained until each candidate cutoff is applied.")
    else:
        print("  Price boundary: two-sided, pregame, fresh at the policy T-horizon.")
    print("  Grading boundary: official starter AND official PA >= 1.")
    print("  SmartStake result null is reported, never used to admit or grade a row.")
    print("  Conditional Early Exit is NOT inferred from PA and is not represented.")
    print()
    print(f"  two-sided quote pairs          {n_two_sided:7,d}")
    print(f"    vendor settlement present    {n_vendor_present:7,d}  (observation only)")
    print(f"    vendor settlement absent     {n_vendor_absent:7,d}  (observation only)")
    selected_label = "uncensored quote pairs" if args.defer_freshness else "fresh quote pairs"
    print(f"  {selected_label:29s} {n_selected_quotes:7,d}")
    print(f"    crosswalk unmapped           {n_unmapped:7,d}")
    print(f"  hard-mapped                     {len(mapped):7,d}")
    print(f"    official role unresolved      {len(role_unresolved):7,d}  (not called void or graded)")
    print(f"    official role resolved        {len(role_resolved):7,d}")
    print(f"    base-rule void               {len(voided):7,d}")
    print(f"  base-rule gradeable            {len(base):7,d}")
    if args.defer_freshness:
        print(f"    duplicate MARKET_KEY observed {len(duplicate_observed):7,d}  (retained)")
        print(f"  POLICY SOURCE                  {len(artifact_frame):7,d}")
    else:
        print(f"    duplicate MARKET_KEY         {len(excluded):7,d}  (both rows excluded)")
        print(f"  STRICT                         {len(artifact_frame):7,d}")

    by_date_data = {
        "hard_mapped": mapped.groupby("official_game_date").size(),
        "official_role_unresolved": role_unresolved.groupby("official_game_date").size() if len(role_unresolved) else 0,
        "official_role_resolved": role_resolved.groupby("official_game_date").size(),
        "base_rule_void": voided.groupby("official_game_date").size(),
        "base_rule_gradeable": base.groupby("official_game_date").size(),
    }
    if args.defer_freshness:
        by_date_data.update({
            "duplicate_observed": (
                duplicate_observed.groupby("official_game_date").size()
                if len(duplicate_observed) else 0
            ),
            "policy_source": artifact_frame.groupby("official_game_date").size(),
        })
    else:
        by_date_data.update({
            "duplicate_excluded": (
                excluded.groupby("official_game_date").size() if len(excluded) else 0
            ),
            "strict": artifact_frame.groupby("official_game_date").size(),
        })
    by_date = pd.DataFrame(by_date_data).fillna(0).astype(int)
    print("\nBY CANONICAL OFFICIAL DATE")
    print(by_date.to_string())

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw_evidence_fields = [*RAW_QUOTE_TIMESTAMP_FIELDS, *RAW_DECIMAL_ODDS_FIELDS]
    emitted_eval_fields = [*EVAL_FIELDS,
                           *(raw_evidence_fields if args.include_raw_odds else [])]
    columns = [*MARKET_KEY, "vendor_game_id", "start_time", "player", "player_key",
               "market_date", "official_game_date", *emitted_eval_fields,
               "is_starter", "official_lineup_slot", "starter_replaced_in_slot",
               "official_pa", "base_rule_eligible"]
    artifact_frame[columns].to_csv(out.with_suffix(".csv"), index=False)
    if len(role_unresolved):
        role_unresolved["base_rule_eligible"] = pd.NA
        role_unresolved[columns].to_csv(
            out.with_name(out.name + "_official_role_unresolved.csv"), index=False
        )
    voided[columns].to_csv(out.with_name(out.name + "_base_rule_void.csv"), index=False)
    if len(excluded):
        excluded[columns].to_csv(out.with_name(out.name + "_excluded.csv"), index=False)
    if args.defer_freshness and len(duplicate_observed):
        duplicate_observed[columns].to_csv(
            out.with_name(out.name + "_duplicate_market_keys_observed.csv"),
            index=False,
        )
    by_date.to_csv(out.with_name(out.name + "_by_date.csv"))

    artifact = out.with_suffix(".csv")
    manifest = dict(
        _comment=(
            "Research-only uncensored hits policy source. It retains every "
            "nonnegative-age two-sided quote and duplicate MARKET_KEY until "
            "freshness is fitted; no inherited freshness cutoff is applied."
            if args.defer_freshness else
            "Research-only strict hits universe. It uses fresh two-sided price "
            "availability and DraftKings' published BASE pregame hitter rule "
            "(official starter plus >=1 PA), not SmartStake result settlement. "
            "Conditional Early Exit eligibility is intentionally not inferred."
        ),
        artifact_kind=(
            POLICY_SOURCE_KIND
            if args.defer_freshness else "strict_market_universe_v1"
        ),
        built_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        selection_rule=SELECTION_RULE,
        starter_role_source="completed_game_per_player_batting_order_sequence_v1",
        side_specific_early_exit_facts_included=True,
        price_freshness_rule=(
            DEFERRED_FRESHNESS_RULE
            if args.defer_freshness else PRICE_FRESHNESS_RULE
        ),
        months=args.months,
        book=values["book"], entry_hours=values["entry_hours"],
        max_quote_age=(None if args.defer_freshness else values["max_quote_age"]),
        current_policy_max_quote_age_observation=values["max_quote_age"],
        markets=list(MARKET_MAP.values()),
        raw_decimal_odds_included=bool(args.include_raw_odds),
        source_key=(POLICY_SOURCE_KEY if args.defer_freshness else MARKET_KEY),
        hashes=dict(code=sha(__file__), policy=policy_sha, crosswalk=sha(args.crosswalk),
                    fragments=sha(args.fragments), eligibility=sha(args.eligibility),
                    artifact=sha(artifact)),
        funnel=dict(
            two_sided_quote_pairs=n_two_sided,
            vendor_settlement_present=n_vendor_present,
            vendor_settlement_absent=n_vendor_absent,
            selected_quote_pairs=n_selected_quotes,
            freshness_applied=not args.defer_freshness,
            crosswalk_unmapped=n_unmapped,
            hard_mapped=int(len(mapped)),
            official_role_unresolved=int(len(role_unresolved)),
            official_role_resolved=int(len(role_resolved)),
            base_rule_void=int(len(voided)),
            base_rule_gradeable=int(len(base)),
            duplicate_rows_observed=int(len(duplicate_observed)),
            duplicate_keys_observed=(
                int(duplicate_observed.groupby(MARKET_KEY).ngroups)
                if len(duplicate_observed) else 0
            ),
            duplicate_rows_excluded=int(len(excluded)),
            duplicate_keys_excluded=(
                int(excluded.groupby(MARKET_KEY).ngroups) if len(excluded) else 0
            ),
            artifact_rows=int(len(artifact_frame)),
        ),
        official_date_universe=sorted(
            artifact_frame.official_game_date.unique().tolist()
        ),
        policy_research_only=research_only,
        policy_unapproved=unapproved,
    )
    if args.defer_freshness:
        manifest["funnel"]["policy_source_rows"] = int(len(artifact_frame))
    else:
        # Preserve the established strict-artifact manifest contract exactly;
        # the generic fields above are additive, never replacements.
        manifest["funnel"]["fresh_quote_pairs"] = int(n_selected_quotes)
        manifest["funnel"]["strict_unique"] = int(len(artifact_frame))
    manifest_path = out.with_name(out.name + "_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {artifact} ({len(artifact_frame):,} rows) sha {manifest['hashes']['artifact']}")
    print(f"      {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
