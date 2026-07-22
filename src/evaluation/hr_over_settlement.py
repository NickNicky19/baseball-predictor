"""Side-specific official grading for DraftKings pregame HR Over 0.5."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from src.evaluation.hr_over_evaluation import MODEL_KEY, require_unique
from src.evaluation.official_game_completion import validate_game_completion
from src.evaluation.official_hitter_eligibility import (
    ELIGIBILITY_KEY,
    validate_eligibility,
)


GRADED_WIN = "GRADED_WIN"
GRADED_LOSS = "GRADED_LOSS"
VOID_NONSTARTER = "VOID_NONSTARTER"
VOID_NO_PA = "VOID_NO_PA"
VOID_EARLY_EXIT = "VOID_EARLY_EXIT"
VOID_GAME_LENGTH = "VOID_GAME_LENGTH"
UNRESOLVED_OFFICIAL_ROLE = "UNRESOLVED_OFFICIAL_ROLE"
UNRESOLVED_GAME_COMPLETION = "UNRESOLVED_GAME_COMPLETION"
UNRESOLVED_ONE_PA_STARTER = "UNRESOLVED_ONE_PA_STARTER"

GRADEABLE = {GRADED_WIN, GRADED_LOSS}
STATUSES = GRADEABLE | {
    VOID_NONSTARTER,
    VOID_NO_PA,
    VOID_EARLY_EXIT,
    VOID_GAME_LENGTH,
    UNRESOLVED_OFFICIAL_ROLE,
    UNRESOLVED_GAME_COMPLETION,
    UNRESOLVED_ONE_PA_STARTER,
}


def _nonnegative_integer(value: Any, label: str) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not np.isfinite(numeric) or numeric < 0 or numeric != np.floor(numeric):
        raise ValueError(f"{label} must be a nonnegative integer")
    return int(numeric)


def hr_over_grade(
    *,
    is_starter: bool,
    official_pa: int | float,
    starter_replaced_in_slot: bool,
    official_home_runs: int | float,
    regular_game_completed: bool | None,
) -> str:
    """Apply the published over-side participation/early-exit/game rules.

    An HR already makes Over 0.5 unconditional.  It does not override the
    separate requirement that the quoted player started and participated.
    """
    pa = _nonnegative_integer(official_pa, "official_pa")
    home_runs = _nonnegative_integer(official_home_runs, "official_home_runs")
    if home_runs > pa:
        raise ValueError("official home runs cannot exceed official PA")
    if not bool(is_starter):
        return VOID_NONSTARTER
    if pa == 0:
        if home_runs:
            raise ValueError("a zero-PA starter cannot have an official home run")
        return VOID_NO_PA
    if home_runs >= 1:
        return GRADED_WIN
    if regular_game_completed is None or pd.isna(regular_game_completed):
        return UNRESOLVED_GAME_COMPLETION
    if not bool(regular_game_completed):
        return VOID_GAME_LENGTH
    if pa == 1:
        return (
            VOID_EARLY_EXIT
            if bool(starter_replaced_in_slot)
            else UNRESOLVED_ONE_PA_STARTER
        )
    return GRADED_LOSS


def build_settlement_bridge(
    scored_market: pd.DataFrame,
    eligibility: pd.DataFrame,
    game_completion: pd.DataFrame,
    allowed_dates: list[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach official role/game facts without dropping unresolved rows."""
    required = [*MODEL_KEY, "official_game_date", "actual_value", "settlement_present"]
    missing = [column for column in required if column not in scored_market.columns]
    if missing:
        raise ValueError(f"scored HR market missing {missing}")
    market = scored_market.copy()
    require_unique(market, MODEL_KEY, "scored HR market")
    market["official_game_date"] = pd.to_datetime(
        market.official_game_date, errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    if market.official_game_date.isna().any():
        raise ValueError("scored HR market has invalid official date")
    observed = set(market.official_game_date.unique())
    declared = set(str(value) for value in allowed_dates)
    if observed != declared:
        raise ValueError("settlement bridge dates differ from the exact open universe")
    if any(value.startswith("2026-05-") for value in observed):
        raise ValueError("May leaked into the HR settlement bridge")

    validate_eligibility(eligibility)
    validate_game_completion(game_completion)
    role_columns = [
        *ELIGIBILITY_KEY, "official_game_date", "is_starter",
        "starter_replaced_in_slot", "official_pa",
    ]
    roles = eligibility[role_columns].rename(
        columns={"official_game_date": "official_game_date_role"}
    )
    games = game_completion.rename(
        columns={"official_game_date": "official_game_date_game"}
    )
    joined = market.merge(roles, on=ELIGIBILITY_KEY, how="left", validate="many_to_one")
    joined = joined.merge(games, on="mlb_game_pk", how="left", validate="many_to_one")

    role_resolved = joined.is_starter.notna() & joined.official_pa.notna()
    game_resolved = joined.regular_game_completed.notna()
    role_date_bad = role_resolved & joined.official_game_date.ne(joined.official_game_date_role)
    game_date_bad = game_resolved & joined.official_game_date.ne(joined.official_game_date_game)
    if role_date_bad.any() or game_date_bad.any():
        raise ValueError("official market/role/game date disagreement")

    statuses: list[str] = []
    for row in joined.itertuples(index=False):
        if pd.isna(row.is_starter) or pd.isna(row.official_pa):
            statuses.append(UNRESOLVED_OFFICIAL_ROLE)
            continue
        regular = (
            None if pd.isna(row.regular_game_completed)
            else bool(row.regular_game_completed)
        )
        statuses.append(hr_over_grade(
            is_starter=bool(row.is_starter),
            official_pa=row.official_pa,
            starter_replaced_in_slot=bool(row.starter_replaced_in_slot),
            official_home_runs=row.actual_value,
            regular_game_completed=regular,
        ))
    joined["official_grade_status"] = statuses
    joined["official_gradeable"] = joined.official_grade_status.isin(GRADEABLE)
    joined["official_won"] = joined.official_grade_status.eq(GRADED_WIN)
    validate_settlement_bridge(joined)
    counts = joined.official_grade_status.value_counts().to_dict()
    funnel = {
        "input_rows": int(len(market)),
        "role_resolved": int(role_resolved.sum()),
        "role_unresolved": int((~role_resolved).sum()),
        "game_completion_resolved": int(game_resolved.sum()),
        "game_completion_unresolved": int((~game_resolved).sum()),
        "gradeable_rows": int(joined.official_gradeable.sum()),
        "void_or_unresolved_rows": int((~joined.official_gradeable).sum()),
        "status_counts": {str(key): int(value) for key, value in counts.items()},
    }
    return joined.sort_values(MODEL_KEY).reset_index(drop=True), funnel


def validate_settlement_bridge(frame: pd.DataFrame) -> None:
    required = [*MODEL_KEY, "actual_value", "official_grade_status",
                "official_gradeable", "official_won"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"HR settlement bridge missing {missing}")
    require_unique(frame, MODEL_KEY, "HR settlement bridge")
    unknown = set(frame.official_grade_status.astype(str).unique()) - STATUSES
    if unknown:
        raise ValueError(f"HR settlement bridge has unknown statuses {sorted(unknown)}")
    gradeable = frame.official_grade_status.isin(GRADEABLE)
    if not frame.official_gradeable.astype(bool).equals(gradeable):
        raise ValueError("HR gradeable flag contradicts grade status")
    won = frame.official_grade_status.eq(GRADED_WIN)
    if not frame.official_won.astype(bool).equals(won):
        raise ValueError("HR win flag contradicts grade status")
    actual_hr = pd.to_numeric(frame.actual_value, errors="coerce")
    if actual_hr.isna().any() or (actual_hr < 0).any():
        raise ValueError("HR settlement bridge has invalid official actual")
    if not actual_hr[won].ge(1).all() or not actual_hr[frame.official_grade_status.eq(GRADED_LOSS)].eq(0).all():
        raise ValueError("HR official actual contradicts graded win/loss")


def attach_official_grades(
    scored_rows: pd.DataFrame, settlement_bridge: pd.DataFrame
) -> pd.DataFrame:
    """Attach one official grade to every already-scored arm row."""
    validate_settlement_bridge(settlement_bridge)
    required = [*MODEL_KEY, "arm", "positive_ev", "entry_decimal_odds",
                "official_game_date"]
    missing = [column for column in required if column not in scored_rows.columns]
    if missing:
        raise ValueError(f"HR scored rows missing {missing}")
    if scored_rows.duplicated([*MODEL_KEY, "arm"]).any():
        raise ValueError("HR scored arm rows are duplicated")
    grades = settlement_bridge[
        [*MODEL_KEY, "official_grade_status", "official_gradeable", "official_won"]
    ]
    joined = scored_rows.merge(grades, on=MODEL_KEY, how="left", validate="many_to_one")
    if joined.official_grade_status.isna().any():
        raise ValueError("an HR scored row lacks an official grade")
    odds = pd.to_numeric(joined.entry_decimal_odds, errors="coerce")
    if odds.isna().any() or (odds <= 1).any():
        raise ValueError("HR regrading received invalid decimal odds")
    joined["official_realised_profit"] = np.where(
        joined.official_won.astype(bool), odds - 1.0, -1.0
    )
    joined.loc[~joined.official_gradeable.astype(bool), "official_realised_profit"] = np.nan
    return joined


def official_positive_ev_metrics(rows: pd.DataFrame) -> dict[str, Any]:
    selected = rows[rows.positive_ev.astype(bool)]
    gradeable = selected[selected.official_gradeable.astype(bool)]
    void_or_unresolved = selected[~selected.official_gradeable.astype(bool)]
    return {
        "positive_ev_rows": int(len(selected)),
        "official_gradeable_rows": int(len(gradeable)),
        "void_or_unresolved_rows": int(len(void_or_unresolved)),
        "official_flat_stake_roi": (
            float(gradeable.official_realised_profit.mean()) if len(gradeable) else None
        ),
        "official_wins": int(gradeable.official_won.astype(bool).sum()),
        "official_losses": int((~gradeable.official_won.astype(bool)).sum()),
    }


def official_roi_interval(
    rows: pd.DataFrame, declared_dates: list[str], draws: int, seed: int
) -> dict[str, Any]:
    if draws <= 0:
        raise ValueError("bootstrap draws must be positive")
    dates = [str(value) for value in declared_dates]
    if set(rows.official_game_date.astype(str).unique()) != set(dates):
        raise ValueError("HR regraded bootstrap dates differ from declared dates")
    selected = rows[
        rows.positive_ev.astype(bool) & rows.official_gradeable.astype(bool)
    ]
    totals = selected.groupby("official_game_date").agg(
        profit=("official_realised_profit", "sum"),
        count=("official_realised_profit", "size"),
    ).reindex(dates, fill_value=0.0)
    rng = np.random.default_rng(int(seed))
    weights = rng.multinomial(
        len(dates), np.full(len(dates), 1.0 / len(dates)), size=int(draws)
    )
    denominator = weights @ totals["count"].to_numpy(float)
    valid = denominator > 0
    roi = (weights[valid] @ totals.profit.to_numpy(float)) / denominator[valid]
    return {
        "draws_requested": int(draws),
        "valid_draws": int(valid.sum()),
        "official_flat_stake_roi_95": [
            float(np.percentile(roi, 2.5)), float(np.percentile(roi, 97.5))
        ],
    }
