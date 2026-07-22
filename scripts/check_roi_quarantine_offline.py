#!/usr/bin/env python3
"""Mutation checks for the legacy ROI quarantine."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.roi_simulator import (  # noqa: E402
    LegacyROISimulatorError,
    ROISimulator,
)


def main() -> int:
    try:
        ROISimulator().simulate([], {})
    except LegacyROISimulatorError:
        pass
    else:
        raise AssertionError("legacy ROI default unexpectedly produced a result")
    print("[OK] legacy flat-stake ROI is disabled by default")

    report = ROISimulator(allow_legacy_research=True).simulate([], {})
    assert report.status == "RESEARCH_ONLY"
    assert "not a valid market evaluation" in report.reason
    print("[OK] explicit legacy opt-in remains permanently labelled research-only")
    print("2/2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
