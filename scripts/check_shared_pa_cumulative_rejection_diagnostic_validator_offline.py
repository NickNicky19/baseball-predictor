#!/usr/bin/env python3
"""Mutation checks for rejection-diagnostic report certification."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_rejection_diagnostic import (  # noqa: E402
    load_protocol, sha256, validate_report,
)


def caught(fn: object) -> bool:
    try:
        fn()  # type: ignore[operator]
    except (KeyError, TypeError, ValueError):
        return True
    return False


def main() -> int:
    evidence_root = Path(r"C:\Projects\baseball_predictor")
    protocol_path = ROOT / "config/shared_pa_cumulative_rejection_diagnostic_protocol.json"
    protocol = load_protocol(protocol_path, code_root=ROOT, evidence_root=evidence_root)
    analysis = {
        "population": {"rows": 1},
        "class_contributions": {},
        "derived_markets": {},
        "diagnostic_ranking": {"diagnostic_only_not_candidate_selection": True},
    }
    runtime_path = ROOT / "src/evaluation/shared_pa_rejection_diagnostic.py"
    report = {
        "schema_version": "shared-pa-cumulative-rejection-diagnostic-report-v1",
        "status": "CERTIFIED_REJECTION_DIAGNOSIS_COMPLETE",
        "betting_authorized": False,
        "production_changed": False,
        "confirmation_2025_opened": False,
        "may_2026_opened": False,
        "model_artifact": None,
        "source_commit": subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "protocol": {"path": "config/shared_pa_cumulative_rejection_diagnostic_protocol.json", "sha256": sha256(protocol_path)},
        "inputs": protocol["inputs"],
        "runtime": {"file_hashes": {"src/evaluation/shared_pa_rejection_diagnostic.py": sha256(runtime_path)}},
        "analysis": analysis,
        "protected_invariants": protocol["protected_invariants"],
    }
    validate = lambda payload: validate_report(  # noqa: E731
        payload, protocol=protocol, protocol_path=protocol_path, code_root=ROOT,
        expected_analysis=analysis,
    )
    checks = [("valid report accepted", not caught(lambda: validate(report)))]
    mutations = [
        ("status", lambda r: r.update(status="PASSED")),
        ("authorization", lambda r: r.update(betting_authorized=True)),
        ("production", lambda r: r.update(production_changed=True)),
        ("2025", lambda r: r.update(confirmation_2025_opened=True)),
        ("May", lambda r: r.update(may_2026_opened=True)),
        ("model", lambda r: r.update(model_artifact={"path": "model.cbm"})),
        ("protocol hash", lambda r: r["protocol"].update(sha256="0" * 64)),
        ("input", lambda r: r["inputs"]["selection_oof"].update(sha256="0" * 64)),
        ("runtime", lambda r: r["runtime"]["file_hashes"].update({"src/evaluation/shared_pa_rejection_diagnostic.py": "0" * 64})),
        ("analysis", lambda r: r["analysis"]["population"].update(rows=2)),
        ("invariant", lambda r: r["protected_invariants"].update(no_may_read=False)),
    ]
    for name, mutate in mutations:
        changed = copy.deepcopy(report)
        mutate(changed)
        checks.append((f"{name} mutation rejected", caught(lambda changed=changed: validate(changed))))
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"rejection diagnostic validator checks failed: {failed}")
    print(f"SHARED PA REJECTION DIAGNOSTIC VALIDATOR CHECKS VALID: {len(checks)}/{len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
