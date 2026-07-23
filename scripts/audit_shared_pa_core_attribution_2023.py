#!/usr/bin/env python3
"""Locked 2023-only source-to-feature ablation audit for the PA core."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts.develop_shared_pa_eb_residual_2023 import (  # noqa: E402
    _calibration, _component, _component_interval, _date_splits,
    _oof_coverage_summary, _scores, atomic, sha256_file,
)
from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.learning.shared_pa_model import fit_catboost  # noqa: E402


def _require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def _load_protocol(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    _require(contract.get("schema_version") == "shared-pa-core-attribution-protocol-v1", "unexpected core attribution protocol schema")
    _require(contract.get("status") == "LOCKED_BEFORE_2023_ATTRIBUTION", "core attribution protocol is not locked")
    _require(contract.get("research_only") is True and contract.get("betting_authorized") is False and contract.get("production_changed") is False, "attribution protocol must remain research-only")
    groups = contract.get("feature_groups")
    _require(isinstance(groups, dict) and list(groups) == ["outcome_history", "pitch_discipline", "batted_ball", "pitch_shape"], "feature-group identity changed")
    flattened = [feature for group in groups.values() for feature in group]
    _require(flattened and len(flattened) == len(set(flattened)), "feature groups overlap or are empty")
    _require(contract.get("comparisons") == ["full_core", "full_core_minus_outcome_history", "full_core_minus_pitch_discipline", "full_core_minus_batted_ball", "full_core_minus_pitch_shape"], "ablation comparison set changed")
    _require(contract.get("metrics", {}).get("separate_components") == ["hits", "hr_over_0_5", "total_bases"], "market separation changed")
    return contract


def _validate_panel(panel: Path, manifest: Path, contract: dict[str, Any]) -> pd.DataFrame:
    boundary = contract["evidence_boundary"]
    _require(str(panel) == boundary["external_panel"] and str(manifest) == boundary["external_manifest"], "external source path changed")
    _require(sha256_file(panel) == boundary["external_panel_sha256"] and sha256_file(manifest) == boundary["external_manifest_sha256"], "external source hash changed")
    source_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    _require(source_manifest.get("status") == "CERTIFIED_2023_ONLY_DEVELOPMENT_RESEARCH_ONLY", "source panel is not certified 2023-only")
    _require(source_manifest.get("validation", {}).get("years") == [2023] and source_manifest.get("validation", {}).get("2024_rows_parsed") == 0 and source_manifest.get("validation", {}).get("2025_confirmation_opened") is False and source_manifest.get("validation", {}).get("may_2026_opened") is False, "source certificate violates sealed evidence boundary")
    frame = pd.read_csv(panel, low_memory=False)
    _require(not frame.columns.duplicated().any(), "panel has duplicate labels")
    core = [column for column in frame.columns if column.startswith("history_") and "age_days" not in column]
    declared = [feature for group in contract["feature_groups"].values() for feature in group]
    _require(set(core) == set(declared), "declared source groups do not exactly partition the fixed core")
    required = {"season", "game_date", "game_pk", "player_id", "max_source_date", "out_pa", "out_ab", "out_hits", "out_doubles", "out_triples", "out_hr", "out_bb", "out_k", *[f"target_{outcome}" for outcome in PA_OUTCOMES]}
    _require(not required.difference(frame.columns), "panel lacks required identity/outcome columns")
    years = pd.to_numeric(frame["season"], errors="coerce")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    sources = pd.to_datetime(frame["max_source_date"], format="%Y-%m-%d", errors="coerce")
    _require(years.notna().all() and set(years.astype(int)) == {2023} and dates.notna().all() and (dates.dt.year == 2023).all(), "panel is not exclusive to 2023")
    _require(not (sources.notna() & sources.ge(dates)).any(), "strict-prior cutoff violated")
    _require(frame[["game_pk", "player_id"]].notna().all().all() and not frame.duplicated(["game_pk", "player_id"]).any(), "panel identity is incomplete or duplicated")
    targets = frame[[f"target_{outcome}" for outcome in PA_OUTCOMES]].rename(columns={f"target_{outcome}": outcome for outcome in PA_OUTCOMES}).astype(int)
    _require(outcome_counts(frame).loc[:, PA_OUTCOMES].astype(int).equals(targets), "consumer outcome accounting differs from raw terminal truth")
    return frame.loc[outcome_counts(frame).sum(axis=1).gt(0)].reset_index(drop=True)


def _feature_sets(frame: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, list[str]]:
    full = [column for column in frame.columns if column.startswith("history_") and "age_days" not in column]
    sets = {"full_core": full}
    for group, features in protocol["feature_groups"].items():
        remaining = [feature for feature in full if feature not in features]
        _require(len(remaining) + len(features) == len(full), f"group removal does not preserve feature cardinality: {group}")
        sets[f"full_core_minus_{group}"] = remaining
    return sets


def run(*, panel: Path, manifest: Path, protocol: Path, report: Path, predictions: Path) -> dict[str, Any]:
    if report.exists() or predictions.exists():
        raise FileExistsError("refusing to overwrite core attribution evidence")
    contract = _load_protocol(protocol)
    frame = _validate_panel(panel, manifest, contract)
    feature_sets = _feature_sets(frame, contract)
    seed = int(contract["fixed_model"]["seed"])
    outer = _date_splits(frame, int(contract["chronology"]["n_splits"]))
    oof = {name: np.full((len(frame), len(PA_OUTCOMES)), np.nan, dtype=float) for name in feature_sets}
    folds: list[dict[str, Any]] = []
    for fold_number, (train_rows, validation_rows) in enumerate(outer, start=1):
        train, validation = frame.iloc[train_rows], frame.iloc[validation_rows]
        fold = {"fold": fold_number, "train_date_min": train["game_date"].min(), "train_date_max": train["game_date"].max(), "validation_date_min": validation["game_date"].min(), "validation_date_max": validation["game_date"].max(), "train_rows": int(len(train)), "validation_rows": int(len(validation)), "models": {}}
        for name, features in feature_sets.items():
            model = fit_catboost(train, features=features, params=contract["fixed_model"]["params"], seed=seed)
            probability = model.predict_proba(validation)
            oof[name][validation_rows] = probability
            model_components: dict[str, Any] = {}
            for component in contract["metrics"]["separate_components"]:
                counts, probabilities = _component(validation, probability, component)
                model_components[component] = _scores(counts, probabilities)
            fold["models"][name] = model_components
        folds.append(fold)
    evaluated = np.isfinite(oof["full_core"]).all(axis=1)
    coverage = _oof_coverage_summary(total_rows=len(frame), outer_splits=outer, evaluated=evaluated)
    for name, probability in oof.items():
        _require(np.array_equal(np.isfinite(probability).all(axis=1), evaluated), f"OOF coverage differs for ablation {name}")
    selected = frame.loc[evaluated].reset_index(drop=True)
    components: dict[str, Any] = {}
    for component in contract["metrics"]["separate_components"]:
        counts, full_probability = _component(selected, oof["full_core"][evaluated], component)
        row: dict[str, Any] = {"full_core": {"scores": _scores(counts, full_probability), "calibration": _calibration(counts, full_probability)}, "ablations": {}}
        for name in [item for item in feature_sets if item != "full_core"]:
            _, ablated_probability = _component(selected, oof[name][evaluated], component)
            interval = _component_interval(counts, ablated_probability, full_probability, selected["game_date"], draws=int(contract["chronology"]["bootstrap_draws"]), seed=int(contract["chronology"]["seed"]))
            direction = {metric: bool(interval[metric]["point"] > 0.0 and interval[metric]["lower"] > 0.0) for metric in ("brier", "log_loss")}
            auc_full = row["full_core"]["scores"]["auc"]
            auc_ablated = _scores(counts, ablated_probability)["auc"]
            fold_auc_loss = all(fold["models"][name][component]["auc"] < fold["models"]["full_core"][component]["auc"] for fold in folds)
            row["ablations"][name] = {"scores": _scores(counts, ablated_probability), "calibration": _calibration(counts, ablated_probability), "ablated_minus_full_interval": interval, "proper_score_loss_direction_consistent": direction, "auc_loss_aggregate": bool(auc_ablated < auc_full), "auc_loss_every_fold": fold_auc_loss, "interpretation": "Positive ablated-minus-full loss means this block improved the fixed core on the shared chronological OOF universe; this is source attribution, not causal proof or a candidate selection."}
        components[component] = row
    prediction_frame = selected[["game_pk", "player_id", "game_date"]].copy()
    for name, probability in oof.items():
        for index, outcome in enumerate(PA_OUTCOMES):
            prediction_frame[f"{name}_{outcome}"] = probability[evaluated, index]
    atomic(predictions, prediction_frame.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    result = {"schema_version": "shared-pa-core-attribution-report-v1", "status": "ATTRIBUTION_COMPLETE_NO_CANDIDATE_OR_PROMOTION", "inputs": {"panel": {"path": str(panel), "sha256": sha256_file(panel)}, "manifest": {"path": str(manifest), "sha256": sha256_file(manifest)}, "protocol": {"path": str(protocol), "sha256": sha256_file(protocol)}}, "chronology": {"development_years": [2023], "2024_opened": False, "2025_opened": False, "may_2026_opened": False, "outer_folds": len(folds), "all_predictions_out_of_fold": True, "realized_pa_used_as_feature": False}, "population": {**coverage, "identity_key": ["game_pk", "player_id"]}, "feature_sets": feature_sets, "folds": folds, "components": components, "predictions": {"path": str(predictions), "sha256": sha256_file(predictions), "rows": int(len(prediction_frame))}, "identity_hashes": {"code": {"scripts/audit_shared_pa_core_attribution_2023.py": sha256_file(Path(__file__)), "src/learning/shared_pa_model.py": sha256_file(ROOT / "src/learning/shared_pa_model.py"), "src/evaluation/shared_pa_training_data.py": sha256_file(ROOT / "src/evaluation/shared_pa_training_data.py")}, "tests": {"tests/test_audit_shared_pa_core_attribution_2023.py": sha256_file(ROOT / "tests/test_audit_shared_pa_core_attribution_2023.py")}}, "next_action": "Interpret only after reviewing all separated market components. This diagnostic cannot itself create a candidate, change a coefficient, or open later-year evidence.", "production_changed": False, "betting_authorized": False}
    atomic(report, (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--protocol", type=Path, default=ROOT / "config/shared_pa_core_attribution_2023_v1.json")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    result = run(**vars(parser.parse_args()))
    print(json.dumps({"status": result["status"], "rows": result["population"]["oof_scored_rows"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
