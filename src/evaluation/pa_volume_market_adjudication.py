"""Locked 2024 adjudication of the repaired 2023-only PA-volume layer."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from src.evaluation.multi_market_foundation import PA_OUTCOMES
from src.evaluation.shared_pa_training_data import outcome_counts
from src.features.pa_volume_gate import PAVolumeDistributionArtifact
from src.learning.shared_pa_model import derived_market_probabilities


EVALUATION_COLUMNS = (
    "season", "game_date", "game_pk", "player_id", "lineup_slot",
    "out_pa", "out_ab", "out_hits", "out_doubles", "out_triples",
    "out_hr", "out_bb", "out_k",
)
MARKETS = {
    "hits_over_0_5": "hits_0.5",
    "home_runs_over_0_5": "home_runs_0.5",
    "total_bases_over_0_5": "total_bases_0.5",
    "total_bases_over_1_5": "total_bases_1.5",
}
EPSILON = 1e-12


def read_2023_2024_without_2025_outcomes(path: str | Path) -> pd.DataFrame:
    """Project locked columns through 2024 and stop before 2025 outcomes."""
    rows: list[dict[str, str]] = []
    with gzip.open(Path(path), "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("market adjudication source is empty") from exc
        if len(header) != len(set(header)):
            raise ValueError("market adjudication source has duplicate columns")
        missing = sorted(set(EVALUATION_COLUMNS) - set(header))
        if missing:
            raise ValueError(f"market adjudication source is missing {missing}")
        index = {name: header.index(name) for name in EVALUATION_COLUMNS}
        last_season: int | None = None
        for raw in reader:
            if len(raw) != len(header):
                raise ValueError("market adjudication row width differs from header")
            try:
                season = int(raw[index["season"]])
            except ValueError as exc:
                raise ValueError("market adjudication season is malformed") from exc
            if last_season is not None and season < last_season:
                raise ValueError("market adjudication source is not season-ordered")
            last_season = season
            if season > 2024:
                break
            if season < 2023:
                continue
            rows.append({name: raw[index[name]] for name in EVALUATION_COLUMNS})
    frame = pd.DataFrame(rows, columns=EVALUATION_COLUMNS)
    if frame.empty:
        raise ValueError("market adjudication has no 2023/2024 rows")
    return frame


def _validate(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = frame.copy()
    dates = pd.to_datetime(work["game_date"], errors="coerce")
    numeric_columns = [name for name in EVALUATION_COLUMNS if name != "game_date"]
    numeric = work[numeric_columns].apply(pd.to_numeric, errors="coerce")
    if dates.isna().any() or numeric.isna().any().any():
        raise ValueError("market adjudication contains null or malformed values")
    if not (numeric.to_numpy() == np.floor(numeric.to_numpy())).all():
        raise ValueError("market adjudication identities/outcomes are not integers")
    for column in numeric_columns:
        work[column] = numeric[column].astype(int)
    work["game_date"] = dates.dt.strftime("%Y-%m-%d")
    if set(work["season"].unique()) != {2023, 2024}:
        raise ValueError("market adjudication chronology changed")
    if not dates.dt.year.eq(work["season"]).all():
        raise ValueError("market adjudication season and date disagree")
    if work.duplicated(["game_pk", "player_id"]).any():
        raise ValueError("market adjudication contains duplicate game/player identity")
    if not work["lineup_slot"].between(1, 9).all():
        raise ValueError("market adjudication contains an invalid lineup slot")
    game_sizes = work.groupby("game_pk", sort=False).size()
    if not game_sizes.eq(18).all():
        raise ValueError("market adjudication is not the full original-starter population")
    outcome_columns = [
        "out_pa", "out_ab", "out_hits", "out_doubles", "out_triples",
        "out_hr", "out_bb", "out_k",
    ]
    if (work[outcome_columns] < 0).any().any():
        raise ValueError("market adjudication outcomes are negative")
    counts = outcome_counts(work)
    if (counts < 0).any().any() or not counts.sum(axis=1).eq(work["out_pa"]).all():
        raise ValueError("market adjudication PA outcome accounting is invalid")
    return work, counts


def _projection_sha256(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values(["game_date", "game_pk", "player_id"], kind="stable")
    payload = ordered.loc[:, EVALUATION_COLUMNS].to_dict(orient="records")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _rolling_eb_probabilities(
    frame: pd.DataFrame, counts: pd.DataFrame, *, prior_strength: float
) -> tuple[np.ndarray, np.ndarray]:
    train_mask = frame["season"].eq(2023).to_numpy()
    selection = frame.loc[frame["season"].eq(2024)].copy()
    training_counts = counts.loc[train_mask, PA_OUTCOMES].to_numpy(float)
    league_counts = training_counts.sum(axis=0)
    if league_counts.sum() <= 0:
        raise ValueError("2023 league PA exposure is empty")
    league = league_counts / league_counts.sum()
    player_history: dict[int, np.ndarray] = {}
    training_players = frame.loc[train_mask, "player_id"].to_numpy(int)
    for player_id, values in zip(training_players, training_counts):
        player_history[player_id] = player_history.get(
            player_id, np.zeros(len(PA_OUTCOMES), dtype=float)
        ) + values

    output = np.empty((len(selection), len(PA_OUTCOMES)), dtype=float)
    selection_counts = counts.loc[frame["season"].eq(2024), PA_OUTCOMES].reset_index(drop=True)
    selection = selection.reset_index(drop=True)
    for _, indices in selection.groupby("game_date", sort=True).groups.items():
        positions = np.asarray(list(indices), dtype=int)
        for position in positions:
            player_id = int(selection.at[position, "player_id"])
            history = player_history.get(player_id, np.zeros(len(PA_OUTCOMES), dtype=float))
            posterior = history + float(prior_strength) * league
            output[position] = posterior / posterior.sum()
        # Same-day outcomes enter only after every prediction for that date.
        for position in positions:
            player_id = int(selection.at[position, "player_id"])
            player_history[player_id] = player_history.get(
                player_id, np.zeros(len(PA_OUTCOMES), dtype=float)
            ) + selection_counts.iloc[position].to_numpy(float)
    league_output = np.tile(league, (len(selection), 1))
    return output, league_output


def _market_outcomes(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    hits = frame["out_hits"].to_numpy(int)
    home_runs = frame["out_hr"].to_numpy(int)
    total_bases = (
        frame["out_hits"]
        + frame["out_doubles"]
        + 2 * frame["out_triples"]
        + 3 * frame["out_hr"]
    ).to_numpy(int)
    return {
        "hits_over_0_5": (hits >= 1).astype(int),
        "home_runs_over_0_5": (home_runs >= 1).astype(int),
        "total_bases_over_0_5": (total_bases >= 1).astype(int),
        "total_bases_over_1_5": (total_bases >= 2).astype(int),
    }


def _ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    order = np.argsort(p, kind="stable")
    total = len(y)
    value = 0.0
    for positions in np.array_split(order, bins):
        if len(positions):
            value += len(positions) / total * abs(float(y[positions].mean() - p[positions].mean()))
    return float(value)


def _metrics(y: np.ndarray, p: np.ndarray, tail_mask: np.ndarray) -> dict[str, Any]:
    clipped = np.clip(p, EPSILON, 1.0 - EPSILON)
    loss = -(y * np.log(clipped) + (1 - y) * np.log1p(-clipped))
    return {
        "rows": int(len(y)),
        "observed_rate": float(y.mean()),
        "mean_probability": float(p.mean()),
        "brier": float(np.mean(np.square(p - y))),
        "log_loss": float(loss.mean()),
        "auc": float(roc_auc_score(y, p)),
        "calibration_in_the_large_abs": float(abs(y.mean() - p.mean())),
        "ece_10_equal_count": _ece(y, p),
        "high_probability_tail_rows": int(tail_mask.sum()),
        "high_probability_tail_mean_probability": float(p[tail_mask].mean()),
        "high_probability_tail_observed_rate": float(y[tail_mask].mean()),
        "high_probability_tail_abs_error": float(
            abs(y[tail_mask].mean() - p[tail_mask].mean())
        ),
    }


def _paired_interval(
    y: np.ndarray,
    candidate: np.ndarray,
    baseline: np.ndarray,
    dates: np.ndarray,
    *,
    metric: str,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    if metric == "brier":
        delta = np.square(candidate - y) - np.square(baseline - y)
    elif metric == "log_loss":
        cp = np.clip(candidate, EPSILON, 1.0 - EPSILON)
        bp = np.clip(baseline, EPSILON, 1.0 - EPSILON)
        delta = -(y * np.log(cp) + (1 - y) * np.log1p(-cp)) + (
            y * np.log(bp) + (1 - y) * np.log1p(-bp)
        )
    else:
        raise ValueError("paired interval metric is unknown")
    block = pd.DataFrame({"date": dates, "delta": delta}).groupby("date", sort=True)["delta"].agg(["sum", "count"])
    rng = np.random.default_rng(seed)
    sums = block["sum"].to_numpy(float)
    counts = block["count"].to_numpy(float)
    samples = np.empty(draws, dtype=float)
    for index in range(draws):
        selected = rng.integers(0, len(block), size=len(block))
        samples[index] = sums[selected].sum() / counts[selected].sum()
    lower, upper = np.quantile(samples, [0.025, 0.975])
    return {
        "point": float(delta.mean()),
        "lower": float(lower),
        "upper": float(upper),
        "date_blocks": int(len(block)),
        "draws": int(draws),
        "seed": int(seed),
    }


def adjudicate(
    *,
    frame: pd.DataFrame,
    artifact: PAVolumeDistributionArtifact,
    protocol: Mapping[str, Any],
    source_path: str,
    source_sha256: str,
    artifact_sha256: str,
    protocol_sha256: str,
) -> dict[str, Any]:
    if protocol.get("status") != "LOCKED_BEFORE_2024_REPAIR_EVALUATION":
        raise ValueError("PA-volume protocol was not locked before selection")
    if protocol.get("candidate_id") != artifact.candidate_id:
        raise ValueError("PA-volume protocol and artifact candidate differ")
    if protocol.get("chronology") != {
        "fit_season": 2023,
        "selection_season": 2024,
        "selection_runs": 1,
        "confirmation_seasons_opened": [],
        "spent_hr_confirmation_year": 2025,
        "may_2026_forbidden": True,
    }:
        raise ValueError("PA-volume chronological boundary changed")
    configured = protocol.get("base_running") or {}
    if configured.get("pa_volume_identity_mode") != "receipt_required_or_pooled" or configured.get("pa_distribution_sha256") != artifact_sha256:
        raise ValueError("PA-volume runtime identity is not bound to the artifact")

    work, counts = _validate(frame)
    selection_mask = work["season"].eq(2024)
    selection = work.loc[selection_mask].reset_index(drop=True)
    eb, league = _rolling_eb_probabilities(work, counts, prior_strength=200.0)
    pooled = {"1": {str(key): value for key, value in artifact.pooled.items()}}
    legacy = {"1": {"4": 0.95, "5": 0.05}}
    dummy_slots = pd.Series(np.ones(len(selection), dtype=int))
    candidate = derived_market_probabilities(eb, dummy_slots, pooled)
    player_legacy = derived_market_probabilities(eb, dummy_slots, legacy)
    league_pooled = derived_market_probabilities(league, dummy_slots, pooled)
    actual = _market_outcomes(selection)
    dates = selection["game_date"].to_numpy()
    uncertainty = protocol["selection_gates"]["uncertainty"]
    draws = int(uncertainty["draws"])
    base_seed = int(uncertainty["seed"])

    market_reports: dict[str, Any] = {}
    all_available_gates = True
    for market_index, (market, derived_key) in enumerate(MARKETS.items()):
        y = actual[market]
        arms = {
            "candidate_2023_pooled_pa_plus_time_safe_player_eb": candidate[derived_key],
            "league_rate": league_pooled[derived_key],
            "player_historical_rate_with_legacy_pa_volume": player_legacy[derived_key],
        }
        threshold = float(np.quantile(arms["candidate_2023_pooled_pa_plus_time_safe_player_eb"], 0.9))
        tail_mask = arms["candidate_2023_pooled_pa_plus_time_safe_player_eb"] >= threshold
        metrics = {name: _metrics(y, values, tail_mask) for name, values in arms.items()}
        comparisons: dict[str, Any] = {}
        candidate_name = "candidate_2023_pooled_pa_plus_time_safe_player_eb"
        for comparator_index, comparator in enumerate(("league_rate", "player_historical_rate_with_legacy_pa_volume")):
            intervals = {}
            material = {}
            for metric_index, metric in enumerate(("brier", "log_loss")):
                interval = _paired_interval(
                    y,
                    arms[candidate_name],
                    arms[comparator],
                    dates,
                    metric=metric,
                    draws=draws,
                    seed=base_seed + market_index * 100 + comparator_index * 10 + metric_index,
                )
                threshold_delta = -0.01 * float(metrics[comparator][metric])
                intervals[metric] = interval
                material[metric] = bool(
                    interval["point"] < threshold_delta
                    and interval["upper"] < threshold_delta
                )
            gates = {
                "brier_material": material["brier"],
                "log_loss_material": material["log_loss"],
                "auc_noninferior": metrics[candidate_name]["auc"] >= metrics[comparator]["auc"],
                "ece_noninferior": metrics[candidate_name]["ece_10_equal_count"] <= metrics[comparator]["ece_10_equal_count"],
                "calibration_in_the_large_noninferior": metrics[candidate_name]["calibration_in_the_large_abs"] <= metrics[comparator]["calibration_in_the_large_abs"],
                "high_probability_tail_noninferior": metrics[candidate_name]["high_probability_tail_abs_error"] <= metrics[comparator]["high_probability_tail_abs_error"],
            }
            comparisons[comparator] = {
                "paired_candidate_minus_comparator": intervals,
                "gates": gates,
                "passes_all": bool(all(gates.values())),
            }
        available_pass = bool(all(item["passes_all"] for item in comparisons.values()))
        all_available_gates = all_available_gates and available_pass
        market_reports[market] = {
            "rows": int(len(selection)),
            "coverage_loss": 0,
            "candidate_tail_threshold": threshold,
            "candidate_tail_rows": int(tail_mask.sum()),
            "metrics": metrics,
            "comparisons": comparisons,
            "available_comparator_gates_pass": available_pass,
            "frozen_production_simulator_comparator": "MISSING_IDENTITY_ALIGNED_2024_PROBABILITIES",
            "valid_market_implied_comparator": "UNAVAILABLE_NO_TIMESTAMP_CERTIFIED_EXECUTABLE_2024_PRICES",
            "promotion_gate": "FAIL_MISSING_REQUIRED_COMPARATORS_AND_FRESH_CONFIRMATION",
        }

    return {
        "schema_version": "pa-volume-chronology-adjudication-v1",
        "candidate_id": artifact.candidate_id,
        "status": "CERTIFIED_SELECTION_REJECTION" if not all_available_gates else "AVAILABLE_GATES_PASS_BUT_PROMOTION_BLOCKED",
        "classification": "integrity_repair_and_single_locked_2024_selection",
        "chronology": {
            "fit_year": 2023,
            "selection_year": 2024,
            "selection_runs": 1,
            "2025_confirmation_opened": False,
            "may_2026_read": False,
        },
        "source": {
            "path": source_path,
            "sha256": source_sha256,
            "projected_2023_2024_sha256": _projection_sha256(work),
            "rows_2023": int(work["season"].eq(2023).sum()),
            "rows_2024": int(selection_mask.sum()),
            "games_2023": int(work.loc[work["season"].eq(2023), "game_pk"].nunique()),
            "games_2024": int(selection["game_pk"].nunique()),
            "zero_pa_rows_2023": int(work.loc[work["season"].eq(2023), "out_pa"].eq(0).sum()),
            "zero_pa_rows_2024": int(selection["out_pa"].eq(0).sum()),
        },
        "artifact_sha256": artifact_sha256,
        "protocol_sha256": protocol_sha256,
        "historical_lineup_slot_consumed_as_prediction_input": False,
        "pa_volume_consumed": "2023 pooled empirical distribution for every 2024 row",
        "market_reports": market_reports,
        "all_available_comparator_gates_pass": all_available_gates,
        "required_promotion_evidence": {
            "identity_aligned_frozen_simulator": False,
            "valid_market_implied_where_available": False,
            "fresh_untouched_confirmation_or_prospective_replication": False,
            "receipt_proven_lineup_slot_evidence": False,
        },
        "promotion_decision": "RETAIN_FROZEN_BASELINE_NO_BETTING_AUTHORIZATION",
        "single_highest_value_next_action": "Deploy the future-only AWS T-minus-4 receipt collector and extend its immutable target evidence to the already-preregistered lineup/PA-volume boundary; do not backfill missing receipts or reopen 2024/2025/May.",
    }
