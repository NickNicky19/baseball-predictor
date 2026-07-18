"""Pure contracts and statistics for the frozen HR-over baseline diagnostic."""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np
import pandas as pd


MODEL_KEY = ["mlb_game_pk", "player_id", "category", "line"]


def validate_open_dates(dates: Iterable[str]) -> list[str]:
    values = [str(value) for value in dates]
    if not values or values != sorted(set(values)):
        raise ValueError("diagnostic dates must be nonempty, sorted, and unique")
    if any(value.startswith("2026-05-") for value in values):
        raise ValueError("May is sealed and cannot enter the HR baseline diagnostic")
    return values


def validate_pa_distribution(payload: dict[str, Any]) -> dict[int, dict[int, float]]:
    raw = payload.get("by_lineup_slot")
    if not isinstance(raw, dict) or set(raw) != {str(slot) for slot in range(1, 10)}:
        raise ValueError("PA distribution must contain exactly lineup slots 1-9")
    result: dict[int, dict[int, float]] = {}
    for slot in range(1, 10):
        values = raw[str(slot)]
        if not isinstance(values, dict) or not values:
            raise ValueError(f"PA distribution slot {slot} is empty")
        parsed: dict[int, float] = {}
        for pa, probability in values.items():
            pa_int = int(pa)
            p = float(probability)
            if pa_int < 0 or not np.isfinite(p) or p < 0:
                raise ValueError(f"invalid PA distribution value for slot {slot}")
            parsed[pa_int] = p
        if not np.isclose(sum(parsed.values()), 1.0, atol=1e-10):
            raise ValueError(f"PA distribution slot {slot} does not sum to one")
        result[slot] = parsed
    return result


def at_least_one_probability(per_pa_probability: float, pa_weights: dict[int, float]) -> float:
    q = float(per_pa_probability)
    if not np.isfinite(q) or not 0.0 <= q <= 1.0:
        raise ValueError("per-PA probability must be finite in [0,1]")
    if not pa_weights:
        raise ValueError("PA weights are empty")
    total = float(sum(float(value) for value in pa_weights.values()))
    if not np.isclose(total, 1.0, atol=1e-10):
        raise ValueError("PA weights do not sum to one")
    no_event = 0.0
    for pa, weight in pa_weights.items():
        n = int(pa)
        w = float(weight)
        if n < 0 or not np.isfinite(w) or w < 0:
            raise ValueError("invalid PA state or weight")
        no_event += w * ((1.0 - q) ** n)
    return float(np.clip(1.0 - no_event, 0.0, 1.0))


def mean_pa_probability(per_pa_probability: float, pa_weights: dict[int, float]) -> float:
    q = float(per_pa_probability)
    mean_pa = sum(int(pa) * float(weight) for pa, weight in pa_weights.items())
    return float(np.clip(1.0 - (1.0 - q) ** mean_pa, 0.0, 1.0))


def realized_pa_probability(per_pa_probability: float, official_pa: int | float) -> float:
    pa = float(official_pa)
    if not np.isfinite(pa) or pa < 0 or pa != np.floor(pa):
        raise ValueError("official PA must be a nonnegative integer")
    q = float(per_pa_probability)
    if not np.isfinite(q) or not 0.0 <= q <= 1.0:
        raise ValueError("per-PA probability must be finite in [0,1]")
    return float(np.clip(1.0 - (1.0 - q) ** int(pa), 0.0, 1.0))


def settlement_conditioned_over_probability(
    per_pa_probability: float, pa_weights: dict[int, float]
) -> dict[str, float]:
    """P(HR over | bet graded) under the verified DK Over participation rule.

    Zero PA is void. One PA with an HR is a win; one PA without an HR is
    void. At least two PA is graded normally. The returned win/loss/void
    probabilities are unconditional and must partition probability mass.
    """
    q = float(per_pa_probability)
    if not np.isfinite(q) or not 0.0 <= q <= 1.0:
        raise ValueError("per-PA probability must be finite in [0,1]")
    if not pa_weights or not np.isclose(sum(pa_weights.values()), 1.0, atol=1e-10):
        raise ValueError("PA weights must be nonempty and sum to one")
    win = loss = void = 0.0
    for pa, raw_weight in pa_weights.items():
        n = int(pa)
        weight = float(raw_weight)
        if n < 0 or not np.isfinite(weight) or weight < 0:
            raise ValueError("invalid PA state or weight")
        if n == 0:
            void += weight
            continue
        no_hr = (1.0 - q) ** n
        has_hr = 1.0 - no_hr
        win += weight * has_hr
        if n == 1:
            void += weight * no_hr
        else:
            loss += weight * no_hr
    total = win + loss + void
    if not np.isclose(total, 1.0, atol=1e-10):
        raise ValueError("settlement states do not partition probability mass")
    graded = win + loss
    if graded <= 0.0:
        raise ValueError("settlement contract has zero grade probability")
    return {
        "conditional_over_probability": float(win / graded),
        "unconditional_win_probability": float(win),
        "unconditional_loss_probability": float(loss),
        "void_probability": float(void),
        "grade_probability": float(graded),
    }


def require_exact_keys(observed: pd.DataFrame, expected: pd.DataFrame, label: str) -> None:
    for frame_name, frame in (("observed", observed), ("expected", expected)):
        missing = [column for column in MODEL_KEY if column not in frame.columns]
        if missing:
            raise ValueError(f"{label} {frame_name} missing {missing}")
        if frame[MODEL_KEY].isna().any().any() or frame.duplicated(MODEL_KEY).any():
            raise ValueError(f"{label} {frame_name} keys are null or duplicated")
    left = observed[MODEL_KEY].sort_values(MODEL_KEY).reset_index(drop=True)
    right = expected[MODEL_KEY].sort_values(MODEL_KEY).reset_index(drop=True)
    if not left.equals(right):
        raise ValueError(f"{label} key set differs from the certified market universe")


