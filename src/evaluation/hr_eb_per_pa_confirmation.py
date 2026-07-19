"""Contracts and scoring for untouched 2025 per-PA HR-rate confirmation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import root
from scipy.special import expit
from sklearn.metrics import roc_auc_score

from src.evaluation.multi_market_foundation import sha256


SCHEMA = "hr-eb-per-pa-2025-confirmation-protocol-v1"
STATUS = "LOCKED_BEFORE_2025_CONFIRMATION_OUTCOMES_OPENED"
RESULT_SCHEMA = "hr-eb-per-pa-2025-confirmation-report-v1"
KEY = ["game_pk", "player_id", "game_date"]
ARMS = ("candidate", "rolling_league", "rolling_raw_player")
EPSILON = 1e-12


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def validate_protocol(payload: dict[str, Any], *, evidence_root: str | Path) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA or payload.get("status") != STATUS:
        raise ValueError("unrecognized 2025 per-PA HR confirmation protocol")
    if payload.get("betting_authorized") is not False or payload.get("production_unchanged") is not True:
        raise ValueError("confirmation protocol altered production or betting status")
    if payload.get("may_2026_opened") is not False or payload.get("confirmation_2025_opened") is not False:
        raise ValueError("confirmation protocol was not locked before sealed outcomes")
    repo_root = Path(__file__).resolve().parents[2]
    parent = payload.get("certified_parent") or {}
    parent_path = repo_root / str(parent.get("path", ""))
    if not parent_path.is_file() or sha256(parent_path) != parent.get("sha256"):
        raise ValueError("certified HR parent missing or hash-mismatched")
    if _json(parent_path).get("status") != parent.get("required_status"):
        raise ValueError("certified HR parent status changed")
    evidence_root = Path(evidence_root)
    for name, record in (payload.get("inputs") or {}).items():
        path = evidence_root / str(record.get("path", ""))
        if not path.is_file() or sha256(path) != record.get("sha256"):
            raise ValueError(f"confirmation input missing or hash-mismatched: {name}")
    dates_path = evidence_root / payload["inputs"]["date_universe"]["path"]
    dates = _json(dates_path)
    expected_dates = list(payload.get("confirmation_dates") or [])
    if dates.get("status") != payload["inputs"]["date_universe"]["required_status"]:
        raise ValueError("date universe status changed")
    if expected_dates != dates.get("confirmation_dates") or len(expected_dates) != 12:
        raise ValueError("untouched confirmation dates changed")
    candidate = payload.get("candidate") or {}
    if candidate != {
        "id": "hr_per_pa_rolling_empirical_bayes_v1",
        "initial_history_seasons": [2023, 2024],
        "rolling_2025_history": "all regular-season player-game PA and HR outcomes from dates strictly earlier than the prediction date",
        "same_date_updates_forbidden": True,
        "prior_strength_pa": 200,
        "formula": "(player_prior_hr + prior_strength_pa * rolling_league_hr_per_pa) / (player_prior_pa + prior_strength_pa)",
    }:
        raise ValueError("per-PA HR candidate definition changed")
    if set((payload.get("comparators") or {})) != {"rolling_league", "rolling_raw_player"}:
        raise ValueError("required simple comparator changed")
    eligibility = payload.get("eligibility") or {}
    if not eligibility or not all(value is True for value in eligibility.values()):
        raise ValueError("confirmation eligibility weakened")
    metrics = payload.get("metrics") or {}
    if metrics != {
        "unit": "official_plate_appearance",
        "proper_scores": ["pa_weighted_binary_log_loss", "pa_weighted_binary_brier"],
        "calibration": ["pa_weighted_intercept", "pa_weighted_slope", "pa_weighted_bias"],
        "discrimination": "pa_weighted_roc_auc",
        "bootstrap_unit": "official_game_date",
        "bootstrap_draws": 20000,
        "bootstrap_seed": 17,
        "interval": "paired_two_sided_95_percentile",
    }:
        raise ValueError("confirmation metrics or uncertainty changed")
    gate = payload.get("material_improvement_gate") or {}
    if gate.get("all_conditions_required") is not True or not all(
        value is True for key, value in gate.items() if key != "all_conditions_required"
    ):
        raise ValueError("material-improvement gate weakened")
    protected = payload.get("protected_invariants") or {}
    if not protected or not all(value is True for value in protected.values()):
        raise ValueError("protected confirmation invariant weakened")
    return payload


def load_protocol(path: str | Path, *, evidence_root: str | Path) -> dict[str, Any]:
    return validate_protocol(_json(Path(path)), evidence_root=evidence_root)


def validate_source(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"builder_schema", "season", "game_date", "game_pk", "player_id", "out_pa", "out_hr"}
    if not required.issubset(frame.columns):
        raise ValueError(f"training source lacks columns: {sorted(required - set(frame.columns))}")
    out = frame[list(required)].copy()
    out["season"] = pd.to_numeric(out["season"], errors="raise").astype(int)
    out["game_pk"] = pd.to_numeric(out["game_pk"], errors="raise").astype(int)
    out["player_id"] = pd.to_numeric(out["player_id"], errors="raise").astype(int)
    out["out_pa"] = pd.to_numeric(out["out_pa"], errors="raise").astype(int)
    out["out_hr"] = pd.to_numeric(out["out_hr"], errors="raise").astype(int)
    out["game_date"] = pd.to_datetime(out["game_date"], errors="raise").dt.strftime("%Y-%m-%d")
    if sorted(out["season"].unique()) != [2023, 2024, 2025]:
        raise ValueError("training seasons changed")
    if set(out["builder_schema"].astype(str).unique()) != {"a3.2"}:
        raise ValueError("training builder schema changed")
    if out[KEY].isna().any().any() or out.duplicated(KEY).any():
        raise ValueError("training player-game identity is null or duplicated")
    if (out["out_pa"] < 0).any() or (out["out_hr"] < 0).any() or (out["out_hr"] > out["out_pa"]).any():
        raise ValueError("official PA/HR outcome is impossible")
    return out.sort_values(KEY).reset_index(drop=True)


def build_predictions(source: pd.DataFrame, confirmation_dates: list[str], prior_strength: float) -> tuple[pd.DataFrame, dict[str, int]]:
    history = source[source["season"].isin([2023, 2024])]
    league_pa = float(history["out_pa"].sum())
    league_hr = float(history["out_hr"].sum())
    if league_pa <= 0 or not (0 <= league_hr <= league_pa):
        raise ValueError("initial HR history is invalid")
    grouped = history.groupby("player_id", sort=False)[["out_pa", "out_hr"]].sum()
    player = {int(pid): (float(row.out_pa), float(row.out_hr)) for pid, row in grouped.iterrows()}
    rows: list[dict[str, Any]] = []
    zero_pa = 0
    fallback = 0
    data_2025 = source[source["season"].eq(2025)]
    confirmation = set(confirmation_dates)
    for date in sorted(data_2025["game_date"].unique()):
        same_date = data_2025[data_2025["game_date"].eq(date)].sort_values(KEY)
        if date in confirmation:
            league_rate = league_hr / league_pa
            for record in same_date.itertuples(index=False):
                if int(record.out_pa) == 0:
                    zero_pa += 1
                    continue
                counts = player.get(int(record.player_id))
                if counts is None or counts[0] <= 0:
                    raw = league_rate
                    player_pa = player_hr = 0.0
                    fallback += 1
                else:
                    player_pa, player_hr = counts
                    raw = player_hr / player_pa
                candidate = (player_hr + prior_strength * league_rate) / (player_pa + prior_strength)
                rows.append({
                    "game_pk": int(record.game_pk), "player_id": int(record.player_id), "game_date": date,
                    "out_pa": int(record.out_pa), "out_hr": int(record.out_hr),
                    "candidate": candidate, "rolling_league": league_rate,
                    "rolling_raw_player": raw, "prior_player_pa": player_pa,
                    "prior_player_hr": player_hr, "prior_league_pa": league_pa,
                    "prior_league_hr": league_hr,
                })
        # Update only after every prediction for this date has been emitted.
        league_pa += float(same_date["out_pa"].sum())
        league_hr += float(same_date["out_hr"].sum())
        for pid, group in same_date.groupby("player_id", sort=False):
            old_pa, old_hr = player.get(int(pid), (0.0, 0.0))
            player[int(pid)] = (old_pa + float(group["out_pa"].sum()), old_hr + float(group["out_hr"].sum()))
    out = pd.DataFrame(rows).sort_values(KEY).reset_index(drop=True)
    if out.empty or out[KEY].isna().any().any() or out.duplicated(KEY).any():
        raise ValueError("confirmation prediction universe is empty or malformed")
    if sorted(out["game_date"].unique()) != confirmation_dates:
        raise ValueError("one or more confirmation dates has no gradeable row")
    for arm in ARMS:
        probability = out[arm]
        if probability.isna().any() or not probability.between(0, 1, inclusive="both").all():
            raise ValueError(f"invalid {arm} probability")
    # The rolling league and shrunk candidate are interior when the history
    # contains both HR and non-HR PA.  The declared raw-player comparator may
    # legitimately be exactly 0 or 1; scoring clips it only for finite log loss.
    if not out["candidate"].between(0, 1, inclusive="neither").all():
        raise ValueError("candidate probability reached a boundary")
    if not out["rolling_league"].between(0, 1, inclusive="neither").all():
        raise ValueError("rolling league probability reached a boundary")
    return out, {"eligible_rows": len(out), "zero_pa_rows": zero_pa, "candidate_fallback_rows": fallback}


def metrics(frame: pd.DataFrame, arm: str) -> dict[str, float | int]:
    pa = frame["out_pa"].to_numpy(float)
    hr = frame["out_hr"].to_numpy(float)
    p = np.clip(frame[arm].to_numpy(float), EPSILON, 1 - EPSILON)
    failures = pa - hr
    total = float(pa.sum())
    log_loss = float(-(hr * np.log(p) + failures * np.log1p(-p)).sum() / total)
    brier = float((hr * np.square(1 - p) + failures * np.square(p)).sum() / total)
    y = np.concatenate([np.ones(len(p)), np.zeros(len(p))])
    scores = np.concatenate([p, p])
    weights = np.concatenate([hr, failures])
    auc = float(roc_auc_score(y, scores, sample_weight=weights))
    rate = hr / pa
    logit = np.log(p) - np.log1p(-p)
    design = np.column_stack([np.ones(len(p)), logit])

    def score(parameters: np.ndarray) -> np.ndarray:
        return design.T @ (pa * (expit(design @ parameters) - rate))

    def jacobian(parameters: np.ndarray) -> np.ndarray:
        fitted = expit(design @ parameters)
        return design.T @ ((pa * fitted * (1 - fitted))[:, None] * design)

    fit = root(score, np.array([0.0, 1.0]), jac=jacobian, method="hybr")
    if not fit.success or not np.isfinite(fit.x).all():
        intercept = slope = float("nan")
    else:
        intercept, slope = map(float, fit.x)
    return {
        "player_games": len(frame), "plate_appearances": int(total), "home_runs": int(hr.sum()),
        "mean_probability": float(np.average(p, weights=pa)), "actual_rate": float(hr.sum() / total),
        "pa_weighted_binary_log_loss": log_loss, "pa_weighted_binary_brier": brier,
        "pa_weighted_roc_auc": auc, "pa_weighted_calibration_intercept": intercept,
        "pa_weighted_calibration_slope": slope, "pa_weighted_bias": float(np.average(p - rate, weights=pa)),
    }


def score_sums(frame: pd.DataFrame, arm: str) -> tuple[float, float, float]:
    pa = frame["out_pa"].to_numpy(float)
    hr = frame["out_hr"].to_numpy(float)
    p = np.clip(frame[arm].to_numpy(float), EPSILON, 1 - EPSILON)
    failure = pa - hr
    return (
        float(-(hr * np.log(p) + failure * np.log1p(-p)).sum()),
        float((hr * np.square(1 - p) + failure * np.square(p)).sum()),
        float(pa.sum()),
    )


def paired_intervals(frame: pd.DataFrame, candidate: str, baseline: str, *, dates: list[str], draws: int, seed: int) -> dict[str, Any]:
    by_date: dict[str, tuple[float, float, float, float, float]] = {}
    for date in dates:
        part = frame[frame["game_date"].eq(date)]
        c_log, c_brier, pa = score_sums(part, candidate)
        b_log, b_brier, b_pa = score_sums(part, baseline)
        if pa != b_pa or pa <= 0:
            raise ValueError("bootstrap date has inconsistent exposure")
        by_date[date] = (c_log, c_brier, b_log, b_brier, pa)
    matrix = np.asarray([by_date[date] for date in dates], dtype=float)
    rng = np.random.default_rng(seed)
    log_delta = np.empty(draws)
    brier_delta = np.empty(draws)
    for index in range(draws):
        sampled = matrix[rng.integers(0, len(matrix), len(matrix))]
        exposure = sampled[:, 4].sum()
        log_delta[index] = (sampled[:, 0].sum() - sampled[:, 2].sum()) / exposure
        brier_delta[index] = (sampled[:, 1].sum() - sampled[:, 3].sum()) / exposure
    return {
        "candidate_minus_baseline_log_loss_95": [float(x) for x in np.quantile(log_delta, [0.025, 0.975])],
        "candidate_minus_baseline_brier_95": [float(x) for x in np.quantile(brier_delta, [0.025, 0.975])],
        "valid_draws": int(np.isfinite(log_delta).sum()) if np.isfinite(brier_delta).all() else 0,
    }


def decide(report: dict[str, Any]) -> dict[str, Any]:
    comparisons = report["comparisons"]
    full_metrics = report["periods"]["full"]["metrics"]
    parts: dict[str, bool] = {}
    for baseline in ("rolling_league", "rolling_raw_player"):
        interval = comparisons[baseline]["intervals"]
        parts[f"{baseline}_log_loss_upper_below_zero"] = interval["candidate_minus_baseline_log_loss_95"][1] < 0
        parts[f"{baseline}_brier_upper_below_zero"] = interval["candidate_minus_baseline_brier_95"][1] < 0
        parts[f"{baseline}_auc_noninferior"] = full_metrics["candidate"]["pa_weighted_roc_auc"] >= full_metrics[baseline]["pa_weighted_roc_auc"]
        for half in ("early", "late"):
            delta = report["periods"][half]["deltas"][baseline]
            parts[f"{half}_{baseline}_log_loss_point_below_zero"] = delta["pa_weighted_binary_log_loss"] < 0
            parts[f"{half}_{baseline}_brier_point_below_zero"] = delta["pa_weighted_binary_brier"] < 0
    candidate_cal = full_metrics["candidate"]
    raw_cal = full_metrics["rolling_raw_player"]
    finite = np.isfinite([
        candidate_cal["pa_weighted_calibration_intercept"], candidate_cal["pa_weighted_calibration_slope"],
        raw_cal["pa_weighted_calibration_intercept"], raw_cal["pa_weighted_calibration_slope"],
    ]).all()
    parts["calibration_available"] = bool(finite)
    parts["calibration_not_both_farther_from_ideal"] = bool(finite and not (
        abs(candidate_cal["pa_weighted_calibration_intercept"]) > abs(raw_cal["pa_weighted_calibration_intercept"])
        and abs(candidate_cal["pa_weighted_calibration_slope"] - 1) > abs(raw_cal["pa_weighted_calibration_slope"] - 1)
    ))
    parts["all_bootstrap_draws_valid"] = all(
        comparisons[name]["intervals"]["valid_draws"] == report["bootstrap_draws"]
        for name in comparisons
    )
    parts["exact_eligible_coverage"] = report["coverage"]["eligible_rows"] == report["prediction_rows"]
    return {
        "parts": {key: bool(value) for key, value in parts.items()},
        "per_pa_component_confirmed": bool(all(parts.values())),
        "full_game_probability_confirmed": False,
        "production_install_permitted": False,
        "betting_authorized": False,
    }
