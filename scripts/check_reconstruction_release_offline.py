#!/usr/bin/env python3
"""Mutation checks for schema-v2 reconstruction release evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_gate_reconstruct import (  # noqa: E402
    _canonical_config_bytes,
    _write_source_bundle,
)
from src.evaluation.market_reconstruct_validation import (  # noqa: E402
    _validate_release_evidence,
)


DATE = "2026-04-30"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expect_failure(payload: dict, needle: str) -> bool:
    try:
        _validate_release_evidence(
            payload,
            dates=[DATE],
            label="fixture",
            chronological_protocol=None,
            chronological_role=None,
        )
    except ValueError as exc:
        return needle in str(exc)
    return False


def main() -> int:
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp_raw:
        tmp = Path(tmp_raw)
        config = {"simulation": {"n_sims": 8000, "random_seed": 17}}
        source = _write_source_bundle(
            root=ROOT,
            manifest_path=tmp / "fixture.manifest.json",
            config=config,
        )

        date_dir = tmp / "features" / DATE
        date_dir.mkdir(parents=True)
        bundle = date_dir / "bundles.json"
        bundle.write_text('[{"player_id":1}]', encoding="utf-8")
        feature_manifest = date_dir / "manifest.json"
        feature_manifest.write_text(
            json.dumps(
                {
                    "game_date": DATE,
                    "bundle_count": 1,
                    "artifacts": {"json": {"path": "bundles.json", "sha256": sha(bundle)}},
                    "provenance": {
                        "config_sha256": hashlib.sha256(_canonical_config_bytes(config)).hexdigest(),
                        "source_tree_sha256": source["source_tree_sha256"],
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        payload = {
            "schema_version": 2,
            "config_sha256": hashlib.sha256(_canonical_config_bytes(config)).hexdigest(),
            "effective_config": config,
            "source_snapshot": source,
            "feature_snapshots": {
                DATE: {
                    "manifest_path": str(feature_manifest.resolve()),
                    "manifest_sha256": sha(feature_manifest),
                    "bundle_path": str(bundle.resolve()),
                    "bundle_sha256": sha(bundle),
                    "bundle_count": 1,
                }
            },
        }

        checks: list[tuple[bool, str]] = []
        _validate_release_evidence(
            payload,
            dates=[DATE],
            label="fixture",
            chronological_protocol=None,
            chronological_role=None,
        )
        checks.append((True, "valid content-addressed release passes"))

        bad_config = copy.deepcopy(payload)
        bad_config["effective_config"]["simulation"]["n_sims"] = 1
        checks.append((expect_failure(bad_config, "config content/hash"), "MUTATION: config tamper fails"))

        source_path = Path(source["path"])
        original_source = source_path.read_bytes()
        source_path.write_bytes(original_source + b"tamper")
        checks.append((expect_failure(payload, "source snapshot"), "MUTATION: source snapshot tamper fails"))
        source_path.write_bytes(original_source)

        original_bundle = bundle.read_bytes()
        bundle.write_bytes(original_bundle + b" ")
        checks.append((expect_failure(payload, "feature snapshot"), "MUTATION: feature bundle tamper fails"))
        bundle.write_bytes(original_bundle)

        missing_date = copy.deepcopy(payload)
        missing_date["feature_snapshots"] = {}
        checks.append((expect_failure(missing_date, "feature snapshot dates"), "MUTATION: missing date fails"))

        for ok, label in checks:
            print(f"  [{'OK' if ok else 'FAIL'}] {label}")
        passed = sum(bool(ok) for ok, _ in checks)
        print(f"\n  {passed}/{len(checks)}")
        return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
