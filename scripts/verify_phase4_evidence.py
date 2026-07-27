#!/usr/bin/env python3
"""Fail closed on incomplete or internally inconsistent Phase 4 evidence."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from generate_phase4_file_manifest import collect

REQUIRED = {
    "PHASE4_EXECUTIVE_SUMMARY.md",
    "PHASE4_BLOCKER_CLOSURE_MATRIX.csv",
    "PHASE4_QA_LIMITATIONS_REGISTER.csv",
    "PHASE4_GIT_IDENTITY.md",
    "PHASE3_PRESERVATION_MANIFEST.csv",
    "PHASE3_PRESERVATION_ATTESTATION.md",
    "MANIFEST_AUTHORIZATION_CONTRACT.md",
    "SNAPSHOT_TRUST_MODEL.md",
    "MANIFEST_TRUST_SCHEMA.json",
    "MANIFEST_AUTHORIZATION_TEST_MATRIX.csv",
    "SNAPSHOT_FRESHNESS_CONTRACT.md",
    "SNAPSHOT_SOURCE_IMMUTABILITY_CONTRACT.md",
    "CROSS_PLATFORM_SOURCE_VERIFICATION.md",
    "SNAPSHOT_SOURCE_REJECTION_MATRIX.csv",
    "DISPLAY_SNAPSHOT_SCHEMA.json",
    "DISPLAY_ROW_IDENTITY_CONTRACT.md",
    "DISPLAY_PAYLOAD_SEMANTIC_CONTRACT.md",
    "SNAPSHOT_SCHEMA_MUTATION_MATRIX.csv",
    "CANONICAL_CHRONOLOGY_AUTHORITY.md",
    "CHRONOLOGY_CALL_GRAPH.json",
    "RETAINED_LIVE_PLAN_HASH_MANIFEST.json",
    "CHRONOLOGY_BYPASS_MUTATION_MATRIX.csv",
    "SCAFFOLD_PACKAGING_CONTRACT.md",
    "WHEEL_CONTENT_ALLOWLIST.txt",
    "WHEEL_CONTENT_MANIFEST.csv",
    "IMPORT_TO_LOCK_CLOSURE.md",
    "DEPENDENCY_LOCK_ATTESTATION.md",
    "INSTALLED_WHEEL_TEST_RESULTS.txt",
    "DASHBOARD_CAPABILITY_POLICY.json",
    "DASHBOARD_TRANSITIVE_IMPORT_GRAPH.json",
    "DASHBOARD_CAPABILITY_AUDIT.md",
    "DASHBOARD_RUNTIME_DENIAL_RESULTS.txt",
    "STRUCTURAL_MUTATION_TEST_MATRIX.csv",
    "PRODUCTION_BOUNDARY_STRUCTURAL_CONTRACT.md",
    "STAGING_DENY_BY_DEFAULT_EGRESS.md",
    "STAGING_NETWORK_BOUNDARY.json",
    "STAGING_IAM_BOUNDARY.json",
    "IAC_STRUCTURAL_TEST_MATRIX.csv",
    "WORKFLOW_STRUCTURAL_TEST_MATRIX.csv",
    "STAGING_DEPLOYMENT_PROHIBITION.md",
    "PHASE4_TEST_RESULTS.txt",
    "PHASE4_RAW_COMMAND_LOG.txt",
    "PHASE4_FILES_CREATED_OR_CHANGED.csv",
    "PHASE4_BLOCKERS.md",
    "PHASE4_NEXT_APPROVAL_REQUIRED.md",
    "PHASE4_FINAL_VERIFICATION.md",
}

QA_DECISIONS = {
    "CLOSED_BY_EXECUTABLE_ENFORCEMENT",
    "PARTIALLY_REMEDIATED",
    "OPEN",
    "REGRESSION",
    "NOT_TESTABLE_WITHIN_PHASE4",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_json(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    value = json.loads(text)
    require(isinstance(value, dict), f"JSON root is not an object: {path.name}")
    require(
        bool(value.get("schema_version") or value.get("x-document-schema-version")),
        f"JSON document schema version missing: {path.name}",
    )
    if path.name in {"DISPLAY_SNAPSHOT_SCHEMA.json", "MANIFEST_TRUST_SCHEMA.json"}:
        expected = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        require(
            text == expected,
            f"generated JSON ordering/format is not deterministic: {path.name}",
        )


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        rows = list(reader)
    require(bool(rows), f"CSV has no rows: {path.name}")
    for required in ("status", "evidence_path", "test_reference"):
        require(required in header, f"CSV lacks {required}: {path.name}")
        require(
            all(row[required].strip() for row in rows), f"empty {required}: {path.name}"
        )
    primary = header[0]
    values = [row[primary] for row in rows]
    require(all(values), f"CSV has empty primary key: {path.name}")
    require(
        len(values) == len(set(values)), f"CSV has duplicate primary rows: {path.name}"
    )
    return header, rows


def validate_file_manifest(root: Path, baseline: str, evidence: Path) -> None:
    path = evidence / "PHASE4_FILES_CREATED_OR_CHANGED.csv"
    _, rows = read_csv(path)
    actual = {row["relative_path"]: row["change_type"] for row in rows}
    expected = collect(root, baseline, path)
    require(
        actual == expected, "Phase 4 file manifest does not match current Git state"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args()
    root = args.repository_root.resolve(strict=True)
    evidence = root / "docs/omega_stage2/phase4"
    actual_names = {path.name for path in evidence.iterdir() if path.is_file()}
    require(
        REQUIRED <= actual_names,
        f"missing required outputs: {sorted(REQUIRED - actual_names)}",
    )
    for path in sorted(evidence.glob("*.json")):
        validate_json(path)
    csv_results = {path.name: read_csv(path) for path in sorted(evidence.glob("*.csv"))}
    _, closure_rows = csv_results["PHASE4_BLOCKER_CLOSURE_MATRIX.csv"]
    require(
        {row["qa_id"] for row in closure_rows}
        == {f"QA-{number:03d}" for number in range(12, 19)},
        "QA-012 through QA-018 closure rows are incomplete",
    )
    require(
        all(row["decision"] in QA_DECISIONS for row in closure_rows),
        "invalid Phase 4 QA decision",
    )
    _, wheel_rows = csv_results["WHEEL_CONTENT_MANIFEST.csv"]
    allowlist = [
        line
        for line in (evidence / "WHEEL_CONTENT_ALLOWLIST.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    require(
        sorted(row["entry_path"] for row in wheel_rows) == allowlist,
        "wheel manifest and allowlist differ",
    )
    _, preserved = csv_results["PHASE3_PRESERVATION_MANIFEST.csv"]
    require(len(preserved) == 20, "Phase 3 preservation manifest must bind 20 files")
    validate_file_manifest(root, args.baseline, evidence)
    print(
        f"Phase 4 evidence verified: {len(REQUIRED)} required outputs; "
        f"{len(list(evidence.glob('*.json')))} JSON; "
        f"{len(list(evidence.glob('*.csv')))} CSV"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
