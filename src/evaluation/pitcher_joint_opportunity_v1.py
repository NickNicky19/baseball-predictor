"""Predeclared, fail-closed pitcher joint-opportunity research scaffold.

This module contains no fitted coefficients and is not imported by production.
It validates future T-4 starter identity and strictly-prior workload lineage,
then exposes the exact removal-hazard / coherent-PA mathematics that a later
hash-bound 2023-only fitted artifact may use.  Missing or contradictory input
produces an explicit abstention; no league average or postgame starter is used.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from src.evaluation.forward_pitcher_context import (
    ForwardPitcherContextError,
    games_from_raw_schedule_response,
    load_context,
)
from src.evaluation.forward_pitcher_context_ledger import (
    ForwardPitcherContextLedger,
    ForwardPitcherContextLedgerError,
)
from src.evaluation.forward_pitcher_context_v2 import (
    ForwardPitcherContextV2,
    ForwardPitcherContextV2Error,
    context_v2_from_schedule,
)
from src.evaluation.shadow_capture_plan import (
    CaptureTarget,
    ShadowCapturePlan,
    ShadowCapturePlanError,
    canonical_schedule_records,
    plan_from_schedule,
)


PROTOCOL_SCHEMA_VERSION = "pitcher-joint-opportunity-protocol-v1"
WORKLOAD_SCHEMA_VERSION = "pitcher-pit-workload-history-v1"
MODEL_SCHEMA_VERSION = "pitcher-joint-opportunity-model-v1"
OUTPUT_SCHEMA_VERSION = "pitcher-joint-opportunity-output-v1"
EXPECTED_PROTOCOL_SHA256 = "d349c81b0168b9924f387cbec6973ed5ea7e29d8194905992960e0605b75dbf8"
EVIDENCE_AUTHORITY_RELATIVE_PATH = "config/pitcher_joint_opportunity_v1_evidence_authority.json"
EXPECTED_EVIDENCE_AUTHORITY_SHA256 = "9c29e53656f70ce30bf1db4a415c814ab825f32288b3f482965d6cd5bc452a18"
EVIDENCE_AUTHORITY_SCHEMA_VERSION = "pitcher-joint-opportunity-evidence-authority-v1"
RAW_WORKLOAD_RECEIPT_SCHEMA_VERSION = "official-mlb-pitcher-game-log-transport-v1"
MODEL_AUTHORIZATION_SCHEMA_VERSION = "pitcher-joint-opportunity-model-authorization-v1"
RUNTIME_RELEASE_SCHEMA_VERSION = "pitcher-joint-opportunity-runtime-release-v1"
OUTCOMES = ("K", "BB_HBP", "1B", "2B", "3B", "HR", "OTHER_OUT")
MARKETS = (
    "pitcher_strikeouts",
    "pitcher_walks_hbp",
    "pitcher_hits_allowed",
    "pitcher_home_runs_allowed",
    "pitcher_total_bases_allowed",
)
EVIDENCE_AUTHORITY_BINDING_FIELDS = (
    "archive_era_id",
    "service_owned_root",
    "archive_relative_path",
    "collector_release_sha256",
    "collector_runtime_sha256",
    "official_game_date",
    "mlb_game_pk",
    "pitching_side",
    "pitching_team_id",
    "probable_pitcher_mlb_id",
    "plan_source_receipt_sha256",
    "plan_source_payload_sha256",
    "ledger_manifest_sha256",
    "ledger_terminal_record_sha256",
    "context_sha256",
    "context_raw_payload_sha256",
    "workload_archive_relative_path",
    "workload_raw_receipt_manifest_sha256",
    "workload_feature_artifact_relative_path",
    "workload_feature_artifact_sha256",
    "workload_target_id",
    "workload_assembled_at_utc",
    "workload_max_source_game_date",
    "observation_cutoff_utc",
    "target_t4_utc",
    "workload_parser_code_sha256",
    "workload_source_schema_sha256",
    "workload_feature_code_sha256",
    "protocol_sha256",
)
COUNT_INCREMENT = {
    "pitcher_strikeouts": {"K": 1},
    "pitcher_walks_hbp": {"BB_HBP": 1},
    "pitcher_hits_allowed": {"1B": 1, "2B": 1, "3B": 1, "HR": 1},
    "pitcher_home_runs_allowed": {"HR": 1},
    "pitcher_total_bases_allowed": {"1B": 1, "2B": 2, "3B": 3, "HR": 4},
}
OUTS_TRANSITION_SUPPORT = {
    # A strikeout can record zero outs on an uncaught third strike and can
    # coincide with additional runner outs while the ball remains live.
    "K": (0, 1, 2, 3),
    # The pooled BB/HBP and non-HR hit states can coincide with runner outs.
    "BB_HBP": (0, 1, 2, 3),
    "1B": (0, 1, 2, 3),
    "2B": (0, 1, 2, 3),
    "3B": (0, 1, 2, 3),
    # A home run is a zero-out event. Giving HR positive-out support changes
    # both opportunity and every downstream market distribution.
    "HR": (0,),
    "OTHER_OUT": (1, 2, 3),
}
WORKLOAD_FEATURES = (
    "batters_faced_before",
    "outs_before",
    "rest_days",
    "prior_appearance_count",
    "prior_start_count",
    "prior_relief_count",
    "last_outs",
    "last_batters_faced",
    "last_pitch_count",
    "last_role_start",
    "rolling_mean_outs",
    "rolling_sd_outs",
    "rolling_mean_batters_faced",
    "rolling_sd_batters_faced",
    "rolling_mean_pitch_count",
    "rolling_sd_pitch_count",
    "rolling_start_share",
    "rolling_k_per_bf",
    "rolling_bb_hbp_per_bf",
    "rolling_1b_per_bf",
    "rolling_2b_per_bf",
    "rolling_3b_per_bf",
    "rolling_hr_per_bf",
    "rolling_other_out_per_bf",
)
_BASE_FEATURES = tuple(item for item in WORKLOAD_FEATURES if item not in {"batters_faced_before", "outs_before"})
_LINEAGE_KEYS = {
    "raw_receipt_manifest_sha256",
    "source_schema_sha256",
    "parser_code_sha256",
    "feature_code_sha256",
    "history_payload_sha256",
    "protocol_sha256",
}
_APPEARANCE_KEYS = {
    "official_game_date",
    "mlb_game_pk",
    "role",
    "outs_recorded",
    "batters_faced",
    "pitch_count",
    "strikeouts",
    "walks_hbp",
    "singles_allowed",
    "doubles_allowed",
    "triples_allowed",
    "home_runs_allowed",
    "other_out_events",
    "unclassified_batters_faced",
    "batters_faced_source_truth_valid",
    "pitch_count_source_truth_valid",
    "source_receipt_sha256",
    "source_record_sha256",
}
_WORKLOAD_KEYS = {
    "schema_version",
    "source_kind",
    "target_id",
    "official_game_date",
    "mlb_game_pk",
    "pitcher_id",
    "pitching_team_id",
    "target_horizon_utc",
    "assembled_at_utc",
    "max_source_game_date",
    "prior_appearances",
    "lineage",
    "workload_sha256",
}
_MODEL_KEYS = {
    "schema_version",
    "candidate_id",
    "qualification_state",
    "protocol_sha256",
    "training_start",
    "training_end",
    "rolling_window_appearances",
    "feature_names",
    "hazard_model",
    "outcome_model",
    "outs_transition_model",
    "training_data_sha256",
    "fit_code_sha256",
    "fit_tests_sha256",
    "qualification_report_sha256",
    "random_seed",
    "artifact_sha256",
}
_MODEL_AUTHORIZATION_KEYS = {
    "schema_version",
    "candidate_id",
    "qualification_state",
    "protocol_sha256",
    "artifact_sha256",
    "training_data_sha256",
    "fit_code_sha256",
    "fit_tests_sha256",
    "qualification_report_sha256",
    "research_only",
    "betting_authorized",
    "authorization_sha256",
}
_RUNTIME_RELEASE_KEYS = {
    "schema_version",
    "candidate_id",
    "protocol_sha256",
    "source_release_sha256",
    "test_evidence_sha256",
    "research_only",
    "betting_authorized",
    "release_sha256",
}


class PitcherJointOpportunityError(ValueError):
    """An input cannot support a truthful joint pitcher probability."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PitcherJointOpportunityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def sha256_value(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha(value: object, label: str) -> str:
    text = value if isinstance(value, str) else ""
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise PitcherJointOpportunityError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise PitcherJointOpportunityError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PitcherJointOpportunityError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PitcherJointOpportunityError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise PitcherJointOpportunityError(f"{label} must be canonical YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PitcherJointOpportunityError(f"{label} must be canonical YYYY-MM-DD") from exc
    if value != parsed.isoformat():
        raise PitcherJointOpportunityError(f"{label} must be canonical YYYY-MM-DD")
    if parsed.year == 2026 and parsed.month == 5:
        raise PitcherJointOpportunityError("May 2026 is sealed")
    return parsed


def _int(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PitcherJointOpportunityError(f"{label} must be an integer >= {minimum}")
    return value


def _float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PitcherJointOpportunityError(f"{label} must be finite")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise PitcherJointOpportunityError(f"{label} must be finite")
    return parsed


@dataclass(frozen=True)
class RawPitcherWorkloadReceipt:
    """Retained official game-log bytes plus their exact transport identity."""

    body: bytes
    request_url: str
    request_sent_at_utc: str
    received_at_utc: str
    http_status: int
    content_type: str
    request_sha256: str
    payload_sha256: str
    payload_size: int
    transport_receipt_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.body, bytes) or not self.body:
            raise PitcherJointOpportunityError("workload response bytes are missing")
        sent = _utc(self.request_sent_at_utc, "workload request_sent_at_utc")
        received = _utc(self.received_at_utc, "workload received_at_utc")
        if received < sent:
            raise PitcherJointOpportunityError("workload response predates its request")
        if not isinstance(self.request_url, str) or not self.request_url:
            raise PitcherJointOpportunityError("workload request URL is missing")
        if isinstance(self.http_status, bool) or self.http_status != 200:
            raise PitcherJointOpportunityError("workload response is not HTTP 200")
        if (
            not isinstance(self.content_type, str)
            or self.content_type.split(";", 1)[0].strip().lower() != "application/json"
        ):
            raise PitcherJointOpportunityError("workload response is not JSON")
        request_identity = sha256_value(
            {
                "method": "GET",
                "request_url": self.request_url,
                "request_sent_at_utc": self.request_sent_at_utc,
            }
        )
        payload_identity = hashlib.sha256(self.body).hexdigest()
        if self.request_sha256 != request_identity:
            raise PitcherJointOpportunityError("workload request hash differs")
        if self.payload_sha256 != payload_identity or self.payload_size != len(self.body):
            raise PitcherJointOpportunityError("workload payload identity differs")
        if self.transport_receipt_sha256 != sha256_value(self.transport_receipt()):
            raise PitcherJointOpportunityError("workload transport receipt hash differs")

    def transport_receipt(self) -> dict[str, Any]:
        return {
            "schema_version": RAW_WORKLOAD_RECEIPT_SCHEMA_VERSION,
            "method": "GET",
            "request_url": self.request_url,
            "request_sent_at_utc": self.request_sent_at_utc,
            "received_at_utc": self.received_at_utc,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "request_sha256": self.request_sha256,
            "payload_sha256": self.payload_sha256,
            "payload_size": self.payload_size,
        }

    @classmethod
    def capture(
        cls,
        *,
        body: bytes,
        request_url: str,
        request_sent_at_utc: str,
        received_at_utc: str,
        http_status: int = 200,
        content_type: str = "application/json",
    ) -> "RawPitcherWorkloadReceipt":
        request_sha = sha256_value(
            {
                "method": "GET",
                "request_url": request_url,
                "request_sent_at_utc": request_sent_at_utc,
            }
        )
        payload_sha = hashlib.sha256(body).hexdigest()
        transport = {
            "schema_version": RAW_WORKLOAD_RECEIPT_SCHEMA_VERSION,
            "method": "GET",
            "request_url": request_url,
            "request_sent_at_utc": request_sent_at_utc,
            "received_at_utc": received_at_utc,
            "http_status": http_status,
            "content_type": content_type,
            "request_sha256": request_sha,
            "payload_sha256": payload_sha,
            "payload_size": len(body),
        }
        return cls(
            body=body,
            request_url=request_url,
            request_sent_at_utc=request_sent_at_utc,
            received_at_utc=received_at_utc,
            http_status=http_status,
            content_type=content_type,
            request_sha256=request_sha,
            payload_sha256=payload_sha,
            payload_size=len(body),
            transport_receipt_sha256=sha256_value(transport),
        )


_WORKLOAD_SOURCE = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
_RAW_SOURCE_SCHEMA = {
    "source": "official_mlb_statsapi_live_feed_complete_pa_sequence",
    "identity_paths": ["gamePk", "gameData.datetime.officialDate", "gameData.game.type"],
    "boxscore_paths": [
        "liveData.boxscore.teams.{side}.team.id",
        "liveData.boxscore.teams.{side}.pitchers",
        "liveData.boxscore.teams.{side}.players.ID{pitcher}.stats.pitching.battersFaced",
        "liveData.boxscore.teams.{side}.players.ID{pitcher}.stats.pitching.numberOfPitches",
    ],
    "play_paths": [
        "liveData.plays.allPlays[].about.inning",
        "liveData.plays.allPlays[].about.halfInning",
        "liveData.plays.allPlays[].about.isComplete",
        "liveData.plays.allPlays[].matchup.pitcher.id",
        "liveData.plays.allPlays[].result.eventType",
        "liveData.plays.allPlays[].count.outs",
    ],
}
_EVENT_OUTCOME = {
    "strikeout": "K",
    "strikeout_double_play": "K",
    "walk": "BB_HBP",
    "intent_walk": "BB_HBP",
    "intentional_walk": "BB_HBP",
    "hit_by_pitch": "BB_HBP",
    "single": "1B",
    "double": "2B",
    "triple": "3B",
    "home_run": "HR",
    "field_out": "OTHER_OUT",
    "force_out": "OTHER_OUT",
    "grounded_into_double_play": "OTHER_OUT",
    "double_play": "OTHER_OUT",
    "triple_play": "OTHER_OUT",
    "fielders_choice_out": "OTHER_OUT",
    "sac_fly": "OTHER_OUT",
    "sac_bunt": "OTHER_OUT",
}


def workload_source_schema_sha256() -> str:
    return sha256_value(_RAW_SOURCE_SCHEMA)


def workload_parser_code_sha256() -> str:
    return file_sha256(__file__)


def expected_workload_request(*, game_pk: int) -> str:
    return _WORKLOAD_SOURCE.format(game_pk=_int(game_pk, "workload game_pk", minimum=1))


def _exact_mapping(
    value: object,
    keys: set[str],
    label: str,
    *,
    required: set[str] | None = None,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PitcherJointOpportunityError(f"{label} must be an object")
    observed = set(value)
    # MLB live-feed envelopes contain many presentation fields.  This is a
    # positive consumption schema: every consumed field is required and typed;
    # unconsumed source fields remain covered by the retained byte hash.
    if required is not None and not required.issubset(observed):
        raise PitcherJointOpportunityError(f"{label} is missing a required field")
    return value


def _validate_workload_url(actual: str, expected: str) -> None:
    observed = urlsplit(actual)
    required = urlsplit(expected)
    if (
        observed.scheme != "https"
        or observed.netloc != required.netloc
        or observed.path != required.path
        or observed.query
        or observed.fragment
    ):
        raise PitcherJointOpportunityError("workload source or game identity differs")


def replay_workload_receipts_for_offline_validation(
    receipts: Sequence[RawPitcherWorkloadReceipt],
    *,
    pitcher_id: int,
    pitching_team_id: int,
    target_date: date,
    target_horizon: datetime,
) -> list[dict[str, Any]]:
    """Offline/test replay into the positive workload schema.

    This function validates fixtures and retained bytes only. Its typed output
    is not accepted by ``apply_candidate`` and therefore cannot directly reach
    the probability-application boundary.
    """

    if (
        not isinstance(receipts, Sequence)
        or isinstance(receipts, (str, bytes))
        or not receipts
    ):
        raise PitcherJointOpportunityError("typed retained workload receipts are required")
    rows: list[dict[str, Any]] = []
    seen_games: set[int] = set()
    for receipt in receipts:
        if not isinstance(receipt, RawPitcherWorkloadReceipt):
            raise PitcherJointOpportunityError("typed retained workload receipts are required")
        if (
            _utc(receipt.request_sent_at_utc, "workload request_sent_at_utc") > target_horizon
            or _utc(receipt.received_at_utc, "workload received_at_utc") > target_horizon
        ):
            raise PitcherJointOpportunityError("workload receipt was not available by T-4")
        try:
            decoded = json.loads(receipt.body, object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PitcherJointOpportunityError("workload response is invalid JSON") from exc
        root = _exact_mapping(
            decoded,
            {"gamePk", "gameData", "liveData"},
            "workload live feed",
            required={"gamePk", "gameData", "liveData"},
        )
        game_pk = _int(root["gamePk"], "workload gamePk", minimum=1)
        _validate_workload_url(receipt.request_url, expected_workload_request(game_pk=game_pk))
        game_data = _exact_mapping(
            root["gameData"], {"datetime", "game"}, "workload gameData", required={"datetime", "game"}
        )
        datetime_row = _exact_mapping(
            game_data["datetime"], {"officialDate"}, "workload datetime", required={"officialDate"}
        )
        game_row = _exact_mapping(game_data["game"], {"type"}, "workload game", required={"type"})
        game_date = _date(datetime_row["officialDate"], "workload officialDate")
        if game_row["type"] != "R" or game_date >= target_date or game_pk in seen_games:
            raise PitcherJointOpportunityError("workload game is ineligible, non-prior, or duplicated")
        live = _exact_mapping(
            root["liveData"], {"boxscore", "plays"}, "workload liveData", required={"boxscore", "plays"}
        )
        boxscore = _exact_mapping(live["boxscore"], {"teams"}, "workload boxscore", required={"teams"})
        teams = _exact_mapping(boxscore["teams"], {"home", "away"}, "workload teams", required={"home", "away"})
        player_key = f"ID{pitcher_id}"
        matched_sides: list[Mapping[str, Any]] = []
        for side_name in ("home", "away"):
            side = _exact_mapping(
                teams[side_name], {"team", "pitchers", "players"}, f"workload {side_name}",
                required={"team", "pitchers", "players"},
            )
            players = side["players"]
            if isinstance(players, Mapping) and player_key in players:
                matched_sides.append(side)
        if len(matched_sides) != 1:
            raise PitcherJointOpportunityError("workload pitcher has no unique boxscore side")
        side = matched_sides[0]
        team = _exact_mapping(side["team"], {"id"}, "workload team", required={"id"})
        if _int(team["id"], "workload team id", minimum=1) != pitching_team_id:
            raise PitcherJointOpportunityError("workload pitching-team identity differs")
        pitchers = side["pitchers"]
        if not isinstance(pitchers, list) or not pitchers or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in pitchers
        ):
            raise PitcherJointOpportunityError("workload boxscore pitcher order is invalid")
        player = _exact_mapping(
            side["players"][player_key], {"person", "stats"}, "workload pitcher", required={"person", "stats"}
        )
        person = _exact_mapping(player["person"], {"id"}, "workload person", required={"id"})
        if _int(person["id"], "workload person id", minimum=1) != pitcher_id:
            raise PitcherJointOpportunityError("workload person identity differs")
        stats = _exact_mapping(player["stats"], {"pitching"}, "workload stats", required={"pitching"})
        pitching = _exact_mapping(
            stats["pitching"], {"battersFaced", "numberOfPitches"}, "workload pitching stats",
            required={"battersFaced", "numberOfPitches"},
        )
        bf = _int(pitching["battersFaced"], "battersFaced", minimum=1)
        pitches = _int(pitching["numberOfPitches"], "numberOfPitches", minimum=1)
        plays_root = _exact_mapping(live["plays"], {"allPlays"}, "workload plays", required={"allPlays"})
        all_plays = plays_root["allPlays"]
        if not isinstance(all_plays, list):
            raise PitcherJointOpportunityError("workload allPlays must be a list")
        counts = {outcome: 0 for outcome in OUTCOMES}
        unclassified = 0
        outs_recorded = 0
        last_outs_by_half: dict[tuple[int, str], int] = {}
        pitcher_pa = 0
        for play_value in all_plays:
            play = _exact_mapping(
                play_value, {"about", "matchup", "result", "count"}, "workload play",
                required={"about", "matchup", "result", "count"},
            )
            about = _exact_mapping(
                play["about"], {"inning", "halfInning", "isComplete"}, "workload play about",
                required={"inning", "halfInning", "isComplete"},
            )
            inning = _int(about["inning"], "workload inning", minimum=1)
            half = about["halfInning"]
            if half not in {"top", "bottom"} or about["isComplete"] is not True:
                raise PitcherJointOpportunityError("workload play is incomplete or has invalid half-inning")
            count = _exact_mapping(play["count"], {"outs"}, "workload play count", required={"outs"})
            after_outs = _int(count["outs"], "workload play outs")
            if after_outs > 3:
                raise PitcherJointOpportunityError("workload play outs exceed inning support")
            half_key = (inning, half)
            before_outs = last_outs_by_half.get(half_key, 0)
            delta = after_outs - before_outs
            if delta < 0 or delta > 3:
                raise PitcherJointOpportunityError("workload play outs transition is contradictory")
            last_outs_by_half[half_key] = after_outs
            matchup = _exact_mapping(play["matchup"], {"pitcher"}, "workload matchup", required={"pitcher"})
            play_pitcher = _exact_mapping(matchup["pitcher"], {"id"}, "workload play pitcher", required={"id"})
            if _int(play_pitcher["id"], "workload play pitcher id", minimum=1) != pitcher_id:
                continue
            pitcher_pa += 1
            result = _exact_mapping(play["result"], {"eventType"}, "workload result", required={"eventType"})
            outcome = _EVENT_OUTCOME.get(result["eventType"])
            if outcome is None:
                unclassified += 1
            else:
                if delta not in OUTS_TRANSITION_SUPPORT[outcome]:
                    raise PitcherJointOpportunityError(
                        f"workload {outcome} play has a structurally impossible outs transition"
                    )
                counts[outcome] += 1
            outs_recorded += delta
        if pitcher_pa != bf:
            raise PitcherJointOpportunityError("workload complete PA sequence does not equal official batters faced")
        unsigned = {
            "official_game_date": game_date.isoformat(),
            "mlb_game_pk": game_pk,
            "role": "start" if pitchers[0] == pitcher_id else "relief",
            "outs_recorded": outs_recorded,
            "batters_faced": bf,
            "pitch_count": pitches,
            "strikeouts": counts["K"],
            "walks_hbp": counts["BB_HBP"],
            "singles_allowed": counts["1B"],
            "doubles_allowed": counts["2B"],
            "triples_allowed": counts["3B"],
            "home_runs_allowed": counts["HR"],
            "other_out_events": counts["OTHER_OUT"],
            "unclassified_batters_faced": unclassified,
            "batters_faced_source_truth_valid": True,
            "pitch_count_source_truth_valid": True,
            "source_receipt_sha256": receipt.transport_receipt_sha256,
        }
        rows.append({**unsigned, "source_record_sha256": sha256_value(unsigned)})
        seen_games.add(game_pk)
    return sorted(rows, key=lambda row: (row["official_game_date"], row["mlb_game_pk"]))


def load_protocol(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        raw = source.read_bytes()
        protocol = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError) as exc:
        raise PitcherJointOpportunityError(f"protocol is unreadable: {exc}") from exc
    if hashlib.sha256(raw).hexdigest() != EXPECTED_PROTOCOL_SHA256:
        raise PitcherJointOpportunityError("protocol bytes differ from the externally fixed digest")
    expected = {
        "schema_version", "candidate_id", "status", "source_commit", "research_boundary",
        "candidate_application", "point_in_time_workload_schema", "learned_model",
        "numerical_contract", "development_and_selection", "market_gates", "shared_gate_rules",
    }
    if not isinstance(protocol, dict) or set(protocol) != expected:
        raise PitcherJointOpportunityError("protocol top-level schema changed")
    if protocol["schema_version"] != PROTOCOL_SCHEMA_VERSION or protocol["candidate_id"] != "pitcher_joint_opportunity_v1":
        raise PitcherJointOpportunityError("protocol identity changed")
    if protocol["status"] != "PREDECLARED_SCAFFOLD_ONLY_NO_FIT_NO_SELECTION":
        raise PitcherJointOpportunityError("protocol is not the pre-fit scaffold")
    boundaries = protocol["research_boundary"]
    if not isinstance(boundaries, Mapping) or not boundaries or any(value is not False for value in boundaries.values()):
        raise PitcherJointOpportunityError("a protected research boundary was weakened")
    if protocol["learned_model"].get("conditional_pa_outcomes") != list(OUTCOMES):
        raise PitcherJointOpportunityError("coherent PA outcome support changed")
    if protocol["learned_model"].get("outs_transition_support") != {
        outcome: list(support) for outcome, support in OUTS_TRANSITION_SUPPORT.items()
    }:
        raise PitcherJointOpportunityError("structural outs-transition support changed")
    if protocol["learned_model"].get("workload_inputs") != list(WORKLOAD_FEATURES):
        raise PitcherJointOpportunityError("workload feature contract changed")
    if set(protocol["market_gates"]) != set(MARKETS):
        raise PitcherJointOpportunityError("separate market gate set changed")
    candidate_application = protocol["candidate_application"]
    if (
        candidate_application.get("decision_horizon") != "T-4h"
        or candidate_application.get("receipt_schema") != "forward-pitcher-context-v2"
        or candidate_application.get("receipt_ledger_schema")
        != "forward-pitcher-context-ledger-v1"
        or candidate_application.get("plan_receipt_schema")
        != "aws-pitcher-receipt-plan-receipt-v1"
        or candidate_application.get("caller_supplied_plan_or_context_assertions_allowed")
        is not False
        or candidate_application.get("evidence_authority_contract_path")
        != EVIDENCE_AUTHORITY_RELATIVE_PATH
        or candidate_application.get("evidence_authority_contract_sha256")
        != EXPECTED_EVIDENCE_AUTHORITY_SHA256
        or candidate_application.get("evidence_authority_required_for_probability")
        is not True
        or candidate_application.get("external_runtime_release_required_for_probability")
        is not True
        or candidate_application.get("authorized_runtime_release_sha256") is not None
    ):
        raise PitcherJointOpportunityError("T-4 receipt or external release boundary changed")
    if (
        protocol["learned_model"].get("authorized_artifact_release_sha256") is not None
        or protocol["shared_gate_rules"].get("each_market_adjudicated_separately") is not True
        or protocol["shared_gate_rules"].get("one_market_may_rescue_another") is not False
        or protocol["shared_gate_rules"].get("capture_lower_bound_must_exceed") != 0.10
        or isinstance(protocol["shared_gate_rules"].get("capture_lower_bound_must_exceed"), bool)
    ):
        raise PitcherJointOpportunityError("artifact authorization or market gate boundary changed")
    if protocol["development_and_selection"] != {
        "development_data": "2023_regular_season_only",
        "development_method": "strictly_chronological_expanding_rolling_origin_folds",
        "development_feature_rule": "features use completed appearances strictly before each target game",
        "candidate_family": "one predeclared regularized shared joint model",
        "protocol_freeze_required_before_2024_access": True,
        "selection_data": "2024_regular_season_once_only",
        "selection_rule": "one simultaneous frozen candidate adjudication with separately reported market decisions",
        "post_selection_tuning_allowed": False,
        "spent_2025_hr_confirmation_reusable": False,
        "confirmation": "fresh untouched future confirmation or prospective forward evidence only",
    }:
        raise PitcherJointOpportunityError("development or confirmation boundary changed")
    return protocol


def validate_evidence_authority_contract_bytes(raw: bytes) -> dict[str, Any]:
    """Validate the independently frozen authority contract without resolving evidence.

    The current release intentionally contains no approved archive era.  A copied,
    rehashed, or jointly forged evidence tree therefore cannot become eligible by
    being internally consistent: changing any authority field requires a new,
    independently reviewed release.
    """

    if hashlib.sha256(raw).hexdigest() != EXPECTED_EVIDENCE_AUTHORITY_SHA256:
        raise PitcherJointOpportunityError(
            "evidence authority bytes differ from the externally fixed digest"
        )
    try:
        authority = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PitcherJointOpportunityError("evidence authority is invalid") from exc
    expected = {
        "schema_version",
        "candidate_id",
        "status",
        "authorized_evidence_authority_receipt_sha256",
        "binding",
        "required_binding_fields",
        "caller_selected_root_allowed",
        "direct_typed_receipt_consumption_allowed",
        "synthetic_replay_probability_consumption_allowed",
        "historical_or_missed_receipt_backfill_allowed",
        "research_only",
        "betting_authorized",
    }
    if not isinstance(authority, dict) or set(authority) != expected:
        raise PitcherJointOpportunityError("evidence authority schema changed")
    binding = authority["binding"]
    if (
        authority["schema_version"] != EVIDENCE_AUTHORITY_SCHEMA_VERSION
        or authority["candidate_id"] != "pitcher_joint_opportunity_v1"
        or authority["status"] != "UNBOUND_NO_APPROVED_ARCHIVE_ERA"
        or authority["authorized_evidence_authority_receipt_sha256"] is not None
        or not isinstance(binding, Mapping)
        or tuple(binding) != EVIDENCE_AUTHORITY_BINDING_FIELDS
        or any(binding[field] is not None for field in EVIDENCE_AUTHORITY_BINDING_FIELDS)
        or authority["required_binding_fields"] != list(EVIDENCE_AUTHORITY_BINDING_FIELDS)
        or authority["caller_selected_root_allowed"] is not False
        or authority["direct_typed_receipt_consumption_allowed"] is not False
        or authority["synthetic_replay_probability_consumption_allowed"] is not False
        or authority["historical_or_missed_receipt_backfill_allowed"] is not False
        or authority["research_only"] is not True
        or authority["betting_authorized"] is not False
    ):
        raise PitcherJointOpportunityError("evidence authority boundary changed")
    return authority


def load_evidence_authority_contract(
    *, protocol_path: str | Path, protocol: Mapping[str, Any]
) -> dict[str, Any]:
    """Load only the repository-fixed authority file named by the exact protocol."""

    source = Path(protocol_path).resolve()
    if source.name != "pitcher_joint_opportunity_v1_protocol.json" or source.parent.name != "config":
        raise PitcherJointOpportunityError("protocol path cannot establish a repository authority root")
    application = protocol.get("candidate_application")
    if not isinstance(application, Mapping):
        raise PitcherJointOpportunityError("candidate application contract is unavailable")
    if (
        application.get("evidence_authority_contract_path")
        != EVIDENCE_AUTHORITY_RELATIVE_PATH
        or application.get("evidence_authority_contract_sha256")
        != EXPECTED_EVIDENCE_AUTHORITY_SHA256
    ):
        raise PitcherJointOpportunityError("protocol evidence authority binding differs")
    authority_path = source.parents[1] / Path(EVIDENCE_AUTHORITY_RELATIVE_PATH)
    try:
        raw = authority_path.read_bytes()
    except OSError as exc:
        raise PitcherJointOpportunityError("fixed evidence authority is unavailable") from exc
    return validate_evidence_authority_contract_bytes(raw)


@dataclass(frozen=True)
class VerifiedT4PitcherReceipt:
    plan: ShadowCapturePlan
    target: CaptureTarget
    context: ForwardPitcherContextV2
    plan_receipt_sha256: str
    ledger_record_sha256: str


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PitcherJointOpportunityError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise PitcherJointOpportunityError(f"{label} must be an object")
    return value


def _inside(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise PitcherJointOpportunityError(f"{label} path is missing")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PitcherJointOpportunityError(f"{label} path escapes its evidence root") from exc
    return candidate


def _replay_t4_pitcher_receipt_from_root(
    *,
    evidence_root: str | Path,
    official_game_date: str,
    mlb_game_pk: int,
) -> VerifiedT4PitcherReceipt:
    """Replay one evidence tree after an external authority has fixed its root.

    Production probability code must never call this function with a
    caller-selected root.  ``apply_candidate`` has no evidence-root argument.
    """

    root = Path(evidence_root).resolve()
    date_value = _date(official_game_date, "official_game_date").isoformat()
    game_pk = _int(mlb_game_pk, "mlb_game_pk", minimum=1)
    plan_path = root / "plans" / f"{date_value}.plan.json"
    try:
        plan = ShadowCapturePlan.from_mapping(_read_object(plan_path, "pitcher receipt plan"))
    except (ShadowCapturePlanError, PitcherJointOpportunityError) as exc:
        raise PitcherJointOpportunityError("immutable pitcher receipt plan cannot be verified") from exc
    if plan.official_game_date != date_value or plan.entry_hours != 4:
        raise PitcherJointOpportunityError("pitcher receipt plan date or horizon differs")
    targets = [target for target in plan.targets if target.mlb_game_pk == game_pk]
    if len(targets) != 1:
        raise PitcherJointOpportunityError("pitcher receipt plan has no unique requested game")
    target = targets[0]

    plan_receipt_path = (
        root
        / "plan-receipts"
        / "plans"
        / f"{date_value}.{plan.plan_sha256}.json"
    )
    plan_receipt = _read_object(plan_receipt_path, "pitcher plan source receipt")
    expected_receipt_keys = {
        "schema_version",
        "official_game_date",
        "plan_sha256",
        "runtime_sha256",
        "source_name",
        "source_payload_sha256",
        "received_at_utc",
        "targets",
        "research_only",
        "betting_authorized",
        "model_or_market_accessed",
        "receipt_sha256",
    }
    if set(plan_receipt) != expected_receipt_keys:
        raise PitcherJointOpportunityError("pitcher plan source receipt schema changed")
    unsigned_receipt = dict(plan_receipt)
    receipt_sha = unsigned_receipt.pop("receipt_sha256")
    runtime_sha = _sha(plan_receipt["runtime_sha256"], "plan runtime_sha256")
    source_sha = _sha(plan_receipt["source_payload_sha256"], "plan source_payload_sha256")
    if (
        plan_receipt["schema_version"] != "aws-pitcher-receipt-plan-receipt-v1"
        or plan_receipt["official_game_date"] != date_value
        or plan_receipt["plan_sha256"] != plan.plan_sha256
        or plan_receipt["source_name"] != "mlb_statsapi_schedule"
        or plan_receipt["targets"] != len(plan.targets)
        or isinstance(plan_receipt["targets"], bool)
        or plan_receipt["research_only"] is not True
        or plan_receipt["betting_authorized"] is not False
        or plan_receipt["model_or_market_accessed"] is not False
        or receipt_sha != sha256_value(unsigned_receipt)
        or runtime_sha != plan.policy_sha256
    ):
        raise PitcherJointOpportunityError("pitcher plan source receipt binding differs")
    plan_received = _utc(plan_receipt["received_at_utc"], "plan received_at_utc")
    if any(plan_received >= _utc(item.entry_target_at_utc, "target T-4") for item in plan.targets):
        raise PitcherJointOpportunityError("pitcher plan source was not retained before every T-4 target")

    plan_raw_path = root / "plan-receipts" / "raw" / f"{date_value}.{source_sha}.json"
    try:
        plan_raw = plan_raw_path.read_bytes()
        plan_source = json.loads(plan_raw, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PitcherJointOpportunityError("retained pitcher plan source bytes are invalid") from exc
    if hashlib.sha256(plan_raw).hexdigest() != source_sha:
        raise PitcherJointOpportunityError("retained pitcher plan source hash differs")
    try:
        rebuilt_plan = plan_from_schedule(
            official_game_date=date_value,
            entry_hours=4,
            policy_sha256=runtime_sha,
            schedule_snapshot=canonical_schedule_records(
                games_from_raw_schedule_response(plan_source, allow_empty_date=True)
            ),
        )
    except (ForwardPitcherContextError, ShadowCapturePlanError) as exc:
        raise PitcherJointOpportunityError("retained schedule cannot reproduce the pitcher plan") from exc
    if rebuilt_plan.to_dict() != plan.to_dict():
        raise PitcherJointOpportunityError("retained schedule cannot reproduce the pitcher plan")

    ledger_root = root / "ledgers" / date_value / plan.plan_sha256
    manifest_path = ledger_root / "manifest.json"
    index_path = ledger_root / "terminal_index.json"
    if not manifest_path.is_file() or not index_path.is_file():
        raise PitcherJointOpportunityError("pitcher T-4 receipt ledger is unavailable")
    ledger_manifest = _read_object(manifest_path, "pitcher ledger manifest")
    if ledger_manifest.get("runtime_sha256") != runtime_sha:
        raise PitcherJointOpportunityError("pitcher ledger runtime differs from the plan source")
    try:
        ledger = ForwardPitcherContextLedger(ledger_root, plan, runtime_sha)
        ledger.verify(assessed_at_utc=target.entry_target_at_utc)
    except (ForwardPitcherContextLedgerError, ForwardPitcherContextError) as exc:
        raise PitcherJointOpportunityError("pitcher T-4 receipt ledger replay failed") from exc
    index = _read_object(index_path, "pitcher ledger terminal index")
    target_rows = index.get("targets")
    row = target_rows.get(target.target_id) if isinstance(target_rows, Mapping) else None
    if not isinstance(row, Mapping) or row.get("terminal_state") != "captured":
        raise PitcherJointOpportunityError("pitcher target has no captured T-4 ledger state")
    record_path = _inside(ledger_root, row.get("record_path"), "pitcher ledger record")
    record = _read_object(record_path, "pitcher ledger record")
    if (
        record.get("chain_sha256") != row.get("chain_sha256")
        or record.get("target_id") != target.target_id
        or record.get("plan_sha256") != plan.plan_sha256
        or record.get("terminal_state") != "captured"
    ):
        raise PitcherJointOpportunityError("pitcher ledger target record identity differs")
    context_path = _inside(ledger_root, record.get("context_path"), "pitcher context")
    raw_path = _inside(ledger_root, record.get("raw_payload_path"), "pitcher context raw")
    try:
        legacy_context = load_context(context_path, plan, target)
        raw = raw_path.read_bytes()
        raw_source = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (ForwardPitcherContextError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PitcherJointOpportunityError("pitcher context/raw ledger replay failed") from exc
    if (
        legacy_context.context_sha256 != record.get("context_sha256")
        or hashlib.sha256(raw).hexdigest() != record.get("raw_payload_sha256")
        or legacy_context.source_payload_sha256 != record.get("raw_payload_sha256")
    ):
        raise PitcherJointOpportunityError("pitcher context/raw ledger hashes differ")
    try:
        context = context_v2_from_schedule(
            target=target,
            plan=plan,
            captured_at_utc=legacy_context.captured_at_utc,
            source_payload_sha256=hashlib.sha256(raw).hexdigest(),
            schedule_games=games_from_raw_schedule_response(raw_source),
        )
    except (ForwardPitcherContextError, ForwardPitcherContextV2Error) as exc:
        raise PitcherJointOpportunityError(
            "existing ledger raw bytes cannot prove the required v2 pitcher context"
        ) from exc
    return VerifiedT4PitcherReceipt(
        plan=plan,
        target=target,
        context=context,
        plan_receipt_sha256=_sha(receipt_sha, "plan receipt_sha256"),
        ledger_record_sha256=_sha(str(record.get("chain_sha256")), "ledger chain_sha256"),
    )


def replay_synthetic_t4_pitcher_receipt_for_tests(
    *, evidence_root: str | Path, official_game_date: str, mlb_game_pk: int
) -> VerifiedT4PitcherReceipt:
    """Test-only raw replay boundary; its return type is not accepted by application."""

    return _replay_t4_pitcher_receipt_from_root(
        evidence_root=evidence_root,
        official_game_date=official_game_date,
        mlb_game_pk=mlb_game_pk,
    )


def validate_workload_history(
    record: Mapping[str, Any],
    *,
    target: CaptureTarget,
    pitcher_id: int,
    pitching_team_id: int,
    protocol_sha256: str,
    raw_receipts: Sequence[RawPitcherWorkloadReceipt],
) -> dict[str, Any]:
    """Validate a positive, source-lineaged, strictly-prior workload schema."""

    if not isinstance(record, Mapping) or set(record) != _WORKLOAD_KEYS:
        raise PitcherJointOpportunityError("workload history schema changed")
    if record["schema_version"] != WORKLOAD_SCHEMA_VERSION or record["source_kind"] != "official_mlb_pitching_game_log_point_in_time":
        raise PitcherJointOpportunityError("workload history source is not approved")
    target_date = _date(target.official_game_date, "target official_game_date")
    if (
        record["target_id"] != target.target_id
        or record["official_game_date"] != target.official_game_date
        or record["mlb_game_pk"] != target.mlb_game_pk
        or _int(record["pitcher_id"], "pitcher_id", minimum=1) != pitcher_id
        or _int(record["pitching_team_id"], "pitching_team_id", minimum=1) != pitching_team_id
    ):
        raise PitcherJointOpportunityError("workload target/team/pitcher identity differs")
    horizon = _utc(record["target_horizon_utc"], "target_horizon_utc")
    if horizon != _utc(target.entry_target_at_utc, "target entry horizon"):
        raise PitcherJointOpportunityError("workload T-4 horizon differs")
    if _utc(record["assembled_at_utc"], "assembled_at_utc") > horizon:
        raise PitcherJointOpportunityError("workload was assembled after T-4")

    lineage = record["lineage"]
    if not isinstance(lineage, Mapping) or set(lineage) != _LINEAGE_KEYS:
        raise PitcherJointOpportunityError("workload lineage schema changed")
    for key in _LINEAGE_KEYS:
        _sha(lineage[key], f"lineage.{key}")
    if lineage["protocol_sha256"] != _sha(protocol_sha256, "protocol_sha256"):
        raise PitcherJointOpportunityError("workload protocol lineage differs")

    receipt_manifest = [receipt.transport_receipt_sha256 for receipt in raw_receipts]
    if (
        lineage["raw_receipt_manifest_sha256"] != sha256_value(receipt_manifest)
        or lineage["source_schema_sha256"] != workload_source_schema_sha256()
        or lineage["parser_code_sha256"] != workload_parser_code_sha256()
        or lineage["feature_code_sha256"] != workload_parser_code_sha256()
    ):
        raise PitcherJointOpportunityError("workload raw/schema/parser/feature lineage differs")

    rows = record["prior_appearances"]
    if not isinstance(rows, list) or not rows:
        raise PitcherJointOpportunityError("workload has no prior appearances; no default is allowed")
    if lineage["history_payload_sha256"] != sha256_value(rows):
        raise PitcherJointOpportunityError("workload history payload hash differs")
    replayed_rows = replay_workload_receipts_for_offline_validation(
        raw_receipts,
        pitcher_id=pitcher_id,
        pitching_team_id=pitching_team_id,
        target_date=target_date,
        target_horizon=horizon,
    )
    if replayed_rows != rows:
        raise PitcherJointOpportunityError("workload rows differ from retained raw receipt replay")
    seen_game_pks: set[int] = set()
    latest_date: date | None = None
    previous_key: tuple[date, int] | None = None
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != _APPEARANCE_KEYS:
            raise PitcherJointOpportunityError(f"prior appearance {index} schema changed")
        game_date = _date(row["official_game_date"], f"prior appearance {index} date")
        game_pk = _int(row["mlb_game_pk"], f"prior appearance {index} game_pk", minimum=1)
        if game_date >= target_date:
            raise PitcherJointOpportunityError("workload includes same-day or future appearance")
        if game_pk in seen_game_pks:
            raise PitcherJointOpportunityError("workload contains duplicate game identity")
        seen_game_pks.add(game_pk)
        key = (game_date, game_pk)
        if previous_key is not None and key <= previous_key:
            raise PitcherJointOpportunityError("workload appearances are not in strict chronological identity order")
        previous_key = key
        latest_date = game_date
        if row["role"] not in {"start", "relief"}:
            raise PitcherJointOpportunityError("prior appearance role must be start or relief")
        outs = _int(row["outs_recorded"], "outs_recorded")
        bf = _int(row["batters_faced"], "batters_faced", minimum=1)
        pitches = _int(row["pitch_count"], "pitch_count", minimum=1)
        if outs > 27 or outs > 3 * bf:
            raise PitcherJointOpportunityError("prior appearance exact outs contradict batters faced")
        if row["batters_faced_source_truth_valid"] is not True or row["pitch_count_source_truth_valid"] is not True:
            raise PitcherJointOpportunityError("BF or pitch-count source truth is not proven")
        _sha(row["source_receipt_sha256"], "source_receipt_sha256")
        _sha(row["source_record_sha256"], "source_record_sha256")
        if row["source_record_sha256"] != sha256_value(
            {key: row[key] for key in _APPEARANCE_KEYS - {"source_record_sha256"}}
        ):
            raise PitcherJointOpportunityError("source record identity differs from replayed row")
        counts = [
            _int(row[name], name)
            for name in (
                "strikeouts", "walks_hbp", "singles_allowed", "doubles_allowed",
                "triples_allowed", "home_runs_allowed", "other_out_events",
                "unclassified_batters_faced",
            )
        ]
        if sum(counts) != bf:
            raise PitcherJointOpportunityError("seven PA outcomes plus unclassified BF do not equal batters faced")
        if row["unclassified_batters_faced"] != 0:
            raise PitcherJointOpportunityError("unclassified batters faced block the seven-outcome model")
    assert latest_date is not None
    if record["max_source_game_date"] != latest_date.isoformat():
        raise PitcherJointOpportunityError("max_source_game_date differs from prior appearances")

    unsigned = {key: record[key] for key in _WORKLOAD_KEYS - {"workload_sha256"}}
    if record["workload_sha256"] != sha256_value(unsigned):
        raise PitcherJointOpportunityError("workload content hash differs")
    return dict(record)


def _mean(values: Sequence[int]) -> float:
    return sum(values) / len(values)


def _population_sd(values: Sequence[int]) -> float:
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def build_workload_features(record: Mapping[str, Any], *, rolling_window_appearances: int) -> dict[str, float]:
    """Mechanically summarize validated rows; the fitted artifact chooses N."""

    rows = record["prior_appearances"]
    window = _int(rolling_window_appearances, "rolling_window_appearances", minimum=1)
    if window > len(rows):
        raise PitcherJointOpportunityError("fitted rolling window exceeds available history; no shorter fallback is allowed")
    recent = rows[-window:]
    target_date = _date(record["official_game_date"], "official_game_date")
    last_date = _date(rows[-1]["official_game_date"], "last appearance date")
    rest_days = (target_date - last_date).days
    if rest_days <= 0:
        raise PitcherJointOpportunityError("rest days must derive from a strictly prior appearance")
    total_bf = sum(row["batters_faced"] for row in recent)
    if total_bf <= 0:
        raise PitcherJointOpportunityError("rolling BF denominator is unavailable")
    starts = sum(row["role"] == "start" for row in rows)
    recent_starts = sum(row["role"] == "start" for row in recent)
    last = rows[-1]
    features: dict[str, float] = {
        "rest_days": float(rest_days),
        "prior_appearance_count": float(len(rows)),
        "prior_start_count": float(starts),
        "prior_relief_count": float(len(rows) - starts),
        "last_outs": float(last["outs_recorded"]),
        "last_batters_faced": float(last["batters_faced"]),
        "last_pitch_count": float(last["pitch_count"]),
        "last_role_start": float(last["role"] == "start"),
        "rolling_mean_outs": _mean([row["outs_recorded"] for row in recent]),
        "rolling_sd_outs": _population_sd([row["outs_recorded"] for row in recent]),
        "rolling_mean_batters_faced": _mean([row["batters_faced"] for row in recent]),
        "rolling_sd_batters_faced": _population_sd([row["batters_faced"] for row in recent]),
        "rolling_mean_pitch_count": _mean([row["pitch_count"] for row in recent]),
        "rolling_sd_pitch_count": _population_sd([row["pitch_count"] for row in recent]),
        "rolling_start_share": recent_starts / window,
        "rolling_k_per_bf": sum(row["strikeouts"] for row in recent) / total_bf,
        "rolling_bb_hbp_per_bf": sum(row["walks_hbp"] for row in recent) / total_bf,
        "rolling_1b_per_bf": sum(row["singles_allowed"] for row in recent) / total_bf,
        "rolling_2b_per_bf": sum(row["doubles_allowed"] for row in recent) / total_bf,
        "rolling_3b_per_bf": sum(row["triples_allowed"] for row in recent) / total_bf,
        "rolling_hr_per_bf": sum(row["home_runs_allowed"] for row in recent) / total_bf,
        "rolling_other_out_per_bf": sum(row["other_out_events"] for row in recent) / total_bf,
    }
    if set(features) != set(_BASE_FEATURES) or not all(math.isfinite(value) for value in features.values()):
        raise PitcherJointOpportunityError("derived workload feature schema or values differ")
    return features


def _validate_linear_spec(spec: object, label: str) -> dict[str, Any]:
    if not isinstance(spec, Mapping) or set(spec) != {"intercept", "coefficients", "centers", "scales"}:
        raise PitcherJointOpportunityError(f"{label} linear specification changed")
    for field in ("coefficients", "centers", "scales"):
        if not isinstance(spec[field], Mapping) or set(spec[field]) != set(WORKLOAD_FEATURES):
            raise PitcherJointOpportunityError(f"{label}.{field} feature schema changed")
    _float(spec["intercept"], f"{label}.intercept")
    for name in WORKLOAD_FEATURES:
        _float(spec["coefficients"][name], f"{label}.coefficients.{name}")
        _float(spec["centers"][name], f"{label}.centers.{name}")
        if _float(spec["scales"][name], f"{label}.scales.{name}") <= 0:
            raise PitcherJointOpportunityError(f"{label}.scales.{name} must be positive")
    return dict(spec)


def validate_model_artifact(artifact: Mapping[str, Any], *, protocol_sha256: str, allow_synthetic: bool = False) -> dict[str, Any]:
    if not isinstance(artifact, Mapping) or set(artifact) != _MODEL_KEYS:
        raise PitcherJointOpportunityError("model artifact schema changed")
    if artifact["schema_version"] != MODEL_SCHEMA_VERSION or artifact["candidate_id"] != "pitcher_joint_opportunity_v1":
        raise PitcherJointOpportunityError("model artifact identity changed")
    allowed_states = {"FUTURE_CONFIRMATION_LOCKED_RESEARCH_ONLY"}
    if allow_synthetic:
        allowed_states.add("SYNTHETIC_TEST_ONLY")
    if artifact["qualification_state"] not in allowed_states:
        raise PitcherJointOpportunityError("model artifact is not qualified for this consumption mode")
    if artifact["protocol_sha256"] != _sha(protocol_sha256, "protocol_sha256"):
        raise PitcherJointOpportunityError("model artifact protocol hash differs")
    start = _date(artifact["training_start"], "training_start")
    end = _date(artifact["training_end"], "training_end")
    if start.year != 2023 or end.year != 2023 or end < start:
        raise PitcherJointOpportunityError("model artifact is not fit only on 2023")
    _int(artifact["rolling_window_appearances"], "rolling_window_appearances", minimum=1)
    if artifact["feature_names"] != list(WORKLOAD_FEATURES):
        raise PitcherJointOpportunityError("model artifact feature order changed")
    _validate_linear_spec(artifact["hazard_model"], "hazard_model")
    outcome_model = artifact["outcome_model"]
    if not isinstance(outcome_model, Mapping) or set(outcome_model) != set(OUTCOMES):
        raise PitcherJointOpportunityError("outcome model does not contain exactly one coherent multinomial")
    for outcome in OUTCOMES:
        _validate_linear_spec(outcome_model[outcome], f"outcome_model.{outcome}")
    transitions = artifact["outs_transition_model"]
    if not isinstance(transitions, Mapping) or set(transitions) != set(OUTCOMES):
        raise PitcherJointOpportunityError("outs transition model outcome schema changed")
    for outcome in OUTCOMES:
        if not isinstance(transitions[outcome], Mapping) or set(transitions[outcome]) != {"0", "1", "2", "3"}:
            raise PitcherJointOpportunityError("outs transition delta schema changed")
        for delta in range(4):
            _validate_linear_spec(transitions[outcome][str(delta)], f"outs_transition_model.{outcome}.{delta}")
    for field in (
        "training_data_sha256",
        "fit_code_sha256",
        "fit_tests_sha256",
        "qualification_report_sha256",
    ):
        _sha(artifact[field], field)
    _int(artifact["random_seed"], "random_seed")
    unsigned = {key: artifact[key] for key in _MODEL_KEYS - {"artifact_sha256"}}
    if artifact["artifact_sha256"] != sha256_value(unsigned):
        raise PitcherJointOpportunityError("model artifact content hash differs")
    return dict(artifact)


def validate_model_authorization(
    receipt: Mapping[str, Any],
    *,
    artifact: Mapping[str, Any],
    protocol_sha256: str,
    externally_fixed_receipt_sha256: str | None,
) -> dict[str, Any]:
    """Require an independently fixed artifact authorization, not self-qualification."""

    if externally_fixed_receipt_sha256 is None:
        raise PitcherJointOpportunityError("no model artifact release is externally authorized")
    expected_receipt_sha = _sha(
        externally_fixed_receipt_sha256, "authorized artifact release sha256"
    )
    if not isinstance(receipt, Mapping) or set(receipt) != _MODEL_AUTHORIZATION_KEYS:
        raise PitcherJointOpportunityError("model artifact authorization schema changed")
    unsigned = dict(receipt)
    observed_receipt_sha = unsigned.pop("authorization_sha256", None)
    if observed_receipt_sha != sha256_value(unsigned) or observed_receipt_sha != expected_receipt_sha:
        raise PitcherJointOpportunityError("model artifact authorization is not externally fixed")
    required = {
        "schema_version": MODEL_AUTHORIZATION_SCHEMA_VERSION,
        "candidate_id": "pitcher_joint_opportunity_v1",
        "qualification_state": "FUTURE_CONFIRMATION_LOCKED_RESEARCH_ONLY",
        "protocol_sha256": _sha(protocol_sha256, "protocol_sha256"),
        "artifact_sha256": artifact["artifact_sha256"],
        "training_data_sha256": artifact["training_data_sha256"],
        "fit_code_sha256": artifact["fit_code_sha256"],
        "fit_tests_sha256": artifact["fit_tests_sha256"],
        "qualification_report_sha256": artifact["qualification_report_sha256"],
        "research_only": True,
        "betting_authorized": False,
    }
    if unsigned != required:
        raise PitcherJointOpportunityError(
            "model artifact authorization does not bind training, code, tests, protocol, and qualification"
        )
    return dict(receipt)


def validate_runtime_release_authorization(
    receipt: Mapping[str, Any],
    *,
    protocol_sha256: str,
    externally_fixed_receipt_sha256: str | None,
) -> dict[str, Any]:
    if externally_fixed_receipt_sha256 is None:
        raise PitcherJointOpportunityError("no runtime release is externally authorized")
    expected_receipt_sha = _sha(
        externally_fixed_receipt_sha256, "authorized runtime release sha256"
    )
    if not isinstance(receipt, Mapping) or set(receipt) != _RUNTIME_RELEASE_KEYS:
        raise PitcherJointOpportunityError("runtime release authorization schema changed")
    unsigned = dict(receipt)
    observed = unsigned.pop("release_sha256", None)
    if observed != sha256_value(unsigned) or observed != expected_receipt_sha:
        raise PitcherJointOpportunityError("runtime release is not externally fixed")
    if (
        receipt["schema_version"] != RUNTIME_RELEASE_SCHEMA_VERSION
        or receipt["candidate_id"] != "pitcher_joint_opportunity_v1"
        or receipt["protocol_sha256"] != protocol_sha256
        or receipt["research_only"] is not True
        or receipt["betting_authorized"] is not False
    ):
        raise PitcherJointOpportunityError("runtime release identity or scope differs")
    _sha(receipt["source_release_sha256"], "source_release_sha256")
    _sha(receipt["test_evidence_sha256"], "test_evidence_sha256")
    return dict(receipt)


def _linear_score(spec: Mapping[str, Any], features: Mapping[str, float]) -> float:
    if set(features) != set(WORKLOAD_FEATURES):
        raise PitcherJointOpportunityError("runtime feature vector differs from the locked model schema")
    score = _float(spec["intercept"], "intercept")
    for name in WORKLOAD_FEATURES:
        value = _float(features[name], f"feature.{name}")
        center = _float(spec["centers"][name], f"center.{name}")
        scale = _float(spec["scales"][name], f"scale.{name}")
        score += _float(spec["coefficients"][name], f"coefficient.{name}") * ((value - center) / scale)
    if not math.isfinite(score):
        raise PitcherJointOpportunityError("linear score is non-finite")
    return score


def _logistic(score: float) -> float:
    if score >= 0:
        exp_neg = math.exp(-score)
        return 1.0 / (1.0 + exp_neg)
    exp_pos = math.exp(score)
    return exp_pos / (1.0 + exp_pos)


def _softmax(scores: Mapping[str, float]) -> dict[str, float]:
    if not scores:
        raise PitcherJointOpportunityError("softmax support cannot be empty")
    maximum = max(scores.values())
    weights = {key: math.exp(value - maximum) for key, value in scores.items()}
    total = sum(weights.values())
    if not math.isfinite(total) or total <= 0:
        raise PitcherJointOpportunityError("softmax denominator is invalid")
    return {key: value / total for key, value in weights.items()}


def state_kernel(
    artifact: Mapping[str, Any],
    base_features: Mapping[str, float],
    *,
    batters_faced_before: int,
    outs_before: int,
) -> tuple[float, dict[str, float], dict[str, dict[int, float]]]:
    """Return learned hazard, one coherent PA multinomial, and outs transitions."""

    bf = _int(batters_faced_before, "batters_faced_before")
    outs = _int(outs_before, "outs_before")
    if outs >= 27:
        raise PitcherJointOpportunityError("game-complete state has no learned next-PA kernel")
    features = {**base_features, "batters_faced_before": float(bf), "outs_before": float(outs)}
    hazard = _logistic(_linear_score(artifact["hazard_model"], features))
    outcome_probabilities = _softmax({outcome: _linear_score(artifact["outcome_model"][outcome], features) for outcome in OUTCOMES})
    remaining_in_inning = 3 - (outs % 3)
    transitions: dict[str, dict[int, float]] = {}
    for outcome in OUTCOMES:
        allowed = [
            delta
            for delta in OUTS_TRANSITION_SUPPORT[outcome]
            if delta <= remaining_in_inning
        ]
        if not allowed:
            raise PitcherJointOpportunityError(
                f"no structurally valid outs transition remains for {outcome}"
            )
        transitions[outcome] = {
            int(delta): probability
            for delta, probability in _softmax({
                str(delta): _linear_score(artifact["outs_transition_model"][outcome][str(delta)], features)
                for delta in allowed
            }).items()
        }
    return hazard, outcome_probabilities, transitions


def _market_pmf(
    *,
    artifact: Mapping[str, Any],
    base_features: Mapping[str, float],
    market: str,
    maximum_iterations: int,
    active_mass_tolerance: float,
    probability_sum_tolerance: float,
) -> tuple[dict[int, float], float]:
    active: dict[tuple[int, int, int], float] = {(0, 0, 0): 1.0}
    terminal: dict[int, float] = defaultdict(float)
    increments = COUNT_INCREMENT[market]
    kernel_cache: dict[
        tuple[int, int],
        tuple[float, dict[str, float], dict[str, dict[int, float]]],
    ] = {}
    for _ in range(maximum_iterations):
        if not active:
            break
        next_active: dict[tuple[int, int, int], float] = defaultdict(float)
        for (bf, outs, count), mass in active.items():
            if outs == 27:
                terminal[count] += mass
                continue
            state = (bf, outs)
            if state not in kernel_cache:
                kernel_cache[state] = state_kernel(
                    artifact,
                    base_features,
                    batters_faced_before=bf,
                    outs_before=outs,
                )
            hazard, outcome_probabilities, transitions = kernel_cache[state]
            terminal[count] += mass * hazard
            continuing = mass * (1.0 - hazard)
            for outcome, outcome_probability in outcome_probabilities.items():
                updated_count = count + increments.get(outcome, 0)
                for delta, delta_probability in transitions[outcome].items():
                    next_active[(bf + 1, outs + delta, updated_count)] += continuing * outcome_probability * delta_probability
        active = dict(next_active)
    unresolved = sum(active.values())
    if unresolved > active_mass_tolerance:
        raise PitcherJointOpportunityError(
            f"{market} unresolved active probability mass {unresolved:.17g} exceeds numerical tolerance"
        )
    total = sum(terminal.values())
    if abs((total + unresolved) - 1.0) > probability_sum_tolerance:
        raise PitcherJointOpportunityError(f"{market} probability mass is not conserved")
    return dict(sorted(terminal.items())), unresolved


def _joint_opportunity_pmf(
    *,
    artifact: Mapping[str, Any],
    base_features: Mapping[str, float],
    maximum_iterations: int,
    active_mass_tolerance: float,
    probability_sum_tolerance: float,
) -> tuple[dict[tuple[int, int], float], float]:
    """Return the removal distribution over exact (BF, outs) states."""

    active: dict[tuple[int, int], float] = {(0, 0): 1.0}
    terminal: dict[tuple[int, int], float] = defaultdict(float)
    kernel_cache: dict[
        tuple[int, int],
        tuple[float, dict[str, float], dict[str, dict[int, float]]],
    ] = {}
    for _ in range(maximum_iterations):
        if not active:
            break
        next_active: dict[tuple[int, int], float] = defaultdict(float)
        for (bf, outs), mass in active.items():
            if outs == 27:
                terminal[(bf, outs)] += mass
                continue
            state = (bf, outs)
            if state not in kernel_cache:
                kernel_cache[state] = state_kernel(
                    artifact,
                    base_features,
                    batters_faced_before=bf,
                    outs_before=outs,
                )
            hazard, outcome_probabilities, transitions = kernel_cache[state]
            terminal[state] += mass * hazard
            continuing = mass * (1.0 - hazard)
            for outcome, outcome_probability in outcome_probabilities.items():
                for delta, delta_probability in transitions[outcome].items():
                    next_active[(bf + 1, outs + delta)] += (
                        continuing * outcome_probability * delta_probability
                    )
        active = dict(next_active)
    unresolved = sum(active.values())
    if unresolved > active_mass_tolerance:
        raise PitcherJointOpportunityError(
            f"joint opportunity unresolved active probability mass {unresolved:.17g} exceeds numerical tolerance"
        )
    total = sum(terminal.values())
    if abs((total + unresolved) - 1.0) > probability_sum_tolerance:
        raise PitcherJointOpportunityError("joint opportunity probability mass is not conserved")
    return dict(sorted(terminal.items())), unresolved


def compute_market_pmfs(
    *,
    artifact: Mapping[str, Any],
    workload_record: Mapping[str, Any],
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive exact opportunity plus separate markets from one shared kernel."""

    numerical = protocol["numerical_contract"]
    base_features = build_workload_features(
        workload_record,
        rolling_window_appearances=artifact["rolling_window_appearances"],
    )
    maximum_iterations = _int(
        numerical["maximum_batters_faced_iterations"],
        "maximum iterations",
        minimum=1,
    )
    active_mass_tolerance = _float(
        numerical["active_mass_tolerance"], "active_mass_tolerance"
    )
    probability_sum_tolerance = _float(
        numerical["probability_sum_tolerance"], "probability_sum_tolerance"
    )
    opportunity_pmf, opportunity_tail = _joint_opportunity_pmf(
        artifact=artifact,
        base_features=base_features,
        maximum_iterations=maximum_iterations,
        active_mass_tolerance=active_mass_tolerance,
        probability_sum_tolerance=probability_sum_tolerance,
    )
    markets: dict[str, Any] = {}
    for market in MARKETS:
        pmf, unresolved = _market_pmf(
            artifact=artifact,
            base_features=base_features,
            market=market,
            maximum_iterations=maximum_iterations,
            active_mass_tolerance=active_mass_tolerance,
            probability_sum_tolerance=probability_sum_tolerance,
        )
        markets[market] = {
            "pmf": {str(key): value for key, value in pmf.items()},
            "mean": sum(key * value for key, value in pmf.items()),
            "unresolved_numerical_tail": unresolved,
        }
    expected_bf = sum(bf * mass for (bf, _), mass in opportunity_pmf.items())
    expected_outs = sum(outs * mass for (_, outs), mass in opportunity_pmf.items())
    return {
        "joint_opportunity": {
            "pmf": {f"{bf}:{outs}": mass for (bf, outs), mass in opportunity_pmf.items()},
            "expected_batters_faced": expected_bf,
            "expected_outs_recorded": expected_outs,
            "expected_innings_pitched": expected_outs / 3.0,
            "unresolved_numerical_tail": opportunity_tail,
        },
        "markets": markets,
    }


def terminal_abstention(*, target: CaptureTarget, pitcher_id: int, pitching_team_id: int, reason_code: str) -> dict[str, Any]:
    if not isinstance(reason_code, str) or not reason_code:
        raise PitcherJointOpportunityError("abstention reason is required")
    unsigned = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "terminal_state": "TERMINAL_ABSTENTION",
        "reason_code": reason_code,
        "research_only": True,
        "production_probability_consumption_authorized": False,
        "betting_authorized": False,
        "target_id": target.target_id,
        "official_game_date": target.official_game_date,
        "mlb_game_pk": target.mlb_game_pk,
        "pitcher_id": pitcher_id,
        "pitching_team_id": pitching_team_id,
        "joint_opportunity": None,
        "market_probabilities": None,
    }
    return {**unsigned, "output_sha256": sha256_value(unsigned)}


def terminal_abstention_for_unverified_game(
    *,
    official_game_date: str,
    mlb_game_pk: int,
    pitcher_id: int,
    pitching_team_id: int,
    reason_code: str,
) -> dict[str, Any]:
    date_value = _date(official_game_date, "official_game_date").isoformat()
    game_pk = _int(mlb_game_pk, "mlb_game_pk", minimum=1)
    player_id = _int(pitcher_id, "pitcher_id", minimum=1)
    team_id = _int(pitching_team_id, "pitching_team_id", minimum=1)
    unsigned = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "terminal_state": "TERMINAL_ABSTENTION",
        "reason_code": reason_code,
        "research_only": True,
        "production_probability_consumption_authorized": False,
        "betting_authorized": False,
        "target_id": None,
        "official_game_date": date_value,
        "mlb_game_pk": game_pk,
        "pitcher_id": player_id,
        "pitching_team_id": team_id,
        "joint_opportunity": None,
        "market_probabilities": None,
    }
    return {**unsigned, "output_sha256": sha256_value(unsigned)}


def apply_candidate(
    *,
    official_game_date: str,
    mlb_game_pk: int,
    pitching_side: str,
    pitching_team_id: int,
    pitcher_id: int,
    model_artifact: Mapping[str, Any] | None,
    model_authorization_receipt: Mapping[str, Any] | None,
    runtime_release_receipt: Mapping[str, Any] | None,
    protocol_path: str | Path,
) -> dict[str, Any]:
    """Fail-closed probability boundary for the current unbound scaffold.

    Evidence location and expected semantic bindings may come only from the
    exact authority contract.  This release intentionally has no approved
    archive era, root, collector release, target, or workload-receipt manifest,
    so it must abstain before touching any supplied model object. Workload root,
    manifest, feature artifact, identity, chronology, parser, and schema have no
    caller-supplied application arguments and must eventually derive solely from
    an externally authorized contract.
    """

    protocol = load_protocol(protocol_path)
    authority = load_evidence_authority_contract(
        protocol_path=protocol_path, protocol=protocol
    )
    team_id = _int(pitching_team_id, "pitching_team_id", minimum=1)
    player_id = _int(pitcher_id, "pitcher_id", minimum=1)
    if pitching_side not in {"home", "away"}:
        raise PitcherJointOpportunityError("pitching_side must be home or away")
    if authority["status"] != "EXTERNALLY_BOUND_APPROVED_ARCHIVE_ERA":
        return terminal_abstention_for_unverified_game(
            official_game_date=official_game_date,
            mlb_game_pk=mlb_game_pk,
            pitcher_id=player_id,
            pitching_team_id=team_id,
            reason_code="EVIDENCE_AUTHORITY_NOT_EXTERNALLY_BOUND",
        )
    raise PitcherJointOpportunityError(
        "this release cannot consume a bound authority without a new reviewed implementation"
    )
