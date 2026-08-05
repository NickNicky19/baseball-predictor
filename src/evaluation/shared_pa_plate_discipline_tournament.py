"""Deterministic, 2023-only provisional shared-PA plate-discipline tournament.

This module is deliberately research-only.  It consumes the already-spent,
hash-bound 2023 direct-batter panel and its local raw Statcast files.  It does
not fetch data, touch production, open later seasons, or qualify a feature.

The experimental parent is the strict-prior eight-outcome player empirical-
Bayes simplex.  Plate-discipline candidates can adjust only the strikeout and
walk logits.  Every other PA class remains on the same shared simplex, so the
secondary Hits/HR/Total Bases diagnostics are coherent rather than separately
simulated.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from src.evaluation.shared_pa_eb_offset_catboost import PA_OUTCOMES, target_counts


RESEARCH_STATUS = "PROVISIONAL_NON_PROMOTABLE_PENDING_QUALIFIED_SOURCE_CONFIRMATION"
DEVELOPMENT_SEASON = 2023
OUTCOME_INDEX = {name: index for index, name in enumerate(PA_OUTCOMES)}
K_INDEX = OUTCOME_INDEX["strikeout"]
BB_INDEX = OUTCOME_INDEX["walk"]
MATERIALITY_FRACTION = 0.01
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 20260804
L2_PENALTY = 1.0

SWING_DESCRIPTIONS = frozenset(
    {
        "swinging_strike",
        "swinging_strike_blocked",
        "foul",
        "foul_tip",
        "hit_into_play",
        "foul_bunt",
        "missed_bunt",
        "bunt_foul_tip",
    }
)
WHIFF_DESCRIPTIONS = frozenset(
    {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
)
KNOWN_DESCRIPTIONS = frozenset(
    {
        "automatic_ball",
        "automatic_strike",
        "ball",
        "blocked_ball",
        "bunt_foul_tip",
        "called_strike",
        "foul",
        "foul_bunt",
        "foul_tip",
        "hit_by_pitch",
        "hit_into_play",
        "missed_bunt",
        "pitchout",
        "swinging_strike",
        "swinging_strike_blocked",
    }
)
COUNT_COLUMNS = (
    "pd_pitch_count",
    "pd_description_missing_count",
    "pd_swing_count",
    "pd_take_count",
    "pd_contact_count",
    "pd_whiff_count",
    "pd_chase_opportunity_count",
    "pd_chase_swing_count",
    "pd_zone_opportunity_count",
    "pd_zone_swing_count",
    "pd_zone_missing_count",
    "pd_called_strike_count",
)
RATE_SPECS = (
    ("swing", "pd_swing_count", "pd_pitch_count"),
    ("whiff", "pd_whiff_count", "pd_swing_count"),
    ("chase", "pd_chase_swing_count", "pd_chase_opportunity_count"),
    ("zone_swing", "pd_zone_swing_count", "pd_zone_opportunity_count"),
    ("called_strike", "pd_called_strike_count", "pd_take_count"),
)
SUBSTANTIVE_VARIANTS = ("raw_rates", "fold_shrunk_rates", "fold_shrunk_rates_support")
CONTROL_VARIANTS = ("feature_off", "missingness_only", "deterministic_shuffle")
ALL_ARMS = (
    "league_rate",
    "eb_fixed_200",
    "eb_fitted",
    *SUBSTANTIVE_VARIANTS,
    *CONTROL_VARIANTS,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validated_zone(source: pd.Series) -> pd.Series:
    zone = pd.to_numeric(source, errors="coerce")
    invalid = zone.notna() & (~zone.between(1, 14) | ~zone.mod(1).eq(0))
    _require(not invalid.any(), "plate-discipline source contains invalid zone values")
    return zone


def daily_plate_counts(frame: pd.DataFrame, *, player_id: int) -> pd.DataFrame:
    """Return exact regular-season pitch-decision counts by source date."""

    required = {
        "game_date",
        "game_pk",
        "batter",
        "at_bat_number",
        "pitch_number",
        "game_type",
        "description",
        "zone",
    }
    missing = sorted(required.difference(frame.columns))
    _require(not missing, f"plate-discipline source missing columns: {missing}")
    work = frame.loc[:, sorted(required)].copy()
    work["_date"] = pd.to_datetime(work["game_date"], format="%Y-%m-%d", errors="coerce")
    _require(not work["_date"].isna().any(), "plate-discipline source has malformed dates")
    _require(work["_date"].dt.year.eq(DEVELOPMENT_SEASON).all(), "later season entered plate source")
    batter = pd.to_numeric(work["batter"], errors="coerce")
    _require(not batter.isna().any(), "plate-discipline source has missing batter identity")
    _require(batter.astype(int).eq(int(player_id)).all(), "plate-discipline batter identity differs")
    identity = work[["game_pk", "batter", "at_bat_number", "pitch_number"]].apply(
        pd.to_numeric, errors="coerce"
    )
    _require(not identity.isna().any().any(), "plate-discipline pitch identity is incomplete")
    _require(not identity.duplicated().any(), "plate-discipline pitch identity is duplicated")
    work = work.loc[work["game_type"].astype("string").eq("R")].copy()
    descriptions = work["description"].astype("string")
    valid = descriptions.notna() & descriptions.str.len().fillna(0).gt(0)
    observed = set(descriptions.loc[valid].astype(str))
    unknown = sorted(observed.difference(KNOWN_DESCRIPTIONS))
    _require(not unknown, f"unknown plate-discipline descriptions: {unknown}")
    swing = valid & descriptions.isin(SWING_DESCRIPTIONS)
    whiff = valid & descriptions.isin(WHIFF_DESCRIPTIONS)
    take = valid & ~swing
    zone = _validated_zone(work["zone"])
    in_zone = valid & zone.between(1, 9)
    outside = valid & zone.isin([11, 12, 13, 14])
    values = pd.DataFrame(
        {
            "_date": work["_date"],
            "pd_pitch_count": valid.astype(int),
            "pd_description_missing_count": (~valid).astype(int),
            "pd_swing_count": swing.astype(int),
            "pd_take_count": take.astype(int),
            "pd_contact_count": (swing & ~whiff).astype(int),
            "pd_whiff_count": whiff.astype(int),
            "pd_chase_opportunity_count": outside.astype(int),
            "pd_chase_swing_count": (outside & swing).astype(int),
            "pd_zone_opportunity_count": in_zone.astype(int),
            "pd_zone_swing_count": (in_zone & swing).astype(int),
            "pd_zone_missing_count": (valid & zone.isna()).astype(int),
            "pd_called_strike_count": (
                valid & descriptions.eq("called_strike").fillna(False)
            ).astype(int),
        }
    )
    if values.empty:
        return pd.DataFrame(columns=["source_date", *COUNT_COLUMNS])
    daily = values.groupby("_date", sort=True)[list(COUNT_COLUMNS)].sum().cumsum()
    daily.index.name = "source_date"
    return daily.reset_index()


def _raw_key(player_id: int) -> str:
    return f"{DEVELOPMENT_SEASON}\\batter_{int(player_id)}.csv"


def build_strict_prior_plate_matrix(
    targets: pd.DataFrame,
    *,
    raw_root: Path,
    raw_source_sha256: Mapping[str, str],
) -> pd.DataFrame:
    """Build a same-row, strictly-prior count matrix from hash-bound raw files."""

    required = {"game_date", "game_pk", "player_id"}
    missing = sorted(required.difference(targets.columns))
    _require(not missing, f"plate target identity missing: {missing}")
    _require(not targets[["game_pk", "player_id"]].duplicated().any(), "plate targets are duplicated")
    dates = pd.to_datetime(targets["game_date"], format="%Y-%m-%d", errors="coerce")
    _require(not dates.isna().any(), "plate targets contain malformed dates")
    _require(dates.dt.year.eq(DEVELOPMENT_SEASON).all(), "plate targets may contain only spent 2023 development rows")
    pieces: list[pd.DataFrame] = []
    indexed_targets = targets.assign(_target_date=dates, _target_row=np.arange(len(targets)))
    for player_id, player_targets in indexed_targets.groupby("player_id", sort=True):
        player = int(player_id)
        key = _raw_key(player)
        _require(key in raw_source_sha256, f"raw source identity is unbound: {key}")
        path = raw_root / str(DEVELOPMENT_SEASON) / f"batter_{player}.csv"
        _require(path.is_file(), f"raw plate source is absent: {path}")
        _require(sha256_file(path) == raw_source_sha256[key], f"raw plate source hash differs: {key}")
        try:
            source = pd.read_csv(path)
        except pd.errors.EmptyDataError as exc:
            raise ValueError(f"bound raw plate source is empty: {key}") from exc
        daily = daily_plate_counts(source, player_id=player)
        left = player_targets.sort_values("_target_date", kind="mergesort").copy()
        left["_target_date"] = left["_target_date"].astype("datetime64[ns]")
        if daily.empty:
            merged = left
            for name in COUNT_COLUMNS:
                merged[name] = 0
            merged["pd_max_source_date"] = pd.NaT
        else:
            right = daily.rename(columns={"source_date": "pd_max_source_date"})
            right["pd_max_source_date"] = right["pd_max_source_date"].astype("datetime64[ns]")
            merged = pd.merge_asof(
                left,
                right,
                left_on="_target_date",
                right_on="pd_max_source_date",
                direction="backward",
                allow_exact_matches=False,
            )
            merged[list(COUNT_COLUMNS)] = merged[list(COUNT_COLUMNS)].fillna(0)
        pieces.append(merged)
    output = pd.concat(pieces, ignore_index=True).sort_values("_target_row", kind="mergesort")
    output[list(COUNT_COLUMNS)] = output[list(COUNT_COLUMNS)].astype(np.int64)
    max_source = pd.to_datetime(output["pd_max_source_date"], errors="coerce")
    _require(
        not (max_source.notna() & (max_source >= pd.to_datetime(output["game_date"]))).any(),
        "same-day or future pitch entered plate matrix",
    )
    _validate_count_identities(output)
    return output[["game_pk", "player_id", "game_date", "pd_max_source_date", *COUNT_COLUMNS]]


def _validate_count_identities(frame: pd.DataFrame) -> None:
    counts = frame.loc[:, COUNT_COLUMNS].apply(pd.to_numeric, errors="coerce")
    _require(not counts.isna().any().any(), "plate counts are missing or nonnumeric")
    _require((counts >= 0).all().all(), "plate counts are negative")
    _require(np.equal(counts.to_numpy(float), np.floor(counts.to_numpy(float))).all(), "plate counts are nonintegral")
    _require((counts["pd_swing_count"] + counts["pd_take_count"]).eq(counts["pd_pitch_count"]).all(), "swing/take partition differs")
    _require((counts["pd_contact_count"] + counts["pd_whiff_count"]).eq(counts["pd_swing_count"]).all(), "contact/whiff partition differs")
    _require(counts["pd_chase_swing_count"].le(counts["pd_chase_opportunity_count"]).all(), "chase swings exceed opportunities")
    _require(counts["pd_zone_swing_count"].le(counts["pd_zone_opportunity_count"]).all(), "zone swings exceed opportunities")
    _require(counts["pd_called_strike_count"].le(counts["pd_take_count"]).all(), "called strikes exceed takes")


def chronological_splits(frame: pd.DataFrame, folds: int = 4) -> list[tuple[np.ndarray, np.ndarray]]:
    dates = np.array(sorted(pd.to_datetime(frame["game_date"], format="%Y-%m-%d").unique()))
    _require(folds > 0 and len(dates) >= folds + 1, "chronological folds have insufficient dates")
    test_size = len(dates) // (folds + 1)
    _require(test_size > 0, "chronological test fold is empty")
    result: list[tuple[np.ndarray, np.ndarray]] = []
    parsed = pd.to_datetime(frame["game_date"], format="%Y-%m-%d").to_numpy()
    first_test = len(dates) - folds * test_size
    for start in range(first_test, len(dates), test_size):
        validation_dates = dates[start : start + test_size]
        if len(validation_dates) != test_size:
            continue
        train = np.flatnonzero(parsed < validation_dates[0])
        validation = np.flatnonzero(np.isin(parsed, validation_dates))
        _require(len(train) > 0 and len(validation) > 0, "chronological fold is empty")
        result.append((train, validation))
    _require(len(result) == folds, "chronological fold count differs")
    return result


def league_probability(frame: pd.DataFrame) -> np.ndarray:
    counts = target_counts(frame).sum(axis=0)
    _require((counts > 0).all(), "outer training fold lacks a PA class")
    return counts / counts.sum()


def _history_count_matrix(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    names = [f"history_{name}_count" for name in PA_OUTCOMES]
    missing = sorted(set(["history_pa", *names]).difference(frame.columns))
    _require(not missing, f"strict-prior outcome history missing: {missing}")
    counts = frame[names].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    exposure = pd.to_numeric(frame["history_pa"], errors="coerce").to_numpy(float)
    _require(np.isfinite(counts).all() and np.isfinite(exposure).all(), "strict-prior outcome history is nonfinite")
    _require((counts >= 0).all() and (exposure >= 0).all(), "strict-prior outcome history is negative")
    _require(np.array_equal(counts.sum(axis=1), exposure), "strict-prior outcome counts do not equal history PA")
    return counts, exposure


def empirical_bayes_probability(frame: pd.DataFrame, league: np.ndarray, concentration: float) -> np.ndarray:
    counts, exposure = _history_count_matrix(frame)
    result = (counts + concentration * league) / (exposure[:, None] + concentration)
    _require(np.isfinite(result).all() and (result > 0).all(), "empirical-Bayes probability is invalid")
    _require(np.allclose(result.sum(axis=1), 1.0, rtol=0.0, atol=1e-12), "empirical-Bayes simplex differs")
    return result


def _dirichlet_multinomial_score(grouped: np.ndarray, league: np.ndarray, concentration: float) -> float:
    alpha = concentration * league
    total = grouped.sum(axis=1)
    value = 0.0
    for row, row_total in zip(grouped, total):
        value += math.lgamma(concentration) - math.lgamma(concentration + float(row_total))
        value += sum(math.lgamma(float(count) + float(a)) - math.lgamma(float(a)) for count, a in zip(row, alpha))
    return value


def fit_dirichlet_concentration(frame: pd.DataFrame, league: np.ndarray) -> float:
    counts = pd.DataFrame(target_counts(frame), columns=PA_OUTCOMES)
    counts["player_id"] = frame["player_id"].to_numpy()
    grouped = counts.groupby("player_id", sort=True).sum().loc[:, PA_OUTCOMES].to_numpy(float)
    _require(len(grouped) >= 2, "Dirichlet concentration requires two players")
    lower, upper = math.log(0.01), math.log(100_000.0)
    best_log = 0.0
    for points in (121, 81, 81):
        grid = np.linspace(lower, upper, points)
        scores = np.asarray([_dirichlet_multinomial_score(grouped, league, math.exp(float(value))) for value in grid])
        index = int(np.argmax(scores))
        _require(index not in {0, len(grid) - 1}, "Dirichlet concentration reached a safety bound")
        best_log = float(grid[index])
        step = float(grid[1] - grid[0])
        lower, upper = best_log - step, best_log + step
    return float(math.exp(best_log))


def _fit_beta_prior(frame: pd.DataFrame, numerator: str, denominator: str) -> tuple[float, float]:
    ordered = frame.sort_values(["player_id", "game_date"], kind="mergesort")
    latest = ordered.groupby("player_id", sort=True).tail(1)
    n = latest[numerator].to_numpy(float)
    d = latest[denominator].to_numpy(float)
    _require((n <= d).all(), f"{numerator} exceeds {denominator}")
    total = float(d.sum())
    _require(total > 0.0, f"{denominator} has no fold-training support")
    mean = float(n.sum() / total)
    mean = min(max(mean, 1e-6), 1.0 - 1e-6)

    def score(concentration: float) -> float:
        alpha = mean * concentration
        beta = (1.0 - mean) * concentration
        return float(
            sum(
                math.lgamma(float(ni) + alpha)
                + math.lgamma(float(di - ni) + beta)
                - math.lgamma(float(di) + concentration)
                - math.lgamma(alpha)
                - math.lgamma(beta)
                + math.lgamma(concentration)
                for ni, di in zip(n, d)
            )
        )

    logs = np.linspace(math.log(0.1), math.log(100_000.0), 121)
    values = np.asarray([score(math.exp(float(value))) for value in logs])
    index = int(np.argmax(values))
    concentration = float(math.exp(float(logs[index])))
    return mean * concentration, (1.0 - mean) * concentration


@dataclass(frozen=True)
class FeatureTransform:
    variant: str
    names: tuple[str, ...]
    beta_priors: Mapping[str, tuple[float, float]]
    mean: np.ndarray
    scale: np.ndarray
    retained: np.ndarray


def _unscaled_features(
    frame: pd.DataFrame,
    *,
    variant: str,
    beta_priors: Mapping[str, tuple[float, float]],
) -> tuple[list[str], np.ndarray]:
    names: list[str] = []
    columns: list[np.ndarray] = []
    for label, numerator, denominator in RATE_SPECS:
        n = frame[numerator].to_numpy(float)
        d = frame[denominator].to_numpy(float)
        _require((n >= 0).all() and (d >= 0).all() and (n <= d).all(), f"invalid {label} count support")
        missing = d == 0.0
        if variant in {"raw_rates", "deterministic_shuffle"}:
            rate = np.divide(n, d, out=np.zeros_like(n), where=~missing)
            names.append(f"{label}_raw_rate")
            columns.append(rate)
            names.append(f"{label}_zero_support")
            columns.append(missing.astype(float))
        elif variant in {"fold_shrunk_rates", "fold_shrunk_rates_support"}:
            alpha, beta = beta_priors[label]
            rate = (n + alpha) / (d + alpha + beta)
            names.append(f"{label}_fold_shrunk_rate")
            columns.append(rate)
            if variant == "fold_shrunk_rates_support":
                names.append(f"{label}_log1p_support")
                columns.append(np.log1p(d))
                names.append(f"{label}_zero_support")
                columns.append(missing.astype(float))
        elif variant == "missingness_only":
            names.append(f"{label}_zero_support")
            columns.append(missing.astype(float))
        else:
            raise ValueError(f"unknown plate feature variant: {variant}")
    return names, np.column_stack(columns)


def fit_feature_transform(frame: pd.DataFrame, variant: str) -> tuple[FeatureTransform, np.ndarray]:
    _validate_count_identities(frame)
    beta_priors = {
        label: _fit_beta_prior(frame, numerator, denominator)
        for label, numerator, denominator in RATE_SPECS
    }
    names, raw = _unscaled_features(frame, variant=variant, beta_priors=beta_priors)
    mean = raw.mean(axis=0)
    scale = raw.std(axis=0, ddof=0)
    retained = scale > 1e-12
    _require(retained.any(), f"plate feature variant is constant in training: {variant}")
    transform = FeatureTransform(
        variant=variant,
        names=tuple(name for name, keep in zip(names, retained) if keep),
        beta_priors=beta_priors,
        mean=mean,
        scale=np.where(retained, scale, 1.0),
        retained=retained,
    )
    return transform, ((raw - mean) / transform.scale)[:, retained]


def apply_feature_transform(frame: pd.DataFrame, transform: FeatureTransform) -> np.ndarray:
    _validate_count_identities(frame)
    _, raw = _unscaled_features(frame, variant=transform.variant, beta_priors=transform.beta_priors)
    return ((raw - transform.mean) / transform.scale)[:, transform.retained]


def adjusted_probability(base: np.ndarray, features: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
    _require(coefficients.shape == (2, features.shape[1]), "plate coefficient shape differs")
    logits = np.log(base.copy())
    logits[:, K_INDEX] += features @ coefficients[0]
    logits[:, BB_INDEX] += features @ coefficients[1]
    logits -= logits.max(axis=1, keepdims=True)
    probability = np.exp(logits)
    probability /= probability.sum(axis=1, keepdims=True)
    _require(np.isfinite(probability).all() and (probability > 0).all(), "adjusted PA probability is invalid")
    return probability


def _objective(base: np.ndarray, features: np.ndarray, counts: np.ndarray, coefficients: np.ndarray) -> float:
    probability = adjusted_probability(base, features, coefficients)
    return float(-(counts * np.log(probability)).sum() + 0.5 * L2_PENALTY * np.square(coefficients).sum())


def fit_kbb_offset(base: np.ndarray, features: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Fit a deterministic penalized K/BB-only multinomial logit offset."""

    _require(len(base) == len(features) == len(counts), "plate model row count differs")
    p = features.shape[1]
    coefficients = np.zeros((2, p), dtype=float)
    exposure = counts.sum(axis=1)
    for _ in range(50):
        probability = adjusted_probability(base, features, coefficients)
        pk = probability[:, K_INDEX]
        pb = probability[:, BB_INDEX]
        gradient = np.vstack(
            [
                features.T @ (exposure * pk - counts[:, K_INDEX]),
                features.T @ (exposure * pb - counts[:, BB_INDEX]),
            ]
        ) + L2_PENALTY * coefficients
        if float(np.max(np.abs(gradient))) < 1e-8:
            break
        hkk = features.T @ (features * (exposure * pk * (1.0 - pk))[:, None])
        hbb = features.T @ (features * (exposure * pb * (1.0 - pb))[:, None])
        hkb = features.T @ (features * (-exposure * pk * pb)[:, None])
        ridge = L2_PENALTY * np.eye(p)
        hessian = np.block([[hkk + ridge, hkb], [hkb.T, hbb + ridge]])
        step = np.linalg.solve(hessian, gradient.reshape(-1))
        current = _objective(base, features, counts, coefficients)
        accepted = False
        for fraction in (1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125, 0.015625):
            candidate = coefficients - fraction * step.reshape(2, p)
            if _objective(base, features, counts, candidate) < current:
                coefficients = candidate
                accepted = True
                break
        if not accepted:
            break
        if float(np.max(np.abs(fraction * step))) < 1e-8:
            break
    return coefficients


