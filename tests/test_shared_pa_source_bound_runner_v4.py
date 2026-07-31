from __future__ import annotations

from pathlib import Path

import pytest

import src.evaluation.shared_pa_source_bound_runner_v4 as subject
from src.evaluation.shared_pa_forward_evidence import sha256_value


def test_v4_runner_replays_raw_boundary_before_hierarchical_builder(
    monkeypatch: pytest.MonkeyPatch,
):
    upstream_unsigned = {
        "source_manifest_sha256": "a" * 64,
        "source_release_commit": "b" * 40,
        "runtime_release_receipt_sha256": "c" * 64,
        "support_player_ids": [101],
        "candidate_records": [{"player_id": 101}],
        "abstentions": [],
    }
    upstream = {
        **upstream_unsigned,
        "side_bundle_sha256": sha256_value(upstream_unsigned),
    }
    calls: list[str] = []
    monkeypatch.setattr(
        subject, "build_side_candidate_bundle_v2",
        lambda **kwargs: calls.append("v2_raw_replay") or upstream,
    )
    monkeypatch.setattr(
        subject, "replay_projected_context_v2",
        lambda **kwargs: {
            "replayed_projection": {"projection_content_sha256": "d" * 64}
        },
    )
    monkeypatch.setattr(
        subject, "load_contract", lambda path: object()
    )
    monkeypatch.setattr(
        subject, "validate_projection", lambda value, contract: {"valid": True}
    )
    monkeypatch.setattr(
        subject, "build_evidence_envelope_v2",
        lambda **kwargs: {
            "evidence_envelope_sha256": "e" * 64,
            "player_id": 101,
        },
    )
    monkeypatch.setattr(
        subject, "build_source_bound_candidate_record_v4",
        lambda **kwargs: calls.append("v4_hierarchical_builder") or {
            "player_id": 101, "candidate_record_sha256": "f" * 64
        },
    )

    class Target:
        target_id = "target"
        official_game_date = "2026-07-31"
        mlb_game_pk = 123
        entry_target_at_utc = "2026-07-31T20:10:00Z"

    class Plan:
        plan_sha256 = "1" * 64

    class Protocol:
        sha256 = "2" * 64

    class Batch:
        responses = {101: [object()]}
        errors = {}

    batch = Batch()
    result = subject.build_side_candidate_bundle_v4(
        root=Path("."), v2_protocol=object(), parent_protocol=object(),
        hierarchical_protocol=Protocol(), authority_arguments={}, plan=Plan(),
        target=Target(), side="home", team_id=117, schedule_response=object(),
        active_roster_receipt={}, active_roster_raw=b"{}", history_records=[],
        history_raw_by_sha256={}, history_coverage={},
        history_schedule_raw_by_sha256={}, opportunity_snapshot={},
        projected_lineup_record={}, stats_batch=batch,
        prediction_generated_at_utc="2026-07-31T20:00:00Z",
        loaded_forward_contract={},
    )
    assert calls == ["v2_raw_replay", "v4_hierarchical_builder"]
    assert result["schema_version"] == subject.SCHEMA_VERSION
    assert result["candidate_records"][0]["player_id"] == 101
    unsigned = dict(result)
    unsigned.pop("side_bundle_sha256")
    assert result["side_bundle_sha256"] == sha256_value(unsigned)
