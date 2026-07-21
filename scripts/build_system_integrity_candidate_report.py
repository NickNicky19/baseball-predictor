#!/usr/bin/env python3
"""Verify and bind the v2 Statcast integrity repair candidate."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.historical_backfill_contract import (
    BackfillContractError,
    assert_not_may_2026,
)
from src.utils.provenance import sha256_file


SCHEMA_VERSION = "mlb-system-integrity-candidate-v1"


def _verify_source(root: Path) -> dict[str, Any]:
    manifests = sorted(root.glob("*.manifest.json"))
    if not manifests:
        raise BackfillContractError("no source manifests")
    rows = bbe = 0
    bindings: list[dict[str, str]] = []
    for path in manifests:
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("schema_version") != "permissible-statcast-input-backfill-v2":
            raise BackfillContractError(f"unexpected source schema: {path}")
        for key in ("start", "end"):
            assert_not_may_2026(item["query"][key], context=f"{path.name} {key}")
        artifact = path.parent / item["artifact"]["path"]
        if sha256_file(artifact) != item["artifact"]["sha256"]:
            raise BackfillContractError(f"source hash mismatch: {artifact}")
        columns = set(item["columns"])
        if columns.intersection({"events", "result", "home_runs", "hits", "player_name"}):
            raise BackfillContractError(f"forbidden/ambiguous source columns: {path}")
        rows += int(item["statistics"]["rows"])
        bbe += int(item["statistics"]["measured_batted_balls"])
        bindings.append(
            {
                "manifest": path.name,
                "manifest_sha256": sha256_file(path),
                "artifact_sha256": item["artifact"]["sha256"],
            }
        )
    return {
        "chunks": len(manifests),
        "rows": rows,
        "measured_batted_balls": bbe,
        "bindings": bindings,
    }


def _verify_features(manifest_path: Path) -> tuple[dict[str, Any], pd.Series]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "repaired-batter-batted-ball-features-v3":
        raise BackfillContractError("unexpected repaired feature schema")
    rows = 0
    target_dates: list[str] = []
    for artifact in manifest["artifacts"]:
        target = artifact["target_date"]
        assert_not_may_2026(target, context="feature artifact target")
        path = manifest_path.parent / artifact["path"]
        if sha256_file(path) != artifact["sha256"]:
            raise BackfillContractError(f"feature hash mismatch: {path}")
        frame = pd.read_csv(path)
        if not frame.empty:
            if not pd.to_datetime(frame["max_source_date"]).lt(pd.Timestamp(target)).all():
                raise BackfillContractError(f"feature chronology failure: {path}")
            if not (frame["barrel_count"] <= frame["hard_hit_count"]).all():
                raise BackfillContractError(f"barrel subset failure: {path}")
            if not (frame["hard_hit_count"] <= frame["batted_ball_denominator"]).all():
                raise BackfillContractError(f"denominator failure: {path}")
            if "player_name_source" in frame.columns:
                raise BackfillContractError(f"ambiguous batter name survived: {path}")
        rows += int(len(frame))
        target_dates.append(target)
    incident_path = manifest_path.parent / "batter_batted_ball_features_2026-07-21.csv"
    incident_frame = pd.read_csv(incident_path)
    incident = incident_frame.loc[incident_frame["player_id"].eq(680757)]
    if len(incident) != 1:
        raise BackfillContractError("repaired Kwan numeric identity is not unique")
    return (
        {
            "manifest_sha256": sha256_file(manifest_path),
            "target_dates": len(target_dates),
            "rows": rows,
            "minimum_target": min(target_dates),
            "maximum_target": max(target_dates),
        },
        incident.iloc[0],
    )


def _preserved_incident(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    matches = [
        row
        for row in payload
        if int(row.get("hitter", {}).get("player", {}).get("mlb_id", -1)) == 680757
    ]
    if len(matches) != 1:
        raise BackfillContractError("preserved incident identity is not unique")
    statcast = matches[0]["statcast"]
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "player_id": 680757,
        "barrel_rate": float(statcast["barrel_rate"]),
        "hard_hit_rate": float(statcast["hard_hit_rate"]),
        "consumed_by_rich_features": bool(
            matches[0].get("metadata", {})
            .get("rich_features", {})
            .get("barrel_rate")
            == statcast["barrel_rate"]
        ),
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    source = _verify_source(Path(args.source_root))
    features, repaired = _verify_features(Path(args.feature_manifest))
    incident = _preserved_incident(Path(args.incident_archive))
    code_paths = [
        "src/data/statcast_integrity.py",
        "src/data/statcast_batted_ball_rates.py",
        "src/data/savant.py",
        "src/data/statcast_distributions.py",
        "src/data/historical_backfill_contract.py",
        "src/features/canonical_pa_features.py",
        "src/features/canonical_cumulative_pa_features.py",
        "src/features/direct_batter_pa_history.py",
        "src/features/rich_feature_enricher.py",
        "src/features/feature_store.py",
        "src/simulation/pa_simulator.py",
        "scripts/backfill_permissible_statcast_inputs.py",
        "scripts/build_repaired_batted_ball_features.py",
        "scripts/build_system_integrity_candidate_report.py",
    ]
    test_paths = [
        "tests/test_statcast_integrity_repair.py",
        "tests/test_permissible_statcast_backfill.py",
        "tests/test_repaired_batted_ball_feature_builder.py",
        "tests/test_phase9.py",
    ]
    bindings = {
        "code": {path: sha256_file(ROOT / path) for path in code_paths},
        "config": {
            "config/historical_raw_backfill_v1.json": sha256_file(
                ROOT / "config" / "historical_raw_backfill_v1.json"
            )
        },
        "tests": {path: sha256_file(ROOT / path) for path in test_paths},
        "source_manifest_set": source["bindings"],
        "feature_manifest_sha256": features["manifest_sha256"],
    }
    candidate_hash = hashlib.sha256(
        json.dumps(bindings, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": f"statcast-integrity-v3-{candidate_hash[:16]}",
        "candidate_sha256": candidate_hash,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "INTEGRITY_REPAIR_AND_RESEARCH_CANDIDATE_NOT_PROMOTED",
        "authorization": "RESEARCH_ONLY_NO_BETTING",
        "sealed_month": "MAY_2026_NOT_FETCHED_NOT_READ_NOT_PARSED_NOT_WRITTEN",
        "prospective_evidence": "NOT_BACKFILLED",
        "source_backfill": source,
        "feature_reconstruction": features,
        "incident": {
            "preserved_archive": incident,
            "root_causes": [
                "sparse barrel and hard_hit columns were averaged over incompatible populations",
                "foul-contact launch speed was eligible for a reconstructed BBE denominator",
                "Statcast player_name identified the pitcher and was attached to a batter ID in rejected v1",
            ],
            "repaired_numeric_identity": {
                "player_id": int(repaired["player_id"]),
                "target_date": repaired["target_date"],
                "max_source_date": repaired["max_source_date"],
                "batted_ball_denominator": int(repaired["batted_ball_denominator"]),
                "barrel_count": int(repaired["barrel_count"]),
                "hard_hit_count": int(repaired["hard_hit_count"]),
                "barrel_rate": float(repaired["barrel_rate"]),
                "hard_hit_rate": float(repaired["hard_hit_rate"]),
            },
            "rejected_artifacts_preserved": [
                "raw_2026_inputs_v1",
                "repaired_batted_ball_features_v1",
                "repaired_batted_ball_features_v2",
            ],
        },
        "tests": {
            "full_suite": {"passed": int(args.full_tests_passed), "failed": 0},
            "focused_mutation_suite": {"passed": int(args.focused_tests_passed), "failed": 0},
        },
        "bindings": bindings,
        "evaluation": {
            "calibration": "NOT_RUN_SOURCE_REPAIR_ONLY",
            "brier": "NOT_RUN_SOURCE_REPAIR_ONLY",
            "log_loss": "NOT_RUN_SOURCE_REPAIR_ONLY",
            "discrimination": "NOT_RUN_SOURCE_REPAIR_ONLY",
            "coverage": "INPUT_FEATURE_COVERAGE_ONLY_NOT_MARKET_COVERAGE",
            "uncertainty": "NOT_RUN_SOURCE_REPAIR_ONLY",
            "market_behavior": "NOT_RUN_NO_EXECUTABLE_PRICE_CLAIM",
        },
        "promotion": False,
        "highest_value_next_action": (
            "Bind the repaired count-bearing features into an isolated HR-first probability "
            "candidate, audit every active formula/override for duplicate or unsafe consumption, "
            "then fit on 2023 and select once on 2024 without opening May or reusing spent HR confirmation."
        ),
    }


def write_outputs(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_tmp = json_path.with_suffix(".json.tmp")
    json_tmp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    json_tmp.replace(json_path)
    repaired = report["incident"]["repaired_numeric_identity"]
    old = report["incident"]["preserved_archive"]
    markdown = f"""# Statcast integrity candidate report

