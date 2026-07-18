#!/usr/bin/env python3
"""Measure the quote-age distribution without tuning a policy to outcomes.

``max_quote_age`` determines whether an entry price could actually have been
taken at the declared horizon.  The inherited 90-minute cutoff has never been
supported by a reproducible measurement, so this script asks a narrower,
outcome-blind question: what was the distribution of age for the exact
two-sided pregame price pairs at the selected book and entry horizon?

This is NOT an optimizer and cannot promote a cutoff.  It does not read vendor
result values, official actuals, model probabilities, capture, or CLV.  It
reports the current cutoff only as an observation.  Any later policy change
requires a predeclared selection rule and a disjoint validation period.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.market_eligibility import (  # noqa: E402
    assert_no_leakage,
    load_policy,
    parquet_source,
    quote_pairs_sql,
)


# These are reporting bins only—not candidate cutoffs.  They are conventional
# elapsed-time units, declared before reading the data, so a visually useful
# histogram cannot become a post-hoc policy choice.
AGE_BINS_MINUTES = (0, 1, 2, 5, 10, 15, 30, 60, 90, 120, 180, 360, 720, 1440)
QUANTILES = (0.00, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99, 1.00)


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def summarize(scope: str, frame: pd.DataFrame, cutoff: int) -> dict:
    """Summarize factual ages; ``cutoff`` is reported, never selected here."""
    age = frame.entry_age_min.to_numpy(dtype=float)
    if not len(age):
        return dict(scope=scope, quote_pairs=0)
    row = dict(scope=scope, quote_pairs=int(len(age)),
               at_or_below_current_policy_cutoff=int((age <= cutoff).sum()),
               above_current_policy_cutoff=int((age > cutoff).sum()))
    for q in QUANTILES:
        row[f"q{int(q * 100):02d}_min"] = float(np.quantile(age, q))
    return row


def age_histogram(frame: pd.DataFrame) -> pd.DataFrame:
    """Fixed-bin distribution, including a final open-ended interval."""
    age = frame.entry_age_min.to_numpy(dtype=float)
    rows: list[dict] = []
    lower = AGE_BINS_MINUTES[0]
    for upper in AGE_BINS_MINUTES[1:]:
        count = int(((age >= lower) & (age < upper)).sum())
        rows.append(dict(lower_inclusive_min=lower, upper_exclusive_min=upper,
                         label=f"[{lower}, {upper})", quote_pairs=count))
        lower = upper
    rows.append(dict(lower_inclusive_min=lower, upper_exclusive_min=None,
                     label=f"[{lower}, inf)", quote_pairs=int((age >= lower).sum())))
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="data/market/smartstake")
    ap.add_argument("--months", nargs="+", required=True,
                    help="Local SmartStake partitions, e.g. 2026-03 2026-04")
    ap.add_argument("--policy", default="config/ab_policy.json")
    ap.add_argument("--out", default="reports/market_policy/quote_age_audit.json")
    args = ap.parse_args(argv)

    missing = [m for m in args.months
               if not list(Path(args.root).glob(f"mon={m}/*.parquet"))]
    if missing:
        raise FileNotFoundError(f"missing local market partitions: {missing}")

    values, policy_sha, research_only, unapproved = load_policy(args.policy)
    pairs = duckdb.sql(quote_pairs_sql(
        parquet_source(args.root, args.months), values["book"], values["entry_hours"]
    )).df()

    # Sanity range stated before calculation: entry_age is elapsed minutes
    # between the selected quote and the declared horizon, so it cannot be
    # negative.  Negative is ctrl4 leakage, not a low-age observation.
    assert_no_leakage(pairs)
    if pairs.empty:
        raise ValueError("no two-sided pregame quote pairs for the requested scope")
    if "result" in pairs.columns:
        raise AssertionError("vendor result value reached the quote-age audit")

    pairs["market_month"] = pd.to_datetime(pairs.market_date).dt.strftime("%Y-%m")
    cutoff = values["max_quote_age"]
    summaries = [summarize("ALL", pairs, cutoff)]
    for (month, market), group in pairs.groupby(["market_month", "market"], sort=True):
        summaries.append(summarize(f"{month} | {market}", group, cutoff))
    summary = pd.DataFrame(summaries)
    histogram = age_histogram(pairs)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    summary_path = out.with_suffix(".csv")
    histogram_path = out.with_name(out.stem + "_histogram.csv")
    summary.to_csv(summary_path, index=False)
    histogram.to_csv(histogram_path, index=False)

    payload = dict(
        _comment=(
            "Outcome-blind measurement of two-sided pregame quote-pair age. "
            "It reports the current cutoff but does not choose, fit, or promote one."
        ),
        months=args.months,
        book=values["book"],
        entry_hours=values["entry_hours"],
        current_max_quote_age_minutes=cutoff,
        sanity_range="entry_age_min >= 0; negative age is leakage and hard-fails",
        result_value_used=False,
        policy_sha256=policy_sha,
        policy_research_only=research_only,
        policy_unapproved=unapproved,
        script_sha256=sha256(__file__),
        outputs=dict(summary_csv=str(summary_path), summary_sha256=sha256(summary_path),
                     histogram_csv=str(histogram_path), histogram_sha256=sha256(histogram_path)),
        all_scope=summary.iloc[0].to_dict(),
        reporting_bins_minutes=list(AGE_BINS_MINUTES),
        verdict=(
            "MEASUREMENT_ONLY: no quote-age policy is promoted by this artifact; "
            "any candidate boundary requires predeclaration and disjoint validation."
        ),
    )
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    all_row = summary.iloc[0]
    print("QUOTE-AGE POLICY AUDIT — OUTCOME-BLIND")
    print(f"  book={values['book']}  entry horizon=T-{values['entry_hours']}h  "
          f"current cutoff={cutoff} min (OBSERVATION ONLY)")
    print(f"  two-sided pregame quote pairs: {int(all_row.quote_pairs):,}")
    print(f"  age q50/q90/q95/q99/max: {all_row.q50_min:.0f} / {all_row.q90_min:.0f} / "
          f"{all_row.q95_min:.0f} / {all_row.q99_min:.0f} / {all_row.q100_min:.0f} min")
    print(f"  at/below current cutoff: {int(all_row.at_or_below_current_policy_cutoff):,} "
          f"({int(all_row.at_or_below_current_policy_cutoff) / int(all_row.quote_pairs):.1%})")
    print("  verdict: measurement only; no policy or betting status changed")
    print(f"wrote {out}\n      {summary_path}\n      {histogram_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
