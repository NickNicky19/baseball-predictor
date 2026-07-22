"""Chronological, payout-aware fitting for the DraftKings hits policy.

This module deliberately separates three questions that older evaluators
conflated:

* ``entry_p_over`` measures distance from the de-vigged market;
* the posted decimal price determines whether a wager has positive expected
  profit; and
* date-block uncertainty determines whether the evidence is strong enough.

It does not authorize betting.  Historical quote age is still only a proxy
for executability, and a policy fitted here remains research-only until the
prospective shadow ledger verifies prices that were actually available.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from src.evaluation.hits_policy_source import materialize_strict_at_age
from src.evaluation.identity_keys import MODEL_KEY, OUTCOME_KEY, require_unique


ARM_COLUMNS = {
    "frozen": "p_frozen",
    "candidate": "p_candidate",
}


@dataclass(frozen=True)
class PolicyMetrics:
    selected_rows: int
    selected_dates: int
    capture: float
    flat_stake_roi: float
    mean_expected_profit: float


@dataclass(frozen=True)
class BlockInterval:
    lower: float
    upper: float
    valid_draws: int


def split_fit_dates(
    fit_dates: Iterable[str], holdout_start: str
) -> tuple[list[str], list[str]]:
    """Create the deterministic internal split and reject holdout leakage."""

    dates = [str(value) for value in fit_dates]
    if len(dates) < 4 or dates != sorted(set(dates)):
        raise ValueError("fit dates must be four-or-more unique chronological dates")
    if not holdout_start or max(dates) >= str(holdout_start):
        raise ValueError("holdout date leaked into the fit universe")
    midpoint = len(dates) // 2
    selector, confirmation = dates[:midpoint], dates[midpoint:]
    if not selector or not confirmation or max(selector) >= min(confirmation):
        raise ValueError("internal fit split is not strictly chronological")
    return selector, confirmation


def confirmation_gate(
    candidate_capture: BlockInterval,
    candidate_minus_frozen: BlockInterval,
    *,
    expected_draws: int,
    capture_bar: float,
) -> tuple[bool, str]:
    """Apply the locked lower-bound gates without rounding or fallbacks."""

    if expected_draws <= 0:
        raise ValueError("expected_draws must be positive")
    if candidate_capture.valid_draws != expected_draws:
        return False, "candidate capture interval has invalid date-block draws"
    if candidate_minus_frozen.valid_draws != expected_draws:
        return False, "paired capture interval has invalid date-block draws"
    if not np.isfinite(candidate_capture.lower) or not np.isfinite(
        candidate_minus_frozen.lower
    ):
        return False, "confirmation interval is non-finite"
    if not candidate_capture.lower > float(capture_bar):
        return False, "candidate capture lower bound does not clear the locked bar"
    if not candidate_minus_frozen.lower > 0.0:
        return False, "candidate-minus-frozen lower bound does not clear zero"
    return True, "both locked lower-bound gates clear"


def _normalise_date(frame: pd.DataFrame, column: str, label: str) -> pd.DataFrame:
    if column not in frame.columns:
        raise ValueError(f"{label}: missing {column!r}")
    out = frame.copy()
    out[column] = pd.to_datetime(out[column], errors="coerce").dt.strftime("%Y-%m-%d")
    if out[column].isna().any():
        raise ValueError(f"{label}: invalid {column!r}")
    return out


def build_policy_pairs(
    source: pd.DataFrame,
    frozen: pd.DataFrame,
    candidate: pd.DataFrame,
    official: pd.DataFrame,
    *,
    max_quote_age: float,
    allowed_dates: Iterable[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build one exact, strict, payout-aware research universe.

    Freshness is applied before symmetric duplicate-key exclusion by
    :func:`materialize_strict_at_age`.  Missing model or MLB-truth rows are
    hard failures; they never become a cleaner-looking inner-joined subset.
    """

    allowed = tuple(str(value) for value in allowed_dates)
    if not allowed or len(allowed) != len(set(allowed)):
        raise ValueError("allowed_dates must be a non-empty unique sequence")

    strict, funnel = materialize_strict_at_age(source, max_quote_age)
    strict = _normalise_date(strict, "official_game_date", "policy source")
    observed_dates = set(strict.official_game_date.unique())
    unexpected = observed_dates - set(allowed)
    if unexpected:
        raise ValueError(
            "policy source contains dates outside its declared chronological role: "
            f"{sorted(unexpected)}"
        )

    required_market = [
        *MODEL_KEY,
        "official_game_date",
        "entry_p_over",
        "close_p_over",
        "entry_overround",
        "entry_age_min",
        "entry_over_odds_decimal",
        "entry_under_odds_decimal",
        "official_pa",
        "official_lineup_slot",
        "is_starter",
    ]
    missing = [column for column in required_market if column not in strict.columns]
    if missing:
        raise ValueError(f"strict policy source missing {missing}")
    require_unique(strict, MODEL_KEY, "fresh strict policy source")

    frames: dict[str, pd.DataFrame] = {}
    for label, frame in (("frozen", frozen), ("candidate", candidate)):
        required = [*MODEL_KEY, "game_date", "sim_p_over"]
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise ValueError(f"{label} model missing {missing}")
        out = _normalise_date(frame, "game_date", f"{label} model")
        require_unique(out, MODEL_KEY, f"{label} model")
        probability = pd.to_numeric(out.sim_p_over, errors="coerce")
        if probability.isna().any() or not probability.between(0.0, 1.0).all():
            raise ValueError(f"{label} model has an invalid probability")
        frames[label] = out

    frozen_keys = set(map(tuple, frames["frozen"][MODEL_KEY].to_numpy()))
    candidate_keys = set(map(tuple, frames["candidate"][MODEL_KEY].to_numpy()))
    if frozen_keys != candidate_keys:
        raise ValueError("frozen and candidate MODEL_KEY sets differ")
    strict_keys = set(map(tuple, strict[MODEL_KEY].to_numpy()))
    missing_model = strict_keys - frozen_keys
    if missing_model:
        raise ValueError(
            f"strict policy source has {len(missing_model)} MODEL_KEY(s) absent "
            "from both model arms"
        )

    required_official = [*OUTCOME_KEY, "game_date", "actual_value"]
    missing = [column for column in required_official if column not in official.columns]
    if missing:
        raise ValueError(f"official actuals missing {missing}")
    truth = _normalise_date(official, "game_date", "official actuals")
    require_unique(truth, OUTCOME_KEY, "official actuals")

    pairs = strict[required_market].merge(
        frames["frozen"][[*MODEL_KEY, "game_date", "sim_p_over"]].rename(
            columns={"game_date": "frozen_game_date", "sim_p_over": "p_frozen"}
        ),
        on=MODEL_KEY,
        how="left",
        validate="one_to_one",
    ).merge(
        frames["candidate"][[*MODEL_KEY, "game_date", "sim_p_over"]].rename(
            columns={"game_date": "candidate_game_date", "sim_p_over": "p_candidate"}
        ),
        on=MODEL_KEY,
        how="left",
        validate="one_to_one",
    ).merge(
        truth[[*OUTCOME_KEY, "game_date", "actual_value"]].rename(
            columns={"game_date": "official_target_date"}
        ),
        on=OUTCOME_KEY,
        how="left",
        validate="many_to_one",
    )
    if len(pairs) != len(strict):
        raise AssertionError("policy join changed strict-source row count")
    if pairs[["p_frozen", "p_candidate", "actual_value"]].isna().any().any():
        raise ValueError("strict policy source has a missing model probability or MLB target")
    mean_drift = float((pairs.p_candidate - pairs.p_frozen).abs().mean())
    if not np.isfinite(mean_drift) or mean_drift < 1e-9:
        raise ValueError("candidate is inert on the strict policy-fit universe")
    bad_dates = pairs[
        (pairs.official_game_date != pairs.frozen_game_date)
        | (pairs.official_game_date != pairs.candidate_game_date)
        | (pairs.official_game_date != pairs.official_target_date)
    ]
    if len(bad_dates):
        raise ValueError(
            "market/model/target canonical dates disagree:\n"
            + bad_dates.head(20).to_string(index=False)
        )
    return pairs, funnel