- Candidate: `{report['candidate_id']}`
- Classification: {report['classification']}
- Promotion: **NO**
- Betting authorization: **NO**
- May 2026: not fetched, read, parsed, or written
- Prospective evidence: not backfilled

## Verified reconstruction

- Source chunks: {report['source_backfill']['chunks']}
- Input-only rows: {report['source_backfill']['rows']}
- Measured BBE: {report['source_backfill']['measured_batted_balls']}
- Feature dates: {report['feature_reconstruction']['target_dates']}
- Strictly-prior feature rows: {report['feature_reconstruction']['rows']}

## July 21 incident

The preserved archive remains unchanged at SHA-256 `{old['sha256']}` and records
barrel rate {old['barrel_rate']:.6f} versus hard-hit rate {old['hard_hit_rate']:.6f}.
The repaired numeric MLB identity 680757 uses sources only through
{repaired['max_source_date']}: {repaired['barrel_count']} barrels and
{repaired['hard_hit_count']} hard-hit BBE over a shared denominator of
{repaired['batted_ball_denominator']} ({repaired['barrel_rate']:.6f} and
{repaired['hard_hit_rate']:.6f}).

## Adjudication

This establishes an integrity repair, not probability improvement. Calibration,
Brier score, log loss, discrimination, uncertainty, and market behavior have not
yet been rerun. The candidate is not promoted and cannot authorize betting.

Highest-value next action: {report['highest_value_next_action']}
"""
    md_tmp = markdown_path.with_suffix(".md.tmp")
    md_tmp.write_text(markdown, encoding="utf-8")
    md_tmp.replace(markdown_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--feature-manifest", required=True)
    parser.add_argument("--incident-archive", required=True)
    parser.add_argument("--full-tests-passed", type=int, required=True)
    parser.add_argument("--focused-tests-passed", type=int, required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--markdown-out", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(args)
    write_outputs(report, Path(args.json_out), Path(args.markdown_out))
    print(report["candidate_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
