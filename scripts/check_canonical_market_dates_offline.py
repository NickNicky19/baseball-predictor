#!/usr/bin/env python3
"""Mutation checks for canonical dates in the strict market artifact.

The artifact carries a vendor ``market_date`` for diagnostics, but its
reconstruction and bootstrap universe must come from the hard-mapped MLB
``official_date``.  These checks use the builder's real bridge function.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_strict_unique_hits import attach_canonical_dates  # noqa: E402


def expect_error(fn, contains: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert contains in str(exc), str(exc)
    else:
        raise AssertionError(f"expected ValueError containing {contains!r}")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        fragments_path = root / "crosswalk_fragments.csv"
        fragments = pd.DataFrame([
            dict(vendor_game_id="g~late", start_time="2026-06-02T01:00:00Z",
                 outcome="mapped", mlb_game_pk=700001,
                 official_date="2026-06-01"),
        ])
        fragments.to_csv(fragments_path, index=False)
        mapped = pd.DataFrame([
            dict(vendor_game_id="g~late", start_time="2026-06-02T01:00:00Z",
                 mlb_game_pk=700001, player_id=42, category="hits", line=0.5,
                 market_date="2026-06-02"),
        ])

        attached = attach_canonical_dates(mapped, fragments_path)
        assert attached.official_game_date.tolist() == ["2026-06-01"]
        assert attached.market_date.tolist() == ["2026-06-02"]
        print("[PASS] canonical official date is retained when vendor market date differs")

        wrong_pk = fragments.copy()
        wrong_pk.loc[0, "mlb_game_pk"] = 700002
        wrong_pk.to_csv(fragments_path, index=False)
        expect_error(lambda: attach_canonical_dates(mapped, fragments_path), "disagree")
        print("[PASS] MUTATION consumer/fragment MLB game_pk disagreement fails")

        missing_date = fragments.copy()
        missing_date.loc[0, "official_date"] = ""
        missing_date.to_csv(fragments_path, index=False)
        expect_error(lambda: attach_canonical_dates(mapped, fragments_path), "lacks MLB")
        print("[PASS] MUTATION mapped fragment without official date fails")

        duplicate = pd.concat([fragments, fragments], ignore_index=True)
        duplicate.to_csv(fragments_path, index=False)
        expect_error(lambda: attach_canonical_dates(mapped, fragments_path), "duplicate")
        print("[PASS] MUTATION duplicate fragment identity fails")

    print("4/4 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
