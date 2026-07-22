#!/usr/bin/env python3
"""Certify one complete operational smoke as permanently non-economic evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.forward_evidence_era import certify_operational_smoke  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evidence-scope", required=True)
    ap.add_argument("--lifecycle-verification", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    output = Path(args.out).resolve()
    payload = certify_operational_smoke(
        evidence_scope_path=Path(args.evidence_scope).resolve(),
        lifecycle_verification_path=Path(args.lifecycle_verification).resolve(),
        official_game_date=args.date,
        certificate_path=output,
        root=ROOT,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    print("OPERATIONAL SMOKE CERTIFIED")
    print("  economic evidence eligible: FALSE; betting authorized: FALSE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
