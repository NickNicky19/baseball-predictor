from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from src.evaluation import full_game_opportunity_cross_envelope_v1 as cross


ROOT = Path(__file__).resolve().parents[1]
H = "a" * 64
H2 = "b" * 64
H3 = "c" * 64


def _rehash(value: dict) -> dict:
    value["batter"]["pa_pmf_sha256"] = cross.sha256_value(value["batter"]["pa_pmf"])
    value["starter"]["starter_removal_pmf_sha256"] = cross.sha256_value(
        value["starter"]["starter_removal_pmf"]
    )
    value["bullpen_transition_boundary"]["starter_removal_pmf_sha256"] = value[
        "starter"
    ]["starter_removal_pmf_sha256"]
    unsigned = {key: item for key, item in value.items() if key != "envelope_sha256"}
    value["envelope_sha256"] = cross.sha256_value(unsigned)
    return value


def _valid() -> dict:
    value = {
        "schema_version": cross.SCHEMA_VERSION,
        "research_only": True,
        "betting_authorized": False,
        "production_probability_consumption_authorized": False,
        "authority_state": cross.UNBOUND,
        "target": {
            "official_game_date": "2026-07-28",
            "mlb_game_pk": 777001,
            "official_start_utc": "2026-07-28T23:00:00Z",
            "target_horizon_utc": "2026-07-28T19:00:00Z",
            "batter_side": "away",
            "batting_team_id": 10,
            "pitching_side": "home",
            "pitching_team_id": 20,
            "batter_opponent_team_id": 20,
            "pitcher_opponent_team_id": 10,
        },
        "release_bindings": {
            "pr32_source_commit": cross.PR32_COMMIT,
            "pr32_candidate_id": cross.PR32_CANDIDATE_ID,
            "pr32_protocol_sha256": H,
            "pr32_source_manifest_sha256": H2,
            "pr32_runtime_release_receipt_sha256": H3,
            "pr33_source_commit": cross.PR33_COMMIT,
            "pr35_source_commit": cross.PR35_COMMIT,
            "pr35_candidate_id": cross.PR35_CANDIDATE_ID,
            "pr35_protocol_sha256": cross.PR35_PROTOCOL_SHA256,
            "pr35_evidence_authority_receipt_sha256": H,
            "pr35_runtime_release_receipt_sha256": H2,
            "pr35_model_authorization_receipt_sha256": H3,
            "cross_envelope_authority_receipt_sha256": None,
        },
        "batter": {
            "side_bundle_sha256": H,
            "candidate_record_sha256": H2,
            "plan_sha256": H3,
            "target_id": "777001:away:T-4",
            "official_game_date": "2026-07-28",
            "mlb_game_pk": 777001,
            "official_start_utc": "2026-07-28T23:00:00Z",
            "target_horizon_utc": "2026-07-28T19:00:00Z",
            "prediction_generated_at_utc": "2026-07-28T18:59:30Z",
            "side": "away",
            "team_id": 10,
            "player_id": 1001,
            "projected_lineup_content_sha256": H,
            "active_roster_receipt_sha256": H2,
            "history_receipt_manifest_sha256": H3,
            "stats_transport_receipt_sha256s": [H, H2],
            "source_kinds": {
                "schedule": "official_mlb_schedule_t4",
                "active_roster": "official_mlb_active_roster_t4",
                "lineup_history": "official_mlb_final_lineups_strictly_prior",
                "batter_stats": "official_mlb_statsapi_dated_game_log_segmented_v2",
            },
            "actual_target_lineup_consumed": False,
            "fallback_used": False,
            "pa_pmf": [
                {"pa": 0, "probability": 0.1},
                {"pa": 3, "probability": 0.2},
                {"pa": 4, "probability": 0.5},
                {"pa": 5, "probability": 0.2},
            ],
            "pa_pmf_sha256": "",
        },
        "starter": {
            "target_id": "777001:home:T-4",
            "official_game_date": "2026-07-28",
            "mlb_game_pk": 777001,
            "official_start_utc": "2026-07-28T23:00:00Z",
            "target_horizon_utc": "2026-07-28T19:00:00Z",
            "pitching_side": "home",
            "pitching_team_id": 20,
            "opposing_team_id": 10,
            "probable_pitcher_mlb_id": 2001,
            "starter_identity_source": "receipt_proven_probable_starter_t4",
            "plan_sha256": H3,
            "plan_source_receipt_sha256": H,
            "ledger_manifest_sha256": H2,
            "ledger_terminal_record_sha256": H3,
            "context_sha256": H,
            "context_raw_payload_sha256": H2,
            "plan_received_at_utc": "2026-07-28T18:58:00Z",
            "context_received_at_utc": "2026-07-28T18:59:00Z",
            "workload_assembled_at_utc": "2026-07-28T18:57:00Z",
            "workload_observation_cutoff_utc": "2026-07-28T18:56:00Z",
            "workload_max_source_game_date": "2026-07-27",
            "workload_raw_receipt_manifest_sha256": H3,
            "workload_feature_artifact_sha256": H,
            "workload_parser_code_sha256": H2,
            "workload_source_schema_sha256": H3,
            "workload_feature_code_sha256": H,
            "source_kinds": {
                "plan": "mlb_statsapi_schedule",
                "context": "forward_pitcher_context_v2_t4",
                "workload": "official_mlb_pitching_game_feed_strictly_prior",
            },
            "may_2026_interval_excluded": True,
            "may_2026_accessed": False,
            "actual_postgame_starter_consumed": False,
            "fallback_used": False,
            "starter_removal_pmf": [
                {"batters_faced": 18, "outs_recorded": 15, "probability": 0.4},
                {"batters_faced": 22, "outs_recorded": 18, "probability": 0.6},
            ],
            "starter_removal_pmf_sha256": "",
        },
        "bullpen_transition_boundary": {
            "status": "TRANSITION_ONLY_QUALITY_NOT_MODELED",
            "transition_source": "starter_removal_pmf",
            "starter_removal_pmf_sha256": "",
            "bullpen_quality_model": None,
            "bullpen_identity_receipts": [],
            "actual_postgame_relievers_consumed": False,
            "fallback_used": False,
            "probability_consumption_authorized": False,
        },
        "protected_boundaries": dict(cross._PROTECTED),
        "evidence_references": [],
        "envelope_sha256": "",
    }
    single = {
        "pr32_protocol": value["release_bindings"]["pr32_protocol_sha256"],
        "pr32_source_manifest": value["release_bindings"]["pr32_source_manifest_sha256"],
        "pr32_runtime_release_receipt": value["release_bindings"]["pr32_runtime_release_receipt_sha256"],
        "pr35_protocol": value["release_bindings"]["pr35_protocol_sha256"],
        "pr35_evidence_authority_receipt": value["release_bindings"]["pr35_evidence_authority_receipt_sha256"],
        "pr35_runtime_release_receipt": value["release_bindings"]["pr35_runtime_release_receipt_sha256"],
        "pr35_model_authorization_receipt": value["release_bindings"]["pr35_model_authorization_receipt_sha256"],
        "batter_side_bundle": value["batter"]["side_bundle_sha256"],
        "batter_candidate_record": value["batter"]["candidate_record_sha256"],
        "shared_plan": value["batter"]["plan_sha256"],
        "batter_projected_lineup": value["batter"]["projected_lineup_content_sha256"],
        "batter_active_roster_receipt": value["batter"]["active_roster_receipt_sha256"],
        "batter_history_receipt_manifest": value["batter"]["history_receipt_manifest_sha256"],
        "starter_plan_source_receipt": value["starter"]["plan_source_receipt_sha256"],
        "starter_ledger_manifest": value["starter"]["ledger_manifest_sha256"],
        "starter_ledger_terminal_record": value["starter"]["ledger_terminal_record_sha256"],
        "starter_context": value["starter"]["context_sha256"],
        "starter_context_raw_payload": value["starter"]["context_raw_payload_sha256"],
        "starter_workload_raw_receipt_manifest": value["starter"]["workload_raw_receipt_manifest_sha256"],
        "starter_workload_feature_artifact": value["starter"]["workload_feature_artifact_sha256"],
        "starter_workload_parser_code": value["starter"]["workload_parser_code_sha256"],
        "starter_workload_source_schema": value["starter"]["workload_source_schema_sha256"],
        "starter_workload_feature_code": value["starter"]["workload_feature_code_sha256"],
    }
    value["evidence_references"] = [
        {"role": role, "relative_path": f"evidence/{index:02d}-{role}.json", "sha256": digest}
        for index, (role, digest) in enumerate(single.items())
    ]
    value["evidence_references"].extend(
        {
            "role": "batter_stats_transport_receipt",
            "relative_path": f"evidence/stats-{index}.json",
            "sha256": digest,
        }
        for index, digest in enumerate(value["batter"]["stats_transport_receipt_sha256s"])
    )
    return _rehash(value)


