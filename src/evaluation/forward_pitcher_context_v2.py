"""Team-ID-bound future-only probable-pitcher evidence.

Version 1 retained the raw schedule hash and pitcher IDs but discarded the
official team IDs needed by a batter-side consumer.  This version keeps those
IDs at the parser boundary.  It is a research input contract, not a matchup
effect or a prediction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping

from src.evaluation.forward_pitcher_context import ProbablePitcher
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan


SCHEMA_VERSION = "forward-pitcher-context-v2"


class ForwardPitcherContextV2Error(ValueError):
    pass


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ForwardPitcherContextV2Error(f"{label} must be a positive integer")
    return value


def _sha(value: object, label: str) -> str:
    text = str(value).strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ForwardPitcherContextV2Error(f"{label} must be a lowercase SHA-256 digest")
    return text


def _utc(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ForwardPitcherContextV2Error(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ForwardPitcherContextV2Error(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForwardPitcherContextV2Error(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_date(value: object) -> str:
    if not isinstance(value, str):
        raise ForwardPitcherContextV2Error("official_game_date must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ForwardPitcherContextV2Error("official_game_date must be YYYY-MM-DD") from exc
    if value != parsed.isoformat():
        raise ForwardPitcherContextV2Error("official_game_date must be canonical")
    if parsed.year == 2026 and parsed.month == 5:
        raise ForwardPitcherContextV2Error("May 2026 is sealed")
    return value


@dataclass(frozen=True)
class ForwardPitcherContextV2:
    target_id: str
    plan_sha256: str
    captured_at_utc: str
    source_name: str
    source_payload_sha256: str
    mlb_game_pk: int
    official_game_date: str
    official_start_time_utc: str
    game_type: str
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    home_probable_pitcher: ProbablePitcher
    away_probable_pitcher: ProbablePitcher

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_id", _sha(self.target_id, "target_id"))
        object.__setattr__(self, "plan_sha256", _sha(self.plan_sha256, "plan_sha256"))
        object.__setattr__(self, "captured_at_utc", _utc(self.captured_at_utc, "captured_at_utc"))
        if self.source_name != "mlb_statsapi_schedule":
            raise ForwardPitcherContextV2Error("source_name must be mlb_statsapi_schedule")
        object.__setattr__(self, "source_payload_sha256", _sha(self.source_payload_sha256, "source_payload_sha256"))
        object.__setattr__(self, "mlb_game_pk", _positive_int(self.mlb_game_pk, "mlb_game_pk"))
        object.__setattr__(self, "official_game_date", _canonical_date(self.official_game_date))
        object.__setattr__(self, "official_start_time_utc", _utc(self.official_start_time_utc, "official_start_time_utc"))
        if self.game_type != "R":
            raise ForwardPitcherContextV2Error("only regular-season games are eligible")
        object.__setattr__(self, "home_team_id", _positive_int(self.home_team_id, "home_team_id"))
        object.__setattr__(self, "away_team_id", _positive_int(self.away_team_id, "away_team_id"))
        if self.home_team_id == self.away_team_id:
            raise ForwardPitcherContextV2Error("home and away team IDs must differ")
        if not self.home_team_name.strip() or not self.away_team_name.strip():
            raise ForwardPitcherContextV2Error("team names cannot be blank")
        if not isinstance(self.home_probable_pitcher, ProbablePitcher) or not isinstance(self.away_probable_pitcher, ProbablePitcher):
            raise ForwardPitcherContextV2Error("probable-pitcher records are invalid")

    @property
    def candidate_input_eligible(self) -> bool:
        return self.home_probable_pitcher.status == "resolved" and self.away_probable_pitcher.status == "resolved"

    def bind_target(self, target: CaptureTarget) -> "ForwardPitcherContextV2":
        if self.target_id != target.target_id or self.mlb_game_pk != target.mlb_game_pk:
            raise ForwardPitcherContextV2Error("pitcher context target identity differs")
        if self.official_game_date != target.official_game_date or self.official_start_time_utc != target.official_start_time_utc:
            raise ForwardPitcherContextV2Error("pitcher context game identity differs")
        if datetime.fromisoformat(self.captured_at_utc.replace("Z", "+00:00")) > datetime.fromisoformat(target.entry_target_at_utc.replace("Z", "+00:00")):
            raise ForwardPitcherContextV2Error("pitcher context arrived after T-minus-4")
        return self

    @property
    def context_sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.to_dict(include_hash=False))).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        row = {"schema_version": SCHEMA_VERSION, **asdict(self), "candidate_input_eligible": self.candidate_input_eligible}
        if include_hash:
            row["context_sha256"] = self.context_sha256
        return row

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ForwardPitcherContextV2":
        required = {
            "schema_version", "context_sha256", "target_id", "plan_sha256", "captured_at_utc", "source_name",
            "source_payload_sha256", "mlb_game_pk", "official_game_date", "official_start_time_utc", "game_type",
            "home_team_id", "home_team_name", "away_team_id", "away_team_name", "home_probable_pitcher",
            "away_probable_pitcher", "candidate_input_eligible",
        }
        if set(row) != required or row.get("schema_version") != SCHEMA_VERSION:
            raise ForwardPitcherContextV2Error("pitcher context v2 schema changed")
        def pitcher(value: object) -> ProbablePitcher:
            if not isinstance(value, Mapping):
                raise ForwardPitcherContextV2Error("probable pitcher must be an object")
            return ProbablePitcher(status=value.get("status"), player_id=value.get("player_id"))
        context = cls(
            **{key: row[key] for key in required - {"schema_version", "context_sha256", "candidate_input_eligible", "home_probable_pitcher", "away_probable_pitcher"}},
            home_probable_pitcher=pitcher(row["home_probable_pitcher"]),
            away_probable_pitcher=pitcher(row["away_probable_pitcher"]),
        )
        if context.context_sha256 != _sha(row["context_sha256"], "context_sha256"):
            raise ForwardPitcherContextV2Error("pitcher context v2 hash differs")
        if row["candidate_input_eligible"] is not context.candidate_input_eligible:
            raise ForwardPitcherContextV2Error("pitcher context eligibility was edited")
        return context


def context_v2_from_schedule(*, target: CaptureTarget, plan: ShadowCapturePlan, captured_at_utc: str, source_payload_sha256: str, schedule_games: list[Mapping[str, Any]]) -> ForwardPitcherContextV2:
    if target.target_id not in {candidate.target_id for candidate in plan.targets}:
        raise ForwardPitcherContextV2Error("target is not in the capture plan")
    matches = [game for game in schedule_games if isinstance(game, Mapping) and game.get("gamePk") == target.mlb_game_pk]
    if len(matches) != 1:
        raise ForwardPitcherContextV2Error("schedule response has no unique target game")
    game = matches[0]
    try:
        home = game["teams"]["home"]
        away = game["teams"]["away"]
        context = ForwardPitcherContextV2(
            target_id=target.target_id, plan_sha256=plan.plan_sha256, captured_at_utc=captured_at_utc,
            source_name="mlb_statsapi_schedule", source_payload_sha256=source_payload_sha256,
            mlb_game_pk=game["gamePk"], official_game_date=game["officialDate"], official_start_time_utc=game["gameDate"],
            game_type=game["gameType"], home_team_id=home["team"]["id"], home_team_name=home["team"]["name"],
            away_team_id=away["team"]["id"], away_team_name=away["team"]["name"],
            home_probable_pitcher=ProbablePitcher.from_schedule_side(home), away_probable_pitcher=ProbablePitcher.from_schedule_side(away),
        )
    except (KeyError, TypeError) as exc:
        raise ForwardPitcherContextV2Error("schedule target lacks official team identity") from exc
    return context.bind_target(target)