def score_probability(rows: pd.DataFrame, probability_column: str) -> pd.DataFrame:
    required = [
        probability_column, "official_won", "entry_decimal_odds",
        "close_decimal_odds", "official_game_date", *MODEL_KEY,
    ]
    missing = [column for column in required if column not in rows.columns]
    if missing:
        raise ValueError(f"diagnostic scoring rows missing {missing}")
    out = rows.copy()
    probability = pd.to_numeric(out[probability_column], errors="coerce")
    if probability.isna().any() or not probability.between(0, 1).all():
        raise ValueError(f"{probability_column} is not finite in [0,1]")
    won = out.official_won.astype(bool)
    entry = pd.to_numeric(out.entry_decimal_odds, errors="raise")
    close = pd.to_numeric(out.close_decimal_odds, errors="raise")
    if (entry <= 1).any() or (close <= 1).any():
        raise ValueError("decimal odds must exceed one")
    safe = probability.clip(np.finfo(float).eps, 1.0 - np.finfo(float).eps)
    out["probability"] = probability
    out["brier"] = (probability - won.astype(float)) ** 2
    out["log_loss"] = -(won * np.log(safe) + (~won) * np.log1p(-safe))
    out["expected_profit"] = probability * entry - 1.0
    out["positive_ev"] = out.expected_profit > 0.0
    out["raw_probability_movement"] = (1.0 / close) - (1.0 / entry)
    out["official_profit"] = np.where(won, entry - 1.0, -1.0)
    return out


def point_metrics(scored: pd.DataFrame) -> dict[str, Any]:
    selected = scored[scored.positive_ev]
    return {
        "rows": int(len(scored)),
        "dates": int(scored.official_game_date.nunique()),
        "mean_probability": float(scored.probability.mean()),
        "observed_hr_rate": float(scored.official_won.astype(bool).mean()),
        "calibration_bias": float(
            scored.probability.mean() - scored.official_won.astype(bool).mean()
        ),
        "brier": float(scored.brier.mean()),
        "log_loss": float(scored.log_loss.mean()),
        "positive_ev_rows": int(len(selected)),
        "positive_ev_raw_movement": (
            float(selected.raw_probability_movement.mean()) if len(selected) else None
        ),
        "positive_ev_official_roi": (
            float(selected.official_profit.mean()) if len(selected) else None
        ),
    }


def paired_date_block_interval(
    baseline: pd.DataFrame,
    variant: pd.DataFrame,
    dates: list[str],
    draws: int = 20_000,
    seed: int = 1701,
) -> dict[str, Any]:
    declared = validate_open_dates(dates)
    require_exact_keys(variant, baseline, "paired diagnostic")
    if set(baseline.official_game_date.astype(str).unique()) != set(declared):
        raise ValueError("baseline dates differ from the declared block")
    if set(variant.official_game_date.astype(str).unique()) != set(declared):
        raise ValueError("variant dates differ from the declared block")
    joined = baseline[[*MODEL_KEY, "official_game_date", "brier", "log_loss"]].merge(
        variant[[*MODEL_KEY, "brier", "log_loss"]],
        on=MODEL_KEY,
        how="inner",
        validate="one_to_one",
        suffixes=("_baseline", "_variant"),
    )
    joined["brier_delta"] = joined.brier_variant - joined.brier_baseline
    joined["log_loss_delta"] = joined.log_loss_variant - joined.log_loss_baseline
    totals = joined.groupby("official_game_date").agg(
        count=("brier_delta", "size"),
        brier_delta=("brier_delta", "sum"),
        log_loss_delta=("log_loss_delta", "sum"),
    ).reindex(declared, fill_value=0.0)
    rng = np.random.default_rng(int(seed))
    weights = rng.multinomial(
        len(declared), np.full(len(declared), 1.0 / len(declared)), size=int(draws)
    )
    denominator = weights @ totals["count"].to_numpy(float)
    valid = denominator > 0
    brier = (weights[valid] @ totals.brier_delta.to_numpy(float)) / denominator[valid]
    log_loss = (weights[valid] @ totals.log_loss_delta.to_numpy(float)) / denominator[valid]
    return {
        "draws_requested": int(draws),
        "valid_draws": int(valid.sum()),
        "variant_minus_baseline_brier_95": [
            float(np.percentile(brier, 2.5)), float(np.percentile(brier, 97.5))
        ],
        "variant_minus_baseline_log_loss_95": [
            float(np.percentile(log_loss, 2.5)), float(np.percentile(log_loss, 97.5))
        ],
    }


def validate_report(report: dict[str, Any]) -> None:
    if report.get("betting_authorized") is not False:
        raise ValueError("HR baseline diagnostic attempted to authorize betting")
    if report.get("may_opened") is not False:
        raise ValueError("HR baseline diagnostic opened May")
    if report.get("status") != "RESEARCH_ONLY":
        raise ValueError("HR baseline diagnostic is not labeled research-only")
    oracle = report.get("variants", {}).get("oracle_realized_pa", {})
    if oracle.get("deployable") is not False:
        raise ValueError("postgame realized-PA oracle was mislabeled deployable")
    if report.get("historical_executability_verified") is not False:
        raise ValueError("historical HR quote executability was fabricated")
