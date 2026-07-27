"""Hard-keyed coverage and abstention contracts without outcome access."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .canonical import (
    canonical_json_bytes,
    require_nonempty_text,
    require_sha256,
    sha256_bytes,
)
from .errors import ContractError
from .identity import ObservationKey


class ObservationDisposition(str, Enum):
    AVAILABLE = "available"
    GRADEABLE = "gradeable"
    ABSTAINED = "abstained"
    MISSING_SOURCE = "missing_source"
    INTEGRITY_FAILURE = "integrity_failure"
    NONSTARTER = "nonstarter"
    VOID = "void"
    CANCELLED = "cancelled"
    UNMATCHED = "unmatched"


@dataclass(frozen=True)
class EvaluationObservation:
    key: ObservationKey
    disposition: ObservationDisposition
    probability_sha256: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.disposition in {
            ObservationDisposition.AVAILABLE,
            ObservationDisposition.GRADEABLE,
        }:
            require_sha256(self.probability_sha256, label="probability sha256")
            if self.reason is not None:
                raise ContractError(
                    "available/gradeable observations cannot carry an exclusion reason"
                )
        else:
            if self.probability_sha256 is not None:
                raise ContractError("excluded observations cannot carry a probability")
            require_nonempty_text(self.reason, label="observation reason")

    @property
    def planned_key(self) -> tuple[str, int, int, str, str, str]:
        return (
            self.key.official_date,
            self.key.mlb_game_pk,
            self.key.player_id,
            self.key.team_side,
            self.key.market,
            self.key.line,
        )


@dataclass(frozen=True)
class CoverageReport:
    planned_universe_sha256: str
    planned: int
    available_by_arm: dict[str, int]
    gradeable_by_arm: dict[str, int]
    excluded_by_arm: dict[str, int]
    common_gradeable_keys: tuple[tuple[str, int, int, str, str, str], ...]


def validate_complete_coverage(
    observations: Iterable[EvaluationObservation],
    *,
    planned_keys: Iterable[ObservationKey],
    planned_universe_sha256: str,
    required_arms: tuple[str, ...],
    candidate_arm: str,
) -> CoverageReport:
    declared_planned_hash = require_sha256(
        planned_universe_sha256, label="planned universe sha256"
    )
    if not required_arms or tuple(sorted(set(required_arms))) != required_arms:
        raise ContractError("required arms must be sorted and unique")
    if candidate_arm not in required_arms:
        raise ContractError("candidate arm is not declared")
    by_key: dict[
        tuple[tuple[str, int, int, str, str, str], str], EvaluationObservation
    ] = {}
    planned: set[tuple[str, int, int, str, str, str]] = set()
    for key in planned_keys:
        if key.arm_id != "planned":
            raise ContractError("planned universe keys must use arm_id=planned")
        normalized = (
            key.official_date,
            key.mlb_game_pk,
            key.player_id,
            key.team_side,
            key.market,
            key.line,
        )
        if normalized in planned:
            raise ContractError("duplicate planned evaluation identity")
        planned.add(normalized)
    if not planned:
        raise ContractError("evaluation universe is empty")
    computed_planned_hash = sha256_bytes(
        canonical_json_bytes(
            {
                "schema_version": "evaluation-planned-universe-v1",
                "keys": [list(key) for key in sorted(planned)],
            }
        )
    )
    if computed_planned_hash != declared_planned_hash:
        raise ContractError(
            "planned universe hash does not match its canonical identities"
        )
    markets = {key[4] for key in planned}
    if len(markets) != 1:
        raise ContractError("markets must be adjudicated separately")
    for row in observations:
        if row.key.arm_id not in required_arms:
            raise ContractError("observation contains an undeclared arm")
        if row.key.market not in markets:
            raise ContractError("markets must be adjudicated separately")
        composite = (row.planned_key, row.key.arm_id)
        if composite in by_key:
            raise ContractError("duplicate evaluation observation")
        by_key[composite] = row
    expected = {(key, arm) for key in planned for arm in required_arms}
    actual = set(by_key)
    if actual != expected:
        missing = len(expected - actual)
        extra = len(actual - expected)
        raise ContractError(
            f"evaluation coverage matrix is incomplete: missing={missing}, extra={extra}"
        )
    available: dict[str, int] = {}
    gradeable: dict[str, int] = {}
    excluded: dict[str, int] = {}
    for arm in required_arms:
        rows = [by_key[(key, arm)] for key in planned]
        available[arm] = sum(
            row.disposition
            in {ObservationDisposition.AVAILABLE, ObservationDisposition.GRADEABLE}
            for row in rows
        )
        gradeable[arm] = sum(
            row.disposition is ObservationDisposition.GRADEABLE for row in rows
        )
        excluded[arm] = len(rows) - available[arm]
    for comparator in required_arms:
        if comparator == candidate_arm:
            continue
        if available[candidate_arm] < available[comparator]:
            raise ContractError(
                f"candidate availability regresses against {comparator}"
            )
        if gradeable[candidate_arm] < gradeable[comparator]:
            raise ContractError(
                f"candidate gradeable coverage regresses against {comparator}"
            )
    common_gradeable = tuple(
        sorted(
            key
            for key in planned
            if all(
                by_key[(key, arm)].disposition is ObservationDisposition.GRADEABLE
                for arm in required_arms
            )
        )
    )
    return CoverageReport(
        computed_planned_hash,
        len(planned),
        available,
        gradeable,
        excluded,
        common_gradeable,
    )


def select_common_gradeable_observations(
    observations: Iterable[EvaluationObservation],
    *,
    coverage: CoverageReport,
    required_arms: tuple[str, ...],
) -> dict[str, tuple[EvaluationObservation, ...]]:
    """Return the only rows permitted for paired proper-score comparisons."""
    by_key = {(row.planned_key, row.key.arm_id): row for row in observations}
    selected: dict[str, tuple[EvaluationObservation, ...]] = {}
    for arm in required_arms:
        rows: list[EvaluationObservation] = []
        for key in coverage.common_gradeable_keys:
            row = by_key.get((key, arm))
            if row is None or row.disposition is not ObservationDisposition.GRADEABLE:
                raise ContractError(
                    "common gradeable comparison matrix changed after coverage validation"
                )
            rows.append(row)
        selected[arm] = tuple(rows)
    return selected
