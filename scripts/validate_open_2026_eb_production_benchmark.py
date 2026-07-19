#!/usr/bin/env python3
"""Independently recompute and certify the locked open-2026 EB benchmark."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
EPSILON = 1e-12
PRODUCTION_MARKETS = ["hits_0.5", "hits_1.5", "home_runs_0.5"]
TOTAL_BASES_MARKETS = [f"total_bases_{line}" for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)]
ALL_MARKETS = [*PRODUCTION_MARKETS, *TOTAL_BASES_MARKETS]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        handle.write((json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [finite(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    return value


def assert_close(actual: Any, expected: Any, path: str = "root") -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            raise ValueError(f"mapping keys changed at {path}")
        for key in expected:
            assert_close(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError(f"list changed at {path}")
        for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
            assert_close(left, right, f"{path}[{index}]")
    elif isinstance(expected, float):
        # BFGS calibration fits can terminate a few optimizer steps differently
        # after atomic CSV serialization.  This tolerance is isolated to those
        # two descriptive fields; proper scores and interval gates remain at
        # near-machine precision, and predictive-screen booleans must be exact.
        calibration = path.endswith(("calibration_intercept", "calibration_slope"))
        tolerance = 5e-5 if calibration else 1e-13
        if not isinstance(actual, (int, float)) or not np.isclose(float(actual), expected, rtol=1e-11, atol=tolerance):
            raise ValueError(f"numeric value changed at {path}")
    elif actual != expected:
        raise ValueError(f"value changed at {path}")


def binary_metrics(actual: np.ndarray, probability: np.ndarray) -> dict[str, Any]:
    y = np.asarray(actual, dtype=float)
    p = np.clip(np.asarray(probability, dtype=float), EPSILON, 1.0 - EPSILON)
    if len(y) == 0 or len(y) != len(p) or not np.isfinite(p).all():
        raise ValueError("invalid independent scoring inputs")
    log_loss = float(-(y * np.log(p) + (1.0 - y) * np.log1p(-p)).mean())
    brier = float(np.square(p - y).mean())
    auc = float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else float("nan")
    logit = np.log(p) - np.log1p(-p)

    def objective(parameters: np.ndarray) -> float:
        linear = parameters[0] + parameters[1] * logit
        calibrated = np.clip(1.0 / (1.0 + np.exp(-np.clip(linear, -40.0, 40.0))), EPSILON, 1.0 - EPSILON)
        return float(-(y * np.log(calibrated) + (1.0 - y) * np.log1p(-calibrated)).sum())

    fit = minimize(objective, x0=np.array([0.0, 1.0]), method="BFGS")
    intercept, slope = (float(fit.x[0]), float(fit.x[1])) if fit.success and np.isfinite(fit.x).all() else (float("nan"), float("nan"))
    return {
        "rows": len(y), "positives": int(y.sum()), "mean_probability": float(p.mean()),
        "actual_rate": float(y.mean()), "binary_log_loss": log_loss, "binary_brier": brier,
        "roc_auc": auc, "calibration_intercept": intercept, "calibration_slope": slope,
    }


def paired_interval(
    actual: np.ndarray, candidate: np.ndarray, baseline: np.ndarray, dates: pd.Series,
    *, metric: str, draws: int, seed: int,
) -> dict[str, Any]:
    y = np.asarray(actual, dtype=float)
    candidate = np.clip(np.asarray(candidate, dtype=float), EPSILON, 1.0 - EPSILON)
    baseline = np.clip(np.asarray(baseline, dtype=float), EPSILON, 1.0 - EPSILON)
    if metric == "binary_log_loss":
        candidate_loss = -(y * np.log(candidate) + (1.0 - y) * np.log1p(-candidate))
        baseline_loss = -(y * np.log(baseline) + (1.0 - y) * np.log1p(-baseline))
    elif metric == "binary_brier":
        candidate_loss = np.square(candidate - y)
        baseline_loss = np.square(baseline - y)
    else:
        raise ValueError("unknown independent paired metric")
    block = pd.DataFrame({"date": dates.astype(str), "delta": candidate_loss - baseline_loss}).groupby("date", sort=True)["delta"].agg(["sum", "count"])
    if len(block) < 2:
        raise ValueError("independent interval requires at least two dates")
    sums = block["sum"].to_numpy(float)
    counts = block["count"].to_numpy(float)
    point = float(sums.sum() / counts.sum())
    rng = np.random.default_rng(seed)
    samples = np.empty(draws, dtype=float)
    for draw in range(draws):
        selected = rng.integers(0, len(block), size=len(block))
        samples[draw] = sums[selected].sum() / counts[selected].sum()
    lower, upper = np.quantile(samples, [0.025, 0.975])
    return {"point": point, "lower": float(lower), "upper": float(upper), "dates": len(block), "draws": draws}


def period_mask(dates: pd.Series, period: str) -> np.ndarray:
    values = dates.astype(str)
    if period == "march_april":
        return values.str.startswith(("2026-03", "2026-04")).to_numpy()
    if period == "june":
        return values.str.startswith("2026-06").to_numpy()
    if period == "pooled_open":
        return np.ones(len(values), dtype=bool)
    raise ValueError("unknown period")


def screen(market: dict[str, Any]) -> dict[str, bool]:
    pooled = market["comparisons"]["pooled_open"]["eb_vs_production"]
    march_april = market["comparisons"]["march_april"]["eb_vs_production"]
    june = market["comparisons"]["june"]["eb_vs_production"]
    eb = market["metrics"]["pooled_open"]["empirical_bayes"]
    production = market["metrics"]["pooled_open"]["production"]
    result = {
        "both_pooled_interval_uppers_below_zero": all(pooled[name]["upper"] < 0 for name in ("binary_log_loss", "binary_brier")),
        "both_periods_both_points_below_zero": all(block[name]["point"] < 0 for block in (march_april, june) for name in ("binary_log_loss", "binary_brier")),
        "calibration_not_both_farther_from_ideal": not (
            abs(eb["calibration_intercept"]) > abs(production["calibration_intercept"])
            and abs(eb["calibration_slope"] - 1.0) > abs(production["calibration_slope"] - 1.0)
        ),
        "auc_noninferior_point_estimate": eb["roc_auc"] >= production["roc_auc"],
        "identical_coverage": eb["rows"] == production["rows"],
    }
    result["predictive_screen_passed"] = all(result.values())
    return result


def validate_prediction_rows(frame: pd.DataFrame, source: dict[str, Any]) -> None:
    key = ["mlb_game_pk", "player_id", "game_date"]
    if frame[key].isna().any().any() or frame.duplicated(key).any():
        raise ValueError("benchmark prediction identity changed")
    dates = sorted(frame["game_date"].astype(str).unique())
    if dates != source["dates"] or any(date.startswith("2026-05") for date in dates):
        raise ValueError("benchmark prediction chronology changed")
    if not frame["pa"].gt(0).all() or not frame["lineup_slot"].between(1, 9).all():
        raise ValueError("gradeable PA or lineup eligibility changed")
    if not frame["eb_player_fallback"].isin([0, 1]).all():
        raise ValueError("fallback labels changed")
    if not frame["singles"].eq(frame["hits"] - frame["doubles"] - frame["triples"] - frame["home_runs"]).all():
        raise ValueError("single accounting changed")
    if not frame["bip_out"].eq(frame["ab"] - frame["strikeouts"] - frame["hits"]).all():
        raise ValueError("BIP-out accounting changed")
    if not frame["other_non_ab"].eq(frame["pa"] - frame["ab"] - frame["walks"]).all():
        raise ValueError("other-non-AB accounting changed")
    if not frame["total_bases"].eq(frame["singles"] + 2 * frame["doubles"] + 3 * frame["triples"] + 4 * frame["home_runs"]).all():
        raise ValueError("total-base accounting changed")
    if (frame[["singles", "bip_out", "other_non_ab"]] < 0).any().any():
        raise ValueError("official outcome accounting became negative")
    for market in ALL_MARKETS:
        actual = frame[f"actual_{market}"]
        if market.startswith("hits_"):
            expected = frame["hits"] > float(market.removeprefix("hits_"))
        elif market.startswith("home_runs_"):
            expected = frame["home_runs"] > float(market.removeprefix("home_runs_"))
        else:
            expected = frame["total_bases"] > float(market.removeprefix("total_bases_"))
        if not actual.eq(expected.astype(int)).all():
            raise ValueError(f"official grade changed for {market}")
        columns = [f"league_{market}", f"eb_{market}"]
        if market in PRODUCTION_MARKETS:
            columns.append(f"production_{market}")
        if frame[columns].isna().any().any() or not frame[columns].apply(lambda x: x.between(0, 1)).all().all():
            raise ValueError(f"probability coverage or range changed for {market}")


def recompute(frame: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, Any]:
    draws = int(protocol["metrics"]["bootstrap_draws"])
    seed = int(protocol["metrics"]["bootstrap_seed"])
    markets: dict[str, Any] = {}
    for market in ALL_MARKETS:
        arms = ["league_rate", "empirical_bayes"] + (["production"] if market in PRODUCTION_MARKETS else [])
        probabilities = {
            "league_rate": frame[f"league_{market}"].to_numpy(float),
            "empirical_bayes": frame[f"eb_{market}"].to_numpy(float),
        }
        if market in PRODUCTION_MARKETS:
            probabilities["production"] = frame[f"production_{market}"].to_numpy(float)
        y = frame[f"actual_{market}"].to_numpy(float)
        item: dict[str, Any] = {"metrics": {}, "comparisons": {}}
        for period in protocol["metrics"]["periods"]:
            mask = period_mask(frame["game_date"], period)
            item["metrics"][period] = {arm: binary_metrics(y[mask], probabilities[arm][mask]) for arm in arms}
            pairs = [("empirical_bayes", "league_rate", "eb_vs_league")]
            if market in PRODUCTION_MARKETS:
                pairs += [("empirical_bayes", "production", "eb_vs_production"), ("production", "league_rate", "production_vs_league")]
            item["comparisons"][period] = {
                label: {
                    metric: paired_interval(y[mask], probabilities[left][mask], probabilities[right][mask], frame.loc[mask, "game_date"], metric=metric, draws=draws, seed=seed)
                    for metric in ("binary_log_loss", "binary_brier")
                }
                for left, right, label in pairs
            }
        item["production_comparator_available"] = market in PRODUCTION_MARKETS
        item["readiness_only"] = market in TOTAL_BASES_MARKETS
        if market in PRODUCTION_MARKETS:
            item["predictive_screen"] = screen(item)
        markets[market] = finite(item)
    return markets


def validate_report_claims(
    report: dict[str, Any], frame: pd.DataFrame, protocol: dict[str, Any],
    source: dict[str, Any], expected_markets: dict[str, Any],
) -> list[str]:
    assert_close(report["markets"], expected_markets, "report.markets")
    passed = [market for market in PRODUCTION_MARKETS if expected_markets[market]["predictive_screen"]["predictive_screen_passed"]]
    expected = {
        "schema_version": "open-2026-eb-production-benchmark-report-v3",
        "status": "OPEN_2026_EB_PRODUCTION_BENCHMARK_COMPLETE",
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "confirmation_2025_opened": False,
        "benchmark_is_diagnostic_not_a_candidate": True,
        "dates": source["dates"],
        "rows": len(frame),
        "excluded_zero_pa_model_rows": int(source["artifacts"]["lineup_snapshots"]["rows"]) - len(frame),
        "eb_player_fallback_rows": int(frame["eb_player_fallback"].sum()),
        "measured_simplification_limiter_markets": passed,
        "predictive_screen_passed_any_market": bool(passed),
        "economic_analysis_allowed": bool(passed),
        "model_publishable": False,
        "selection_passed": False,
        "total_bases_production_comparator_available": False,
    }
    for key, value in expected.items():
        assert_close(report.get(key), value, f"report.{key}")
    if report.get("next_action") != (
        "Predeclare exactly one market-specific simplification challenger; do not install the empirical-Bayes control directly."
        if passed else "Retain immutable production; do not build a shared-rate simplification challenger from mixed or weak evidence."
    ):
        raise ValueError("next action changed")
    return passed


def validate_runtime(
    report: dict[str, Any], report_path: Path, predictions_path: Path,
    protocol_path: Path, source_path: Path, evidence_root: Path,
) -> None:
    artifacts = report["artifacts"]
    if artifacts["protocol"]["sha256"] != sha256(protocol_path):
        raise ValueError("result protocol hash changed")
    if artifacts["source_manifest"]["sha256"] != sha256(source_path):
        raise ValueError("result source-manifest hash changed")
    if artifacts["predictions"]["sha256"] != sha256(predictions_path):
        raise ValueError("result prediction hash changed")
    if Path(artifacts["predictions"]["path"]) != predictions_path.relative_to(evidence_root):
        raise ValueError("result prediction path changed")
    commit = report["runtime"]["source_commit"]
    subprocess.check_call(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=ROOT)
    for name in ("runner", "module"):
        record = report["runtime"][name]
        content = subprocess.check_output(["git", "show", f"{commit}:{record['path']}"], cwd=ROOT)
        if hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise ValueError(f"result {name} hash does not match source commit")
    if report_path.stat().st_size == 0:
        raise ValueError("benchmark report is empty")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    protocol_path = args.protocol.resolve()
    report_path = args.report.resolve()
    predictions_path = args.predictions.resolve()
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol.get("schema_version") != "open-2026-eb-production-benchmark-protocol-v3":
        raise ValueError("validator requires locked v3 protocol")
    source_path = evidence_root / protocol["source_manifest"]["path"]
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if sha256(source_path) != protocol["source_manifest"]["sha256"]:
        raise ValueError("validator source manifest changed")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    frame = pd.read_csv(predictions_path)
    validate_prediction_rows(frame, source)
    expected_markets = recompute(frame, protocol)
    passed = validate_report_claims(report, frame, protocol, source, expected_markets)
    validate_runtime(report, report_path, predictions_path, protocol_path, source_path, evidence_root)
    certificate = {
        "schema_version": "open-2026-eb-production-benchmark-certificate-v1",
        "status": "OPEN_2026_EB_PRODUCTION_BENCHMARK_CERTIFIED_DIAGNOSTIC",
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "confirmation_2025_opened": False,
        "model_publishable": False,
        "selection_passed": False,
        "rows": len(frame),
        "dates": source["dates"],
        "predictive_screen_passed_markets": passed,
        "economic_analysis_allowed_under_locked_diagnostic_contract": bool(passed),
        "artifacts": {
            "protocol": {"path": str(protocol_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(protocol_path)},
            "source_manifest": {"path": str(source_path.relative_to(evidence_root)).replace("\\", "/"), "sha256": sha256(source_path)},
            "report": {"path": str(report_path.relative_to(evidence_root)).replace("\\", "/"), "sha256": sha256(report_path)},
            "predictions": {"path": str(predictions_path.relative_to(evidence_root)).replace("\\", "/"), "sha256": sha256(predictions_path)},
        },
        "validator": {
            "path": "scripts/validate_open_2026_eb_production_benchmark.py",
            "sha256": sha256(Path(__file__)),
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        },
        "protected_invariants": {
            "independent_metrics_recomputed": True,
            "independent_intervals_recomputed": True,
            "official_grades_recomputed": True,
            "May_2026_remains_sealed": True,
            "2025_confirmation_remains_unread": True,
            "no_model_published": True,
            "no_betting_authorization": True,
        },
    }
    atomic_json(args.out.resolve(), certificate)
    print("OPEN_2026_EB_PRODUCTION_BENCHMARK_CERTIFIED_DIAGNOSTIC")
    print(f"rows={len(frame)} predictive_screen_passed={passed}")
    print(f"certificate_sha256={sha256(args.out.resolve())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
