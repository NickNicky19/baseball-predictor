"""Locked evaluation logic for the empirical-Bayes versus production benchmark."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.metrics import roc_auc_score

from src.evaluation.multi_market_foundation import sha256
from src.evaluation.open_2026_benchmark_sources import validate_source_manifest
from src.learning.shared_pa_model import PA_OUTCOMES, derived_market_probabilities, normalized_counts


SCHEMA = "open-2026-eb-production-benchmark-protocol-v1"
STATUS = "LOCKED_BEFORE_OPEN_2026_BENCHMARK_SCORING"
PRODUCTION_MARKETS = ["hits_0.5", "hits_1.5", "home_runs_0.5"]
TOTAL_BASES_MARKETS = [f"total_bases_{line}" for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)]
ALL_MARKETS = [*PRODUCTION_MARKETS, *TOTAL_BASES_MARKETS]
HITTER_KEY = ["mlb_game_pk", "player_id", "game_date"]
EPSILON = 1e-12


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def validate_protocol(payload: dict[str, Any], *, evidence_root: str | Path) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA or payload.get("status") != STATUS:
        raise ValueError("unrecognized open-2026 EB benchmark protocol")
    if payload.get("betting_authorized") is not False or payload.get("production_unchanged") is not True:
        raise ValueError("benchmark changed production or authorized betting")
    if payload.get("may_2026_opened") is not False or payload.get("confirmation_2025_opened") is not False:
        raise ValueError("benchmark opened sealed evidence")
    repo_root = Path(__file__).resolve().parents[2]
    foundation = payload.get("foundation_protocol") or {}
    foundation_path = repo_root / str(foundation.get("path", ""))
    if not foundation_path.is_file() or sha256(foundation_path) != foundation.get("sha256"):
        raise ValueError("multi-market foundation protocol changed")
    root = Path(evidence_root)
    source = payload.get("source_manifest") or {}
    source_path = root / str(source.get("path", ""))
    if not source_path.is_file() or sha256(source_path) != source.get("sha256"):
        raise ValueError("source manifest missing or hash-mismatched")
    source_payload = _json(source_path)
    validate_source_manifest(source_payload, evidence_root=root)
    if source_payload.get("status") != source.get("required_status"):
        raise ValueError("source manifest status changed")
    certificate = payload.get("source_certificate") or {}
    certificate_path = root / str(certificate.get("path", ""))
    if not certificate_path.is_file() or sha256(certificate_path) != certificate.get("sha256"):
        raise ValueError("source certificate missing or hash-mismatched")
    certificate_payload = _json(certificate_path)
    if certificate_payload.get("status") != certificate.get("required_status"):
        raise ValueError("source certificate status changed")
    for record in (payload.get("prior_evidence") or {}).values():
        path = repo_root / str(record.get("path", ""))
        if not path.is_file() or sha256(path) != record.get("sha256"):
            raise ValueError("prior rejection evidence changed")

    if payload.get("chronology") != {
        "initial_history": [2023, 2024],
        "open_evaluation_dates": 56,
        "open_evaluation_months": ["2026-03", "2026-04", "2026-06"],
        "rolling_updates": "strictly_earlier_open_evaluation_dates_only",
        "missing_month_between_april_and_june": "excluded_from_fit_and_evaluation",
        "forbidden": [2025, "2026-05"],
    }:
        raise ValueError("benchmark chronology changed")
    if payload.get("identity") != {
        "model_key": ["mlb_game_pk", "player_id", "game_date", "category", "line"],
        "official_hitter_key": HITTER_KEY,
        "require_unique_non_null_keys": True,
        "doubleheader_game_key_required": True,
    }:
        raise ValueError("benchmark identity contract changed")
    arms = payload.get("probability_arms") or {}
    eb = arms.get("rolling_empirical_bayes_player_rate_pa_200") or {}
    if set(arms) != {"rolling_league_rate", "rolling_empirical_bayes_player_rate_pa_200", "immutable_production"}:
        raise ValueError("probability arm removed or added")
    if eb.get("prior_strength_pa") != 200 or eb.get("selection_source") != "certified_2024_simple_control":
        raise ValueError("empirical-Bayes control was retuned")
    pa = payload.get("pa_volume") or {}
    pa_path = root / str(pa.get("artifact_path", ""))
    if not pa_path.is_file() or sha256(pa_path) != pa.get("sha256") or pa.get("realized_pa_forbidden_as_prediction_input") is not True:
        raise ValueError("PA-volume contract changed")
    market = payload.get("market_contract") or {}
    if market.get("production_comparable") != PRODUCTION_MARKETS or market.get("readiness_only_without_production_comparator") != TOTAL_BASES_MARKETS:
        raise ValueError("market comparison scope changed")
    if market.get("each_market_and_line_scored_separately") is not True or market.get("market_pooling_forbidden") is not True:
        raise ValueError("market separation weakened")
    metrics = payload.get("metrics") or {}
    if metrics != {
        "proper_scores": ["binary_log_loss", "binary_brier"],
        "calibration": ["calibration_intercept", "calibration_slope"],
        "discrimination": ["roc_auc"],
        "uncertainty_unit": "official_game_date",
        "bootstrap_draws": 10000,
        "bootstrap_seed": 260719,
        "probability_clip_epsilon": 1e-12,
        "periods": ["march_april", "june", "pooled_open"],
    }:
        raise ValueError("benchmark metrics or uncertainty changed")
    eligibility = payload.get("eligibility") or {}
    if set(eligibility) != {"proper_scoring_requires_official_pa_above_zero", "zero_pa_is_not_a_model_coverage_claim",
                           "zero_pa_is_not_a_settlement_decision", "identical_key_intersection_for_paired_comparisons",
                           "fallbacks_reported_separately"} or not all(value is True for value in eligibility.values()):
        raise ValueError("benchmark eligibility weakened")
    decision = payload.get("decision_contract") or {}
    if set(decision) != {
        "benchmark_is_diagnostic_not_a_candidate", "no_model_may_be_published_from_this_benchmark",
        "no_policy_or_economic_threshold_may_be_fit", "measured_simplification_limiter_requires_both_paired_interval_uppers_below_zero",
        "measured_simplification_limiter_requires_both_period_point_estimates_below_zero",
        "calibration_point_estimates_must_not_both_move_farther_from_ideal", "discrimination_point_estimate_must_not_regress",
        "total_bases_cannot_drive_a_production_replacement_decision", "economic_analysis_allowed_only_after_predictive_screen_passes",
        "one_next_intervention_maximum", "weak_or_mixed_result_requires_no_intervention",
    } or not all(value is True for value in decision.values()):
        raise ValueError("benchmark decision contract weakened")
    protected = payload.get("protected_invariants") or {}
    if not protected or not all(value is True for value in protected.values()):
        raise ValueError("protected invariant weakened")
    return payload


def load_protocol(path: str | Path, *, evidence_root: str | Path) -> dict[str, Any]:
    return validate_protocol(_json(Path(path)), evidence_root=evidence_root)


def _official_to_training(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "player_id": frame["player_id"].astype(int),
        "out_pa": frame["pa"].astype(int),
        "out_ab": frame["ab"].astype(int),
        "out_hits": frame["hits"].astype(int),
        "out_doubles": frame["doubles"].astype(int),
        "out_triples": frame["triples"].astype(int),
        "out_hr": frame["home_runs"].astype(int),
        "out_bb": frame["walks"].astype(int),
        "out_k": frame["strikeouts"].astype(int),
    })


def rolling_pa_probabilities(
    history: pd.DataFrame, targets: pd.DataFrame, official: pd.DataFrame, *, prior_strength: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    history_counts, _ = normalized_counts(history)
    base = history_counts.copy()
    base["player_id"] = history["player_id"].astype(int).to_numpy()
    grouped = base.groupby("player_id", sort=False)[PA_OUTCOMES].sum()
    league_counts = grouped.sum(axis=0).to_numpy(float)
    player_counts = {int(index): row.to_numpy(float) for index, row in grouped.iterrows()}
    league_parts: list[np.ndarray] = []
    eb_parts: list[np.ndarray] = []
    fallback_parts: list[np.ndarray] = []
    official_dates = pd.to_datetime(official["game_date"], errors="raise")
    for date in sorted(targets["game_date"].astype(str).unique()):
        date_targets = targets[targets["game_date"].astype(str) == date]
        league = league_counts / league_counts.sum()
        league_probability = np.tile(league, (len(date_targets), 1))
        eb_probability = np.empty_like(league_probability)
        fallback = np.zeros(len(date_targets), dtype=bool)
        for index, player_id in enumerate(date_targets["player_id"].astype(int)):
            counts = player_counts.get(int(player_id))
            if counts is None:
                counts = np.zeros(len(PA_OUTCOMES), dtype=float)
                fallback[index] = True
            posterior = counts + float(prior_strength) * league
            eb_probability[index] = posterior / posterior.sum()
        league_parts.append(league_probability)
        eb_parts.append(eb_probability)
        fallback_parts.append(fallback)
        additions = official[official_dates == pd.Timestamp(date)]
        if not additions.empty:
            addition_counts, _ = normalized_counts(_official_to_training(additions))
            league_counts += addition_counts.loc[:, PA_OUTCOMES].sum(axis=0).to_numpy(float)
            addition_counts = addition_counts.copy()
            addition_counts["player_id"] = additions["player_id"].astype(int).to_numpy()
            for player_id, group in addition_counts.groupby("player_id", sort=False):
                vector = group.loc[:, PA_OUTCOMES].sum(axis=0).to_numpy(float)
                player_counts[int(player_id)] = player_counts.get(int(player_id), np.zeros(len(PA_OUTCOMES))) + vector
    return np.vstack(league_parts), np.vstack(eb_parts), np.concatenate(fallback_parts)


def actual_markets(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    return {
        "hits_0.5": (frame["hits"].to_numpy(int) > 0.5).astype(float),
        "hits_1.5": (frame["hits"].to_numpy(int) > 1.5).astype(float),
        "home_runs_0.5": (frame["home_runs"].to_numpy(int) > 0.5).astype(float),
        **{f"total_bases_{line}": (frame["total_bases"].to_numpy(int) > line).astype(float)
           for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)},
    }


def binary_metrics(actual: np.ndarray, probability: np.ndarray) -> dict[str, float | int]:
    y = np.asarray(actual, dtype=float)
    p = np.clip(np.asarray(probability, dtype=float), EPSILON, 1.0 - EPSILON)
    if len(y) == 0 or len(y) != len(p) or not np.isfinite(p).all():
        raise ValueError("invalid binary scoring inputs")
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
        "rows": len(y),
        "positives": int(y.sum()),
        "mean_probability": float(p.mean()),
        "actual_rate": float(y.mean()),
        "binary_log_loss": log_loss,
        "binary_brier": brier,
        "roc_auc": auc,
        "calibration_intercept": intercept,
        "calibration_slope": slope,
    }


def paired_interval(
    actual: np.ndarray, candidate: np.ndarray, baseline: np.ndarray, dates: pd.Series,
    *, metric: str, draws: int, seed: int,
) -> dict[str, float | int]:
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
        raise ValueError("unknown paired metric")
    block = pd.DataFrame({"date": dates.astype(str).to_numpy(), "delta": candidate_loss - baseline_loss}).groupby("date", sort=True)["delta"].agg(["sum", "count"])
    if len(block) < 2:
        raise ValueError("paired interval requires at least two official dates")
    point = float(block["sum"].sum() / block["count"].sum())
    rng = np.random.default_rng(int(seed))
    samples = np.empty(int(draws), dtype=float)
    sums = block["sum"].to_numpy(float)
    counts = block["count"].to_numpy(float)
    for draw in range(int(draws)):
        selected = rng.integers(0, len(block), size=len(block))
        samples[draw] = sums[selected].sum() / counts[selected].sum()
    lower, upper = np.quantile(samples, [0.025, 0.975])
    return {"point": point, "lower": float(lower), "upper": float(upper), "dates": len(block), "draws": int(draws)}


def period_mask(dates: pd.Series, period: str) -> np.ndarray:
    values = dates.astype(str)
    if period == "march_april":
        return values.str.startswith(("2026-03", "2026-04")).to_numpy()
    if period == "june":
        return values.str.startswith("2026-06").to_numpy()
    if period == "pooled_open":
        return np.ones(len(values), dtype=bool)
    raise ValueError(f"unknown period: {period}")
