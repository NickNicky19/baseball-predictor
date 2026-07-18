#!/usr/bin/env python3
"""Mutation checks for the date-block capture intervals used by market A/B."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_market_ab import (  # noqa: E402
    date_block_capture_change_interval,
    date_block_capture_interval,
)


def frame(edges, clvs) -> pd.DataFrame:
    return pd.DataFrame({"edge": edges, "clv": clvs})


def main() -> int:
    dates = np.array(["2026-06-01", "2026-06-01", "2026-06-02", "2026-06-02"])
    frozen = frame([0.10, 0.08, 0.12, 0.09], [0.01, 0.00, 0.02, 0.01])
    candidate = frame([0.10, 0.08, 0.12, 0.09], [0.04, 0.03, 0.05, 0.04])

    flo, fhi, fn = date_block_capture_interval(
        frozen, dates, min_edge=0.04, bootstrap=200, seed=7
    )
    assert fn == 200 and np.isfinite(flo) and np.isfinite(fhi) and flo <= fhi
    print("[OK] single-arm date-block capture interval is defined on selected rows")

    clo, chi, cn = date_block_capture_interval(
        candidate, dates, min_edge=0.04, bootstrap=200, seed=7
    )
    assert cn == 200 and clo > fhi
    print("[OK] MUTATION: changing CLV moves the arm capture interval")

    dlo, dhi, dn = date_block_capture_change_interval(
        frozen, candidate, dates, min_edge=0.04, bootstrap=200, seed=7
    )
    assert dn == 200 and dlo > 0.0 and dhi >= dlo
    print("[OK] paired candidate-minus-frozen interval keeps the date blocks aligned")

    # A ratio with one selected row is mathematically defined.  The old code
    # silently imposed an undocumented 10-bet requirement inside each draw;
    # this fixture proves no second cutoff remains.
    one = frame([0.10], [0.01])
    lo, hi, n = date_block_capture_interval(
        one, np.array(["2026-06-01"]), min_edge=0.04, bootstrap=50, seed=7
    )
    assert n == 50 and np.isclose(lo, 0.1) and np.isclose(hi, 0.1)
    print("[OK] MUTATION: one selected row remains defined; no hidden bootstrap minimum")

    empty = frame([0.01, 0.02], [0.50, -0.50])
    lo, hi, n = date_block_capture_interval(
        empty, np.array(["2026-06-01", "2026-06-02"]), min_edge=0.04, bootstrap=50, seed=7
    )
    assert n == 0 and np.isnan(lo) and np.isnan(hi)
    print("[OK] no selected claimed edge produces no capture interval, not an epsilon ratio")

    print("5/5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
