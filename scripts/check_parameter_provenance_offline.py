#!/usr/bin/env python3
"""Offline mutation checks for the parameter-provenance audit."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.parameter_provenance import audit_parameter_provenance


ROOTS = ("weights",)


def main() -> int:
    config = {"weights": {"season": 0.35, "recent": 0.65}}

    rows, verdict = audit_parameter_provenance(config, roots=ROOTS)
    assert verdict == "RESEARCH_ONLY"
    assert {row.origin for row in rows} == {"unverified"}
    print("[OK] missing records are unverified, never inferred fitted")

    config["parameter_provenance"] = {
        "weights.season": {"origin": "placeholder", "evidence": "not fitted"},
        "weights.recent": {"origin": "predeclared_structural", "evidence": "documented rule"},
    }
    rows, verdict = audit_parameter_provenance(config, roots=ROOTS)
    assert verdict == "RESEARCH_ONLY"
    assert next(row for row in rows if row.path == "weights.season").status == "research_only"
    print("[OK] a placeholder keeps the configuration research-only")

    config["parameter_provenance"]["weights.season"] = {
        "origin": "fitted",
        "evidence": "walk-forward artifact",
        # Deliberately omit the hash: a fitted claim without a pinned artifact
        # must not pass by assertion alone.
    }
    rows, verdict = audit_parameter_provenance(config, roots=ROOTS)
    season = next(row for row in rows if row.path == "weights.season")
    assert season.origin == "unverified" and verdict == "RESEARCH_ONLY"
    print("[OK] mutation: unpinned fitted claim fails closed")

    config["parameter_provenance"]["weights.season"]["artifact_sha256"] = "a" * 64
    rows, verdict = audit_parameter_provenance(config, roots=ROOTS)
    assert verdict == "PROMOTION_ELIGIBLE"
    assert all(row.status == "approved" for row in rows)
    print("[OK] explicit pinned provenance is the only promotion-eligible path")
    print("4/4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
