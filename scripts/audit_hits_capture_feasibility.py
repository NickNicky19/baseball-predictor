#!/usr/bin/env python3
"""Audit whether open-period Hits information predicts CLV and positive ROI.

This diagnostic uses only the already-open March-April fit period.  It does
not read May, change the model or policy, prove historical executability, or
authorize betting.  A selector-only linear market-movement model is evaluated
unchanged on the later spent confirmation dates.
"""
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

from src.evaluation.hits_policy_fit import arm_policy_rows, build_policy_pairs  # noqa: E402


SCHEMA = "hits-capture-feasibility-report-v1"
EXECUTABILITY_VERDICT = "HISTORICAL_EXECUTABILITY_NOT_ESTABLISHED"
FEATURES = (
    "expected_profit",
    "market_edge",
    "entry_age_min",
    "entry_overround",
    "line_is_1_5",
    "side_is_over",
)
AGE_EDGES = (-np.inf, 15.0, 30.0, 60.0, 90.0, 120.0, 180.0, 360.0, 720.0, np.inf)
BOOTSTRAP_DRAWS = 20_000
SEED = 17


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def verify_hash(path: str | Path, expected: str, label: str) -> Path:
    resolved = Path(path)
    if not resolved.is_file() or sha256(resolved) != str(expected).lower():
        raise ValueError(f"{label} is missing or hash-mismatched")
    return resolved


def verify_dates(
    observed: pd.Series,
    selector_dates: list[str],
    confirmation_dates: list[str],
) -> None:
    selector = [str(value) for value in selector_dates]
    confirmation = [str(value) for value in confirmation_dates]
    if (
        selector != sorted(set(selector))
        or confirmation != sorted(set(confirmation))
        or set(selector) & set(confirmation)
        or not selector
        or not confirmation
        or max(selector) >= min(confirmation)
    ):
        raise ValueError("selector/confirmation chronology is not exact and forward")
    declared = set(selector) | set(confirmation)
    if any(value >= "2026-05-01" for value in declared):
        raise ValueError("May leaked into the feasibility audit")
    actual = set(pd.Series(observed).astype(str).unique())
    if actual != declared:
        raise ValueError(
            "feasibility dates differ from the exact declared open universe: "
            f"missing={sorted(declared - actual)} extra={sorted(actual - declared)}"
        )


def _selector_edges(values: pd.Series, label: str) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="raise").to_numpy(float)
    edges = np.quantile(numeric, [0.0, 0.25, 0.5, 0.75, 1.0])
    if len(np.unique(edges)) != len(edges):
        raise ValueError(f"selector {label} quartiles are not distinct")
    edges[0], edges[-1] = -np.inf, np.inf
    return edges


def _apply_bins(values: pd.Series, edges: np.ndarray, prefix: str) -> pd.Series:
    labels = [f"{prefix}{index + 1}" for index in range(len(edges) - 1)]
    out = pd.cut(values, bins=edges, labels=labels, include_lowest=True, right=True)
    if out.isna().any():
        raise ValueError(f"{prefix} bins did not classify every row")
    return out.astype(str)


def _economic_rows(pairs: pd.DataFrame) -> pd.DataFrame:
    policy = arm_policy_rows(pairs, "candidate")
    out = pairs.copy()
    for column in policy.columns:
        out[column] = policy[column].to_numpy()
    out["line_is_1_5"] = np.isclose(out.line.to_numpy(float), 1.5).astype(float)
    out["side_is_over"] = out.selection_side.eq("over").astype(float)
    if not set(out.selection_side.unique()) <= {"over", "under"}:
        raise ValueError("candidate produced an unknown side")
    return out