def _materialize_references(
    value: dict, root: Path, *, overrides: dict[str, dict] | None = None
) -> dict:
    overrides = overrides or {}
    references = value["evidence_references"]
    by_role: dict[str, list[dict]] = {}
    for reference in references:
        by_role.setdefault(reference["role"], []).append(reference)

    def write(reference: dict, payload: object, *, raw: bytes | None = None) -> str:
        encoded = raw if raw is not None else json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        path = root / reference["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        digest = hashlib.sha256(encoded).hexdigest()
        reference["sha256"] = digest
        return digest

    def self_hashed(payload: dict, field: str) -> tuple[dict, str]:
        digest = cross.sha256_value(payload)
        return {**payload, field: digest}, digest

    # Establish raw-byte authorities first.  The PR35 protocol has a frozen
    # digest, so the test fixture uses the exact repository bytes.
    pr35_protocol = (ROOT / "config/pitcher_joint_opportunity_v1_protocol.json").read_bytes()
    direct = {
        "pr32_protocol": ("release_bindings", "pr32_protocol_sha256", b'{"protocol":"pr32-test"}'),
        "pr32_source_manifest": ("release_bindings", "pr32_source_manifest_sha256", b'{"manifest":"pr32-test"}'),
        "pr35_protocol": ("release_bindings", "pr35_protocol_sha256", pr35_protocol),
        "pr35_evidence_authority_receipt": (
            "release_bindings",
            "pr35_evidence_authority_receipt_sha256",
            json.dumps(
                {
                    "schema_version": "pitcher-joint-opportunity-evidence-authority-v1",
                    "candidate_id": cross.PR35_CANDIDATE_ID,
                    "status": "UNBOUND_NO_APPROVED_ARCHIVE_ERA",
                    "authorized_evidence_authority_receipt_sha256": None,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        ),
        "starter_ledger_manifest": ("starter", "ledger_manifest_sha256", b'{"ledger":"test"}'),
        "starter_context_raw_payload": ("starter", "context_raw_payload_sha256", b'{"raw":"context"}'),
        "starter_workload_parser_code": ("starter", "workload_parser_code_sha256", b"# parser test bytes\n"),
        "starter_workload_source_schema": ("starter", "workload_source_schema_sha256", b'{"schema":"workload-test"}'),
        "starter_workload_feature_code": ("starter", "workload_feature_code_sha256", b"# feature test bytes\n"),
    }
    for role, (section, field, raw) in direct.items():
        digest = write(by_role[role][0], {}, raw=raw)
        value[section][field] = digest

    shared_plan, plan_digest = self_hashed(
        {
            "schema_version": "shadow-capture-plan-v1",
            "official_game_date": value["target"]["official_game_date"],
            "mlb_game_pk": value["target"]["mlb_game_pk"],
            "entry_hours": 4,
        },
        "plan_sha256",
    )
    value["batter"]["plan_sha256"] = plan_digest
    value["starter"]["plan_sha256"] = plan_digest
    write(by_role["shared_plan"][0], shared_plan)

    expected_payloads = {
        "pr32_runtime_release_receipt": {
            "schema_version": "shared-pa-projected-opportunity-runtime-release-v2",
            "candidate_id": cross.PR32_CANDIDATE_ID,
            "source_commit": cross.PR32_COMMIT,
            "source_manifest_sha256": value["release_bindings"]["pr32_source_manifest_sha256"],
            "candidate_protocol_sha256": value["release_bindings"]["pr32_protocol_sha256"],
            "research_only": True,
            "betting_authorized": False,
        },
        "pr35_runtime_release_receipt": {
            "schema_version": "pitcher-joint-opportunity-runtime-release-v1",
            "candidate_id": cross.PR35_CANDIDATE_ID,
            "protocol_sha256": cross.PR35_PROTOCOL_SHA256,
            "research_only": True,
            "betting_authorized": False,
        },
        "pr35_model_authorization_receipt": {
            "schema_version": "pitcher-joint-opportunity-model-authorization-v1",
            "candidate_id": cross.PR35_CANDIDATE_ID,
            "protocol_sha256": cross.PR35_PROTOCOL_SHA256,
            "research_only": True,
            "betting_authorized": False,
        },
        "batter_side_bundle": {
            "schema_version": "shared-pa-projected-opportunity-side-bundle-v2",
            "candidate_id": cross.PR32_CANDIDATE_ID,
            "source_release_commit": cross.PR32_COMMIT,
            "plan_sha256": value["batter"]["plan_sha256"],
            "target_id": value["batter"]["target_id"],
            "official_game_date": value["target"]["official_game_date"],
            "mlb_game_pk": value["target"]["mlb_game_pk"],
            "target_horizon_utc": value["target"]["target_horizon_utc"],
            "team_id": value["batter"]["team_id"],
            "side": value["batter"]["side"],
            "research_only": True,
            "betting_authorized": False,
        },
        "batter_candidate_record": {
            "schema_version": "shared-pa-projected-opportunity-player-v2",
            "candidate_id": cross.PR32_CANDIDATE_ID,
            "source_release_commit": cross.PR32_COMMIT,
            "plan_sha256": value["batter"]["plan_sha256"],
            "target_id": value["batter"]["target_id"],
            "official_game_date": value["target"]["official_game_date"],
            "mlb_game_pk": value["target"]["mlb_game_pk"],
            "target_horizon_utc": value["target"]["target_horizon_utc"],
            "player_id": value["batter"]["player_id"],
            "team_id": value["batter"]["team_id"],
            "side": value["batter"]["side"],
            "stats_source": "official_mlb_statsapi_dated_game_log_segmented_v2",
            "research_only": True,
            "betting_authorized": False,
        },
        "batter_projected_lineup": {"projected_lineup": [1001]},
        "batter_active_roster_receipt": {"active_roster_player_ids": [1001]},
        "batter_history_receipt_manifest": {"prior_lineup_receipts": []},
        "starter_plan_source_receipt": {
            "schema_version": "aws-pitcher-receipt-plan-receipt-v1",
            "official_game_date": value["target"]["official_game_date"],
            "mlb_game_pk": value["target"]["mlb_game_pk"],
            "plan_sha256": value["starter"]["plan_sha256"],
            "source_name": "mlb_statsapi_schedule",
            "received_at_utc": value["starter"]["plan_received_at_utc"],
            "research_only": True,
            "betting_authorized": False,
            "model_or_market_accessed": False,
        },
        "starter_ledger_terminal_record": {
            "schema_version": "forward-pitcher-context-ledger-record-v1",
            "target_id": value["starter"]["target_id"],
            "plan_sha256": value["starter"]["plan_sha256"],
            "terminal_state": "captured",
            "context_sha256": value["starter"]["context_sha256"],
            "raw_payload_sha256": value["starter"]["context_raw_payload_sha256"],
        },
        "starter_context": {
            "schema_version": "forward-pitcher-context-v2",
            "target_id": value["starter"]["target_id"],
            "plan_sha256": value["starter"]["plan_sha256"],
            "source_name": "mlb_statsapi_schedule",
            "mlb_game_pk": value["target"]["mlb_game_pk"],
            "official_game_date": value["target"]["official_game_date"],
            "official_start_time_utc": value["target"]["official_start_utc"],
        },
        "starter_workload_raw_receipt_manifest": {"strictly_prior_receipts": []},
        "starter_workload_feature_artifact": {
            "schema_version": "pitcher-pit-workload-history-v1",
            "source_kind": "official_mlb_pitching_game_log_point_in_time",
            "target_id": value["starter"]["target_id"],
            "official_game_date": value["target"]["official_game_date"],
            "mlb_game_pk": value["target"]["mlb_game_pk"],
            "pitcher_id": value["starter"]["probable_pitcher_mlb_id"],
            "pitching_team_id": value["starter"]["pitching_team_id"],
            "target_horizon_utc": value["target"]["target_horizon_utc"],
            "lineage": {},
        },
    }

    # Canonical-hash payloads have no embedded self-hash.
    canonical_roles = {
        "batter_active_roster_receipt": ("batter", "active_roster_receipt_sha256"),
        "batter_history_receipt_manifest": ("batter", "history_receipt_manifest_sha256"),
        "starter_workload_raw_receipt_manifest": ("starter", "workload_raw_receipt_manifest_sha256"),
    }
    for role, (section, field) in canonical_roles.items():
        payload = copy.deepcopy(expected_payloads[role])
        payload.update(overrides.get(role, {}))
        value[section][field] = cross.sha256_value(payload)
        write(by_role[role][0], payload)

    stats_digests = []
    for occurrence, reference in enumerate(by_role["batter_stats_transport_receipt"]):
        payload = {"receipt_sequence": occurrence, "transport": "official-mlb-test"}
        payload.update(overrides.get("batter_stats_transport_receipt", {}))
        stats_digests.append(cross.sha256_value(payload))
        write(reference, payload)
    value["batter"]["stats_transport_receipt_sha256s"] = stats_digests

    self_roles = {
        "pr32_runtime_release_receipt": ("release_receipt_sha256", "release_bindings", "pr32_runtime_release_receipt_sha256"),
        "pr35_runtime_release_receipt": ("release_sha256", "release_bindings", "pr35_runtime_release_receipt_sha256"),
        "pr35_model_authorization_receipt": ("authorization_sha256", "release_bindings", "pr35_model_authorization_receipt_sha256"),
        "batter_side_bundle": ("side_bundle_sha256", "batter", "side_bundle_sha256"),
        "batter_candidate_record": ("candidate_record_sha256", "batter", "candidate_record_sha256"),
        "batter_projected_lineup": ("projection_content_sha256", "batter", "projected_lineup_content_sha256"),
        "starter_plan_source_receipt": ("receipt_sha256", "starter", "plan_source_receipt_sha256"),
        "starter_ledger_terminal_record": ("chain_sha256", "starter", "ledger_terminal_record_sha256"),
        "starter_context": ("context_sha256", "starter", "context_sha256"),
        "starter_workload_feature_artifact": ("workload_sha256", "starter", "workload_feature_artifact_sha256"),
    }
    # Workload lineage is declared by exact raw/canonical bindings above.
    expected_payloads["starter_workload_feature_artifact"]["lineage"] = {
        "raw_receipt_manifest_sha256": value["starter"]["workload_raw_receipt_manifest_sha256"],
        "parser_code_sha256": value["starter"]["workload_parser_code_sha256"],
        "source_schema_sha256": value["starter"]["workload_source_schema_sha256"],
        "feature_code_sha256": value["starter"]["workload_feature_code_sha256"],
        "protocol_sha256": cross.PR35_PROTOCOL_SHA256,
    }
    for role, (self_field, section, field) in self_roles.items():
        payload = copy.deepcopy(expected_payloads[role])
        payload.update(overrides.get(role, {}))
        hashed, digest = self_hashed(payload, self_field)
        value[section][field] = digest
        write(by_role[role][0], hashed)

    # The terminal record binds the final context identity, so regenerate it
    # after context hashing.
    terminal = copy.deepcopy(expected_payloads["starter_ledger_terminal_record"])
    terminal["context_sha256"] = value["starter"]["context_sha256"]
    terminal.update(overrides.get("starter_ledger_terminal_record", {}))
    terminal_hashed, terminal_digest = self_hashed(terminal, "chain_sha256")
    value["starter"]["ledger_terminal_record_sha256"] = terminal_digest
    write(by_role["starter_ledger_terminal_record"][0], terminal_hashed)

    value["bullpen_transition_boundary"]["starter_removal_pmf_sha256"] = value["starter"]["starter_removal_pmf_sha256"]
    return _rehash(value)


def _put_role_first(value: dict, role: str) -> None:
    matching = [row for row in value["evidence_references"] if row["role"] == role]
    value["evidence_references"] = matching + [
        row for row in value["evidence_references"] if row["role"] != role
    ]
    _rehash(value)


def test_unbound_validator_never_returns_pmfs_or_accepts_hash_only_evidence() -> None:
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="hash-only evidence"):
        cross.validate_cross_envelope(_valid())


def test_complete_retained_bytes_still_fail_closed_without_pr32_replay_contract(
    tmp_path: Path,
) -> None:
    value = _materialize_references(_valid(), tmp_path)
    with pytest.raises(
        cross.FullGameOpportunityEnvelopeError,
        match="PR32 retained-evidence replay contract is unavailable",
    ):
        cross.validate_cross_envelope(value, evidence_root=tmp_path)


def test_nonexistent_or_fabricated_receipt_cannot_be_replaced_by_a_hash(
    tmp_path: Path,
) -> None:
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="unavailable"):
        cross.validate_cross_envelope(_valid(), evidence_root=tmp_path)


def test_retained_semantic_identity_mismatch_fails_before_external_authority(
    tmp_path: Path,
) -> None:
    value = _materialize_references(
        _valid(), tmp_path, overrides={"batter_side_bundle": {"mlb_game_pk": 999999}}
    )
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="semantic target identity"):
        cross.validate_cross_envelope(value, evidence_root=tmp_path)


