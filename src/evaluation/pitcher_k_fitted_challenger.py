"""Leakage-safe fitted pitcher-K challenger; never wired into production."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.special import gammaln
from scipy.stats import nbinom, poisson
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


SCHEMA = "pitcher-k-fitted-challenger-protocol-v1"
STATUS = "LOCKED_BEFORE_FITTED_K_CHALLENGER"
KEY = ["game_pk", "player_id", "game_date"]
PREGAME_FEATURES = [
    "is_home", "throws", "pit_ip", "pit_k9", "pit_bb9", "pit_hr9", "pit_gs",
    "pit_recent_ip", "pit_recent_k9", "pit_recent_bb9", "pit_recent_hr9",
    "pit_recent_gs", "pit_recent_ip_per_gs", "park_hits_factor", "park_hr_factor",
    "park_runs_factor", "park_resolved", "weather_temp", "weather_wind",
    "weather_is_dome", "weather_resolved", "umpire_resolved", "has_prior_data",
]
OUTCOMES = ["out_ip", "out_k", "out_bb", "out_hr"]
OPEN_MONTHS = {"2026-03", "2026-04", "2026-06"}
LINES = (4.5, 5.5, 6.5)
EPSILON = 1e-12


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _bound(root: Path, rel: object) -> Path:
    value = (root / str(rel)).resolve()
    try:
        value.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("bound source path escapes root") from exc
    return value


def _verify(root: Path, record: dict[str, Any]) -> Path:
    path = _bound(root, record.get("path"))
    if not path.is_file() or sha256(path) != record.get("sha256"):
        raise ValueError(f"hash mismatch: {record.get('path')}")
    return path


def validate_protocol(payload: dict[str, Any], *, repo_root: Path, evidence_root: Path) -> None:
    if payload.get("schema_version") != SCHEMA or payload.get("status") != STATUS:
        raise ValueError("unrecognized fitted pitcher-K challenger protocol")
    if any(payload.get(key) is not value for key, value in {
        "betting_authorized": False, "production_unchanged": True,
        "may_2026_opened": False, "economic_evidence_eligible": False,
    }.items()):
        raise ValueError("challenger protocol weakens protected scope")
    chronology = payload.get("chronology") or {}
    expected_chronology = {
        "initial_fit": [2023], "selection": [2024], "untouched_confirmation": [2025],
        "open_2026_evaluation": ["2026-03", "2026-04", "2026-06"],
        "forbidden": ["2026-05"], "refit_for_open_evaluation_only_after_confirmation_pass": [2023, 2024, 2025],
    }
    if chronology != expected_chronology:
        raise ValueError("challenger chronology changed")
    candidate = payload.get("candidate") or {}
    if candidate.get("features") != PREGAME_FEATURES or set(candidate.get("forbidden_features") or []).intersection(PREGAME_FEATURES):
        raise ValueError("challenger feature contract changed")
    if set(candidate.get("forbidden_features") or []) != {"game_pk", "player_id", "player_name", *OUTCOMES}:
        raise ValueError("challenger forbidden feature contract changed")
    if candidate.get("regularization_alpha_grid") != [0.01, 0.1, 1.0, 10.0] or candidate.get("dispersion_grid") != [0.0, 0.05, 0.1, 0.2]:
        raise ValueError("challenger selection grid changed")
    if candidate.get("no_calibration_or_post_fit_adjustment") is not True:
        raise ValueError("challenger allows post-fit calibration")
    evaluation = payload.get("evaluation") or {}
    if evaluation.get("lines") != list(LINES) or evaluation.get("market_pooling_forbidden") is not True or evaluation.get("economic_scoring_forbidden") is not True:
        raise ValueError("challenger evaluation contract changed")
    if evaluation.get("required_open_market_rows") != 966 or evaluation.get("required_open_pitcher_games") != 765:
        raise ValueError("challenger open denominator changed")
    if (evaluation.get("uncertainty") or {}) != {"method": "date_cluster_bootstrap_percentile", "draws": 10000, "seed": 20260719, "confidence_level": 0.95}:
        raise ValueError("challenger uncertainty contract changed")
    if not all((payload.get("protected_invariants") or {}).values()):
        raise ValueError("challenger invariant weakened")
    for record in (payload.get("prior_evidence") or {}).values():
        base = repo_root if str(record.get("path", "")).startswith("reports/") else evidence_root
        _verify(base, record)
    for name in ("pre2026_training", "open_date_manifest", "open_benchmark_rows", "open_official_outcomes"):
        _verify(evidence_root, (payload.get("inputs") or {}).get(name) or {})


def load_protocol(path: str | Path, *, repo_root: Path, evidence_root: Path) -> dict[str, Any]:
    payload = _json(Path(path))
    validate_protocol(payload, repo_root=repo_root, evidence_root=evidence_root)
    return payload


def _read_training(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = sorted(set(KEY + PREGAME_FEATURES + OUTCOMES) - set(frame.columns))
    if missing:
        raise ValueError(f"pitcher training missing columns: {missing}")
    frame = frame.copy()
    frame["game_date"] = pd.to_datetime(frame.game_date, errors="raise").dt.strftime("%Y-%m-%d")
    for column in ("game_pk", "player_id"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(int)
    if frame[KEY].isna().any().any() or frame.duplicated(KEY).any():
        raise ValueError("pitcher training key invalid")
    return frame


def _eligible_training(frame: pd.DataFrame) -> pd.DataFrame:
    out_ip = pd.to_numeric(frame.out_ip, errors="raise")
    out_k = pd.to_numeric(frame.out_k, errors="raise")
    if out_ip.isna().any() or out_k.isna().any() or (out_ip < 0).any() or (out_k < 0).any():
        raise ValueError("training outcome is invalid")
    eligible = frame.loc[out_ip.gt(0)].copy()
    if eligible.empty:
        raise ValueError("no positive-IP historical pitcher rows")
    eligible["out_k"] = out_k.loc[eligible.index].astype(int)
    return eligible


def load_pre2026(protocol: dict[str, Any], *, evidence_root: Path) -> pd.DataFrame:
    record = protocol["inputs"]["pre2026_training"]
    frame = _read_training(_verify(evidence_root, record))
    years = set(pd.to_datetime(frame.game_date).dt.year.unique())
    if years != {2023, 2024, 2025} or len(frame) != record["rows"]:
        raise ValueError("pre-2026 training chronology or denominator changed")
    eligible = _eligible_training(frame)
    if len(eligible) != record["positive_ip_rows"]:
        raise ValueError("pre-2026 positive-IP training denominator changed")
    return eligible


def load_open_2026(protocol: dict[str, Any], *, evidence_root: Path) -> pd.DataFrame:
    manifest_record = protocol["inputs"]["open_date_manifest"]
    manifest = _json(_verify(evidence_root, manifest_record))
    dates = [str(value) for value in manifest.get("dates") or []]
    if len(dates) != 56 or len(set(dates)) != 56 or any(date[:7] not in OPEN_MONTHS for date in dates):
        raise ValueError("open-date source manifest opened May or changed")
    root = _bound(evidence_root, protocol["inputs"]["open_daily_pitcher_root"])
    daily_paths = [root / f"pitchers_{date}.csv" for date in dates]
    if any(not path.is_file() for path in daily_paths):
        raise ValueError("an approved open-date pitcher source file is missing")
    frame = pd.concat([_read_training(path) for path in daily_paths], ignore_index=True)
    if set(frame.game_date.unique()) != set(dates) or frame.duplicated(KEY).any():
        raise ValueError("open pitcher sources changed identity or accessed a non-approved date")
    return frame


def _pipeline(alpha: float) -> Pipeline:
    numeric = [feature for feature in PREGAME_FEATURES if feature != "throws"]
    transforms = ColumnTransformer([
        ("numeric", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
        ("throws", Pipeline([("impute", SimpleImputer(strategy="most_frequent")), ("encode", OneHotEncoder(handle_unknown="ignore"))]), ["throws"]),
    ])
    return Pipeline([("transform", transforms), ("model", PoissonRegressor(alpha=float(alpha), max_iter=1000))])


def fit_mean_model(frame: pd.DataFrame, *, alpha: float) -> Pipeline:
    model = _pipeline(alpha)
    model.fit(frame[PREGAME_FEATURES], pd.to_numeric(frame.out_k, errors="raise"))
    return model


def predict_mean(model: Pipeline, frame: pd.DataFrame) -> np.ndarray:
    value = np.asarray(model.predict(frame[PREGAME_FEATURES]), dtype=float)
    if not np.isfinite(value).all() or (value < 0).any():
        raise ValueError("fitted pitcher-K mean is invalid")
    return value


def count_nll(y: np.ndarray, mean: np.ndarray, dispersion: float) -> float:
    y = np.asarray(y, dtype=int)
    mean = np.asarray(mean, dtype=float)
    if dispersion <= 0:
        values = y * np.log(np.clip(mean, EPSILON, None)) - mean - gammaln(y + 1)
    else:
        r = 1.0 / float(dispersion)
        p = r / (r + mean)
        values = gammaln(y + r) - gammaln(r) - gammaln(y + 1) + r * np.log(p) + y * np.log1p(-p)
    return float(-np.mean(values))


def tail_probability(mean: np.ndarray, line: float, dispersion: float) -> np.ndarray:
    threshold = int(np.ceil(float(line))) - 1
    if dispersion <= 0:
        result = poisson.sf(threshold, mean)
    else:
        r = 1.0 / float(dispersion)
        p = r / (r + mean)
        result = nbinom.sf(threshold, r, p)
    result = np.asarray(result, dtype=float)
    if not np.isfinite(result).all() or (result < 0).any() or (result > 1).any():
        raise ValueError("fitted pitcher-K tail probability is invalid")
    return result


def empirical_pmf(frame: pd.DataFrame) -> dict[int, float]:
    values = pd.to_numeric(frame.out_k, errors="raise").astype(int)
    counts = values.value_counts().sort_index()
    return {int(key): float(value / len(values)) for key, value in counts.items()}


def empirical_tail(pmf: dict[int, float], line: float) -> float:
    return float(sum(prob for count, prob in pmf.items() if count > line))


def binary_metrics(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(probability, dtype=float), EPSILON, 1.0 - EPSILON)
    return {
        "brier": float(np.mean((p - y) ** 2)),
        "log_loss": float(-np.mean(y * np.log(p) + (1.0 - y) * np.log1p(-p))),
        "mean_probability_bias": float(np.mean(p - y)),
        "auc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else float("nan"),
    }


def calibration_rows(y: np.ndarray, p: np.ndarray, *, label: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    bins = np.digitize(p, np.linspace(0.0, 1.0, 11), right=False) - 1
    bins = np.clip(bins, 0, 9)
    for index in range(10):
        mask = bins == index
        if mask.any():
            values.append({"comparator": label, "bin": int(index), "n": int(mask.sum()), "mean_probability": float(np.mean(p[mask])), "actual_rate": float(np.mean(y[mask]))})
    return values


def paired_bootstrap(frame: pd.DataFrame, *, candidate: str, baseline: str, draws: int, seed: int) -> dict[str, float | int]:
    dates = sorted(frame.game_date.unique())
    if len(dates) < 2:
        raise ValueError("date-cluster uncertainty needs at least two dates")
    candidate_p = np.clip(frame[candidate].to_numpy(float), EPSILON, 1 - EPSILON)
    baseline_p = np.clip(frame[baseline].to_numpy(float), EPSILON, 1 - EPSILON)
    y = frame.actual_over.to_numpy(float)
    daily = pd.DataFrame({
        "date": frame.game_date.to_numpy(),
        "n": 1,
        "brier_delta": (baseline_p - y) ** 2 - (candidate_p - y) ** 2,
        "log_loss_delta": -y * np.log(baseline_p) - (1-y) * np.log1p(-baseline_p) + y * np.log(candidate_p) + (1-y) * np.log1p(-candidate_p),
    }).groupby("date", sort=True).sum()
    rng = np.random.default_rng(int(seed))
    choice = rng.integers(0, len(daily), size=(int(draws), len(daily)))
    denom = daily.n.to_numpy(float)[choice].sum(axis=1)
    brier = daily.brier_delta.to_numpy(float)[choice].sum(axis=1) / denom
    loss = daily.log_loss_delta.to_numpy(float)[choice].sum(axis=1) / denom
    return {
        "baseline": baseline, "candidate": candidate, "dates": int(len(daily)), "draws": int(draws),
        "brier_improvement_point": float(daily.brier_delta.sum() / daily.n.sum()),
        "brier_improvement_p2_5": float(np.quantile(brier, .025)), "brier_improvement_p97_5": float(np.quantile(brier, .975)),
        "log_loss_improvement_point": float(daily.log_loss_delta.sum() / daily.n.sum()),
        "log_loss_improvement_p2_5": float(np.quantile(loss, .025)), "log_loss_improvement_p97_5": float(np.quantile(loss, .975)),
    }


def choose_parameters(training_2023: pd.DataFrame, selection_2024: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, Any]:
    candidate = protocol["candidate"]
    values: list[dict[str, float]] = []
    y = selection_2024.out_k.to_numpy(int)
    for alpha in candidate["regularization_alpha_grid"]:
        model = fit_mean_model(training_2023, alpha=float(alpha))
        mean = predict_mean(model, selection_2024)
        for dispersion in candidate["dispersion_grid"]:
            values.append({"alpha": float(alpha), "dispersion": float(dispersion), "selection_count_nll": count_nll(y, mean, float(dispersion))})
    values.sort(key=lambda row: (row["selection_count_nll"], row["alpha"], row["dispersion"]))
    return {"selected": values[0], "grid": values}


def market_frame(frame: pd.DataFrame, mean: np.ndarray, dispersion: float, *, baseline_pmf: dict[int, float]) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for line in LINES:
        actual = (frame.out_k.to_numpy(int) > line).astype(int)
        pieces.append(pd.DataFrame({
            "game_pk": frame.game_pk.to_numpy(int), "player_id": frame.player_id.to_numpy(int), "game_date": frame.game_date.to_numpy(str),
            "line": float(line), "actual_over": actual, "candidate_p_over": tail_probability(mean, line, dispersion),
            "simple_p_over": empirical_tail(baseline_pmf, line),
        }))
    return pd.concat(pieces, ignore_index=True)


def score_market_rows(rows: pd.DataFrame, *, baseline: str, protocol: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    uncertainty = protocol["evaluation"]["uncertainty"]
    scored, calibration, paired = [], [], []
    for line, group in rows.groupby("line", sort=True):
        y = group.actual_over.to_numpy(float)
        candidate = group.candidate_p_over.to_numpy(float)
        comparator = group[baseline].to_numpy(float)
        scored.append({"line": float(line), "comparator": "candidate", **binary_metrics(y, candidate)})
        scored.append({"line": float(line), "comparator": baseline, **binary_metrics(y, comparator)})
        calibration.extend(calibration_rows(y, candidate, label=f"candidate:{line}"))
        calibration.extend(calibration_rows(y, comparator, label=f"{baseline}:{line}"))
        paired.append({"line": float(line), **paired_bootstrap(group, candidate="candidate_p_over", baseline=baseline, draws=int(uncertainty["draws"]), seed=int(uncertainty["seed"]) + int(line * 10))})
    return pd.DataFrame(scored), pd.DataFrame(calibration), paired


def confirmation_pass(selection: dict[str, Any], metrics: pd.DataFrame, paired: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
    candidate = metrics.set_index(["line", "comparator"])
    passed_lines, line_details = 0, []
    for row in paired:
        line = float(row["line"])
        c = candidate.loc[(line, "candidate")]
        b = candidate.loc[(line, "simple_p_over")]
        better_ci = row["brier_improvement_p2_5"] > 0 and row["log_loss_improvement_p2_5"] > 0
        not_both_worse = not (c.brier > b.brier and c.log_loss > b.log_loss)
        calibration_ok = abs(c.mean_probability_bias) <= abs(b.mean_probability_bias) + 1e-12
        auc_ok = c.auc + 1e-12 >= b.auc
        if better_ci:
            passed_lines += 1
        line_details.append({"line": line, "better_ci": bool(better_ci), "not_both_worse": bool(not_both_worse), "calibration_ok": bool(calibration_ok), "auc_ok": bool(auc_ok)})
    count_ok = selection["confirmation_count_nll_candidate"] < selection["confirmation_count_nll_simple"]
    passed = bool(count_ok and passed_lines >= 2 and all(item["not_both_worse"] and item["calibration_ok"] and item["auc_ok"] for item in line_details))
    return passed, {"count_nll_improved": bool(count_ok), "positive_ci_lines": int(passed_lines), "line_details": line_details}
