from __future__ import annotations

import copy
from pathlib import Path

import pytest

from src.evaluation.shared_pa_projected_opportunity_candidate_v2 import (
    ProjectedOpportunityCandidateV2Error,
    validate_prediction_release_chronology_v2,
)
from src.evaluation.shared_pa_projected_opportunity_release_v2 import (
    PROTOCOL_RELATIVE,
    PUBLISHED_PROTOCOL_STATUS,
    ProjectedOpportunityReleaseV2Error,
    build_runtime_release_receipt_v2,
    validate_release_claim_v2,
)
import scripts.publish_shared_pa_projected_opportunity_release_v2 as publisher
from scripts.publish_shared_pa_projected_opportunity_release_v2 import (
    _external_destination,
    _publish_once,
)


def _receipt(**updates: object) -> dict[str, object]:
    values = {
        "protocol_status": PUBLISHED_PROTOCOL_STATUS,
        "protocol_sha256": "a" * 64,
        "protocol_path": PROTOCOL_RELATIVE,
        "source_manifest_sha256": "b" * 64,
        "observed_commit": "c" * 40,
        "observed_clean": True,
        "created_at_utc": "2026-07-29T13:00:00-05:00",
    }
    values.update(updates)
    return build_runtime_release_receipt_v2(**values)


def test_release_receipt_is_canonical_utc_and_replays() -> None:
    receipt = _receipt()
    assert receipt["created_at_utc"] == "2026-07-29T18:00:00.000000Z"
    identity = validate_release_claim_v2(
        protocol_status=PUBLISHED_PROTOCOL_STATUS,
        protocol_sha256="a" * 64,
        protocol_path=PROTOCOL_RELATIVE,
        source_manifest_sha256="b" * 64,
        receipt_payload=receipt,
        observed_commit="c" * 40,
        observed_clean=True,
    )
    assert identity["confirmation_release_eligible"] is True
    assert identity["release_created_at_utc"] == "2026-07-29T18:00:00.000000Z"


@pytest.mark.parametrize(
    ("created_at", "prediction_at", "horizon_at", "message"),
    [
        (
            "2026-07-29T18:00:01.000000Z",
            "2026-07-29T18:00:00.000000Z",
            "2026-07-29T18:01:00.000000Z",
            "chronology is invalid",
        ),
        (
            "2026-07-29T17:59:00.000000Z",
            "2026-07-29T18:01:01.000000Z",
            "2026-07-29T18:01:00.000000Z",
            "chronology is invalid",
        ),
        (
            None,
            "2026-07-29T18:00:00.000000Z",
            "2026-07-29T18:01:00.000000Z",
            "chronology is invalid",
        ),
        (
            "2026-07-29T17:59:00.000000Z",
            "2026-07-29T18:00:00",
            "2026-07-29T18:01:00.000000Z",
            "naive timestamp",
        ),
    ],
)
def test_release_prediction_chronology_mutations_fail_closed(
    created_at: object,
    prediction_at: str,
    horizon_at: str,
    message: str,
) -> None:
    identity = {
        "confirmation_release_eligible": True,
        "release_created_at_utc": created_at,
    }
    with pytest.raises(ProjectedOpportunityCandidateV2Error, match=message):
        validate_prediction_release_chronology_v2(
            release_identity=identity,
            prediction_generated_at_utc=prediction_at,
            target_horizon_utc=horizon_at,
        )


def test_release_prediction_chronology_accepts_exact_ordering() -> None:
    validate_prediction_release_chronology_v2(
        release_identity={
            "confirmation_release_eligible": True,
            "release_created_at_utc": "2026-07-29T17:59:00.000000Z",
        },
        prediction_generated_at_utc="2026-07-29T18:00:00.000000Z",
        target_horizon_utc="2026-07-29T18:01:00.000000Z",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("protocol_status", "RESEARCH_ONLY_EXACT_BYTES_RELEASE_PENDING", "published protocol"),
        ("observed_clean", False, "clean source tree"),
        ("observed_commit", "short", "full lowercase Git commit"),
        ("protocol_sha256", "bad", "lowercase SHA-256"),
        ("source_manifest_sha256", "bad", "lowercase SHA-256"),
        ("protocol_path", "elsewhere.json", "protocol path"),
        ("created_at_utc", "2026-07-29T13:00:00", "naive"),
    ],
)
def test_release_authority_mutations_fail_closed(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ProjectedOpportunityReleaseV2Error, match=message):
        _receipt(**{field: value})