def test_may_in_retained_stats_receipt_fails_even_for_july_target(tmp_path: Path) -> None:
    value = _materialize_references(
        _valid(),
        tmp_path,
        overrides={
            "batter_stats_transport_receipt": {
                "request_url": "https://statsapi.mlb.com/api/v1/people/1001/stats?startDate=2026-05-01&endDate=2026-05-31"
            }
        },
    )
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="sealed May 2026"):
        cross.validate_cross_envelope(value, evidence_root=tmp_path)


def test_may_in_retained_history_path_fails_even_for_july_target(tmp_path: Path) -> None:
    value = _materialize_references(_valid(), tmp_path)
    reference = next(
        row for row in value["evidence_references"]
        if row["role"] == "batter_history_receipt_manifest"
    )
    old_path = tmp_path / reference["relative_path"]
    reference["relative_path"] = "evidence/history/2026-05/manifest.json"
    new_path = tmp_path / reference["relative_path"]
    new_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.write_bytes(old_path.read_bytes())
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="sealed May 2026"):
        cross.validate_cross_envelope(value, evidence_root=tmp_path)


@pytest.mark.parametrize(
    ("section", "field", "may_alias"),
    [
        ("target", "official_game_date", "2026.05.01"),
        ("target", "official_game_date", "05.31.2026"),
        ("target", "official_start_utc", "2026.05.01T23:00:00Z"),
        ("target", "target_horizon_utc", "05.31.2026T19:00:00Z"),
        ("batter", "prediction_generated_at_utc", "2026.05.01T18:59:30Z"),
        ("starter", "workload_max_source_game_date", "05.31.2026"),
    ],
)
def test_may_aliases_in_envelope_are_explicitly_sealed(
    section: str, field: str, may_alias: str
) -> None:
    value = _valid()
    value[section][field] = may_alias
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="sealed May 2026"):
        cross.validate_cross_envelope(value)


