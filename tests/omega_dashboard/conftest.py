from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

CAPTURED_AT = "2026-07-27T12:00:00Z"
NOW = datetime(2026, 7, 27, 12, 1, 0, tzinfo=timezone.utc)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def bind_row_hash(row: dict[str, Any]) -> None:
    payload = {key: value for key, value in row.items() if key != "row_content_sha256"}
    row["row_content_sha256"] = hashlib.sha256(canonical_bytes(payload)).hexdigest()


def receipt_bindings() -> dict[str, Any]:
    return {
        "identity": [
            {
                "schema_version": "identity-resolution-receipt-v1",
                "sha256": "c" * 64,
                "source_id": "synthetic-identity-source-v1",
                "observed_at_utc": "2026-07-27T11:58:00Z",
                "subject_role": "batter",
                "game_pk": 880001,
                "mlb_player_id": 111,
                "mlb_team_id": 10,
                "mlb_opponent_team_id": 20,
                "player_name": "Synthetic Batter",
                "team_name": "Alpha Club",
                "opponent_name": "Beta Club",
                "identity_state": "RESOLVED",
            },
            {
                "schema_version": "identity-resolution-receipt-v1",
                "sha256": "d" * 64,
                "source_id": "synthetic-identity-source-v1",
                "observed_at_utc": "2026-07-27T11:58:00Z",
                "subject_role": "opposing_starter",
                "game_pk": 880001,
                "mlb_player_id": 222,
                "mlb_team_id": 20,
                "mlb_opponent_team_id": 10,
                "player_name": "Synthetic Pitcher",
                "team_name": "Beta Club",
                "opponent_name": "Alpha Club",
                "identity_state": "RESOLVED",
            },
        ],
        "source": [
            {
                "schema_version": "source-observation-receipt-v1",
                "sha256": "e" * 64,
                "source_id": "synthetic-pregame-source-v1",
                "parser_id": "synthetic-parser-v1",
                "protocol_id": "synthetic-display-protocol-v1",
                "observed_at_utc": "2026-07-27T11:59:00Z",
                "game_pk": 880001,
                "mlb_player_id": 111,
                "mlb_team_id": 10,
                "mlb_opponent_team_id": 20,
            }
        ],
        "chronology": [
            {
                "schema_version": "chronology-decision-receipt-v1",
                "sha256": "f" * 64,
                "source_id": "synthetic-chronology-source-v1",
                "game_pk": 880001,
                "official_slate_date": "2026-07-27",
                "event_start_utc": "2026-07-27T23:00:00Z",
                "decision_horizon_utc": CAPTURED_AT,
                "decision": "ELIGIBLE",
            }
        ],
    }


def valid_snapshot() -> dict[str, Any]:
    row: dict[str, Any] = {
        "row_schema_version": "prediction-display-row-v2",
        "row_content_sha256": "0" * 64,
        "row_id": "game-880001-player-111-hits",
        "game_pk": 880001,
        "player_name": "Synthetic Batter",
        "mlb_player_id": 111,
        "mlb_team_id": 10,
        "mlb_opponent_team_id": 20,
        "team": "Alpha Club",
        "opponent": "Beta Club",
        "identity_state": "RESOLVED",
        "identity_receipt_sha256": "c" * 64,
        "source_receipt_sha256": "e" * 64,
        "chronology_decision_receipt_sha256": "f" * 64,
        "event_start_utc": "2026-07-27T23:00:00Z",
        "decision_horizon_utc": CAPTURED_AT,
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
            "identity_state": "RESOLVED",
            "identity_receipt_sha256": "d" * 64,
            "status": "probable_receipt_verified",
        },
        "point_prediction_kind": "expected_count",
        "point_prediction": 0.8,
        "count_distribution": [
            {"value": 0, "probability": 0.4},
            {"value": 1, "probability": 0.4},
            {"value": 2, "probability": 0.2},
        ],
        "uncertainty": {"method": "display_bound", "lower": 0.6, "upper": 1.1},
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
    bind_row_hash(row)
    return {
        "schema_version": "prediction-display-snapshot-v2",
        "official_slate_date": "2026-07-27",
        "captured_at_utc": CAPTURED_AT,
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
            "reason_codes": ["ALL_REQUIRED_RECEIPTS_VALID"],
            "latest_refresh_utc": CAPTURED_AT,
        },
        "config_sha256": "a" * 64,
        "artifact_sha256s": {"frozen-pa-artifact": "b" * 64},
        "supported_markets": ["hits", "home_runs", "total_bases"],
        "rows": [row],
    }


def valid_manifest(snapshot_bytes: bytes) -> dict[str, Any]:
    return {
        "schema_version": "prediction-display-manifest-v2",
        "trust_mode": "out_of_band_manifest_sha256_v1",
        "snapshot_path": "snapshot.json",
        "snapshot_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        "snapshot_byte_size": len(snapshot_bytes),
        "official_slate_date": "2026-07-27",
        "captured_at_utc": CAPTURED_AT,
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
        "receipt_bindings": receipt_bindings(),
    }


def publish_fixture(
    root: Path,
    *,
    snapshot: dict[str, Any] | None = None,
    mutate_manifest: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[Path, str]:
    payload = canonical_bytes(snapshot if snapshot is not None else valid_snapshot())
    manifest = valid_manifest(payload)
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    manifest_bytes = canonical_bytes(manifest)
    (root / "snapshot.json").write_bytes(payload)
    (root / "manifest.json").write_bytes(manifest_bytes)
    return Path("manifest.json"), hashlib.sha256(manifest_bytes).hexdigest()


def unseal_tree(root: Path) -> None:
    if os.name == "posix":
        for path in sorted(root.rglob("*"), reverse=True):
            path.chmod(0o755 if path.is_dir() else 0o644)
        root.chmod(0o755)


@pytest.fixture
def snapshot_document() -> dict[str, Any]:
    return copy.deepcopy(valid_snapshot())


@pytest.fixture
def synthetic_source_permission(monkeypatch):
    """Bypass only OS ownership setup; all path/identity/hash gates remain live."""
    import dashboard.snapshot_store as module

    monkeypatch.setattr(module, "_assert_not_writable", lambda *args, **kwargs: None)


@pytest.fixture
def published_snapshot(tmp_path: Path):
    manifest, digest = publish_fixture(tmp_path)
    return tmp_path, manifest, digest
