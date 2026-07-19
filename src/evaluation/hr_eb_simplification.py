"""Locked contracts for the single HR-over rolling-EB simplification candidate."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from src.evaluation.multi_market_foundation import sha256


SCHEMA = "hr-eb-simplification-open-gate-protocol-v1"
STATUS = "LOCKED_BEFORE_HR_EB_MARKET_JOIN_OR_ECONOMIC_RESULT"
MODEL_KEY = ["mlb_game_pk", "player_id", "category", "line"]
PREDICTION_KEY = ["mlb_game_pk", "player_id", "game_date"]


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def validate_protocol(payload: dict[str, Any], *, evidence_root: str | Path) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA or payload.get("status") != STATUS:
        raise ValueError("unrecognized HR EB simplification protocol")
    if payload.get("betting_authorized") is not False or payload.get("production_unchanged") is not True:
        raise ValueError("HR EB protocol changed production or authorized betting")
    if payload.get("may_2026_opened") is not False or payload.get("confirmation_2025_opened") is not False:
        raise ValueError("HR EB protocol opened sealed evidence")
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
            raise ValueError(f"HR EB input missing or hash-mismatched: {name}")
    candidate = payload.get("candidate") or {}
    if candidate != {
        "id": "hr_over_0_5_rolling_eb_shared_pa_v1",
        "market": {"sportsbook": "draftkings", "category": "home_runs", "line": 0.5, "side": "over"},
        "probability_source_column": "eb_home_runs_0.5",
        "initial_history": [2023, 2024],
        "rolling_updates": "strictly_earlier_open_2026_official_PA_outcomes_only",
        "prior_strength_pa": 200,
        "pa_volume": "unchanged_hash_bound_2023_2024_lineup_slot_distribution",
        "policy_or_edge_threshold_fit": False,
        "production_install_permitted": False,
    }:
        raise ValueError("HR EB candidate definition changed")
    chronology = payload.get("chronology") or {}
    if chronology.get("forbidden") != [2025, "2026-05"] or chronology.get("both_blocks_required") is not True:
        raise ValueError("HR EB chronology weakened")
    coverage = payload.get("coverage") or {}
    expected_counts = {
        "strict_market_rows": 9356, "model_market_rows": 8854,
        "official_gradeable_rows": 8827, "official_void_or_unresolved_rows": 27,
        "benchmark_candidate_rows_covering_model_market": 8848,
    }
    if any(coverage.get(key) != value for key, value in expected_counts.items()):
        raise ValueError("HR EB coverage denominator changed")
    if not all(coverage.get(key) is True for key in (
        "six_missing_candidate_rows_must_be_exact_VOID_NO_PA",
        "gradeable_candidate_coverage_must_be_exact", "no_silent_drop",
    )):
        raise ValueError("HR EB coverage contract weakened")
    scoring = payload.get("scoring") or {}
    if scoring.get("edge_threshold") is not None or scoring.get("top_n") is not None:
        raise ValueError("HR EB policy was tuned")
    if scoring.get("vendor_numeric_result_forbidden") is not True:
        raise ValueError("vendor result reached HR EB truth")
    inference = payload.get("inference") or {}
    if inference != {
        "bootstrap_unit": "official_game_date", "bootstrap_draws": 20000,
        "bootstrap_seed": 17, "interval": "paired_two_sided_95_percentile",
    }:
        raise ValueError("HR EB inference contract changed")
    for name in ("probability_confirmation_readiness", "historical_economic_readiness"):
        block = payload.get(name) or {}
        if block.get("all_conditions_required") is not True or any(
            value is not True for key, value in block.items()
            if key not in {"all_conditions_required"}
        ):
            raise ValueError(f"HR EB readiness weakened: {name}")
    decision = payload.get("decision") or {}
    if decision.get("one_market_only") is not True:
        raise ValueError("HR EB market separation weakened")
    protected = payload.get("protected_invariants") or {}
    if not protected or not all(value is True for value in protected.values()):
        raise ValueError("HR EB protected invariant weakened")
    return payload


def load_protocol(path: str | Path, *, evidence_root: str | Path) -> dict[str, Any]:
    return validate_protocol(_json(Path(path)), evidence_root=evidence_root)


def _boolean(series: pd.Series, label: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    mapped = series.astype(str).str.strip().str.lower().map({"true": True, "false": False})
    if mapped.isna().any():
        raise ValueError(f"{label} contains a non-boolean value")
    return mapped.astype(bool)


def build_candidate_probabilities(
    frozen: pd.DataFrame,
    benchmark: pd.DataFrame,
    settlement: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    arm = frozen[
        frozen["category"].astype(str).eq("home_runs")
        & np.isclose(pd.to_numeric(frozen["line"], errors="coerce"), 0.5)
    ].copy()
    if arm[MODEL_KEY].isna().any().any() or arm.duplicated(MODEL_KEY).any() or len(arm) != 12780:
        raise ValueError("frozen HR candidate universe changed")
    arm["game_date"] = pd.to_datetime(arm["game_date"], errors="raise").dt.strftime("%Y-%m-%d")
    candidate_source = benchmark[PREDICTION_KEY + ["eb_home_runs_0.5"]].copy()
    if candidate_source[PREDICTION_KEY].isna().any().any() or candidate_source.duplicated(PREDICTION_KEY).any():
        raise ValueError("benchmark candidate identity changed")
    merged = arm.merge(candidate_source, on=PREDICTION_KEY, how="left", validate="one_to_one")
    bridge = settlement[
        settlement["category"].astype(str).eq("home_runs")
        & np.isclose(pd.to_numeric(settlement["line"], errors="coerce"), 0.5)
    ].copy()
    bridge = bridge[MODEL_KEY + ["official_grade_status", "official_gradeable"]]
    if bridge[MODEL_KEY].isna().any().any() or bridge.duplicated(MODEL_KEY).any() or len(bridge) != 8854:
        raise ValueError("official HR settlement universe changed")
    bridge["official_gradeable"] = _boolean(bridge["official_gradeable"], "official_gradeable")
    model_market = arm.merge(bridge, on=MODEL_KEY, how="inner", validate="one_to_one")
    model_market = model_market.merge(candidate_source, on=PREDICTION_KEY, how="left", validate="one_to_one")
    missing = model_market[model_market["eb_home_runs_0.5"].isna()].copy()
    gradeable = model_market[model_market["official_gradeable"]]
    if len(model_market) != 8854 or len(gradeable) != 8827 or len(missing) != 6:
        raise ValueError("HR EB candidate market coverage changed")
    if not missing["official_grade_status"].astype(str).eq("VOID_NO_PA").all():
        raise ValueError("a missing HR EB probability is not exact VOID_NO_PA")
    if gradeable["eb_home_runs_0.5"].isna().any():
        raise ValueError("a gradeable HR market row lacks candidate probability")
    probability = pd.to_numeric(merged["eb_home_runs_0.5"], errors="coerce")
    fallback = probability.isna()
    merged["candidate_source"] = np.where(fallback, "production_fallback_VOID_NO_PA_only", "rolling_eb_shared_pa")
    merged["sim_p_over"] = probability.where(~fallback, pd.to_numeric(merged["sim_p_over"], errors="raise"))
    if merged["sim_p_over"].isna().any() or not merged["sim_p_over"].between(0, 1).all():
        raise ValueError("HR EB candidate probability is missing or out of range")
    return merged, {
        "candidate_rows": len(merged),
        "rolling_eb_rows": int((~fallback).sum()),
        "void_no_pa_fallback_rows": int(fallback.sum()),
        "model_market_rows": len(model_market),
        "gradeable_candidate_rows": len(gradeable),
    }


def auc(rows: pd.DataFrame, arm: str) -> float:
    return float(roc_auc_score(rows["won"].astype(int), rows[f"{arm}_p_over"].astype(float)))


def decide(
    roles: dict[str, Any],
    *,
    exact_gradeable_coverage: bool,
    all_draws_valid: bool,
) -> dict[str, Any]:
    if set(roles) != {"diagnostic", "confirmation"}:
        raise ValueError("HR EB decision requires both open blocks")
    diagnostic = roles["diagnostic"]
    confirmation = roles["confirmation"]
    confirmation_interval = confirmation["date_block_intervals"]
    probability_parts = {
        "diagnostic_brier_improved": diagnostic["candidate"]["brier"] < diagnostic["frozen"]["brier"],
        "diagnostic_log_loss_improved": diagnostic["candidate"]["log_loss"] < diagnostic["frozen"]["log_loss"],
        "confirmation_brier_improved": confirmation["candidate"]["brier"] < confirmation["frozen"]["brier"],
        "confirmation_log_loss_improved": confirmation["candidate"]["log_loss"] < confirmation["frozen"]["log_loss"],
        "confirmation_brier_upper_below_zero": confirmation_interval["candidate_minus_frozen_brier_95"][1] < 0.0,
        "confirmation_log_loss_upper_below_zero": confirmation_interval["candidate_minus_frozen_log_loss_95"][1] < 0.0,
        "pooled_auc_noninferior": roles["diagnostic"]["candidate"]["auc"] >= roles["diagnostic"]["frozen"]["auc"] and roles["confirmation"]["candidate"]["auc"] >= roles["confirmation"]["frozen"]["auc"],
        "gradeable_coverage_exact": exact_gradeable_coverage,
        "all_bootstrap_draws_valid": all_draws_valid,
    }
    economic_parts: dict[str, bool] = {}
    for name, role in roles.items():
        interval = role["date_block_intervals"]
        economic_parts[f"{name}_candidate_positive_ev_rows"] = role["candidate"]["positive_ev_rows"] > 0
        economic_parts[f"{name}_movement_lower_above_zero"] = interval["candidate_positive_ev_raw_movement_95"][0] > 0.0
        economic_parts[f"{name}_roi_lower_above_zero"] = interval["candidate_positive_ev_theoretical_roi_95"][0] > 0.0
    probability_ready = all(probability_parts.values())
    economic_ready = all(economic_parts.values())
    return {
        "probability_parts": {key: bool(value) for key, value in probability_parts.items()},
        "historical_economic_parts": {key: bool(value) for key, value in economic_parts.items()},
        "probability_confirmation_ready": bool(probability_ready),
        "historical_economic_ready": bool(economic_ready),
        "production_install_permitted": False,
        "betting_authorized": False,
    }
