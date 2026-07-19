#!/usr/bin/env python3
"""Fail closed if a bound probability-foundation decision record drifts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _bound(root: Path, relative: object) -> Path:
    path = (root / str(relative)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"artifact escapes repository: {relative}") from exc
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _explicitly_unauthorized(payload: dict[str, Any]) -> bool:
    if payload.get("betting_authorized") is False:
        return True
    scope = payload.get("scope")
    return isinstance(scope, dict) and scope.get("betting_authorized") is False


def validate(protocol_path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    protocol = _json(protocol_path)
    if protocol.get("schema_version") != "probability-foundation-consolidation-protocol-v1":
        raise ValueError("unexpected consolidation protocol schema")
    if protocol.get("status") != "LOCKED_REJECTION_AND_READINESS_EVIDENCE":
        raise ValueError("consolidation protocol is not locked")
    scope = protocol.get("scope")
    if not isinstance(scope, dict) or scope != {
        "production_changed": False,
        "betting_authorized": False,
        "may_2026_is_not_a_new_candidate_holdout": True,
        "markets_must_remain_separate": True,
    }:
        raise ValueError("protected consolidation scope changed")

    validated: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for record in protocol.get("records", []):
        if not isinstance(record, dict):
            raise ValueError("record is not an object")
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id or record_id in seen_ids:
            raise ValueError("record id is missing or duplicated")
        seen_ids.add(record_id)
        artifact = _bound(root, record.get("path"))
        if not artifact.is_file():
            raise ValueError(f"missing bound evidence: {record_id}")
        if _sha256(artifact) != record.get("sha256"):
            raise ValueError(f"bound evidence hash changed: {record_id}")
        payload = _json(artifact)
        if payload.get("status") != record.get("required_status"):
            raise ValueError(f"bound evidence status changed: {record_id}")
        if not _explicitly_unauthorized(payload):
            raise ValueError(f"bound evidence weakened authorization boundary: {record_id}")
        validated.append({"id": record_id, "path": str(record["path"]), "sha256": str(record["sha256"])})
    if len(validated) != 10:
        raise ValueError("consolidation record count changed")
    return {"validated_records": validated, "record_count": len(validated)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=ROOT / "config" / "probability_foundation_consolidation_v1.json")
    args = parser.parse_args()
    result = validate(args.protocol)
    print(f"PROBABILITY FOUNDATION CONSOLIDATION VALID: {result['record_count']} records")
    for record in result["validated_records"]:
        print(f"  {record['id']}: {record['sha256']}")


if __name__ == "__main__":
    main()
