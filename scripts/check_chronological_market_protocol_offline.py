#!/usr/bin/env python3
"""Mutation checks for the chronological market-policy lock."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from argparse import Namespace
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.chronological_market_protocol import (  # noqa: E402
    build_protocol,
    verify_protocol_role,
)
from run_gate_reconstruct import validate_chronological_source  # noqa: E402


def write_arm(root: Path, name: str, rows: list[dict]) -> Path:
    artifact = root / f"{name}.csv"
    frame = pd.DataFrame(rows)
    frame.to_csv(artifact, index=False)
    manifest = root / f"{name}_manifest.json"
    dates = sorted(frame.official_game_date.unique().tolist())
    manifest.write_text(json.dumps({
        "selection_rule": "dk_base_pregame_starter_and_pa_v1",
        "price_freshness_rule": "both_entry_sides_fresh_v1",
        "markets": ["hits"],
        "book": "draftkings",
        "entry_hours": 4,
        "max_quote_age": 90,
        "hashes": {
            "artifact": hashlib.sha256(artifact.read_bytes()).hexdigest()[:16],
            "policy": "POLICYSHA",
        },
        "funnel": {"strict_unique": len(frame)},
        "official_date_universe": dates,
    }), encoding="utf-8")
    return manifest


def expect_error(fn, text: str) -> None:
    try:
        fn()
    except (ValueError, FileNotFoundError) as exc:
        assert text in str(exc), (str(exc), text)
    else:
        raise AssertionError(f"expected failure containing {text!r}")


def main() -> int:
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        root = Path(tmp)
        fit_rows = [dict(mlb_game_pk=1, player_id=10, category="hits", line=0.5,
                         official_game_date="2026-04-30", entry_p_over=0.51)]
        hold_rows = [dict(mlb_game_pk=2, player_id=20, category="hits", line=0.5,
                          official_game_date="2026-05-01", entry_p_over=0.52)]
        fit = write_arm(root, "fit", fit_rows)
        holdout = write_arm(root, "holdout", hold_rows)
        parent = write_arm(root, "parent", fit_rows + hold_rows)
        protocol = root / "protocol.json"

        build_protocol(parent_manifest=parent, fit_manifest=fit,
                       holdout_manifest=holdout, holdout_start="2026-05-01",
                       output=protocol)
        print("[PASS] production builder locks an exact chronological partition")
        assert verify_protocol_role(protocol, role="fit", market_manifest=fit)["rows"] == 1
        assert verify_protocol_role(protocol, role="holdout", market_manifest=holdout)["rows"] == 1
        print("[PASS] both reconstruction roles verify against the pinned protocol")

        wired = validate_chronological_source(Namespace(
            chronological_protocol=str(protocol), chronological_role="holdout",
            market_manifest=str(holdout),
        ))
        assert wired and wired["role"] == "holdout"
        print("[PASS] the production reconstruction seam consumes the lock")

        expect_error(
            lambda: validate_chronological_source(Namespace(
                chronological_protocol=str(protocol), chronological_role=None,
                market_manifest=str(holdout),
            )),
            "must be supplied together",
        )
        print("[PASS] MUTATION a protocol without a declared role hard-fails")

        expect_error(
            lambda: verify_protocol_role(protocol, role="fit", market_manifest=holdout),
            "not the protocol's locked fit manifest",
        )
        print("[PASS] MUTATION holdout cannot masquerade as fit")

        fit_artifact = root / "fit.csv"
        original = fit_artifact.read_text(encoding="utf-8")
        fit_artifact.write_text(original.replace("0.51", "0.61"), encoding="utf-8")
        expect_error(
            lambda: verify_protocol_role(protocol, role="fit", market_manifest=fit),
            "strict artifact hash mismatch",
        )
        fit_artifact.write_text(original, encoding="utf-8")
        print("[PASS] MUTATION a changed price cannot enter the locked fit arm")

        sidecar = protocol.with_suffix(".json.sha256")
        good_hash = sidecar.read_text(encoding="utf-8")
        sidecar.write_text("0" * 64 + "\n", encoding="utf-8")
        expect_error(
            lambda: verify_protocol_role(protocol, role="fit", market_manifest=fit),
            "protocol hash mismatch",
        )
        sidecar.write_text(good_hash, encoding="utf-8")
        print("[PASS] MUTATION a changed protocol cannot authorize reconstruction")

        expect_error(
            lambda: build_protocol(parent_manifest=parent, fit_manifest=fit,
                                   holdout_manifest=holdout,
                                   holdout_start="2026-05-02",
                                   output=root / "bad_boundary.json"),
            "holdout contains a date before holdout_start",
        )
        print("[PASS] MUTATION chronology is enforced, not descriptive prose")

        drift_hold_rows = [dict(hold_rows[0], entry_p_over=0.72)]
        drift_hold = write_arm(root, "holdout_drift", drift_hold_rows)
        expect_error(
            lambda: build_protocol(parent_manifest=parent, fit_manifest=fit,
                                   holdout_manifest=drift_hold,
                                   holdout_start="2026-05-01",
                                   output=root / "bad_value.json"),
            "evaluator-consumed value drifted",
        )
        print("[PASS] MUTATION full values are compared; key-only equality is insufficient")

    print("9/9 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
