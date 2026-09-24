"""Positive, fail-closed contracts for dashboard display snapshots.

The dashboard accepts only the fields declared here.  It does not infer,
repair, clip, enrich, or calculate prediction values.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DISPLAY_MANIFEST_SCHEMA = "prediction-display-manifest-v1"
DISPLAY_SNAPSHOT_SCHEMA = "prediction-display-snapshot-v1"
SUPPORTED_MARKETS = frozenset(
    {"hits", "home_runs", "total_bases", "hrr", "pitcher_strikeouts"}
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,191}$")
FORBIDDEN_DISPLAY_LANGUAGE = re.compile(
    r"(?:\bbest\s+bets?\b|\blocks?\b|\bexpected\s+value\b|"
    r"\bsportsbook\s+edge\b|\bbetting\s+(?:confidence|advice)\b|"
    r"\bwager(?:ing)?\b)",
    re.IGNORECASE,
)


class ContractModel(BaseModel):
    """Strict positive schema: undeclared fields and coercions are rejected."""

    model_config = ConfigDict(extra="forbid", strict=True)


def parse_canonical_date(value: object, *, field_name: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{field_name} must be canonical YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid date") from exc
    if parsed.year == 2026 and parsed.month == 5:
        raise ValueError("May 2026 is sealed")
    return parsed


def parse_utc_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        raise ValueError(f"{field_name} must be a canonical UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field_name} must be UTC-aware")
    if parsed.year == 2026 and parsed.month == 5:
        raise ValueError("May 2026 is sealed")
    return parsed


def _require_identifier(value: str, *, field_name: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field_name} is not a valid identifier")
    return value


def _require_hash(value: str, *, field_name: str) -> str:
    if not SHA256_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
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


class DisplayManifest(ContractModel):
    schema_version: Literal[DISPLAY_MANIFEST_SCHEMA]
    snapshot_path: str
    snapshot_sha256: str
    snapshot_byte_size: Annotated[int, Field(ge=1)]
    official_slate_date: str
    created_utc: str
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
    signature_attestation_ref: str | None

    @field_validator("official_slate_date")
    @classmethod
    def canonical_date(cls, value: str) -> str:
        parse_canonical_date(value, field_name="official_slate_date")
        return value

    @field_validator("created_utc")
    @classmethod
    def canonical_created(cls, value: str) -> str:
        parse_utc_timestamp(value, field_name="created_utc")
        return value

    @field_validator("source_commit")
    @classmethod
    def commit_hash(cls, value: str) -> str:
        if not COMMIT_RE.fullmatch(value):
            raise ValueError("source_commit must be a lowercase 40-character Git commit")
        return value

    @field_validator("snapshot_sha256", "config_sha256")
    @classmethod
    def hashes(cls, value: str, info) -> str:
        return _require_hash(value, field_name=info.field_name)

    @field_validator("model_id")
    @classmethod
    def model_identifier(cls, value: str) -> str:
        return _require_identifier(value, field_name="model_id")

    @field_validator("frozen_comparator_model_id")
    @classmethod
    def optional_model_identifier(cls, value: str | None) -> str | None:
        if value is not None:
            _require_identifier(value, field_name="frozen_comparator_model_id")
        return value

    @field_validator("artifact_sha256s")
    @classmethod
    def artifact_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("artifact_sha256s must bind at least one artifact")
        for key, digest in value.items():
            _require_identifier(key, field_name="artifact identifier")
            _require_hash(digest, field_name=f"artifact_sha256s[{key}]")
        return value

    @field_validator("supported_markets")
    @classmethod
    def unique_markets(cls, value: list[str]) -> list[str]:
        if not value or len(value) != len(set(value)):
            raise ValueError("supported_markets must be nonempty and unique")
        return value

    @field_validator("signature_attestation_ref")
    @classmethod
    def optional_attestation(cls, value: str | None) -> str | None:
        if value is not None:
            _require_identifier(value, field_name="signature_attestation_ref")
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
    name: Annotated[str, Field(min_length=1, max_length=160)]
    status: Literal["probable_receipt_verified", "confirmed_receipt_verified"]


class UncertaintyInterval(ContractModel):
    method: Annotated[str, Field(min_length=1, max_length=96)]
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
        if self.research_candidate_prediction is None and self.research_candidate_state != "not_present":
            raise ValueError("research candidate state requires a displayed candidate value")
        if self.research_candidate_prediction is not None and self.research_candidate_state == "not_present":
            raise ValueError("research candidate value must remain explicitly non-active")
        return self


class PredictionDisplayRow(ContractModel):
    row_id: str
    game_pk: Annotated[int, Field(gt=0)]
    player_name: Annotated[str, Field(min_length=1, max_length=160)]
    mlb_player_id: Annotated[int, Field(gt=0)] | None
    team: str
    opponent: str
    game_time_utc: str
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
    source_observed_utc: str | None
    source_freshness_seconds: Annotated[int, Field(ge=0)] | None
    exclusion_flags: list[str]
    fallback_flags: list[str]
    abstention_reason: str | None
    model_id: str
    comparison: ComparisonValues

    @field_validator("row_id", "team", "opponent", "model_id", "product_id")
    @classmethod
    def row_identifiers(cls, value: str, info) -> str:
        return _require_identifier(value, field_name=info.field_name)

    @field_validator("game_time_utc")
    @classmethod
    def game_time(cls, value: str) -> str:
        parse_utc_timestamp(value, field_name="game_time_utc")
        return value

    @field_validator("source_observed_utc")
    @classmethod
    def observed_time(cls, value: str | None) -> str | None:
        if value is not None:
            parse_utc_timestamp(value, field_name="source_observed_utc")
        return value

    @field_validator("projected_pa", "point_prediction", "market_line")
    @classmethod
    def finite_values(cls, value: float | None, info) -> float | None:
        if value is not None:
            _finite(value, field_name=info.field_name)
        return value

    @field_validator("exclusion_flags", "fallback_flags")
    @classmethod
    def unique_flags(cls, value: list[str], info) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        for flag in value:
            _require_identifier(flag, field_name=info.field_name)
        return value

    @model_validator(mode="after")
    def semantic_consistency(self) -> "PredictionDisplayRow":
        comparison_values = (
            self.comparison.current_live_prediction,
            self.comparison.frozen_comparator_prediction,
            self.comparison.research_candidate_prediction,
        )
        if self.mlb_player_id is None and (
            self.point_prediction is not None
            or any(value is not None for value in comparison_values)
        ):
            raise ValueError("prediction-bearing rows require a positive MLB player ID")
        if self.source_observed_utc is None and self.source_freshness_seconds is not None:
            raise ValueError("source freshness requires source_observed_utc")
        if self.source_observed_utc is not None and self.source_freshness_seconds is None:
            raise ValueError("source_observed_utc requires source freshness")
        if self.point_prediction is None and self.abstention_reason is None:
            raise ValueError("missing prediction requires an abstention reason")
        if self.point_prediction is not None and self.abstention_reason is not None:
            raise ValueError("abstained rows cannot carry a point prediction")
        if self.abstention_reason is not None and self.count_distribution is not None:
            raise ValueError("abstained rows cannot carry a count distribution")
        if self.data_health_tier == "quarantined" and self.point_prediction is not None:
            raise ValueError("quarantined rows cannot carry a point prediction")
        if self.point_prediction_kind == "probability" and self.point_prediction is not None:
            if not 0.0 <= self.point_prediction <= 1.0:
                raise ValueError("probability point prediction must be in [0, 1]")
        if self.point_prediction_kind == "probability":
            if self.market_side is None or self.market_line is None:
                raise ValueError("probability prediction requires exact market side and line")
            if self.market_line < 0:
                raise ValueError("market line must be non-negative")
            if self.market == "home_runs" and self.market_line != 0.5:
                raise ValueError("home_runs probability rows require the exact 0.5 line")
            if not (self.market_line * 2).is_integer():
                raise ValueError("count-market probability line must be integer or half-integer")
            for field_name, value in (
                ("current_live_prediction", self.comparison.current_live_prediction),
                ("frozen_comparator_prediction", self.comparison.frozen_comparator_prediction),
                ("research_candidate_prediction", self.comparison.research_candidate_prediction),
            ):
                if value is not None and value > 1.0:
                    raise ValueError(f"{field_name} probability must be in [0, 1]")
        elif self.market_side is not None or self.market_line is not None:
            raise ValueError("expected-count prediction cannot claim a market side or line")
        if self.uncertainty is not None:
            if self.point_prediction_kind == "probability" and not (
                0.0 <= self.uncertainty.lower <= self.uncertainty.upper <= 1.0
            ):
                raise ValueError("probability uncertainty must remain in [0, 1]")
            if self.point_prediction is not None and not (
                self.uncertainty.lower <= self.point_prediction <= self.uncertainty.upper
            ):
                raise ValueError("point prediction must lie inside its uncertainty interval")
        for name, distribution in (
            ("pa_distribution", self.pa_distribution),
            ("count_distribution", self.count_distribution),
        ):
            if distribution is None:
                continue
            values = [item.value for item in distribution]
            if len(values) != len(set(values)):
                raise ValueError(f"{name} has duplicate support values")
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
                raise ValueError("point prediction does not reconcile to count distribution")
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
    def model_identifiers(cls, value: str | None, info) -> str | None:
        if value is not None:
            _require_identifier(value, field_name=info.field_name)
        return value

    @model_validator(mode="after")
    def research_state_matches_identifier(self) -> "ModelBanner":
        if self.research_candidate_model_id is None and self.research_candidate_state != "not_present":
            raise ValueError("research candidate state requires a candidate model ID")
        if self.research_candidate_model_id is not None and self.research_candidate_state == "not_present":
            raise ValueError("research candidate ID must remain explicitly non-active")
        return self


class SourceHealth(ContractModel):
    status: Literal["healthy", "degraded", "unavailable"]
    message: Annotated[str, Field(min_length=1, max_length=320)]
    latest_refresh_utc: str

    @field_validator("latest_refresh_utc")
    @classmethod
    def refresh_time(cls, value: str) -> str:
        parse_utc_timestamp(value, field_name="latest_refresh_utc")
        return value


class PredictionDisplaySnapshot(ContractModel):
    schema_version: Literal[DISPLAY_SNAPSHOT_SCHEMA]
    official_slate_date: str
    created_utc: str
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
    def canonical_date(cls, value: str) -> str:
        parse_canonical_date(value, field_name="official_slate_date")
        return value

    @field_validator("created_utc")
    @classmethod
    def canonical_created(cls, value: str) -> str:
        parse_utc_timestamp(value, field_name="created_utc")
        return value

    @field_validator("source_commit")
    @classmethod
    def commit_hash(cls, value: str) -> str:
        if not COMMIT_RE.fullmatch(value):
            raise ValueError("source_commit must be a lowercase 40-character Git commit")
        return value

    @field_validator("config_sha256")
    @classmethod
    def config_hash(cls, value: str) -> str:
        return _require_hash(value, field_name="config_sha256")

    @field_validator("artifact_sha256s")
    @classmethod
    def artifact_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("artifact_sha256s must bind at least one artifact")
        for key, digest in value.items():
            _require_identifier(key, field_name="artifact identifier")
            _require_hash(digest, field_name=f"artifact_sha256s[{key}]")
        return value

    @field_validator("supported_markets")
    @classmethod
    def unique_markets(cls, value: list[str]) -> list[str]:
        if not value or len(value) != len(set(value)):
            raise ValueError("supported_markets must be nonempty and unique")
        return value

    @model_validator(mode="after")
    def snapshot_consistency(self) -> "PredictionDisplaySnapshot":
        created = parse_utc_timestamp(self.created_utc, field_name="created_utc")
        refresh = parse_utc_timestamp(
            self.source_health.latest_refresh_utc, field_name="latest_refresh_utc"
        )
        if refresh > created:
            raise ValueError("latest refresh cannot be after snapshot creation")
        identities: set[tuple[object, ...]] = set()
        row_ids: set[str] = set()
        for row in self.rows:
            if row.market not in self.supported_markets:
                raise ValueError("row market is not declared in supported_markets")
            if row.row_id in row_ids:
                raise ValueError("duplicate row_id")
            row_ids.add(row.row_id)
            player_key: object = row.mlb_player_id if row.mlb_player_id is not None else row.player_name
            identity = (
                row.game_pk,
                player_key,
                row.team,
                row.market,
                row.product_id,
                row.market_side,
                row.market_line,
                row.model_id,
            )
            if identity in identities:
                raise ValueError("duplicate normalized prediction identity")
            identities.add(identity)
            game_time = parse_utc_timestamp(row.game_time_utc, field_name="game_time_utc")
            if created >= game_time:
                raise ValueError("snapshot must be created before every target game")
            if row.source_observed_utc is not None:
                observed = parse_utc_timestamp(
                    row.source_observed_utc, field_name="source_observed_utc"
                )
                if observed > created:
                    raise ValueError("source observation cannot be after snapshot creation")
                elapsed = int((created - observed).total_seconds())
                if elapsed != row.source_freshness_seconds:
                    raise ValueError("source freshness does not match bound timestamps")
        return self


def reject_forbidden_display_language(value: object) -> None:
    """Reject content that could turn a research viewer into betting advice."""

    if isinstance(value, str):
        if FORBIDDEN_DISPLAY_LANGUAGE.search(value):
            raise ValueError("snapshot contains prohibited betting-advice language")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            reject_forbidden_display_language(key)
            reject_forbidden_display_language(item)
        return
    if isinstance(value, list):
        for item in value:
            reject_forbidden_display_language(item)
