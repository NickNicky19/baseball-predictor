"""Pure statistics for the open-period per-PA hitter-skill diagnostic."""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, log2
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ResidualInterval:
    lower: float
    upper: float
    valid_draws: int


def fit_selector_baselines(
    frame: pd.DataFrame,
    *,
    selector_dates: Iterable[str],
    confirmation_dates: Iterable[str],
) -> tuple[float, dict[int, float]]:
    """Fit global and slot-only hit rates from selector outcomes only."""

    rows = validate_trials(frame)
    selector_set = {str(value) for value in selector_dates}
    confirmation_set = {str(value) for value in confirmation_dates}
    if not selector_set or not confirmation_set or selector_set & confirmation_set:
        raise ValueError("selector and confirmation dates must be non-empty and disjoint")
    if max(selector_set) >= min(confirmation_set):
        raise ValueError("selector dates must strictly precede confirmation dates")
    if set(rows.official_game_date.astype(str).unique()) - (selector_set | confirmation_set):
        raise ValueError("baseline frame contains a date outside selector/confirmation")
    selector = rows[rows.official_game_date.astype(str).isin(selector_set)]
    if selector.empty:
        raise ValueError("selector baseline has no rows")
    global_rate = float(selector.official_hits.sum() / selector.official_pa.sum())
    slot_rates: dict[int, float] = {}
    for slot in range(1, 10):
        group = selector[selector.official_lineup_slot.eq(slot)]
        if group.empty or group.official_pa.sum() <= 0:
            raise ValueError(f"selector baseline has no exposure for lineup slot {slot}")
        slot_rates[slot] = float(group.official_hits.sum() / group.official_pa.sum())
    return global_rate, slot_rates


def add_selector_baseline_probabilities(
    frame: pd.DataFrame,
    global_rate: float,
    slot_rates: dict[int, float],
) -> pd.DataFrame:
    rows = validate_trials(frame)
    if not np.isfinite(global_rate) or not 0.0 <= global_rate <= 1.0:
        raise ValueError("selector global baseline lies outside [0,1]")
    if set(slot_rates) != set(range(1, 10)) or any(
        not np.isfinite(value) or not 0.0 <= value <= 1.0
        for value in slot_rates.values()
    ):
        raise ValueError("selector slot baselines are incomplete or invalid")
    rows["selector_global_probability"] = float(global_rate)
    rows["selector_slot_probability"] = rows.official_lineup_slot.map(slot_rates)
    if rows.selector_slot_probability.isna().any():
        raise ValueError("a trial lacks a selector slot baseline")
    return rows


def probability_score_totals(
    frame: pd.DataFrame, probability_column: str
) -> dict[str, float | int]:
    rows = validate_trials(frame)
    if probability_column not in frame.columns:
        raise ValueError(f"score frame lacks {probability_column!r}")
    probability = pd.to_numeric(frame.loc[rows.index, probability_column], errors="raise").to_numpy(float)
    if (~np.isfinite(probability)).any() or ((probability < 0.0) | (probability > 1.0)).any():
        raise ValueError(f"{probability_column} lies outside [0,1]")
    pa = rows.official_pa.to_numpy(float)
    hits = rows.official_hits.to_numpy(float)
    clipped = np.clip(probability, 1e-12, 1.0 - 1e-12)
    brier_total = float(np.sum(hits * (1.0 - probability) ** 2 + (pa - hits) * probability**2))
    log_loss_total = float(
        -np.sum(hits * np.log(clipped) + (pa - hits) * np.log(1.0 - clipped))
    )
    return {
        "official_pa": int(pa.sum()),
        "brier_total": brier_total,
        "log_loss_total": log_loss_total,
        "pa_weighted_brier": brier_total / float(pa.sum()),
        "pa_weighted_log_loss": log_loss_total / float(pa.sum()),
    }


