"""Chronology-safe hitter splits by opposing-pitcher handedness."""
from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SplitFit:
    prior_strength: float
    alpha: float
    log_loss: float
    brier: float


@dataclass(frozen=True)
class SplitInterval:
    lower: float
    upper: float
    valid_draws: int


def _logit(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, 1e-12, 1.0 - 1e-12)
    return np.log(clipped / (1.0 - clipped))


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -35.0, 35.0)))


def _prefix_window(
    evidence: pd.DataFrame,
    target_dates: np.ndarray,
    *,
    sum_column: str,
    count_column: str,
    window_days: int,
) -> tuple[np.ndarray, np.ndarray]:
    if evidence.empty:
        return np.zeros(len(target_dates)), np.zeros(len(target_dates))
    ordered = evidence.sort_values("event_date")
    dates = ordered.event_date.to_numpy(dtype="datetime64[ns]")
    sums = ordered[sum_column].to_numpy(float)
    counts = ordered[count_column].to_numpy(float)
    cumulative_sum = np.concatenate(([0.0], np.cumsum(sums)))
    cumulative_count = np.concatenate(([0.0], np.cumsum(counts)))
    right = np.searchsorted(dates, target_dates, side="left")
    left = np.searchsorted(
        dates, target_dates - np.timedelta64(window_days, "D"), side="left"
    )
    return (
        cumulative_sum[right] - cumulative_sum[left],
        cumulative_count[right] - cumulative_count[left],
    )


