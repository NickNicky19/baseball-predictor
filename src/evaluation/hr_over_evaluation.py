"""Fail-closed, one-sided DraftKings HR-over research evaluation."""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np
import pandas as pd

from src.evaluation.hr_over_contract import (
    LINE,
    MODEL_CATEGORY,
    expected_profit_per_unit,
    implied_break_even,
    raw_implied_probability_movement,
    validate_quotes,
)


MODEL_KEY = ["mlb_game_pk", "player_id", "category", "line"]
OUTCOME_KEY = ["mlb_game_pk", "player_id", "category"]


def _normalise_date(frame: pd.DataFrame, column: str, label: str) -> pd.DataFrame:
    if column not in frame.columns:
        raise ValueError(f"{label} missing {column}")
    out = frame.copy()
    parsed = pd.to_datetime(out[column], errors="coerce")
    if parsed.isna().any():
        raise ValueError(f"{label} has invalid dates")
    out[column] = parsed.dt.strftime("%Y-%m-%d")
    return out


def require_unique(frame: pd.DataFrame, key: list[str], label: str) -> None:
    missing = [column for column in key if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} missing {missing}")
    if frame[key].isna().any().any():
        raise ValueError(f"{label} key contains nulls")
    duplicated = frame.duplicated(key, keep=False)
    if duplicated.any():
        raise ValueError(f"{label} key is duplicated")


def validate_date_contract(
    observed: Iterable[str], diagnostic_dates: list[str], confirmation_dates: list[str]
) -> None:
    diagnostic = [str(value) for value in diagnostic_dates]
    confirmation = [str(value) for value in confirmation_dates]
    if (
        not diagnostic
        or not confirmation
        or diagnostic != sorted(set(diagnostic))
        or confirmation != sorted(set(confirmation))
        or set(diagnostic) & set(confirmation)
        or max(diagnostic) >= min(confirmation)
    ):
        raise ValueError("HR chronology is not exact, unique, disjoint, and forward")
    declared = set(diagnostic) | set(confirmation)
    if any(value.startswith("2026-05-") for value in declared):
        raise ValueError("May leaked into the HR evaluation")
    actual = set(pd.Series(list(observed), dtype="object").astype(str).unique())
    if actual != declared:
        raise ValueError(
            "HR evaluation dates differ from the locked universe: "
            f"missing={sorted(declared - actual)} extra={sorted(actual - declared)}"
        )


def _arm(frame: pd.DataFrame, label: str, allowed_dates: set[str]) -> pd.DataFrame:
    required = [*MODEL_KEY, "game_date", "sim_p_over"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} probabilities missing {missing}")
    out = frame[
        frame.category.astype(str).eq(MODEL_CATEGORY)
        & np.isclose(pd.to_numeric(frame.line, errors="coerce"), LINE)
    ].copy()
    out = _normalise_date(out, "game_date", label)
    out = out[out.game_date.isin(allowed_dates)].copy()
    require_unique(out, MODEL_KEY, label)
    probability = pd.to_numeric(out.sim_p_over, errors="coerce")
    if probability.isna().any() or (~np.isfinite(probability)).any() or not probability.between(0, 1).all():
        raise ValueError(f"{label} probabilities must be finite in [0,1]")
    out["sim_p_over"] = probability.astype(float)
    return out.sort_values(MODEL_KEY).reset_index(drop=True)


