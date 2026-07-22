"""Pure, chronology-safe statistics for the hitter contact-skill audit."""
from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ScoreInterval:
    lower: float
    upper: float
    valid_draws: int


def validate_contact_rows(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "game_pk", "player_id", "game_date", "lineup_slot",
        "roll15_xba", "roll15_bip", "recent_pa_15",
        "roll30_xba", "roll30_bip", "recent_pa_30",
        "contact_events", "hits_on_contact",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"contact audit rows missing {missing}")
    out = frame.copy()
    key = ["game_pk", "player_id"]
    if out[key].isna().any().any() or out.duplicated(key).any():
        raise ValueError("contact audit has a null or duplicate player-game key")
    out["game_date"] = out.game_date.astype(str)
    for column in required - {"game_date"}:
        out[column] = pd.to_numeric(out[column], errors="raise")
    for window in (15, 30):
        xba = out[f"roll{window}_xba"]
        bip = out[f"roll{window}_bip"]
        pa = out[f"recent_pa_{window}"]
        if (~np.isfinite(xba)).any() or not xba.between(0.0, 1.0).all():
            raise ValueError(f"roll{window}_xba lies outside [0,1]")
        if (~np.isfinite(bip)).any() or (bip < 0).any():
            raise ValueError(f"roll{window}_bip is invalid")
        if (~np.isfinite(pa)).any() or (pa < 0).any():
            raise ValueError(f"recent_pa_{window} is invalid")
    if (out.contact_events <= 0).any() or (out.hits_on_contact < 0).any():
        raise ValueError("contact outcomes require positive exposure and non-negative hits")
    if (out.hits_on_contact > out.contact_events).any():
        raise ValueError("contact hits exceed contact events")
    if not out.lineup_slot.between(1, 9).all():
        raise ValueError("lineup slot lies outside 1..9")
    return out


def add_daily_anchors(frame: pd.DataFrame) -> pd.DataFrame:
    """Add outcome-blind, date-specific rolling-xBA league anchors."""
    out = frame.copy()
    for window in (15, 30):
        xba_col = f"roll{window}_xba"
        n_col = f"roll{window}_bip"
        numerator = (out[xba_col] * out[n_col]).groupby(out.game_date).transform("sum")
        denominator = out[n_col].groupby(out.game_date).transform("sum")
        if (denominator <= 0).any():
            raise ValueError(f"a date has no roll{window} BIP evidence for its league anchor")
        anchor = numerator / denominator
        if (~np.isfinite(anchor)).any() or not anchor.between(0.0, 1.0).all():
            raise ValueError(f"roll{window} daily anchor is invalid")
        out[f"roll{window}_league_xba"] = anchor
    return out


def add_probability(
    frame: pd.DataFrame,
    *,
    output: str,
    window: int,
    evidence_column: str,
    prior_strength: float,
) -> pd.DataFrame:
    if window not in (15, 30):
        raise ValueError("contact audit window must be 15 or 30 games")
    if evidence_column not in frame.columns:
        raise ValueError(f"contact audit lacks evidence column {evidence_column!r}")
    out = frame.copy()
    xba = pd.to_numeric(out[f"roll{window}_xba"], errors="raise").to_numpy(float)
    evidence = pd.to_numeric(out[evidence_column], errors="raise").to_numpy(float)
    anchor = pd.to_numeric(
        out[f"roll{window}_league_xba"], errors="raise"
    ).to_numpy(float)
    if prior_strength == inf:
        probability = anchor
    else:
        if not np.isfinite(prior_strength) or prior_strength < 0.0:
            raise ValueError("prior strength must be non-negative or infinity")
        denominator = evidence + float(prior_strength)
        if (denominator <= 0.0).any():
            raise ValueError("zero evidence with zero prior has no defined probability")
        probability = (evidence * xba + float(prior_strength) * anchor) / denominator
    if (~np.isfinite(probability)).any() or ((probability < 0.0) | (probability > 1.0)).any():
        raise ValueError("contact probability lies outside [0,1]")
    out[output] = probability
    return out


def score_totals(frame: pd.DataFrame, probability_column: str) -> dict[str, float | int]:
    rows = validate_contact_rows(frame)
    if probability_column not in frame.columns:
        raise ValueError(f"score frame lacks {probability_column!r}")
    probability = pd.to_numeric(
        frame.loc[rows.index, probability_column], errors="raise"
    ).to_numpy(float)
    if (~np.isfinite(probability)).any() or ((probability < 0.0) | (probability > 1.0)).any():
        raise ValueError("score probability lies outside [0,1]")
    exposure = rows.contact_events.to_numpy(float)
    hits = rows.hits_on_contact.to_numpy(float)
    clipped = np.clip(probability, 1e-12, 1.0 - 1e-12)
    brier_total = float(np.sum(hits * (1.0 - probability) ** 2 + (exposure - hits) * probability**2))
    log_loss_total = float(
        -np.sum(hits * np.log(clipped) + (exposure - hits) * np.log(1.0 - clipped))
    )
    total = float(exposure.sum())
    return {
        "player_games": int(len(rows)),
        "contact_events": int(total),
        "hits_on_contact": int(hits.sum()),
        "brier_total": brier_total,
        "log_loss_total": log_loss_total,
        "contact_weighted_brier": brier_total / total,
        "contact_weighted_log_loss": log_loss_total / total,
    }


