#!/usr/bin/env python3
"""Validate required hash-bound runtime artifacts before a clean release runs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.release_artifact_preflight import (  # noqa: E402
    ReleaseArtifactPreflightError,
    validate_hash_bound_runtime_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        verified = validate_hash_bound_runtime_artifacts(repo_root=args.repo_root, config_path=args.config)
    except ReleaseArtifactPreflightError as exc:
        print(f"RELEASE ARTIFACT PREFLIGHT FAILED: {exc}", file=sys.stderr)
        return 2
    print("RELEASE ARTIFACT PREFLIGHT PASS")
    for key, value in verified.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
