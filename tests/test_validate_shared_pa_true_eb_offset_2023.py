from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.validate_shared_pa_true_eb_offset_2023 import validate


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _fixture(root: Path) -> Path:
    root.mkdir()
    market_pass = {"hits": False, "hr_over_0_5": False, "total_bases": False}
    report = {
        "status": "2023_DEVELOPMENT_REJECTED_NO_CANDIDATE",
        "chronology": {"oof_eligible_rows": 1},
        "components": {name: {"passed": passed} for name, passed in market_pass.items()},
        "hr_high_probability_tail": {"passed": False},
        "gates": {"all_predeclared_development_gates_passed": False},
    }
    decision = {
        "decision": "2023_DEVELOPMENT_REJECTED_NO_CANDIDATE",
        "market_pass": market_pass,
    }
    _write_json(root / "report.json", report)
    _write_json(root / "decision.json", decision)
    row = {"season": 2023, "game_date": "2023-04-01", "game_pk": 1, "player_id": 2}
    outcomes = ("strikeout", "walk", "single", "double", "triple", "home_run", "bip_out", "other_non_ab")
    markets = (
        "hits_over_0_5", "hits_over_1_5", "home_runs_over_0_5",
        "total_bases_over_0_5", "total_bases_over_1_5", "total_bases_over_2_5",
        "total_bases_over_3_5", "total_bases_over_4_5", "total_bases_over_5_5",
    )
    arms = (
        "candidate_true_eb_offset_catboost", "league_rate", "empirical_bayes_mle",
        "empirical_bayes_pa_200", "frozen_all_prior_catboost_core",
    )
    for arm in arms:
        for outcome in outcomes:
            row[f"{arm}__pa_{outcome}"] = 0.125
        for market in markets:
            row[f"{arm}__{market}"] = 0.5
    payload = pd.DataFrame([row]).to_csv(index=False, lineterminator="\n").encode()
    (root / "oof_predictions.csv.gz").write_bytes(gzip.compress(payload, mtime=0))
    artifacts = {}
    for name in ("report.json", "decision.json", "oof_predictions.csv.gz"):
        path = root / name
        artifacts[name] = {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    _write_json(
        root / "manifest.json",
        {
            "schema_version": "shared-pa-true-eb-offset-development-artifact-manifest-v1",
            "status": "2023_DEVELOPMENT_REJECTED_NO_CANDIDATE",
            "artifacts": artifacts,
            "protected_boundaries": {
                "2024_opened": False,
                "2025_opened": False,
                "may_2026_opened": False,
                "production_changed": False,
                "betting_authorized": False,
            },
        },
    )
    return root


def test_valid_rejected_artifact_surface_passes(tmp_path: Path) -> None:
    result = validate(_fixture(tmp_path / "artifact"))
    assert result["status"] == "2023_DEVELOPMENT_REJECTED_NO_CANDIDATE"
    assert result["rows"] == 1


def test_artifact_byte_mutation_fails_closed(tmp_path: Path) -> None:
    root = _fixture(tmp_path / "artifact")
    (root / "decision.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="size mismatch|hash mismatch"):
        validate(root)


def test_probability_mutation_fails_closed(tmp_path: Path) -> None:
    root = _fixture(tmp_path / "artifact")
    frame = pd.read_csv(root / "oof_predictions.csv.gz")
    frame.loc[0, "league_rate__pa_strikeout"] = 0.5
    (root / "oof_predictions.csv.gz").write_bytes(
        gzip.compress(frame.to_csv(index=False, lineterminator="\n").encode(), mtime=0)
    )
    manifest = json.loads((root / "manifest.json").read_text())
    path = root / "oof_predictions.csv.gz"
    manifest["artifacts"][path.name] = {
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    _write_json(root / "manifest.json", manifest)
    with pytest.raises(ValueError, match="does not sum"):
        validate(root)