def paired_date_block_score_interval(
    frame: pd.DataFrame,
    *,
    candidate_column: str,
    baseline_column: str,
    metric: str,
    declared_dates: Iterable[str],
    bootstrap: int,
    seed: int,
) -> ResidualInterval:
    """Paired candidate-minus-baseline score interval; negative is better."""

    rows = validate_trials(frame)
    if metric not in {"brier", "log_loss"}:
        raise ValueError("paired score metric must be brier or log_loss")
    for column in (candidate_column, baseline_column):
        if column not in frame.columns:
            raise ValueError(f"paired score frame lacks {column!r}")
    dates = np.asarray(sorted({str(value) for value in declared_dates}))
    if len(dates) < 2 or bootstrap <= 0:
        raise ValueError("paired score interval needs two dates and positive draws")
    observed = set(rows.official_game_date.astype(str).unique())
    if observed - set(dates):
        raise ValueError("paired score rows contain a date outside declared blocks")
    candidate = pd.to_numeric(frame.loc[rows.index, candidate_column], errors="raise").to_numpy(float)
    baseline = pd.to_numeric(frame.loc[rows.index, baseline_column], errors="raise").to_numpy(float)
    if any(
        (~np.isfinite(probability)).any()
        or ((probability < 0.0) | (probability > 1.0)).any()
        for probability in (candidate, baseline)
    ):
        raise ValueError("paired score probability lies outside [0,1]")
    pa = rows.official_pa.to_numpy(float)
    hits = rows.official_hits.to_numpy(float)
    if metric == "brier":
        candidate_loss = hits * (1.0 - candidate) ** 2 + (pa - hits) * candidate**2
        baseline_loss = hits * (1.0 - baseline) ** 2 + (pa - hits) * baseline**2
    else:
        candidate = np.clip(candidate, 1e-12, 1.0 - 1e-12)
        baseline = np.clip(baseline, 1e-12, 1.0 - 1e-12)
        candidate_loss = -(hits * np.log(candidate) + (pa - hits) * np.log(1.0 - candidate))
        baseline_loss = -(hits * np.log(baseline) + (pa - hits) * np.log(1.0 - baseline))
    daily = pd.DataFrame(
        {
            "official_game_date": rows.official_game_date.to_numpy(),
            "loss_difference": candidate_loss - baseline_loss,
            "official_pa": pa,
        }
    ).groupby("official_game_date").sum().reindex(dates, fill_value=0.0)
    rng = np.random.default_rng(seed)
    weights = rng.multinomial(
        len(dates), np.full(len(dates), 1.0 / len(dates)), size=bootstrap
    )
    denominator = weights @ daily.official_pa.to_numpy(float)
    numerator = weights @ daily.loss_difference.to_numpy(float)
    valid = denominator > 0.0
    values = numerator[valid] / denominator[valid]
    if not len(values):
        return ResidualInterval(float("nan"), float("nan"), 0)
    return ResidualInterval(
        float(np.percentile(values, 2.5)),
        float(np.percentile(values, 97.5)),
        int(len(values)),
    )


def validate_trials(frame: pd.DataFrame) -> pd.DataFrame:
    required = [
        "mlb_game_pk",
        "player_id",
        "official_game_date",
        "official_lineup_slot",
        "official_pa",
        "official_hits",
        "inferred_per_pa_hit_probability",
    ]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"per-PA trials missing {missing}")
    out = frame.copy()
    key = ["mlb_game_pk", "player_id"]
    if out[key].isna().any().any() or out.duplicated(key).any():
        raise ValueError("per-PA trials have a null or duplicate player-game key")
    for column in ("official_pa", "official_hits", "official_lineup_slot"):
        out[column] = pd.to_numeric(out[column], errors="raise")
    q = pd.to_numeric(out.inferred_per_pa_hit_probability, errors="raise")
    if (~np.isfinite(q)).any() or ((q < 0.0) | (q > 1.0)).any():
        raise ValueError("per-PA hit probability lies outside [0,1]")
    if (out.official_pa <= 0.0).any() or not np.equal(
        out.official_pa, np.floor(out.official_pa)
    ).all():
        raise ValueError("official PA must be a positive integer")
    if (out.official_hits < 0.0).any() or not np.equal(
        out.official_hits, np.floor(out.official_hits)
    ).all():
        raise ValueError("official hits must be a non-negative integer")
    if (out.official_hits > out.official_pa).any():
        raise ValueError("official hits exceed official PA")
    if not out.official_lineup_slot.between(1, 9).all():
        raise ValueError("official lineup slot lies outside 1..9")
    out["official_pa"] = out.official_pa.astype(int)
    out["official_hits"] = out.official_hits.astype(int)
    out["official_lineup_slot"] = out.official_lineup_slot.astype(int)
    out["inferred_per_pa_hit_probability"] = q.astype(float)
    return out


