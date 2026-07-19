"""Executable gates for canonical shared-PA model selection."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from src.evaluation.shared_pa_training_data import OUTCOME_COLUMNS


IDENTITY = ["game_pk", "player_id"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_protocol(path: Path, *, evidence_root: Path, code_root: Path) -> dict[str, Any]:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if protocol.get("status") != "LOCKED_BEFORE_2023_2024_CANONICAL_SELECTION":
        raise ValueError("canonical selection protocol is not locked")
    if not protocol.get("confirmation_2025_forbidden_during_selection"):
        raise ValueError("canonical selection does not protect 2025")
    if not protocol.get("may_2026_forbidden"):
        raise ValueError("canonical selection does not protect May")
    for name in ("official_outcomes", "canonical_features", "canonical_manifest", "canonical_certificate", "pa_distribution"):
        record = protocol["inputs"][name]
        artifact = evidence_root / record["path"]
        if not artifact.exists() or sha256(artifact) != record["sha256"]:
            raise ValueError(f"canonical selection input hash changed: {name}")
    runtime_record = protocol["inputs"]["runtime_feature_contract"]
    runtime_contract = code_root / runtime_record["path"]
    if not runtime_contract.exists() or sha256(runtime_contract) != runtime_record["sha256"]:
        raise ValueError("canonical runtime feature contract hash changed")
    certificate_record = protocol["inputs"]["canonical_certificate"]
    certificate = json.loads((evidence_root / certificate_record["path"]).read_text(encoding="utf-8"))
    if certificate.get("status") != certificate_record["required_status"]:
        raise ValueError("canonical feature certificate status changed")
    if certificate.get("confirmation_2025_read") or certificate.get("may_2026_read"):
        raise ValueError("canonical feature certificate crossed protected evidence")
    if certificate.get("artifact", {}).get("sha256") != protocol["inputs"]["canonical_features"]["sha256"]:
        raise ValueError("canonical certificate artifact binding changed")
    return protocol


def feature_columns(protocol: dict[str, Any], variant_id: str) -> list[str]:
    variants = {item["id"]: item for item in protocol["feature_variants"]}
    if variant_id not in variants:
        raise ValueError(f"unknown canonical feature variant: {variant_id}")
    columns: list[str] = []
    for group in variants[variant_id]["groups"]:
        for column in protocol["feature_groups"][group]:
            if column not in columns:
                columns.append(column)
    overlap = set(columns) & set(protocol["forbidden_classifier_features"])
    if overlap:
        raise ValueError(f"forbidden canonical classifier features selected: {sorted(overlap)}")
    if not protocol["opposing_pitcher_quarantine"]["classifier_allowed"]:
        pitcher = [column for column in columns if column.startswith("opp_sp_")]
        if pitcher:
            raise ValueError(f"quarantined opposing-pitcher features selected: {pitcher}")
    return columns


def load_selection_frame(protocol: dict[str, Any], *, evidence_root: Path) -> pd.DataFrame:
    outcome_record = protocol["inputs"]["official_outcomes"]
    outcomes = pd.read_csv(
        evidence_root / outcome_record["path"],
        nrows=int(outcome_record["maximum_rows_read"]),
        low_memory=False,
    )
    features = pd.read_csv(evidence_root / protocol["inputs"]["canonical_features"]["path"], low_memory=False)
    if len(outcomes) != int(outcome_record["maximum_rows_read"]):
        raise ValueError("canonical selection outcome prefix length changed")
    if len(features) != int(protocol["inputs"]["canonical_features"]["rows"]):
        raise ValueError("canonical selection feature length changed")
    if set(outcomes["season"].astype(int)) != {2023, 2024} or set(features["season"].astype(int)) != {2023, 2024}:
        raise ValueError("canonical selection crossed into 2025")
    if outcomes.duplicated(IDENTITY).any() or features.duplicated(IDENTITY).any():
        raise ValueError("canonical selection input identity is duplicated")
    retained_outcomes = outcomes[[
        "season", "game_date", *IDENTITY, "lineup_slot", *OUTCOME_COLUMNS,
    ]].copy()
    merged = retained_outcomes.merge(
        features,
        on=IDENTITY,
        how="inner",
        validate="one_to_one",
        suffixes=("_outcome", "_feature"),
    )
    if len(merged) != len(outcomes):
        raise ValueError("canonical outcome/feature identity coverage differs")
    for column in ("season", "game_date", "lineup_slot"):
        left, right = f"{column}_outcome", f"{column}_feature"
        if not merged[left].astype(str).equals(merged[right].astype(str)):
            raise ValueError(f"canonical outcome/feature {column} differs")
        merged[column] = merged[left]
        merged.drop(columns=[left, right], inplace=True)
    if set(pd.to_datetime(merged["game_date"], format="%Y-%m-%d", errors="raise").dt.year) != {2023, 2024}:
        raise ValueError("canonical merged frame crossed into confirmation")
    for variant in protocol["feature_variants"]:
        missing = sorted(set(feature_columns(protocol, variant["id"])) - set(merged.columns))
        if missing:
            raise ValueError(f"canonical selection features missing: {missing}")
    return merged.sort_values(["game_date", "game_pk", "player_id"]).reset_index(drop=True)


def complexity_benefit_floor(simple_report: dict[str, Any]) -> dict[str, float]:
    selected = simple_report["selected"]
    league = simple_report["league_rate"]["scores"]
    strongest = simple_report[selected]["scores"]
    return {
        metric: max(0.0, float(league[metric]) - float(strongest[metric]))
        for metric in ("multiclass_log_loss", "multiclass_brier")
    }


def clears_materiality(
    intervals: dict[str, dict[str, float]],
    floor: dict[str, float],
) -> tuple[bool, dict[str, Any]]:
    decisions: dict[str, Any] = {}
    for metric in ("multiclass_log_loss", "multiclass_brier"):
        threshold = -float(floor[metric])
        interval = intervals[metric]
        passed = float(interval["point"]) < threshold and float(interval["upper"]) < threshold
        decisions[metric] = {
            "required_candidate_minus_simple_below": threshold,
            "point": float(interval["point"]),
            "upper": float(interval["upper"]),
            "passed": bool(passed),
        }
    return all(item["passed"] for item in decisions.values()), decisions
