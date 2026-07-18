#!/usr/bin/env python3
"""Build the strict-unique, uncensored HR-over reconstruction source."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_over_mapped_source import MARKET_KEY, load_json, resolve_input, sha256  # noqa: E402
from src.evaluation.hr_over_reconstruct_source import (  # noqa: E402
    strict_unique,
    validate_protocol,
)
from src.evaluation.strict_market_artifact import (  # noqa: E402
    HR_OVER_05_COMPLETE_PATH_RULE,
    ONE_SIDED_FRESHNESS_DEFERRED_RULE,
)


LOCKED_PROTOCOL_SHA256 = "e5c093e9c484b74431065aae0f885a6bd898f85216e973cc0add8e254bbf40a0"


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_name(path.name + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(payload: dict[str, Any], path: Path) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        default="data/analysis/hr_over_contract_v1/reconstruct_source_protocol.json",
    )
    parser.add_argument(
        "--out",
        default="data/analysis/hr_over_contract_v1/hr_over_reconstruct_source",
    )
    args = parser.parse_args(argv)

    protocol_path = Path(args.protocol)
    if not protocol_path.is_absolute():
        protocol_path = ROOT / protocol_path
    if sha256(protocol_path) != LOCKED_PROTOCOL_SHA256:
        raise ValueError("locked HR reconstruction-source protocol hash mismatch")
    protocol = load_json(protocol_path)
    validate_protocol(protocol)

    parent_report_path = resolve_input(ROOT, protocol["inputs"]["mapped_source_report"])
    parent_source_path = resolve_input(ROOT, protocol["inputs"]["mapped_source"])
    parent = load_json(parent_report_path)
    if parent.get("betting_authorized") is not False:
        raise ValueError("parent HR mapping artifact attempted authorization")
    if parent.get("may_opened") is not False:
        raise ValueError("parent HR mapping artifact opened May")
    bound_source = parent.get("outputs", {}).get("mapped_source", {})
    if bound_source.get("sha256") != sha256(parent_source_path):
        raise ValueError("parent mapping report does not bind the supplied mapped source")

    source = pd.read_csv(parent_source_path)
    unique, excluded = strict_unique(source)
    if len(unique) + len(excluded) != int(parent.get("hard_mapped_rows", -1)):
        raise ValueError("strict HR source does not account for every parent hard-mapped row")

    out_stem = Path(args.out)
    if not out_stem.is_absolute():
        out_stem = ROOT / out_stem
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    artifact_path = out_stem.with_suffix(".csv")
    excluded_path = out_stem.with_name(out_stem.name + "_excluded.csv")
    by_date_path = out_stem.with_name(out_stem.name + "_by_date.csv")
    manifest_path = out_stem.with_name(out_stem.name + "_manifest.json")

    by_date = pd.DataFrame({
        "hard_mapped": source.groupby("official_game_date").size(),
        "duplicate_rows_excluded": excluded.groupby("official_game_date").size(),
        "strict_unique": unique.groupby("official_game_date").size(),
    }).fillna(0).astype(int)
    by_date["strict_unique_pct"] = (
        by_date.strict_unique / by_date.hard_mapped
    ).round(6)

    atomic_csv(unique, artifact_path)
    atomic_csv(excluded, excluded_path)
    atomic_csv(by_date.reset_index(), by_date_path)
    artifact_hash = sha256(artifact_path)
    dates = sorted(unique.official_game_date.astype(str).unique().tolist())
    duplicate_keys = int(excluded.groupby(MARKET_KEY, dropna=False).ngroups)
    manifest: dict[str, Any] = {
        "schema_version": "draftkings-hr-over-reconstruct-source-manifest-v1",
        "artifact_kind": "strict_unique_reconstruction_only",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "parent": {
            "mapped_source_report": {
                "path": str(parent_report_path),
                "sha256": sha256(parent_report_path),
            },
            "mapped_source": {
                "path": str(parent_source_path),
                "sha256": sha256(parent_source_path),
            },
        },
        "builder": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "module": {
            "path": str(ROOT / "src/evaluation/hr_over_reconstruct_source.py"),
            "sha256": sha256(ROOT / "src/evaluation/hr_over_reconstruct_source.py"),
        },
        "markets": ["home_runs"],
        "selection_rule": HR_OVER_05_COMPLETE_PATH_RULE,
        "price_freshness_rule": ONE_SIDED_FRESHNESS_DEFERRED_RULE,
        "max_quote_age": None,
        "settlement_presence_filter_applied": False,
        "raw_decimal_odds_included": True,
        "official_date_universe": dates,
        "funnel": {
            "hard_mapped": int(len(source)),
            "duplicate_rows_excluded": int(len(excluded)),
            "duplicate_keys_excluded": duplicate_keys,
            "strict_unique": int(len(unique)),
            "strict_unique_pct_of_hard_mapped": round(len(unique) / len(source), 6),
            "old_unique_quotes_retained": int((pd.to_numeric(unique.entry_age_min) > 90).sum()),
            "vendor_settlement_absent_unique_rows_retained": int(
                (~unique.settlement_present.astype(bool)).sum()
            ),
        },
        "hashes": {
            "artifact": artifact_hash,
            "excluded": sha256(excluded_path),
            "by_date": sha256(by_date_path),
            "protocol": sha256(protocol_path),
            "parent_report": sha256(parent_report_path),
            "parent_source": sha256(parent_source_path),
        },
        "may_opened": False,
        "official_outcomes_used": False,
        "model_probabilities_used": False,
        "freshness_cutoff_selected": False,
        "official_draftkings_settlement_rule_verified": False,
        "betting_authorized": False,
        "verdict": "VALID_FOR_MODEL_RECONSTRUCTION_ONLY",
        "verdict_reason": (
            "Every ambiguous final MARKET_KEY was removed in full; all unique "
            "hard-mapped one-sided HR price paths remain, including old and vendor-"
            "settlement-absent rows. This defines only the identity/date universe "
            "for model reconstruction. It is not a scoring or betting universe."
        ),
    }
    atomic_json(manifest, manifest_path)

    print("HR OVER 0.5 — STRICT-UNIQUE RECONSTRUCTION SOURCE")
    print(f"  hard mapped: {len(source):,}")
    print(f"  duplicate ambiguity excluded: {len(excluded):,} rows / {duplicate_keys:,} keys (ALL rows)")
    print(f"  strict unique: {len(unique):,} ({len(unique) / len(source):.1%})")
    print(f"  old unique quotes retained (>90 min): {manifest['funnel']['old_unique_quotes_retained']:,}")
    print(
        "  vendor-settlement-absent unique rows retained: "
        f"{manifest['funnel']['vendor_settlement_absent_unique_rows_retained']:,}"
    )
    print(f"  official dates: {len(dates)}; May opened: NO")
    print("  verdict: VALID FOR MODEL RECONSTRUCTION ONLY; betting authorized: NO")
    print(f"  wrote {artifact_path}")
    print(f"        {excluded_path}")
    print(f"        {by_date_path}")
    print(f"        {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