@pytest.mark.parametrize("may_alias", ["2026.05.01", "05.31.2026", "May/31/2026"])
def test_may_aliases_in_retained_payload_are_explicitly_sealed(
    tmp_path: Path, may_alias: str
) -> None:
    value = _materialize_references(
        _valid(),
        tmp_path,
        overrides={"batter_stats_transport_receipt": {"source_date": may_alias}},
    )
    _put_role_first(value, "batter_stats_transport_receipt")
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="sealed May 2026"):
        cross.validate_cross_envelope(value, evidence_root=tmp_path)


@pytest.mark.parametrize(
    ("role", "section", "field", "message"),
    [
        (
            "pr32_protocol",
            "release_bindings",
            "pr32_protocol_sha256",
            "raw-byte digest differs from envelope",
        ),
        (
            "pr32_runtime_release_receipt",
            "release_bindings",
            "pr32_runtime_release_receipt_sha256",
            "semantic digest differs from envelope",
        ),
        (
            "batter_side_bundle",
            "batter",
            "side_bundle_sha256",
            "semantic digest differs from envelope",
        ),
        (
            "starter_context",
            "starter",
            "context_sha256",
            "semantic digest differs from envelope",
        ),
        (
            "starter_workload_feature_artifact",
            "starter",
            "workload_feature_artifact_sha256",
            "semantic digest differs from envelope",
        ),
    ],
)
def test_reference_bytes_cannot_be_rebound_to_a_different_declared_identity(
    tmp_path: Path, role: str, section: str, field: str, message: str
) -> None:
    value = _materialize_references(_valid(), tmp_path)
    _put_role_first(value, role)
    value[section][field] = "f" * 64
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match=message):
        cross.validate_cross_envelope(value, evidence_root=tmp_path)


