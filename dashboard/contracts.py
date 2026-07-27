"""Strict, receipt-bound schema for the inactive Omega display scaffold.

This module validates supplied display values.  It never resolves identities,
fetches data, computes predictions, repairs payloads, or supplies fallbacks.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.omega_contracts.canonical import canonical_json_bytes, sha256_bytes
from src.omega_contracts.chronology import (
    parse_canonical_utc,
    parse_date,
    require_event_eligibility,
)
from src.omega_contracts.errors import ContractError

DISPLAY_MANIFEST_SCHEMA = "prediction-display-manifest-v2"
DISPLAY_SNAPSHOT_SCHEMA = "prediction-display-snapshot-v2"
DISPLAY_ROW_SCHEMA = "prediction-display-row-v2"
MANIFEST_TRUST_MODE = "out_of_band_manifest_sha256_v1"
SUPPORTED_MARKETS = frozenset(
    {"hits", "home_runs", "total_bases", "hrr", "pitcher_strikeouts"}
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,191}$")
PROHIBITED_SEMANTIC_TOKEN = re.compile(
    r"(?:^|[^a-z0-9])(?:roi|profit|loss|result|settlement|void|push|"
    r"win|won|lost|odds?|prices?|sportsbook|wager|bet)(?:$|[^a-z0-9])",
    re.IGNORECASE,
)

ExclusionCode = Literal[
    "MISSING_SOURCE_RECEIPT",
    "IDENTITY_UNRESOLVED",
    "CHRONOLOGY_INELIGIBLE",
    "SOURCE_CONTRADICTION",
    "INPUT_QUARANTINED",
]
FallbackCode = Literal["BATTER_ONLY_PITCHER_BLOCK_EXCLUDED"]
AbstentionCode = Literal[
    "MISSING_REQUIRED_RECEIPT",
    "STALE_SOURCE",
    "IDENTITY_UNRESOLVED",
    "CHRONOLOGY_INELIGIBLE",
    "SOURCE_CONTRADICTION",
    "UNQUALIFIED_MODEL",
    "NO_AUTHORIZED_VALUE",
]
HealthReasonCode = Literal[
    "ALL_REQUIRED_RECEIPTS_VALID",
    "SOURCE_BLOCK_EXCLUDED",
    "SOURCE_RECEIPT_MISSING",
    "SOURCE_CONTRADICTION",
    "IDENTITY_QUARANTINED",
    "CHRONOLOGY_QUARANTINED",
]


class ContractModel(BaseModel):
    """Recursively closed positive schema with no coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


def _require_hash(value: str, *, field_name: str) -> str:
    if SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    return value


def _require_identifier(value: str, *, field_name: str) -> str:
    if IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is not a valid controlled identifier")
    if PROHIBITED_SEMANTIC_TOKEN.search(value):
        raise ValueError(f"{field_name} contains prohibited result/economic semantics")
    return value


def _require_display_name(value: str, *, field_name: str) -> str:
    if value != value.strip() or not value or len(value) > 160:
        raise ValueError(f"{field_name} must be a bounded, trimmed canonical name")
    if unicodedata.normalize("NFKC", value) != value:
        raise ValueError(f"{field_name} must already be Unicode NFKC normalized")
    if any(not (char.isalnum() or char in " .'-") for char in value):
        raise ValueError(f"{field_name} contains a character outside the name schema")
    if PROHIBITED_SEMANTIC_TOKEN.search(value):
        raise ValueError(f"{field_name} contains prohibited result/economic semantics")
    return value


