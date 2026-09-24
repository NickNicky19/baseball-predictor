"""Verify research-only state and frozen production source hashes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.omega_contracts.production import verify_candidate_and_production_boundaries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = verify_candidate_and_production_boundaries(args.repository_root)
    print(json.dumps({"status": "PASS", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
