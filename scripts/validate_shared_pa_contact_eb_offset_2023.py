#!/usr/bin/env python3
"""Validate the immutable output of the locked 2023 contact experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ARMS = (
    "candidate_contact_eb_offset_catboost",
    "rejected_true_eb_offset_control",
    "league_rate",
    "empirical_bayes_mle",
    "empirical_bayes_pa_200",
    "frozen_all_prior_catboost_core",
)
OUTCOMES = (
    "strikeout",
    "walk",
    "single",
    "double",
    "triple",
    "home_run",
    "bip_out",
    "other_non_ab",
)
MARKETS = (
    "hits_over_0_5",
    "hits_over_1_5",
    "home_runs_over_0_5",
    "total_bases_over_0_5",
    "total_bases_over_1_5",
    "total_bases_over_2_5",
    "total_bases_over_3_5",
    "total_bases_over_4_5",
    "total_bases_over_5_5",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON root is not an object: {path.name}")
    return value


def validate(output_dir: Path) -> dict[str, Any]:
    require(output_dir.is_dir(), "experiment output directory is missing")
    expected_names = {"decision.json", "manifest.json", "oof_predictions.csv.gz", "report.json"}
    actual_names = {path.name for path in output_dir.iterdir() if path.is_file()}
    require(actual_names == expected_names, "experiment output file surface changed")
    manifest = load_json(output_dir / "manifest.json")
    report = load_json(output_dir / "report.json")
    decision = load_json(output_dir / "decision.json")
    require(
        manifest.get("schema_version")
        == "shared-pa-contact-eb-offset-development-artifact-manifest-v1",
        "artifact manifest schema changed",
    )
    artifacts = manifest.get("artifacts")
    require(
        isinstance(artifacts, dict) and set(artifacts) == expected_names - {"manifest.json"},
        "artifact bindings changed",
    )
    for name, identity in artifacts.items():
        path = output_dir / name
        require(identity.get("bytes") == path.stat().st_size, f"artifact size mismatch: {name}")
        require(identity.get("sha256") == sha256_file(path), f"artifact hash mismatch: {name}")
    require(
        report.get("schema_version") == "shared-pa-contact-eb-offset-development-report-v1",
        "report schema changed",
    )
    require(
        decision.get("schema_version") == "shared-pa-contact-eb-offset-development-decision-v1",
        "decision schema changed",
    )
    statuses = {manifest.get("status"), report.get("status"), decision.get("decision")}
    require(len(statuses) == 1, "artifact statuses disagree")
    status = statuses.pop()
    market_pass = {
        market: bool(report["components"][market]["passed"])
        for market in ("hits", "hr_over_0_5", "total_bases")
    }
    require(decision.get("market_pass") == market_pass, "decision market states differ from report")
    all_passed = all(market_pass.values()) and bool(report["hr_high_probability_tail"]["passed"])
    expected_status = (
        "2023_CONTACT_DEVELOPMENT_SURVIVOR_REQUIRES_NEW_UNTOUCHED_PROSPECTIVE_CONFIRMATION"
        if all_passed
        else "2023_CONTACT_DEVELOPMENT_REJECTED_NO_CANDIDATE"
    )
    require(status == expected_status, "status does not follow the predeclared gates")
    require(
        report["gates"]["all_predeclared_development_gates_passed"] is all_passed,
        "summary gate differs from component gates",
    )
    boundaries = manifest.get("protected_boundaries", {})
    require(
        boundaries
        == {
            "2024_opened": False,
            "2025_opened": False,
            "may_2026_opened": False,
            "production_changed": False,
            "betting_authorized": False,
        },
        "protected artifact boundary changed",
    )
    frame = pd.read_csv(output_dir / "oof_predictions.csv.gz", low_memory=False)
    require(len(frame) == report["chronology"]["oof_eligible_rows"], "prediction row count changed")
    identity = ["season", "game_date", "game_pk", "player_id"]
    require(
        frame[identity].notna().all().all() and not frame.duplicated(identity).any(),
        "prediction identity is invalid",
    )
    years = pd.to_numeric(frame["season"], errors="coerce")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    require(years.notna().all() and set(years.astype(int)) == {2023}, "prediction year is not 2023")
    require(dates.notna().all() and dates.dt.year.eq(2023).all(), "prediction date is not 2023")
    require(not any("lineup" in column.lower() for column in frame), "unreceipted lineup data entered predictions")
    for arm in ARMS:
        pa_columns = [f"{arm}__pa_{outcome}" for outcome in OUTCOMES]
        market_columns = [f"{arm}__{market}" for market in MARKETS]
        require(set(pa_columns + market_columns).issubset(frame), f"prediction columns missing for {arm}")
        pa = frame[pa_columns].to_numpy(float)
        market = frame[market_columns].to_numpy(float)
        require(
            np.isfinite(pa).all() and (pa > 0.0).all() and (pa < 1.0).all(),
            f"PA probability is invalid for {arm}",
        )
        require(
            np.allclose(pa.sum(axis=1), 1.0, rtol=0.0, atol=1e-9),
            f"PA probability does not sum to one for {arm}",
        )
        require(
            np.isfinite(market).all() and (market >= 0.0).all() and (market <= 1.0).all(),
            f"market probability is invalid for {arm}",
        )
    return {
        "status": status,
        "rows": int(len(frame)),
        "market_pass": market_pass,
        "artifact_hashes": {
            name: sha256_file(output_dir / name) for name in sorted(expected_names)
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    print(json.dumps(validate(parser.parse_args().output_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
