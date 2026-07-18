#!/usr/bin/env python3
"""Mutation checks for account-visible raw-evidence containment."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.execution_product_observation import ExecutionProductObservationError  # noqa: E402
from scripts.validate_execution_product_observation_contained import (  # noqa: E402
    require_contained_raw_evidence,
)


def rejected(path: Path, root: Path) -> bool:
    try:
        require_contained_raw_evidence(path, evidence_root=root)
    except ExecutionProductObservationError:
        return True
    return False


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="product_evidence_containment_") as temporary:
        parent = Path(temporary)
        root = parent / "evidence"
        root.mkdir()
        inside = root / "screen.png"
        inside.write_bytes(b"inside")
        outside = parent / "outside.png"
        outside.write_bytes(b"outside")
        observation = root / "observation.json"

        def write(raw_path: str) -> None:
            observation.write_text(json.dumps({"raw_evidence": {"path": raw_path}}), encoding="utf-8")

        checks: list[tuple[str, bool]] = []
        write("screen.png")
        checks.append((
            "contained relative evidence passes",
            require_contained_raw_evidence(observation, evidence_root=root) == inside.resolve(),
        ))
        write("../outside.png")
        checks.append(("MUTATION parent traversal fails", rejected(observation, root)))
        write(str(outside.resolve()))
        checks.append(("MUTATION absolute evidence path fails", rejected(observation, root)))
        write("")
        checks.append(("MUTATION blank evidence path fails", rejected(observation, root)))

    for label, passed in checks:
        print(f"[{'OK' if passed else 'FAIL'}] {label}")
    print(f"{sum(passed for _, passed in checks)}/{len(checks)}")
    return 0 if all(passed for _, passed in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
