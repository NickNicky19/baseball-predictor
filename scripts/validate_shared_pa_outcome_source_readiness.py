#!/usr/bin/env python3
"""Verify whether a qualified 2023 source can support shared-PA outcome fitting."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_outcome_source_readiness import inspect_release


def _atomic_json(path: Path, value: dict) -> None:
    payload = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = inspect_release(args.source_root)
    if args.output:
        _atomic_json(args.output, report)
    print(json.dumps({"status": report["status"], "coverage": report["coverage"]}, sort_keys=True))
    return 0 if report["status"].startswith("OUTCOME_COMPLETE") else 2


if __name__ == "__main__":
    raise SystemExit(main())
