#!/usr/bin/env python3
"""Audit the locked DraftKings HR-over-0.5 price contract without outcomes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_over_contract import (  # noqa: E402
    implied_break_even,
    quote_sql,
    raw_implied_probability_movement,
    validate_quotes,
    validate_research_verdict,
)
from src.evaluation.market_eligibility import parquet_source  # noqa: E402


SCHEMA = "draftkings-hr-over-availability-report-v1"
PROTOCOL_SHA256 = "a1cb0bb66f3db5fe398a1ba47969170debe026c0a1a6a9431b8de3e94d51dc3f"
CONTRACT_MODULE_SHA256 = "201643d4ba34a724b3e82dc5e33e83b0ad465ba5200c82189bab7b60de58b8f7"
QUANTILES = (0.0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0)


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def source_snapshot(root: Path, months: list[str]) -> dict[str, Any]:
    records: list[dict[str, str]] = []
    for month in months:
        files = sorted((root / f"mon={month}").glob("*.parquet"))
        if not files:
            raise FileNotFoundError(f"missing SmartStake partition {month}")
        for path in files:
            records.append({
                "path": path.as_posix(),
                "sha256": sha256(path),
            })
    aggregate = hashlib.sha256()
    for record in records:
        aggregate.update(record["path"].encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(record["sha256"].encode("ascii"))
        aggregate.update(b"\n")
    return {
        "files": records,
        "file_count": len(records),
        "aggregate_sha256": aggregate.hexdigest(),
    }


def classify(frame: pd.DataFrame) -> pd.Series:
    missing_entry = frame.entry_quote_time.isna() | frame.entry_decimal_odds.isna()
    missing_close = ~missing_entry & (
        frame.close_quote_time.isna() | frame.close_decimal_odds.isna()
    )
    settlement_absent = ~missing_entry & ~missing_close & ~frame.settlement_present.astype(bool)
    return pd.Series(
        np.select(
            [missing_entry, missing_close, settlement_absent],
            ["missing_t4h_entry", "missing_pregame_close", "vendor_settlement_absent"],
            default="price_path_observed",
        ),
        index=frame.index,
        dtype="object",
    )


def quantiles(values: pd.Series) -> list[float]:
    numeric = pd.to_numeric(values, errors="raise").to_numpy(float)
    return [float(value) for value in np.quantile(numeric, QUANTILES)]


def scope_summary(scope: str, frame: pd.DataFrame) -> dict[str, Any]:
    available = frame[frame.status.eq("price_path_observed")]
    priced = frame[frame.entry_quote_time.notna() & frame.close_quote_time.notna()]
    bracketed = priced[priced.first_post_horizon_time.notna()]
    return {
        "scope": scope,
        "raw_over_05_selections": int(len(frame)),
        "missing_t4h_entry": int(frame.status.eq("missing_t4h_entry").sum()),
        "missing_pregame_close": int(frame.status.eq("missing_pregame_close").sum()),
        "vendor_settlement_absent": int(frame.status.eq("vendor_settlement_absent").sum()),
        "price_path_observed": int(len(available)),
        "mixed_settlement_snapshots": int(frame.mixed_settlement_snapshots.fillna(False).sum()),
        "bracketed_by_vendor_observation": int(len(bracketed)),
        "bracketed_at_same_price": int(bracketed.same_price_after_horizon.fillna(False).sum()),
        "entry_age_minutes_quantiles": quantiles(priced.entry_age_min) if len(priced) else [],
        "entry_decimal_odds_quantiles": quantiles(priced.entry_decimal_odds) if len(priced) else [],
        "close_decimal_odds_quantiles": quantiles(priced.close_decimal_odds) if len(priced) else [],
        "raw_implied_probability_movement_quantiles": (
            quantiles(priced.raw_implied_probability_movement) if len(priced) else []
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--protocol", default="data/analysis/hr_over_contract_v1/protocol.json")
    ap.add_argument("--root", default="data/market/smartstake")
    ap.add_argument("--out-dir", default="data/analysis/hr_over_contract_v1")
    args = ap.parse_args(argv)

    protocol_path = Path(args.protocol)
    if sha256(protocol_path) != PROTOCOL_SHA256:
        raise ValueError("HR-over protocol hash mismatch")
    contract_path = ROOT / "src/evaluation/hr_over_contract.py"
    if sha256(contract_path) != CONTRACT_MODULE_SHA256:
        raise ValueError("HR-over contract module hash mismatch")
    protocol = load_json(protocol_path)
    if protocol.get("status") != "LOCKED_BEFORE_HR_OVER_AUDIT_RESULTS":
        raise ValueError("HR-over protocol was not locked before results")
    scope = protocol["scope"]
    months = [str(value) for value in scope["open_market_months"]]
    if "2026-05" in months or scope.get("may_opened") is not False:
        raise ValueError("May leaked into the HR-over availability audit")
    if scope != {
        "sportsbook": "draftkings",
        "vendor_market": "player home runs",
        "model_category": "home_runs",
        "line": 0.5,
        "selection_side": "over",
        "entry_horizon_hours": 4,
        "open_market_months": ["2026-03", "2026-04", "2026-06"],
        "may_opened": False,
    }:
        raise ValueError("HR-over protocol scope changed")

    root = Path(args.root)
    snapshot = source_snapshot(root, months)
    source = parquet_source(args.root, months)
    frame = duckdb.sql(quote_sql(source, int(scope["entry_horizon_hours"]))).df()
    if frame.empty:
        raise ValueError("no DraftKings HR-over-0.5 selections")
    if frame[["vendor_game_id", "start_time", "player", "line"]].isna().any().any():
        raise ValueError("HR-over raw selection key is null")
    if frame.duplicated(["vendor_game_id", "start_time", "player", "line"]).any():
        raise ValueError("HR-over raw selection key is duplicated")
    if not frame.non_over_rows.eq(0).all():
        raise ValueError("a non-over side entered the locked HR-over product")
    frame["status"] = classify(frame)
    classified = frame.status.value_counts().sum()
    if int(classified) != len(frame):
        raise AssertionError("HR-over statuses are not exhaustive")
    priced = frame[frame.entry_quote_time.notna() & frame.close_quote_time.notna()].copy()
    validate_quotes(priced)
    priced["entry_raw_break_even_probability"] = implied_break_even(priced.entry_decimal_odds)
    priced["close_raw_break_even_probability"] = implied_break_even(priced.close_decimal_odds)
    priced["raw_implied_probability_movement"] = raw_implied_probability_movement(
        priced.entry_decimal_odds, priced.close_decimal_odds
    )
    frame = frame.merge(
        priced[[
            "vendor_game_id", "start_time", "player", "line",
            "entry_raw_break_even_probability", "close_raw_break_even_probability",
            "raw_implied_probability_movement",
        ]],
        on=["vendor_game_id", "start_time", "player", "line"],
        how="left",
        validate="one_to_one",
    )
    frame["market_month"] = pd.to_datetime(frame.market_date).dt.strftime("%Y-%m")
    summaries = [scope_summary("ALL", frame)]
    for month, group in frame.groupby("market_month", sort=True):
        summaries.append(scope_summary(str(month), group))
    summary = pd.DataFrame(summaries)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = out_dir / "hr_over_quote_paths.csv"
    summary_path = out_dir / "hr_over_availability.csv"
    report_path = out_dir / "availability_report.json"
    frame.to_csv(rows_path, index=False)
    summary.to_csv(summary_path, index=False)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "contract_module": {"path": str(contract_path), "sha256": sha256(contract_path)},
        "source_snapshot": snapshot,
        "may_opened": False,
        "betting_authorized": False,
        "probability_label": "RAW_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN",
        "vendor_numeric_result_used": False,
        "official_outcomes_used": False,
        "model_probabilities_used": False,
        "freshness_cutoff_selected": False,
        "all_scope": summaries[0],
        "historical_executability": {
            "verified": False,
            "verdict": "NOT_ESTABLISHED_FROM_VENDOR_OBSERVATIONS",
        },
        "official_draftkings_settlement_rule": {
            "verified": False,
            "effect": "scored HR evaluation remains blocked",
        },
        "verdict": "MARKET_DATA_SUPPORTS_RESEARCH_RECONSTRUCTION_ONLY",
        "verdict_reason": (
            "The exact over-0.5 product has observed T-4h and closing price paths, "
            "but historical executability and the official book settlement rule "
            "remain unverified. No performance or profitability claim was tested."
        ),
        "outputs": {
            "quote_paths": {"path": str(rows_path), "sha256": sha256(rows_path)},
            "summary": {"path": str(summary_path), "sha256": sha256(summary_path)},
        },
    }
    validate_research_verdict(payload)
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    all_scope = summaries[0]
    print("DRAFTKINGS HR OVER 0.5 — OUTCOME-BLIND AVAILABILITY")
    print(f"  open months: {months}; May opened: NO")
    print(f"  raw over-0.5 selections: {all_scope['raw_over_05_selections']:,}")
    print(f"  T-4h entry + close: {int(frame.entry_quote_time.notna().mul(frame.close_quote_time.notna()).sum()):,}")
    print(f"  price path + vendor settlement presence: {all_scope['price_path_observed']:,}")
    print(f"  median entry age: {all_scope['entry_age_minutes_quantiles'][2]:.0f} min")
    print("  implied probability label: RAW / MARGIN UNKNOWN — NEVER DE-VIGGED")
    print("  historical executability: NOT ESTABLISHED")
    print(f"  verdict: {payload['verdict']}; betting authorized: NO")
    print(f"  wrote {report_path}\n        {summary_path}\n        {rows_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