def choose_window_on_selector(frame: pd.DataFrame, prior_strength: float = 120.0) -> int:
    rows = add_daily_anchors(validate_contact_rows(frame))
    ranked: list[tuple[float, float, int]] = []
    for window in (15, 30):
        column = f"window_{window}"
        scored = add_probability(
            rows,
            output=column,
            window=window,
            evidence_column=f"roll{window}_bip",
            prior_strength=prior_strength,
        )
        totals = score_totals(scored, column)
        ranked.append((
            float(totals["contact_weighted_log_loss"]),
            float(totals["contact_weighted_brier"]),
            window,
        ))
    ranked.sort()
    if ranked[0][:2] == ranked[1][:2]:
        raise ValueError("recency windows are exactly tied; refusing to guess")
    return ranked[0][2]


def fit_prior_strength_on_selector(frame: pd.DataFrame, window: int) -> float:
    rows = add_daily_anchors(validate_contact_rows(frame))
    evidence_column = f"roll{window}_bip"
    observed = sorted({float(value) for value in rows[evidence_column] if float(value) > 0.0})
    candidates = [0.0, *observed, inf]
    ranked: list[tuple[float, float, float]] = []
    for index, strength in enumerate(candidates):
        column = f"candidate_{index}"
        scored = add_probability(
            rows,
            output=column,
            window=window,
            evidence_column=evidence_column,
            prior_strength=strength,
        )
        totals = score_totals(scored, column)
        ranked.append((
            float(totals["contact_weighted_log_loss"]),
            float(totals["contact_weighted_brier"]),
            strength,
        ))
    ranked.sort(key=lambda value: (value[0], value[1], value[2]))
    if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
        raise ValueError("shrinkage strengths are exactly tied; refusing to guess")
    return ranked[0][2]


def paired_date_score_interval(
    frame: pd.DataFrame,
    *,
    candidate_column: str,
    baseline_column: str,
    metric: str,
    declared_dates: Iterable[str],
    bootstrap: int,
    seed: int,
) -> ScoreInterval:
    rows = validate_contact_rows(frame)
    if metric not in {"brier", "log_loss"}:
        raise ValueError("paired contact score metric must be brier or log_loss")
    dates = np.asarray(sorted({str(value) for value in declared_dates}))
    if len(dates) < 2 or bootstrap <= 0:
        raise ValueError("paired contact interval needs two dates and positive draws")
    if set(rows.game_date.unique()) - set(dates):
        raise ValueError("paired contact rows contain a date outside declared blocks")
    probabilities: list[np.ndarray] = []
    for column in (candidate_column, baseline_column):
        if column not in frame.columns:
            raise ValueError(f"paired contact frame lacks {column!r}")
        value = pd.to_numeric(frame.loc[rows.index, column], errors="raise").to_numpy(float)
        if (~np.isfinite(value)).any() or ((value < 0.0) | (value > 1.0)).any():
            raise ValueError("paired contact probability lies outside [0,1]")
        probabilities.append(value)
    candidate, baseline = probabilities
    exposure = rows.contact_events.to_numpy(float)
    hits = rows.hits_on_contact.to_numpy(float)
    if metric == "brier":
        candidate_loss = hits * (1.0 - candidate) ** 2 + (exposure - hits) * candidate**2
        baseline_loss = hits * (1.0 - baseline) ** 2 + (exposure - hits) * baseline**2
    else:
        candidate = np.clip(candidate, 1e-12, 1.0 - 1e-12)
        baseline = np.clip(baseline, 1e-12, 1.0 - 1e-12)
        candidate_loss = -(hits * np.log(candidate) + (exposure - hits) * np.log(1.0 - candidate))
        baseline_loss = -(hits * np.log(baseline) + (exposure - hits) * np.log(1.0 - baseline))
    daily = pd.DataFrame({
        "game_date": rows.game_date.to_numpy(),
        "loss_difference": candidate_loss - baseline_loss,
        "contact_events": exposure,
    }).groupby("game_date").sum().reindex(dates, fill_value=0.0)
    rng = np.random.default_rng(seed)
    weights = rng.multinomial(len(dates), np.full(len(dates), 1.0 / len(dates)), size=bootstrap)
    denominator = weights @ daily.contact_events.to_numpy(float)
    numerator = weights @ daily.loss_difference.to_numpy(float)
    valid = denominator > 0.0
    values = numerator[valid] / denominator[valid]
    if not len(values):
        return ScoreInterval(float("nan"), float("nan"), 0)
    return ScoreInterval(
        float(np.percentile(values, 2.5)),
        float(np.percentile(values, 97.5)),
        int(len(values)),
    )
