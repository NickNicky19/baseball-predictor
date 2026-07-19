#!/usr/bin/env python3
"""Run the locked read-only cumulative rejection diagnostic."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.evaluation.shared_pa_rejection_diagnostic import (  # noqa: E402
    evaluate, load_oof, load_pa_distribution, load_protocol, sha256,
)


def atomic_json(path: Path, payload: dict) -> None:
    if path.exists():
        raise ValueError("diagnostic output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staging.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staging, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=ROOT / "config/shared_pa_cumulative_rejection_diagnostic_protocol.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    protocol_path = args.protocol.resolve()
    protocol = load_protocol(protocol_path, code_root=ROOT, evidence_root=evidence_root)
    frame = load_oof(protocol, evidence_root=evidence_root)
    pa_distribution = load_pa_distribution(protocol, evidence_root=evidence_root)
    analysis = evaluate(protocol, frame, pa_distribution)
    source_commit = subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    runtime_files = [
        Path(__file__),
        ROOT / "scripts/validate_shared_pa_cumulative_rejection_diagnostic.py",
        ROOT / "scripts/check_shared_pa_cumulative_rejection_diagnostic_validator_offline.py",
        ROOT / "src/evaluation/shared_pa_rejection_diagnostic.py",
        ROOT / "src/learning/shared_pa_model.py",
        ROOT / "src/evaluation/shared_pa_cumulative_selection.py",
        protocol_path,
    ]
    report = {
        "schema_version": "shared-pa-cumulative-rejection-diagnostic-report-v1",
        "status": "CERTIFIED_REJECTION_DIAGNOSIS_COMPLETE",
        "betting_authorized": False,
        "production_changed": False,
        "confirmation_2025_opened": False,
        "may_2026_opened": False,
        "model_artifact": None,
        "source_commit": source_commit,
        "protocol": {"path": str(protocol_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(protocol_path)},
        "inputs": protocol["inputs"],
        "runtime": {
            "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
            "file_hashes": {str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path) for path in runtime_files},
        },
        "analysis": analysis,
        "protected_invariants": protocol["protected_invariants"],
    }
    atomic_json(args.out.resolve(), report)
    print(json.dumps({"status": report["status"], **analysis["diagnostic_ranking"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
