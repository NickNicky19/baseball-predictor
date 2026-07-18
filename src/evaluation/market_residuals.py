"""Fail-closed construction and factual summaries for market residual audits.

These functions intentionally diagnose *where* probabilities disagree with
official outcomes.  They do not select bets, fit a calibration layer, or infer
why a residual exists.  Any model intervention needs a separate mechanism
measurement after this diagnostic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.evaluation.identity_keys import MODEL_KEY, OUTCOME_KEY, require_unique


# Fixed, equal-width probability bins declared before looking at residuals.
# They make calibration shape visible; they are not fitted thresholds.
PROBABILITY_EDGES = np.linspace(0.0, 1.0, 11)


def _normalise_date(frame: pd.DataFrame, column: str, label: str) -> pd.DataFrame:
    if column not in frame.columns:
        raise ValueError(f"{label}: missing {column!r}")
    out = frame.copy()
    out[column] = pd.to_datetime(out[column], errors="coerce").dt.strftime("%Y-%m-%d")
    if out[column].isna().any():
        raise ValueError(f"{label}: invalid {column!r}")
    return out


def build_scored_pairs(
    market: pd.DataFrame,
    frozen: pd.DataFrame,
    candidate: pd.DataFrame,
    official: pd.DataFrame,
) -> pd.DataFrame:
    """Join a strict market artifact to two exact model arms and MLB truth.

    This refuses missing model/target rows.  A residual plot calculated on an
    inner-joined subset can look clean precisely because the problematic rows
    vanished; it is not a diagnostic of the certified universe.
    """
    for label, frame, required in (
        ("strict market", market, [*MODEL_KEY, "official_game_date", "entry_p_over"]),
        ("frozen model", frozen, [*MODEL_KEY, "game_date", "sim_p_over"]),
        ("candidate model", candidate, [*MODEL_KEY, "game_date", "sim_p_over"]),
        ("official actuals", official, [*OUTCOME_KEY, "game_date", "actual_value"]),
    ):
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise ValueError(f"{label}: missing {missing}")

    market = _normalise_date(market, "official_game_date", "strict market")
    frozen = _normalise_date(frozen, "game_date", "frozen model")
    candidate = _normalise_date(candidate, "game_date", "candidate model")
    official = _normalise_date(official, "game_date", "official actuals")
    require_unique(market, MODEL_KEY, "strict market")
    require_unique(frozen, MODEL_KEY, "frozen model")
    require_unique(candidate, MODEL_KEY, "candidate model")
    require_unique(official, OUTCOME_KEY, "official actuals")

    f_keys = set(map(tuple, frozen[MODEL_KEY].to_numpy()))
    c_keys = set(map(tuple, candidate[MODEL_KEY].to_numpy()))
    if f_keys != c_keys:
        raise ValueError("frozen/candidate MODEL_KEY sets differ; residuals would "
                         "silently compare different universes")

    m_keys = set(map(tuple, market[MODEL_KEY].to_numpy()))
    missing_models = m_keys - f_keys
    if missing_models:
        raise ValueError(f"strict market has {len(missing_models)} key(s) absent "
                         "from both model arms; residual audit refuses a subset")

    pairs = market[[*MODEL_KEY, "official_game_date", "entry_p_over"]].merge(
        frozen[[*MODEL_KEY, "game_date", "sim_p_over"]].rename(
            columns={"game_date": "frozen_game_date", "sim_p_over": "p_frozen"}),
        on=MODEL_KEY, how="left", validate="one_to_one",
    ).merge(
        candidate[[*MODEL_KEY, "game_date", "sim_p_over"]].rename(
            columns={"game_date": "candidate_game_date", "sim_p_over": "p_candidate"}),
        on=MODEL_KEY, how="left", validate="one_to_one",
    ).merge(
        official[[*OUTCOME_KEY, "game_date", "actual_value"]].rename(
            columns={"game_date": "official_target_date"}),
        on=OUTCOME_KEY, how="left", validate="many_to_one",
    )
    if len(pairs) != len(market):
        raise AssertionError("market residual join changed row count")
    if pairs[["p_frozen", "p_candidate", "actual_value"]].isna().any().any():
        raise ValueError("strict market has a missing model probability or MLB target")
    if not (pairs[["p_frozen", "p_candidate", "entry_p_over"]].apply(
            pd.to_numeric, errors="coerce").apply(lambda s: s.between(0, 1)).all().all()):
        raise ValueError("model or market probability outside [0,1]")
    bad_dates = pairs[
        (pairs.official_game_date != pairs.frozen_game_date)
        | (pairs.official_game_date != pairs.candidate_game_date)
        | (pairs.official_game_date != pairs.official_target_date)
    ]
    if len(bad_dates):
        raise ValueError("market/model/target canonical dates disagree:\n" +
                         bad_dates.head(20).to_string(index=False))

    pairs["over_outcome"] = (pairs.actual_value > pairs.line).astype(int)
    for arm in ("frozen", "candidate"):
        p = pairs[f"p_{arm}"].to_numpy(float)
        y = pairs.over_outcome.to_numpy(float)
        pairs[f"residual_{arm}"] = p - y
        pairs[f"squared_error_{arm}"] = (p - y) ** 2
        pairs[f"market_edge_{arm}"] = p - pairs.entry_p_over.to_numpy(float)
    return pairs


def global_metrics(pairs: pd.DataFrame, arm: str) -> dict:
    """Factual calibration and market-distance metrics for one arm."""
    residual = pairs[f"residual_{arm}"].to_numpy(float)
    errors = pairs[f"squared_error_{arm}"].to_numpy(float)
    edge = pairs[f"market_edge_{arm}"].to_numpy(float)
    return dict(
        arm=arm,
        rows=int(len(pairs)),
        official_dates=int(pairs.official_game_date.nunique()),
        brier=float(errors.mean()),
        mean_probability_residual=float(residual.mean()),
        mean_abs_probability_residual=float(np.abs(residual).mean()),
        mean_signed_market_edge=float(edge.mean()),
        mean_abs_market_edge=float(np.abs(edge).mean()),
    )


def reliability_bins(pairs: pd.DataFrame, arm: str) -> pd.DataFrame:
    """Fixed-bin reliability table, split by market line rather than pooled."""
    p_column = f"p_{arm}"
    frame = pairs.copy()
    frame["probability_bin"] = pd.cut(
        frame[p_column], PROBABILITY_EDGES, include_lowest=True, right=False,
    ).astype(str)
    rows = []
    for (category, line, bucket), group in frame.groupby(
        ["category", "line", "probability_bin"], observed=True, sort=True
    ):
        if not len(group):
            continue
        predicted = float(group[p_column].mean())
        observed = float(group.over_outcome.mean())
        rows.append(dict(
            arm=arm, category=category, line=float(line), probability_bin=bucket,
            rows=int(len(group)), official_dates=int(group.official_game_date.nunique()),
            predicted_mean=predicted, observed_rate=observed,
            calibration_residual=predicted - observed,
            mean_market_probability=float(group.entry_p_over.mean()),
        ))
    return pd.DataFrame(rows)


def by_date_metrics(pairs: pd.DataFrame, arm: str) -> pd.DataFrame:
    """One row per canonical date, preserving date-block structure."""
    grouped = pairs.groupby("official_game_date", sort=True)
    return grouped.agg(
        rows=("over_outcome", "size"),
        observed_rate=("over_outcome", "mean"),
        predicted_mean=(f"p_{arm}", "mean"),
        brier=(f"squared_error_{arm}", "mean"),
        residual=(f"residual_{arm}", "mean"),
        market_probability=("entry_p_over", "mean"),
        market_edge=(f"market_edge_{arm}", "mean"),
    ).reset_index().assign(arm=arm)