def arm_policy_rows(pairs: pd.DataFrame, arm: str) -> pd.DataFrame:
    """Return payout-aware candidate rows for one model arm.

    The chosen side is the side with larger expected profit at the exact
    posted price.  A positive de-vig edge is not enough: rows with negative
    posted-price expected profit remain in the diagnostic frame but can never
    pass a non-negative policy threshold.
    """

    if arm not in ARM_COLUMNS:
        raise ValueError(f"unknown arm {arm!r}")
    p = pd.to_numeric(pairs[ARM_COLUMNS[arm]], errors="raise").to_numpy(float)
    over_decimal = pd.to_numeric(
        pairs.entry_over_odds_decimal, errors="raise"
    ).to_numpy(float)
    under_decimal = pd.to_numeric(
        pairs.entry_under_odds_decimal, errors="raise"
    ).to_numpy(float)
    if (
        (~np.isfinite(over_decimal)).any()
        or (~np.isfinite(under_decimal)).any()
        or (over_decimal <= 1.0).any()
        or (under_decimal <= 1.0).any()
    ):
        raise ValueError("policy rows contain invalid posted decimal odds")

    over_ev = p * over_decimal - 1.0
    under_ev = (1.0 - p) * under_decimal - 1.0
    bet_over = over_ev >= under_ev
    expected_profit = np.where(bet_over, over_ev, under_ev)
    market_edge = np.abs(p - pairs.entry_p_over.to_numpy(float))
    clv = np.where(
        bet_over,
        pairs.close_p_over.to_numpy(float) - pairs.entry_p_over.to_numpy(float),
        pairs.entry_p_over.to_numpy(float) - pairs.close_p_over.to_numpy(float),
    )
    won_over = (pairs.actual_value.to_numpy(float) > pairs.line.to_numpy(float))
    won = np.where(bet_over, won_over, ~won_over)
    chosen_decimal = np.where(bet_over, over_decimal, under_decimal)
    realised_profit = np.where(won, chosen_decimal - 1.0, -1.0)

    return pd.DataFrame(
        {
            "official_game_date": pairs.official_game_date.to_numpy(),
            "expected_profit": expected_profit,
            "market_edge": market_edge,
            "clv": clv,
            "won": won.astype(int),
            "realised_profit": realised_profit,
            "selection_side": np.where(bet_over, "over", "under"),
        },
        index=pairs.index,
    )


