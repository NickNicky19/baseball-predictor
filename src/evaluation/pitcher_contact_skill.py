"""Pure chronology and scoring helpers for opposing-pitcher contact quality."""
from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Iterable

import numpy as np
import pandas as pd

from src.evaluation.hit_contact_skill import ScoreInterval, paired_date_score_interval


@dataclass(frozen=True)
class PitcherContactFit:
    prior_strength: float
    alpha: float
    log_loss: float
    brier: float


def build_prior_window_profiles(
    contacts: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    window_days: int,
) -> pd.DataFrame:
    """Build strict-prior pitcher and league xBA profiles at target dates."""
    if window_days <= 0:
        raise ValueError("pitcher-contact window must be positive")
    contact_required = {"contact_date", "pitcher_id", "xba"}
    target_required = {"game_date", "opp_sp_id"}
    if contact_required - set(contacts.columns):
        raise ValueError(f"pitcher contacts missing {sorted(contact_required - set(contacts.columns))}")
    if target_required - set(targets.columns):
        raise ValueError(f"pitcher targets missing {sorted(target_required - set(targets.columns))}")
    raw = contacts.copy()
    target = targets[["game_date", "opp_sp_id"]].drop_duplicates().copy()
    if target.isna().any().any():
        raise ValueError("pitcher profile target has a null identity")
    raw["contact_date"] = pd.to_datetime(raw.contact_date, errors="raise").dt.normalize()
    raw["pitcher_id"] = pd.to_numeric(raw.pitcher_id, errors="raise").astype(int)
    raw["xba"] = pd.to_numeric(raw.xba, errors="raise")
    if raw[["contact_date", "pitcher_id", "xba"]].isna().any().any():
        raise ValueError("pitcher contact evidence has a null identity or value")
    if not np.isfinite(raw.xba.to_numpy(float)).all() or not raw.xba.between(0.0, 1.0).all():
        raise ValueError("raw pitcher xBA lies outside [0,1]")
    target["target_date"] = pd.to_datetime(target.game_date, errors="raise").dt.normalize()
    target["opp_sp_id"] = pd.to_numeric(target.opp_sp_id, errors="raise").astype(int)

    def prior_sums(evidence: pd.DataFrame, request_dates: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ordered = evidence.sort_values("contact_date")
        dates = ordered.contact_date.to_numpy(dtype="datetime64[ns]")
        values = ordered.xba.to_numpy(float)
        cumulative = np.concatenate(([0.0], np.cumsum(values)))
        right = np.searchsorted(dates, request_dates, side="left")
        left = np.searchsorted(
            dates, request_dates - np.timedelta64(window_days, "D"), side="left"
        )
        return cumulative[right] - cumulative[left], (right - left).astype(float)

    request_dates = target.target_date.to_numpy(dtype="datetime64[ns]")
    league_sum, league_n = prior_sums(raw, request_dates)
    if (league_n <= 0).any():
        examples = target.loc[league_n <= 0, ["game_date"]].head(10)
        raise ValueError("target date has no strict-prior league contact evidence:\n" + examples.to_string(index=False))
    target["league_bip"] = league_n
    target["league_xba"] = league_sum / league_n
    target["pitcher_bip"] = 0.0
    target["pitcher_xba"] = target.league_xba
    contact_groups = {int(pid): group for pid, group in raw.groupby("pitcher_id", sort=False)}
    for pitcher_id, index in target.groupby("opp_sp_id", sort=False).groups.items():
        evidence = contact_groups.get(int(pitcher_id))
        if evidence is None:
            continue
        target_dates = target.loc[index, "target_date"].to_numpy(dtype="datetime64[ns]")
        pitcher_sum, pitcher_n = prior_sums(evidence, target_dates)
        target.loc[index, "pitcher_bip"] = pitcher_n
        has_history = pitcher_n > 0
        if has_history.any():
            positions = np.asarray(list(index))
            target.loc[positions[has_history], "pitcher_xba"] = (
                pitcher_sum[has_history] / pitcher_n[has_history]
            )
    if not target.pitcher_xba.between(0.0, 1.0).all():
        raise ValueError("built pitcher xBA lies outside [0,1]")
    target["pitcher_history_present"] = target.pitcher_bip.gt(0)
    return target[
        ["game_date", "opp_sp_id", "pitcher_xba", "pitcher_bip", "league_xba", "league_bip", "pitcher_history_present"]
    ]


def _logit(probability: np.ndarray) -> np.ndarray:
    clipped = np.clip(probability, 1e-12, 1.0 - 1e-12)
    return np.log(clipped / (1.0 - clipped))


def _sigmoid(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def validate_pitcher_contact_rows(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "game_pk", "player_id", "game_date", "opp_sp_id", "baseline_p",
        "pitcher_xba", "pitcher_bip", "league_xba", "league_bip",
        "contact_events", "hits_on_contact",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"pitcher-contact rows missing {missing}")
    out = frame.copy()
    key = ["game_pk", "player_id"]
    if out[key].isna().any().any() or out.duplicated(key).any():
        raise ValueError("pitcher-contact rows have a null or duplicate player-game key")
    if out.opp_sp_id.isna().any():
        raise ValueError("pitcher-contact rows have a null opponent pitcher id")
    out["game_date"] = pd.to_datetime(out.game_date, errors="raise").dt.strftime("%Y-%m-%d")
    numeric = required - {"game_date"}
    for column in numeric:
        out[column] = pd.to_numeric(out[column], errors="raise")
    if not np.isfinite(out[list(numeric)].to_numpy(float)).all():
        raise ValueError("pitcher-contact rows contain a non-finite number")
    if not out.baseline_p.between(0.0, 1.0).all():
        raise ValueError("baseline probability lies outside [0,1]")
    if not out.pitcher_xba.between(0.0, 1.0).all() or not out.league_xba.between(0.0, 1.0).all():
        raise ValueError("pitcher or league xBA lies outside [0,1]")
    if (out.pitcher_bip < 0).any() or (out.league_bip <= 0).any():
        raise ValueError("pitcher or league contact evidence is invalid")
    if (out.contact_events <= 0).any() or (out.hits_on_contact < 0).any():
        raise ValueError("contact outcomes are invalid")
    if (out.hits_on_contact > out.contact_events).any():
        raise ValueError("contact hits exceed contact events")
    return out


def add_pitcher_probability(
    frame: pd.DataFrame,
    *,
    prior_strength: float,
    alpha: float,
    output: str = "candidate_p",
) -> pd.DataFrame:
    rows = validate_pitcher_contact_rows(frame)
    if not np.isfinite(alpha):
        raise ValueError("pitcher-contact alpha is non-finite")
    n = rows.pitcher_bip.to_numpy(float)
    raw = rows.pitcher_xba.to_numpy(float)
    anchor = rows.league_xba.to_numpy(float)
    if prior_strength == inf:
        shrunk = anchor.copy()
    else:
        if not np.isfinite(prior_strength) or prior_strength < 0.0:
            raise ValueError("pitcher prior strength must be non-negative or infinity")
        denominator = n + float(prior_strength)
        neutral = denominator <= 0.0
        safe_denominator = np.where(neutral, 1.0, denominator)
        shrunk = (n * raw + float(prior_strength) * anchor) / safe_denominator
        shrunk[neutral] = anchor[neutral]
    signal = _logit(shrunk) - _logit(anchor)
    probability = _sigmoid(_logit(rows.baseline_p.to_numpy(float)) + float(alpha) * signal)
    neutral = signal == 0.0
    probability[neutral] = rows.baseline_p.to_numpy(float)[neutral]
    if not np.isfinite(probability).all() or ((probability < 0.0) | (probability > 1.0)).any():
        raise ValueError("pitcher-adjusted probability lies outside [0,1]")
    out = rows.copy()
    out["pitcher_shrunk_xba"] = shrunk
    out["pitcher_logit_signal"] = signal
    out[output] = probability
    return out


def _aggregate_scores(rows: pd.DataFrame, probability: np.ndarray) -> tuple[float, float]:
    n = rows.contact_events.to_numpy(float)
    y = rows.hits_on_contact.to_numpy(float)
    clipped = np.clip(probability, 1e-12, 1.0 - 1e-12)
    log_loss = float(-np.sum(y * np.log(clipped) + (n - y) * np.log(1.0 - clipped)) / n.sum())
    brier = float(np.sum(y * (1.0 - probability) ** 2 + (n - y) * probability**2) / n.sum())
    return log_loss, brier


def fit_alpha(frame: pd.DataFrame, prior_strength: float) -> PitcherContactFit:
    rows = add_pitcher_probability(frame, prior_strength=prior_strength, alpha=0.0)
    signal = rows.pitcher_logit_signal.to_numpy(float)
    if np.all(signal == 0.0):
        alpha = 0.0
    else:
        offset = _logit(rows.baseline_p.to_numpy(float))
        n = rows.contact_events.to_numpy(float)
        y = rows.hits_on_contact.to_numpy(float)
        alpha = 0.0
        converged = False
        for _ in range(200):
            probability = _sigmoid(offset + alpha * signal)
            score = float(np.sum(signal * (y - n * probability)))
            information = float(np.sum(n * probability * (1.0 - probability) * signal**2))
            if information <= 0.0 or not np.isfinite(information):
                raise ValueError("pitcher-contact coefficient has no finite information")
            step = score / information
            alpha += step
            if not np.isfinite(alpha):
                raise ValueError("pitcher-contact coefficient diverged")
            if abs(step) <= 1e-12:
                converged = True
                break
        if not converged:
            raise ValueError("pitcher-contact coefficient did not converge")
    fitted = add_pitcher_probability(rows, prior_strength=prior_strength, alpha=alpha)
    log_loss, brier = _aggregate_scores(fitted, fitted.candidate_p.to_numpy(float))
    return PitcherContactFit(prior_strength, float(alpha), log_loss, brier)


def fit_pitcher_contact(frame: pd.DataFrame) -> PitcherContactFit:
    rows = validate_pitcher_contact_rows(frame)
    observed = sorted({float(value) for value in rows.pitcher_bip if float(value) > 0.0})
    candidates = [0.0, *observed, inf]
    fits = [fit_alpha(rows, strength) for strength in candidates]
    fits.sort(key=lambda value: (value.log_loss, value.brier, value.prior_strength))
    if len(fits) > 1 and (fits[0].log_loss, fits[0].brier) == (fits[1].log_loss, fits[1].brier):
        raise ValueError("pitcher-contact fits are exactly tied; refusing to guess")
    return fits[0]


def fit_rolling_fold(
    frame: pd.DataFrame,
    *,
    fit_seasons: Iterable[int],
    evaluation_season: int,
) -> tuple[PitcherContactFit, pd.DataFrame]:
    """Fit only on declared earlier seasons and return the untouched evaluation rows."""
    rows = validate_pitcher_contact_rows(frame)
    if "season" not in rows.columns:
        raise ValueError("rolling pitcher-contact rows lack season")
    season = pd.to_numeric(rows.season, errors="raise").astype(int)
    declared = {int(value) for value in fit_seasons}
    evaluation_season = int(evaluation_season)
    if not declared or max(declared) >= evaluation_season:
        raise ValueError("pitcher-contact fold seasons are not ordered")
    unexpected = set(season.unique()) - declared - {evaluation_season}
    invalid_unexpected = {value for value in unexpected if value <= evaluation_season}
    if invalid_unexpected:
        raise ValueError(
            "pitcher-contact fold contains undeclared non-future seasons "
            f"{sorted(invalid_unexpected)}"
        )
    fit_rows = rows[season.isin(declared)].copy()
    evaluation = rows[season.eq(evaluation_season)].copy()
    if fit_rows.empty or evaluation.empty or max(fit_rows.game_date) >= min(evaluation.game_date):
        raise ValueError("pitcher-contact rolling fold chronology is invalid")
    return fit_pitcher_contact(fit_rows), evaluation


def paired_interval(
    frame: pd.DataFrame,
    *,
    metric: str,
    declared_dates: Iterable[str],
    bootstrap: int,
    seed: int,
) -> ScoreInterval:
    rows = validate_pitcher_contact_rows(frame)
    required = {"candidate_p", "baseline_p"}
    if required - set(frame.columns):
        raise ValueError("paired pitcher-contact frame lacks candidate or baseline")
    bridge = rows.rename(columns={"baseline_p": "baseline_for_score"}).copy()
    bridge["baseline_p"] = pd.to_numeric(frame.loc[rows.index, "baseline_p"], errors="raise")
    bridge["candidate_p"] = pd.to_numeric(frame.loc[rows.index, "candidate_p"], errors="raise")
    return paired_date_score_interval(
        bridge,
        candidate_column="candidate_p",
        baseline_column="baseline_p",
        metric=metric,
        declared_dates=declared_dates,
        bootstrap=bootstrap,
        seed=seed,
    )
