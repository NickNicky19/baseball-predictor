#!/usr/bin/env python3
"""Diagnose candidate hitter-skill calibration per official plate appearance.

May is sealed.  Selector probability bands are outcome-blind and applied
unchanged to confirmation.  Official PA/hits are outcome-time scoring facts;
they never alter the inferred prediction or production model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_pa_counterfactual import (  # noqa: E402
    reject_holdout_dates,
    require_exact_key_set,
)
from src.evaluation.hits_per_pa_diagnostic import (  # noqa: E402
    assign_probability_bands,
    date_block_residual_interval,
    per_pa_metrics,
    sturges_quantile_edges,
    validate_trials,
)


SCHEMA = "hits-per-pa-skill-report-v1"
PLAYER_KEY = ["mlb_game_pk", "player_id"]


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {source}")
    return payload


def verified_path(record: dict[str, Any], label: str) -> Path:
    path = Path(str(record.get("path", "")))
    expected = str(record.get("sha256", ""))
    if not path.is_file() or not expected or sha256(path) != expected:
        raise ValueError(f"{label} is missing or hash-mismatched")
    return path


def build_trials(player: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    player_required = [
        *PLAYER_KEY,
        "official_game_date",
        "official_lineup_slot",
        "official_pa",
        "inferred_per_pa_hit_probability",
    ]
    missing = [column for column in player_required if column not in player.columns]
    if missing:
        raise ValueError(f"player counterfactuals missing {missing}")
    market_required = [*PLAYER_KEY, "official_game_date", "actual_value"]
    missing = [column for column in market_required if column not in market.columns]
    if missing:
        raise ValueError(f"market counterfactuals missing {missing}")

    truth = market[market_required].drop_duplicates()
    duplicated = truth.duplicated(PLAYER_KEY, keep=False)
    if duplicated.any():
        raise ValueError(
            "one player-game has contradictory official hits/date facts:\n"
            + truth.loc[duplicated].head(20).to_string(index=False)
        )
    truth = truth.rename(columns={"actual_value": "official_hits"})
    trials = player[player_required].merge(
        truth,
        on=[*PLAYER_KEY, "official_game_date"],
        how="left",
        validate="one_to_one",
    )
    if len(trials) != len(player) or trials.official_hits.isna().any():
        raise ValueError("official hits did not cover the exact player-game universe")
    trials = validate_trials(trials)
    require_exact_key_set(
        set(map(tuple, trials[PLAYER_KEY].to_numpy())),
        set(map(tuple, player[PLAYER_KEY].to_numpy())),
        "per-PA diagnostic player-game",
    )
    return trials


def group_record(
    frame: pd.DataFrame,
    *,
    period: str,
    dimension: str,
    value: str,
    declared_dates: list[str],
    bootstrap: int,
    seed: int,
) -> dict[str, Any]:
    metrics = per_pa_metrics(frame)
    interval = date_block_residual_interval(
        frame,
        declared_dates=declared_dates,
        bootstrap=bootstrap,
        seed=seed,
    )
    return {
        "period": period,
        "dimension": dimension,
        "value": value,
        **metrics,
        "residual_lower_95": interval.lower,
        "residual_upper_95": interval.upper,
        "valid_bootstrap_draws": interval.valid_draws,
        "interval_excludes_zero": bool(
            np.isfinite(interval.lower)
            and np.isfinite(interval.upper)
            and (interval.lower > 0.0 or interval.upper < 0.0)
        ),
    }


def build_group_table(
    trials: pd.DataFrame,
    *,
    selector_dates: list[str],
    confirmation_dates: list[str],
    bootstrap: int,
    seed: int,
) -> pd.DataFrame:
    periods = {
        "selector": selector_dates,
        "confirmation": confirmation_dates,
        "all_open_fit": sorted(set(selector_dates) | set(confirmation_dates)),
    }
    records: list[dict[str, Any]] = []
    for period, declared_dates in periods.items():
        subset = trials[trials.official_game_date.isin(declared_dates)]
        if subset.empty:
            raise ValueError(f"{period} contains no per-PA trials")
        records.append(
            group_record(
                subset,
                period=period,
                dimension="ALL",
                value="ALL",
                declared_dates=declared_dates,
                bootstrap=bootstrap,
                seed=seed,
            )
        )
        for dimension, column in (
            ("probability_band", "probability_band"),
            ("official_lineup_slot", "official_lineup_slot"),
        ):
            grouped_rows = 0
            for value in sorted(subset[column].unique(), key=str):
                group = subset[subset[column].eq(value)]
                grouped_rows += len(group)
                records.append(
                    group_record(
                        group,
                        period=period,
                        dimension=dimension,
                        value=str(value),
                        declared_dates=declared_dates,
                        bootstrap=bootstrap,
                        seed=seed,
                    )
                )
            if grouped_rows != len(subset):
                raise AssertionError(f"{period}/{dimension} groups do not sum to universe")
    return pd.DataFrame(records)


def stability_summary(groups: pd.DataFrame, dimension: str) -> dict[str, Any]:
    table = groups[groups.dimension.eq(dimension)].pivot(
        index="value", columns="period", values="predicted_minus_observed_hit_rate"
    )
    table = table.dropna(subset=["selector", "confirmation"])
    if len(table) < 2:
        return {"groups": int(len(table)), "spearman": None, "same_sign_fraction": None}
    selector_rank = table.selector.rank(method="average")
    confirmation_rank = table.confirmation.rank(method="average")
    spearman = float(selector_rank.corr(confirmation_rank))
    same_sign = np.sign(table.selector.to_numpy(float)) == np.sign(
        table.confirmation.to_numpy(float)
    )
    return {
        "groups": int(len(table)),
        "spearman": spearman,
        "same_sign_fraction": float(np.mean(same_sign)),
        "interpretation": (
            "Descriptive stability only. Group slices are multiple comparisons "
            "and cannot independently authorize a correction."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--protocol",
        default="data/analysis/market_policy_hits_2026/per_pa_skill_protocol_v1.json",
    )
    ap.add_argument(
        "--out-dir",
        default="data/analysis/market_policy_hits_2026/per_pa_skill_time_safe_v1",
    )
    args = ap.parse_args(argv)

    protocol_path = Path(args.protocol)
    protocol = load_json(protocol_path)
    if protocol.get("schema_version") != "hits-per-pa-skill-protocol-v1":
        raise ValueError("unknown per-PA skill protocol")
    if protocol.get("status") != "PREDECLARED_DIAGNOSTIC_ONLY":
        raise ValueError("per-PA skill protocol was not predeclared")
    if protocol.get("betting_authorized") is not False:
        raise ValueError("per-PA skill diagnostic refuses an authorized protocol")

    pa_report_path = verified_path(
        protocol["pa_counterfactual_report"], "PA counterfactual report"
    )
    pa_report = load_json(pa_report_path)
    if pa_report.get("betting_authorized") is not False:
        raise ValueError("PA counterfactual unexpectedly authorizes betting")
    if pa_report["chronology"].get("may_opened") is not False:
        raise ValueError("PA counterfactual reports that May was opened")
    player_path = verified_path(protocol["player_counterfactuals"], "player counterfactuals")
    market_path = verified_path(protocol["market_counterfactuals"], "market counterfactuals")
    if pa_report["outputs"]["player_counterfactuals"]["sha256"] != sha256(player_path):
        raise ValueError("PA report is not bound to player counterfactuals")
    if pa_report["outputs"]["market_counterfactuals"]["sha256"] != sha256(market_path):
        raise ValueError("PA report is not bound to market counterfactuals")

    selector_dates = list(pa_report["chronology"]["selector_dates"])
    confirmation_dates = list(pa_report["chronology"]["confirmation_dates"])
    holdout_start = str(protocol["scope"]["holdout_start"])
    all_dates = sorted(set(selector_dates) | set(confirmation_dates))
    reject_holdout_dates(all_dates, holdout_start)
    if set(selector_dates) & set(confirmation_dates) or max(selector_dates) >= min(confirmation_dates):
        raise ValueError("selector/confirmation chronology is not disjoint and ordered")

    player = pd.read_csv(player_path)
    market = pd.read_csv(market_path)
    trials = build_trials(player, market)
    if len(trials) != 3386 or len(trials) != int(pa_report["player_games"]):
        raise ValueError("per-PA diagnostic does not contain the predeclared 3,386 player-games")
    reject_holdout_dates(sorted(trials.official_game_date.unique()), holdout_start)

    selector = trials[trials.official_game_date.isin(selector_dates)]
    edges = sturges_quantile_edges(selector.inferred_per_pa_hit_probability)
    trials["probability_band"] = assign_probability_bands(
        trials.inferred_per_pa_hit_probability, edges
    )
    trials["period"] = np.where(
        trials.official_game_date.isin(selector_dates), "selector", "confirmation"
    )
    if not trials.official_game_date.isin(all_dates).all():
        raise ValueError("per-PA trial lies outside the declared open chronology")
    q = trials.inferred_per_pa_hit_probability.to_numpy(float)
    pa = trials.official_pa.to_numpy(float)
    hits = trials.official_hits.to_numpy(float)
    trials["expected_hits"] = q * pa
    trials["predicted_minus_observed_hits"] = trials.expected_hits - hits
    trials["observed_hit_rate"] = hits / pa

    bootstrap = int(protocol["uncertainty"]["bootstrap_draws"])
    seed = int(protocol["uncertainty"]["seed"])
    groups = build_group_table(
        trials,
        selector_dates=selector_dates,
        confirmation_dates=confirmation_dates,
        bootstrap=bootstrap,
        seed=seed,
    )
    daily_records = []
    for game_date in all_dates:
        day = trials[trials.official_game_date.eq(game_date)]
        if day.empty:
            daily_records.append(
                {
                    "official_game_date": game_date,
                    "player_games": 0,
                    "official_pa": 0,
                    "official_hits": 0,
                    "expected_hits": 0.0,
                    "predicted_minus_observed_hit_rate": float("nan"),
                }
            )
        else:
            daily_records.append({"official_game_date": game_date, **per_pa_metrics(day)})
    daily = pd.DataFrame(daily_records)

    global_confirmation = groups[
        groups.period.eq("confirmation")
        & groups.dimension.eq("ALL")
        & groups.value.eq("ALL")
    ].iloc[0]
    global_bias = bool(global_confirmation.interval_excludes_zero)
    confirmation_slices = groups[
        groups.period.eq("confirmation") & ~groups.dimension.eq("ALL")
    ]
    stability = {
        dimension: stability_summary(groups, dimension)
        for dimension in ("probability_band", "official_lineup_slot")
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    trials_path = out_dir / "player_game_trials.csv"
    groups_path = out_dir / "calibration_by_period_band_slot.csv"
    daily_path = out_dir / "daily_residuals.csv"
    edges_path = out_dir / "selector_probability_band_edges.json"
    report_path = out_dir / "report.json"
    trials.to_csv(trials_path, index=False)
    groups.to_csv(groups_path, index=False)
    daily.to_csv(daily_path, index=False)
    edges_payload = {
        "method": protocol["probability_bands"]["method"],
        "selector_player_games": int(len(selector)),
        "bands": int(len(edges) - 1),
        "edges": [float(value) for value in edges],
        "outcomes_used": False,
        "confirmation_probabilities_used": False,
    }
    edges_path.write_text(json.dumps(edges_payload, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": SCHEMA,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "pa_counterfactual_report": {"path": str(pa_report_path), "sha256": sha256(pa_report_path)},
        "chronology": {
            "selector_dates": selector_dates,
            "confirmation_dates": confirmation_dates,
            "holdout_start": holdout_start,
            "may_opened": False,
        },
        "player_games": int(len(trials)),
        "selector_probability_bands": edges_payload,
        "confirmation_global": {
            key: (
                value.item() if isinstance(value, np.generic) else value
            )
            for key, value in global_confirmation.to_dict().items()
        },
        "global_per_pa_bias_detected": global_bias,
        "global_bias_rule": (
            "confirmation date-block 95% predicted-minus-observed hit-rate "
            "interval excludes zero"
        ),
        "confirmation_unadjusted_slice_intervals_excluding_zero": {
            dimension: int(
                confirmation_slices[
                    confirmation_slices.dimension.eq(dimension)
                ].interval_excludes_zero.sum()
            )
            for dimension in ("probability_band", "official_lineup_slot")
        },
        "selector_confirmation_slice_stability": stability,
        "interpretation_limits": [
            "Official hits and PA are outcome-time scoring facts and cannot enter live prediction.",
            "Probability-band and lineup-slot intervals are unadjusted multiple comparisons and generate hypotheses only.",
            "This strict market-listed starter population need not represent every MLB hitter-game.",
            "No calibration function was fit, no policy changed, May remained sealed, and betting is not authorized."
        ],
        "outputs": {
            "trials": {"path": str(trials_path), "sha256": sha256(trials_path)},
            "groups": {"path": str(groups_path), "sha256": sha256(groups_path)},
            "daily": {"path": str(daily_path), "sha256": sha256(daily_path)},
            "band_edges": {"path": str(edges_path), "sha256": sha256(edges_path)},
        },
        "verdict": "DIAGNOSTIC_ONLY",
        "betting_authorized": False,
    }
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    print("HITS PER-PA HITTER-SKILL DIAGNOSTIC — OPEN MARCH/APRIL ONLY")
    print(f"  player-games: {len(trials):,}; bands: {len(edges)-1}; May opened: NO")
    print(
        "  confirmation predicted-minus-observed hit rate: "
        f"{global_confirmation.predicted_minus_observed_hit_rate:+.6f} "
        f"[{global_confirmation.residual_lower_95:+.6f}, "
        f"{global_confirmation.residual_upper_95:+.6f}]"
    )
    print(f"  global per-PA bias detected: {global_bias}")
    print("  production changes: NONE; May opened: NO; betting authorized: NO")
    print(f"  wrote: {report_path}\n         {groups_path}\n         {trials_path}\n         {daily_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
