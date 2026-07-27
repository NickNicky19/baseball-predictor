from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_shared_pa_contact_eb_offset_2023 import validate


def test_artifact_hash_mutation_fails_closed(tmp_path: Path) -> None:
    for name in ("decision.json", "report.json"):
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    (tmp_path / "oof_predictions.csv.gz").write_bytes(b"mutated")
    manifest = {
        "schema_version": "shared-pa-contact-eb-offset-development-artifact-manifest-v1",
        "artifacts": {
            name: {"bytes": (tmp_path / name).stat().st_size, "sha256": "0" * 64}
            for name in ("decision.json", "report.json", "oof_predictions.csv.gz")
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        validate(tmp_path)


def test_extra_artifact_fails_closed_before_payload_use(tmp_path: Path) -> None:
    for name in ("decision.json", "manifest.json", "report.json"):
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    (tmp_path / "oof_predictions.csv.gz").write_bytes(b"x")
    (tmp_path / "unexpected.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="file surface changed"):
        validate(tmp_path)
