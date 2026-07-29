from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from src.evaluation.pa_volume_source_truth_v2 import (
    PAVolumeSourceTruthError,
    build_pa_volume_artifact,
    canonical_json_bytes,
    validate_pa_volume_artifact,
)


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 64


def rows() -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for game_pk, game_date in ((1, "2023-03-30"), (2, "2023-03-31")):
        for side, team, player_base in (("away", 10, 1000), ("home", 20, 2000)):
            for slot in range(1, 10):
                output.append(
                    {
                        "game_pk": game_pk,
                        "official_date": game_date,
                        "side": side,
                        "team_id": team,
                        "player_id": player_base + game_pk * 10 + slot,
                        "lineup_slot": slot,
                        "out_pa": 5 if slot <= 4 else 4,
                    }
                )
    return output


def build(value: list[dict[str, object]] | None = None) -> dict:
    return build_pa_volume_artifact(
        rows=rows() if value is None else value,
        source_release_manifest_sha256=SHA,
        source_release_external_verification_sha256="b" * 64,
        dependency_lock_sha256="c" * 64,
        builder_source_sha256="d" * 64,
    )


def test_build_is_order_independent_and_path_free() -> None:
    original = rows()
    reverse = list(reversed(original))
    first = build(original)
    second = build(reverse)
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first["source"]["fit_rows"] == 36
    assert first["source"]["fit_games"] == 2
    assert first["counts_by_lineup_slot"]["1"] == {"5": 4}
    assert first["counts_by_lineup_slot"]["9"] == {"4": 4}
    assert "path" not in first["source"]
    assert first["chronology"] == {
        "source_fit_seasons": [2023],
        "selection_performed": False,
        "selection_seasons": [],
        "outcome_scoring_performed": False,
    }


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda value: value[0].update({"official_date": "2024-03-30"}), "2023"),
        (lambda value: value[0].update({"official_date": "2026-05-01"}), "2023"),
        (lambda value: value[0].update({"lineup_slot": 0}), "positive"),
        (lambda value: value[0].update({"out_pa": -1}), "non-negative"),
        (lambda value: value[0].update({"actual_starter": True}), "positive schema"),
        (lambda value: value.append(copy.deepcopy(value[0])), "duplicated"),
        (lambda value: value.pop(), "18 original starters"),
        (lambda value: value[0].update({"team_id": 20}), "conflicting team"),
    ],
)
def test_projection_mutations_fail_closed(mutation, match: str) -> None:
    value = rows()
    mutation(value)
    with pytest.raises(PAVolumeSourceTruthError, match=match):
        build(value)


def test_doubleheaders_remain_distinct_by_game_pk() -> None:
    value = rows()
    for row in value:
        row["official_date"] = "2023-04-01"
    artifact = build(value)
    assert artifact["source"]["fit_games"] == 2


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda value: value["source"]["bindings"].update({"dependency_lock_sha256": "0" * 63}), "SHA-256"),
        (lambda value: value["counts_by_lineup_slot"]["1"].update({"5": 3}), "differ"),
        (lambda value: value["by_lineup_slot"]["1"].update({"5": 0.5}), "differ"),
        (lambda value: value["pooled_counts"].update({"4": 1}), "differ"),
        (lambda value: value.update({"betting_authorized": True}), "safety"),
        (
            lambda value: value["chronology"].update(
                {"selection_performed": True, "selection_seasons": [2024]}
            ),
            "chronology",
        ),
        (lambda value: value["source"].update({"path": "C:/mixed_2023_2025.csv"}), "schema"),
    ],
)
def test_artifact_mutations_fail_closed(mutation, match: str) -> None:
    artifact = build()
    mutation(artifact)
    with pytest.raises(PAVolumeSourceTruthError, match=match):
        validate_pa_volume_artifact(artifact)


def test_builder_source_hash_can_bind_exact_module_bytes() -> None:
    source = ROOT / "src/evaluation/pa_volume_source_truth_v2.py"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    artifact = build_pa_volume_artifact(
        rows=rows(),
        source_release_manifest_sha256=SHA,
        source_release_external_verification_sha256="b" * 64,
        dependency_lock_sha256="c" * 64,
        builder_source_sha256=digest,
    )
    assert artifact["source"]["bindings"]["builder_source_sha256"] == digest
