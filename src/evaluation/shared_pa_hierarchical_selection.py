"""Fail-closed contract and helpers for hierarchical PA selection."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from src.evaluation.multi_market_foundation import PA_OUTCOMES
from src.evaluation.shared_pa_cumulative_selection import load_protocol as load_cumulative_protocol
from src.evaluation.shared_pa_rejection_diagnostic import sha256
from src.learning.shared_pa_hierarchical_model import STAGE_1, STAGE_2


SCHEMA = "shared-pa-hierarchical-selection-protocol-v1"
STATUS = "LOCKED_BEFORE_2024_HIERARCHICAL_SELECTION"
MARKETS = [
    "hits_0.5", "hits_1.5", "home_runs_0.5", "total_bases_0.5",
    "total_bases_1.5", "total_bases_2.5", "total_bases_3.5",
    "total_bases_4.5", "total_bases_5.5",
]


def _root(item: dict[str, Any], *, code_root: Path, evidence_root: Path) -> Path:
    if item.get("root") == "code":
        return code_root
    if item.get("root") == "evidence":
        return evidence_root
    raise ValueError("hierarchical protocol input root changed")


def load_protocol(path: Path, *, code_root: Path, evidence_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if protocol.get("schema_version") != SCHEMA or protocol.get("status") != STATUS:
        raise ValueError("hierarchical selection protocol is not locked")
    if protocol.get("betting_authorized") or not protocol.get("production_unchanged"):
        raise ValueError("hierarchical selection changed authorization or production")
    if not protocol.get("confirmation_2025_forbidden_during_selection") or not protocol.get("may_2026_forbidden"):
        raise ValueError("hierarchical selection crossed protected evidence")
    if protocol.get("chronology") != {
        "initial_fit": [2023], "rolling_selection": [2024],
        "confirmation_open_once_only_after_selection_pass": [2025], "forbidden": ["2026-05"],
    }:
        raise ValueError("hierarchical chronology changed")
    for name, item in protocol.get("inputs", {}).items():
        artifact = _root(item, code_root=code_root, evidence_root=evidence_root) / item["path"]
        if not artifact.is_file() or sha256(artifact) != item["sha256"]:
            raise ValueError(f"hierarchical input changed: {name}")
        if "required_status" in item:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            if payload.get("status") != item["required_status"]:
                raise ValueError(f"hierarchical input status changed: {name}")
    intervention = protocol.get("single_intervention", {})
    if intervention.get("id") != "hierarchical_recent_noncontact_cumulative_contact":
        raise ValueError("hierarchical intervention changed")
    measured = intervention.get("measured_basis", {})
    if measured != {
        "robust_gain_outcomes": ["bip_out", "single"],
        "largest_offsetting_log_loss_regression": "strikeout",
        "derived_markets_robust_vs_both_comparators": [],
    }:
        raise ValueError("hierarchical measured basis changed")
    if set(intervention.get("forbidden_additions", [])) != {
        "opposing_pitcher", "park", "weather", "umpire", "lineup_slot_classifier",
        "player_identifier", "hand_set_shrinkage", "new_hyperparameter_search",
        "market_price", "policy_threshold",
    }:
        raise ValueError("hierarchical forbidden additions changed")
    hierarchy = protocol.get("hierarchy", {})
    if hierarchy.get("stage_1", {}).get("outcomes") != STAGE_1:
        raise ValueError("hierarchical stage 1 outcomes changed")
    if hierarchy.get("stage_2", {}).get("outcomes") != STAGE_2:
        raise ValueError("hierarchical stage 2 outcomes changed")
    if hierarchy.get("required_final_outcomes") != PA_OUTCOMES:
        raise ValueError("hierarchical final outcomes changed")
    if hierarchy["stage_1"].get("feature_groups") != ["canonical_hitter_46d"]:
        raise ValueError("hierarchical stage 1 features changed")
    if hierarchy["stage_2"].get("feature_groups") != ["canonical_hitter_46d", "canonical_hitter_cumulative"]:
        raise ValueError("hierarchical stage 2 features changed")
    model = protocol.get("model", {})
    if model.get("fixed_params_from_prior_bounded_selection") != {
        "depth": 4, "l2_leaf_reg": 10.0, "learning_rate": 0.03, "iterations": 1200,
    }:
        raise ValueError("hierarchical fixed parameters changed")
    if model.get("stability_audit_seeds") != [260725, 260726]:
        raise ValueError("hierarchical stability seeds changed")
    if model.get("kind") != "two_stage_catboost_multiclass" or model.get("loss") != "MultiClass" or model.get("random_seed") != 260724:
        raise ValueError("hierarchical model identity changed")
    if model.get("inner_early_stopping") != {"holdout_tail_official_dates": 28, "early_stopping_rounds": 100}:
        raise ValueError("hierarchical early stopping changed")
    if protocol.get("selection_folds") != [
        ["2024-03-28", "2024-06-30"], ["2024-07-01", "2024-07-31"],
        ["2024-08-01", "2024-08-31"], ["2024-09-01", "2024-09-30"],
    ]:
        raise ValueError("hierarchical selection folds changed")
    inference = protocol.get("inference", {})
    if inference != {
        "primary_metrics": ["multiclass_log_loss", "multiclass_brier"],
        "bootstrap_draws": 10000, "bootstrap_seed": 260724,
        "uncertainty_unit": "official_game_date",
    }:
        raise ValueError("hierarchical inference changed")
    gates = protocol.get("derived_market_gates", {})
    if gates.get("markets") != MARKETS:
        raise ValueError("hierarchical derived markets changed")
    if not all(gates.get(name) is True for name in (
        "each_market_evaluated_separately",
        "market_eligible_only_if_both_score_points_and_upper_bounds_below_zero_vs_both_comparators",
        "one_market_cannot_validate_another",
        "confirmation_may_open_only_if_foundation_passes_and_at_least_one_market_is_independently_eligible",
    )):
        raise ValueError("hierarchical market separation changed")
    materiality = protocol.get("materiality", {})
    required = materiality.get("vs_strongest_simple_required_point_and_upper_below", {})
    if not math.isclose(float(required.get("multiclass_log_loss", 0)), -0.012134926932596546, rel_tol=0, abs_tol=1e-15):
        raise ValueError("hierarchical log-loss materiality changed")
    if not math.isclose(float(required.get("multiclass_brier", 0)), -0.004660965632301117, rel_tol=0, abs_tol=1e-15):
        raise ValueError("hierarchical Brier materiality changed")
    if materiality.get("vs_canonical_v1_required_point_and_upper_below") != {
        "multiclass_log_loss": 0.0, "multiclass_brier": 0.0,
    }:
        raise ValueError("hierarchical canonical-v1 gate changed")
    if materiality.get("all_stability_seeds_must_improve_both_metrics_vs_both_comparators") is not True or materiality.get("coverage_loss_allowed") != 0:
        raise ValueError("hierarchical stability or coverage gate changed")
    if protocol.get("calibration") != {
        "method": "single_temperature_multiclass",
        "cross_fitted_on_prior_oof_folds_only": True,
        "first_fold_temperature": 1.0,
        "log_temperature_bounds": [-2.0, 2.0],
        "install_only_if_both_scores_improve_and_both_paired_upper_bounds_below_zero": True,
    }:
        raise ValueError("hierarchical calibration contract changed")
    if protocol.get("selection_output") != {
        "publish_model_only_if_foundation_and_at_least_one_market_pass": True,
        "confirmation_2025_must_remain_unread_on_rejection": True,
        "may_2026_must_remain_unread": True,
        "production_must_remain_unchanged": True,
        "betting_authorized": False,
    }:
        raise ValueError("hierarchical selection output changed")
    cumulative_record = protocol["inputs"]["cumulative_selection_protocol"]
    cumulative, base = load_cumulative_protocol(
        code_root / cumulative_record["path"], evidence_root=evidence_root, code_root=code_root
    )
    rejection = json.loads((code_root / protocol["inputs"]["certified_rejection_diagnostic"]["path"]).read_text(encoding="utf-8"))
    if not rejection.get("decision", {}).get("cumulative_candidate_remains_rejected"):
        raise ValueError("hierarchical predecessor is not rejected")
    if not rejection.get("decision", {}).get("confirmation_2025_must_remain_unread"):
        raise ValueError("hierarchical predecessor opened confirmation")
    return protocol, cumulative, base


def stage_features(protocol: dict[str, Any], cumulative: dict[str, Any], stage: str) -> list[str]:
    groups = protocol["hierarchy"][stage]["feature_groups"]
    features: list[str] = []
    for group in groups:
        features.extend(cumulative["feature_groups"][group])
    if len(features) != len(set(features)):
        raise ValueError("hierarchical stage contains duplicate features")
    return features


def clears_primary(
    protocol: dict[str, Any],
    *,
    vs_simple: dict[str, dict[str, float]],
    vs_v1: dict[str, dict[str, float]],
) -> tuple[bool, dict[str, Any]]:
    decisions: dict[str, Any] = {"vs_strongest_simple": {}, "vs_canonical_v1": {}}
    for metric in protocol["inference"]["primary_metrics"]:
        simple_required = float(protocol["materiality"]["vs_strongest_simple_required_point_and_upper_below"][metric])
        simple = vs_simple[metric]
        simple_pass = float(simple["point"]) < simple_required and float(simple["upper"]) < simple_required
        decisions["vs_strongest_simple"][metric] = {
            "required_below": simple_required,
            "point": float(simple["point"]), "upper": float(simple["upper"]),
            "passed": bool(simple_pass),
        }
        v1_required = float(protocol["materiality"]["vs_canonical_v1_required_point_and_upper_below"][metric])
        v1 = vs_v1[metric]
        v1_pass = float(v1["point"]) < v1_required and float(v1["upper"]) < v1_required
        decisions["vs_canonical_v1"][metric] = {
            "required_below": v1_required,
            "point": float(v1["point"]), "upper": float(v1["upper"]),
            "passed": bool(v1_pass),
        }
    return bool(all(item["passed"] for group in decisions.values() for item in group.values())), decisions


def eligible_market(comparisons: dict[str, dict[str, dict[str, float]]]) -> bool:
    return all(
        float(comparisons[f"vs_{comparator}"][metric]["point"]) < 0.0
        and float(comparisons[f"vs_{comparator}"][metric]["upper"]) < 0.0
        for comparator in ("strongest_simple", "canonical_v1")
        for metric in ("log_loss", "brier")
    )
