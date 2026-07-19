"""Future-only, T-horizon evidence for opposing probable pitchers.

Historical MLB schedule responses may show the pitcher who ultimately started,
but do not prove that pitcher was known at a past decision time.  This module
therefore records only a schedule response received no later than the bound
T-horizon.  It is input evidence for a later challenger, never a prediction,
selection, or betting decision.

Every target receives one immutable observation.  Missing probable pitchers
are retained explicitly; they are not quietly dropped or replaced with the
post-game starter.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan


SCHEMA_VERSION = "forward-pitcher-context-v1"
PitcherStatus = Literal["resolved", "unavailable", "malformed"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ForwardPitcherContextError(ValueError):
    """A pitcher context observation cannot prove a pregame input fact."""


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _hash(value: object, label: str) -> str:
    text = str(value).strip().lower()
    if not _SHA256.fullmatch(text):
        raise ForwardPitcherContextError(f"{label} must be a SHA-256 digest")
    return text


def _utc(value: object, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ForwardPitcherContextError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ForwardPitcherContextError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ForwardPitcherContextError(f"{label} must be a positive integer")
    try:
        output = int(value)
    except (TypeError, ValueError) as exc:
        raise ForwardPitcherContextError(f"{label} must be a positive integer") from exc
    if output <= 0:
        raise ForwardPitcherContextError(f"{label} must be a positive integer")
    return output


def _team(value: object, label: str) -> str:
    output = str(value).strip()
    if not output:
        raise ForwardPitcherContextError(f"{label} cannot be blank")
    return output


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _bound_target(plan: ShadowCapturePlan, target: CaptureTarget) -> None:
    if not isinstance(plan, ShadowCapturePlan) or not isinstance(target, CaptureTarget):
        raise ForwardPitcherContextError("context requires exact capture-plan and target objects")
    if target.target_id not in {candidate.target_id for candidate in plan.targets}:
        raise ForwardPitcherContextError("target is not a member of the capture plan")


@dataclass(frozen=True)
class ProbablePitcher:
    """One team-side observation, including absence as a first-class fact."""

    status: PitcherStatus
    player_id: int | None

    def __post_init__(self) -> None:
        if self.status not in {"resolved", "unavailable", "malformed"}:
            raise ForwardPitcherContextError("probable pitcher status is invalid")
        if self.status == "resolved":
            object.__setattr__(self, "player_id", _positive_int(self.player_id, "probable pitcher player_id"))
        elif self.player_id is not None:
            raise ForwardPitcherContextError("unavailable or malformed pitcher cannot carry a player ID")

    @classmethod
    def from_schedule_side(cls, side: Mapping[str, Any]) -> "ProbablePitcher":
        raw = side.get("probablePitcher")
        if raw is None:
            return cls(status="unavailable", player_id=None)
        if not isinstance(raw, Mapping):
            return cls(status="malformed", player_id=None)
        try:
            return cls(status="resolved", player_id=_positive_int(raw.get("id"), "probablePitcher.id"))
        except ForwardPitcherContextError:
            return cls(status="malformed", player_id=None)


@dataclass(frozen=True)
class ForwardPitcherContext:
    """A hard target-bound record of what the official schedule exposed."""

    target_id: str
    plan_sha256: str
    captured_at_utc: str
    source_name: str
    source_payload_sha256: str
    mlb_game_pk: int
    official_game_date: str
    official_start_time_utc: str
    game_type: str
    home_team: str
    away_team: str
    home_probable_pitcher: ProbablePitcher
    away_probable_pitcher: ProbablePitcher

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_id", _hash(self.target_id, "target_id"))
        object.__setattr__(self, "plan_sha256", _hash(self.plan_sha256, "plan_sha256"))
        object.__setattr__(self, "captured_at_utc", _utc(self.captured_at_utc, "captured_at_utc"))
        if str(self.source_name).strip().lower() != "mlb_statsapi_schedule":
            raise ForwardPitcherContextError("source_name must be mlb_statsapi_schedule")
        object.__setattr__(self, "source_name", "mlb_statsapi_schedule")
        object.__setattr__(self, "source_payload_sha256", _hash(self.source_payload_sha256, "source_payload_sha256"))
        object.__setattr__(self, "mlb_game_pk", _positive_int(self.mlb_game_pk, "mlb_game_pk"))
        object.__setattr__(self, "official_game_date", str(self.official_game_date).strip())
        object.__setattr__(self, "official_start_time_utc", _utc(self.official_start_time_utc, "official_start_time_utc"))
        game_type = str(self.game_type).strip().upper()
        if len(game_type) != 1:
            raise ForwardPitcherContextError("game_type must be a one-character MLB game type")
        object.__setattr__(self, "game_type", game_type)
        object.__setattr__(self, "home_team", _team(self.home_team, "home_team"))
        object.__setattr__(self, "away_team", _team(self.away_team, "away_team"))
        if not isinstance(self.home_probable_pitcher, ProbablePitcher) or not isinstance(self.away_probable_pitcher, ProbablePitcher):
            raise ForwardPitcherContextError("probable pitcher values must be ProbablePitcher records")

    @property
    def candidate_input_eligible(self) -> bool:
        return (
            self.game_type == "R"
            and self.home_probable_pitcher.status == "resolved"
            and self.away_probable_pitcher.status == "resolved"
        )

    def bind_target(self, target: CaptureTarget) -> "ForwardPitcherContext":
        if self.target_id != target.target_id or self.plan_sha256 == "":
            raise ForwardPitcherContextError("context target identity does not match its target")
        if self.mlb_game_pk != target.mlb_game_pk:
            raise ForwardPitcherContextError("context MLB game does not match target")
        if self.official_game_date != target.official_game_date or self.official_start_time_utc != target.official_start_time_utc:
            raise ForwardPitcherContextError("context official game identity does not match target")
        if _utc_dt(self.captured_at_utc) > _utc_dt(target.entry_target_at_utc):
            raise ForwardPitcherContextError("pitcher context was captured after the declared T-horizon")
        return self

    @property
    def context_sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.to_dict(include_hash=False))).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = {
            "schema_version": SCHEMA_VERSION,
            **asdict(self),
            "candidate_input_eligible": self.candidate_input_eligible,
        }
        if include_hash:
            payload["context_sha256"] = self.context_sha256
        return payload

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ForwardPitcherContext":
        required = {
            "schema_version", "context_sha256", "target_id", "plan_sha256", "captured_at_utc", "source_name", "source_payload_sha256",
            "mlb_game_pk", "official_game_date", "official_start_time_utc", "game_type", "home_team", "away_team",
            "home_probable_pitcher", "away_probable_pitcher", "candidate_input_eligible",
        }
        missing = sorted(required - set(row))
        if missing or row.get("schema_version") != SCHEMA_VERSION:
            raise ForwardPitcherContextError("context has missing fields or an unknown schema")
        def pitcher(value: object) -> ProbablePitcher:
            if not isinstance(value, Mapping):
                raise ForwardPitcherContextError("probable pitcher record must be an object")
            return ProbablePitcher(status=value.get("status"), player_id=value.get("player_id"))
        context = cls(
            **{key: row[key] for key in (
                "target_id", "plan_sha256", "captured_at_utc", "source_name", "source_payload_sha256", "mlb_game_pk", "official_game_date",
                "official_start_time_utc", "game_type", "home_team", "away_team"
            )},
            home_probable_pitcher=pitcher(row["home_probable_pitcher"]),
            away_probable_pitcher=pitcher(row["away_probable_pitcher"]),
        )
        if _hash(row["context_sha256"], "context_sha256") != context.context_sha256:
            raise ForwardPitcherContextError("context hash does not match immutable fields")
        if bool(row["candidate_input_eligible"]) != context.candidate_input_eligible:
            raise ForwardPitcherContextError("candidate eligibility was edited")
        return context


def context_from_schedule(
    *,
    target: CaptureTarget,
    plan: ShadowCapturePlan,
    captured_at_utc: str,
    source_payload_sha256: str,
    schedule_games: list[Mapping[str, Any]],
) -> ForwardPitcherContext:
    """Create one context fact from the retained raw MLB schedule response."""
    _bound_target(plan, target)
    matches = [row for row in schedule_games if isinstance(row, Mapping) and row.get("gamePk") == target.mlb_game_pk]
    if len(matches) != 1:
        raise ForwardPitcherContextError("schedule response has no unique target game")
    game = matches[0]
    try:
        teams = game["teams"]
        home = teams["home"]
        away = teams["away"]
        home_name = home["team"]["name"]
        away_name = away["team"]["name"]
    except (KeyError, TypeError) as exc:
        raise ForwardPitcherContextError("schedule target lacks exact team identity") from exc
    context = ForwardPitcherContext(
        target_id=target.target_id,
        plan_sha256=plan.plan_sha256,
        captured_at_utc=captured_at_utc,
        source_name="mlb_statsapi_schedule",
        source_payload_sha256=source_payload_sha256,
        mlb_game_pk=game.get("gamePk"),
        official_game_date=game.get("officialDate"),
        official_start_time_utc=game.get("gameDate"),
        game_type=game.get("gameType"),
        home_team=home_name,
        away_team=away_name,
        home_probable_pitcher=ProbablePitcher.from_schedule_side(home),
        away_probable_pitcher=ProbablePitcher.from_schedule_side(away),
    )
    return context.bind_target(target)


def publish_context(
    context: ForwardPitcherContext,
    plan: ShadowCapturePlan,
    target: CaptureTarget,
    path: str | Path,
) -> bool:
    """Atomically publish once; a retry may verify but cannot overwrite a fact."""
    _bound_target(plan, target)
    if context.plan_sha256 != plan.plan_sha256:
        raise ForwardPitcherContextError("pitcher context is bound to a different plan")
    context.bind_target(target)
    output = Path(path)
    payload = _canonical_bytes(context.to_dict())
    if output.exists():
        if output.read_bytes() != payload:
            raise ForwardPitcherContextError("conflicting retry cannot overwrite pitcher context")
        return False
    _atomic_write(output, payload)
    return True


def load_context(path: str | Path, plan: ShadowCapturePlan, target: CaptureTarget) -> ForwardPitcherContext:
    try:
        row = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardPitcherContextError("cannot load pitcher context") from exc
    if not isinstance(row, Mapping):
        raise ForwardPitcherContextError("pitcher context root must be an object")
    context = ForwardPitcherContext.from_mapping(row)
    _bound_target(plan, target)
    if context.plan_sha256 != plan.plan_sha256:
        raise ForwardPitcherContextError("pitcher context is bound to a different plan")
    return context.bind_target(target)
