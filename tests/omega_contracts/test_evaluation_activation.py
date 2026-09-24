import pytest

from src.omega_contracts.activation import assert_research_only_manifest
from src.omega_contracts.errors import ContractError
from src.omega_contracts.evaluation import (
    EvaluationObservation,
    ObservationDisposition,
    validate_complete_coverage,
)
from src.omega_contracts.canonical import canonical_json_bytes, sha256_bytes
from src.omega_contracts.identity import Market, ObservationKey


def key(*, arm: str, player: int = 10, market=Market.HR):
    return ObservationKey.create(
        official_date="2026-07-26",
        mlb_game_pk=1,
        player_id=player,
        team_side="home",
        market=market,
        line="0.5",
        arm_id=arm,
    )


def gradeable(*, arm: str, player: int = 10):
    return EvaluationObservation(
        key=key(arm=arm, player=player),
        disposition=ObservationDisposition.GRADEABLE,
        probability_sha256=("a" if arm == "candidate" else "b") * 64,
    )


def excluded(*, arm: str, player: int = 10):
    return EvaluationObservation(
        key=key(arm=arm, player=player),
        disposition=ObservationDisposition.MISSING_SOURCE,
        reason="source receipt unavailable before horizon",
    )


def planned(*players: int, market=Market.HR):
    return tuple(key(arm="planned", player=player, market=market) for player in players)


def planned_hash(keys):
    normalized = sorted(
        (
            item.official_date,
            item.mlb_game_pk,
            item.player_id,
            item.team_side,
            item.market,
            item.line,
        )
        for item in keys
    )
    return sha256_bytes(
        canonical_json_bytes(
            {
                "schema_version": "evaluation-planned-universe-v1",
                "keys": [list(item) for item in normalized],
            }
        )
    )


def coverage(rows, *, players=(10,), market=Market.HR):
    universe = planned(*players, market=market)
    return validate_complete_coverage(
        rows,
        planned_keys=universe,
        planned_universe_sha256=planned_hash(universe),
        required_arms=("candidate", "frozen"),
        candidate_arm="candidate",
    )


def test_exact_coverage_matrix_and_nonregression():
    rows = [
        gradeable(arm="candidate", player=10),
        gradeable(arm="frozen", player=10),
        gradeable(arm="candidate", player=11),
        excluded(arm="frozen", player=11),
    ]
    report = coverage(rows, players=(10, 11))
    assert report.planned == 2
    assert report.gradeable_by_arm == {"candidate": 2, "frozen": 1}
    assert report.common_gradeable_keys == (
        ("2026-07-26", 1, 10, "home", "hr_over_0_5", "0.5"),
    )


def test_duplicate_missing_arm_and_coverage_regression_fail():
    duplicate = gradeable(arm="candidate")
    with pytest.raises(ContractError, match="duplicate"):
        coverage([duplicate, duplicate, gradeable(arm="frozen")])
    with pytest.raises(ContractError, match="incomplete"):
        coverage([gradeable(arm="candidate")])
    with pytest.raises(ContractError, match="regresses"):
        coverage([excluded(arm="candidate"), gradeable(arm="frozen")])

    with pytest.raises(ContractError, match="incomplete"):
        coverage(
            [gradeable(arm="candidate"), gradeable(arm="frozen")],
            players=(10, 11),
        )

    universe = planned(10)
    with pytest.raises(ContractError, match="hash"):
        validate_complete_coverage(
            [gradeable(arm="candidate"), gradeable(arm="frozen")],
            planned_keys=universe,
            planned_universe_sha256="c" * 64,
            required_arms=("candidate", "frozen"),
            candidate_arm="candidate",
        )


def test_markets_cannot_be_pooled():
    rows = [
        gradeable(arm="candidate"),
        gradeable(arm="frozen"),
        EvaluationObservation(
            key=key(arm="candidate", player=11, market=Market.HITS),
            disposition=ObservationDisposition.GRADEABLE,
            probability_sha256="a" * 64,
        ),
        EvaluationObservation(
            key=key(arm="frozen", player=11, market=Market.HITS),
            disposition=ObservationDisposition.GRADEABLE,
            probability_sha256="b" * 64,
        ),
    ]
    with pytest.raises(ContractError, match="separately"):
        coverage(rows, players=(10, 11), market=Market.HR)


def test_excluded_rows_cannot_hide_probability_and_gradeable_needs_probability():
    with pytest.raises(ContractError, match="cannot carry"):
        EvaluationObservation(
            key=key(arm="candidate"),
            disposition=ObservationDisposition.ABSTAINED,
            probability_sha256="a" * 64,
            reason="abstain",
        )
    with pytest.raises(ContractError):
        EvaluationObservation(
            key=key(arm="candidate"), disposition=ObservationDisposition.GRADEABLE
        )


def test_activation_and_betting_are_fail_closed():
    assert_research_only_manifest(
        {
            "qualification_state": "IMPLEMENTED_UNQUALIFIED",
            "active": False,
            "betting_authorized": False,
            "promotion_approval_sha256": None,
        }
    )
    for field in ("active", "betting_authorized"):
        payload = {
            "qualification_state": "IMPLEMENTED_UNQUALIFIED",
            "active": False,
            "betting_authorized": False,
            "promotion_approval_sha256": None,
        }
        payload[field] = True
        with pytest.raises(ContractError):
            assert_research_only_manifest(payload)
    with pytest.raises(ContractError):
        assert_research_only_manifest(
            {
                "qualification_state": "ACTIVE",
                "active": True,
                "betting_authorized": False,
                "promotion_approval_sha256": None,
            }
        )
