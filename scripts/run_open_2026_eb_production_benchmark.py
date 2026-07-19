#!/usr/bin/env python3
"""Run the locked rolling EB-versus-production benchmark on open 2026 dates."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import sha256  # noqa: E402
from src.evaluation.open_2026_probability_benchmark import (  # noqa: E402
    ALL_MARKETS, PRODUCTION_MARKETS, TOTAL_BASES_MARKETS, actual_markets,
    binary_metrics, load_protocol, paired_interval, period_mask,
    rolling_pa_probabilities, screen_market,
)
from src.learning.shared_pa_model import derived_market_probabilities  # noqa: E402


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        frame.to_csv(handle, index=False, lineterminator="\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def atomic_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        handle.write((json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def finite_or_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: finite_or_none(item) for key, item in value.items()}
    if isinstance(value, list):
        return [finite_or_none(item) for item in value]
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def production_wide(frame: pd.DataFrame) -> pd.DataFrame:
    selected = frame[
        ((frame["category"] == "hits") & frame["line"].isin([0.5, 1.5]))
        | ((frame["category"] == "home_runs") & (frame["line"] == 0.5))
    ].copy()
    selected["market"] = selected.apply(
        lambda row: f"{row['category']}_{float(row['line']):.1f}", axis=1
    )
    wide = selected.pivot(
        index=["mlb_game_pk", "player_id", "game_date"],
        columns="market", values="sim_p_over",
    ).reset_index()
    if any(market not in wide for market in PRODUCTION_MARKETS):
        raise ValueError("production comparable market is missing")
    return wide


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence_root.resolve()
    out_dir = args.out_dir.resolve()
    protocol = load_protocol(args.protocol, evidence_root=root)
    source_path = root / protocol["source_manifest"]["path"]
    source = json.loads(source_path.read_text(encoding="utf-8"))
    artifacts = source["artifacts"]

    def read_csv(name: str, **kwargs: Any) -> pd.DataFrame:
        return pd.read_csv(root / artifacts[name]["path"], **kwargs)

    training = artifacts.get("official_training_outcomes") or {}
    training_path = root / str(training.get("path", ""))
    if not training_path.is_file() or sha256(training_path) != training.get("sha256"):
        raise ValueError("official training outcome source is missing or hash-mismatched")
    history = pd.read_csv(training_path, compression="gzip", nrows=87462)
    required_history = {
        "season", "game_date", "player_id", "out_pa", "out_ab", "out_hits",
        "out_doubles", "out_triples", "out_hr", "out_bb", "out_k",
    }
    if not required_history.issubset(history.columns):
        raise ValueError("official training outcome schema changed")
    seasons = sorted(pd.to_numeric(history["season"], errors="raise").astype(int).unique())
    if seasons != [2023, 2024] or len(history) != 87462:
        raise ValueError("historical fit boundary changed or admitted confirmation data")
    training_dates = pd.to_datetime(history["game_date"], errors="raise")
    if training_dates.max().strftime("%Y-%m-%d") != "2024-09-30":
        raise ValueError("historical fit date boundary changed or admitted confirmation data")
    official = read_csv("official_outcomes")
    lineups = read_csv("lineup_snapshots")
    production = read_csv("production_probabilities")
    targets = lineups.sort_values(["game_date", "mlb_game_pk", "player_id"]).reset_index(drop=True)
    dates = sorted(targets["game_date"].astype(str).unique())
    if dates != source["dates"] or any(date.startswith("2026-05") for date in dates):
        raise ValueError("evaluation date boundary changed")

    league_pa, eb_pa, fallback = rolling_pa_probabilities(
        history, targets, official, prior_strength=200.0
    )
    pa_payload = json.loads((root / protocol["pa_volume"]["artifact_path"]).read_text(encoding="utf-8"))
    pa_distribution = pa_payload.get("by_lineup_slot")
    if not isinstance(pa_distribution, dict):
        raise ValueError("PA distribution schema changed")
    league_markets = derived_market_probabilities(league_pa, targets["lineup_slot"], pa_distribution)
    eb_markets = derived_market_probabilities(eb_pa, targets["lineup_slot"], pa_distribution)
    predictions = targets.copy()
    predictions["eb_player_fallback"] = fallback.astype(int)
    for market in ALL_MARKETS:
        predictions[f"league_{market}"] = league_markets[market]
        predictions[f"eb_{market}"] = eb_markets[market]
    production_probability = production_wide(production)
    production_probability = production_probability.rename(
        columns={market: f"production_{market}" for market in PRODUCTION_MARKETS}
    )
    predictions = predictions.merge(
        production_probability, on=["mlb_game_pk", "player_id", "game_date"],
        how="left", validate="one_to_one",
    )
    if predictions[[f"production_{market}" for market in PRODUCTION_MARKETS]].isna().any().any():
        raise ValueError("production probability coverage changed")
    gradeable = predictions.merge(
        official[official["pa"] > 0], on=["mlb_game_pk", "player_id", "game_date"],
        how="inner", validate="one_to_one",
    ).sort_values(["game_date", "mlb_game_pk", "player_id"]).reset_index(drop=True)
    actual = actual_markets(gradeable)
    for market in ALL_MARKETS:
        gradeable[f"actual_{market}"] = actual[market]

    draws = int(protocol["metrics"]["bootstrap_draws"])
    seed = int(protocol["metrics"]["bootstrap_seed"])
    markets: dict[str, Any] = {}
    for market in ALL_MARKETS:
        market_report: dict[str, Any] = {"metrics": {}, "comparisons": {}}
        arms = ["league_rate", "empirical_bayes"]
        if market in PRODUCTION_MARKETS:
            arms.append("production")
        probabilities = {
            "league_rate": gradeable[f"league_{market}"].to_numpy(float),
            "empirical_bayes": gradeable[f"eb_{market}"].to_numpy(float),
        }
        if market in PRODUCTION_MARKETS:
            probabilities["production"] = gradeable[f"production_{market}"].to_numpy(float)
        y = gradeable[f"actual_{market}"].to_numpy(float)
        for period in protocol["metrics"]["periods"]:
            mask = period_mask(gradeable["game_date"], period)
            market_report["metrics"][period] = {
                arm: binary_metrics(y[mask], probabilities[arm][mask]) for arm in arms
            }
            comparisons: dict[str, Any] = {}
            pairs = [("empirical_bayes", "league_rate", "eb_vs_league")]
            if market in PRODUCTION_MARKETS:
                pairs.extend([
                    ("empirical_bayes", "production", "eb_vs_production"),
                    ("production", "league_rate", "production_vs_league"),
                ])
            for candidate, baseline, name in pairs:
                comparisons[name] = {
                    metric: paired_interval(
                        y[mask], probabilities[candidate][mask], probabilities[baseline][mask],
                        gradeable.loc[mask, "game_date"], metric=metric, draws=draws, seed=seed,
                    )
                    for metric in ("binary_log_loss", "binary_brier")
                }
            market_report["comparisons"][period] = comparisons
        market_report["production_comparator_available"] = market in PRODUCTION_MARKETS
        market_report["readiness_only"] = market in TOTAL_BASES_MARKETS
        if market in PRODUCTION_MARKETS:
            market_report["predictive_screen"] = screen_market(market_report)
        markets[market] = market_report

    passed = [
        market for market in PRODUCTION_MARKETS
        if markets[market]["predictive_screen"]["predictive_screen_passed"]
    ]
    predictions_path = out_dir / "benchmark_predictions.csv"
    atomic_csv(gradeable, predictions_path)
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    report = finite_or_none({
        "schema_version": "open-2026-eb-production-benchmark-report-v4",
        "status": "OPEN_2026_EB_PRODUCTION_BENCHMARK_COMPLETE",
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "confirmation_2025_opened": False,
        "benchmark_is_diagnostic_not_a_candidate": True,
        "dates": dates,
        "rows": len(gradeable),
        "excluded_zero_pa_model_rows": len(predictions) - len(gradeable),
        "eb_player_fallback_rows": int(gradeable["eb_player_fallback"].sum()),
        "markets": markets,
        "measured_simplification_limiter_markets": passed,
        "predictive_screen_passed_any_market": bool(passed),
        "economic_analysis_allowed": bool(passed),
        "model_publishable": False,
        "selection_passed": False,
        "total_bases_production_comparator_available": False,
        "next_action": (
            "Predeclare exactly one market-specific simplification challenger; do not install the empirical-Bayes control directly."
            if passed else
            "Retain immutable production; do not build a shared-rate simplification challenger from mixed or weak evidence."
        ),
        "artifacts": {
            "protocol": {"path": args.protocol.resolve().relative_to(ROOT).as_posix(), "sha256": sha256(args.protocol)},
            "source_manifest": {"path": source_path.relative_to(root).as_posix(), "sha256": sha256(source_path)},
            "source_certificate": protocol["source_certificate"],
            "predictions": {"path": predictions_path.relative_to(root).as_posix(), "sha256": sha256(predictions_path), "rows": len(gradeable)},
        },
        "runtime": {
            "source_commit": source_commit,
            "runner": {"path": "scripts/run_open_2026_eb_production_benchmark.py", "sha256": sha256(ROOT / "scripts/run_open_2026_eb_production_benchmark.py")},
            "module": {"path": "src/evaluation/open_2026_probability_benchmark.py", "sha256": sha256(ROOT / "src/evaluation/open_2026_probability_benchmark.py")},
        },
    })
    report_path = out_dir / "benchmark_report.json"
    atomic_json(report, report_path)
    print("OPEN_2026_EB_PRODUCTION_BENCHMARK_COMPLETE")
    print(f"rows={len(gradeable)} zero_pa_excluded={len(predictions) - len(gradeable)} fallback={int(gradeable['eb_player_fallback'].sum())}")
    print(f"predictive_screen_passed={passed}")
    print(f"report_sha256={sha256(report_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
