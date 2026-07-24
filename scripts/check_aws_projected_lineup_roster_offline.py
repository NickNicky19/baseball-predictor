#!/usr/bin/env python3
"""Offline integrity gate for the projected-lineup AWS release."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_aws_projected_lineup_roster_tick import collector_code_sha256, load_runtime


def validate_service_isolation(service: str) -> None:
    if "/srv/baseball-shadow/current" in service:
        raise ValueError("collector service must not use a shared live release tree")
    if "WorkingDirectory=/opt/baseball-predictor-projected-lineup-roster/current" not in service:
        raise ValueError("collector service must use its independent release tree")
    if "/srv/baseball-shadow/venv/bin/python" in service:
        raise ValueError("collector service must not depend on an undeployed virtual environment")
    expected_exec = (
        "ExecStart=/usr/bin/python3 "
        "/opt/baseball-predictor-projected-lineup-roster/current/"
        "scripts/run_aws_projected_lineup_roster_tick.py"
    )
    if expected_exec not in service:
        raise ValueError("collector service must use the pinned system Python and absolute release script")
    for directive in (
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=full",
        "ProtectHome=true",
        "ReadWritePaths=/srv/baseball-shadow/projected-lineup-roster-receipts",
        "RestrictSUIDSGID=true",
        "LockPersonality=true",
        "CapabilityBoundingSet=",
    ):
        if directive not in service:
            raise ValueError(f"collector service is missing hardening directive {directive}")


def main() -> int:
    runtime, _ = load_runtime(ROOT / "config/projected_lineup_roster_runtime_v1.json")
    contract = ROOT / runtime["contract"]["path"]
    if hashlib.sha256(contract.read_bytes()).hexdigest() != runtime["contract"]["sha256"]:
        raise SystemExit("projected-lineup contract hash differs")
    if len(collector_code_sha256()) != 64:
        raise SystemExit("collector code hash is malformed")
    service = (ROOT / "deploy/projected_lineup_roster/baseball-projected-lineup-roster-tick.service").read_text(encoding="utf-8")
    try:
        validate_service_isolation(service)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print("AWS projected-lineup roster offline checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