def build_prior_split_profiles(
    terminal_pa: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    window_days: int,
) -> pd.DataFrame:
    required_pa = {"event_date", "batter_id", "p_throws", "contact_xba", "is_k", "is_bb"}
    required_target = {"game_date", "player_id", "opp_sp_throws"}
    if required_pa - set(terminal_pa.columns):
        raise ValueError(f"terminal PA evidence missing {sorted(required_pa - set(terminal_pa.columns))}")
    if required_target - set(targets.columns):
        raise ValueError(f"split targets missing {sorted(required_target - set(targets.columns))}")
    if window_days <= 0:
        raise ValueError("split history window must be positive")
    evidence = terminal_pa.copy()
    target = targets[list(required_target)].drop_duplicates().reset_index(drop=True)
    evidence["event_date"] = pd.to_datetime(evidence.event_date, errors="raise").dt.normalize()
    evidence["batter_id"] = pd.to_numeric(evidence.batter_id, errors="raise").astype(int)
    target["target_date"] = pd.to_datetime(target.game_date, errors="raise").dt.normalize()
    target["player_id"] = pd.to_numeric(target.player_id, errors="raise").astype(int)
    for frame, column in ((evidence, "p_throws"), (target, "opp_sp_throws")):
        frame[column] = frame[column].astype(str).str.upper()
        if not frame[column].isin({"L", "R"}).all():
            raise ValueError(f"invalid pitcher-handedness value in {column}")
    evidence["is_k"] = pd.to_numeric(evidence.is_k, errors="raise")
    evidence["is_bb"] = pd.to_numeric(evidence.is_bb, errors="raise")
    if not evidence.is_k.isin({0, 1}).all() or not evidence.is_bb.isin({0, 1}).all():
        raise ValueError("K/BB terminal indicators must be binary")
    evidence["pa_n"] = 1.0
    evidence["contact_n"] = evidence.contact_xba.notna().astype(float)
    evidence["contact_sum"] = pd.to_numeric(evidence.contact_xba, errors="coerce").fillna(0.0)
    if not evidence.loc[evidence.contact_n.eq(1), "contact_sum"].between(0.0, 1.0).all():
        raise ValueError("terminal contact xBA lies outside [0,1]")
    daily = evidence.groupby(
        ["batter_id", "p_throws", "event_date"], as_index=False
    ).agg(
        pa_n=("pa_n", "sum"),
        k_sum=("is_k", "sum"),
        bb_sum=("is_bb", "sum"),
        contact_n=("contact_n", "sum"),
        contact_sum=("contact_sum", "sum"),
    )
    pooled = daily.groupby(["batter_id", "event_date"], as_index=False).agg(
        pa_n=("pa_n", "sum"), k_sum=("k_sum", "sum"), bb_sum=("bb_sum", "sum"),
        contact_n=("contact_n", "sum"), contact_sum=("contact_sum", "sum"),
    )
    output = target.copy()
    for prefix in ("pooled", "split"):
        for metric in ("contact", "k", "bb"):
            output[f"{prefix}_{metric}_sum"] = 0.0
            output[f"{prefix}_{metric}_n"] = 0.0
    pooled_groups = {int(pid): group for pid, group in pooled.groupby("batter_id", sort=False)}
    split_groups = {
        (int(pid), str(hand)): group
        for (pid, hand), group in daily.groupby(["batter_id", "p_throws"], sort=False)
    }
    for player_id, index in output.groupby("player_id", sort=False).groups.items():
        dates = output.loc[index, "target_date"].to_numpy(dtype="datetime64[ns]")
        pooled_evidence = pooled_groups.get(int(player_id), pd.DataFrame())
        for metric, sum_col, n_col in (
            ("contact", "contact_sum", "contact_n"),
            ("k", "k_sum", "pa_n"),
            ("bb", "bb_sum", "pa_n"),
        ):
            sums, counts = _prefix_window(
                pooled_evidence, dates, sum_column=sum_col, count_column=n_col,
                window_days=window_days,
            )
            output.loc[index, f"pooled_{metric}_sum"] = sums
            output.loc[index, f"pooled_{metric}_n"] = counts
        for hand in ("L", "R"):
            hand_index = [value for value in index if output.at[value, "opp_sp_throws"] == hand]
            if not hand_index:
                continue
            hand_dates = output.loc[hand_index, "target_date"].to_numpy(dtype="datetime64[ns]")
            split_evidence = split_groups.get((int(player_id), hand), pd.DataFrame())
            for metric, sum_col, n_col in (
                ("contact", "contact_sum", "contact_n"),
                ("k", "k_sum", "pa_n"),
                ("bb", "bb_sum", "pa_n"),
            ):
                sums, counts = _prefix_window(
                    split_evidence, hand_dates, sum_column=sum_col, count_column=n_col,
                    window_days=window_days,
                )
                output.loc[hand_index, f"split_{metric}_sum"] = sums
                output.loc[hand_index, f"split_{metric}_n"] = counts
    for prefix in ("pooled", "split"):
        for metric in ("contact", "k", "bb"):
            count = output[f"{prefix}_{metric}_n"]
            output[f"{prefix}_{metric}_rate"] = np.where(
                count > 0,
                output[f"{prefix}_{metric}_sum"] / np.where(count > 0, count, 1.0),
                np.nan,
            )
            rate = output[f"{prefix}_{metric}_rate"]
            material_range_error = count.gt(0) & ((rate < -1e-12) | (rate > 1.0 + 1e-12))
            if material_range_error.any():
                raise ValueError(f"built {prefix} {metric} rate materially exceeds [0,1]")
            output.loc[count.gt(0), f"{prefix}_{metric}_rate"] = rate[count.gt(0)].clip(0.0, 1.0)
    return output.drop(columns=["target_date"])


def validate_split_rows(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "game_pk", "player_id", "game_date", "season", "baseline_p",
        "split_rate", "split_n", "pooled_rate", "pooled_n", "exposure", "successes",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"split audit rows missing {missing}")
    out = frame.copy()
    key = ["game_pk", "player_id"]
    if out[key].isna().any().any() or out.duplicated(key).any():
        raise ValueError("split audit rows have a null or duplicate player-game key")
    out["game_date"] = pd.to_datetime(out.game_date, errors="raise").dt.strftime("%Y-%m-%d")
    for column in required - {"game_date"}:
        out[column] = pd.to_numeric(out[column], errors="raise")
    finite_columns = ["game_pk", "player_id", "season", "baseline_p", "split_n", "pooled_n", "exposure", "successes"]
    if not np.isfinite(out[finite_columns].to_numpy(float)).all():
        raise ValueError("split audit contains non-finite required values")
    if not out.baseline_p.between(0.0, 1.0).all():
        raise ValueError("split baseline probability lies outside [0,1]")
    if (out.split_n < 0).any() or (out.pooled_n < 0).any():
        raise ValueError("split evidence count is negative")
    for rate, count in (("split_rate", "split_n"), ("pooled_rate", "pooled_n")):
        present = out[count].gt(0)
        if out.loc[present, rate].isna().any() or not out.loc[present, rate].between(0.0, 1.0).all():
            invalid = present & (out[rate].isna() | ~out[rate].between(0.0, 1.0))
            raise ValueError(
                f"{rate} is invalid where evidence exists; examples:\n"
                + out.loc[invalid, key + ["game_date", rate, count]].head(20).to_string(index=False)
            )
    if (out.exposure <= 0).any() or (out.successes < 0).any() or (out.successes > out.exposure).any():
        raise ValueError("split audit outcomes are invalid")
    return out


