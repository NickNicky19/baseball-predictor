from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_omega_phase4_schemas import (
    DOCUMENT_SCHEMA_VERSION,
    rendered_documents,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/omega_stage2/phase4"


def test_phase4_json_schemas_are_deterministic_and_current() -> None:
    for name, rendered in rendered_documents().items():
        path = OUTPUT / name
        assert path.read_text(encoding="utf-8") == rendered
        parsed = json.loads(rendered)
        assert parsed["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert parsed["x-document-schema-version"] == DOCUMENT_SCHEMA_VERSION
        assert parsed["additionalProperties"] is False


def test_trust_schema_requires_every_fail_closed_operational_control() -> None:
    schema = json.loads(
        (OUTPUT / "MANIFEST_TRUST_SCHEMA.json").read_text(encoding="utf-8")
    )
    assert set(schema["required"]) == {
        "schema_version",
        "manifest_relative_path",
        "trusted_manifest_sha256",
        "maximum_snapshot_age_seconds",
        "maximum_manifest_bytes",
        "maximum_snapshot_bytes",
        "immutability_anchor",
    }
