#!/usr/bin/env python3
"""Run the hash-locked 2023-only true-EB-offset shared-PA experiment."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_eb_offset_catboost import (  # noqa: E402
    PA_OUTCOMES,
    empirical_bayes_probability,
    fit_eb_offset_catboost,
    fit_frozen_core_catboost,
    history_counts,
    league_probability,
    target_counts,
)
from src.evaluation.shared_pa_offset_evaluation import (  # noqa: E402
    binary_market_counts,
    binary_market_probability,
    component,
    derive_market_probabilities,
    fit_pooled_pa_distribution,
    metrics,
    paired_date_interval,
)


ARM_ORDER = (
    "candidate_true_eb_offset_catboost",
    "league_rate",
    "empirical_bayes_mle",
    "empirical_bayes_pa_200",
    "frozen_all_prior_catboost_core",
)
COMPARATOR_ORDER = ARM_ORDER[1:]
COMPONENTS = {
    "hits": ("per_pa_hit_event", "hits_over_0_5", "hits_over_1_5"),
    "hr_over_0_5": ("per_pa_home_run_event", "home_runs_over_0_5"),
    "total_bases": (
        "per_pa_total_bases_distribution",
        "total_bases_over_0_5",
        "total_bases_over_1_5",
        "total_bases_over_2_5",
        "total_bases_over_3_5",
        "total_bases_over_4_5",
        "total_bases_over_5_5",
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def verify_execution_lock(lock_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify code/config/test/runtime bytes before opening any panel outcomes."""

    lock = _load_json(lock_path)
    require(
        lock.get("schema_version") == "shared-pa-true-eb-offset-execution-lock-v1",
        "unexpected execution-lock schema",
    )
    require(lock.get("status") == "LOCKED_BEFORE_FIRST_V4_1_OUTCOME_SCORE", "execution is not pre-score locked")
    require(lock.get("development_years") == [2023], "execution lock permits a non-2023 year")
    boundaries = lock.get("protected_boundaries", {})
    required_false = ("production_changed", "betting_authorized", "selection_or_confirmation_opened")
    require(all(boundaries.get(name) is False for name in required_false), "execution lock claims a prohibited action")
    require(boundaries.get("may_2026_sealed") is True, "May 2026 seal is absent")
    require(boundaries.get("spent_2025_hr_not_reused") is True, "spent HR evidence boundary is absent")
    checked: dict[str, Any] = {}
    files = lock.get("required_file_hashes")
    require(isinstance(files, dict) and files, "execution lock lacks required file hashes")
    for raw_path, expected in sorted(files.items()):
        path = ROOT / raw_path
        require(path.is_file(), f"locked file is missing: {raw_path}")
        actual = sha256_file(path)
        require(actual == expected, f"locked file hash mismatch: {raw_path}")
        checked[raw_path] = {"sha256": actual, "bytes": path.stat().st_size}
    runtime = lock.get("runtime", {})
    require(sys.version.split()[0] == runtime.get("python_version"), "Python patch version differs from execution lock")
    installed: dict[str, str] = {}
    for name, expected in sorted(runtime.get("distributions", {}).items()):
        actual = importlib.metadata.version(name)
        require(actual == expected, f"installed distribution differs from lock: {name}")
        installed[name] = actual
    return lock, {"files": checked, "python_version": sys.version.split()[0], "distributions": installed}


def load_protocol(path: Path) -> dict[str, Any]:
    protocol = _load_json(path)
    require(
        protocol.get("schema_version") == "shared-pa-true-eb-offset-development-protocol-v1",
        "unexpected development protocol schema",
    )
    require(
        protocol.get("status") == "DESIGN_LOCKED_BEFORE_ANY_V4_1_OUTCOME_SCORING",
        "development protocol is not locked before scoring",
    )
    require(protocol["candidate"]["outcomes"] == list(PA_OUTCOMES), "PA outcome order changed")
    require(protocol["data"]["development_years"] == [2023], "protocol permits a non-2023 year")
    require(protocol["pa_opportunity"]["target_game_lineup_slot_consumed"] is False, "target lineup is not prohibited")
    require(protocol["protected_boundaries"]["may_2026_sealed"] is True, "May seal is absent")
    require(protocol["protected_boundaries"]["betting_authorized"] is False, "protocol claims betting authorization")
    return protocol


