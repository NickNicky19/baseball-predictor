"""Tamper-evident, hard-keyed forward shadow ledger.

The old name-keyed shadow comparison is useful for watching model/market
disagreement but cannot establish forward CLV.  This ledger is the evaluable
record instead.  It accepts only independently resolved MLB identities and
source hashes, then protects every append with a SHA-256 hash chain.  A local
hash chain is tamper-evident, not tamper-proof: anchor the reported head hash
outside this filesystem for an independently auditable forward record.

Entries record the decision-time model and two-sided quote.  Resolutions are
separate immutable records, linked by entry id, so a later close, official
outcome, void, or missing-data status can never rewrite the original decision.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Literal, Mapping, Optional

from src.evaluation.market_economics import (
    MarketEconomicsError,
    expected_profit_per_unit,
    fair_over_probability as _fair_over_probability,
)


SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SettlementStatus = Literal["graded", "void", "unscored"]


class ShadowLedgerError(ValueError):
    """Raised when a forward record is incomplete, conflicting, or tampered."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc(value: str, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ShadowLedgerError(f"{label} is not an ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ShadowLedgerError(f"{label} must include an explicit UTC offset: {value!r}")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_datetime(value: str, label: str) -> datetime:
    return datetime.fromisoformat(_utc(value, label).replace("Z", "+00:00"))


