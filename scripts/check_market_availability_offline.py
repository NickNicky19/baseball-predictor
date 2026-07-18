#!/usr/bin/env python3
"""Mutation checks for the DraftKings HR availability wording."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_market_availability import draftkings_hr_status  # noqa: E402


def row(book: str, scoreable: int) -> dict:
    return {"book": book, "SCOREABLE": scoreable}


def main() -> int:
    none = pd.DataFrame([row("pinnacle", 4139)])
    assert "ABSENT" in draftkings_hr_status(none)
    print("[OK] no DK row is reported as absent from the reporting table")

    # M1: the bug was calling this case merely "present", even though it
    # contributes no evaluable HR row.  The status must name the distinction.
    zero = pd.DataFrame([row("draftkings", 0), row("pinnacle", 4139)])
    message = draftkings_hr_status(zero)
    assert "ZERO SCOREABLE" in message and message != "present"
    print("[OK] MUTATION raw-presence with zero scoreable rows is not mislabeled")

    covered = pd.DataFrame([row("draftkings", 12)])
    assert "12 SCOREABLE" in draftkings_hr_status(covered)
    print("[OK] real scoreable DK coverage remains distinguishable")
    print("3/3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
