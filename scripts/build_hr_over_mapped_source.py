#!/usr/bin/env python3
"""Build the locked, outcome-blind DraftKings HR-over-0.5 identity source."""
from __future__ import annotations

import argparse
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

from src.evaluation.hr_over_mapped_source import (  # noqa: E402
    MARKET_KEY,
    OPEN_MONTHS,
    REPORT_SCHEMA,
    assert_duplicate_rows_preserved,
    complete_price_paths,
    load_json,
    map_price_paths,
    month_funnel,
    resolve_input,
    sha256,
    validate_crosswalk_frames,
    validate_protocol,
    validate_quote_source,
)


LOCKED_PROTOCOL_SHA256 = "8b77438159afa5e006257212bcf05c59efa063eadd9056b9b4b850cf8c20cb18"


def _atomic_csv(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, destination)


def _atomic_json(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def _load_crosswalk(
    root: Path,
    name: str,
    spec: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, str]]:
    players_path = resolve_input(root, spec["players"])
    fragments_path = resolve_input(root, spec["fragments"])
    report_path = resolve_input(root, spec["report"])
    report = load_json(report_path)
    players, fragments = validate_crosswalk_frames(
        pd.read_csv(players_path),
        pd.read_csv(fragments_path),
        [str(value) for value in spec["months"]],
        report,
    )
    players["crosswalk_scope"] = name
    fragments["crosswalk_scope"] = name
    evidence = {
        "players": sha256(players_path),
        "fragments": sha256(fragments_path),
        "report": sha256(report_path),
    }
    return players, fragments, report, evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        default="data/analysis/hr_over_contract_v1/mapped_source_protocol_v2.json",
    )
    parser.add_argument("--out-dir", default="data/analysis/hr_over_contract_v1")
    args = parser.parse_args(argv)

    protocol_path = Path(args.protocol)
    if not protocol_path.is_absolute():
        protocol_path = ROOT / protocol_path
    if sha256(protocol_path) != LOCKED_PROTOCOL_SHA256:
        raise ValueError("locked HR-over mapped-source protocol hash mismatch")
    protocol = load_json(protocol_path)
    validate_protocol(protocol)

    inputs = protocol["inputs"]
    availability_path = resolve_input(ROOT, inputs["availability_report"])
    quote_path = resolve_input(ROOT, inputs["quote_paths"])
    availability = load_json(availability_path)
    if availability.get("may_opened") is not False:
        raise ValueError("May entered the parent HR-over availability report")
    if availability.get("betting_authorized") is not False:
        raise ValueError("parent availability report attempted to authorize betting")
    if availability.get("official_outcomes_used") is not False:
        raise ValueError("parent availability report used official outcomes")
    if availability.get("model_probabilities_used") is not False:
        raise ValueError("parent availability report used model probabilities")
    if availability.get("freshness_cutoff_selected") is not False:
        raise ValueError("parent availability report selected a freshness cutoff")
    parent_quote = availability.get("outputs", {}).get("quote_paths", {})
    if parent_quote.get("sha256") != sha256(quote_path):
        raise ValueError("availability report does not bind the supplied quote paths")

    quotes = validate_quote_source(pd.read_csv(quote_path), OPEN_MONTHS)
    raw_count = int(availability.get("all_scope", {}).get("raw_over_05_selections", -1))
    if raw_count != len(quotes):
        raise ValueError(f"availability row count changed: {len(quotes)} != {raw_count}")
    complete = complete_price_paths(quotes)

    mar_apr = _load_crosswalk(ROOT, "2026-03_2026-04", inputs["crosswalk_mar_apr"])
    june = _load_crosswalk(ROOT, "2026-06", inputs["crosswalk_june"])
    players = pd.concat([mar_apr[0], june[0]], ignore_index=True)
    fragments = pd.concat([mar_apr[1], june[1]], ignore_index=True)
    players, fragments = validate_crosswalk_frames(
        players, fragments, OPEN_MONTHS, report=None
    )

    mapped_all = map_price_paths(complete, players, fragments)
    assert_duplicate_rows_preserved(complete, mapped_all)
    funnel_rows = month_funnel(mapped_all)
    funnel = pd.DataFrame(funnel_rows)
    mapped = mapped_all[mapped_all.mapping_status.eq("hard_mapped")].copy()
    unmapped = mapped_all[~mapped_all.mapping_status.eq("hard_mapped")].copy()
    duplicates = mapped[mapped.duplicate_market_key].copy()

    mapped["mlb_game_pk"] = pd.to_numeric(mapped.mlb_game_pk, errors="raise").astype("int64")
    mapped["player_id"] = pd.to_numeric(mapped.player_id, errors="raise").astype("int64")
    if mapped[MARKET_KEY].isna().any().any():
        raise ValueError("mapped HR source has a null MARKET_KEY")
    if "result" in mapped.columns or "actual" in mapped.columns or "sim_p_over" in mapped.columns:
        raise ValueError("truth or model probability entered the HR identity artifact")
    if mapped.official_game_date.astype(str).str.startswith("2026-05").any():
        raise ValueError("May entered the mapped HR artifact")
    if len(mapped) + len(unmapped) != len(complete):
        raise AssertionError("mapped and unmapped HR rows do not reconstruct the complete source")

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    mapped_path = out_dir / "hr_over_mapped_price_source.csv"
    unmapped_path = out_dir / "hr_over_unmapped_price_paths.csv"
    duplicate_path = out_dir / "hr_over_duplicate_market_keys.csv"
    funnel_path = out_dir / "hr_over_mapped_source_funnel.csv"
    report_path = out_dir / "mapped_source_report.json"
    _atomic_csv(mapped, mapped_path)
    _atomic_csv(unmapped, unmapped_path)
    _atomic_csv(duplicates, duplicate_path)
    _atomic_csv(funnel, funnel_path)

    official_dates = sorted(mapped.official_game_date.astype(str).unique().tolist())
    payload: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": {
            "path": str(protocol_path),
            "sha256": sha256(protocol_path),
        },
        "builder": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "mapping_module": {
            "path": str(ROOT / "src/evaluation/hr_over_mapped_source.py"),
            "sha256": sha256(ROOT / "src/evaluation/hr_over_mapped_source.py"),
        },
        "inputs": {
            "availability_report": sha256(availability_path),
            "quote_paths": sha256(quote_path),
            "crosswalk_mar_apr": mar_apr[3],
            "crosswalk_june": june[3],
        },
        "scope": protocol["scope"],
        "may_opened": False,
        "vendor_numeric_result_used": False,
        "official_outcomes_used": False,
        "model_probabilities_used": False,
        "freshness_cutoff_selected": False,
        "historical_executability_verified": False,
        "official_draftkings_settlement_rule_verified": False,
        "probability_label": "RAW_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN",
        "betting_authorized": False,
        "source_rows": int(len(quotes)),
        "complete_price_paths": int(len(complete)),
        "hard_mapped_rows": int(len(mapped)),
        "unmapped_rows": int(len(unmapped)),
        "duplicate_market_key_rows": int(len(duplicates)),
        "duplicate_market_keys": int(duplicates.groupby(MARKET_KEY, dropna=False).ngroups),
        "official_date_universe": official_dates,
        "funnel": funnel_rows,
        "verdict": "HARD_KEYED_HR_RESEARCH_SOURCE_ONLY",
        "verdict_reason": (
            "Open-month one-sided HR-over price paths have canonical identity where "
            "the hard crosswalk resolves them. Unmapped rows and duplicate final "
            "MARKET_KEYs remain explicit. Outcomes, model probabilities, executable-"
            "price proof, settlement verification, performance, and authorization "
            "are outside this artifact."
        ),
        "outputs": {
            "mapped_source": {"path": str(mapped_path), "sha256": sha256(mapped_path)},
            "unmapped_source": {"path": str(unmapped_path), "sha256": sha256(unmapped_path)},
            "duplicate_keys": {"path": str(duplicate_path), "sha256": sha256(duplicate_path)},
            "funnel": {"path": str(funnel_path), "sha256": sha256(funnel_path)},
        },
    }
    _atomic_json(payload, report_path)

    all_scope = funnel.iloc[0]
    print("DRAFTKINGS HR OVER 0.5 — HARD-KEYED, OUTCOME-BLIND SOURCE")
    print(f"  raw selections: {len(quotes):,}")
    print(f"  complete T-4h + close price paths: {len(complete):,}")
    print(f"  hard mapped: {len(mapped):,} ({len(mapped) / len(complete):.1%})")
    print(f"  unmapped: {len(unmapped):,}")
    print(
        f"  duplicate final MARKET_KEYs: {int(all_scope.duplicate_market_keys):,} "
        f"keys / {int(all_scope.duplicate_market_key_rows):,} rows — PRESERVED"
    )
    print(f"  official dates: {len(official_dates)}")
    print("  May opened: NO; outcomes used: NO; model probabilities used: NO")
    print("  freshness cutoff selected: NO; betting authorized: NO")
    print(f"  wrote {report_path}")
    print(f"        {mapped_path}")
    print(f"        {unmapped_path}")
    print(f"        {duplicate_path}")
    print(f"        {funnel_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
