#!/usr/bin/env python3
"""Fail when the exact v2 source manifest differs from reviewed release bytes."""

from __future__ import annotations

from pathlib import Path

from src.evaluation.shared_pa_projected_opportunity_release_v2 import (
    REQUIRED_FILE_ROLES,
    load_source_manifest_v2,
)

ROOT = Path(__file__).resolve().parents[1]
def main() -> int:
    _, digest = load_source_manifest_v2(root=ROOT)
    print(
        f"v2 candidate source manifest verified: {len(REQUIRED_FILE_ROLES)} files; "
        f"sha256={digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