def test_stats_receipt_semantic_digest_order_is_exact(tmp_path: Path) -> None:
    value = _materialize_references(_valid(), tmp_path)
    _put_role_first(value, "batter_stats_transport_receipt")
    value["batter"]["stats_transport_receipt_sha256s"].reverse()
    _rehash(value)
    with pytest.raises(
        cross.FullGameOpportunityEnvelopeError,
        match="digest set/order differs from envelope",
    ):
        cross.validate_cross_envelope(value, evidence_root=tmp_path)


def test_ledger_terminal_record_must_bind_context_and_raw_payload(tmp_path: Path) -> None:
    value = _materialize_references(
        _valid(),
        tmp_path,
        overrides={"starter_ledger_terminal_record": {"context_sha256": "f" * 64}},
    )
    _put_role_first(value, "starter_ledger_terminal_record")
    with pytest.raises(
        cross.FullGameOpportunityEnvelopeError,
        match="ledger terminal semantic identity/lineage differs",
    ):
        cross.validate_cross_envelope(value, evidence_root=tmp_path)


def test_evidence_root_ancestor_reparse_is_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    value = _materialize_references(_valid(), real_root)
    linked_root = tmp_path / "linked"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this host")
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="redirected"):
        cross.validate_cross_envelope(value, evidence_root=linked_root)