def _validate_panel_inputs(
    *,
    panel_path: Path,
    manifest_path: Path,
    certificate_path: Path,
    protocol: Mapping[str, Any],
    lock: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    data = protocol["data"]
    expected = {
        panel_path: data["panel_sha256"],
        manifest_path: data["panel_manifest_sha256"],
        certificate_path: data["panel_certificate_sha256"],
    }
    for path, digest in expected.items():
        require(path.is_file(), f"required panel artifact is missing: {path}")
        require(sha256_file(path) == digest, f"panel artifact hash mismatch: {path.name}")
    manifest = _load_json(manifest_path)
    certificate = _load_json(certificate_path)
    require(
        manifest.get("status") == "DIRECT_BATTER_PA_TIMING_CONTRACT_PASSED_RESEARCH_ONLY",
        "panel timing contract is not passed",
    )
    require(manifest.get("output", {}).get("sha256") == data["panel_sha256"], "manifest does not bind panel")
    require(manifest.get("may_2026_opened") is False, "panel manifest reports May 2026 opened")
    require(manifest.get("confirmation_2025_opened") is False, "panel manifest reports 2025 confirmation opened")
    require(manifest.get("production_changed") is False and manifest.get("betting_authorized") is False, "panel manifest claims prohibited use")
    require(
        certificate.get("status") == "DIRECT_BATTER_PA_PANEL_CERTIFIED_RESEARCH_ONLY",
        "panel certificate is not research certified",
    )
    require(certificate.get("panel", {}).get("sha256") == data["panel_sha256"], "certificate does not bind panel")
    require(certificate.get("manifest", {}).get("sha256") == data["panel_manifest_sha256"], "certificate does not bind manifest")
    require(certificate.get("protected_invariants", {}).get("may_2026_opened") is False, "certificate reports May opened")
    validation = certificate.get("validation", {})
    require(validation.get("physical_target_rows_2023") == data["physical_rows"], "physical row count changed")
    require(validation.get("fit_eligible_rows_2023") == data["fit_eligible_positive_pa_rows"], "eligible row count changed")
    require(validation.get("zero_pa_rows") == data["receipt_proven_zero_pa_rows_excluded_from_scoring"], "zero-PA count changed")
    require(validation.get("chronology_violations") == 0 and validation.get("identity_duplicates") == 0, "certified integrity count is nonzero")

    frame = pd.read_csv(panel_path, low_memory=False)
    require(len(frame) == data["physical_rows"], "physical panel rows changed")
    require(not frame.columns.duplicated().any(), "panel has duplicate columns")
    required = {
        "season",
        "game_date",
        "game_pk",
        "player_id",
        "max_source_date",
        "out_pa",
        "out_ab",
        "out_hits",
        "out_doubles",
        "out_triples",
        "out_hr",
        "out_bb",
        "out_k",
        "history_pa",
        *[f"history_{name}_count" for name in PA_OUTCOMES],
        *[f"target_{name}" for name in PA_OUTCOMES],
        *protocol["candidate"]["features"],
    }
    missing = sorted(required.difference(frame.columns))
    require(not missing, f"panel misses locked columns: {missing}")
    years = pd.to_numeric(frame["season"], errors="coerce")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    source_dates = pd.to_datetime(frame["max_source_date"], format="%Y-%m-%d", errors="coerce")
    require(years.notna().all() and set(years.astype(int)) == {2023}, "panel is not exclusively 2023")
    require(dates.notna().all() and dates.dt.year.eq(2023).all(), "panel game date is not exclusively 2023")
    require(not (source_dates.notna() & source_dates.ge(dates)).any(), "feature chronology is not strictly prior")
    identity = ["season", "game_date", "game_pk", "player_id"]
    require(frame[identity].notna().all().all() and not frame.duplicated(identity).any(), "panel identity is missing or duplicated")
    consumed = target_counts(frame.loc[pd.to_numeric(frame["out_pa"], errors="coerce").gt(0)])
    eligible_mask = pd.to_numeric(frame["out_pa"], errors="coerce").gt(0)
    raw = frame.loc[eligible_mask, [f"target_{name}" for name in PA_OUTCOMES]].to_numpy(float)
    require(np.array_equal(consumed, raw), "probability-consumer outcome truth differs from panel targets")
    require(int(eligible_mask.sum()) == data["fit_eligible_positive_pa_rows"], "positive-PA population changed")
    require(int((~eligible_mask).sum()) == data["receipt_proven_zero_pa_rows_excluded_from_scoring"], "zero-PA exclusion changed")
    eligible = frame.loc[eligible_mask].reset_index(drop=True)
    history_counts(eligible)
    for count_column, rate_column in (("history_barrel_count", "history_barrel_rate"), ("history_hard_hit_count", "history_hard_hit_rate")):
        require(count_column in eligible and rate_column in eligible, "batted-ball lineage is missing")
    barrel_count = pd.to_numeric(eligible["history_barrel_count"], errors="coerce")
    hard_count = pd.to_numeric(eligible["history_hard_hit_count"], errors="coerce")
    barrel_rate = pd.to_numeric(eligible["history_barrel_rate"], errors="coerce")
    hard_rate = pd.to_numeric(eligible["history_hard_hit_rate"], errors="coerce")
    require(not barrel_count.gt(hard_count).any(), "barrel count exceeds hard-hit count")
    require(not (barrel_rate.notna() & hard_rate.notna() & barrel_rate.gt(hard_rate + 1e-12)).any(), "barrel rate exceeds hard-hit rate")
    core_features = [name for name in frame.columns if name.startswith("history_") and "age_days" not in name]
    require(len(core_features) == lock["frozen_core_feature_identity"]["count"], "frozen core feature count changed")
    require(canonical_sha256(core_features) == lock["frozen_core_feature_identity"]["ordered_names_sha256"], "frozen core feature identity changed")
    return eligible, {
        "physical_rows": int(len(frame)),
        "fit_eligible_rows": int(len(eligible)),
        "zero_pa_rows_excluded": int((~eligible_mask).sum()),
        "date_min": dates.min().date().isoformat(),
        "date_max": dates.max().date().isoformat(),
        "core_features": core_features,
    }


def chronological_splits(frame: pd.DataFrame, n_splits: int) -> list[tuple[np.ndarray, np.ndarray]]:
    date_values = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise").dt.date
    dates = np.array(sorted(date_values.unique()))
    require(n_splits >= 2 and len(dates) > n_splits, "chronological split dimensions are invalid")
    test_size = len(dates) // (n_splits + 1)
    require(test_size > 0, "chronological validation window is empty")
    starts = range(len(dates) - n_splits * test_size, len(dates), test_size)
    output: list[tuple[np.ndarray, np.ndarray]] = []
    for start in starts:
        train_dates = dates[:start]
        validation_dates = dates[start : start + test_size]
        require(max(train_dates) < min(validation_dates), "chronological split overlaps")
        train = np.flatnonzero(date_values.isin(set(train_dates)).to_numpy())
        validation = np.flatnonzero(date_values.isin(set(validation_dates)).to_numpy())
        require(len(train) > 0 and len(validation) > 0, "chronological fold is empty")
        output.append((train, validation))
    require(len(output) == n_splits, "chronological fold count changed")
    return output


def _component_values(
    frame: pd.DataFrame,
    pa_probability: np.ndarray,
    market_probability: Mapping[str, np.ndarray],
    name: str,
) -> tuple[np.ndarray, np.ndarray]:
    if name.startswith("per_pa_"):
        return component(frame, pa_probability, name)
    return binary_market_counts(frame, name), binary_market_probability(market_probability[name])


def _calibration_pass(candidate: Mapping[str, Any], comparator: Mapping[str, Any]) -> bool:
    return (
        float(candidate["max_absolute_calibration_bias"]) <= float(comparator["max_absolute_calibration_bias"]) + 1e-15
        and float(candidate["mean_ece_10"]) <= float(comparator["mean_ece_10"]) + 1e-15
    )


def _auc_noninferior(candidate: Mapping[str, Any], comparator: Mapping[str, Any]) -> bool:
    left, right = candidate.get("auc"), comparator.get("auc")
    return left is not None and right is not None and float(left) + 1e-15 >= float(right)


def _evaluate_components(
    *,
    selection: pd.DataFrame,
    pa_by_arm: Mapping[str, np.ndarray],
    market_by_arm: Mapping[str, Mapping[str, np.ndarray]],
    fold_rows: list[np.ndarray],
    draws: int,
    seed: int,
) -> tuple[dict[str, Any], bool]:
    results: dict[str, Any] = {}
    all_markets_pass = True
    for market, names in COMPONENTS.items():
        market_result: dict[str, Any] = {"components": {}, "passed": True}
        for name in names:
            counts, candidate_probability = _component_values(
                selection, pa_by_arm[ARM_ORDER[0]], market_by_arm[ARM_ORDER[0]], name
            )
            candidate_score = metrics(counts, candidate_probability)
            row: dict[str, Any] = {"candidate": candidate_score, "comparators": {}, "passed": True}
            for comparator_name in COMPARATOR_ORDER:
                _, comparator_probability = _component_values(
                    selection, pa_by_arm[comparator_name], market_by_arm[comparator_name], name
                )
                comparator_score = metrics(counts, comparator_probability)
                interval = paired_date_interval(
                    counts,
                    candidate_probability,
                    comparator_probability,
                    selection["game_date"],
                    draws=draws,
                    seed=seed,
                )
                materiality: dict[str, Any] = {}
                proper_pass = True
                for metric_name in ("brier", "log_loss"):
                    required_below = -0.01 * float(comparator_score[metric_name])
                    passed = (
                        interval[metric_name]["point"] < required_below
                        and interval[metric_name]["upper"] < required_below
                    )
                    materiality[metric_name] = {
                        **interval[metric_name],
                        "required_below": required_below,
                        "passed": bool(passed),
                    }
                    proper_pass = proper_pass and passed
                aggregate_auc = _auc_noninferior(candidate_score, comparator_score)
                fold_auc = True
                fold_details: list[dict[str, Any]] = []
                for fold_number, rows in enumerate(fold_rows, start=1):
                    fold_counts = counts[rows]
                    fold_candidate = metrics(fold_counts, candidate_probability[rows])
                    fold_comparator = metrics(fold_counts, comparator_probability[rows])
                    passed = _auc_noninferior(fold_candidate, fold_comparator)
                    fold_details.append(
                        {
                            "fold": fold_number,
                            "candidate_auc": fold_candidate["auc"],
                            "comparator_auc": fold_comparator["auc"],
                            "passed": bool(passed),
                        }
                    )
                    fold_auc = fold_auc and passed
                calibration = _calibration_pass(candidate_score, comparator_score)
                comparison_pass = proper_pass and aggregate_auc and fold_auc and calibration
                row["comparators"][comparator_name] = {
                    "score": comparator_score,
                    "paired_date_interval": interval,
                    "materiality": materiality,
                    "auc_noninferior_aggregate": bool(aggregate_auc),
                    "auc_noninferior_every_fold": bool(fold_auc),
                    "fold_auc": fold_details,
                    "calibration_noninferior": bool(calibration),
                    "passed": bool(comparison_pass),
                }
                row["passed"] = row["passed"] and comparison_pass
            market_result["components"][name] = row
            market_result["passed"] = market_result["passed"] and row["passed"]
        results[market] = market_result
        all_markets_pass = all_markets_pass and market_result["passed"]
    return results, bool(all_markets_pass)


def _hr_tail_gate(
    *,
    selection: pd.DataFrame,
    market_by_arm: Mapping[str, Mapping[str, np.ndarray]],
    draws: int,
    seed: int,
) -> dict[str, Any]:
    candidate = market_by_arm[ARM_ORDER[0]]["home_runs_over_0_5"]
    count = max(1, int(np.ceil(len(candidate) * 0.10)))
    identity = selection[["game_pk", "player_id"]].astype(str).agg("|".join, axis=1).to_numpy()
    order = np.lexsort((identity, -candidate))
    rows = np.sort(order[:count])
    tail = selection.iloc[rows].reset_index(drop=True)
    counts = binary_market_counts(tail, "home_runs_over_0_5")
    candidate_probability = binary_market_probability(candidate[rows])
    candidate_score = metrics(counts, candidate_probability)
    support_pass = len(tail) >= 500 and int(counts[:, 1].sum()) >= 20
    result: dict[str, Any] = {
        "definition": "deterministic highest candidate home_runs_over_0_5 probability ceil(10%)",
        "rows": int(len(tail)),
        "home_runs": int(counts[:, 1].sum()),
        "probability_min": float(candidate[rows].min()),
        "candidate": candidate_score,
        "support_passed": bool(support_pass),
        "comparators": {},
        "passed": bool(support_pass),
    }
    for comparator_name in COMPARATOR_ORDER:
        comparator_probability = binary_market_probability(
            market_by_arm[comparator_name]["home_runs_over_0_5"][rows]
        )
        comparator_score = metrics(counts, comparator_probability)
        interval = paired_date_interval(
            counts,
            candidate_probability,
            comparator_probability,
            tail["game_date"],
            draws=draws,
            seed=seed,
        )
        calibration = (
            abs(float(candidate_score["max_absolute_calibration_bias"]))
            <= abs(float(comparator_score["max_absolute_calibration_bias"])) + 1e-15
        )
        proper = all(
            interval[name]["point"] <= 0.0 and interval[name]["upper"] <= 0.0
            for name in ("brier", "log_loss")
        )
        passed = calibration and proper
        result["comparators"][comparator_name] = {
            "score": comparator_score,
            "paired_date_interval": interval,
            "absolute_calibration_gap_noninferior": bool(calibration),
            "proper_score_point_and_upper_noninferior": bool(proper),
            "passed": bool(passed),
        }
        result["passed"] = result["passed"] and passed
    return result


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def run(
    *,
    panel: Path,
    panel_manifest: Path,
    panel_certificate: Path,
    protocol_path: Path,
    execution_lock_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    require(not output_dir.exists(), "refusing to overwrite an existing experiment output directory")
    lock, runtime_identity = verify_execution_lock(execution_lock_path)
    protocol = load_protocol(protocol_path)
    require(
        sha256_file(protocol_path) == lock["required_file_hashes"][str(protocol_path.relative_to(ROOT)).replace("\\", "/")],
        "protocol identity is not execution locked",
    )
    frame, panel_identity = _validate_panel_inputs(
        panel_path=panel,
        manifest_path=panel_manifest,
        certificate_path=panel_certificate,
        protocol=protocol,
        lock=lock,
    )
    split_rows = chronological_splits(frame, protocol["chronological_evaluation"]["outer_folds"])
    arrays = {
        arm: np.full((len(frame), len(PA_OUTCOMES)), np.nan, dtype=float) for arm in ARM_ORDER
    }
    market_arrays: dict[str, dict[str, np.ndarray]] = {arm: {} for arm in ARM_ORDER}
    evaluated = np.zeros(len(frame), dtype=bool)
    fold_records: list[dict[str, Any]] = []
    fold_selection_rows: list[np.ndarray] = []
    core_features = panel_identity["core_features"]
    candidate_features = protocol["candidate"]["features"]
    parameters = protocol["candidate"]["residual_model"]
    for fold_number, (train_rows, validation_rows) in enumerate(split_rows, start=1):
        require(not evaluated[validation_rows].any(), "outer validation rows overlap")
        train = frame.iloc[train_rows]
        validation = frame.iloc[validation_rows]
        candidate_model = fit_eb_offset_catboost(
            train,
            features=candidate_features,
            locked_parameters=parameters,
        )
        core_model = fit_frozen_core_catboost(
            train,
            features=core_features,
            locked_parameters=parameters,
        )
        league = league_probability(train)
        pa_probability = {
            ARM_ORDER[0]: candidate_model.predict_proba(validation),
            "league_rate": np.tile(league, (len(validation), 1)),
            "empirical_bayes_mle": empirical_bayes_probability(
                validation, league, candidate_model.concentration
            ),
            "empirical_bayes_pa_200": empirical_bayes_probability(validation, league, 200.0),
            "frozen_all_prior_catboost_core": core_model.predict_proba(validation),
        }
        pa_distribution = fit_pooled_pa_distribution(train)
        market_probability = {
            arm: derive_market_probabilities(probability, pa_distribution)
            for arm, probability in pa_probability.items()
        }
        for arm in ARM_ORDER:
            arrays[arm][validation_rows] = pa_probability[arm]
            for name, values in market_probability[arm].items():
                market_arrays[arm].setdefault(name, np.full(len(frame), np.nan, dtype=float))
                market_arrays[arm][name][validation_rows] = values
        evaluated[validation_rows] = True
        fold_records.append(
            {
                "fold": fold_number,
                "train_rows": int(len(train)),
                "validation_rows": int(len(validation)),
                "train_date_min": str(train["game_date"].min()),
                "train_date_max": str(train["game_date"].max()),
                "validation_date_min": str(validation["game_date"].min()),
                "validation_date_max": str(validation["game_date"].max()),
                "fitted_dirichlet_concentration": float(candidate_model.concentration),
                "pooled_pa_distribution": pa_distribution,
            }
        )
    expected = np.zeros(len(frame), dtype=bool)
    for _, validation_rows in split_rows:
        expected[validation_rows] = True
    require(np.array_equal(evaluated, expected), "OOF coverage differs from locked fold windows")
    require(all(np.isfinite(arrays[arm][evaluated]).all() for arm in ARM_ORDER), "OOF PA probability is incomplete")
    require(
        all(np.isfinite(values[evaluated]).all() for arm in ARM_ORDER for values in market_arrays[arm].values()),
        "OOF game-market probability is incomplete",
    )
    selection = frame.loc[evaluated].reset_index(drop=True)
    pa_selected = {arm: arrays[arm][evaluated] for arm in ARM_ORDER}
    market_selected = {
        arm: {name: values[evaluated] for name, values in market_arrays[arm].items()}
        for arm in ARM_ORDER
    }
    source_to_selected = np.full(len(frame), -1, dtype=int)
    source_to_selected[np.flatnonzero(evaluated)] = np.arange(int(evaluated.sum()))
    for _, validation_rows in split_rows:
        fold_selection_rows.append(source_to_selected[validation_rows])
    components, component_gates = _evaluate_components(
        selection=selection,
        pa_by_arm=pa_selected,
        market_by_arm=market_selected,
        fold_rows=fold_selection_rows,
        draws=protocol["chronological_evaluation"]["bootstrap_draws"],
        seed=protocol["chronological_evaluation"]["bootstrap_seed"],
    )
    hr_tail = _hr_tail_gate(
        selection=selection,
        market_by_arm=market_selected,
        draws=protocol["chronological_evaluation"]["bootstrap_draws"],
        seed=protocol["chronological_evaluation"]["bootstrap_seed"],
    )
    passed = component_gates and bool(hr_tail["passed"])
    status = (
        "2023_DEVELOPMENT_SURVIVOR_REQUIRES_NEW_UNTOUCHED_PROSPECTIVE_CONFIRMATION"
        if passed
        else "2023_DEVELOPMENT_REJECTED_NO_CANDIDATE"
    )
    prediction_frame = selection[["season", "game_date", "game_pk", "player_id"]].copy()
    for arm in ARM_ORDER:
        for index, outcome in enumerate(PA_OUTCOMES):
            prediction_frame[f"{arm}__pa_{outcome}"] = pa_selected[arm][:, index]
        for name in sorted(market_selected[arm]):
            prediction_frame[f"{arm}__{name}"] = market_selected[arm][name]
    report = {
        "schema_version": "shared-pa-true-eb-offset-development-report-v1",
        "candidate_id": protocol["candidate"]["id"],
        "status": status,
        "scope": "2023_ONLY_RESEARCH_DEVELOPMENT_NOT_MARKET_QUALIFICATION",
        "input_identity": {
            "panel": {"path": str(panel), "sha256": sha256_file(panel)},
            "panel_manifest": {"path": str(panel_manifest), "sha256": sha256_file(panel_manifest)},
            "panel_certificate": {"path": str(panel_certificate), "sha256": sha256_file(panel_certificate)},
            "protocol": {"path": str(protocol_path), "sha256": sha256_file(protocol_path)},
            "execution_lock": {"path": str(execution_lock_path), "sha256": sha256_file(execution_lock_path)},
        },
        "runtime_identity": runtime_identity,
        "panel_identity": panel_identity,
        "chronology": {
            "development_years": [2023],
            "outer_folds": len(fold_records),
            "all_validation_rows_out_of_fold": True,
            "initial_training_only_rows": int((~evaluated).sum()),
            "oof_eligible_rows": int(evaluated.sum()),
            "coverage_loss_within_oof_windows": 0,
            "target_game_lineup_slot_consumed": False,
            "2024_opened": False,
            "2025_opened": False,
            "may_2026_opened": False,
        },
        "folds": fold_records,
        "components": components,
        "hr_high_probability_tail": hr_tail,
        "gates": {
            "separate_market_component_gates_passed": bool(component_gates),
            "hr_high_probability_tail_passed": bool(hr_tail["passed"]),
            "all_predeclared_development_gates_passed": bool(passed),
        },
        "limitations": [
            "The historical game-market screen uses one pooled outer-training PA distribution because target-game historical lineup availability is not receipt-proven.",
            "This is not executable-price, settlement, ROI, promotion, activation, or betting evidence.",
            "A survivor still needs a candidate lock followed by a new untouched future confirmation window with valid pregame opportunity receipts.",
        ],
        "production_changed": False,
        "betting_authorized": False,
    }
    decision = {
        "schema_version": "shared-pa-true-eb-offset-development-decision-v1",
        "candidate_id": protocol["candidate"]["id"],
        "decision": status,
        "market_pass": {name: bool(value["passed"]) for name, value in components.items()},
        "hr_tail_pass": bool(hr_tail["passed"]),
        "selection_or_confirmation_opened": False,
        "production_changed": False,
        "betting_authorized": False,
        "single_highest_value_next_action": (
            "Freeze the exact survivor before a new untouched prospective window."
            if passed
            else "Use the failed component and comparator attribution to predeclare one source-input improvement; do not retune this candidate."
        ),
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        prediction_bytes = gzip.compress(
            prediction_frame.to_csv(index=False, lineterminator="\n").encode("utf-8"),
            compresslevel=9,
            mtime=0,
        )
        _write_bytes(staging / "oof_predictions.csv.gz", prediction_bytes)
        _write_bytes(staging / "report.json", _json_bytes(report))
        _write_bytes(staging / "decision.json", _json_bytes(decision))
        artifacts = {}
        for name in ("oof_predictions.csv.gz", "report.json", "decision.json"):
            path = staging / name
            artifacts[name] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        manifest = {
            "schema_version": "shared-pa-true-eb-offset-development-artifact-manifest-v1",
            "candidate_id": protocol["candidate"]["id"],
            "status": status,
            "artifacts": artifacts,
            "input_identity": report["input_identity"],
            "required_file_identity": runtime_identity["files"],
            "protected_boundaries": {
                "2024_opened": False,
                "2025_opened": False,
                "may_2026_opened": False,
                "production_changed": False,
                "betting_authorized": False,
            },
        }
        _write_bytes(staging / "manifest.json", _json_bytes(manifest))
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"status": status, "market_pass": decision["market_pass"], "output_dir": str(output_dir)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--panel-manifest", required=True, type=Path)
    parser.add_argument("--panel-certificate", required=True, type=Path)
    parser.add_argument(
        "--protocol-path",
        type=Path,
        default=ROOT / "config/shared_pa_true_eb_offset_development_2023_v1.json",
    )
    parser.add_argument(
        "--execution-lock-path",
        type=Path,
        default=ROOT / "config/shared_pa_true_eb_offset_execution_lock_2023_v1.json",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    result = run(**vars(parser.parse_args()))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