def _finite(value: float, *, field_name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    return value


class ProducerIdentity(ContractModel):
    producer_id: str
    producer_version: str

    @field_validator("producer_id", "producer_version")
    @classmethod
    def identifiers(cls, value: str, info) -> str:
        return _require_identifier(value, field_name=info.field_name)


class IdentityReceiptBinding(ContractModel):
    schema_version: Literal["identity-resolution-receipt-v1"]
    sha256: str
    source_id: str
    observed_at_utc: str
    subject_role: Literal["batter", "opposing_starter"]
    game_pk: Annotated[int, Field(gt=0)]
    mlb_player_id: Annotated[int, Field(gt=0)]
    mlb_team_id: Annotated[int, Field(gt=0)]
    mlb_opponent_team_id: Annotated[int, Field(gt=0)]
    player_name: str
    team_name: str
    opponent_name: str
    identity_state: Literal["RESOLVED"]

    @field_validator("sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return _require_hash(value, field_name="identity receipt sha256")

    @field_validator("source_id")
    @classmethod
    def source(cls, value: str) -> str:
        return _require_identifier(value, field_name="identity receipt source_id")

    @field_validator("observed_at_utc")
    @classmethod
    def observed(cls, value: str) -> str:
        parse_canonical_utc(value, label="identity receipt observed_at_utc")
        return value

    @field_validator("player_name", "team_name", "opponent_name")
    @classmethod
    def names(cls, value: str, info) -> str:
        return _require_display_name(value, field_name=info.field_name)


class SourceReceiptBinding(ContractModel):
    schema_version: Literal["source-observation-receipt-v1"]
    sha256: str
    source_id: str
    parser_id: str
    protocol_id: str
    observed_at_utc: str
    game_pk: Annotated[int, Field(gt=0)]
    mlb_player_id: Annotated[int, Field(gt=0)]
    mlb_team_id: Annotated[int, Field(gt=0)]
    mlb_opponent_team_id: Annotated[int, Field(gt=0)]

    @field_validator("sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return _require_hash(value, field_name="source receipt sha256")

    @field_validator("source_id", "parser_id", "protocol_id")
    @classmethod
    def identifiers(cls, value: str, info) -> str:
        return _require_identifier(value, field_name=info.field_name)

    @field_validator("observed_at_utc")
    @classmethod
    def observed(cls, value: str) -> str:
        parse_canonical_utc(value, label="source receipt observed_at_utc")
        return value


class ChronologyDecisionReceiptBinding(ContractModel):
    schema_version: Literal["chronology-decision-receipt-v1"]
    sha256: str
    source_id: str
    game_pk: Annotated[int, Field(gt=0)]
    official_slate_date: str
    event_start_utc: str
    decision_horizon_utc: str
    decision: Literal["ELIGIBLE"]

    @field_validator("sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return _require_hash(value, field_name="chronology receipt sha256")

    @field_validator("source_id")
    @classmethod
    def source(cls, value: str) -> str:
        return _require_identifier(value, field_name="chronology source_id")

    @field_validator("official_slate_date")
    @classmethod
    def slate_date(cls, value: str) -> str:
        parse_date(value, label="chronology official_slate_date")
        return value

    @field_validator("event_start_utc", "decision_horizon_utc")
    @classmethod
    def timestamps(cls, value: str, info) -> str:
        parse_canonical_utc(value, label=info.field_name)
        return value


class ManifestReceiptBindings(ContractModel):
    identity: list[IdentityReceiptBinding]
    source: list[SourceReceiptBinding]
    chronology: list[ChronologyDecisionReceiptBinding]

    @model_validator(mode="after")
    def unique_and_nonempty(self) -> "ManifestReceiptBindings":
        if not self.identity or not self.source or not self.chronology:
            raise ValueError(
                "manifest must bind identity, source, and chronology receipts"
            )
        digests = [
            *(item.sha256 for item in self.identity),
            *(item.sha256 for item in self.source),
            *(item.sha256 for item in self.chronology),
        ]
        if len(digests) != len(set(digests)):
            raise ValueError("manifest receipt SHA-256 values must be globally unique")
        return self


class DisplayManifest(ContractModel):
    schema_version: Literal[DISPLAY_MANIFEST_SCHEMA]
    trust_mode: Literal[MANIFEST_TRUST_MODE]
    snapshot_path: str
    snapshot_sha256: str
    snapshot_byte_size: Annotated[int, Field(ge=1)]
    official_slate_date: str
    captured_at_utc: str
    source_commit: str
    producer: ProducerIdentity
    model_id: str
    frozen_comparator_model_id: str | None
    config_sha256: str
    artifact_sha256s: dict[str, str]
    qualification_state: Literal[
        "frozen_production_baseline",
        "current_live_research",
        "research_only",
        "unqualified",
    ]
    betting_authorized: Literal[False]
    supported_markets: list[
        Literal["hits", "home_runs", "total_bases", "hrr", "pitcher_strikeouts"]
    ]
    row_count: Annotated[int, Field(ge=0)]
    receipt_bindings: ManifestReceiptBindings

    @field_validator("official_slate_date")
    @classmethod
    def slate_date(cls, value: str) -> str:
        parse_date(value, label="manifest official_slate_date")
        return value

    @field_validator("captured_at_utc")
    @classmethod
    def captured(cls, value: str) -> str:
        parse_canonical_utc(value, label="manifest captured_at_utc")
        return value

    @field_validator("source_commit")
    @classmethod
    def commit(cls, value: str) -> str:
        if COMMIT_RE.fullmatch(value) is None:
            raise ValueError(
                "source_commit must be an exact lowercase 40-hex Git commit"
            )
        return value

    @field_validator("snapshot_sha256", "config_sha256")
    @classmethod
    def hashes(cls, value: str, info) -> str:
        return _require_hash(value, field_name=info.field_name)

    @field_validator("model_id", "frozen_comparator_model_id")
    @classmethod
    def model_ids(cls, value: str | None, info) -> str | None:
        if value is not None:
            _require_identifier(value, field_name=info.field_name)
        return value

    @field_validator("artifact_sha256s")
    @classmethod
    def artifacts(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("artifact_sha256s must bind at least one artifact")
        for key, digest in value.items():
            _require_identifier(key, field_name="artifact identifier")
            _require_hash(digest, field_name=f"artifact_sha256s[{key}]")
        return value

    @field_validator("supported_markets")
    @classmethod
    def markets(cls, value: list[str]) -> list[str]:
        if not value or len(value) != len(set(value)):
            raise ValueError("supported_markets must be nonempty and unique")
        return value


class DiscreteProbability(ContractModel):
    value: Annotated[int, Field(ge=0)]
    probability: Annotated[float, Field(ge=0.0, le=1.0)]

    @field_validator("probability")
    @classmethod
    def finite_probability(cls, value: float) -> float:
        return _finite(value, field_name="probability")


class OpposingStarter(ContractModel):
    mlb_player_id: Annotated[int, Field(gt=0)]
    name: str
    identity_state: Literal["RESOLVED"]
    identity_receipt_sha256: str
    status: Literal["probable_receipt_verified", "confirmed_receipt_verified"]

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value: str) -> str:
        return _require_display_name(value, field_name="opposing starter name")

    @field_validator("identity_receipt_sha256")
    @classmethod
    def receipt(cls, value: str) -> str:
        return _require_hash(value, field_name="opposing starter identity receipt")


class UncertaintyInterval(ContractModel):
    method: Literal[
        "bootstrap_interval",
        "conformal_interval",
        "posterior_interval",
        "display_bound",
    ]
    lower: float
    upper: float

    @field_validator("lower", "upper")
    @classmethod
    def finite_bounds(cls, value: float, info) -> float:
        return _finite(value, field_name=info.field_name)

    @model_validator(mode="after")
    def ordered(self) -> "UncertaintyInterval":
        if self.lower > self.upper:
            raise ValueError("uncertainty lower must not exceed upper")
        return self


class ComparisonValues(ContractModel):
    current_live_prediction: float | None
    frozen_comparator_prediction: float | None
    research_candidate_prediction: float | None
    research_candidate_state: Literal["not_present", "research_only", "unqualified"]

    @field_validator(
        "current_live_prediction",
        "frozen_comparator_prediction",
        "research_candidate_prediction",
    )
    @classmethod
    def finite_predictions(cls, value: float | None, info) -> float | None:
        if value is not None:
            _finite(value, field_name=info.field_name)
            if value < 0:
                raise ValueError(f"{info.field_name} cannot be negative")
        return value

    @model_validator(mode="after")
    def research_state_matches_value(self) -> "ComparisonValues":
        if (
            self.research_candidate_prediction is None
            and self.research_candidate_state != "not_present"
        ):
            raise ValueError("research candidate state requires a candidate value")
        if (
            self.research_candidate_prediction is not None
            and self.research_candidate_state == "not_present"
        ):
            raise ValueError(
                "research candidate value must remain explicitly non-active"
            )
        return self


class PredictionDisplayRow(ContractModel):
    row_schema_version: Literal[DISPLAY_ROW_SCHEMA]
    row_content_sha256: str
    row_id: str
    game_pk: Annotated[int, Field(gt=0)]
    player_name: str
    mlb_player_id: Annotated[int, Field(gt=0)]
    mlb_team_id: Annotated[int, Field(gt=0)]
    mlb_opponent_team_id: Annotated[int, Field(gt=0)]
    team: str
    opponent: str
    identity_state: Literal["RESOLVED"]
    identity_receipt_sha256: str
    source_receipt_sha256: str
    chronology_decision_receipt_sha256: str
    event_start_utc: str
    decision_horizon_utc: str
    home_away: Literal["home", "away"]
    market: Literal["hits", "home_runs", "total_bases", "hrr", "pitcher_strikeouts"]
    product_id: str
    market_side: Literal["over", "under"] | None
    market_line: float | None
    lineup_status: Literal["projected", "confirmed", "unknown", "not_applicable"]
    batting_slot: Annotated[int, Field(ge=1, le=9)] | None
    projected_pa: Annotated[float, Field(ge=0.0)] | None
    pa_distribution: list[DiscreteProbability] | None
    opposing_starter: OpposingStarter | None
    point_prediction_kind: Literal["probability", "expected_count"]
    point_prediction: float | None
    count_distribution: list[DiscreteProbability] | None
    uncertainty: UncertaintyInterval | None
    data_health_tier: Literal["healthy", "degraded", "quarantined", "unknown"]
    source_observed_utc: str
    source_freshness_seconds: Annotated[int, Field(ge=0)]
    exclusion_flags: list[ExclusionCode]
    fallback_flags: list[FallbackCode]
    abstention_reason: AbstentionCode | None
    model_id: str
    comparison: ComparisonValues

    @field_validator(
        "row_content_sha256",
        "identity_receipt_sha256",
        "source_receipt_sha256",
        "chronology_decision_receipt_sha256",
    )
    @classmethod
    def hashes(cls, value: str, info) -> str:
        return _require_hash(value, field_name=info.field_name)

    @field_validator("row_id", "model_id", "product_id")
    @classmethod
    def identifiers(cls, value: str, info) -> str:
        return _require_identifier(value, field_name=info.field_name)

    @field_validator("player_name", "team", "opponent")
    @classmethod
    def names(cls, value: str, info) -> str:
        return _require_display_name(value, field_name=info.field_name)

    @field_validator("event_start_utc", "decision_horizon_utc", "source_observed_utc")
    @classmethod
    def timestamps(cls, value: str, info) -> str:
        parse_canonical_utc(value, label=info.field_name)
        return value

    @field_validator("projected_pa", "point_prediction", "market_line")
    @classmethod
    def finite_values(cls, value: float | None, info) -> float | None:
        if value is not None:
            _finite(value, field_name=info.field_name)
        return value

    @field_validator("exclusion_flags", "fallback_flags")
    @classmethod
    def unique_codes(cls, value: list[str], info) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        return value

    @model_validator(mode="after")
    def semantic_consistency(self) -> "PredictionDisplayRow":
        comparison_values = (
            self.comparison.current_live_prediction,
            self.comparison.frozen_comparator_prediction,
            self.comparison.research_candidate_prediction,
        )
        if self.point_prediction is None and self.abstention_reason is None:
            raise ValueError("missing prediction requires a controlled abstention code")
        if self.point_prediction is not None and self.abstention_reason is not None:
            raise ValueError("abstained rows cannot carry a point prediction")
        if self.abstention_reason is not None and (
            self.count_distribution is not None
            or any(v is not None for v in comparison_values)
        ):
            raise ValueError("abstained rows cannot carry prediction values")
        if self.data_health_tier == "quarantined" and self.point_prediction is not None:
            raise ValueError("quarantined rows cannot carry a point prediction")
        if (
            self.point_prediction_kind == "probability"
            and self.point_prediction is not None
        ):
            if not 0.0 <= self.point_prediction <= 1.0:
                raise ValueError("probability point prediction must be in [0, 1]")
            if self.market_side is None or self.market_line is None:
                raise ValueError(
                    "probability prediction requires exact market side and line"
                )
            if self.market == "home_runs" and self.market_line != 0.5:
                raise ValueError(
                    "home_runs probability rows require the exact 0.5 line"
                )
            if not (self.market_line * 2).is_integer():
                raise ValueError(
                    "count-market probability line must be integer or half-integer"
                )
            for value in comparison_values:
                if value is not None and value > 1.0:
                    raise ValueError("comparison probability must be in [0, 1]")
        elif self.point_prediction_kind == "expected_count":
            if self.market_side is not None or self.market_line is not None:
                raise ValueError(
                    "expected-count prediction cannot claim market side or line"
                )
        if self.uncertainty is not None:
            if self.point_prediction_kind == "probability" and not (
                0.0 <= self.uncertainty.lower <= self.uncertainty.upper <= 1.0
            ):
                raise ValueError("probability uncertainty must remain in [0, 1]")
            if self.point_prediction is not None and not (
                self.uncertainty.lower
                <= self.point_prediction
                <= self.uncertainty.upper
            ):
                raise ValueError("point prediction must lie inside uncertainty")
        for name, distribution in (
            ("pa_distribution", self.pa_distribution),
            ("count_distribution", self.count_distribution),
        ):
            if distribution is None:
                continue
            support = [item.value for item in distribution]
            if len(support) != len(set(support)):
                raise ValueError(f"{name} has duplicate support")
            if abs(math.fsum(item.probability for item in distribution) - 1.0) > 1e-12:
                raise ValueError(f"{name} probabilities must sum to one")
        if self.count_distribution is not None and self.point_prediction is not None:
            if self.point_prediction_kind == "expected_count":
                derived = math.fsum(
                    item.value * item.probability for item in self.count_distribution
                )
            elif self.market_side == "over":
                derived = math.fsum(
                    item.probability
                    for item in self.count_distribution
                    if item.value > self.market_line
                )
            else:
                derived = math.fsum(
                    item.probability
                    for item in self.count_distribution
                    if item.value < self.market_line
                )
            if abs(derived - self.point_prediction) > 1e-12:
                raise ValueError(
                    "point prediction does not reconcile to count distribution"
                )
        expected_hash = sha256_bytes(
            canonical_json_bytes(
                self.model_dump(mode="json", exclude={"row_content_sha256"})
            )
        )
        if self.row_content_sha256 != expected_hash:
            raise ValueError("row content SHA-256 does not bind the canonical row")
        return self


class ModelBanner(ContractModel):
    model_id: str
    model_version: str
    qualification_state: Literal[
        "frozen_production_baseline",
        "current_live_research",
        "research_only",
        "unqualified",
    ]
    betting_authorized: Literal[False]
    current_live_model_id: str | None
    frozen_comparator_model_id: str | None
    research_candidate_model_id: str | None
    research_candidate_state: Literal["not_present", "research_only", "unqualified"]

    @field_validator(
        "model_id",
        "model_version",
        "current_live_model_id",
        "frozen_comparator_model_id",
        "research_candidate_model_id",
    )
    @classmethod
    def identifiers(cls, value: str | None, info) -> str | None:
        if value is not None:
            _require_identifier(value, field_name=info.field_name)
        return value

    @model_validator(mode="after")
    def research_state_matches_identifier(self) -> "ModelBanner":
        if (
            self.research_candidate_model_id is None
            and self.research_candidate_state != "not_present"
        ):
            raise ValueError("research candidate state requires a candidate model ID")
        if (
            self.research_candidate_model_id is not None
            and self.research_candidate_state == "not_present"
        ):
            raise ValueError("research candidate ID must remain explicitly non-active")
        return self


class SourceHealth(ContractModel):
    status: Literal["healthy", "degraded", "unavailable"]
    reason_codes: list[HealthReasonCode]
    latest_refresh_utc: str

    @field_validator("reason_codes")
    @classmethod
    def reasons(cls, value: list[str]) -> list[str]:
        if not value or len(value) != len(set(value)):
            raise ValueError("source health reason codes must be nonempty and unique")
        return value

    @field_validator("latest_refresh_utc")
    @classmethod
    def refresh(cls, value: str) -> str:
        parse_canonical_utc(value, label="source health latest_refresh_utc")
        return value


class PredictionDisplaySnapshot(ContractModel):
    schema_version: Literal[DISPLAY_SNAPSHOT_SCHEMA]
    official_slate_date: str
    captured_at_utc: str
    source_commit: str
    producer: ProducerIdentity
    model_banner: ModelBanner
    source_health: SourceHealth
    config_sha256: str
    artifact_sha256s: dict[str, str]
    supported_markets: list[
        Literal["hits", "home_runs", "total_bases", "hrr", "pitcher_strikeouts"]
    ]
    rows: list[PredictionDisplayRow]

    @field_validator("official_slate_date")
    @classmethod
    def slate_date(cls, value: str) -> str:
        parse_date(value, label="snapshot official_slate_date")
        return value

    @field_validator("captured_at_utc")
    @classmethod
    def captured(cls, value: str) -> str:
        parse_canonical_utc(value, label="snapshot captured_at_utc")
        return value

    @field_validator("source_commit")
    @classmethod
    def commit(cls, value: str) -> str:
        if COMMIT_RE.fullmatch(value) is None:
            raise ValueError(
                "source_commit must be an exact lowercase 40-hex Git commit"
            )
        return value

    @field_validator("config_sha256")
    @classmethod
    def config_hash(cls, value: str) -> str:
        return _require_hash(value, field_name="config_sha256")

    @field_validator("artifact_sha256s")
    @classmethod
    def artifacts(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("artifact_sha256s must bind at least one artifact")
        for key, digest in value.items():
            _require_identifier(key, field_name="artifact identifier")
            _require_hash(digest, field_name=f"artifact_sha256s[{key}]")
        return value

    @field_validator("supported_markets")
    @classmethod
    def markets(cls, value: list[str]) -> list[str]:
        if not value or len(value) != len(set(value)):
            raise ValueError("supported_markets must be nonempty and unique")
        return value

    @model_validator(mode="after")
    def snapshot_consistency(self) -> "PredictionDisplaySnapshot":
        if self.model_banner.model_id not in {
            self.model_banner.current_live_model_id,
            self.model_banner.frozen_comparator_model_id,
            self.model_banner.research_candidate_model_id,
            self.model_banner.model_id,
        }:
            raise ValueError("model banner identity is inconsistent")
        seen_row_ids: set[str] = set()
        seen_observations: set[tuple[object, ...]] = set()
        for row in self.rows:
            if row.row_id in seen_row_ids:
                raise ValueError("duplicate row_id")
            seen_row_ids.add(row.row_id)
            identity = (
                row.game_pk,
                row.mlb_player_id,
                row.market,
                row.product_id,
                row.market_side,
                row.market_line,
            )
            if identity in seen_observations:
                raise ValueError("duplicate normalized display observation")
            seen_observations.add(identity)
            if row.market not in self.supported_markets:
                raise ValueError("row market is not declared by snapshot")
            try:
                observed, _, captured, _ = require_event_eligibility(
                    observed_at=row.source_observed_utc,
                    decision_horizon=row.decision_horizon_utc,
                    captured_at=self.captured_at_utc,
                    event_start=row.event_start_utc,
                )
            except ContractError as exc:
                raise ValueError("row chronology is ineligible") from exc
            if (
                int((captured - observed).total_seconds())
                != row.source_freshness_seconds
            ):
                raise ValueError(
                    "row source freshness does not match canonical chronology"
                )
        return self


def reject_prohibited_semantics(value: object) -> None:
    """Secondary defense after the positive schema; not an identity mechanism."""

    if isinstance(value, dict):
        for item in value.values():
            reject_prohibited_semantics(item)
    elif isinstance(value, list):
        for item in value:
            reject_prohibited_semantics(item)
    elif isinstance(value, str) and PROHIBITED_SEMANTIC_TOKEN.search(value):
        raise ValueError(
            "display payload contains prohibited result/economic semantics"
        )
