#!/usr/bin/env python3
"""Verify every hash-bound file in the comparator release manifest."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config/shared_pa_comparator_release_manifest_v1.json"
SHA = re.compile(r"^[0-9a-f]{64}$")


def main() -> int:
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        files = payload.get("files_sha256")
        if (
            payload.get("schema_version") != "shared-pa-comparator-release-manifest-v1"
            or payload.get("status") != "RESEARCH_ONLY_UNPROMOTED"
            or payload.get("model_probability_improvement_proven_percent") != 0
            or payload.get("betting_authorized") is not False
            or not isinstance(files, dict)
            or not files
        ):
            raise ValueError("release manifest surface changed")
        for relative, expected in files.items():
            if not isinstance(relative, str) or not SHA.fullmatch(str(expected)):
                raise ValueError("release manifest path/hash is malformed")
            path = (ROOT / relative).resolve()
            path.relative_to(ROOT)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"release file hash differs: {relative}")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "schema_version": "shared-pa-comparator-release-check-v1",
        "verified_files": len(files),
        "research_only": True,
        "betting_authorized": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
