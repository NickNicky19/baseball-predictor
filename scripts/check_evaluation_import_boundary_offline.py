#!/usr/bin/env python3
"""Regression checks for the simulation/evaluation import boundary."""

from __future__ import annotations

import subprocess
import sys


CASES = {
    "simulation-first": (
        "from src.simulation.pa_simulator import HybridPASimulator; "
        "from src.evaluation import CalibrationEngine; "
        "assert HybridPASimulator and CalibrationEngine"
    ),
    "evaluation-first": (
        "from src.evaluation import CalibrationEngine, BacktestEngine; "
        "from src.simulation.pa_simulator import HybridPASimulator; "
        "assert CalibrationEngine and BacktestEngine and HybridPASimulator"
    ),
    "focused-adapter-first": (
        "from src.evaluation.hits_contact_adapter import consume_fitted_contact_xba; "
        "from src.simulation.pa_simulator import HybridPASimulator; "
        "assert consume_fitted_contact_xba and HybridPASimulator"
    ),
}


def main() -> int:
    passed = 0
    for label, source in CASES.items():
        result = subprocess.run(
            [sys.executable, "-c", source],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            print(f"  [FAIL] {label}\n{result.stdout}{result.stderr}")
            continue
        passed += 1
        print(f"  [OK] {label}")
    print(f"\n{passed}/{len(CASES)} checks passed")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
