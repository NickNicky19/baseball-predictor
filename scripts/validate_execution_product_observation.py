#!/usr/bin/env python3
"""Validate one user-supplied account-visible execution-product observation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.execution_product_observation import (  # noqa: E402
    validate_execution_product_observation,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--observation", required=True)
    ap.add_argument("--evidence-root", required=True)
    ap.add_argument("--contracts", default="config/hits_execution_product_contracts.json")
    args = ap.parse_args()
    payload, digest = validate_execution_product_observation(
        args.observation,
        evidence_root=args.evidence_root,
        contracts_path=args.contracts,
    )
    print("ACCOUNT-VISIBLE PRODUCT OBSERVATION VALID")
    print(f"  product: {payload['product']}; sha256: {digest}")
    print("  entry submitted: FALSE; betting authorized: FALSE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
