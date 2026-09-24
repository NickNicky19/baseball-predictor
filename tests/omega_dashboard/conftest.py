from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def valid_snapshot() -> dict[str, Any]:
    return {
        "schema_version": "prediction-display-snapshot-v1",
        "official_slate_date": "2026-07-27",
        "created_utc": "2026-07-27T12:00:00Z",
        "source_commit": "67efa1427987517d2c283cd502c1898b97c6bb2b",
        "producer": {
            "producer_id": "authorized-runner-v1",
            "producer_version": "release-1",
        },
        "model_banner": {
            "model_id": "frozen-research-model-v1",
            "model_version": "frozen-research-model-v1.0",
            "qualification_state": "research_only",
            "betting_authorized": False,
            "current_live_model_id": "frozen-research-model-v1",
            "frozen_comparator_model_id": "frozen-comparator-v1",
            "research_candidate_model_id": None,
            "research_candidate_state": "not_present",
        },
        "source_health": {
            "status": "healthy",
            "message": "All declared sources passed their display contracts.",
            "latest_refresh_utc": "2026-07-27T12:00:00Z",
        },
        "config_sha256": "a" * 64,
        "artifact_sha256s": {"frozen-pa-artifact": "b" * 64},
        "supported_markets": ["hits", "home_runs", "total_bases"],
        "rows": [
            {
                "row_id": "game-880001-player-111-hits",
                "game_pk": 880001,
                "player_name": "Synthetic Batter",
                "mlb_player_id": 111,
                "team": "AAA",
                "opponent": "BBB",
                "game_time_utc": "2026-07-27T23:00:00Z",
                "home_away": "home",
                "market": "hits",
                "product_id": "model-count-distribution",
                "market_side": None,
                "market_line": None,
                "lineup_status": "projected",
                "batting_slot": 2,
                "projected_pa": 4.1,
                "pa_distribution": [
                    {"value": 3, "probability": 0.2},
                    {"value": 4, "probability": 0.5},
                    {"value": 5, "probability": 0.3},
                ],
                "opposing_starter": {
                    "mlb_player_id": 222,
                    "name": "Synthetic Pitcher",
                    "status": "probable_receipt_verified",
                },
                "point_prediction_kind": "expected_count",
                "point_prediction": 0.8,
                "count_distribution": [
                    {"value": 0, "probability": 0.4},
                    {"value": 1, "probability": 0.4},
                    {"value": 2, "probability": 0.2},
                ],
                "uncertainty": {"method": "display-bound", "lower": 0.6, "upper": 1.1},
                "data_health_tier": "healthy",
                "source_observed_utc": "2026-07-27T11:59:00Z",
                "source_freshness_seconds": 60,
                "exclusion_flags": [],
                "fallback_flags": [],
                "abstention_reason": None,
                "model_id": "frozen-research-model-v1",
                "comparison": {
                    "current_live_prediction": 0.8,
                    "frozen_comparator_prediction": 0.79,
                    "research_candidate_prediction": None,
                    "research_candidate_state": "not_present",
                },
            }
        ],
    }


def valid_manifest(snapshot_bytes: bytes) -> dict[str, Any]:
    return {
        "schema_version": "prediction-display-manifest-v1",
        "snapshot_path": "snapshot.json",
        "snapshot_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        "snapshot_byte_size": len(snapshot_bytes),
        "official_slate_date": "2026-07-27",
        "created_utc": "2026-07-27T12:00:00Z",
        "source_commit": "67efa1427987517d2c283cd502c1898b97c6bb2b",
        "producer": {
            "producer_id": "authorized-runner-v1",
            "producer_version": "release-1",
        },
        "model_id": "frozen-research-model-v1",
        "frozen_comparator_model_id": "frozen-comparator-v1",
        "config_sha256": "a" * 64,
        "artifact_sha256s": {"frozen-pa-artifact": "b" * 64},
        "qualification_state": "research_only",
        "betting_authorized": False,
        "supported_markets": ["hits", "home_runs", "total_bases"],
        "row_count": 1,
        "signature_attestation_ref": None,
    }


def publish_fixture(root: Path, *, snapshot: dict[str, Any] | None = None) -> Path:
    payload = canonical_bytes(snapshot if snapshot is not None else valid_snapshot())
    snapshot_path = root / "snapshot.json"
    manifest_path = root / "manifest.json"
    snapshot_path.write_bytes(payload)
    manifest_path.write_bytes(canonical_bytes(valid_manifest(payload)))
    return manifest_path


@pytest.fixture
def snapshot_document() -> dict[str, Any]:
    return copy.deepcopy(valid_snapshot())


@pytest.fixture
def published_snapshot(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path, publish_fixture(tmp_path)
