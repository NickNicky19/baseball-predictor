"""Positive, versioned point-in-time feature authorization."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Mapping

from .canonical import (
    canonical_json_bytes,
    require_nonempty_text,
    require_sha256,
    sha256_bytes,
)
from .chronology import require_before
from .errors import ContractError, FeatureAbstentionRequired


class ConstantClassification(str, Enum):
    STRUCTURAL = "STRUCTURAL"
    OPERATIONAL = "OPERATIONAL"
    FITTED = "FITTED"
    FROZEN_COMPARATOR_ONLY = "FROZEN_COMPARATOR_ONLY"


class FeatureKind(str, Enum):
    FLOAT = "float"
    INTEGER = "integer"
    RATE = "rate"


class MissingPolicy(str, Enum):
    REQUIRED = "required"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    kind: FeatureKind
    classification: ConstantClassification
    unit: str
    source_kind: str
    receipt_type: str
    consumer_nodes: tuple[str, ...]
    missing_policy: MissingPolicy
    minimum: float | None = None
    maximum: float | None = None
    subset_of: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FeatureKind):
            raise ContractError(f"{self.name}.kind must be a FeatureKind")
        if not isinstance(self.classification, ConstantClassification):
            raise ContractError(f"{self.name}.classification must be declared")
        if not isinstance(self.missing_policy, MissingPolicy):
            raise ContractError(f"{self.name}.missing_policy must be declared")
        require_nonempty_text(self.name, label="feature name")
        require_nonempty_text(self.unit, label=f"{self.name}.unit")
        require_nonempty_text(self.source_kind, label=f"{self.name}.source_kind")
        require_nonempty_text(self.receipt_type, label=f"{self.name}.receipt_type")
        if (
            not self.consumer_nodes
            or tuple(sorted(set(self.consumer_nodes))) != self.consumer_nodes
        ):
            raise ContractError(f"{self.name}.consumer_nodes must be sorted and unique")
        for node in self.consumer_nodes:
            require_nonempty_text(node, label=f"{self.name}.consumer_node")
        for bound, label in ((self.minimum, "minimum"), (self.maximum, "maximum")):
            if bound is not None and (
                isinstance(bound, bool) or not math.isfinite(bound)
            ):
                raise ContractError(f"{self.name}.{label} must be finite")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ContractError(f"{self.name} has reversed bounds")
        if self.subset_of == self.name:
            raise ContractError(f"{self.name} cannot be a subset of itself")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "classification": self.classification.value,
            "unit": self.unit,
            "source_kind": self.source_kind,
            "receipt_type": self.receipt_type,
            "consumer_nodes": list(self.consumer_nodes),
            "missing_policy": self.missing_policy.value,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "subset_of": self.subset_of,
        }


@dataclass(frozen=True)
class FeatureSchema:
    schema_version: str
    candidate_id: str
    features: tuple[FeatureSpec, ...]

    def __post_init__(self) -> None:
        require_nonempty_text(self.schema_version, label="schema_version")
        require_nonempty_text(self.candidate_id, label="candidate_id")
        names = tuple(item.name for item in self.features)
        if not names or names != tuple(sorted(set(names))):
            raise ContractError("features must be non-empty and sorted by unique name")
        known = set(names)
        for item in self.features:
            if item.subset_of is not None and item.subset_of not in known:
                raise ContractError(f"{item.name}.subset_of is not in this schema")

    @property
    def sha256(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "features": [item.canonical_dict() for item in self.features],
        }
        return sha256_bytes(canonical_json_bytes(payload))


@dataclass(frozen=True)
class FeatureValue:
    value: float | int
    observed_at_utc: datetime
    receipt_sha256: str
    source_kind: str
    receipt_type: str
    numerator: int | None = None
    denominator: int | None = None
    sample_size: int | None = None


class FeatureRegistry:
    def __init__(self) -> None:
        self._by_hash: dict[str, FeatureSchema] = {}
        self._by_candidate: dict[str, str] = {}

    def register(self, schema: FeatureSchema) -> str:
        digest = schema.sha256
        previous = self._by_candidate.get(schema.candidate_id)
        if previous is not None and previous != digest:
            raise ContractError("candidate feature schema cannot be silently replaced")
        self._by_candidate[schema.candidate_id] = digest
        self._by_hash[digest] = schema
        return digest

    def resolve(self, *, candidate_id: str, schema_sha256: str) -> FeatureSchema:
        require_sha256(schema_sha256, label="feature schema sha256")
        schema = self._by_hash.get(schema_sha256)
        if schema is None or schema.candidate_id != candidate_id:
            raise ContractError(
                "feature schema reference is unresolved or has wrong candidate"
            )
        if self._by_candidate.get(candidate_id) != schema_sha256:
            raise ContractError("feature registry candidate binding mismatch")
        return schema


def validate_feature_vector(
    *,
    schema: FeatureSchema,
    values: Mapping[str, FeatureValue],
    decision_horizon_utc: datetime,
    consumer_node: str,
) -> None:
    specs = {item.name: item for item in schema.features}
    unknown = set(values) - set(specs)
    missing = {
        name
        for name, spec in specs.items()
        if name not in values and spec.missing_policy is MissingPolicy.REQUIRED
    }
    if unknown:
        raise ContractError(f"unknown feature names: {sorted(unknown)}")
    if missing:
        raise ContractError(f"missing required features: {sorted(missing)}")
    for name, item in values.items():
        spec = specs[name]
        if consumer_node not in spec.consumer_nodes:
            raise ContractError(f"feature {name} is not authorized for {consumer_node}")
        if (
            item.source_kind != spec.source_kind
            or item.receipt_type != spec.receipt_type
        ):
            raise ContractError(f"feature {name} source/receipt type mismatch")
        require_sha256(item.receipt_sha256, label=f"{name}.receipt_sha256")
        require_before(
            item.observed_at_utc, decision_horizon_utc, label=f"{name}.observed_at_utc"
        )
        if isinstance(item.value, bool) or not isinstance(item.value, (int, float)):
            raise ContractError(f"feature {name} must be numeric")
        numeric = float(item.value)
        if not math.isfinite(numeric):
            raise ContractError(f"feature {name} must be finite")
        if spec.kind is FeatureKind.INTEGER and not isinstance(item.value, int):
            raise ContractError(f"feature {name} must be an integer")
        if spec.minimum is not None and numeric < spec.minimum:
            raise ContractError(f"feature {name} is below its declared minimum")
        if spec.maximum is not None and numeric > spec.maximum:
            raise ContractError(f"feature {name} is above its declared maximum")
        if spec.kind is FeatureKind.RATE:
            if (
                item.numerator is None
                or item.denominator is None
                or item.sample_size is None
            ):
                raise ContractError(
                    f"rate feature {name} requires counts and sample size"
                )
            if any(
                isinstance(count, bool) or not isinstance(count, int)
                for count in (item.numerator, item.denominator, item.sample_size)
            ):
                raise ContractError(f"rate feature {name} counts must be integers")
            if (
                item.numerator < 0
                or item.denominator <= 0
                or item.numerator > item.denominator
            ):
                raise ContractError(f"rate feature {name} has invalid counts")
            if item.sample_size != item.denominator:
                raise ContractError(
                    f"rate feature {name} sample size must equal denominator"
                )
            if numeric != item.numerator / item.denominator:
                raise ContractError(
                    f"rate feature {name} is inconsistent with its counts"
                )
        elif any(
            count is not None
            for count in (item.numerator, item.denominator, item.sample_size)
        ):
            raise ContractError(
                f"non-rate feature {name} cannot carry rate count metadata"
            )
    for name, spec in specs.items():
        if spec.subset_of is None or name not in values or spec.subset_of not in values:
            continue
        child = values[name]
        parent = values[spec.subset_of]
        if (
            child.denominator != parent.denominator
            or child.numerator is None
            or parent.numerator is None
        ):
            raise ContractError(
                f"subset features {name}/{spec.subset_of} require the same denominator"
            )
        if child.numerator > parent.numerator or float(child.value) > float(
            parent.value
        ):
            raise ContractError(
                f"feature {name} cannot exceed its superset {spec.subset_of}"
            )
    abstain_missing = tuple(
        sorted(
            name
            for name, spec in specs.items()
            if name not in values and spec.missing_policy is MissingPolicy.ABSTAIN
        )
    )
    if abstain_missing:
        raise FeatureAbstentionRequired(abstain_missing)