def _iso_date(value: str, label: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ShadowLedgerError(f"{label} must be YYYY-MM-DD: {value!r}") from exc


def _hash(value: str, label: str) -> str:
    normalised = str(value).strip().lower()
    if not _SHA256.fullmatch(normalised):
        raise ShadowLedgerError(f"{label} must be a 64-character SHA-256 hex digest")
    return normalised


def _finite_probability(value: Any, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ShadowLedgerError(f"{label} must be numeric") from exc
    if not math.isfinite(out) or not 0.0 <= out <= 1.0:
        raise ShadowLedgerError(f"{label} must be in [0, 1], got {value!r}")
    return out


def _finite_line(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ShadowLedgerError("line must be numeric") from exc
    if not math.isfinite(out) or out < 0:
        raise ShadowLedgerError(f"line must be finite and non-negative, got {value!r}")
    return out


def _american_odds(value: Any, label: str) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise ShadowLedgerError(f"{label} must be an integer American price") from exc
    if out == 0:
        raise ShadowLedgerError(f"{label} cannot be 0")
    return out


def fair_over_probability(over_odds_american: int, under_odds_american: int) -> float:
    """De-vig a two-sided American-price market into P(Over).

    This is the same proportional normalisation used by the market evaluator.
    It is intentionally derived from the immutable recorded two-sided prices,
    never supplied as a later editable field.
    """
    try:
        return _fair_over_probability(over_odds_american, under_odds_american)
    except MarketEconomicsError as exc:
        raise ShadowLedgerError(str(exc)) from exc


def _nonempty(value: Any, label: str) -> str:
    out = str(value).strip()
    if not out:
        raise ShadowLedgerError(f"{label} cannot be blank")
    return out


@dataclass(frozen=True)
class ShadowEntry:
    """A single, hard-keyed, pre-game shadow decision.

    ``entry_target_at_utc`` is the declared horizon (for example T-4h), while
    ``entry_quote_at_utc`` is the actual quote timestamp selected by the
    market extractor.  Both are facts preserved for later coverage and timing
    diagnostics; this class does not invent a tolerance policy.
    """

    mlb_game_pk: int
    player_id: int
    game_date: str
    official_start_time_utc: str
    sportsbook: str
    category: str
    line: float
    entry_target_at_utc: str
    entry_quote_at_utc: str
    entry_over_odds_american: int
    entry_under_odds_american: int
    model_p_over: float
    selection_side: Literal["over", "under"]
    selection_policy_id: str
    selection_policy_sha256: str
    model_version: str
    config_sha256: str
    code_sha256: str
    prediction_artifact_sha256: str
    quote_artifact_sha256: str

    def __post_init__(self) -> None:
        try:
            game_pk = int(self.mlb_game_pk)
            player_id = int(self.player_id)
        except (TypeError, ValueError) as exc:
            raise ShadowLedgerError("mlb_game_pk and player_id must be integers") from exc
        if game_pk <= 0 or player_id <= 0:
            raise ShadowLedgerError("mlb_game_pk and player_id must be positive")
        object.__setattr__(self, "mlb_game_pk", game_pk)
        object.__setattr__(self, "player_id", player_id)
        object.__setattr__(self, "game_date", _iso_date(self.game_date, "game_date"))
        object.__setattr__(self, "sportsbook", _nonempty(self.sportsbook, "sportsbook").lower())
        object.__setattr__(self, "category", _nonempty(self.category, "category"))
        object.__setattr__(self, "line", _finite_line(self.line))
        object.__setattr__(self, "official_start_time_utc", _utc(self.official_start_time_utc, "official_start_time_utc"))
        object.__setattr__(self, "entry_target_at_utc", _utc(self.entry_target_at_utc, "entry_target_at_utc"))
        object.__setattr__(self, "entry_quote_at_utc", _utc(self.entry_quote_at_utc, "entry_quote_at_utc"))
        object.__setattr__(self, "entry_over_odds_american", _american_odds(self.entry_over_odds_american, "entry_over_odds_american"))
        object.__setattr__(self, "entry_under_odds_american", _american_odds(self.entry_under_odds_american, "entry_under_odds_american"))
        object.__setattr__(self, "model_p_over", _finite_probability(self.model_p_over, "model_p_over"))
        selection_side = _nonempty(self.selection_side, "selection_side").lower()
        if selection_side not in {"over", "under"}:
            raise ShadowLedgerError("selection_side must be over or under")
        object.__setattr__(self, "selection_side", selection_side)
        object.__setattr__(self, "selection_policy_id", _nonempty(self.selection_policy_id, "selection_policy_id"))
        object.__setattr__(self, "selection_policy_sha256", _hash(self.selection_policy_sha256, "selection_policy_sha256"))
        object.__setattr__(self, "model_version", _nonempty(self.model_version, "model_version"))
        for field_name in (
            "config_sha256",
            "code_sha256",
            "prediction_artifact_sha256",
            "quote_artifact_sha256",
        ):
            object.__setattr__(self, field_name, _hash(getattr(self, field_name), field_name))

        start = _utc_datetime(self.official_start_time_utc, "official_start_time_utc")
        if _utc_datetime(self.entry_target_at_utc, "entry_target_at_utc") >= start:
            raise ShadowLedgerError("entry_target_at_utc must be before official_start_time_utc")
        if _utc_datetime(self.entry_quote_at_utc, "entry_quote_at_utc") >= start:
            raise ShadowLedgerError("entry_quote_at_utc must be before official_start_time_utc")
        if _utc_datetime(self.entry_quote_at_utc, "entry_quote_at_utc") > _utc_datetime(
            self.entry_target_at_utc, "entry_target_at_utc"
        ):
            raise ShadowLedgerError(
                "entry_quote_at_utc must be on or before entry_target_at_utc; "
                "a post-horizon quote is not decision-time evidence"
            )
        fair_over = fair_over_probability(
            self.entry_over_odds_american, self.entry_under_odds_american
        )
        model_side = self.model_p_over if self.selection_side == "over" else 1.0 - self.model_p_over
        market_side = fair_over if self.selection_side == "over" else 1.0 - fair_over
        if model_side <= market_side:
            raise ShadowLedgerError(
                "selected side has no positive de-vigged claimed edge; "
                "a forward capture ledger records selected shadow bets, not all quotes"
            )
        payout_odds = (self.entry_over_odds_american if selection_side == "over"
                       else self.entry_under_odds_american)
        try:
            expected_profit = expected_profit_per_unit(model_side, payout_odds)
        except MarketEconomicsError as exc:
            raise ShadowLedgerError(str(exc)) from exc
        if expected_profit <= 0.0:
            raise ShadowLedgerError(
                "selected side has positive de-vigged claimed edge but non-positive "
                "expected profit at the posted price; it is not a wager candidate"
            )

    @property
    def expected_profit_per_unit(self) -> float:
        """Exact posted-price expected profit, derived from immutable entry terms."""
        probability = (self.model_p_over if self.selection_side == "over"
                       else 1.0 - self.model_p_over)
        odds = (self.entry_over_odds_american if self.selection_side == "over"
                else self.entry_under_odds_american)
        return expected_profit_per_unit(probability, odds)

    @property
    def entry_id(self) -> str:
        # One entry per independently declared market selection/horizon. The
        # actual quote timestamp remains evidence, not a way to duplicate it.
        return _sha256(
            {
                "schema_version": SCHEMA_VERSION,
                "record_type": "entry_identity",
                "mlb_game_pk": self.mlb_game_pk,
                "player_id": self.player_id,
                "category": self.category,
                "line": self.line,
                "sportsbook": self.sportsbook,
                "entry_target_at_utc": self.entry_target_at_utc,
                "selection_side": self.selection_side,
            }
        )

    def payload(self) -> dict[str, Any]:
        return {"entry_id": self.entry_id, **asdict(self)}

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ShadowEntry":
        keys = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        missing = sorted(keys - set(row))
        if missing:
            raise ShadowLedgerError(f"entry input is missing required columns {missing}")
        return cls(**{key: row[key] for key in keys})


@dataclass(frozen=True)
class ShadowResolution:
    """Immutable close/outcome disposition for one existing shadow entry."""

    entry_id: str
    settlement_status: SettlementStatus
    settled_at_utc: str
    close_quote_at_utc: Optional[str] = None
    close_over_odds_american: Optional[int] = None
    close_under_odds_american: Optional[int] = None
    close_quote_artifact_sha256: Optional[str] = None
    official_actual_value: Optional[float] = None
    official_outcome_artifact_sha256: Optional[str] = None
    settlement_evidence_artifact_sha256: Optional[str] = None
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_id", _hash(self.entry_id, "entry_id"))
        status = _nonempty(self.settlement_status, "settlement_status")
        if status not in {"graded", "void", "unscored"}:
            raise ShadowLedgerError("settlement_status must be graded, void, or unscored")
        object.__setattr__(self, "settlement_status", status)
        object.__setattr__(self, "settled_at_utc", _utc(self.settled_at_utc, "settled_at_utc"))
        object.__setattr__(self, "reason", str(self.reason).strip())

        close_values = (
            self.close_quote_at_utc,
            self.close_over_odds_american,
            self.close_under_odds_american,
        )
        has_close = any(value not in (None, "") for value in close_values)
        if has_close and not all(value not in (None, "") for value in close_values):
            raise ShadowLedgerError("close quote requires timestamp plus both Over and Under prices")
        if has_close:
            if self.close_quote_artifact_sha256 in (None, ""):
                raise ShadowLedgerError("close quote requires its retained artifact hash")
            object.__setattr__(self, "close_quote_at_utc", _utc(str(self.close_quote_at_utc), "close_quote_at_utc"))
            object.__setattr__(self, "close_over_odds_american", _american_odds(self.close_over_odds_american, "close_over_odds_american"))
            object.__setattr__(self, "close_under_odds_american", _american_odds(self.close_under_odds_american, "close_under_odds_american"))
            object.__setattr__(
                self,
                "close_quote_artifact_sha256",
                _hash(self.close_quote_artifact_sha256, "close_quote_artifact_sha256"),
            )
        else:
            object.__setattr__(self, "close_quote_at_utc", None)
            object.__setattr__(self, "close_over_odds_american", None)
            object.__setattr__(self, "close_under_odds_american", None)
            if self.close_quote_artifact_sha256 not in (None, ""):
                raise ShadowLedgerError("close artifact hash cannot exist without a complete close quote")
            object.__setattr__(self, "close_quote_artifact_sha256", None)

        if status == "graded":
            if self.official_actual_value is None or self.official_outcome_artifact_sha256 is None:
                raise ShadowLedgerError("graded resolution requires official actual value and outcome artifact hash")
            try:
                actual = float(self.official_actual_value)
            except (TypeError, ValueError) as exc:
                raise ShadowLedgerError("official_actual_value must be numeric") from exc
            if not math.isfinite(actual) or actual < 0:
                raise ShadowLedgerError("official_actual_value must be finite and non-negative")
            if not has_close:
                raise ShadowLedgerError("graded resolution requires a two-sided close quote")
            object.__setattr__(self, "official_actual_value", actual)
            object.__setattr__(
                self,
                "official_outcome_artifact_sha256",
                _hash(self.official_outcome_artifact_sha256, "official_outcome_artifact_sha256"),
            )
            if self.settlement_evidence_artifact_sha256 not in (None, ""):
                object.__setattr__(
                    self,
                    "settlement_evidence_artifact_sha256",
                    _hash(
                        self.settlement_evidence_artifact_sha256,
                        "settlement_evidence_artifact_sha256",
                    ),
                )
            else:
                object.__setattr__(self, "settlement_evidence_artifact_sha256", None)
        else:
            if self.official_actual_value not in (None, ""):
                raise ShadowLedgerError(f"{status} resolution cannot carry official_actual_value")
            if self.official_outcome_artifact_sha256 not in (None, ""):
                raise ShadowLedgerError(f"{status} resolution cannot carry official outcome hash")
            if not self.reason:
                raise ShadowLedgerError(f"{status} resolution requires an explicit reason")
            if self.settlement_evidence_artifact_sha256 in (None, ""):
                raise ShadowLedgerError(
                    f"{status} resolution requires a retained settlement evidence artifact hash"
                )
            object.__setattr__(self, "official_actual_value", None)
            object.__setattr__(self, "official_outcome_artifact_sha256", None)
            object.__setattr__(
                self,
                "settlement_evidence_artifact_sha256",
                _hash(
                    self.settlement_evidence_artifact_sha256,
                    "settlement_evidence_artifact_sha256",
                ),
            )

    @property
    def resolution_id(self) -> str:
        return _sha256(
            {
                "schema_version": SCHEMA_VERSION,
                "record_type": "resolution_identity",
                "entry_id": self.entry_id,
            }
        )

    def payload(self) -> dict[str, Any]:
        return {"resolution_id": self.resolution_id, **asdict(self)}

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ShadowResolution":
        keys = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        missing = sorted({"entry_id", "settlement_status", "settled_at_utc"} - set(row))
        if missing:
            raise ShadowLedgerError(f"resolution input is missing required columns {missing}")
        payload = {key: row.get(key) for key in keys}
        return cls(**payload)


@dataclass(frozen=True)
class LedgerAppendReport:
    added: int
    idempotent: int
    total_records: int
    head_hash: Optional[str]


class ForwardShadowLedger:
    """Append-only ledger with identity, provenance, and tamper guards."""

    def __init__(
        self,
        path: str | Path = "data/learning/shadow/forward_ledger.jsonl",
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.path = Path(path)
        # Injection keeps the temporal contract testable without letting test
        # fixtures depend on the workstation's clock. Production uses UTC now.
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def append_entries(self, entries: Iterable[ShadowEntry]) -> LedgerAppendReport:
        values = list(entries)
        if not values:
            return self.verify()
        with self._exclusive_lock():
            records = self._read_verified_records()
            existing = {record["entry_id"]: record for record in records if record["record_type"] == "entry"}
            self._assert_no_duplicate_ids([entry.entry_id for entry in values], "entry input")
            payloads: list[dict[str, Any]] = []
            idempotent = 0
            for entry in sorted(values, key=lambda value: value.entry_id):
                current = entry.payload()
                prior = existing.get(entry.entry_id)
                if prior is None:
                    payloads.append({"record_type": "entry", **current})
                elif self._payload_without_chain(prior) == {"record_type": "entry", **current}:
                    idempotent += 1
                else:
                    raise ShadowLedgerError(
                        f"entry_id {entry.entry_id} already exists with different decision-time evidence; "
                        "refusing to overwrite a forward record"
                    )
            return self._append_payloads(records, payloads, idempotent)

    def append_resolutions(self, resolutions: Iterable[ShadowResolution]) -> LedgerAppendReport:
        values = list(resolutions)
        if not values:
            return self.verify()
        with self._exclusive_lock():
            records = self._read_verified_records()
            entries = {record["entry_id"]: record for record in records if record["record_type"] == "entry"}
            existing = {
                record["resolution_id"]: record
                for record in records
                if record["record_type"] == "resolution"
            }
            self._assert_no_duplicate_ids([value.resolution_id for value in values], "resolution input")
            payloads: list[dict[str, Any]] = []
            idempotent = 0
            for resolution in sorted(values, key=lambda value: value.resolution_id):
                entry = entries.get(resolution.entry_id)
                if entry is None:
                    raise ShadowLedgerError(
                        f"resolution references unknown entry_id {resolution.entry_id}; entries must be recorded first"
                    )
                self._validate_resolution_against_entry(resolution, entry)
                current = resolution.payload()
                prior = existing.get(resolution.resolution_id)
                if prior is None:
                    payloads.append({"record_type": "resolution", **current})
                elif self._payload_without_chain(prior) == {"record_type": "resolution", **current}:
                    idempotent += 1
                else:
                    raise ShadowLedgerError(
                        f"entry_id {resolution.entry_id} already has a different immutable resolution"
                    )
            return self._append_payloads(records, payloads, idempotent)

    def verify(self) -> LedgerAppendReport:
        records = self._read_verified_records()
        return LedgerAppendReport(
            added=0,
            idempotent=0,
            total_records=len(records),
            head_hash=records[-1]["record_hash"] if records else None,
        )

    def recorded_entry_ids(self) -> set[str]:
        """Return immutable entry ids only after verifying the full hash chain."""

        return {
            str(record["entry_id"])
            for record in self._read_verified_records()
            if record["record_type"] == "entry"
        }

    def unresolved_entries(self) -> list[ShadowEntry]:
        """Return verified entries that do not yet have an immutable resolution."""

        records = self._read_verified_records()
        resolved = {
            str(record["entry_id"])
            for record in records
            if record["record_type"] == "resolution"
        }
        return [
            ShadowEntry.from_mapping(record)
            for record in records
            if record["record_type"] == "entry" and str(record["entry_id"]) not in resolved
        ]

    def verified_records(self) -> tuple[dict[str, Any], ...]:
        """Expose a defensive copy only after full schema/link/hash validation."""

        return tuple(dict(record) for record in self._read_verified_records())

    def graded_pairs(self) -> list[dict[str, Any]]:
        """Return verified, immutable entry/resolution pairs for shadow scoring."""
        records = self._read_verified_records()
        entries = {
            record["entry_id"]: record
            for record in records
            if record["record_type"] == "entry"
        }
        pairs: list[dict[str, Any]] = []
        for record in records:
            if record["record_type"] != "resolution" or record["settlement_status"] != "graded":
                continue
            entry = entries[record["entry_id"]]
            pairs.append({
                **{
                    key: value
                    for key, value in entry.items()
                    if key not in {"schema_version", "record_type", "previous_record_hash", "record_hash"}
                },
                **{
                    f"resolution_{key}": value
                    for key, value in record.items()
                    if key not in {"schema_version", "record_type", "previous_record_hash", "record_hash", "entry_id"}
                },
            })
        return pairs

    def _read_verified_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ShadowLedgerError(f"cannot read ledger {self.path}: {exc}") from exc

        records: list[dict[str, Any]] = []
        previous: Optional[str] = None
        seen_entries: set[str] = set()
        seen_resolutions: set[str] = set()
        for line_no, raw in enumerate(lines, 1):
            if not raw.strip():
                raise ShadowLedgerError(f"ledger {self.path} contains a blank line at {line_no}")
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ShadowLedgerError(f"ledger {self.path} has invalid JSON at line {line_no}") from exc
            if not isinstance(record, dict):
                raise ShadowLedgerError(f"ledger {self.path} line {line_no} is not an object")
            if record.get("schema_version") != SCHEMA_VERSION:
                raise ShadowLedgerError(f"ledger {self.path} line {line_no} has unsupported schema")
            _utc_datetime(record.get("ledger_recorded_at_utc"), "ledger_recorded_at_utc")
            if record.get("previous_record_hash") != previous:
                raise ShadowLedgerError(f"ledger {self.path} hash chain breaks at line {line_no}")
            claimed = record.get("record_hash")
            payload = dict(record)
            payload.pop("record_hash", None)
            if not isinstance(claimed, str) or claimed != _sha256(payload):
                raise ShadowLedgerError(f"ledger {self.path} hash mismatch at line {line_no}")
            kind = record.get("record_type")
            if kind == "entry":
                entry = ShadowEntry.from_mapping(record)
                if entry.entry_id != record.get("entry_id") or entry.entry_id in seen_entries:
                    raise ShadowLedgerError(f"ledger {self.path} invalid or duplicate entry at line {line_no}")
                recorded_at = _utc_datetime(
                    record.get("ledger_recorded_at_utc"), "ledger_recorded_at_utc"
                )
                if recorded_at > _utc_datetime(entry.entry_target_at_utc, "entry_target_at_utc"):
                    raise ShadowLedgerError(
                        f"ledger {self.path} contains a post-horizon entry at line {line_no}"
                    )
                seen_entries.add(entry.entry_id)
            elif kind == "resolution":
                resolution = ShadowResolution.from_mapping(record)
                if resolution.resolution_id != record.get("resolution_id"):
                    raise ShadowLedgerError(f"ledger {self.path} invalid resolution at line {line_no}")
                if resolution.entry_id not in seen_entries or resolution.resolution_id in seen_resolutions:
                    raise ShadowLedgerError(f"ledger {self.path} invalid resolution linkage at line {line_no}")
                entry = next(
                    candidate
                    for candidate in records
                    if candidate.get("record_type") == "entry"
                    and candidate.get("entry_id") == resolution.entry_id
                )
                self._validate_resolution_against_entry(resolution, entry)
                seen_resolutions.add(resolution.resolution_id)
            else:
                raise ShadowLedgerError(f"ledger {self.path} unknown record type at line {line_no}")
            previous = claimed
            records.append(record)
        return records

    def _append_payloads(
        self,
        records: list[dict[str, Any]],
        payloads: list[dict[str, Any]],
        idempotent: int,
    ) -> LedgerAppendReport:
        if not payloads:
            return LedgerAppendReport(
                added=0,
                idempotent=idempotent,
                total_records=len(records),
                head_hash=records[-1]["record_hash"] if records else None,
            )
        previous = records[-1]["record_hash"] if records else None
        clock_value = self._clock()
        if clock_value.tzinfo is None:
            raise ShadowLedgerError("ledger clock must return a timezone-aware UTC datetime")
        now = clock_value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        new_records: list[dict[str, Any]] = []
        for payload in payloads:
            if payload["record_type"] == "entry" and _utc_datetime(now, "ledger_recorded_at_utc") > _utc_datetime(
                payload["entry_target_at_utc"], "entry_target_at_utc"
            ):
                raise ShadowLedgerError(
                    "entry is being recorded after its declared decision horizon; "
                    "it is not forward evidence"
                )
            record = {
                "schema_version": SCHEMA_VERSION,
                "ledger_recorded_at_utc": now,
                **payload,
                "previous_record_hash": previous,
            }
            record["record_hash"] = _sha256(record)
            previous = record["record_hash"]
            new_records.append(record)
        self._write_atomically([*records, *new_records])
        return LedgerAppendReport(
            added=len(new_records),
            idempotent=idempotent,
            total_records=len(records) + len(new_records),
            head_hash=previous,
        )

    @staticmethod
    def _payload_without_chain(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in record.items()
            if key not in {"schema_version", "ledger_recorded_at_utc", "previous_record_hash", "record_hash"}
        }

    @staticmethod
    def _assert_no_duplicate_ids(values: list[str], label: str) -> None:
        if len(values) != len(set(values)):
            duplicate = next(value for value in values if values.count(value) > 1)
            raise ShadowLedgerError(f"{label} has duplicate immutable id {duplicate}")

    @staticmethod
    def _validate_resolution_against_entry(
        resolution: ShadowResolution, entry: Mapping[str, Any]
    ) -> None:
        entry_quote = _utc_datetime(entry["entry_quote_at_utc"], "entry_quote_at_utc")
        start = _utc_datetime(entry["official_start_time_utc"], "official_start_time_utc")
        settled = _utc_datetime(resolution.settled_at_utc, "settled_at_utc")
        if resolution.close_quote_at_utc is not None:
            close_time = _utc_datetime(resolution.close_quote_at_utc, "close_quote_at_utc")
            if close_time < entry_quote or close_time >= start:
                raise ShadowLedgerError(
                    "close_quote_at_utc must be on/after the entry quote and before official_start_time_utc"
                )
        if resolution.settlement_status == "graded" and settled < start:
            raise ShadowLedgerError(
                "graded settlement is timestamped before official_start_time_utc; "
                "it cannot be an official post-game outcome"
            )

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        """Serialize read-verify-append so concurrent writers cannot lose a record."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            try:
                yield
            finally:
                unlock()

    def _write_atomically(self, records: list[Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        tmp = Path(raw_tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                for record in records:
                    handle.write(_canonical_json(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(self.path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            finally:
                raise
