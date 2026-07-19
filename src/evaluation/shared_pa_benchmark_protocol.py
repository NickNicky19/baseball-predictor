"""Validator for the predeclared shared batter PA benchmark."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.evaluation.multi_market_foundation import PA_OUTCOMES, sha256


SCHEMA = "shared-batter-pa-benchmark-protocol-v1"
STATUS = "LOCKED_BEFORE_2025_CONFIRMATION"


def validate_protocol(payload: dict[str, Any], *, evidence_root: str | Path) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA or payload.get("status") != STATUS:
        raise ValueError("unrecognized shared PA benchmark protocol")
    if payload.get("betting_authorized") is not False or payload.get("may_2026_forbidden") is not True:
        raise ValueError("benchmark cannot authorize betting or open May")
    foundation = payload.get("foundation_protocol") or {}
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / str(foundation.get("path", ""))
    expected = foundation.get("sha256")
    if not path.is_file() or sha256(path) != expected:
        raise ValueError("foundation protocol is missing or hash-mismatched")
    if payload.get("outcome_classes") != PA_OUTCOMES:
        raise ValueError("PA outcome classes changed")
    if payload.get("identity_key") != ["game_pk", "player_id"]:
        raise ValueError("official game/player identity changed")

    split = payload.get("chronology") or {}
    if split != {
        "fit": [2023],
        "rolling_selection": [2024],
        "confirmation_open_once": [2025],
        "open_2026_research": ["2026-03", "2026-04", "2026-06"],
        "forbidden": ["2026-05"],
    }:
        raise ValueError("benchmark chronology changed")
    boundary = payload.get("selection_input_boundary") or {}
    if boundary != {
        "ordered_prefix_only": True,
        "maximum_rows_read": 87462,
        "expected_rows_by_season": {"2023": 43740, "2024": 43722},
        "expected_date_min": "2023-03-30",
        "expected_date_max": "2024-09-30",
        "forbid_any_loaded_year_at_or_after": 2025,
    }:
        raise ValueError("selection input boundary changed")

    accounting = payload.get("outcome_accounting") or {}
    required_accounting = {
        "strikeout": "out_k",
        "walk": "out_bb",
        "single": "out_hits-out_doubles-out_triples-out_hr",
        "double": "out_doubles",
        "triple": "out_triples",
        "home_run": "out_hr",
        "bip_out": "out_ab-out_k-out_hits",
        "other_non_ab": "out_pa-out_ab-out_bb",
    }
    if accounting != required_accounting:
        raise ValueError("PA outcome accounting changed")
    if payload.get("pa_target_eligibility") != {
        "retain_all_source_rows": True,
        "training_weight": "official_pa_outcome_count",
        "proper_scoring_requires_official_pa_above_zero": True,
        "zero_pa_rows_have_zero_training_weight": True,
        "zero_pa_rows_omitted_only_from_pa_proper_scoring": True,
        "zero_pa_is_not_a_market_settlement_decision": True,
        "zero_pa_is_not_a_model_coverage_claim": True,
        "expected_selection_zero_pa_rows": 32,
        "expected_selection_zero_pa_rows_by_season": {"2023": 14, "2024": 18},
    }:
        raise ValueError("PA target-eligibility or zero-exposure contract changed")

    expected_model_family = {
        "kind": "catboost_multiclass",
        "reason": "One coherent nonlinear probability model with native missing and categorical handling; no separate market-specific regressions.",
        "selection_loss": "MultiClass",
        "random_seed": 260719,
        "bounded_grid": {
            "depth": [4, 6],
            "l2_leaf_reg": [3, 10],
            "learning_rate": [0.03],
            "iterations_max": [1200],
            "early_stopping_rounds": [100],
        },
        "inner_early_stopping": {
            "holdout_tail_official_dates": 28,
            "selection_only": True,
        },
    }
    if payload.get("model_family") != expected_model_family:
        raise ValueError("model family, grid, seed, or early-stopping contract changed")

    forbidden = set(payload.get("forbidden_features") or [])
    outcomes = {
        "out_pa", "out_ab", "out_hits", "out_doubles", "out_triples",
        "out_hr", "out_rbi", "out_runs", "out_bb", "out_k",
    }
    if not outcomes.issubset(forbidden) or not {"game_pk", "player_id", "player_name"}.issubset(forbidden):
        raise ValueError("outcome or identity leakage is not fully forbidden")
    quarantined = set(payload.get("quarantined_until_availability_proven") or [])
    if quarantined != {"umpire_id", "umpire_resolved", "weather_temp", "weather_wind", "weather_resolved", "lineup_slot"}:
        raise ValueError("unproven point-in-time features left quarantine")
    expected_sanitization = {
        "postgame_only_opposing_pitcher_sources": ["actual_starter"],
        "replacement_source": "unavailable_historical",
        "nullified_columns": [
            "opp_sp_throws", "opp_sp_ip", "opp_sp_k9", "opp_sp_bb9", "opp_sp_hr9",
            "opp_sp_gs", "opp_sp_recent_ip", "opp_sp_recent_k9", "opp_sp_recent_bb9",
            "opp_sp_recent_hr9", "platoon_adv",
        ],
        "lineup_slot_classifier_feature_forbidden": True,
        "lineup_slot_allowed_for_pa_volume_fit": True,
        "derived_market_confirmation_requires_point_in_time_reconstructed_slot": True,
    }
    if payload.get("historical_feature_sanitization") != expected_sanitization:
        raise ValueError("historical postgame-feature sanitization changed")

    variants = payload.get("sequential_feature_variants")
    if not isinstance(variants, list) or [item.get("id") for item in variants] != [
        "core", "core_statcast", "core_statcast_pitcher", "core_statcast_pitcher_context"
    ]:
        raise ValueError("feature-selection sequence changed")
    if any(item.get("selection_data") != "2024_rolling_origin_only" for item in variants):
        raise ValueError("feature variant can see confirmation data")
    all_classifier_features = {
        column
        for item in variants
        for group in item["groups"]
        for column in (payload.get("feature_group_contract") or {}).get(group, [])
    }
    if "lineup_slot" in all_classifier_features:
        raise ValueError("official lineup slot entered the PA classifier")

    baselines = payload.get("required_baselines")
    if baselines != [
        "league_rate", "lineup_slot_rate", "empirical_bayes_player_rate",
        "current_production_on_open_2026", "valid_market_implied_on_open_2026",
    ]:
        raise ValueError("required baseline removed or reordered")
    folds = payload.get("selection_folds")
    if folds != [
        ["2024-03-28", "2024-06-30"],
        ["2024-07-01", "2024-07-31"],
        ["2024-08-01", "2024-08-31"],
        ["2024-09-01", "2024-09-30"],
    ]:
        raise ValueError("2024 rolling-origin folds changed")
    simple = payload.get("simple_baseline_selection") or {}
    if simple != {
        "source": "2024_rolling_origin_only",
        "empirical_bayes_prior_strength_pa_grid": [50, 100, 200, 400],
        "selection_eligible": ["league_rate", "empirical_bayes_player_rate"],
        "diagnostic_only": {
            "lineup_slot_rate": "official historical slot is not proven available at the T-4h decision horizon",
        },
        "selection_metric": "multiclass_log_loss",
        "tie_breaker": "multiclass_brier",
    }:
        raise ValueError("simple-baseline selection changed")
    metrics = payload.get("required_metrics") or {}
    if metrics.get("pa_primary") != ["multiclass_log_loss", "multiclass_brier"]:
        raise ValueError("proper-score pair changed")
    if metrics.get("derived_markets") != ["hits_0.5", "hits_1.5", "home_runs_0.5", "total_bases_0.5_to_5.5"]:
        raise ValueError("derived market scoring scope changed")
    if metrics.get("uncertainty_unit") != "official_game_date" or metrics.get("bootstrap_draws") != 10000 or metrics.get("bootstrap_seed") != 260719:
        raise ValueError("uncertainty unit, draws, or seed changed")

    pa_volume = payload.get("pa_volume") or {}
    expected_pa_path = "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/pa_distribution_fit_2023_2024.json"
    if pa_volume.get("artifact_path") != expected_pa_path:
        raise ValueError("PA-volume artifact path changed")
    pa_path = Path(evidence_root) / expected_pa_path
    if not pa_path.is_file() or sha256(pa_path) != pa_volume.get("sha256"):
        raise ValueError("PA-volume artifact is missing or hash-mismatched")
    if pa_volume.get("realized_pa_forbidden_as_prediction_input") is not True:
        raise ValueError("realized PA prediction leakage admitted")

    gate = payload.get("confirmation_gate") or {}
    required_true = {
        "beat_current_and_best_simple_where_comparable",
        "paired_log_loss_interval_upper_below_zero",
        "paired_brier_interval_upper_below_zero",
        "calibration_noninferior",
        "discrimination_noninferior",
        "all_season_direction_consistent",
        "relevant_line_direction_consistent",
        "identical_coverage",
        "fallbacks_measured",
        "no_subgroup_material_regression",
        "open_2026_economic_coherence",
        "production_requires_capture_lower_bound_above_0_10",
        "production_requires_net_roi_lower_bound_above_zero",
    }
    if set(gate) != required_true or not all(value is True for value in gate.values()):
        raise ValueError("confirmation/material-improvement gate weakened")
    calibration = payload.get("calibration") or {}
    expected_calibration = {
        "method": "single_temperature_multiclass",
        "selection_source": "2024_out_of_fold_only",
        "selection_evaluation": "chronological_cross_fitted_by_selection_fold",
        "first_fold_temperature": 1.0,
        "log_temperature_bounds": [-2.0, 2.0],
        "identity_calibration_is_default": True,
        "install_only_if_both_proper_scores_improve": True,
        "install_only_if_both_paired_interval_uppers_below_zero": True,
    }
    if calibration != expected_calibration:
        raise ValueError("calibration chronology, bounds, or non-regression contract changed")
    return payload


def load_protocol(path: str | Path, *, evidence_root: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("shared PA benchmark protocol must be an object")
    return validate_protocol(payload, evidence_root=evidence_root)