def test_evidence_root_ancestor_reparse_mutation_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _materialize_references(_valid(), tmp_path)
    poisoned_ancestor = tmp_path.absolute().parent
    original = cross._is_link_or_reparse
    monkeypatch.setattr(
        cross,
        "_is_link_or_reparse",
        lambda path: path == poisoned_ancestor or original(path),
    )
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="ancestor is redirected"):
        cross.validate_cross_envelope(value, evidence_root=tmp_path)


def test_public_consumer_abstains_before_probability_consumption() -> None:
    result = cross.consume_cross_envelope(root=ROOT, envelope={"malformed": "ignored"})
    assert result["terminal_state"] == "abstained"
    assert result["reason_code"] == "EXTERNAL_AUTHORITY_UNBOUND"
    assert result["batter_pa_pmf"] is None
    assert result["starter_removal_pmf"] is None


@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        (("batter", "mlb_game_pk"), 777002, "batter bundle target identity"),
        (("starter", "pitching_team_id"), 21, "starter bundle target identity"),
        (("batter", "official_game_date"), "2026-07-27", "batter bundle target identity"),
        (("starter", "target_horizon_utc"), "2026-07-28T18:59:59Z", "starter bundle target identity"),
        (("release_bindings", "pr32_source_commit"), "d" * 40, "exact release identity"),
        (("release_bindings", "pr35_source_commit"), "e" * 40, "exact release identity"),
        (("batter", "source_kinds"), {"schedule": "swapped"}, "batter source identity"),
        (("starter", "source_kinds"), {"plan": "swapped"}, "starter source identity"),
    ],
)
def test_cross_identity_release_and_source_swaps_fail(path, replacement, message) -> None:
    value = _valid()
    value[path[0]][path[1]] = replacement
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match=message):
        cross.validate_cross_envelope(value)


