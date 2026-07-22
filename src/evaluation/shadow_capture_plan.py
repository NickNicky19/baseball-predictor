"""Hard-keyed expectation and accountability contract for forward captures.

The forward ledger proves what was selected *after* a valid live quote exists.
It cannot distinguish a quiet slate from an external collector that never ran.
This module supplies that missing operational evidence without pretending an
empty market response is a model or scheduler success.

It intentionally does not fetch odds, resolve names, or select a quote.  A
source adapter must do those things at the proper boundary and publish a
separate hard-keyed ledger quote.  Here we record only:

* which official MLB games were expected at the declared horizon;
* whether the collector produced one terminal observation for each due game;
* whether that observation found market rows, found none, or failed.

``no_eligible_market`` is observed coverage, not a missing capture.  A
``source_error`` is not coverage, even if it has a well-formed error artifact.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping


SCHEMA_VERSION = "shadow-capture-plan-v1"
ATTEMPT_SCHEMA_VERSION = "shadow-capture-attempt-v2"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
CaptureOutcome = Literal["captured", "no_eligible_market", "source_error"]


class ShadowCapturePlanError(ValueError):
    """Raised when expected capture evidence is incomplete or ambiguous."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _hash(value: Any, label: str) -> str:
    out = str(value).strip().lower()
    if not _SHA256.fullmatch(out):
        raise ShadowCapturePlanError(f"{label} must be a 64-character SHA-256 digest")
    return out