def policy_metrics(rows: pd.DataFrame, min_expected_profit: float) -> PolicyMetrics:
    threshold = float(min_expected_profit)
    if not np.isfinite(threshold) or threshold < 0.0:
        raise ValueError("min_expected_profit must be finite and non-negative")
    selected = rows[rows.expected_profit >= threshold]
    denominator = float(selected.market_edge.sum())
    capture = (
        float(selected.clv.sum() / denominator)
        if len(selected) and denominator > 0.0
        else float("nan")
    )
    return PolicyMetrics(
        selected_rows=int(len(selected)),
        selected_dates=int(selected.official_game_date.nunique()),
        capture=capture,
        flat_stake_roi=(
            float(selected.realised_profit.mean()) if len(selected) else float("nan")
        ),
        mean_expected_profit=(
            float(selected.expected_profit.mean()) if len(selected) else float("nan")
        ),
    )


def empirical_thresholds(rows: pd.DataFrame) -> list[float]:
    """Build an outcome-blind threshold family tied to temporal sample size.

    There is one interior empirical quantile per independent date block, plus
    the structural zero-EV boundary.  The family therefore grows with the
    effective temporal sample rather than with thousands of correlated player
    rows.  Outcomes, CLV, wins, and the confirmation period are not inputs.
    """

    dates = int(rows.official_game_date.nunique())
    if dates < 2:
        raise ValueError("at least two date blocks are required")
    positive = rows.loc[rows.expected_profit >= 0.0, "expected_profit"].to_numpy(float)
    if not len(positive):
        return [0.0]
    levels = np.arange(1, dates + 1, dtype=float) / (dates + 1.0)
    values = [0.0, *np.quantile(positive, levels).astype(float).tolist()]
    return sorted(set(values))