def build_pairs(
    source: pd.DataFrame,
    frozen: pd.DataFrame,
    candidate: pd.DataFrame,
    official: pd.DataFrame,
    diagnostic_dates: list[str],
    confirmation_dates: list[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the exact scored universe without silently dropping model gaps."""
    validate_quotes(source)
    if "result" in source.columns:
        raise ValueError("numeric vendor result reached HR evaluation")
    required_source = [*MODEL_KEY, "official_game_date", "entry_decimal_odds", "close_decimal_odds"]
    missing = [column for column in required_source if column not in source.columns]
    if missing:
        raise ValueError(f"HR market source missing {missing}")
    market = _normalise_date(source, "official_game_date", "HR market source")
    market = market[
        market.category.astype(str).eq(MODEL_CATEGORY)
        & np.isclose(pd.to_numeric(market.line, errors="coerce"), LINE)
    ].copy()
    require_unique(market, MODEL_KEY, "HR final market")
    validate_date_contract(
        market.official_game_date, diagnostic_dates, confirmation_dates
    )
    allowed = set(diagnostic_dates) | set(confirmation_dates)

    frozen_arm = _arm(frozen, "frozen", allowed)
    candidate_arm = _arm(candidate, "candidate", allowed)
    if not frozen_arm[MODEL_KEY].equals(candidate_arm[MODEL_KEY]):
        raise ValueError("frozen/candidate MODEL_KEY sets differ")
    if np.array_equal(
        frozen_arm.sim_p_over.to_numpy(float), candidate_arm.sim_p_over.to_numpy(float)
    ):
        raise ValueError("candidate probabilities are identical to frozen")

    model = frozen_arm.rename(
        columns={"game_date": "frozen_game_date", "sim_p_over": "frozen_p_over"}
    ).merge(
        candidate_arm.rename(
            columns={"game_date": "candidate_game_date", "sim_p_over": "candidate_p_over"}
        ),
        on=MODEL_KEY,
        how="inner",
        validate="one_to_one",
    )
    joined = market.merge(model, on=MODEL_KEY, how="left", validate="one_to_one")
    one_missing = joined.frozen_p_over.isna() ^ joined.candidate_p_over.isna()
    if one_missing.any():
        raise ValueError("only one model arm covers an HR market key")
    model_available = joined.frozen_p_over.notna()

    required_official = [*OUTCOME_KEY, "game_date", "actual_value"]
    missing = [column for column in required_official if column not in official.columns]
    if missing:
        raise ValueError(f"official outcomes missing {missing}")
    truth = official[official.category.astype(str).eq(MODEL_CATEGORY)].copy()
    truth = _normalise_date(truth, "game_date", "official outcomes")
    require_unique(truth, OUTCOME_KEY, "official outcomes")
    truth["actual_value"] = pd.to_numeric(truth.actual_value, errors="coerce")
    if truth.actual_value.isna().any() or (truth.actual_value < 0).any():
        raise ValueError("official HR actual is missing or negative")
    scored = joined.loc[model_available].merge(
        truth.rename(columns={"game_date": "official_target_date"}),
        on=OUTCOME_KEY,
        how="left",
        validate="many_to_one",
    )
    if scored.actual_value.isna().any():
        raise ValueError("a model-covered HR market row lacks official truth")
    dates_agree = (
        scored.official_game_date.eq(scored.frozen_game_date)
        & scored.official_game_date.eq(scored.candidate_game_date)
        & scored.official_game_date.eq(scored.official_target_date)
    )
    if not dates_agree.all():
        raise ValueError("official/market/model date disagreement")
    require_unique(scored, MODEL_KEY, "scored HR universe")
    scored["won"] = scored.actual_value.ge(1.0)
    funnel = {
        "strict_market_rows": int(len(market)),
        "model_available_rows": int(model_available.sum()),
        "model_unavailable_rows": int((~model_available).sum()),
        "model_market_coverage": float(model_available.mean()) if len(market) else None,
        "officially_scored_rows": int(len(scored)),
        "diagnostic_rows": int(scored.official_game_date.isin(diagnostic_dates).sum()),
        "confirmation_rows": int(scored.official_game_date.isin(confirmation_dates).sum()),
    }
    return scored.sort_values(MODEL_KEY).reset_index(drop=True), funnel


def arm_rows(pairs: pd.DataFrame, arm: str) -> pd.DataFrame:
    if arm not in {"frozen", "candidate"}:
        raise ValueError("arm must be frozen or candidate")
    out = pairs.copy()
    probability = out[f"{arm}_p_over"].to_numpy(float)
    won = out.won.to_numpy(bool)
    entry_odds = pd.to_numeric(out.entry_decimal_odds, errors="raise").to_numpy(float)
    close_odds = pd.to_numeric(out.close_decimal_odds, errors="raise").to_numpy(float)
    eps = np.finfo(float).eps
    safe = np.clip(probability, eps, 1.0 - eps)
    out["arm"] = arm
    out["model_probability"] = probability
    out["brier"] = np.square(probability - won.astype(float))
    out["log_loss"] = -(won * np.log(safe) + (~won) * np.log1p(-safe))
    out["entry_raw_break_even"] = implied_break_even(entry_odds)
    out["close_raw_break_even"] = implied_break_even(close_odds)
    out["model_edge_raw"] = probability - out.entry_raw_break_even.to_numpy(float)
    out["expected_profit"] = expected_profit_per_unit(probability, entry_odds)
    out["positive_ev"] = out.expected_profit.gt(0.0)
    out["raw_probability_movement"] = raw_implied_probability_movement(entry_odds, close_odds)
    out["theoretical_realised_profit"] = np.where(won, entry_odds - 1.0, -1.0)
    return out


def point_metrics(rows: pd.DataFrame) -> dict[str, Any]:
    selected = rows[rows.positive_ev]
    return {
        "all_rows": int(len(rows)),
        "dates": int(rows.official_game_date.nunique()),
        "mean_model_probability": float(rows.model_probability.mean()),
        "observed_hr_rate": float(rows.won.mean()),
        "calibration_bias": float(rows.model_probability.mean() - rows.won.mean()),
        "brier": float(rows.brier.mean()),
        "log_loss": float(rows.log_loss.mean()),
        "positive_ev_rows": int(len(selected)),
        "positive_ev_dates": int(selected.official_game_date.nunique()),
        "positive_ev_mean_expected_profit": (
            float(selected.expected_profit.mean()) if len(selected) else None
        ),
        "positive_ev_mean_raw_probability_movement": (
            float(selected.raw_probability_movement.mean()) if len(selected) else None
        ),
        "positive_ev_positive_movement_rate": (
            float(selected.raw_probability_movement.gt(0).mean()) if len(selected) else None
        ),
        "positive_ev_theoretical_flat_stake_roi": (
            float(selected.theoretical_realised_profit.mean()) if len(selected) else None
        ),
    }


def _interval(values: np.ndarray) -> list[float]:
    return [float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))]


def date_block_intervals(
    frozen_rows: pd.DataFrame,
    candidate_rows: pd.DataFrame,
    declared_dates: list[str],
    draws: int,
    seed: int,
) -> dict[str, Any]:
    if draws <= 0:
        raise ValueError("bootstrap draws must be positive")
    dates = [str(value) for value in declared_dates]
    for label, frame in (("frozen", frozen_rows), ("candidate", candidate_rows)):
        if set(frame.official_game_date.astype(str).unique()) != set(dates):
            raise ValueError(f"{label} bootstrap dates differ from declared dates")
    if not frozen_rows[MODEL_KEY].reset_index(drop=True).equals(
        candidate_rows[MODEL_KEY].reset_index(drop=True)
    ):
        raise ValueError("paired bootstrap MODEL_KEY rows differ")

    base = pd.DataFrame({
        "official_game_date": frozen_rows.official_game_date.to_numpy(),
        "brier_delta": candidate_rows.brier.to_numpy(float) - frozen_rows.brier.to_numpy(float),
        "log_loss_delta": candidate_rows.log_loss.to_numpy(float) - frozen_rows.log_loss.to_numpy(float),
    })
    totals = base.groupby("official_game_date").agg(
        count=("brier_delta", "size"),
        brier_delta=("brier_delta", "sum"),
        log_loss_delta=("log_loss_delta", "sum"),
    ).reindex(dates, fill_value=0.0)
    for arm, frame in (("frozen", frozen_rows), ("candidate", candidate_rows)):
        selected = frame[frame.positive_ev]
        selected_totals = selected.groupby("official_game_date").agg(
            selected_count=("positive_ev", "size"),
            movement=("raw_probability_movement", "sum"),
            profit=("theoretical_realised_profit", "sum"),
        ).reindex(dates, fill_value=0.0)
        for column in selected_totals.columns:
            totals[f"{arm}_{column}"] = selected_totals[column]

    rng = np.random.default_rng(int(seed))
    weights = rng.multinomial(
        len(dates), np.full(len(dates), 1.0 / len(dates)), size=int(draws)
    )
    all_count = weights @ totals["count"].to_numpy(float)
    frozen_count = weights @ totals["frozen_selected_count"].to_numpy(float)
    candidate_count = weights @ totals["candidate_selected_count"].to_numpy(float)
    valid = (all_count > 0) & (frozen_count > 0) & (candidate_count > 0)
    return {
        "draws_requested": int(draws),
        "valid_draws": int(valid.sum()),
        "candidate_minus_frozen_brier_95": _interval(
            (weights[valid] @ totals.brier_delta.to_numpy(float)) / all_count[valid]
        ),
        "candidate_minus_frozen_log_loss_95": _interval(
            (weights[valid] @ totals.log_loss_delta.to_numpy(float)) / all_count[valid]
        ),
        "frozen_positive_ev_raw_movement_95": _interval(
            (weights[valid] @ totals.frozen_movement.to_numpy(float)) / frozen_count[valid]
        ),
        "candidate_positive_ev_raw_movement_95": _interval(
            (weights[valid] @ totals.candidate_movement.to_numpy(float)) / candidate_count[valid]
        ),
        "frozen_positive_ev_theoretical_roi_95": _interval(
            (weights[valid] @ totals.frozen_profit.to_numpy(float)) / frozen_count[valid]
        ),
        "candidate_positive_ev_theoretical_roi_95": _interval(
            (weights[valid] @ totals.candidate_profit.to_numpy(float)) / candidate_count[valid]
        ),
    }


def validate_research_report(payload: dict[str, Any]) -> None:
    if payload.get("betting_authorized") is not False:
        raise ValueError("HR evaluation attempted to authorize betting")
    if payload.get("may_opened") is not False:
        raise ValueError("HR evaluation opened May")
    if payload.get("probability_label") != "RAW_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN":
        raise ValueError("raw one-sided break-even was mislabeled fair/de-vigged")
    if payload.get("historical_executability_verified") is not False:
        raise ValueError("historical HR prices were mislabeled executable")
    if payload.get("official_draftkings_settlement_rule_verified") is not False:
        raise ValueError("DraftKings HR settlement was mislabeled verified")
