#!/usr/bin/env python3
"""Independently reproduce and certify the locked rejection diagnostic."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_rejection_diagnostic import (  # noqa: E402
    evaluate, load_oof, load_pa_distribution, load_protocol, sha256, validate_report,
)


def atomic_json(path: Path, payload: dict) -> None:
    if path.exists():
        raise ValueError("diagnostic certificate already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staging.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staging, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    protocol_path = args.protocol.resolve()
    report_path = args.report.resolve()
    protocol = load_protocol(protocol_path, code_root=ROOT, evidence_root=evidence_root)
    frame = load_oof(protocol, evidence_root=evidence_root)
    pa_distribution = load_pa_distribution(protocol, evidence_root=evidence_root)
    expected_analysis = evaluate(protocol, frame, pa_distribution)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    validate_report(
        report, protocol=protocol, protocol_path=protocol_path, code_root=ROOT,
        expected_analysis=expected_analysis,
    )
    head = subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    if report.get("source_commit") != head:
        raise ValueError("rejection diagnostic source commit differs from certifier checkout")
    certificate = {
        "schema_version": "shared-pa-cumulative-rejection-diagnostic-certificate-v1",
        "status": "CERTIFIED_REJECTION_DIAGNOSIS_REPRODUCED",
        "betting_authorized": False,
        "production_changed": False,
        "confirmation_2025_opened": False,
        "may_2026_opened": False,
        "source_commit": head,
        "protocol": {"path": str(protocol_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(protocol_path)},
        "report": {"path": str(report_path), "sha256": sha256(report_path)},
        "population": expected_analysis["population"],
        "diagnostic_ranking": expected_analysis["diagnostic_ranking"],
        "protected_invariants": protocol["protected_invariants"],
    }
    atomic_json(args.out.resolve(), certificate)
    print(json.dumps({"status": certificate["status"], **certificate["diagnostic_ranking"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