def add_split_probability(frame: pd.DataFrame, *, prior_strength: float, alpha: float) -> pd.DataFrame:
    rows = validate_split_rows(frame)
    if not np.isfinite(alpha):
        raise ValueError("split alpha is non-finite")
    split_n = rows.split_n.to_numpy(float)
    pooled_n = rows.pooled_n.to_numpy(float)
    split_rate = rows.split_rate.fillna(0.0).to_numpy(float)
    pooled_rate = rows.pooled_rate.fillna(0.0).to_numpy(float)
    history = (split_n > 0) & (pooled_n > 0)
    shrunk = pooled_rate.copy()
    if prior_strength != inf:
        if not np.isfinite(prior_strength) or prior_strength < 0:
            raise ValueError("split prior must be non-negative or infinity")
        denominator = split_n + float(prior_strength)
        usable = history & (denominator > 0)
        shrunk[usable] = (
            split_n[usable] * split_rate[usable]
            + float(prior_strength) * pooled_rate[usable]
        ) / denominator[usable]
    signal = np.zeros(len(rows), dtype=float)
    signal[history] = _logit(shrunk[history]) - _logit(pooled_rate[history])
    baseline = rows.baseline_p.to_numpy(float)
    candidate = _sigmoid(_logit(baseline) + float(alpha) * signal)
    neutral = signal == 0.0
    candidate[neutral] = baseline[neutral]
    out = rows.copy()
    out["split_shrunk_rate"] = shrunk
    out["split_logit_signal"] = signal
    out["candidate_p"] = candidate
    out["split_history_present"] = history
    return out


def _score(rows: pd.DataFrame, probability: np.ndarray) -> tuple[float, float]:
    n = rows.exposure.to_numpy(float)
    y = rows.successes.to_numpy(float)
    p = np.clip(probability, 1e-12, 1.0 - 1e-12)
    log_loss = float(-np.sum(y * np.log(p) + (n - y) * np.log(1.0 - p)) / n.sum())
    brier = float(np.sum(y * (1.0 - probability) ** 2 + (n - y) * probability**2) / n.sum())
    return log_loss, brier


def fit_split(frame: pd.DataFrame) -> SplitFit:
    rows = validate_split_rows(frame)
    candidates = [0.0, *sorted({float(v) for v in rows.split_n if float(v) > 0}), inf]
    fits: list[SplitFit] = []
    for strength in candidates:
        base = add_split_probability(rows, prior_strength=strength, alpha=0.0)
        signal = base.split_logit_signal.to_numpy(float)
        offset = _logit(base.baseline_p.to_numpy(float))
        n = base.exposure.to_numpy(float)
        y = base.successes.to_numpy(float)
        alpha = 0.0
        if np.any(signal != 0):
            for iteration in range(200):
                p = _sigmoid(offset + alpha * signal)
                information = float(np.sum(n * p * (1.0 - p) * signal**2))
                if information <= 0 or not np.isfinite(information):
                    raise ValueError("split coefficient has no finite information")
                step = float(np.sum(signal * (y - n * p))) / information
                alpha += step
                if not np.isfinite(alpha):
                    raise ValueError("split coefficient diverged")
                if abs(step) <= 1e-12:
                    break
            else:
                raise ValueError("split coefficient did not converge")
        fitted = add_split_probability(rows, prior_strength=strength, alpha=alpha)
        log_loss, brier = _score(fitted, fitted.candidate_p.to_numpy(float))
        fits.append(SplitFit(strength, float(alpha), log_loss, brier))
    fits.sort(key=lambda value: (value.log_loss, value.brier, value.prior_strength))
    if len(fits) > 1 and (fits[0].log_loss, fits[0].brier) == (fits[1].log_loss, fits[1].brier):
        raise ValueError("split fits are exactly tied; refusing to guess")
    return fits[0]


