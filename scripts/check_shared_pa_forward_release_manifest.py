#!/usr/bin/env python3
"""Verify every file and composite hash in the shared-PA release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "reports/shared_pa_forward_release_v1_hash_manifest.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _composite(mapping: Mapping[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(dict(mapping), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def verify_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "shared-pa-forward-release-hash-manifest-v1":
        raise ValueError("release hash manifest schema changed")
    groups = {
        "code": "code_files",
        "configuration": "configuration_files",
        "feature_contract": "feature_contract_files",
        "tests": "test_files",
        "deployment": "deployment_files",
    }
    composites = payload.get("composite_sha256")
    if not isinstance(composites, dict) or set(composites) != set(groups):
        raise ValueError("release composite hash set changed")
    checked = 0
    for composite_name, file_group_name in groups.items():
        mapping = payload.get(file_group_name)
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError(f"release {file_group_name} is missing")
        normalized: dict[str, str] = {}
        for relative, expected in mapping.items():
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise ValueError(f"release {file_group_name} entry is malformed")
            target = (ROOT / relative).resolve()
            try:
                target.relative_to(ROOT)
            except ValueError as exc:
                raise ValueError("release manifest path escapes repository") from exc
            actual = _sha(target)
            if actual != expected:
                raise ValueError(f"release file hash differs: {relative}")
            normalized[relative] = expected
            checked += 1
        if _composite(normalized) != composites[composite_name]:
            raise ValueError(f"release composite hash differs: {composite_name}")
    control = json.loads((ROOT / "config/shared_pa_forward_eb_control_v1.json").read_text(encoding="utf-8"))
    data = payload.get("data_bindings")
    if not isinstance(data, dict) or data != {
        "certified_2023_pa_panel_sha256": control["source"]["certified_panel_sha256"],
        "certified_2023_pa_panel_manifest_sha256": control["source"]["panel_manifest_sha256"],
        "pa_volume_fit_2023_artifact_sha256": _sha(ROOT / "data/analysis/system_integrity_v2/pa_volume_chronology_v1/pa_distribution_fit_2023.json"),
        "pa_volume_fit_2023_protocol_sha256": control["source"]["protocol_sha256"],
    }:
        raise ValueError("release data bindings differ from certified inputs")
    return {
        "schema_version": "shared-pa-forward-release-hash-verification-v1",
        "manifest_sha256": _sha(path),
        "checked_file_bindings": checked,
        "composite_sha256": composites,
        "state": "verified",
        "research_only": True,
        "betting_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify_manifest(args.manifest), sort_keys=True))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
