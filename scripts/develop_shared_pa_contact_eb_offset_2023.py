#!/usr/bin/env python3
"""Run the locked 2023 contact-augmented shared-PA development experiment."""

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

import scripts.develop_shared_pa_true_eb_offset_2023 as base  # noqa: E402
from scripts.validate_shared_pa_contact_quality_panel import validate as validate_contact_panel  # noqa: E402
from src.evaluation.shared_pa_eb_offset_catboost import (  # noqa: E402
    PA_OUTCOMES,
    empirical_bayes_probability,
    fit_eb_offset_catboost,
    fit_frozen_core_catboost,
    league_probability,
)
from src.evaluation.shared_pa_offset_evaluation import (  # noqa: E402
    binary_market_counts,
    binary_market_probability,
    derive_market_probabilities,
    fit_pooled_pa_distribution,
    metrics,
    paired_date_interval,
)
from src.evaluation.shared_pa_contact_quality import FEATURE_COLUMNS  # noqa: E402


ARM_ORDER = (
    "candidate_contact_eb_offset_catboost",
    "rejected_true_eb_offset_control",
    "league_rate",
    "empirical_bayes_mle",
    "empirical_bayes_pa_200",
    "frozen_all_prior_catboost_core",
)
COMPARATOR_ORDER = ARM_ORDER[1:]
COMPONENTS = base.COMPONENTS
IDENTITY_COLUMNS = ["season", "game_date", "game_pk", "player_id"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON root is not an object: {path}")
    return value


def load_protocol(path: Path) -> dict[str, Any]:
    protocol = load_json(path)
    require(
        protocol.get("schema_version") == "shared-pa-contact-eb-offset-development-protocol-v1",
        "unexpected contact development protocol schema",
    )
    require(
        protocol.get("status") == "DESIGN_LOCKED_BEFORE_ANY_CONTACT_CANDIDATE_OUTCOME_SCORING",
        "contact development protocol is not pre-score locked",
    )
    require(protocol["candidate"]["outcomes"] == list(PA_OUTCOMES), "PA outcome order changed")
    require(protocol["data"]["development_years"] == [2023], "protocol permits a non-2023 year")
    require(protocol["markets"]["adjudicate_separately"] is True, "markets are not separate")
    require(protocol["markets"]["market_rescue_forbidden"] is True, "market rescue is permitted")
    require(protocol["pa_opportunity"]["target_game_lineup_slot_consumed"] is False, "target lineup is consumed")
    boundaries = protocol["protected_boundaries"]
    require(boundaries["never_open_2024_for_this_candidate"] is True, "2024 boundary is absent")
    require(boundaries["spent_2025_hr_not_reused"] is True, "spent HR boundary is absent")
    require(boundaries["may_2026_sealed"] is True, "May seal is absent")
    require(boundaries["production_changed"] is False, "protocol claims production change")
    require(boundaries["betting_authorized"] is False, "protocol claims betting authorization")
    return protocol


def verify_execution_lock(lock_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    lock = load_json(lock_path)
    require(
        lock.get("schema_version") == "shared-pa-contact-eb-offset-execution-lock-v1",
        "unexpected contact execution-lock schema",
    )
    require(lock.get("status") == "LOCKED_BEFORE_FIRST_CONTACT_CANDIDATE_OUTCOME_SCORE", "execution is not pre-score locked")
    require(lock.get("development_years") == [2023], "execution lock permits a non-2023 year")
    boundaries = lock.get("protected_boundaries", {})
    require(boundaries.get("may_2026_sealed") is True, "execution lock lacks May seal")
    require(boundaries.get("spent_2024_not_reused") is True, "execution lock lacks spent-2024 boundary")
    require(boundaries.get("spent_2025_hr_not_reused") is True, "execution lock lacks spent-HR boundary")
    require(boundaries.get("production_changed") is False, "execution lock claims production change")
    require(boundaries.get("betting_authorized") is False, "execution lock claims betting authorization")
    files = lock.get("required_file_hashes")
    require(isinstance(files, dict) and files, "execution lock lacks required file hashes")
    checked: dict[str, Any] = {}
    for raw_path, expected in sorted(files.items()):
        path = ROOT / raw_path
        require(path.is_file(), f"locked file is missing: {raw_path}")
        actual = sha256_file(path)
        require(actual == expected, f"locked file hash mismatch: {raw_path}")
        checked[raw_path] = {"bytes": path.stat().st_size, "sha256": actual}
    runtime = lock.get("runtime", {})
    require(sys.version.split()[0] == runtime.get("python_version"), "Python patch version differs from execution lock")
    installed: dict[str, str] = {}
    for name, expected in sorted(runtime.get("distributions", {}).items()):
        actual = importlib.metadata.version(name)
        require(actual == expected, f"installed distribution differs from lock: {name}")
        installed[name] = actual
    return lock, {
        "files": checked,
        "python_version": sys.version.split()[0],
        "distributions": installed,
    }


def feature_contracts(protocol: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    sources = protocol["feature_sources"]
    base_path = ROOT / sources["base_residual"]["path"]
    contact_path = ROOT / sources["contact_contract"]["path"]
    registry_path = ROOT / sources["contact_registry"]["path"]
    for path, expected in (
        (base_path, sources["base_residual"]["sha256"]),
        (contact_path, sources["contact_contract"]["sha256"]),
        (registry_path, sources["contact_registry"]["sha256"]),
    ):
        require(path.is_file() and sha256_file(path) == expected, f"feature-source identity mismatch: {path.name}")
    base_protocol = base.load_protocol(base_path)
    require(
        protocol["candidate"]["residual_model"] == base_protocol["candidate"]["residual_model"],
        "candidate residual parameters differ from the frozen control",
    )
    contact_contract = load_json(contact_path)
    require(contact_contract.get("status") == "LOCKED_BEFORE_2023_FEATURE_PANEL_BUILD", "contact contract is not locked")
    require(contact_contract.get("features") == list(FEATURE_COLUMNS), "contact feature contract changed")
    registry = load_json(registry_path)
    require(registry.get("status") == "HASH_BOUND_2023_RESEARCH_INPUT_NOT_A_MODEL_OR_PROMOTION", "contact registry status changed")
    require(registry["qualification"]["research_input_ready"] is True, "contact input is not research ready")
    require(registry["qualification"]["model_fitted"] is False, "contact registry unexpectedly claims a fitted model")
    chronology_field = sources["contact_contract"]["chronology_certificate_field"]
    require(chronology_field == "history_contact_max_source_date", "contact chronology field changed")
    contact_model_features = [name for name in contact_contract["features"] if name != chronology_field]
    require(
        len(contact_contract["features"]) == sources["contact_contract"]["panel_field_count"],
        "contact panel field count changed",
    )
    require(
        len(contact_model_features) == sources["contact_contract"]["numeric_model_feature_count"],
        "contact numeric feature count changed",
    )
    features = [*base_protocol["candidate"]["features"], *contact_model_features]
    require(len(features) == len(set(features)), "candidate feature contracts overlap")
    return base_protocol, registry, features


def load_contact_input(
    *,
    panel: Path,
    manifest: Path,
    certificate: Path,
    protocol: Mapping[str, Any],
) -> pd.DataFrame:
    data = protocol["data"]
    for path, expected in (
        (panel, data["contact_panel_sha256"]),
        (manifest, data["contact_manifest_sha256"]),
        (certificate, data["contact_certificate_sha256"]),
    ):
        require(path.is_file() and sha256_file(path) == expected, f"contact artifact identity mismatch: {path.name}")
    require(panel.parent == manifest.parent == certificate.parent, "contact artifacts do not share one authority")
    result = validate_contact_panel(output_dir=panel.parent)
    require(result["panel"]["sha256"] == data["contact_panel_sha256"], "contact validator bound a different panel")
    frame = pd.read_csv(panel, low_memory=False)
    require(list(frame.columns) == [*IDENTITY_COLUMNS, *FEATURE_COLUMNS], "contact panel surface changed")
    require(len(frame) == data["physical_rows"], "contact panel row count changed")
    require(frame[IDENTITY_COLUMNS].notna().all().all(), "contact identity is missing")
    require(not frame.duplicated(IDENTITY_COLUMNS).any(), "contact identity is duplicated")
    years = pd.to_numeric(frame["season"], errors="coerce")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    source = pd.to_datetime(frame["history_contact_max_source_date"], format="%Y-%m-%d", errors="coerce")
    require(years.notna().all() and set(years.astype(int)) == {2023}, "contact input is not exclusively 2023")
    require(dates.notna().all() and dates.dt.year.eq(2023).all(), "contact target date is invalid")
    require(not (source.notna() & source.ge(dates)).any(), "contact input is not strictly prior")
    require(not any(name.startswith("out_") or name.startswith("target_") for name in frame), "contact input contains outcomes")
    return frame


def join_contact(base_frame: pd.DataFrame, contact_frame: pd.DataFrame) -> pd.DataFrame:
    output = base_frame.merge(
        contact_frame,
        how="left",
        on=IDENTITY_COLUMNS,
        validate="one_to_one",
        indicator=True,
        sort=False,
    )
    require(len(output) == len(base_frame), "contact join changed fit-eligible coverage")
    require(output["_merge"].eq("both").all(), "fit-eligible base row lacks contact input")
    output = output.drop(columns="_merge")
    require(not output.columns.duplicated().any(), "contact join created duplicate columns")
    return output


def evaluate_components(
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
            counts, candidate_probability = base._component_values(
                selection, pa_by_arm[ARM_ORDER[0]], market_by_arm[ARM_ORDER[0]], name
            )
            candidate_score = metrics(counts, candidate_probability)
            row: dict[str, Any] = {"candidate": candidate_score, "comparators": {}, "passed": True}
            for comparator_name in COMPARATOR_ORDER:
                _, comparator_probability = base._component_values(
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
                aggregate_auc = base._auc_noninferior(candidate_score, comparator_score)
                fold_auc = True
                fold_details: list[dict[str, Any]] = []
                for fold_number, rows in enumerate(fold_rows, start=1):
                    fold_counts = counts[rows]
                    fold_candidate = metrics(fold_counts, candidate_probability[rows])
                    fold_comparator = metrics(fold_counts, comparator_probability[rows])
                    passed = base._auc_noninferior(fold_candidate, fold_comparator)
                    fold_details.append(
                        {
                            "fold": fold_number,
                            "candidate_auc": fold_candidate["auc"],
                            "comparator_auc": fold_comparator["auc"],
                            "passed": bool(passed),
                        }
                    )
                    fold_auc = fold_auc and passed
                calibration = base._calibration_pass(candidate_score, comparator_score)
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


def hr_tail_gate(
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


def run(
    *,
    panel: Path,
    panel_manifest: Path,
    panel_certificate: Path,
    contact_panel: Path,
    contact_manifest: Path,
    contact_certificate: Path,
    protocol_path: Path,
    execution_lock_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    require(not output_dir.exists(), "refusing to overwrite an existing experiment output directory")
    lock, runtime_identity = verify_execution_lock(execution_lock_path)
    protocol = load_protocol(protocol_path)
    protocol_key = str(protocol_path.relative_to(ROOT)).replace("\\", "/")
    require(sha256_file(protocol_path) == lock["required_file_hashes"][protocol_key], "protocol is not execution locked")
    base_protocol, contact_registry, candidate_features = feature_contracts(protocol)
    frame, panel_identity = base._validate_panel_inputs(
        panel_path=panel,
        manifest_path=panel_manifest,
        certificate_path=panel_certificate,
        protocol=base_protocol,
        lock=lock,
    )
    contact = load_contact_input(
        panel=contact_panel,
        manifest=contact_manifest,
        certificate=contact_certificate,
        protocol=protocol,
    )
    frame = join_contact(frame, contact)
    require(
        base.canonical_sha256(candidate_features) == lock["candidate_feature_identity"]["ordered_names_sha256"],
        "candidate feature identity changed",
    )
    require(len(candidate_features) == lock["candidate_feature_identity"]["count"], "candidate feature count changed")
    control_features = base_protocol["candidate"]["features"]
    parameters = protocol["candidate"]["residual_model"]
    split_rows = base.chronological_splits(frame, protocol["chronological_evaluation"]["outer_folds"])
    arrays = {arm: np.full((len(frame), len(PA_OUTCOMES)), np.nan) for arm in ARM_ORDER}
    market_arrays: dict[str, dict[str, np.ndarray]] = {arm: {} for arm in ARM_ORDER}
    evaluated = np.zeros(len(frame), dtype=bool)
    fold_records: list[dict[str, Any]] = []
    fold_selection_rows: list[np.ndarray] = []
    for fold_number, (train_rows, validation_rows) in enumerate(split_rows, start=1):
        require(not evaluated[validation_rows].any(), "outer validation rows overlap")
        train = frame.iloc[train_rows]
        validation = frame.iloc[validation_rows]
        candidate_model = fit_eb_offset_catboost(train, features=candidate_features, locked_parameters=parameters)
        control_model = fit_eb_offset_catboost(train, features=control_features, locked_parameters=parameters)
        require(
            np.isclose(candidate_model.concentration, control_model.concentration, rtol=0.0, atol=1e-10),
            "candidate and control empirical-Bayes concentrations differ",
        )
        core_model = fit_frozen_core_catboost(
            train,
            features=panel_identity["core_features"],
            locked_parameters=parameters,
        )
        league = league_probability(train)
        pa_probability = {
            ARM_ORDER[0]: candidate_model.predict_proba(validation),
            "rejected_true_eb_offset_control": control_model.predict_proba(validation),
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
                market_arrays[arm].setdefault(name, np.full(len(frame), np.nan))
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
    components, component_gates = evaluate_components(
        selection=selection,
        pa_by_arm=pa_selected,
        market_by_arm=market_selected,
        fold_rows=fold_selection_rows,
        draws=protocol["chronological_evaluation"]["bootstrap_draws"],
        seed=protocol["chronological_evaluation"]["bootstrap_seed"],
    )
    hr_tail = hr_tail_gate(
        selection=selection,
        market_by_arm=market_selected,
        draws=protocol["chronological_evaluation"]["bootstrap_draws"],
        seed=protocol["chronological_evaluation"]["bootstrap_seed"],
    )
    passed = component_gates and bool(hr_tail["passed"])
    status = (
        "2023_CONTACT_DEVELOPMENT_SURVIVOR_REQUIRES_NEW_UNTOUCHED_PROSPECTIVE_CONFIRMATION"
        if passed
        else "2023_CONTACT_DEVELOPMENT_REJECTED_NO_CANDIDATE"
    )
    prediction_frame = selection[IDENTITY_COLUMNS].copy()
    for arm in ARM_ORDER:
        for index, outcome in enumerate(PA_OUTCOMES):
            prediction_frame[f"{arm}__pa_{outcome}"] = pa_selected[arm][:, index]
        for name in sorted(market_selected[arm]):
            prediction_frame[f"{arm}__{name}"] = market_selected[arm][name]
    input_identity = {
        "base_panel": {"path": str(panel), "sha256": sha256_file(panel)},
        "base_panel_manifest": {"path": str(panel_manifest), "sha256": sha256_file(panel_manifest)},
        "base_panel_certificate": {"path": str(panel_certificate), "sha256": sha256_file(panel_certificate)},
        "contact_panel": {"path": str(contact_panel), "sha256": sha256_file(contact_panel)},
        "contact_manifest": {"path": str(contact_manifest), "sha256": sha256_file(contact_manifest)},
        "contact_certificate": {"path": str(contact_certificate), "sha256": sha256_file(contact_certificate)},
        "protocol": {"path": str(protocol_path), "sha256": sha256_file(protocol_path)},
        "execution_lock": {"path": str(execution_lock_path), "sha256": sha256_file(execution_lock_path)},
    }
    report = {
        "schema_version": "shared-pa-contact-eb-offset-development-report-v1",
        "candidate_id": protocol["candidate"]["id"],
        "status": status,
        "scope": "2023_ONLY_RESEARCH_DEVELOPMENT_NOT_SELECTION_CONFIRMATION_OR_MARKET_QUALIFICATION",
        "input_identity": input_identity,
        "runtime_identity": runtime_identity,
        "panel_identity": {
            **panel_identity,
            "contact_rows": int(len(contact)),
            "contact_features": len(FEATURE_COLUMNS),
            "candidate_features": len(candidate_features),
            "contact_registry_status": contact_registry["status"],
        },
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
            "The 2023 parent-lineage window is development evidence only and is not independent selection or confirmation.",
            "The historical game-market screen uses one pooled outer-training PA distribution because target-game lineup availability is not receipt-proven.",
            "This is not executable-price, settlement, ROI, promotion, activation, or betting evidence.",
            "A survivor must be frozen before a genuinely future untouched confirmation window begins.",
        ],
        "production_changed": False,
        "betting_authorized": False,
    }
    decision = {
        "schema_version": "shared-pa-contact-eb-offset-development-decision-v1",
        "candidate_id": protocol["candidate"]["id"],
        "decision": status,
        "market_pass": {name: bool(value["passed"]) for name, value in components.items()},
        "hr_tail_pass": bool(hr_tail["passed"]),
        "selection_or_confirmation_opened": False,
        "production_changed": False,
        "betting_authorized": False,
        "single_highest_value_next_action": (
            "Freeze the exact 2023 survivor before a genuinely future untouched confirmation window."
            if passed
            else "Retain the fitted empirical-Bayes foundation and use comparator/component attribution to identify one new source-input limiter; do not retune this candidate."
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
        base._write_bytes(staging / "oof_predictions.csv.gz", prediction_bytes)
        base._write_bytes(staging / "report.json", base._json_bytes(report))
        base._write_bytes(staging / "decision.json", base._json_bytes(decision))
        artifacts = {}
        for name in ("oof_predictions.csv.gz", "report.json", "decision.json"):
            path = staging / name
            artifacts[name] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        manifest_value = {
            "schema_version": "shared-pa-contact-eb-offset-development-artifact-manifest-v1",
            "candidate_id": protocol["candidate"]["id"],
            "status": status,
            "artifacts": artifacts,
            "input_identity": input_identity,
            "required_file_identity": runtime_identity["files"],
            "protected_boundaries": {
                "2024_opened": False,
                "2025_opened": False,
                "may_2026_opened": False,
                "production_changed": False,
                "betting_authorized": False,
            },
        }
        base._write_bytes(staging / "manifest.json", base._json_bytes(manifest_value))
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
    parser.add_argument("--contact-panel", required=True, type=Path)
    parser.add_argument("--contact-manifest", required=True, type=Path)
    parser.add_argument("--contact-certificate", required=True, type=Path)
    parser.add_argument(
        "--protocol-path",
        type=Path,
        default=ROOT / "config/shared_pa_contact_eb_offset_development_2023_v1.json",
    )
    parser.add_argument(
        "--execution-lock-path",
        type=Path,
        default=ROOT / "config/shared_pa_contact_eb_offset_execution_lock_2023_v1.json",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    result = run(**vars(parser.parse_args()))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
