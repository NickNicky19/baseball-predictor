#!/usr/bin/env python3
"""Score the hard-keyed *forward* shadow ledger, per market.

This consumes only entries that were written by ``run_shadow_ledger.py`` at or
before their declared decision horizon and later received a linked, official
graded resolution.  It refuses legacy name-keyed comparisons and cannot turn a
retrospective reconstruction into forward evidence.

Capture is mean de-vigged closing movement in the recorded selected side divided
by the model's mean claimed edge at the recorded entry quote.  Results are
separated by sportsbook, category, model version, and frozen selection policy;
no aggregate can conceal a losing market or mixed policy.  The report is
SHADOW ONLY even when an interval clears the supplied bar: promotion requires
the wider replication and coverage evidence, not this command.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.evaluation.shadow_ledger import ForwardShadowLedger, ShadowLedgerError, fair_over_probability


def _fail(message: str) -> None:
    print(f"FATAL: {message}", file=sys.stderr)
    raise SystemExit(2)


def _date_block_bootstrap_ratio(
    numerator: np.ndarray,
    denominator: np.ndarray,
    dates: np.ndarray,
    b: int,
    seed: int,
) -> tuple[float, float]:
    unique, inverse = np.unique(dates, return_inverse=True)
    if len(unique) < 2:
        raise ValueError("at least two distinct game dates are needed for a date-block interval")
    sums_num = np.zeros(len(unique))
    sums_den = np.zeros(len(unique))
    counts = np.zeros(len(unique))
    np.add.at(sums_num, inverse, numerator)
    np.add.at(sums_den, inverse, denominator)
    np.add.at(counts, inverse, 1.0)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(unique), size=(b, len(unique)))
    mean_num = sums_num[draws].sum(axis=1) / counts[draws].sum(axis=1)
    mean_den = sums_den[draws].sum(axis=1) / counts[draws].sum(axis=1)
    if (mean_den <= 0).any():
        raise ValueError("bootstrap produced a non-positive mean claimed edge")
    values = mean_num / mean_den
    return float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))


def _score_rows(pairs: list[dict]) -> pd.DataFrame:
    rows: list[dict] = []
    for pair in pairs:
        try:
            side = str(pair["selection_side"])
            entry_over = fair_over_probability(
                int(pair["entry_over_odds_american"]), int(pair["entry_under_odds_american"])
            )
            close_over = fair_over_probability(
                int(pair["resolution_close_over_odds_american"]),
                int(pair["resolution_close_under_odds_american"]),
            )
            model_over = float(pair["model_p_over"])
        except (KeyError, TypeError, ValueError, ShadowLedgerError) as exc:
            _fail(f"invalid verified ledger pair {pair.get('entry_id', '<unknown>')}: {exc}")
        model_side = model_over if side == "over" else 1.0 - model_over
        entry_side = entry_over if side == "over" else 1.0 - entry_over
        close_side = close_over if side == "over" else 1.0 - close_over
        claimed = model_side - entry_side
        if claimed <= 0:
            _fail(f"ledger pair {pair['entry_id']} has non-positive selected claimed edge")
        rows.append({
            "entry_id": pair["entry_id"],
            "game_date": pair["game_date"],
            "sportsbook": pair["sportsbook"],
            "category": pair["category"],
            "line": float(pair["line"]),
            "model_version": pair["model_version"],
            "selection_policy_id": pair["selection_policy_id"],
            "selection_policy_sha256": pair["selection_policy_sha256"],
            "model_p_selected_side": model_side,
            "entry_fair_p_selected_side": entry_side,
            "close_fair_p_selected_side": close_side,
            "claimed_edge": claimed,
            "realized_clv": close_side - entry_side,
        })
    if not rows:
        _fail("ledger has no linked graded forward entries; capture is not yet measurable")
    return pd.DataFrame(rows)


def _summary(
    frame: pd.DataFrame,
    *,
    capture_bar: float,
    b: int,
    seed: int,
    head_hash: str | None,
    row_type: str,
    line: float | None = None,
) -> dict:
    claimed = frame["claimed_edge"].to_numpy(float)
    realized = frame["realized_clv"].to_numpy(float)
    denominator = float(claimed.mean())
    if denominator <= 0:
        _fail("selected shadow rows have non-positive mean claimed edge")
    capture = float(realized.mean() / denominator)
    n_dates = int(frame["game_date"].nunique())
    if n_dates < 2:
        ci_lo = ci_hi = np.nan
        ci_status = "INSUFFICIENT_DATES_FOR_BLOCK_INTERVAL"
        ci_clears_bar = False
    else:
        ci_lo, ci_hi = _date_block_bootstrap_ratio(
            realized, claimed, frame["game_date"].to_numpy(), b, seed
        )
        ci_status = "DATE_BLOCK_BOOTSTRAP"
        ci_clears_bar = bool(ci_lo > capture_bar)
    first = frame.iloc[0]
    return {
        "row_type": row_type,
        "sportsbook": first["sportsbook"],
        "category": first["category"],
        "line": line,
        "model_version": first["model_version"],
        "selection_policy_id": first["selection_policy_id"],
        "selection_policy_sha256": first["selection_policy_sha256"],
        "n_selected_forward_entries": len(frame),
        "n_game_dates": n_dates,
        "mean_claimed_edge": denominator,
        "mean_realized_clv": float(realized.mean()),
        "capture_ratio": capture,
        "capture_ci_lo": ci_lo,
        "capture_ci_hi": ci_hi,
        "capture_ci_status": ci_status,
        "predeclared_capture_bar": capture_bar,
        "capture_ci_lower_exceeds_bar": ci_clears_bar,
        "ledger_head_hash": head_hash,
        "verdict": "SHADOW_ONLY_NOT_PROMOTED",
        "denominator_definition": "mean claimed edge on predeclared selected sides from immutable entry-time de-vigged prices",
        "numerator_definition": "mean closing fair-probability movement in those same recorded selected sides",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-market forward shadow-ledger capture report")
    parser.add_argument("--ledger", default="data/learning/shadow/forward_ledger.jsonl")
    parser.add_argument("--capture-bar", required=True, type=float,
                        help="predeclared capture bar, e.g. 0.10; never fitted here")
    parser.add_argument("--b", type=int, default=4000, help="date-block bootstrap repetitions")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if not np.isfinite(args.capture_bar) or args.capture_bar < 0:
        _fail("capture bar must be finite and non-negative")
    if args.b <= 0:
        _fail("--b must be positive")

    ledger = ForwardShadowLedger(args.ledger)
    try:
        verification = ledger.verify()
        scored = _score_rows(ledger.graded_pairs())
    except ShadowLedgerError as exc:
        _fail(str(exc))

    group_keys = ["sportsbook", "category", "model_version", "selection_policy_id", "selection_policy_sha256"]
    rows: list[dict] = []
    for _, group in scored.groupby(group_keys, sort=True):
        rows.append(_summary(
            group, capture_bar=args.capture_bar, b=args.b, seed=args.seed,
            head_hash=verification.head_hash, row_type="market_capture",
        ))
        for line, line_group in group.groupby("line", sort=True):
            rows.append(_summary(
                line_group, capture_bar=args.capture_bar, b=args.b, seed=args.seed,
                head_hash=verification.head_hash, row_type="line_capture", line=float(line),
            ))

    output = pd.DataFrame(rows)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    output.to_csv(tmp, index=False)
    if not tmp.exists() or tmp.stat().st_size == 0:
        _fail(f"failed to write shadow report {tmp}")
    tmp.replace(target)
    print(f"verified ledger head: {verification.head_hash or 'EMPTY'}")
    print(f"graded forward entries: {len(scored):,}; per-market reports: {len(rows):,}")
    print("Every result remains SHADOW ONLY -- NOT PROMOTED.")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