def _utc(value: Any, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ShadowCapturePlanError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ShadowCapturePlanError(f"{label} must include an explicit timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso_date(value: Any, label: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ShadowCapturePlanError(f"{label} must be YYYY-MM-DD") from exc


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ShadowCapturePlanError(f"{label} must be a positive integer")
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise ShadowCapturePlanError(f"{label} must be a positive integer") from exc
    if out <= 0:
        raise ShadowCapturePlanError(f"{label} must be positive")
    return out


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ShadowCapturePlanError(f"{label} must be a non-negative integer")
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise ShadowCapturePlanError(f"{label} must be a non-negative integer") from exc
    if out < 0:
        raise ShadowCapturePlanError(f"{label} must be non-negative")
    return out


def _nonempty(value: Any, label: str) -> str:
    out = str(value).strip()
    if not out:
        raise ShadowCapturePlanError(f"{label} cannot be blank")
    return out


@dataclass(frozen=True)
class CaptureTarget:
    """One official game that must receive a capture attempt at T-horizon."""

    mlb_game_pk: int
    official_game_date: str
    official_start_time_utc: str
    entry_target_at_utc: str
    entry_hours: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "mlb_game_pk", _positive_int(self.mlb_game_pk, "mlb_game_pk"))
        object.__setattr__(self, "official_game_date", _iso_date(self.official_game_date, "official_game_date"))
        object.__setattr__(self, "official_start_time_utc", _utc(self.official_start_time_utc, "official_start_time_utc"))
        object.__setattr__(self, "entry_target_at_utc", _utc(self.entry_target_at_utc, "entry_target_at_utc"))
        object.__setattr__(self, "entry_hours", _positive_int(self.entry_hours, "entry_hours"))
        expected = _utc_dt(self.official_start_time_utc) - timedelta(hours=self.entry_hours)
        if _utc_dt(self.entry_target_at_utc) != expected:
            raise ShadowCapturePlanError(
                "entry_target_at_utc must equal official_start_time_utc minus entry_hours; "
                "a capture target is a computed fact, not an editable timestamp"
            )

    @property
    def target_id(self) -> str:
        return _sha256({
            "schema_version": SCHEMA_VERSION,
            "mlb_game_pk": self.mlb_game_pk,
            "official_start_time_utc": self.official_start_time_utc,
            "entry_hours": self.entry_hours,
        })

    def to_dict(self) -> dict[str, Any]:
        return {"target_id": self.target_id, **asdict(self)}

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "CaptureTarget":
        required = {
            "target_id", "mlb_game_pk", "official_game_date",
            "official_start_time_utc", "entry_target_at_utc", "entry_hours",
        }
        missing = sorted(required - set(row))
        if missing:
            raise ShadowCapturePlanError(f"capture target is missing fields {missing}")
        target = cls(**{key: row[key] for key in required - {"target_id"}})
        if _hash(row["target_id"], "target_id") != target.target_id:
            raise ShadowCapturePlanError("capture target_id does not match its immutable fields")
        return target


@dataclass(frozen=True)
class ShadowCapturePlan:
    """Content-addressed schedule input for one collection slate."""

    official_game_date: str
    entry_hours: int
    policy_sha256: str
    schedule_snapshot_sha256: str
    targets: tuple[CaptureTarget, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "official_game_date", _iso_date(self.official_game_date, "official_game_date"))
        object.__setattr__(self, "entry_hours", _positive_int(self.entry_hours, "entry_hours"))
        object.__setattr__(self, "policy_sha256", _hash(self.policy_sha256, "policy_sha256"))
        object.__setattr__(self, "schedule_snapshot_sha256", _hash(self.schedule_snapshot_sha256, "schedule_snapshot_sha256"))
        targets = tuple(self.targets)
        for target in targets:
            if not isinstance(target, CaptureTarget):
                raise ShadowCapturePlanError("targets must contain CaptureTarget values")
            if target.official_game_date != self.official_game_date:
                raise ShadowCapturePlanError("target official_game_date differs from plan date")
            if target.entry_hours != self.entry_hours:
                raise ShadowCapturePlanError("target entry_hours differs from plan entry_hours")
        ids = [target.target_id for target in targets]
        if len(set(ids)) != len(ids):
            raise ShadowCapturePlanError("duplicate capture targets; do not silently deduplicate games")
        object.__setattr__(self, "targets", tuple(sorted(targets, key=lambda target: target.target_id)))

    @property
    def plan_sha256(self) -> str:
        return _sha256({
            "schema_version": SCHEMA_VERSION,
            "official_game_date": self.official_game_date,
            "entry_hours": self.entry_hours,
            "policy_sha256": self.policy_sha256,
            "schedule_snapshot_sha256": self.schedule_snapshot_sha256,
            "targets": [target.to_dict() for target in self.targets],
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "plan_sha256": self.plan_sha256,
            "official_game_date": self.official_game_date,
            "entry_hours": self.entry_hours,
            "policy_sha256": self.policy_sha256,
            "schedule_snapshot_sha256": self.schedule_snapshot_sha256,
            "targets": [target.to_dict() for target in self.targets],
        }

    def write(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ShadowCapturePlan":
        required = {
            "schema_version", "plan_sha256", "official_game_date", "entry_hours",
            "policy_sha256", "schedule_snapshot_sha256", "targets",
        }
        missing = sorted(required - set(row))
        if missing:
            raise ShadowCapturePlanError(f"capture plan is missing fields {missing}")
        if row["schema_version"] != SCHEMA_VERSION:
            raise ShadowCapturePlanError(
                f"unknown capture plan schema {row['schema_version']!r}; refusing best-effort parse"
            )
        targets = row["targets"]
        if not isinstance(targets, list):
            raise ShadowCapturePlanError("capture plan targets must be a list")
        plan = cls(
            official_game_date=row["official_game_date"],
            entry_hours=row["entry_hours"],
            policy_sha256=row["policy_sha256"],
            schedule_snapshot_sha256=row["schedule_snapshot_sha256"],
            targets=tuple(CaptureTarget.from_mapping(target) for target in targets),
        )
        if _hash(row["plan_sha256"], "plan_sha256") != plan.plan_sha256:
            raise ShadowCapturePlanError("capture plan hash does not match its immutable contents")
        return plan


@dataclass(frozen=True)
class CaptureAttempt:
    """Terminal result of one collector's attempt for one scheduled target.

    ``source_payload_sha256`` hashes the immutable provider-response/error
    artifact retained by the collector.  The receipt itself is not a market
    quote and therefore cannot be converted into a ledger entry.
    """

    target_id: str
    plan_sha256: str
    started_at_utc: str
    completed_at_utc: str
    source_name: str
    source_payload_sha256: str
    outcome: CaptureOutcome
    resolved_two_sided_quote_count: int
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_id", _hash(self.target_id, "target_id"))
        object.__setattr__(self, "plan_sha256", _hash(self.plan_sha256, "plan_sha256"))
        object.__setattr__(self, "started_at_utc", _utc(self.started_at_utc, "started_at_utc"))
        object.__setattr__(self, "completed_at_utc", _utc(self.completed_at_utc, "completed_at_utc"))
        if _utc_dt(self.started_at_utc) > _utc_dt(self.completed_at_utc):
            raise ShadowCapturePlanError("started_at_utc cannot be after completed_at_utc")
        object.__setattr__(self, "source_name", _nonempty(self.source_name, "source_name").lower())
        object.__setattr__(self, "source_payload_sha256", _hash(self.source_payload_sha256, "source_payload_sha256"))
        outcome = _nonempty(self.outcome, "outcome")
        if outcome not in {"captured", "no_eligible_market", "source_error"}:
            raise ShadowCapturePlanError("outcome must be captured, no_eligible_market, or source_error")
        object.__setattr__(self, "outcome", outcome)
        count = _nonnegative_int(
            self.resolved_two_sided_quote_count, "resolved_two_sided_quote_count"
        )
        object.__setattr__(self, "resolved_two_sided_quote_count", count)
        detail = str(self.detail).strip()
        object.__setattr__(self, "detail", detail)
        if outcome == "captured" and count <= 0:
            raise ShadowCapturePlanError("captured attempt must report at least one resolved two-sided quote")
        if outcome == "no_eligible_market" and count != 0:
            raise ShadowCapturePlanError("no_eligible_market attempt must report zero resolved quotes")
        if outcome == "source_error":
            if count != 0:
                raise ShadowCapturePlanError("source_error attempt must report zero resolved quotes")
            if not detail:
                raise ShadowCapturePlanError("source_error attempt requires a redacted detail")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": ATTEMPT_SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "CaptureAttempt":
        required = {
            "schema_version", "target_id", "plan_sha256", "completed_at_utc", "source_name",
            "source_payload_sha256", "outcome", "resolved_two_sided_quote_count",
        }
        missing = sorted(required - set(row))
        if missing:
            raise ShadowCapturePlanError(f"capture attempt is missing fields {missing}")
        if row["schema_version"] != ATTEMPT_SCHEMA_VERSION:
            raise ShadowCapturePlanError(
                f"unknown capture attempt schema {row['schema_version']!r}; refusing best-effort parse"
            )
        return cls(**{key: row.get(key, "") for key in cls.__dataclass_fields__})  # type: ignore[attr-defined]


@dataclass(frozen=True)
class CaptureCoverageReport:
    """Count-preserving assessment of due capture targets."""

    plan_sha256: str
    assessed_at_utc: str
    due_targets: int
    future_targets: int
    captured_targets: int
    no_eligible_market_targets: int
    source_error_targets: int
    missing_target_ids: tuple[str, ...]

    @property
    def observed_targets(self) -> int:
        return self.captured_targets + self.no_eligible_market_targets

    @property
    def complete(self) -> bool:
        return not self.missing_target_ids and self.source_error_targets == 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "observed_targets": self.observed_targets,
            "complete": self.complete,
        }


def plan_from_schedule(
    *,
    official_game_date: str,
    entry_hours: int,
    policy_sha256: str,
    schedule_snapshot: Iterable[Mapping[str, Any]],
) -> ShadowCapturePlan:
    """Build an exact-horizon plan from a retained official schedule snapshot.

    Required source fields are deliberately MLB-shaped: ``gamePk``,
    ``officialDate``, and ``gameDate``.  The caller must retain the same
    source snapshot whose hash enters the plan; a display-only schedule or a
    guessed local start time is not enough.
    """
    plan_date = _iso_date(official_game_date, "official_game_date")
    horizon = _positive_int(entry_hours, "entry_hours")
    records = [dict(record) for record in schedule_snapshot]
    snapshot_sha = _sha256({"schedule": records})
    targets: list[CaptureTarget] = []
    seen_game_pks: set[int] = set()
    for record in records:
        try:
            game_pk = _positive_int(record["gamePk"], "schedule.gamePk")
            source_date = _iso_date(record["officialDate"], "schedule.officialDate")
            start = _utc(record["gameDate"], "schedule.gameDate")
        except KeyError as exc:
            raise ShadowCapturePlanError(f"schedule record missing required field {exc.args[0]!r}") from exc
        if source_date != plan_date:
            # MLB can return a game from an adjacent official date inside a
            # date-filtered schedule response (for example around a
            # reschedule).  Keep that row in the retained, hash-bound source
            # snapshot, but never relabel it or create a target for it.
            continue
        if game_pk in seen_game_pks:
            raise ShadowCapturePlanError(
                f"schedule snapshot has duplicate game_pk {game_pk}; do not silently deduplicate a game identity"
            )
        seen_game_pks.add(game_pk)
        target = _utc_dt(start) - timedelta(hours=horizon)
        targets.append(CaptureTarget(
            mlb_game_pk=game_pk,
            official_game_date=plan_date,
            official_start_time_utc=start,
            entry_target_at_utc=target.strftime("%Y-%m-%dT%H:%M:%SZ"),
            entry_hours=horizon,
        ))
    return ShadowCapturePlan(
        official_game_date=plan_date,
        entry_hours=horizon,
        policy_sha256=policy_sha256,
        schedule_snapshot_sha256=snapshot_sha,
        targets=tuple(targets),
    )


def canonical_schedule_records(
    schedule_snapshot: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only schedule fields that define a capture/game identity.

    Hydrated MLB schedule rows contain mutable status and presentation fields.
    Binding those unrelated fields would make a harmless display update look
    like a start-time or team-identity change.  This projection is explicit:
    no date, time, gamePk, or team-name drift is hidden.
    """

    canonical: list[dict[str, Any]] = []
    for record in schedule_snapshot:
        try:
            home = record["teams"]["home"]["team"]["name"]
            away = record["teams"]["away"]["team"]["name"]
            row = {
                "gamePk": record["gamePk"],
                "officialDate": record["officialDate"],
                "gameDate": record["gameDate"],
                "teams": {
                    "home": {"team": {"name": _nonempty(home, "schedule.home_team")}},
                    "away": {"team": {"name": _nonempty(away, "schedule.away_team")}},
                },
            }
        except (KeyError, TypeError) as exc:
            raise ShadowCapturePlanError(
                "schedule record lacks exact home/away team identity"
            ) from exc
        canonical.append(row)
    return canonical


def assess_capture_attempts(
    plan: ShadowCapturePlan,
    attempts: Iterable[CaptureAttempt],
    *,
    assessed_at_utc: str,
) -> CaptureCoverageReport:
    """Assess terminal collector accountability without inventing quote facts."""
    as_of = _utc(assessed_at_utc, "assessed_at_utc")
    expected = {target.target_id: target for target in plan.targets}
    seen: dict[str, CaptureAttempt] = {}
    for attempt in attempts:
        if not isinstance(attempt, CaptureAttempt):
            raise ShadowCapturePlanError("attempts must contain CaptureAttempt values")
        if attempt.plan_sha256 != plan.plan_sha256:
            raise ShadowCapturePlanError("capture attempt is bound to a different plan hash")
        if attempt.target_id not in expected:
            raise ShadowCapturePlanError("capture attempt references an unknown target")
        if attempt.target_id in seen:
            raise ShadowCapturePlanError(
                "multiple terminal attempts for one target; retries must be resolved before "
                "publishing the immutable terminal receipt"
            )
        target = expected[attempt.target_id]
        if _utc_dt(attempt.started_at_utc) > _utc_dt(target.entry_target_at_utc):
            raise ShadowCapturePlanError("capture attempt started after its declared entry target")
        if _utc_dt(attempt.completed_at_utc) > _utc_dt(target.entry_target_at_utc):
            raise ShadowCapturePlanError("capture attempt completed after its declared entry target")
        seen[attempt.target_id] = attempt

    due = [target for target in plan.targets if _utc_dt(target.entry_target_at_utc) <= _utc_dt(as_of)]
    future = len(plan.targets) - len(due)
    missing = tuple(sorted(target.target_id for target in due if target.target_id not in seen))
    due_attempts = [seen[target.target_id] for target in due if target.target_id in seen]
    return CaptureCoverageReport(
        plan_sha256=plan.plan_sha256,
        assessed_at_utc=as_of,
        due_targets=len(due),
        future_targets=future,
        captured_targets=sum(attempt.outcome == "captured" for attempt in due_attempts),
        no_eligible_market_targets=sum(attempt.outcome == "no_eligible_market" for attempt in due_attempts),
        source_error_targets=sum(attempt.outcome == "source_error" for attempt in due_attempts),
        missing_target_ids=missing,
    )


def load_capture_plan(path: str | Path) -> ShadowCapturePlan:
    """Load and content-verify a persisted capture plan."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise ShadowCapturePlanError(f"capture plan is not valid JSON: {source}") from exc
    if not isinstance(payload, Mapping):
        raise ShadowCapturePlanError("capture plan root must be an object")
    return ShadowCapturePlan.from_mapping(payload)


def load_capture_attempts(path: str | Path) -> list[CaptureAttempt]:
    """Load append-only JSONL terminal receipts; blank lines are ignored."""
    source = Path(path)
    attempts: list[CaptureAttempt] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ShadowCapturePlanError(
                f"capture attempt JSONL is malformed at {source}:{line_number}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ShadowCapturePlanError(
                f"capture attempt JSONL row is not an object at {source}:{line_number}"
            )
        attempts.append(CaptureAttempt.from_mapping(payload))
    return attempts
