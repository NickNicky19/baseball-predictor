#!/usr/bin/env python3
"""Validate account-visible product evidence with a contained raw artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.execution_product_observation import (  # noqa: E402
    ExecutionProductObservationError,
    validate_execution_product_observation,
)


def require_contained_raw_evidence(
    observation_path: str | Path,
    *,
    evidence_root: str | Path,
) -> Path:
    """Return the raw artifact only when it resolves inside evidence_root."""

    source = Path(observation_path).resolve()
    root = Path(evidence_root).resolve()
    try:
        payload: Any = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionProductObservationError(f"cannot read execution-product observation: {exc}") from exc
    raw = payload.get("raw_evidence") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise ExecutionProductObservationError("raw evidence binding is absent")
    raw_path = str(raw.get("path", "")).strip()
    if not raw_path:
        raise ExecutionProductObservationError("raw evidence path must be relative to evidence_root")
    relative = Path(raw_path)
    if relative.is_absolute():
        raise ExecutionProductObservationError("raw evidence path must be relative to evidence_root")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ExecutionProductObservationError("raw evidence path escapes evidence_root")
    return candidate


def validate_contained_observation(
    observation_path: str | Path,
    *,
    evidence_root: str | Path,
    contracts_path: str | Path,
) -> tuple[dict[str, Any], str]:
    require_contained_raw_evidence(observation_path, evidence_root=evidence_root)
    return validate_execution_product_observation(
        observation_path,
        evidence_root=evidence_root,
        contracts_path=contracts_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--contracts", default="config/hits_execution_product_contracts.json")
    args = parser.parse_args(argv)
    payload, digest = validate_contained_observation(
        args.observation,
        evidence_root=args.evidence_root,
        contracts_path=args.contracts,
    )
    print("CONTAINED ACCOUNT-VISIBLE PRODUCT OBSERVATION VALID")
    print(f"  product: {payload['product']}; sha256: {digest}")
    print("  entry submitted: FALSE; betting authorized: FALSE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