@pytest.mark.parametrize("field", ["actual_postgame_starter_consumed", "fallback_used"])
def test_actual_starter_or_fallback_fails(field: str) -> None:
    value = _valid()
    value["starter"][field] = True
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="fallback, actual identity"):
        cross.validate_cross_envelope(value)


def test_actual_starter_source_alias_fails() -> None:
    value = _valid()
    value["starter"]["starter_identity_source"] = "actual_boxscore_starter"
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="receipt-proven"):
        cross.validate_cross_envelope(value)


def test_missing_bullpen_boundary_fails() -> None:
    value = _valid()
    del value["bullpen_transition_boundary"]
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="surface changed"):
        cross.validate_cross_envelope(value)


def test_missing_workload_identity_fails() -> None:
    value = _valid()
    del value["starter"]["workload_raw_receipt_manifest_sha256"]
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="starter bundle surface"):
        cross.validate_cross_envelope(value)


@pytest.mark.parametrize("section", ["batter", "starter"])
def test_noncanonical_target_identity_fails(section: str) -> None:
    value = _valid()
    value[section]["target_id"] = "777001:swapped:T-4"
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="target identity is not canonical"):
        cross.validate_cross_envelope(value)


def test_duplicate_receipt_identity_fails() -> None:
    value = _valid()
    value["batter"]["stats_transport_receipt_sha256s"] = [H, H]
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="receipt identity is duplicated"):
        cross.validate_cross_envelope(value)


