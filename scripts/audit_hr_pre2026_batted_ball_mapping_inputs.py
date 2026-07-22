#!/usr/bin/env python3
"""Bind and audit the 2023-2025 inputs for the next HR mapping experiment."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_pre2026_batted_ball_mapping import (  # noqa: E402
    ALLOWED_MAPPING_FEATURES,
    ALLOWED_SEASONS,
    SCHEMA_VERSION,
    validate_feature_allowlist,
    validate_historical_input,
    validate_manifest_coverage,
)
from src.utils.provenance import code_provenance, sha256_file, sha256_json  # noqa: E402


DEFAULT_INPUT = ROOT / "data/training/training_hitters_2023_2025_statcast.csv.gz"
DEFAULT_OUTPUT = (
    ROOT
    / "data/analysis/hr_over_contract_v1/pre2026_fitted_batted_ball_mapping_v1/input_audit.json"
)
BOUND_SOURCES = [
    ROOT / "scripts/audit_hr_pre2026_batted_ball_mapping_inputs.py",
    ROOT / "src/evaluation/hr_pre2026_batted_ball_mapping.py",
    ROOT / "run_assemble_enriched.py",
    ROOT / "src/data/statcast_roller.py",
    ROOT / "src/data/statcast_batted_ball_rates.py",
    ROOT / "scripts/check_statcast_roller_offline.py",
]


def _load_manifest(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be an object: {path}")
    return payload


def _run_chronology_canary() -> dict[str, object]:
    checker = ROOT / "scripts/check_statcast_roller_offline.py"
    completed = subprocess.run(
        [sys.executable, str(checker)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(
            "Statcast chronology canary failed:\n"
            + completed.stdout[-2000:]
            + completed.stderr[-2000:]
        )
    return {
        "status": "passed",
        "checker_path": str(checker.relative_to(ROOT)).replace("\\", "/"),
        "checker_sha256": sha256_file(checker),
        "stdout_sha256": sha256_json(completed.stdout),
        "contract": "every rolling Statcast observation is strictly before game_date",
    }


def build_report(
    input_path: Path,
    *,
    training_dir: Path | None = None,
    builder_schema: str = "a3.1",
) -> dict[str, object]:
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    frame = pd.read_csv(input_path, low_memory=False)
    validated = validate_historical_input(
        frame, expected_builder_schema=builder_schema
    )
    manifest_root = training_dir.resolve() if training_dir is not None else ROOT / "data/training"
    manifests: dict[int, dict[str, object]] = {}
    manifest_evidence: dict[str, object] = {}
    for season in ALLOWED_SEASONS:
        path = manifest_root / f"manifest_{season}.json"
        manifests[season] = _load_manifest(path)
        manifest_evidence[str(season)] = {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(path),
        }
    coverage = validate_manifest_coverage(
        validated.frame, manifests, expected_builder_schema=builder_schema
    )
    selected = validate_feature_allowlist(sorted(ALLOWED_MAPPING_FEATURES))
    sources = {
        str(path.relative_to(ROOT)).replace("\\", "/"): sha256_file(path)
        for path in BOUND_SOURCES
    }
    provenance = code_provenance(ROOT)
    if provenance.get("status") != "available":
        raise ValueError(f"code provenance unavailable: {provenance}")

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "QUALIFIED_FOR_PROTOCOL_DESIGN_ONLY",
        "market": "draftkings_home_runs_over_0.5",
        "fit_period": {"seasons": list(ALLOWED_SEASONS), "contains_2026": False},
        "input": {
            "path": str(input_path.resolve().relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(input_path),
            **validated.summary,
            "builder_schema": builder_schema,
        },
        "manifests": manifest_evidence,
        "manifest_coverage": coverage,
        "chronology_canary": _run_chronology_canary(),
        "mapping_feature_allowlist": list(selected),
        "bound_source_sha256": sources,
        "code_provenance": provenance,
        "baseline_probability_availability": {
            "status": "not_provided_by_training_artifact",
            "consequence": (
                "No fitted probability adapter may be designed until the protocol explicitly "
                "defines a leakage-safe baseline probability source or a different measurable target."
            ),
        },
        "decision": {
            "input_audit_passed": True,
            "model_fit_permitted": False,
            "live_behavior_change_permitted": False,
            "may_2026_opened": False,
            "betting_authorized": False,
            "next_action": "predeclare the mapping target and obtain time-safe baseline HR probabilities",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--training-dir", type=Path)
    parser.add_argument("--builder-schema", choices=("a3.1", "a3.2"), default="a3.1")
    args = parser.parse_args(argv)
    report = build_report(
        args.input.resolve(),
        training_dir=args.training_dir,
        builder_schema=args.builder_schema,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("HR PRE-2026 BATTED-BALL INPUT AUDIT: PASS")
    print(f"  rows: {report['input']['rows']:,}")
    print(f"  artifact: {args.output}")
    print(f"  sha256: {sha256_file(args.output)}")
    print("  model fit: NOT YET PERMITTED (baseline probability source is not bound)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