def fit_selector_ols(
    selector: pd.DataFrame,
    confirmation: pd.DataFrame,
    selector_dates: list[str],
    confirmation_dates: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    """Fit on exact selector dates and return unchanged confirmation predictions."""
    if set(selector.official_game_date.astype(str).unique()) != set(selector_dates):
        raise ValueError("OLS training rows are not the exact selector dates")
    if set(confirmation.official_game_date.astype(str).unique()) != set(confirmation_dates):
        raise ValueError("OLS evaluation rows are not the exact confirmation dates")
    if max(selector_dates) >= min(confirmation_dates):
        raise ValueError("OLS chronology is not forward")
    if "close_p_over" in FEATURES or "actual_value" in FEATURES or "clv" in FEATURES:
        raise AssertionError("post-entry information reached the T-4h feature list")

    x_train = selector.loc[:, FEATURES].to_numpy(float)
    x_confirm = confirmation.loc[:, FEATURES].to_numpy(float)
    mean = x_train.mean(axis=0)
    scale = x_train.std(axis=0, ddof=0)
    if (~np.isfinite(x_train)).any() or (~np.isfinite(x_confirm)).any():
        raise ValueError("non-finite T-4h feature")
    if (scale <= 0.0).any() or (~np.isfinite(scale)).any():
        raise ValueError("zero-variance or invalid selector feature")
    train_design = np.column_stack([np.ones(len(x_train)), (x_train - mean) / scale])
    confirm_design = np.column_stack([np.ones(len(x_confirm)), (x_confirm - mean) / scale])
    target = selector.clv.to_numpy(float)
    coefficients, _, rank, _ = np.linalg.lstsq(train_design, target, rcond=None)
    if rank != train_design.shape[1]:
        raise ValueError("T-4h feasibility design is rank-deficient")
    return coefficients, confirm_design @ coefficients, mean, scale, int(rank)


def selected_by_feasibility(frame: pd.DataFrame, predicted_clv: np.ndarray) -> pd.DataFrame:
    if len(frame) != len(predicted_clv):
        raise ValueError("predicted CLV length differs from evaluation rows")
    mask = frame.expected_profit.to_numpy(float) >= 0.0
    mask &= np.asarray(predicted_clv, dtype=float) > 0.0
    out = frame.loc[mask].copy()
    out["predicted_clv"] = np.asarray(predicted_clv, dtype=float)[mask]
    return out


def _metrics(frame: pd.DataFrame) -> dict[str, Any]:
    denominator = float(frame.market_edge.sum())
    return {
        "rows": int(len(frame)),
        "dates": int(frame.official_game_date.nunique()),
        "mean_market_edge": float(frame.market_edge.mean()) if len(frame) else None,
        "mean_directional_clv": float(frame.clv.mean()) if len(frame) else None,
        "capture": float(frame.clv.sum() / denominator) if denominator > 0.0 else None,
        "flat_stake_roi": float(frame.realised_profit.mean()) if len(frame) else None,
        "positive_clv_rate": float(frame.clv.gt(0.0).mean()) if len(frame) else None,
        "mean_expected_profit": float(frame.expected_profit.mean()) if len(frame) else None,
    }


def block_intervals(frame: pd.DataFrame, declared_dates: list[str]) -> dict[str, Any]:
    observed = set(frame.official_game_date.astype(str).unique())
    if observed != set(declared_dates):
        raise ValueError(
            "selected feasibility rows do not cover every declared confirmation date"
        )
    totals = frame.groupby("official_game_date").agg(
        edge=("market_edge", "sum"),
        clv=("clv", "sum"),
        profit=("realised_profit", "sum"),
        count=("realised_profit", "size"),
    ).reindex(declared_dates)
    if totals.isna().any().any():
        raise ValueError("confirmation date totals are incomplete")
    rng = np.random.default_rng(SEED)
    weights = rng.multinomial(
        len(declared_dates),
        np.full(len(declared_dates), 1.0 / len(declared_dates)),
        size=BOOTSTRAP_DRAWS,
    )
    capture_den = weights @ totals.edge.to_numpy(float)
    roi_den = weights @ totals["count"].to_numpy(float)
    valid = (capture_den > 0.0) & (roi_den > 0.0)
    capture = (weights[valid] @ totals.clv.to_numpy(float)) / capture_den[valid]
    roi = (weights[valid] @ totals.profit.to_numpy(float)) / roi_den[valid]
    return {
        "draws_requested": BOOTSTRAP_DRAWS,
        "valid_draws": int(valid.sum()),
        "capture_95": [float(np.percentile(capture, 2.5)), float(np.percentile(capture, 97.5))],
        "flat_stake_roi_95": [float(np.percentile(roi, 2.5)), float(np.percentile(roi, 97.5))],
    }


def grouped_summary(frame: pd.DataFrame, role: str) -> pd.DataFrame:
    positive = frame[frame.expected_profit >= 0.0]
    records: list[dict[str, Any]] = []
    dimensions = {
        "line": positive.line.map(lambda value: f"{float(value):g}"),
        "side": positive.selection_side,
        "quote_age_band": positive.quote_age_band,
        "edge_quartile": positive.edge_quartile,
        "overround_quartile": positive.overround_quartile,
    }
    for dimension, values in dimensions.items():
        for value in sorted(values.unique()):
            group = positive[values.eq(value)]
            records.append({"role": role, "dimension": dimension, "value": value, **_metrics(group)})
        if sum(row["rows"] for row in records if row["role"] == role and row["dimension"] == dimension) != len(positive):
            raise AssertionError(f"{role}/{dimension} groups do not sum")
    return pd.DataFrame(records)


def quote_continuity(source: pd.DataFrame, root: Path) -> tuple[dict[str, Any], pd.DataFrame]:
    """Measure vendor observations bracketing T-4h; never infer a real fill."""
    keys = source[["vendor_game_id", "start_time", "player", "line"]].copy()
    keys["start_time"] = pd.to_datetime(keys.start_time, utc=True).dt.tz_localize(None)
    con = duckdb.connect()
    con.register("policy_keys", keys)
    paths = [str(root / f"mon={month}" / "*.parquet") for month in ("2026-03", "2026-04")]
    sql = """
    WITH raw AS (
      SELECT r.game_id, r.start_time, r.player, r.line, lower(r.side) side,
             r.ts, r.odds, r.start_time - INTERVAL 4 HOUR horizon
      FROM read_parquet(?, hive_partitioning=true) r
      JOIN policy_keys k ON r.game_id=k.vendor_game_id
        AND r.start_time=k.start_time AND r.player=k.player AND r.line=k.line
      WHERE lower(r.book)='draftkings' AND lower(r.market)='player hits'
        AND lower(r.side) IN ('over','under') AND r.odds>1 AND r.ts<r.start_time
    ), side_obs AS (
      SELECT game_id,start_time,player,line,side,horizon,
        max(ts) FILTER(WHERE ts<=horizon) pre_ts,
        arg_max(odds,ts) FILTER(WHERE ts<=horizon) pre_odds,
        min(ts) FILTER(WHERE ts>horizon) post_ts,
        arg_min(odds,ts) FILTER(WHERE ts>horizon) post_odds
      FROM raw GROUP BY ALL
    )
    SELECT game_id AS vendor_game_id,start_time,player,line,horizon,
      max(CASE WHEN side='over' THEN date_diff('second',pre_ts,horizon)/60.0 END) over_pre_gap,
      max(CASE WHEN side='under' THEN date_diff('second',pre_ts,horizon)/60.0 END) under_pre_gap,
      max(CASE WHEN side='over' THEN date_diff('second',horizon,post_ts)/60.0 END) over_post_gap,
      max(CASE WHEN side='under' THEN date_diff('second',horizon,post_ts)/60.0 END) under_post_gap,
      max(CASE WHEN side='over' THEN pre_odds=post_odds END) over_same_price,
      max(CASE WHEN side='under' THEN pre_odds=post_odds END) under_same_price,
      count(*) FILTER(WHERE pre_ts IS NOT NULL) pre_sides
    FROM side_obs GROUP BY game_id,start_time,player,line,horizon
    """
    observations = con.execute(sql, [paths]).fetchdf()
    if len(observations) != len(source):
        raise ValueError("quote-continuity rows differ from exact policy-source rows")
    if not observations.pre_sides.eq(2).all():
        raise ValueError("a policy-source row lacks a pre-horizon side observation")
    bracketed = observations.over_post_gap.notna() & observations.under_post_gap.notna()
    same = bracketed & observations.over_same_price.fillna(False) & observations.under_same_price.fillna(False)
    pre_gap = observations[["over_pre_gap", "under_pre_gap"]].max(axis=1)
    post_gap = observations[["over_post_gap", "under_post_gap"]].max(axis=1)
    width = observations[["over_pre_gap", "under_pre_gap"]].add(
        observations[["over_post_gap", "under_post_gap"]].to_numpy()
    ).max(axis=1)
    quantiles = [0.0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0]
    summary = {
        "exact_policy_source_rows": int(len(observations)),
        "both_sides_observed_before_t4h": int(observations.pre_sides.eq(2).sum()),
        "both_sides_observed_after_t4h": int(bracketed.sum()),
        "both_sides_bracket_t4h_at_same_prices": int(same.sum()),
        "both_sides_bracket_t4h_with_any_price_change": int((bracketed & ~same).sum()),
        "pre_horizon_gap_minutes_quantiles": [float(value) for value in np.quantile(pre_gap, quantiles)],
        "post_horizon_gap_minutes_quantiles_bracketed": [float(value) for value in np.quantile(post_gap[bracketed], quantiles)],
        "bracket_width_minutes_quantiles_bracketed": [float(value) for value in np.quantile(width[bracketed], quantiles)],
        "verdict": EXECUTABILITY_VERDICT,
        "reason": (
            "Vendor observations can show that prices were recorded around T-4h, "
            "but they do not prove sportsbook availability or an actual fill."
        ),
    }
    return summary, observations


def validate_non_authorizing_report(payload: dict[str, Any]) -> None:
    if payload.get("betting_authorized") is not False:
        raise ValueError("feasibility diagnostic attempted to authorize betting")
    if payload.get("may_opened") is not False:
        raise ValueError("feasibility diagnostic opened May")
    if payload.get("executability", {}).get("verdict") != EXECUTABILITY_VERDICT:
        raise ValueError("historical timestamps were mislabeled executable")


def self_test() -> int:
    print("SELF-TEST — HITS CAPTURE FEASIBILITY CONTRACT")
    dates_a = ["2026-03-25", "2026-03-26"]
    dates_b = ["2026-03-27", "2026-03-28"]
    observed = pd.Series(dates_a + dates_b)
    verify_dates(observed, dates_a, dates_b)
    print("  [OK] exact forward selector/confirmation chronology passes")
    try:
        verify_dates(pd.Series(dates_a + dates_b + ["2026-05-01"]), dates_a, dates_b + ["2026-05-01"])
        raise AssertionError("May mutation passed")
    except ValueError:
        print("  [OK] MUTATION: injecting May hard-fails")
    base = pd.DataFrame({
        "official_game_date": dates_a + dates_b,
        "expected_profit": [0.02, 0.03, 0.01, 0.04],
        "market_edge": [0.03, 0.04, 0.02, 0.05],
        "entry_age_min": [10.0, 20.0, 30.0, 40.0],
        "entry_overround": [0.04, 0.05, 0.06, 0.07],
        "line_is_1_5": [0.0, 1.0, 0.0, 1.0],
        "side_is_over": [1.0, 0.0, 1.0, 0.0],
        "clv": [0.01, -0.01, 0.02, -0.02],
    })
    try:
        fit_selector_ols(base, base.iloc[2:], dates_a, dates_b)
        raise AssertionError("confirmation-in-training mutation passed")
    except ValueError:
        print("  [OK] MUTATION: confirmation rows cannot enter OLS training")
    chosen = selected_by_feasibility(base.iloc[2:], np.array([-1e-6, 1e-6]))
    if len(chosen) != 1 or chosen.index[0] != base.index[-1]:
        raise AssertionError("fixed predicted-CLV boundary changed")
    print("  [OK] predicted CLV boundary is structurally fixed at > 0")
    try:
        block_intervals(
            pd.DataFrame({
                "official_game_date": [dates_b[0]], "market_edge": [0.1],
                "clv": [0.01], "realised_profit": [1.0]
            }), dates_b
        )
        raise AssertionError("missing date mutation passed")
    except ValueError:
        print("  [OK] MUTATION: missing confirmation date hard-fails")
    good = {"betting_authorized": False, "may_opened": False,
            "executability": {"verdict": EXECUTABILITY_VERDICT}}
    validate_non_authorizing_report(good)
    print("  [OK] diagnostic remains non-authorizing")
    bad = json.loads(json.dumps(good))
    bad["executability"]["verdict"] = "EXECUTABLE"
    try:
        validate_non_authorizing_report(bad)
        raise AssertionError("executability mutation passed")
    except ValueError:
        print("  [OK] MUTATION: vendor timestamps cannot be labeled executable")
    try:
        verify_hash(__file__, "0" * 64, "mutated script")
        raise AssertionError("hash mutation passed")
    except ValueError:
        print("  [OK] MUTATION: input hash mismatch hard-fails")
    print("  8/8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--protocol", default="data/analysis/hits_capture_feasibility_v1/protocol.json")
    ap.add_argument("--market-root", default="data/market/smartstake")
    ap.add_argument("--out-dir", default="data/analysis/hits_capture_feasibility_v1")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

    protocol_path = Path(args.protocol)
    protocol = load_json(protocol_path)
    if protocol.get("status") != "LOCKED_BEFORE_FEASIBILITY_RESULTS":
        raise ValueError("feasibility protocol was not locked before results")
    fit_input = protocol["inputs"]["fit_report"]
    report_path = verify_hash(fit_input["path"], fit_input["sha256"], "fit report")
    report = load_json(report_path)
    if report.get("betting_authorized") is not False or report.get("may_holdout_permitted_to_open_once") is not False:
        raise ValueError("fit report unexpectedly authorizes action or May access")
    source_input = protocol["inputs"]["uncensored_policy_source"]
    source_path = verify_hash(source_input["path"], source_input["sha256"], "uncensored source")
    fit_protocol = load_json(report["protocol"]["path"])
    selector_dates = list(fit_protocol["internal_chronology"]["selector_dates"])
    confirmation_dates = list(fit_protocol["internal_chronology"]["confirmation_dates"])

    def report_input(name: str) -> Path:
        item = report["inputs"][name]
        return verify_hash(item["path"], item["sha256"], name)

    source = pd.read_csv(source_path)
    frozen = pd.read_csv(report_input("frozen_probabilities"))
    candidate = pd.read_csv(report_input("candidate_probabilities"))
    official = pd.read_csv(report_input("official_outcomes"))
    maximum_observed_age = float(pd.to_numeric(source.entry_age_min, errors="raise").max())
    pairs, funnel = build_policy_pairs(
        source, frozen, candidate, official,
        max_quote_age=maximum_observed_age,
        allowed_dates=selector_dates + confirmation_dates,
    )
    verify_dates(pairs.official_game_date, selector_dates, confirmation_dates)
    rows = _economic_rows(pairs)
    selector = rows[rows.official_game_date.isin(selector_dates)].copy()
    confirmation = rows[rows.official_game_date.isin(confirmation_dates)].copy()
    edge_edges = _selector_edges(selector.market_edge, "edge")
    overround_edges = _selector_edges(selector.entry_overround, "overround")
    for frame in (selector, confirmation):
        frame["quote_age_band"] = pd.cut(
            frame.entry_age_min, bins=AGE_EDGES,
            labels=["[0,15]", "(15,30]", "(30,60]", "(60,90]", "(90,120]", "(120,180]", "(180,360]", "(360,720]", "(720,inf)"],
            include_lowest=True,
        ).astype(str)
        frame["edge_quartile"] = _apply_bins(frame.market_edge, edge_edges, "Q")
        frame["overround_quartile"] = _apply_bins(frame.entry_overround, overround_edges, "Q")

    coefficients, predicted, means, scales, rank = fit_selector_ols(
        selector, confirmation, selector_dates, confirmation_dates
    )
    feasible = selected_by_feasibility(confirmation, predicted)
    intervals = block_intervals(feasible, confirmation_dates)
    success = (
        intervals["valid_draws"] == BOOTSTRAP_DRAWS
        and intervals["capture_95"][0] > 0.10
        and intervals["flat_stake_roi_95"][0] > 0.0
    )
    groups = pd.concat(
        [grouped_summary(selector, "selector"), grouped_summary(confirmation, "confirmation")],
        ignore_index=True,
    )
    executability, continuity = quote_continuity(source, Path(args.market_root))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    groups_path = out_dir / "grouped_clv_roi.csv"
    scored_path = out_dir / "confirmation_predictions.csv"
    continuity_path = out_dir / "quote_continuity.csv"
    report_out = out_dir / "report.json"
    groups.to_csv(groups_path, index=False)
    confirmation.assign(predicted_clv=predicted).to_csv(scored_path, index=False)
    continuity.to_csv(continuity_path, index=False)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "fit_report": {"path": str(report_path), "sha256": sha256(report_path)},
        "may_opened": False,
        "betting_authorized": False,
        "strict_uncensored_funnel": funnel,
        "maximum_observed_quote_age_minutes": maximum_observed_age,
        "executability": executability,
        "selector_only_bins": {
            "edge_edges": [float(value) for value in edge_edges],
            "overround_edges": [float(value) for value in overround_edges],
            "fixed_quote_age_edges": [None if not np.isfinite(value) else float(value) for value in AGE_EDGES],
        },
        "t4h_market_movement_model": {
            "features": list(FEATURES),
            "coefficients_intercept_then_standardized_features": [float(value) for value in coefficients],
            "selector_feature_means": [float(value) for value in means],
            "selector_feature_scales": [float(value) for value in scales],
            "design_rank": rank,
            "selector_rows": int(len(selector)),
            "confirmation_rows": int(len(confirmation)),
            "confirmation_prediction_correlation_with_clv": float(np.corrcoef(predicted, confirmation.clv.to_numpy(float))[0, 1]),
        },
        "feasibility_selection": {
            "rule": "expected_profit >= 0 and selector-fitted predicted_clv > 0",
            "point": _metrics(feasible),
            "date_block_intervals": intervals,
            "locked_gate_passed": bool(success),
        },
        "verdict": (
            "PLAUSIBLE_T4H_SIGNAL_UNDER_UNEXECUTABLE_HISTORICAL_PROXY"
            if success else "PREDECLARED_LINEAR_FEASIBILITY_GATE_FAILED"
        ),
        "interpretation": (
            "This open-period diagnostic does not establish impossibility if it fails, "
            "does not establish executability if it passes, and cannot open May or authorize betting."
        ),
        "outputs": {
            "grouped_clv_roi": {"path": str(groups_path), "sha256": sha256(groups_path)},
            "confirmation_predictions": {"path": str(scored_path), "sha256": sha256(scored_path)},
            "quote_continuity": {"path": str(continuity_path), "sha256": sha256(continuity_path)},
        },
    }
    validate_non_authorizing_report(payload)
    report_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("HITS CAPTURE-FEASIBILITY AUDIT — OPEN MARCH-APRIL ONLY")
    print(f"  uncensored strict rows: {len(rows):,}; selector/confirmation: {len(selector):,}/{len(confirmation):,}")
    print(f"  confirmation selected: {len(feasible):,} across {feasible.official_game_date.nunique()} dates")
    print(f"  capture 95%: {intervals['capture_95'][0]:+.4f} to {intervals['capture_95'][1]:+.4f}")
    print(f"  flat-stake ROI 95%: {intervals['flat_stake_roi_95'][0]:+.4f} to {intervals['flat_stake_roi_95'][1]:+.4f}")
    print(f"  historical executability: {EXECUTABILITY_VERDICT}")
    print(f"  verdict: {payload['verdict']}; betting authorized: NO; May opened: NO")
    print(f"  wrote {report_out}\n        {groups_path}\n        {scored_path}\n        {continuity_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
