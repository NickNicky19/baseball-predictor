#!/usr/bin/env python3
"""Mutation checks for the archive-to-forward-shadow prediction boundary.

The test deliberately operates on serialized daily archives, not fabricated
``ShadowPrediction`` objects. It proves the adapter rejects the missing
provenance, identity, and tail-probability cases that would otherwise tempt a
future collector to guess.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shadow_prediction_snapshot import (
    ShadowPredictionSnapshotError,
    load_shadow_prediction_snapshot,
)


SHA = "a" * 64


def write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


def archive() -> dict:
    return {
        "game_date": "2026-07-20",
        "prediction_provenance": {
            "schema_version": "daily-prediction-provenance-v1",
            "captured_at_utc": "2026-07-20T16:00:00Z",
            "model_version": "model-v1",
            "effective_config_sha256": SHA,
            "code": {"status": "available", "snapshot_sha256": SHA},
        },
        "hitter_projections": [
            {
                "mlb_game_pk": 123,
                "player_id": 456,
                "category": "hits",
                "game_date": "2026-07-20",
                "simulation": {"p_ge_threshold": {"1.0": 0.63, "2.0": 0.22}},
            }
        ],
        "pitcher_projections": [],
    }


def must_fail(path: Path, data: dict, text: str) -> None:
    write(path, data)
    try:
        load_shadow_prediction_snapshot(path)
    except ShadowPredictionSnapshotError:
        print(f"[OK] mutation: {text}")
        return
    raise AssertionError(f"mutation was accepted: {text}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "prediction.json"
        base = archive()
        write(path, base)
        snapshot = load_shadow_prediction_snapshot(path)
        assert snapshot.probability_for(123, 456, "hits", 0.5) == 0.63
        assert snapshot.probability_for(123, 456, "hits", 1.5) == 0.22
        print("[OK] valid decision-time archive yields exact model tails")

        bad = copy.deepcopy(base)
        bad.pop("prediction_provenance")
        must_fail(path, bad, "old archive cannot be backfilled with current provenance")

        bad = copy.deepcopy(base)
        bad["hitter_projections"][0]["mlb_game_pk"] = None
        must_fail(path, bad, "missing game key cannot become a name/date join")

        bad = copy.deepcopy(base)
        bad["prediction_provenance"]["code"] = {"status": "unavailable"}
        must_fail(path, bad, "unavailable code provenance cannot feed a ledger")

        bad = copy.deepcopy(base)
        bad["hitter_projections"].append(copy.deepcopy(bad["hitter_projections"][0]))
        must_fail(path, bad, "duplicate hard model keys do not silently collapse")

        write(path, base)
        snapshot = load_shadow_prediction_snapshot(path)
        try:
            snapshot.probability_for(123, 456, "hits", 2.5)
        except ShadowPredictionSnapshotError:
            print("[OK] mutation: missing simulation tail does not use a fallback distribution")
        else:
            raise AssertionError("missing tail probability received a fallback")

    print("6/6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