@pytest.mark.parametrize("which", ["batter", "starter"])
def test_duplicate_state_identity_fails(which: str) -> None:
    value = _valid()
    key = "pa_pmf" if which == "batter" else "starter_removal_pmf"
    value[which][key].append(copy.deepcopy(value[which][key][0]))
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="duplicate state identity"):
        cross.validate_cross_envelope(value)


def test_may_2026_target_fails_before_any_probability() -> None:
    value = _valid()
    value["target"]["official_game_date"] = "2026-05-15"
    value["target"]["official_start_utc"] = "2026-05-15T23:00:00Z"
    value["target"]["target_horizon_utc"] = "2026-05-15T19:00:00Z"
    for section in ("batter", "starter"):
        value[section]["official_game_date"] = "2026-05-15"
        value[section]["official_start_utc"] = "2026-05-15T23:00:00Z"
        value[section]["target_horizon_utc"] = "2026-05-15T19:00:00Z"
    value["batter"]["prediction_generated_at_utc"] = "2026-05-15T18:59:00Z"
    value["starter"]["plan_received_at_utc"] = "2026-05-15T18:58:00Z"
    value["starter"]["context_received_at_utc"] = "2026-05-15T18:59:00Z"
    value["starter"]["workload_assembled_at_utc"] = "2026-05-15T18:57:00Z"
    value["starter"]["workload_observation_cutoff_utc"] = "2026-05-15T18:56:00Z"
    value["starter"]["workload_max_source_game_date"] = "2026-04-30"
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="May 2026"):
        cross.validate_cross_envelope(value)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("batter", "prediction_generated_at_utc"),
        ("starter", "plan_received_at_utc"),
        ("starter", "context_received_at_utc"),
        ("starter", "workload_assembled_at_utc"),
        ("starter", "workload_observation_cutoff_utc"),
    ],
)
def test_post_start_or_post_horizon_data_fails(section: str, field: str) -> None:
    value = _valid()
    value[section][field] = "2026-07-28T23:00:01Z"
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="post-horizon|postdates T-4"):
        cross.validate_cross_envelope(value)


def test_workload_same_date_fails() -> None:
    value = _valid()
    value["starter"]["workload_max_source_game_date"] = "2026-07-28"
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="strictly prior"):
        cross.validate_cross_envelope(value)


def test_workload_may_source_fails_even_when_target_is_later() -> None:
    value = _valid()
    value["starter"]["workload_max_source_game_date"] = "2026-05-31"
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="sealed May 2026"):
        cross.validate_cross_envelope(value)


def test_probability_mass_is_never_normalized_or_clipped() -> None:
    value = _valid()
    value["batter"]["pa_pmf"][0]["probability"] = 0.2
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="no normalization"):
        cross.validate_cross_envelope(value)


def test_bullpen_quality_or_identity_cannot_be_smuggled_in() -> None:
    value = _valid()
    value["bullpen_transition_boundary"]["bullpen_quality_model"] = {"league_average": 0.5}
    _rehash(value)
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="bullpen transition-only"):
        cross.validate_cross_envelope(value)


def test_envelope_hash_mutation_fails() -> None:
    value = _valid()
    value["envelope_sha256"] = "f" * 64
    with pytest.raises(cross.FullGameOpportunityEnvelopeError, match="hash differs"):
        cross.validate_cross_envelope(value)


def test_schema_and_authority_are_inert_and_exact() -> None:
    schema = json.loads((ROOT / "config/schemas/full_game_opportunity_cross_envelope_v1.schema.json").read_text(encoding="utf-8"))
    assert schema["$id"] == cross.SCHEMA_VERSION
    for section in (
        "target", "release_bindings", "batter", "starter",
        "bullpen_transition_boundary", "protected_boundaries",
    ):
        assert schema["properties"][section]["additionalProperties"] is False
        assert schema["properties"][section]["required"]
    assert schema["properties"]["evidence_references"]["items"] == {
        "$ref": "#/$defs/reference"
    }
    assert schema["$defs"]["reference"]["additionalProperties"] is False
    authority = cross.load_authority(ROOT)
    assert authority["status"] == cross.UNBOUND
    assert authority["public_probability_consumption_authorized"] is False
