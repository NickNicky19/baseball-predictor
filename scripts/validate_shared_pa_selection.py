#!/usr/bin/env python3
"""Validate a completed shared-PA selection artifact chain independently."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_selection_report import validate_selection_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    report = validate_selection_report(args.report, repo_root=ROOT, evidence_root=args.evidence_root)
    print(json.dumps({
        "status": report["status"],
        "selection_oof_rows": report["selection_oof"]["rows"],
        "betting_authorized": report["betting_authorized"],
        "may_2026_opened": report["may_2026_opened"],
        "confirmation_2025_opened": report["confirmation_2025_opened"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