def fit_rolling_split(
    frame: pd.DataFrame, *, fit_seasons: Iterable[int], evaluation_season: int
) -> tuple[SplitFit, pd.DataFrame]:
    rows = validate_split_rows(frame)
    seasons = rows.season.astype(int)
    fit_set = {int(v) for v in fit_seasons}
    evaluation_season = int(evaluation_season)
    if not fit_set or max(fit_set) >= evaluation_season:
        raise ValueError("split fold seasons are not ordered")
    invalid = {int(v) for v in seasons.unique() if int(v) <= evaluation_season} - fit_set - {evaluation_season}
    if invalid:
        raise ValueError(f"split fold has undeclared non-future seasons {sorted(invalid)}")
    fit_rows = rows[seasons.isin(fit_set)].copy()
    evaluation = rows[seasons.eq(evaluation_season)].copy()
    if fit_rows.empty or evaluation.empty or max(fit_rows.game_date) >= min(evaluation.game_date):
        raise ValueError("split fold chronology is invalid")
    return fit_split(fit_rows), evaluation


def score_difference(frame: pd.DataFrame) -> dict[str, float | int]:
    rows = validate_split_rows(frame)
    if "candidate_p" not in frame:
        raise ValueError("scored split rows lack candidate probability")
    candidate = pd.to_numeric(frame.loc[rows.index, "candidate_p"], errors="raise").to_numpy(float)
    baseline = rows.baseline_p.to_numpy(float)
    candidate_score = _score(rows, candidate)
    baseline_score = _score(rows, baseline)
    return {
        "player_games": int(len(rows)),
        "events": int(rows.exposure.sum()),
        "log_loss_difference": candidate_score[0] - baseline_score[0],
        "brier_difference": candidate_score[1] - baseline_score[1],
    }


def paired_date_interval(
    frame: pd.DataFrame,
    *,
    metric: str,
    declared_dates: Iterable[str],
    bootstrap: int,
    seed: int,
    family_size: int,
) -> SplitInterval:
    rows = validate_split_rows(frame)
    if metric not in {"brier", "log_loss"} or family_size <= 0:
        raise ValueError("invalid split interval contract")
    candidate = pd.to_numeric(frame.loc[rows.index, "candidate_p"], errors="raise").to_numpy(float)
    baseline = rows.baseline_p.to_numpy(float)
    n = rows.exposure.to_numpy(float)
    y = rows.successes.to_numpy(float)
    if metric == "brier":
        delta = y * ((1-candidate)**2 - (1-baseline)**2) + (n-y) * (candidate**2 - baseline**2)
    else:
        c = np.clip(candidate, 1e-12, 1-1e-12)
        b = np.clip(baseline, 1e-12, 1-1e-12)
        delta = -(y*np.log(c)+(n-y)*np.log(1-c)) + (y*np.log(b)+(n-y)*np.log(1-b))
    dates = np.asarray(sorted({str(v) for v in declared_dates}))
    if len(dates) < 2 or set(rows.game_date.unique()) - set(dates):
        raise ValueError("split interval date blocks are invalid")
    daily = pd.DataFrame({"game_date": rows.game_date, "delta": delta, "n": n}).groupby("game_date").sum().reindex(dates, fill_value=0)
    rng = np.random.default_rng(seed)
    weights = rng.multinomial(len(dates), np.full(len(dates), 1/len(dates)), size=bootstrap)
    denominator = weights @ daily.n.to_numpy(float)
    values = (weights @ daily.delta.to_numpy(float))[denominator > 0] / denominator[denominator > 0]
    tail = 0.025 / family_size
    return SplitInterval(
        float(np.percentile(values, 100*tail)),
        float(np.percentile(values, 100*(1-tail))),
        int(len(values)),
    )