def pooled_pa_distribution(frame: pd.DataFrame) -> dict[int, float]:
    pa = pd.to_numeric(frame["out_pa"], errors="coerce")
    _require(not pa.isna().any() and (pa > 0).all(), "PA opportunity is invalid")
    counts = pa.astype(int).value_counts().sort_index()
    return {int(value): float(count / counts.sum()) for value, count in counts.items()}


def count_pmf(probability: np.ndarray, distributions: Sequence[Mapping[int, float]], max_pa: int) -> np.ndarray:
    output = np.zeros((len(probability), max_pa + 1), dtype=float)
    for row, (q, distribution) in enumerate(zip(probability, distributions)):
        for pa, weight in distribution.items():
            for count in range(pa + 1):
                output[row, count] += weight * math.comb(pa, count) * q**count * (1.0 - q) ** (pa - count)
    _require(np.allclose(output.sum(axis=1), 1.0, rtol=0.0, atol=1e-10), "count PMF is not normalized")
    return output


def _count_losses(observed: np.ndarray, pmf: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rows = np.arange(len(observed))
    _require((observed >= 0).all() and (observed < pmf.shape[1]).all(), "observed count is outside PMF")
    brier = 1.0 + np.square(pmf).sum(axis=1) - 2.0 * pmf[rows, observed]
    log_loss = -np.log(np.clip(pmf[rows, observed], 1e-12, 1.0))
    return brier, log_loss


def _binary_auc(outcome: np.ndarray, probability: np.ndarray) -> float:
    order = np.argsort(probability, kind="mergesort")
    sorted_y = outcome[order]
    sorted_p = probability[order]
    positives = float(sorted_y.sum())
    negatives = float(len(sorted_y) - positives)
    if positives == 0 or negatives == 0:
        return float("nan")
    concordant = 0.0
    seen_negative = 0.0
    start = 0
    while start < len(sorted_p):
        end = start + 1
        while end < len(sorted_p) and sorted_p[end] == sorted_p[start]:
            end += 1
        group_positive = float(sorted_y[start:end].sum())
        group_negative = float(end - start) - group_positive
        concordant += group_positive * (seen_negative + 0.5 * group_negative)
        seen_negative += group_negative
        start = end
    return concordant / (positives * negatives)


def score_count_market(observed: np.ndarray, pmf: np.ndarray) -> dict[str, float | int]:
    brier, log_loss = _count_losses(observed, pmf)
    support = np.arange(pmf.shape[1], dtype=float)
    predicted_mean = pmf @ support
    over_zero = 1.0 - pmf[:, 0]
    binary = (observed > 0).astype(float)
    return {
        "rows": int(len(observed)),
        "brier": float(brier.mean()),
        "log_loss": float(log_loss.mean()),
        "mean_count_bias": float(predicted_mean.mean() - observed.mean()),
        "over_0_5_brier": float(np.mean(np.square(over_zero - binary))),
        "over_0_5_log_loss": float(-np.mean(binary * np.log(np.clip(over_zero, 1e-12, 1.0)) + (1.0 - binary) * np.log(np.clip(1.0 - over_zero, 1e-12, 1.0)))),
        "over_0_5_auc": float(_binary_auc(binary, over_zero)),
    }


def paired_cluster_interval(
    candidate_loss: np.ndarray,
    comparator_loss: np.ndarray,
    cluster: Iterable[Any],
    *,
    draws: int,
    seed: int,
) -> dict[str, float | int]:
    delta = np.asarray(candidate_loss) - np.asarray(comparator_loss)
    grouped = pd.DataFrame({"cluster": list(cluster), "delta": delta, "rows": 1}).groupby("cluster", sort=True).sum()
    _require(len(grouped) >= 2, "cluster interval needs two clusters")
    values = grouped["delta"].to_numpy(float)
    rows = grouped["rows"].to_numpy(float)
    rng = np.random.default_rng(seed)
    samples = np.empty(draws, dtype=float)
    for draw in range(draws):
        chosen = rng.integers(0, len(grouped), size=len(grouped))
        samples[draw] = values[chosen].sum() / rows[chosen].sum()
    return {
        "clusters": int(len(grouped)),
        "draws": int(draws),
        "point": float(delta.mean()),
        "lower": float(np.quantile(samples, 0.025)),
        "upper": float(np.quantile(samples, 0.975)),
    }


def _secondary_metrics(frame: pd.DataFrame, probability: np.ndarray) -> dict[str, dict[str, float]]:
    counts = target_counts(frame)
    exposure = counts.sum(axis=1)
    members = {
        "hits_per_pa": [OUTCOME_INDEX[name] for name in ("single", "double", "triple", "home_run")],
        "home_runs_per_pa": [OUTCOME_INDEX["home_run"]],
    }
    result: dict[str, dict[str, float]] = {}
    for name, indices in members.items():
        observed = counts[:, indices].sum(axis=1)
        q = probability[:, indices].sum(axis=1)
        result[name] = {
            "brier": float((exposure * np.square(q) - 2.0 * observed * q + observed).sum() / exposure.sum()),
            "log_loss": float(-(observed * np.log(np.clip(q, 1e-12, 1.0)) + (exposure - observed) * np.log(np.clip(1.0 - q, 1e-12, 1.0))).sum() / exposure.sum()),
        }
    zero = [OUTCOME_INDEX[name] for name in ("strikeout", "walk", "bip_out", "other_non_ab")]
    tb_counts = np.column_stack(
        [
            counts[:, zero].sum(axis=1),
            counts[:, OUTCOME_INDEX["single"]],
            counts[:, OUTCOME_INDEX["double"]],
            counts[:, OUTCOME_INDEX["triple"]],
            counts[:, OUTCOME_INDEX["home_run"]],
        ]
    )
    tb_probability = np.column_stack(
        [
            probability[:, zero].sum(axis=1),
            probability[:, OUTCOME_INDEX["single"]],
            probability[:, OUTCOME_INDEX["double"]],
            probability[:, OUTCOME_INDEX["triple"]],
            probability[:, OUTCOME_INDEX["home_run"]],
        ]
    )
    result["total_bases_per_pa"] = {
        "brier": float((tb_counts.sum(axis=1) * (1.0 + np.square(tb_probability).sum(axis=1)) - 2.0 * (tb_counts * tb_probability).sum(axis=1)).sum() / tb_counts.sum()),
        "log_loss": float(-(tb_counts * np.log(np.clip(tb_probability, 1e-12, 1.0))).sum() / tb_counts.sum()),
    }
    return result


def run_provisional_tournament(panel: pd.DataFrame, plate: pd.DataFrame) -> dict[str, Any]:
    """Run the fixed finite tournament on spent 2023 development evidence."""

    _require(len(panel) == len(plate), "panel and plate matrix rows differ")
    identity = ["game_pk", "player_id", "game_date"]
    _require(panel[identity].reset_index(drop=True).equals(plate[identity].reset_index(drop=True)), "panel and plate identity rows differ")
    frame = pd.concat([panel.reset_index(drop=True), plate.drop(columns=identity).reset_index(drop=True)], axis=1)
    _validate_count_identities(frame)
    positive = pd.to_numeric(frame["out_pa"], errors="coerce").fillna(0).gt(0)
    frame = frame.loc[positive].reset_index(drop=True)
    folds = chronological_splits(frame, 4)
    evaluated = np.zeros(len(frame), dtype=bool)
    probabilities = {arm: np.full((len(frame), len(PA_OUTCOMES)), np.nan) for arm in ALL_ARMS}
    fold_pa_distribution: list[Mapping[int, float] | None] = [None] * len(frame)
    fold_records: list[dict[str, Any]] = []
    for fold_number, (train_rows, validation_rows) in enumerate(folds, start=1):
        train = frame.iloc[train_rows]
        validation = frame.iloc[validation_rows]
        league = league_probability(train)
        concentration = fit_dirichlet_concentration(train, league)
        base_train = empirical_bayes_probability(train, league, concentration)
        base_validation = empirical_bayes_probability(validation, league, concentration)
        probabilities["league_rate"][validation_rows] = np.tile(league, (len(validation), 1))
        probabilities["eb_fixed_200"][validation_rows] = empirical_bayes_probability(validation, league, 200.0)
        probabilities["eb_fitted"][validation_rows] = base_validation
        probabilities["feature_off"][validation_rows] = base_validation
        counts = target_counts(train)
        for variant in (*SUBSTANTIVE_VARIANTS, "missingness_only"):
            transform, train_features = fit_feature_transform(train, variant)
            validation_features = apply_feature_transform(validation, transform)
            coefficients = fit_kbb_offset(base_train, train_features, counts)
            probabilities[variant][validation_rows] = adjusted_probability(base_validation, validation_features, coefficients)
        transform, train_features = fit_feature_transform(train, "deterministic_shuffle")
        validation_features = apply_feature_transform(validation, transform)
        train_rng = np.random.default_rng(BOOTSTRAP_SEED + 100 * fold_number)
        validation_rng = np.random.default_rng(BOOTSTRAP_SEED + 100 * fold_number + 1)
        shuffled_train = train_features[train_rng.permutation(len(train_features))]
        shuffled_validation = validation_features[validation_rng.permutation(len(validation_features))]
        coefficients = fit_kbb_offset(base_train, shuffled_train, counts)
        probabilities["deterministic_shuffle"][validation_rows] = adjusted_probability(base_validation, shuffled_validation, coefficients)
        distribution = pooled_pa_distribution(train)
        for row in validation_rows:
            fold_pa_distribution[row] = distribution
        evaluated[validation_rows] = True
        fold_records.append(
            {
                "fold": fold_number,
                "train_rows": int(len(train)),
                "validation_rows": int(len(validation)),
                "train_date_max": str(train["game_date"].max()),
                "validation_date_min": str(validation["game_date"].min()),
                "validation_date_max": str(validation["game_date"].max()),
                "fitted_dirichlet_concentration": concentration,
                "pooled_pa_distribution": {str(key): value for key, value in distribution.items()},
            }
        )
    _require(np.array_equal(probabilities["feature_off"][evaluated], probabilities["eb_fitted"][evaluated]), "feature-off control does not equal parent")
    _require(all(np.isfinite(probabilities[arm][evaluated]).all() for arm in ALL_ARMS), "OOF probability is incomplete")
    selection = frame.loc[evaluated].reset_index(drop=True)
    distributions = [fold_pa_distribution[row] for row in np.flatnonzero(evaluated)]
    _require(all(value is not None for value in distributions), "OOF PA distribution is incomplete")
    max_pa = int(pd.to_numeric(frame["out_pa"], errors="raise").max())
    primary: dict[str, Any] = {}
    arm_pmfs: dict[str, dict[str, np.ndarray]] = {arm: {} for arm in ALL_ARMS}
    for market, outcome_column, outcome_index in (
        ("batter_strikeouts", "out_k", K_INDEX),
        ("batter_walks", "out_bb", BB_INDEX),
    ):
        observed = pd.to_numeric(selection[outcome_column], errors="raise").astype(int).to_numpy()
        scores: dict[str, Any] = {}
        losses: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for arm in ALL_ARMS:
            q = probabilities[arm][evaluated, outcome_index]
            pmf = count_pmf(q, distributions, max_pa)
            arm_pmfs[arm][market] = pmf
            scores[arm] = score_count_market(observed, pmf)
            losses[arm] = _count_losses(observed, pmf)
        comparators = ("league_rate", "eb_fixed_200", "eb_fitted")
        strongest = min(comparators, key=lambda arm: float(scores[arm]["log_loss"]))
        variants: dict[str, Any] = {}
        for variant in (*SUBSTANTIVE_VARIANTS, "missingness_only", "deterministic_shuffle"):
            comparison: dict[str, Any] = {}
            passed = True
            for comparator in comparators:
                metrics_by_name: dict[str, Any] = {}
                for metric_index, metric in enumerate(("brier", "log_loss")):
                    candidate_loss = losses[variant][metric_index]
                    comparator_loss = losses[comparator][metric_index]
                    game_interval = paired_cluster_interval(
                        candidate_loss,
                        comparator_loss,
                        selection["game_pk"],
                        draws=BOOTSTRAP_DRAWS,
                        seed=BOOTSTRAP_SEED + metric_index,
                    )
                    player_interval = paired_cluster_interval(
                        candidate_loss,
                        comparator_loss,
                        selection["player_id"],
                        draws=BOOTSTRAP_DRAWS,
                        seed=BOOTSTRAP_SEED + 10 + metric_index,
                    )
                    material = (
                        game_interval["point"] <= -MATERIALITY_FRACTION * float(scores[comparator][metric])
                        and game_interval["upper"] < 0.0
                        and player_interval["upper"] < 0.0
                    )
                    metrics_by_name[metric] = {
                        "game_cluster": game_interval,
                        "player_cluster": player_interval,
                        "one_percent_materiality_passed": bool(material),
                    }
                    passed = passed and material
                calibration = abs(float(scores[variant]["mean_count_bias"])) <= abs(float(scores[comparator]["mean_count_bias"])) + 1e-12
                discrimination = float(scores[variant]["over_0_5_auc"]) >= float(scores[comparator]["over_0_5_auc"]) - 1e-12
                passed = passed and calibration and discrimination
                comparison[comparator] = {
                    "paired": metrics_by_name,
                    "mean_count_calibration_noninferior": bool(calibration),
                    "over_0_5_auc_noninferior": bool(discrimination),
                }
            variants[variant] = {"score": scores[variant], "comparators": comparison, "passed": bool(passed)}
        selected_variant = min(SUBSTANTIVE_VARIANTS, key=lambda arm: float(scores[arm]["log_loss"]))
        controls_passed = not variants["deterministic_shuffle"]["passed"]
        verdict_passed = bool(variants[selected_variant]["passed"] and controls_passed)
        primary[market] = {
            "strongest_eligible_simpler_parent": strongest,
            "parent_scores": {arm: scores[arm] for arm in comparators},
            "selected_finite_variant": selected_variant,
            "variants": variants,
            "feature_off_parent_byte_equal": True,
            "shuffle_control_did_not_pass_family_gate": bool(controls_passed),
            "provisional_market_verdict": (
                "PROVISIONAL_INCREMENTAL_VALUE_DETECTED_NON_PROMOTABLE"
                if verdict_passed
                else "PROVISIONAL_NO_MATERIAL_INCREMENTAL_VALUE_NON_PROMOTABLE"
            ),
        }
    secondary = {
        arm: _secondary_metrics(selection, probabilities[arm][evaluated])
        for arm in ("eb_fitted", *SUBSTANTIVE_VARIANTS)
    }
    return {
        "schema_version": "shared-pa-plate-discipline-provisional-tournament-v1",
        "status": RESEARCH_STATUS,
        "development_season": DEVELOPMENT_SEASON,
        "rows_physical": int(len(panel)),
        "rows_fit_eligible": int(len(frame)),
        "rows_oof": int(evaluated.sum()),
        "initial_training_only_rows": int((~evaluated).sum()),
        "folds": fold_records,
        "candidate_budget": list(SUBSTANTIVE_VARIANTS),
        "controls": list(CONTROL_VARIANTS),
        "primary_markets": primary,
        "secondary_diagnostics_no_rescue": secondary,
        "invariants": {
            "same_rows_all_arms": True,
            "same_folds_all_arms": True,
            "same_pa_distribution_all_arms": True,
            "shared_eight_class_pa_simplex": True,
            "feature_off_equals_parent_exactly": True,
            "2024_opened": False,
            "2025_opened": False,
            "may_2026_opened": False,
            "network_request_performed": False,
            "production_changed": False,
            "promotion_eligible": False,
            "betting_authorized": False,
        },
    }


def synthetic_signal_control() -> bool:
    """Prove the fixed offset learner can recover a deterministic known signal."""

    x = np.linspace(-2.0, 2.0, 400)[:, None]
    base = np.tile(np.asarray([0.22, 0.09, 0.14, 0.05, 0.01, 0.04, 0.40, 0.05]), (len(x), 1))
    truth = adjusted_probability(base, x, np.asarray([[1.1], [-0.8]]))
    exposure = np.full(len(x), 100.0)
    counts = truth * exposure[:, None]
    fitted = fit_kbb_offset(base, x, counts)
    learned = adjusted_probability(base, x, fitted)
    parent_loss = float(-(counts * np.log(base)).sum())
    learned_loss = float(-(counts * np.log(learned)).sum())
    return learned_loss < parent_loss * 0.99 and fitted[0, 0] > 0.0 and fitted[1, 0] < 0.0
