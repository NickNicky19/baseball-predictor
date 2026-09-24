from datetime import datetime, timedelta, timezone

import pytest

from src.omega_contracts.errors import ContractError, FeatureAbstentionRequired
from src.omega_contracts.features import (
    ConstantClassification,
    FeatureKind,
    FeatureRegistry,
    FeatureSchema,
    FeatureSpec,
    FeatureValue,
    MissingPolicy,
    validate_feature_vector,
)


def schema() -> FeatureSchema:
    return FeatureSchema(
        schema_version="omega-feature-schema-v1",
        candidate_id="research-only",
        features=(
            FeatureSpec(
                name="barrel_rate",
                kind=FeatureKind.RATE,
                classification=ConstantClassification.FITTED,
                unit="fraction_of_bbe",
                source_kind="statcast_bbe",
                receipt_type="statcast_bbe_receipt_v1",
                consumer_nodes=("coherent_pa",),
                missing_policy=MissingPolicy.ABSTAIN,
                minimum=0.0,
                maximum=1.0,
                subset_of="hard_hit_rate",
            ),
            FeatureSpec(
                name="hard_hit_rate",
                kind=FeatureKind.RATE,
                classification=ConstantClassification.FITTED,
                unit="fraction_of_bbe",
                source_kind="statcast_bbe",
                receipt_type="statcast_bbe_receipt_v1",
                consumer_nodes=("coherent_pa",),
                missing_policy=MissingPolicy.REQUIRED,
                minimum=0.0,
                maximum=1.0,
            ),
        ),
    )


def value(rate: float, count: int, total: int, *, observed=None) -> FeatureValue:
    return FeatureValue(
        value=rate,
        observed_at_utc=observed or datetime(2026, 7, 26, 10, tzinfo=timezone.utc),
        receipt_sha256="b" * 64,
        source_kind="statcast_bbe",
        receipt_type="statcast_bbe_receipt_v1",
        numerator=count,
        denominator=total,
        sample_size=total,
    )


def test_registry_is_content_addressed_and_cannot_replace_candidate():
    registry = FeatureRegistry()
    first = schema()
    digest = registry.register(first)
    assert registry.resolve(candidate_id="research-only", schema_sha256=digest) is first
    changed = FeatureSchema(
        schema_version="omega-feature-schema-v2",
        candidate_id="research-only",
        features=first.features,
    )
    with pytest.raises(ContractError, match="silently replaced"):
        registry.register(changed)


def test_valid_rates_require_truthful_counts_and_same_denominator():
    horizon = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    validate_feature_vector(
        schema=schema(),
        values={"barrel_rate": value(0.1, 1, 10), "hard_hit_rate": value(0.4, 4, 10)},
        decision_horizon_utc=horizon,
        consumer_node="coherent_pa",
    )
    with pytest.raises(ContractError, match="inconsistent"):
        validate_feature_vector(
            schema=schema(),
            values={"barrel_rate": value(0.2, 1, 10), "hard_hit_rate": value(0.4, 4, 10)},
            decision_horizon_utc=horizon,
            consumer_node="coherent_pa",
        )


def test_kwan_shaped_barrel_hard_hit_contradiction_fails_closed():
    horizon = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    with pytest.raises(ContractError, match="cannot exceed"):
        validate_feature_vector(
            schema=schema(),
            values={"barrel_rate": value(0.5, 50, 100), "hard_hit_rate": value(0.09, 9, 100)},
            decision_horizon_utc=horizon,
            consumer_node="coherent_pa",
        )


def test_unknown_nonfinite_late_and_unauthorized_features_fail():
    horizon = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    valid = {"hard_hit_rate": value(0.4, 4, 10)}
    with pytest.raises(ContractError, match="unknown"):
        validate_feature_vector(
            schema=schema(),
            values={**valid, "leakage_final_outcome": value(0.1, 1, 10)},
            decision_horizon_utc=horizon,
            consumer_node="coherent_pa",
        )
    with pytest.raises(ContractError, match="finite"):
        validate_feature_vector(
            schema=schema(),
            values={"hard_hit_rate": value(float("inf"), 4, 10)},
            decision_horizon_utc=horizon,
            consumer_node="coherent_pa",
        )
    with pytest.raises(ContractError, match="strictly before"):
        validate_feature_vector(
            schema=schema(),
            values={"hard_hit_rate": value(0.4, 4, 10, observed=horizon)},
            decision_horizon_utc=horizon,
            consumer_node="coherent_pa",
        )
    with pytest.raises(ContractError, match="not authorized"):
        validate_feature_vector(
            schema=schema(), values=valid, decision_horizon_utc=horizon, consumer_node="dashboard"
        )


def test_missing_abstain_feature_and_fractional_counts_fail_closed():
    horizon = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    with pytest.raises(FeatureAbstentionRequired) as captured:
        validate_feature_vector(
            schema=schema(),
            values={"hard_hit_rate": value(0.4, 4, 10)},
            decision_horizon_utc=horizon,
            consumer_node="coherent_pa",
        )
    assert captured.value.missing_features == ("barrel_rate",)

    fractional = value(0.15, 1, 10)
    object.__setattr__(fractional, "numerator", 1.5)
    object.__setattr__(fractional, "value", 0.15)
    with pytest.raises(ContractError, match="integers"):
        validate_feature_vector(
            schema=schema(),
            values={
                "barrel_rate": fractional,
                "hard_hit_rate": value(0.4, 4, 10),
            },
            decision_horizon_utc=horizon,
            consumer_node="coherent_pa",
        )
