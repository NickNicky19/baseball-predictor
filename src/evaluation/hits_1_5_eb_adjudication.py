"""Fail-closed adjudication for the single Hits 1.5 rolling-EB candidate."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from src.evaluation.multi_market_foundation import sha256


SCHEMA = "hits-1-5-eb-open-gate-protocol-v1"
STATUS = "LOCKED_BEFORE_HITS_1_5_OUTCOME_OR_ECONOMIC_JOIN"
MODEL_KEY = ["mlb_game_pk", "player_id", "category", "line"]
PREDICTION_KEY = ["mlb_game_pk", "player_id", "game_date"]


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _unique(frame: pd.DataFrame, key: list[str], label: str) -> None:
    missing = [column for column in key if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} missing columns: {missing}")
    if frame[key].isna().any().any() or frame.duplicated(key).any():
        raise ValueError(f"{label} key is null or duplicated")


def _bool(series: pd.Series, label: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    values = series.astype(str).str.strip().str.lower().map({"true": True, "false": False})
    if values.isna().any():
        raise ValueError(f"{label} contains a non-boolean value")
    return values.astype(bool)


def _model_key_text(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["mlb_game_pk"].astype(int).astype(str) + "|"
        + frame["player_id"].astype(int).astype(str) + "|"
        + frame["category"].astype(str) + "|"
        + pd.to_numeric(frame["line"], errors="raise").map(lambda value: f"{value:g}")
    )


def _missing_key_text(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["mlb_game_pk"].astype(int).astype(str) + "|"
        + frame["player_id"].astype(int).astype(str) + "|"
        + frame["official_game_date"].astype(str) + "|"
        + frame["category"].astype(str) + "|"
        + pd.to_numeric(frame["line"], errors="raise").map(lambda value: f"{value:g}")
    )


def validate_protocol(payload: dict[str, Any], *, evidence_root: str | Path) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA or payload.get("status") != STATUS:
        raise ValueError("unrecognized Hits 1.5 protocol")
    if payload.get("betting_authorized") is not False or payload.get("production_unchanged") is not True:
        raise ValueError("Hits 1.5 protocol changed production or authorized betting")
    if payload.get("may_2026_opened") is not False:
        raise ValueError("Hits 1.5 protocol opened May")
    repo_root = Path(__file__).resolve().parents[2]
    evidence_root = Path(evidence_root)
    parent = payload.get("certified_parent") or {}
    parent_path = repo_root / str(parent.get("path", ""))
    if not parent_path.is_file() or sha256(parent_path) != parent.get("sha256"):
        raise ValueError("certified benchmark parent changed")
    if _json(parent_path).get("status") != parent.get("required_status"):
        raise ValueError("certified benchmark parent status changed")
    local_inputs = {"benchmark_protocol"}
    for name, record in (payload.get("inputs") or {}).items():
        base = repo_root if name in local_inputs else evidence_root
        path = base / str(record.get("path", ""))
        if not path.is_file() or sha256(path) != record.get("sha256"):
            raise ValueError(f"Hits 1.5 input missing or hash-mismatched: {name}")
    expected_candidate = {
        "id": "hits_1_5_rolling_eb_shared_pa_v1",
        "market": {"sportsbook": "draftkings", "category": "hits", "line": 1.5, "sides": ["over", "under"]},
        "probability_source_column": "eb_hits_1.5",
        "production_source_column": "production_hits_1.5",
        "league_control_column": "league_hits_1.5",
        "initial_history": [2023, 2024],
        "rolling_updates": "strictly_earlier_open_2026_official_PA_outcomes_only",
        "prior_strength_pa": 200,
        "pa_volume": "unchanged_hash_bound_2023_2024_lineup_slot_distribution",
        "policy_or_edge_threshold_fit": False,
        "production_install_permitted": False,
    }
    if payload.get("candidate") != expected_candidate:
        raise ValueError("Hits 1.5 candidate definition changed")
    chronology = payload.get("chronology") or {}
    diagnostic = list(chronology.get("diagnostic_dates") or [])
    confirmation = list(chronology.get("confirmation_dates") or [])
    if (
        len(diagnostic) != 37 or len(confirmation) != 19
        or diagnostic != sorted(set(diagnostic)) or confirmation != sorted(set(confirmation))
        or set(diagnostic) & set(confirmation) or max(diagnostic) >= min(confirmation)
        or chronology.get("forbidden") != ["2025", "2026-05"]
        or chronology.get("both_blocks_required") is not True
        or any(value.startswith("2026-05") for value in diagnostic + confirmation)
    ):
        raise ValueError("Hits 1.5 chronology changed or weakened")
    coverage = payload.get("coverage") or {}
    counts = {
        "diagnostic_raw_rows": 898, "confirmation_raw_rows": 495,
        "conflicting_duplicate_rows": 2, "strict_market_rows": 1391,
        "model_available_rows": 1383, "model_unavailable_rows": 8,
        "diagnostic_scored_rows": 896, "confirmation_scored_rows": 487,
        "official_positive_pa_rows": 1383, "entry_two_sided_rows": 1391,
        "close_two_sided_rows": 1391,
    }
    if any(coverage.get(key) != value for key, value in counts.items()):
        raise ValueError("Hits 1.5 coverage denominator changed")
    if coverage.get("conflicting_duplicate_keys_excluded_whole") != ["823964|666182|hits|1.5"]:
        raise ValueError("Hits 1.5 duplicate exclusion changed")
    expected_missing = {
        "823780|694192|2026-06-03|hits|1.5", "824349|686217|2026-06-07|hits|1.5",
        "824348|608348|2026-06-10|hits|1.5", "824996|650859|2026-06-10|hits|1.5",
        "824996|661388|2026-06-10|hits|1.5", "824997|666126|2026-06-12|hits|1.5",
        "824995|691016|2026-06-13|hits|1.5", "824994|703607|2026-06-14|hits|1.5",
    }
    if set(coverage.get("model_unavailable_keys") or []) != expected_missing or coverage.get("no_silent_drop") is not True:
        raise ValueError("Hits 1.5 model-unavailable contract changed")
    scoring = payload.get("scoring") or {}
    if scoring.get("edge_threshold") is not None or scoring.get("top_n") is not None:
        raise ValueError("Hits 1.5 selection policy was tuned")
    if scoring.get("vendor_numeric_result_forbidden") is not True or scoring.get("historical_executability_verified") is not False:
        raise ValueError("Hits 1.5 truth or executability contract changed")
    if payload.get("inference") != {
        "bootstrap_unit": "official_game_date", "bootstrap_draws": 20000,
        "bootstrap_seed": 17, "interval": "paired_two_sided_95_percentile",
    }:
        raise ValueError("Hits 1.5 inference contract changed")
    for name in ("probability_readiness", "historical_economic_readiness"):
        block = payload.get(name) or {}
        if block.get("all_conditions_required") is not True or any(
            value is not True for key, value in block.items() if key != "all_conditions_required"
        ):
            raise ValueError(f"Hits 1.5 readiness weakened: {name}")
    protected = payload.get("protected_invariants") or {}
    if not protected or not all(value is True for value in protected.values()):
        raise ValueError("Hits 1.5 protected invariant weakened")
    return payload


def load_protocol(path: str | Path, *, evidence_root: str | Path) -> dict[str, Any]:
    return validate_protocol(_json(Path(path)), evidence_root=evidence_root)


def prepare_market(
    diagnostic_source: pd.DataFrame,
    confirmation_source: pd.DataFrame,
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    required = [
        *MODEL_KEY, "official_game_date", "official_pa", "is_starter", "base_rule_eligible",
        "entry_over_odds_decimal", "entry_under_odds_decimal",
        "close_over_odds_decimal", "close_under_odds_decimal",
    ]
    arms: list[pd.DataFrame] = []
    excluded: list[pd.DataFrame] = []
    raw_counts: dict[str, int] = {}
    for role, source in (("diagnostic", diagnostic_source), ("confirmation", confirmation_source)):
        missing = [column for column in required if column not in source.columns]
        if missing:
            raise ValueError(f"{role} market missing columns: {missing}")
        if "result" in source.columns:
            raise ValueError("vendor numeric result entered Hits 1.5 adjudication")
        arm = source[
            source["category"].astype(str).eq("hits")
            & np.isclose(pd.to_numeric(source["line"], errors="coerce"), 1.5)
        ].copy()
        raw_counts[role] = len(arm)
        duplicated = arm.duplicated(MODEL_KEY, keep=False)
        if role == "diagnostic":
            duplicate_keys = set(_model_key_text(arm.loc[duplicated]))
            if duplicate_keys != set(protocol["coverage"]["conflicting_duplicate_keys_excluded_whole"]):
                raise ValueError("diagnostic conflicting duplicate keys changed")
            excluded.append(arm.loc[duplicated].copy())
            arm = arm.loc[~duplicated].copy()
        elif duplicated.any():
            raise ValueError("confirmation market contains duplicate MODEL_KEY")
        arm["chronology_role"] = role
        arms.append(arm)
    market = pd.concat(arms, ignore_index=True)
    excluded_rows = pd.concat(excluded, ignore_index=True)
    _unique(market, MODEL_KEY, "strict Hits 1.5 market")
    chronology = protocol["chronology"]
    for role, dates in (("diagnostic", chronology["diagnostic_dates"]), ("confirmation", chronology["confirmation_dates"])):
        observed = set(market.loc[market.chronology_role.eq(role), "official_game_date"].astype(str))
        if observed != set(dates):
            raise ValueError(f"{role} Hits 1.5 dates changed")
    market["is_starter"] = _bool(market["is_starter"], "is_starter")
    market["base_rule_eligible"] = _bool(market["base_rule_eligible"], "base_rule_eligible")
    market["official_pa"] = pd.to_numeric(market["official_pa"], errors="coerce")
    if not market["is_starter"].all() or not market["base_rule_eligible"].all() or not market["official_pa"].gt(0).all():
        raise ValueError("Hits 1.5 market eligibility changed")
    odds_columns = [
        "entry_over_odds_decimal", "entry_under_odds_decimal",
        "close_over_odds_decimal", "close_under_odds_decimal",
    ]
    for column in odds_columns:
        market[column] = pd.to_numeric(market[column], errors="coerce")
        if market[column].isna().any() or (~np.isfinite(market[column])).any() or not market[column].gt(1).all():
            raise ValueError(f"Hits 1.5 {column} is missing or invalid")
    funnel = {
        "diagnostic_raw_rows": raw_counts["diagnostic"],
        "confirmation_raw_rows": raw_counts["confirmation"],
        "conflicting_duplicate_keys": int(excluded_rows[MODEL_KEY].drop_duplicates().shape[0]),
        "conflicting_duplicate_rows": int(len(excluded_rows)),
        "strict_market_rows": int(len(market)),
        "entry_two_sided_rows": int(market[odds_columns[:2]].notna().all(axis=1).sum()),
        "close_two_sided_rows": int(market[odds_columns[2:]].notna().all(axis=1).sum()),
    }
    return market.sort_values(MODEL_KEY).reset_index(drop=True), excluded_rows, funnel


def build_scored(
    market: pd.DataFrame,
    benchmark: pd.DataFrame,
    official: pd.DataFrame,
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    columns = [
        *PREDICTION_KEY, "eb_hits_1.5", "production_hits_1.5", "league_hits_1.5",
        "pa", "hits", "actual_hits_1.5",
    ]
    missing = [column for column in columns if column not in benchmark.columns]
    if missing:
        raise ValueError(f"benchmark predictions missing columns: {missing}")
    prediction = benchmark[columns].copy()
    _unique(prediction, PREDICTION_KEY, "Hits 1.5 benchmark predictions")
    market_join = market.copy()
    market_join["game_date"] = market_join["official_game_date"].astype(str)
    joined = market_join.merge(prediction, on=PREDICTION_KEY, how="left", validate="one_to_one", indicator=True)
    unavailable = joined.loc[joined["_merge"].eq("left_only")].copy()
    observed_unavailable = set(_missing_key_text(unavailable))
    if observed_unavailable != set(protocol["coverage"]["model_unavailable_keys"]):
        raise ValueError("Hits 1.5 model-unavailable keys changed")
    scored = joined.loc[joined["_merge"].eq("both")].drop(columns="_merge").copy()
    probability_columns = ["eb_hits_1.5", "production_hits_1.5", "league_hits_1.5"]
    for column in probability_columns:
        scored[column] = pd.to_numeric(scored[column], errors="coerce")
        if scored[column].isna().any() or (~np.isfinite(scored[column])).any() or not scored[column].between(0, 1).all():
            raise ValueError(f"Hits 1.5 {column} is missing or invalid")
    truth_columns = ["mlb_game_pk", "player_id", "game_date", "pa", "hits"]
    missing = [column for column in truth_columns if column not in official.columns]
    if missing:
        raise ValueError(f"official Hits truth missing columns: {missing}")
    truth = official[truth_columns].copy()
    _unique(truth, PREDICTION_KEY, "official Hits truth")
    truth = truth.rename(columns={"pa": "official_truth_pa", "hits": "official_truth_hits"})
    scored = scored.merge(truth, on=PREDICTION_KEY, how="left", validate="one_to_one")
    for column in ("pa", "hits", "actual_hits_1.5", "official_truth_pa", "official_truth_hits"):
        scored[column] = pd.to_numeric(scored[column], errors="coerce")
    if scored[["pa", "hits", "actual_hits_1.5", "official_truth_pa", "official_truth_hits"]].isna().any().any():
        raise ValueError("official Hits truth is incomplete")
    if not (
        np.isclose(scored["pa"], scored["official_pa"]).all()
        and np.isclose(scored["pa"], scored["official_truth_pa"]).all()
        and np.isclose(scored["hits"], scored["official_truth_hits"]).all()
        and scored["pa"].gt(0).all()
        and scored["actual_hits_1.5"].eq(scored["hits"].ge(2).astype(int)).all()
    ):
        raise ValueError("official Hits truth cross-check failed")
    scored["won_over"] = scored["hits"].ge(2)
    scored = scored.rename(columns={
        "eb_hits_1.5": "candidate_p_over",
        "production_hits_1.5": "production_p_over",
        "league_hits_1.5": "league_p_over",
    })
    _unique(scored, MODEL_KEY, "scored Hits 1.5 universe")
    funnel = {
        "strict_market_rows": int(len(market)),
        "model_available_rows": int(len(scored)),
        "model_unavailable_rows": int(len(unavailable)),
        "diagnostic_scored_rows": int(scored["chronology_role"].eq("diagnostic").sum()),
        "confirmation_scored_rows": int(scored["chronology_role"].eq("confirmation").sum()),
        "official_positive_pa_rows": int(scored["pa"].gt(0).sum()),
    }
    return scored.sort_values(MODEL_KEY).reset_index(drop=True), unavailable, funnel


def arm_rows(scored: pd.DataFrame, arm: str) -> pd.DataFrame:
    if arm not in {"candidate", "production", "league"}:
        raise ValueError("unknown Hits 1.5 arm")
    out = scored.copy()
    probability = out[f"{arm}_p_over"].to_numpy(float)
    won_over = out["won_over"].to_numpy(bool)
    eps = np.finfo(float).eps
    safe = np.clip(probability, eps, 1.0 - eps)
    out["arm"] = arm
    out["model_probability"] = probability
    out["brier"] = np.square(probability - won_over.astype(float))
    out["log_loss"] = -(won_over * np.log(safe) + (~won_over) * np.log1p(-safe))
    over_ev = probability * out["entry_over_odds_decimal"].to_numpy(float) - 1.0
    under_ev = (1.0 - probability) * out["entry_under_odds_decimal"].to_numpy(float) - 1.0
    choose_over = (over_ev > under_ev) & (over_ev > 0.0)
    choose_under = (under_ev > over_ev) & (under_ev > 0.0)
    out["selected_side"] = np.where(choose_over, "over", np.where(choose_under, "under", "none"))
    out["positive_ev"] = choose_over | choose_under
    out["expected_profit"] = np.where(choose_over, over_ev, np.where(choose_under, under_ev, np.nan))
    entry = np.where(choose_over, out["entry_over_odds_decimal"], np.where(choose_under, out["entry_under_odds_decimal"], np.nan))
    close = np.where(choose_over, out["close_over_odds_decimal"], np.where(choose_under, out["close_under_odds_decimal"], np.nan))
    selected_won = np.where(choose_over, won_over, np.where(choose_under, ~won_over, False))
    out["raw_probability_movement"] = 1.0 / entry - 1.0 / close
    out["theoretical_realised_profit"] = np.where(out["positive_ev"], np.where(selected_won, entry - 1.0, -1.0), np.nan)
    return out


def point_metrics(rows: pd.DataFrame) -> dict[str, Any]:
    selected = rows.loc[rows["positive_ev"]]
    return {
        "all_rows": int(len(rows)),
        "dates": int(rows["official_game_date"].nunique()),
        "mean_model_probability": float(rows["model_probability"].mean()),
        "observed_over_rate": float(rows["won_over"].mean()),
        "calibration_bias": float(rows["model_probability"].mean() - rows["won_over"].mean()),
        "brier": float(rows["brier"].mean()),
        "log_loss": float(rows["log_loss"].mean()),
        "auc": float(roc_auc_score(rows["won_over"].astype(int), rows["model_probability"])),
        "positive_ev_rows": int(len(selected)),
        "positive_ev_dates": int(selected["official_game_date"].nunique()),
        "positive_ev_over_rows": int(selected["selected_side"].eq("over").sum()),
        "positive_ev_under_rows": int(selected["selected_side"].eq("under").sum()),
        "positive_ev_mean_expected_profit": float(selected["expected_profit"].mean()) if len(selected) else None,
        "positive_ev_mean_raw_probability_movement": float(selected["raw_probability_movement"].mean()) if len(selected) else None,
        "positive_ev_positive_movement_rate": float(selected["raw_probability_movement"].gt(0).mean()) if len(selected) else None,
        "positive_ev_theoretical_flat_stake_roi": float(selected["theoretical_realised_profit"].mean()) if len(selected) else None,
    }


def _interval(values: np.ndarray) -> list[float]:
    return [float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))]


def date_block_intervals(
    arms: dict[str, pd.DataFrame], declared_dates: list[str], *, draws: int, seed: int,
) -> dict[str, Any]:
    if set(arms) != {"candidate", "production", "league"} or draws <= 0:
        raise ValueError("invalid Hits 1.5 bootstrap inputs")
    dates = list(declared_dates)
    candidate = arms["candidate"]
    for label, frame in arms.items():
        if set(frame["official_game_date"].astype(str)) != set(dates):
            raise ValueError(f"{label} bootstrap dates changed")
        if not frame[MODEL_KEY].reset_index(drop=True).equals(candidate[MODEL_KEY].reset_index(drop=True)):
            raise ValueError(f"{label} bootstrap keys changed")
    totals = pd.DataFrame({"official_game_date": candidate["official_game_date"]})
    totals["count"] = 1
    for comparison in ("production", "league"):
        totals[f"brier_delta_{comparison}"] = candidate["brier"].to_numpy(float) - arms[comparison]["brier"].to_numpy(float)
        totals[f"log_delta_{comparison}"] = candidate["log_loss"].to_numpy(float) - arms[comparison]["log_loss"].to_numpy(float)
    grouped = totals.groupby("official_game_date").sum().reindex(dates, fill_value=0.0)
    selected = candidate.loc[candidate["positive_ev"]].groupby("official_game_date").agg(
        selected_count=("positive_ev", "size"),
        movement=("raw_probability_movement", "sum"),
        profit=("theoretical_realised_profit", "sum"),
    ).reindex(dates, fill_value=0.0)
    grouped = grouped.join(selected)
    rng = np.random.default_rng(int(seed))
    weights = rng.multinomial(len(dates), np.full(len(dates), 1.0 / len(dates)), size=int(draws))
    all_count = weights @ grouped["count"].to_numpy(float)
    selected_count = weights @ grouped["selected_count"].to_numpy(float)
    valid = (all_count > 0) & (selected_count > 0)
    result: dict[str, Any] = {"draws_requested": int(draws), "valid_draws": int(valid.sum())}
    for comparison in ("production", "league"):
        result[f"candidate_minus_{comparison}_brier_95"] = _interval(
            (weights[valid] @ grouped[f"brier_delta_{comparison}"].to_numpy(float)) / all_count[valid]
        )
        result[f"candidate_minus_{comparison}_log_loss_95"] = _interval(
            (weights[valid] @ grouped[f"log_delta_{comparison}"].to_numpy(float)) / all_count[valid]
        )
    result["candidate_positive_ev_raw_movement_95"] = _interval(
        (weights[valid] @ grouped["movement"].to_numpy(float)) / selected_count[valid]
    )
    result["candidate_positive_ev_theoretical_roi_95"] = _interval(
        (weights[valid] @ grouped["profit"].to_numpy(float)) / selected_count[valid]
    )
    return result


def decide(
    roles: dict[str, Any], *, exact_coverage: bool, all_draws_valid: bool,
) -> dict[str, Any]:
    if set(roles) != {"diagnostic", "confirmation"}:
        raise ValueError("Hits 1.5 decision requires both chronological blocks")
    probability_parts: dict[str, bool] = {}
    for role_name, role in roles.items():
        candidate = role["candidate"]
        production = role["production"]
        league = role["league"]
        probability_parts[f"{role_name}_brier_below_production"] = candidate["brier"] < production["brier"]
        probability_parts[f"{role_name}_log_loss_below_production"] = candidate["log_loss"] < production["log_loss"]
        probability_parts[f"{role_name}_brier_below_league"] = candidate["brier"] < league["brier"]
        probability_parts[f"{role_name}_log_loss_below_league"] = candidate["log_loss"] < league["log_loss"]
        probability_parts[f"{role_name}_auc_noninferior"] = candidate["auc"] >= max(production["auc"], league["auc"])
    interval = roles["confirmation"]["date_block_intervals"]
    for comparison in ("production", "league"):
        probability_parts[f"confirmation_{comparison}_brier_upper_below_zero"] = interval[f"candidate_minus_{comparison}_brier_95"][1] < 0.0
        probability_parts[f"confirmation_{comparison}_log_loss_upper_below_zero"] = interval[f"candidate_minus_{comparison}_log_loss_95"][1] < 0.0
    probability_parts["exact_coverage"] = exact_coverage
    probability_parts["all_bootstrap_draws_valid"] = all_draws_valid
    economic_parts: dict[str, bool] = {}
    for role_name, role in roles.items():
        interval = role["date_block_intervals"]
        economic_parts[f"{role_name}_positive_ev_rows"] = role["candidate"]["positive_ev_rows"] > 0
        economic_parts[f"{role_name}_movement_lower_above_zero"] = interval["candidate_positive_ev_raw_movement_95"][0] > 0.0
        economic_parts[f"{role_name}_roi_lower_above_zero"] = interval["candidate_positive_ev_theoretical_roi_95"][0] > 0.0
    return {
        "probability_parts": {key: bool(value) for key, value in probability_parts.items()},
        "historical_economic_parts": {key: bool(value) for key, value in economic_parts.items()},
        "probability_ready": bool(all(probability_parts.values())),
        "historical_economic_ready": bool(all(economic_parts.values())),
        "production_install_permitted": False,
        "betting_authorized": False,
    }