def test_rehashed_semantic_mutation_still_fails_against_observed_release() -> None:
    receipt = _receipt()
    mutated = copy.deepcopy(receipt)
    mutated["source_commit"] = "d" * 40
    from src.evaluation.projected_lineup_contract import sha256_value

    unsigned = dict(mutated)
    unsigned.pop("release_receipt_sha256")
    mutated["release_receipt_sha256"] = sha256_value(unsigned)
    with pytest.raises(ProjectedOpportunityReleaseV2Error, match="differs"):
        validate_release_claim_v2(
            protocol_status=PUBLISHED_PROTOCOL_STATUS,
            protocol_sha256="a" * 64,
            protocol_path=PROTOCOL_RELATIVE,
            source_manifest_sha256="b" * 64,
            receipt_payload=mutated,
            observed_commit="c" * 40,
            observed_clean=True,
        )


def test_rehashed_noncanonical_timestamp_fails_closed() -> None:
    receipt = _receipt()
    mutated = copy.deepcopy(receipt)
    mutated["created_at_utc"] = "2026-07-29T18:00:00+00:00"
    from src.evaluation.projected_lineup_contract import sha256_value

    unsigned = dict(mutated)
    unsigned.pop("release_receipt_sha256")
    mutated["release_receipt_sha256"] = sha256_value(unsigned)
    with pytest.raises(ProjectedOpportunityReleaseV2Error, match="differs"):
        validate_release_claim_v2(
            protocol_status=PUBLISHED_PROTOCOL_STATUS,
            protocol_sha256="a" * 64,
            protocol_path=PROTOCOL_RELATIVE,
            source_manifest_sha256="b" * 64,
            receipt_payload=mutated,
            observed_commit="c" * 40,
            observed_clean=True,
        )


def test_external_receipt_publication_is_idempotent_and_never_overwrites(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "release.json"
    assert _publish_once(destination, b'{"release":"exact"}\n') is True
    assert _publish_once(destination, b'{"release":"exact"}\n') is False
    with pytest.raises(ProjectedOpportunityReleaseV2Error, match="differs"):
        _publish_once(destination, b'{"release":"different"}\n')
    assert destination.read_bytes() == b'{"release":"exact"}\n'


def test_external_receipt_destination_cannot_be_inside_release(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    with pytest.raises(ProjectedOpportunityReleaseV2Error, match="outside"):
        _external_destination(root=release, output=release / "receipt.json")


def test_final_release_validation_precedes_immutable_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / "release"
    external = tmp_path / "external"
    release.mkdir()
    external.mkdir()
    protocol_path = release / PROTOCOL_RELATIVE
    protocol_path.parent.mkdir(parents=True)
    protocol_path.write_text("{}", encoding="utf-8")
    observations = iter([("c" * 40, True), ("d" * 40, True)])
    monkeypatch.setattr(
        publisher,
        "_protocol",
        lambda _root: (
            protocol_path,
            {"candidate_id": "shared_pa_projected_opportunity_eb200_v2", "status": PUBLISHED_PROTOCOL_STATUS},
            "a" * 64,
        ),
    )
    monkeypatch.setattr(
        publisher,
        "load_source_manifest_v2",
        lambda **_kwargs: ({}, "b" * 64),
    )
    monkeypatch.setattr(
        publisher,
        "observe_clean_git_release_v2",
        lambda **_kwargs: next(observations),
    )

    destination = external / "receipt.json"
    with pytest.raises(ProjectedOpportunityReleaseV2Error, match="differs"):
        publisher.publish(root=release, output=destination)
    assert not destination.exists()