def sturges_quantile_edges(selector_probabilities: Iterable[float]) -> np.ndarray:
    """Outcome-blind equal-frequency edges with Sturges' derived band count."""

    values = np.asarray(list(selector_probabilities), dtype=float)
    if len(values) < 2 or (~np.isfinite(values)).any() or (
        (values < 0.0) | (values > 1.0)
    ).any():
        raise ValueError("selector probabilities are insufficient or invalid")
    bands = int(ceil(log2(len(values))) + 1)
    edges = np.quantile(values, np.linspace(0.0, 1.0, bands + 1))
    edges[0], edges[-1] = 0.0, 1.0
    if len(np.unique(edges)) != len(edges):
        raise ValueError("selector probability lattice collapses a Sturges band")
    if not np.all(np.diff(edges) > 0.0):
        raise ValueError("selector probability-band edges are not increasing")
    return edges


def assign_probability_bands(probabilities: Iterable[float], edges: np.ndarray) -> np.ndarray:
    values = np.asarray(list(probabilities), dtype=float)
    boundary = np.asarray(edges, dtype=float)
    if len(boundary) < 2 or boundary[0] != 0.0 or boundary[-1] != 1.0:
        raise ValueError("probability bands must cover [0,1]")
    if (~np.isfinite(values)).any() or ((values < 0.0) | (values > 1.0)).any():
        raise ValueError("probability-band input lies outside [0,1]")
    index = np.searchsorted(boundary, values, side="right") - 1
    index = np.clip(index, 0, len(boundary) - 2)
    width = len(str(len(boundary) - 1))
    return np.asarray([f"B{value + 1:0{width}d}" for value in index])


def per_pa_metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    rows = validate_trials(frame)
    q = rows.inferred_per_pa_hit_probability.to_numpy(float)
    pa = rows.official_pa.to_numpy(float)
    hits = rows.official_hits.to_numpy(float)
    expected = q * pa
    total_pa = float(pa.sum())
    clipped = np.clip(q, 1e-12, 1.0 - 1e-12)
    brier_numerator = np.sum(hits * (1.0 - q) ** 2 + (pa - hits) * q**2)
    log_loss_numerator = -np.sum(
        hits * np.log(clipped) + (pa - hits) * np.log(1.0 - clipped)
    )
    return {
        "player_games": int(len(rows)),
        "official_dates": int(rows.official_game_date.nunique()),
        "official_pa": int(total_pa),
        "official_hits": int(hits.sum()),
        "expected_hits": float(expected.sum()),
        "predicted_minus_observed_hit_rate": float((expected.sum() - hits.sum()) / total_pa),
        "observed_hit_rate": float(hits.sum() / total_pa),
        "expected_hit_rate": float(expected.sum() / total_pa),
        "pa_weighted_brier": float(brier_numerator / total_pa),
        "pa_weighted_log_loss": float(log_loss_numerator / total_pa),
    }


def date_block_residual_interval(
    frame: pd.DataFrame,
    *,
    declared_dates: Iterable[str],
    bootstrap: int,
    seed: int,
) -> ResidualInterval:
    """Date-block interval for predicted-minus-observed hits per official PA."""

    rows = validate_trials(frame)
    dates = np.asarray(sorted({str(value) for value in declared_dates}))
    if len(dates) < 2 or bootstrap <= 0:
        raise ValueError("date-block interval needs two dates and positive draws")
    observed = set(rows.official_game_date.astype(str).unique())
    if observed - set(dates):
        raise ValueError("per-PA rows contain a date outside the declared block universe")
    grouped = rows.assign(
        expected_hits=(
            rows.inferred_per_pa_hit_probability.to_numpy(float)
            * rows.official_pa.to_numpy(float)
        )
    ).groupby("official_game_date").agg(
        expected_hits=("expected_hits", "sum"),
        official_hits=("official_hits", "sum"),
        official_pa=("official_pa", "sum"),
    ).reindex(dates, fill_value=0.0)
    rng = np.random.default_rng(seed)
    weights = rng.multinomial(
        len(dates), np.full(len(dates), 1.0 / len(dates)), size=bootstrap
    )
    denominator = weights @ grouped.official_pa.to_numpy(float)
    numerator = weights @ (
        grouped.expected_hits.to_numpy(float) - grouped.official_hits.to_numpy(float)
    )
    valid = denominator > 0.0
    values = numerator[valid] / denominator[valid]
    if not len(values):
        return ResidualInterval(float("nan"), float("nan"), 0)
    return ResidualInterval(
        float(np.percentile(values, 2.5)),
        float(np.percentile(values, 97.5)),
        int(len(values)),
    )
