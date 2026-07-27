from datetime import date, datetime, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.omega_contracts.chronology import parse_date
from src.omega_contracts.errors import ContractError
from src.omega_contracts.features import (
    ConstantClassification,
    FeatureKind,
    FeatureSchema,
    FeatureSpec,
    FeatureValue,
    MissingPolicy,
    validate_feature_vector,
)


@given(st.dates(min_value=date(2023, 1, 1), max_value=date(2027, 12, 31)))
def test_canonical_date_round_trip_or_may_seal(value: date):
    if value.year == 2026 and value.month == 5:
        with pytest.raises(ContractError):
            parse_date(value.isoformat())
    else:
        assert parse_date(value.isoformat()) == value


@given(
    total=st.integers(min_value=1, max_value=10000),
    hard_count=st.integers(min_value=0, max_value=10000),
    barrel_count=st.integers(min_value=0, max_value=10000),
)
def test_subset_rate_contract_matches_counts(
    total: int, hard_count: int, barrel_count: int
):
    hard_count %= total + 1
    barrel_count %= total + 1
    schema = FeatureSchema(
        schema_version="property-v1",
        candidate_id="property",
        features=(
            FeatureSpec(
                "barrel_rate",
                FeatureKind.RATE,
                ConstantClassification.FITTED,
                "fraction",
                "statcast",
                "receipt-v1",
                ("pa",),
                MissingPolicy.REQUIRED,
                0.0,
                1.0,
                "hard_hit_rate",
            ),
            FeatureSpec(
                "hard_hit_rate",
                FeatureKind.RATE,
                ConstantClassification.FITTED,
                "fraction",
                "statcast",
                "receipt-v1",
                ("pa",),
                MissingPolicy.REQUIRED,
                0.0,
                1.0,
            ),
        ),
    )
    observed = datetime(2026, 7, 26, 10, tzinfo=timezone.utc)
    values = {
        "barrel_rate": FeatureValue(
            barrel_count / total,
            observed,
            "a" * 64,
            "statcast",
            "receipt-v1",
            barrel_count,
            total,
            total,
        ),
        "hard_hit_rate": FeatureValue(
            hard_count / total,
            observed,
            "b" * 64,
            "statcast",
            "receipt-v1",
            hard_count,
            total,
            total,
        ),
    }
    if barrel_count > hard_count:
        with pytest.raises(ContractError, match="cannot exceed"):
            validate_feature_vector(
                schema=schema,
                values=values,
                decision_horizon_utc=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
                consumer_node="pa",
            )
    else:
        validate_feature_vector(
            schema=schema,
            values=values,
            decision_horizon_utc=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
            consumer_node="pa",
        )
