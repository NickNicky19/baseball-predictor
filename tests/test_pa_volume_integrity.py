from __future__ import annotations

import csv
import gzip
import hashlib
import json

import pandas as pd
import pytest

from src.evaluation.pa_volume_chronology import SOURCE_COLUMNS, fit_2023_pa_volume
from src.evaluation.pa_volume_market_adjudication import (
    EVALUATION_COLUMNS,
    read_2023_2024_without_2025_outcomes,
)
from src.evaluation.prediction_health import health_for_bundle
from src.features.feature_vector import FeatureVectorBuilder
from src.features.pa_volume_gate import (
    PAVolumeAuthorization,
    PAVolumeDistributionArtifact,
    PAVolumeGateError,
    load_pa_volume_artifact,
    resolve_pa_volume_inputs,
)
from src.models.dataclasses import (
    GameContext,
    HitterGameContext,
    MatchupContext,
    ParkFactors,
    PlayerFeatureBundle,
    PlayerIdentity,
    StatcastProfile,
    WeatherContext,
)
from src.prediction.prop_engine import PropEngine
from src.simulation.game_simulator import GameSimulator, GameSimulatorInput


def _artifact() -> PAVolumeDistributionArtifact:
    by_slot = {slot: ({0: 0.5, 6: 0.5} if slot == 1 else {3: 1.0}) for slot in range(1, 10)}
    return PAVolumeDistributionArtifact(
        candidate_id="pa_volume_2023_only_v1",
        fit_season=2023,
        selection_season=2024,
        population="original_sequence_zero_starters",
        source_path="permitted.csv.gz",
        source_sha256="a" * 64,
        source_projection_sha256="b" * 64,
        source_columns=SOURCE_COLUMNS,
        fit_rows=18,
        fit_games=1,
        date_min="2023-03-30",
        date_max="2023-03-30",
        rows_by_lineup_slot={slot: 2 for slot in range(1, 10)},
        by_lineup_slot=by_slot,
        pooled={0: 1 / 18, 3: 8 / 9, 6: 1 / 18},
    )


