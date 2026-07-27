"""Generate the deterministic Phase 4 trust and display JSON schemas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dashboard.contracts import PredictionDisplaySnapshot

DOCUMENT_SCHEMA_VERSION = "omega-phase4-schema-document-v1"


def manifest_trust_schema() -> dict[str, object]:
    return {
        "$id": "urn:omega:manifest-trust-configuration:v1",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "const": "omega-manifest-trust-configuration-v1",
                "type": "string",
            },
            "manifest_relative_path": {
                "maxLength": 255,
                "minLength": 1,
                "pattern": r"^(?![A-Za-z]:)(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$",
                "type": "string",
            },
            "trusted_manifest_sha256": {
                "pattern": "^[0-9a-f]{64}$",
                "type": "string",
            },
            "maximum_snapshot_age_seconds": {
                "minimum": 1,
                "type": "integer",
            },
            "maximum_manifest_bytes": {"minimum": 1, "type": "integer"},
            "maximum_snapshot_bytes": {"minimum": 1, "type": "integer"},
            "immutability_anchor": {
                "const": "runtime_verified_read_only_tree_v1",
                "type": "string",
            },
        },
        "required": [
            "schema_version",
            "manifest_relative_path",
            "trusted_manifest_sha256",
            "maximum_snapshot_age_seconds",
            "maximum_manifest_bytes",
            "maximum_snapshot_bytes",
            "immutability_anchor",
        ],
        "title": "Omega out-of-band manifest trust configuration",
        "type": "object",
        "x-document-schema-version": DOCUMENT_SCHEMA_VERSION,
    }


def display_snapshot_schema() -> dict[str, object]:
    schema = PredictionDisplaySnapshot.model_json_schema(mode="validation")
    schema["$id"] = "urn:omega:prediction-display-snapshot:v2"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["x-document-schema-version"] = DOCUMENT_SCHEMA_VERSION
    return schema


def rendered_documents() -> dict[str, str]:
    documents = {
        "MANIFEST_TRUST_SCHEMA.json": manifest_trust_schema(),
        "DISPLAY_SNAPSHOT_SCHEMA.json": display_snapshot_schema(),
    }
    return {
        name: json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        for name, value in documents.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, rendered in rendered_documents().items():
        output = args.output_directory / name
        if args.check:
            if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
                raise SystemExit(f"generated schema is absent or stale: {output}")
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8", newline="\n")
    print("Phase 4 schemas verified" if args.check else "Phase 4 schemas generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