def _date_totals(
    rows: pd.DataFrame, threshold: float, dates: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return aligned per-date totals for vectorized block resampling."""

    selected = rows[rows.expected_profit >= threshold]
    grouped = selected.groupby("official_game_date", sort=False).agg(
        edge=("market_edge", "sum"),
        clv=("clv", "sum"),
        profit=("realised_profit", "sum"),
        count=("realised_profit", "size"),
    )
    grouped = grouped.reindex(dates, fill_value=0.0)
    return tuple(
        grouped[column].to_numpy(float)
        for column in ("edge", "clv", "profit", "count")
    )  # type: ignore[return-value]


def _bootstrap_weights(dates: int, bootstrap: int, seed: int) -> np.ndarray:
    """Sample date blocks as multinomial counts, preserving exact pairing."""

    rng = np.random.default_rng(seed)
    return rng.multinomial(dates, np.full(dates, 1.0 / dates), size=bootstrap)


def date_block_interval(
    rows: pd.DataFrame,
    min_expected_profit: float,
    *,
    block_dates: Iterable[str] | None = None,
    bootstrap: int,
    seed: int,
    metric: str = "capture",
) -> BlockInterval:
    """Return a date-block percentile interval for capture or flat-stake ROI."""

    if bootstrap <= 0:
        raise ValueError("bootstrap must be positive")
    if metric not in {"capture", "flat_stake_roi"}:
        raise ValueError("metric must be 'capture' or 'flat_stake_roi'")
    observed = set(rows.official_game_date.astype(str).unique())
    declared = observed if block_dates is None else {str(value) for value in block_dates}
    if observed - declared:
        raise ValueError("policy rows contain a date outside the declared block universe")
    unique = np.array(sorted(declared))
    if len(unique) < 2:
        return BlockInterval(float("nan"), float("nan"), 0)
    edge, clv, profit, count = _date_totals(rows, min_expected_profit, unique)
    weights = _bootstrap_weights(len(unique), bootstrap, seed)
    denominator = weights @ (edge if metric == "capture" else count)
    numerator = weights @ (clv if metric == "capture" else profit)
    valid = denominator > 0.0
    values = numerator[valid] / denominator[valid]
    if not len(values):
        return BlockInterval(float("nan"), float("nan"), 0)
    return BlockInterval(
        lower=float(np.percentile(values, 2.5)),
        upper=float(np.percentile(values, 97.5)),
        valid_draws=len(values),
    )


def paired_capture_change_interval(
    frozen_rows: pd.DataFrame,
    candidate_rows: pd.DataFrame,
    min_expected_profit: float,
    *,
    block_dates: Iterable[str] | None = None,
    bootstrap: int,
    seed: int,
) -> BlockInterval:
    """Paired date-block interval for candidate-minus-frozen capture."""

    if bootstrap <= 0:
        raise ValueError("bootstrap must be positive")
    f_dates = set(frozen_rows.official_game_date.unique())
    c_dates = set(candidate_rows.official_game_date.unique())
    if f_dates != c_dates:
        raise ValueError("paired arms do not contain the same observed date blocks")
    declared = f_dates if block_dates is None else {str(value) for value in block_dates}
    if f_dates - declared or len(declared) < 2:
        raise ValueError("paired rows violate the declared two-or-more date blocks")
    unique = np.array(sorted(declared))
    f_edge, f_clv, _, _ = _date_totals(frozen_rows, min_expected_profit, unique)
    c_edge, c_clv, _, _ = _date_totals(candidate_rows, min_expected_profit, unique)
    weights = _bootstrap_weights(len(unique), bootstrap, seed)
    f_denominator = weights @ f_edge
    c_denominator = weights @ c_edge
    valid = (f_denominator > 0.0) & (c_denominator > 0.0)
    values = (
        (weights[valid] @ c_clv) / c_denominator[valid]
        - (weights[valid] @ f_clv) / f_denominator[valid]
    )
    if not len(values):
        return BlockInterval(float("nan"), float("nan"), 0)
    return BlockInterval(
        lower=float(np.percentile(values, 2.5)),
        upper=float(np.percentile(values, 97.5)),
        valid_draws=len(values),
    )


def choose_threshold(
    selector_rows: pd.DataFrame,
    *,
    expected_dates: Iterable[str],
    bootstrap: int,
    seed: int,
) -> tuple[float, pd.DataFrame]:
    """Select one EV threshold using selector dates only.

    The objective is the lower 95% date-block bound on capture.  Policies that
    do not select at least one row on every selector date are ineligible.  The
    deterministic tie-break prefers more rows and then the lower threshold,
    avoiding an unnecessarily narrow, brittle bet definition.
    """

    declared = {str(value) for value in expected_dates}
    observed = set(selector_rows.official_game_date.astype(str).unique())
    if not declared or observed != declared:
        raise ValueError(
            "threshold selector rows do not exactly match the locked selector dates"
        )
    dates = len(declared)
    records: list[dict[str, Any]] = []
    for threshold in empirical_thresholds(selector_rows):
        metrics = policy_metrics(selector_rows, threshold)
        interval = date_block_interval(
            selector_rows,
            threshold,
            block_dates=declared,
            bootstrap=bootstrap,
            seed=seed,
            metric="capture",
        )
        records.append(
            {
                "min_expected_profit": threshold,
                **metrics.__dict__,
                "capture_lower": interval.lower,
                "capture_upper": interval.upper,
                "valid_bootstrap_draws": interval.valid_draws,
                "eligible_all_selector_dates": metrics.selected_dates == dates,
            }
        )
    table = pd.DataFrame(records)
    eligible = table[
        table.eligible_all_selector_dates
        & np.isfinite(table.capture_lower)
        & table.valid_bootstrap_draws.eq(bootstrap)
    ].copy()
    if eligible.empty:
        raise ValueError("no threshold has complete selector-date evidence")
    winner = eligible.sort_values(
        ["capture_lower", "selected_rows", "min_expected_profit"],
        ascending=[False, False, True],
        kind="mergesort",
    ).iloc[0]
    return float(winner.min_expected_profit), table