def _write_artifact(tmp_path):
    path = tmp_path / "pa.json"
    path.write_text(json.dumps(_artifact().to_dict(), sort_keys=True) + "\n", encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _hitter() -> HitterGameContext:
    return HitterGameContext(
        player=PlayerIdentity(1, "PA Hitter", "NYY"),
        game=GameContext(777, "2026-07-22", "Park", True, "BOS", "confirmed"),
        lineup_slot=1,
    )


def _authorization(**changes) -> PAVolumeAuthorization:
    values = {
        "mlb_game_pk": 777,
        "official_game_date": "2026-07-22",
        "player_id": 1,
        "hitter_team": "NYY",
        "opponent_team": "BOS",
        "hitter_is_home": True,
        "official_start_utc": "2026-07-22T23:00:00Z",
        "target_horizon_utc": "2026-07-22T19:00:00Z",
        "receipt_utc": "2026-07-22T18:59:00Z",
        "lineup_state": "confirmed",
        "lineup_slot": 1,
        "raw_source_payload_sha256": "c" * 64,
    }
    values.update(changes)
    return PAVolumeAuthorization(**values)


def _bundle(authorization=None) -> PlayerFeatureBundle:
    metadata = {}
    if authorization is not None:
        metadata["pa_volume_authorization"] = authorization.to_dict()
    return PlayerFeatureBundle(
        hitter=_hitter(),
        statcast=StatcastProfile(1, "PA Hitter", sample_pa=100, xwoba=0.32),
        park=ParkFactors("Park"),
        weather=WeatherContext("Park", "2026-07-22"),
        matchup=MatchupContext(),
        metadata=metadata,
    )


def test_artifact_round_trip_and_zero_pa_support():
    artifact = PAVolumeDistributionArtifact.from_mapping(_artifact().to_dict())
    assert artifact.pooled[0] == 1 / 18
    assert artifact.fit_season == 2023 and artifact.selection_season == 2024


def test_artifact_chronology_and_mixture_mutations_fail_closed():
    raw = _artifact().to_dict()
    raw["chronology"]["fit_season"] = 2024
    with pytest.raises(PAVolumeGateError, match="fit 2023"):
        PAVolumeDistributionArtifact.from_mapping(raw)

    raw = _artifact().to_dict()
    raw["pooled"]["3"] -= 0.01
    raw["pooled"]["6"] += 0.01
    with pytest.raises(PAVolumeGateError, match="weighted slot mixture"):
        PAVolumeDistributionArtifact.from_mapping(raw)


def test_strict_artifact_file_is_required_and_hash_bound(tmp_path):
    path, digest = _write_artifact(tmp_path)
    assert load_pa_volume_artifact(path, digest).candidate_id == "pa_volume_2023_only_v1"
    with pytest.raises(PAVolumeGateError, match="hash mismatch"):
        load_pa_volume_artifact(path, "d" * 64)
    with pytest.raises(PAVolumeGateError, match="does not exist"):
        load_pa_volume_artifact(tmp_path / "missing.json", digest)


def test_missing_receipt_forces_pooled_and_ignores_official_slot():
    resolved = resolve_pa_volume_inputs(_bundle(), mode="receipt_required_or_pooled")
    assert resolved.lineup_slot is None
    assert resolved.status == "pooled_missing_receipt"


def test_confirmed_receipt_allows_exact_slot_only():
    authorization = _authorization()
    resolved = resolve_pa_volume_inputs(
        _bundle(authorization), mode="receipt_required_or_pooled"
    )
    assert resolved.lineup_slot == 1
    assert resolved.status == "receipt_confirmed_slot"
    assert resolved.authorization_sha256 == authorization.authorization_sha256


def test_projected_or_unknown_receipt_cannot_carry_slot():
    with pytest.raises(PAVolumeGateError, match="cannot claim"):
        _authorization(lineup_state="projected")
    authorization = _authorization(lineup_state="projected", lineup_slot=None)
    resolved = resolve_pa_volume_inputs(
        _bundle(authorization), mode="receipt_required_or_pooled"
    )
    assert resolved.lineup_slot is None
    assert resolved.status == "pooled_lineup_projected"


def test_authorization_time_identity_and_hash_mutations_fail_closed():
    with pytest.raises(PAVolumeGateError, match="after T-minus-4"):
        _authorization(receipt_utc="2026-07-22T19:00:01Z")
    with pytest.raises(PAVolumeGateError, match="exact T-minus-4"):
        _authorization(target_horizon_utc="2026-07-22T18:59:00Z")

    raw = _authorization().to_dict()
    raw["lineup_slot"] = 2
    with pytest.raises(PAVolumeGateError, match="hash mismatch"):
        PAVolumeAuthorization.from_mapping(raw)

    authorization = _authorization(player_id=2)
    with pytest.raises(PAVolumeGateError, match="does not match hitter"):
        resolve_pa_volume_inputs(
            _bundle(authorization), mode="receipt_required_or_pooled"
        )


def test_may_authorization_is_sealed():
    with pytest.raises(PAVolumeGateError, match="May 2026"):
        _authorization(
            official_game_date="2026-05-12",
            official_start_utc="2026-05-12T23:00:00Z",
            target_horizon_utc="2026-05-12T19:00:00Z",
            receipt_utc="2026-05-12T18:59:00Z",
        )


def test_game_simulator_strict_mode_uses_pooled_without_receipt(tmp_path):
    path, digest = _write_artifact(tmp_path)
    config = {
        "base_running": {
            "pa_volume_identity_mode": "receipt_required_or_pooled",
            "pa_distribution_path": str(path),
            "pa_distribution_sha256": digest,
        }
    }
    simulator = GameSimulator(config=config, random_seed=4)
    draws = {
        simulator._sample_pa_count(4.05, None, "pooled_missing_receipt")
        for _ in range(300)
    }
    assert draws == {0, 3, 6}
    assert simulator._sample_pa_count(4.05, 1, "receipt_confirmed_slot") in {0, 6}
    with pytest.raises(ValueError, match="cannot carry"):
        simulator._sample_pa_count(4.05, 1, "pooled_missing_receipt")
    with pytest.raises(ValueError, match="explicit pooled status"):
        simulator._sample_pa_count(4.05, None, "legacy_frozen")


def test_strict_runtime_refuses_unbound_or_missing_artifact(tmp_path):
    with pytest.raises(PAVolumeGateError, match="SHA-256"):
        GameSimulator(config={"base_running": {"pa_volume_identity_mode": "receipt_required_or_pooled"}})
    path, digest = _write_artifact(tmp_path)
    with pytest.raises(ValueError, match="unbound in-memory"):
        GameSimulator(
            config={
                "base_running": {
                    "pa_volume_identity_mode": "receipt_required_or_pooled",
                    "pa_distribution_path": str(path),
                    "pa_distribution_sha256": digest,
                }
            },
            pa_distribution={1: {4: 1.0}},
        )


def test_prop_engine_strict_boundary_never_passes_unverified_slot(tmp_path):
    path, digest = _write_artifact(tmp_path)
    engine = PropEngine(
        config={
            "base_running": {
                "pa_volume_identity_mode": "receipt_required_or_pooled",
                "pa_distribution_path": str(path),
                "pa_distribution_sha256": digest,
            }
        },
        n_sims=500,
    )
    sim_input = engine._bundle_to_sim_input(_bundle())
    assert sim_input.lineup_slot is None
    assert sim_input.pa_volume_status == "pooled_missing_receipt"


def test_unverified_slot_is_absent_from_feature_vector_and_health_boundary():
    bundle = _bundle()
    bundle.metadata["pa_volume_status"] = "pooled_missing_receipt"
    features = FeatureVectorBuilder().build(bundle)
    assert "ctx_lineup_slot" not in features.values
    assert "ctx_expected_pa" not in features.values
    health = health_for_bundle(bundle)
    assert health.lineup_slot is None
    assert health.pa_volume_status == "pooled_missing_receipt"
    assert "strict_pooled_pa_volume" in health.flags


def test_fitter_retains_zero_pa_and_rejects_2024_fit_rows():
    rows = []
    for slot in range(1, 10):
        for side in range(2):
            rows.append({
                "game_date": "2023-03-30",
                "game_pk": 777,
                "player_id": slot * 10 + side,
                "lineup_slot": slot,
                "out_pa": 0 if slot == 1 and side == 0 else 4,
            })
    frame = pd.DataFrame(rows, columns=SOURCE_COLUMNS)
    artifact = fit_2023_pa_volume(
        frame, source_path="source.csv.gz", source_sha256="e" * 64
    )
    assert artifact.fit_rows == 18
    assert artifact.pooled[0] == 1 / 18

    mutated = frame.copy()
    mutated.loc[0, "game_date"] = "2024-03-30"
    with pytest.raises(ValueError, match="2023 and no other year"):
        fit_2023_pa_volume(
            mutated, source_path="source.csv.gz", source_sha256="e" * 64
        )


def test_legacy_runtime_behavior_is_unchanged():
    simulator = GameSimulator(random_seed=1)
    result = simulator.simulate_game(
        GameSimulatorInput(expected_pa=4.0, pitcher_k_pct=22.0, pitcher_bb_pct=8.0)
    )
    assert result.plate_appearances == 4


def test_artifact_unknown_schema_and_unknown_mode_fail_closed():
    raw = _artifact().to_dict()
    raw["schema_version"] = "unknown"
    with pytest.raises(PAVolumeGateError, match="unknown"):
        PAVolumeDistributionArtifact.from_mapping(raw)
    with pytest.raises(PAVolumeGateError, match="pa_volume_identity_mode"):
        resolve_pa_volume_inputs(_bundle(), mode="actual_lineup")


def test_selection_reader_stops_before_spent_2025_outcome_projection(tmp_path):
    path = tmp_path / "chronological.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVALUATION_COLUMNS)
        writer.writeheader()
        base = {name: "0" for name in EVALUATION_COLUMNS}
        for season in (2023, 2024):
            row = {
                **base,
                "season": str(season),
                "game_date": f"{season}-04-01",
                "game_pk": str(season),
                "player_id": "1",
                "lineup_slot": "1",
                "out_pa": "4",
                "out_ab": "4",
            }
            writer.writerow(row)
        # If later-season outcomes were projected, this deliberately malformed
        # value would reach the returned frame. The reader must stop first.
        writer.writerow({
            **base,
            "season": "2025",
            "game_date": "2025-04-01",
            "game_pk": "2025",
            "player_id": "1",
            "lineup_slot": "1",
            "out_pa": "SPENT_CONFIRMATION_MUST_NOT_BE_PROJECTED",
        })
    frame = read_2023_2024_without_2025_outcomes(path)
    assert frame["season"].tolist() == ["2023", "2024"]
