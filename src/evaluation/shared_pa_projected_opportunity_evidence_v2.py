"""Replayable pregame evidence for the projected-opportunity v2 candidate.

This boundary deliberately accepts retained source bytes, not caller-authored
hash labels.  It proves the immutable schedule plan, game side/team, T-4
horizon, active roster, strictly-prior lineup history, projected lineup, and
date-bounded official hitting counts before returning any model input.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

from src.evaluation.projected_lineup_contract import load_contract, sha256_value
from src.evaluation.projected_lineup_empirical_joint import build_empirical_joint_projection
from src.evaluation.projected_lineup_history import build_feature_store
from src.evaluation.projected_lineup_official_roster import (
    RawOfficialRosterResponse,
    parse_active_roster_receipt,
)
from src.evaluation.prospective_batter_opportunity import (
    ProspectiveBatterOpportunityError,
    build_pregame_opportunity_snapshot,
)
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.shared_pa_forward_collector import (
    RawPregameResponse,
    projected_lineups_from_schedule,
)
from src.evaluation.shared_pa_forward_evidence import PA_OUTCOMES


class ProjectedOpportunityEvidenceV2Error(ValueError):
    """The retained pregame evidence does not replay to one exact target."""


SCHEMA_VERSION = "shared-pa-projected-opportunity-evidence-v2"
STATS_SOURCE = "https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
STATS_FIELDS = (
    "people,id,stats,group,displayName,type,splits,date,game,stat,plateAppearances,"
    "atBats,hits,doubles,triples,homeRuns,baseOnBalls,strikeOuts"
)


@dataclass(frozen=True)
class RawDateBoundedStatsResponse:
    body: bytes
    request_sent_at_utc: str
    received_at_utc: str
    request_url: str
    http_status: int
    content_type: str
    request_sha256: str
    payload_sha256: str
    payload_size: int
    transport_receipt_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.body, bytes) or not self.body:
            raise ProjectedOpportunityEvidenceV2Error("stats response bytes are missing")
        sent = _utc(self.request_sent_at_utc, "stats request_sent_at_utc")
        received = _utc(self.received_at_utc, "stats received_at_utc")
        if received < sent:
            raise ProjectedOpportunityEvidenceV2Error("stats response predates its request")
        if not isinstance(self.request_url, str) or not self.request_url:
            raise ProjectedOpportunityEvidenceV2Error("stats request URL is missing")
        if isinstance(self.http_status, bool) or self.http_status != 200:
            raise ProjectedOpportunityEvidenceV2Error("stats response is not HTTP 200")
        if not isinstance(self.content_type, str) or self.content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise ProjectedOpportunityEvidenceV2Error("stats response is not JSON")
        request_identity = sha256_value({
            "method": "GET",
            "request_url": self.request_url,
            "request_sent_at_utc": self.request_sent_at_utc,
        })
        payload_identity = hashlib.sha256(self.body).hexdigest()
        if self.request_sha256 != request_identity:
            raise ProjectedOpportunityEvidenceV2Error("stats transport request hash differs")
        if self.payload_sha256 != payload_identity or self.payload_size != len(self.body):
            raise ProjectedOpportunityEvidenceV2Error("stats transport payload identity differs")
        if self.transport_receipt_sha256 != sha256_value(self.transport_receipt()):
            raise ProjectedOpportunityEvidenceV2Error("stats transport receipt hash differs")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def transport_receipt(self) -> dict[str, Any]:
        return {
            "schema_version": "official-mlb-dated-game-log-transport-v1",
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
        cls, *, body: bytes, request_sent_at_utc: str, received_at_utc: str,
        request_url: str, http_status: int = 200,
        content_type: str = "application/json",
    ) -> "RawDateBoundedStatsResponse":
        request_hash = sha256_value({
            "method": "GET",
            "request_url": request_url,
            "request_sent_at_utc": request_sent_at_utc,
        })
        payload_hash = hashlib.sha256(body).hexdigest()
        receipt = {
            "schema_version": "official-mlb-dated-game-log-transport-v1",
            "method": "GET",
            "request_url": request_url,
            "request_sent_at_utc": request_sent_at_utc,
            "received_at_utc": received_at_utc,
            "http_status": http_status,
            "content_type": content_type,
            "request_sha256": request_hash,
            "payload_sha256": payload_hash,
            "payload_size": len(body),
        }
        return cls(
            body=body,
            request_sent_at_utc=request_sent_at_utc,
            received_at_utc=received_at_utc,
            request_url=request_url,
            http_status=http_status,
            content_type=content_type,
            request_sha256=request_hash,
            payload_sha256=payload_hash,
            payload_size=len(body),
            transport_receipt_sha256=sha256_value(receipt),
        )


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProjectedOpportunityEvidenceV2Error(f"{label} must be timezone-aware ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectedOpportunityEvidenceV2Error(f"{label} must be timezone-aware ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProjectedOpportunityEvidenceV2Error(f"{label} must be timezone-aware ISO-8601")
    return parsed.astimezone(timezone.utc)


def _canonical_date(value: Any) -> date:
    if not isinstance(value, str):
        raise ProjectedOpportunityEvidenceV2Error("official_game_date must be canonical YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ProjectedOpportunityEvidenceV2Error("official_game_date must be canonical YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ProjectedOpportunityEvidenceV2Error("official_game_date must be canonical YYYY-MM-DD")
    if parsed.year == 2026 and parsed.month == 5:
        raise ProjectedOpportunityEvidenceV2Error("May 2026 is sealed")
    return parsed


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProjectedOpportunityEvidenceV2Error(f"{label} must be a positive integer")
    return value


def expected_stats_requests(*, player_id: int, target_date: date) -> tuple[list[str], str]:
    """Return the exact prior-date request segments, skipping May 2026 entirely."""
    player = _positive_int(player_id, "player_id")
    if not isinstance(target_date, date) or (
        target_date.year == 2026 and target_date.month == 5
    ):
        raise ProjectedOpportunityEvidenceV2Error("May 2026 is sealed")
    cutoff = target_date.fromordinal(target_date.toordinal() - 1).isoformat()
    cutoff_date = date.fromisoformat(cutoff)
    intervals: list[tuple[date, date]]
    if target_date.year == 2026 and target_date.month > 5:
        intervals = [(date(2026, 1, 1), date(2026, 4, 30))]
        if cutoff_date >= date(2026, 6, 1):
            intervals.append((date(2026, 6, 1), cutoff_date))
    else:
        intervals = [(date(target_date.year, 1, 1), cutoff_date)]
    urls = [
        f"{STATS_SOURCE.format(player_id=player)}?stats=gameLog&group=hitting&"
        f"startDate={start.isoformat()}&endDate={end.isoformat()}&fields={STATS_FIELDS}"
        for start, end in intervals
    ]
    return urls, cutoff


def _validate_stats_request(
    url: str, *, expected: str
) -> tuple[date, date]:
    actual_parts = urlsplit(url)
    expected_parts = urlsplit(expected)
    if (
        actual_parts.scheme != "https"
        or actual_parts.netloc != expected_parts.netloc
        or actual_parts.path != expected_parts.path
        or actual_parts.fragment
        or parse_qs(actual_parts.query, keep_blank_values=True)
        != parse_qs(expected_parts.query, keep_blank_values=True)
    ):
        raise ProjectedOpportunityEvidenceV2Error("stats source, player, season, or prior-date cutoff differs")
    query = parse_qs(actual_parts.query, keep_blank_values=True)
    return date.fromisoformat(query["startDate"][0]), date.fromisoformat(query["endDate"][0])


def _nonnegative(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProjectedOpportunityEvidenceV2Error(f"{label} must be a non-negative integer")
    return value


def _exact_keys(
    value: Any, allowed: set[str], label: str, *, required: set[str] | None = None
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectedOpportunityEvidenceV2Error(f"{label} must be an object")
    keys = set(value)
    if not keys.issubset(allowed) or (required is not None and not required.issubset(keys)):
        raise ProjectedOpportunityEvidenceV2Error(
            f"{label} contains an unapproved or missing field"
        )
    return value


def _validate_dated_stats_surface(body: bytes) -> Mapping[str, Any]:
    """Allow only the dated game-log identity and locked count surface."""
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectedOpportunityEvidenceV2Error("dated stats payload is invalid JSON") from exc
    payload = _exact_keys(decoded, {"people"}, "dated stats root", required={"people"})
    people = payload["people"]
    if not isinstance(people, list):
        raise ProjectedOpportunityEvidenceV2Error("dated stats people must be a list")
    approved_stats = {
        "plateAppearances", "atBats", "hits", "doubles", "triples",
        "homeRuns", "baseOnBalls", "strikeOuts",
    }
    for person_value in people:
        person = _exact_keys(
            person_value, {"id", "stats"}, "dated stats person", required={"id", "stats"}
        )
        blocks = person["stats"]
        if not isinstance(blocks, list):
            raise ProjectedOpportunityEvidenceV2Error("dated stats blocks must be a list")
        for block_value in blocks:
            block = _exact_keys(
                block_value,
                {"group", "type", "splits"},
                "dated stats block",
                required={"group", "type", "splits"},
            )
            _exact_keys(
                block["group"], {"displayName"}, "dated stats group",
                required={"displayName"},
            )
            _exact_keys(
                block["type"], {"displayName"}, "dated stats type",
                required={"displayName"},
            )
            splits = block["splits"]
            if not isinstance(splits, list):
                raise ProjectedOpportunityEvidenceV2Error("dated stats splits must be a list")
            for split_value in splits:
                split = _exact_keys(
                    split_value,
                    {"date", "game", "stat"},
                    "dated stats split",
                    required={"date", "game", "stat"},
                )
                _exact_keys(
                    split["game"], {"id"}, "dated stats game", required={"id"}
                )
                _exact_keys(split["stat"], approved_stats, "dated stats counts")
    return payload


def replay_date_bounded_counts(
    responses: Sequence[RawDateBoundedStatsResponse],
    *,
    player_id: int,
    target_date: date,
    horizon: datetime,
) -> tuple[dict[str, int], str]:
    expected_urls, cutoff = expected_stats_requests(player_id=player_id, target_date=target_date)
    if (
        not isinstance(responses, Sequence)
        or isinstance(responses, (str, bytes))
        or len(responses) != len(expected_urls)
    ):
        raise ProjectedOpportunityEvidenceV2Error("every permitted stats request segment is required")
    candidates: list[Mapping[str, Any]] = []
    seen_games: set[int] = set()
    for response, expected_url in zip(responses, expected_urls):
        if not isinstance(response, RawDateBoundedStatsResponse):
            raise ProjectedOpportunityEvidenceV2Error("stats transport receipt type changed")
        if _utc(response.received_at_utc, "stats received_at_utc") > horizon:
            raise ProjectedOpportunityEvidenceV2Error("stats response arrived after T-4")
        segment_start, segment_end = _validate_stats_request(
            response.request_url, expected=expected_url
        )
        payload = _validate_dated_stats_surface(response.body)
        people = payload.get("people")
        if not isinstance(people, list) or len(people) != 1 or people[0].get("id") != player_id:
            raise ProjectedOpportunityEvidenceV2Error("stats response player identity differs")
        blocks = people[0].get("stats", [])
        if not isinstance(blocks, list) or len(blocks) > 1:
            raise ProjectedOpportunityEvidenceV2Error("dated game-log stats block is ambiguous")
        for block in blocks:
            if (
                not isinstance(block, Mapping)
                or block.get("group", {}).get("displayName") != "hitting"
                or block.get("type", {}).get("displayName") != "gameLog"
            ):
                raise ProjectedOpportunityEvidenceV2Error("stats response is not a hitting game log")
            for split in block.get("splits", []):
                if not isinstance(split, Mapping) or not isinstance(split.get("stat"), Mapping):
                    raise ProjectedOpportunityEvidenceV2Error("dated game-log split is malformed")
                split_date = _canonical_date(split.get("date"))
                game = split.get("game")
                game_pk = _positive_int(game.get("id") if isinstance(game, Mapping) else None, "stats game id")
                if not segment_start <= split_date <= segment_end or game_pk in seen_games:
                    raise ProjectedOpportunityEvidenceV2Error(
                        "stats body contains an out-of-range or duplicate dated game"
                    )
                seen_games.add(game_pk)
                candidates.append(split["stat"])
    required = {
        "plateAppearances", "atBats", "hits", "doubles", "triples",
        "homeRuns", "baseOnBalls", "strikeOuts",
    }
    totals = {key: 0 for key in required}
    for stat in candidates:
        if set(stat) != required:
            raise ProjectedOpportunityEvidenceV2Error("date-bounded stats count surface changed")
        for key in required:
            totals[key] += _nonnegative(stat[key], key)
    pa = totals["plateAppearances"]
    ab = totals["atBats"]
    hits = totals["hits"]
    doubles = totals["doubles"]
    triples = totals["triples"]
    home_runs = totals["homeRuns"]
    walks = totals["baseOnBalls"]
    strikeouts = totals["strikeOuts"]
    counts = {
        "strikeout": strikeouts,
        "walk": walks,
        "single": hits - doubles - triples - home_runs,
        "double": doubles,
        "triple": triples,
        "home_run": home_runs,
        "bip_out": ab - hits - strikeouts,
        "other_non_ab": pa - ab - walks,
    }
    if any(value < 0 for value in counts.values()) or sum(counts.values()) != pa:
        raise ProjectedOpportunityEvidenceV2Error("date-bounded counts do not form a truthful PA partition")
    return counts, cutoff


def replay_projected_context_v2(
    *,
    root: Any,
    plan: ShadowCapturePlan,
    target: CaptureTarget,
    side: str,
    team_id: int,
    schedule_response: RawPregameResponse,
    active_roster_receipt: Mapping[str, Any],
    active_roster_raw: bytes,
    history_records: Sequence[Mapping[str, Any]],
    history_raw_by_sha256: Mapping[str, bytes],
    history_coverage: Mapping[str, Any],
    history_schedule_raw_by_sha256: Mapping[str, bytes],
    opportunity_snapshot: Mapping[str, Any],
    projected_lineup_record: Mapping[str, Any],
    prediction_generated_at_utc: str,
) -> dict[str, Any]:
    """Replay the shared plan, schedule, roster, history, and projection bytes."""
    if not isinstance(plan, ShadowCapturePlan) or not isinstance(target, CaptureTarget):
        raise ProjectedOpportunityEvidenceV2Error("typed immutable plan and target are required")
    if target.target_id not in {value.target_id for value in plan.targets}:
        raise ProjectedOpportunityEvidenceV2Error("target is absent from immutable plan")
    if plan.entry_hours != 4 or target.entry_hours != 4:
        raise ProjectedOpportunityEvidenceV2Error("candidate requires exact T-4")
    target_date = _canonical_date(target.official_game_date)
    horizon = _utc(target.entry_target_at_utc, "target horizon")
    generated = _utc(prediction_generated_at_utc, "prediction_generated_at_utc")
    if generated > horizon:
        raise ProjectedOpportunityEvidenceV2Error("prediction was generated after T-4")
    parsed_schedule = projected_lineups_from_schedule(
        response=schedule_response, plan=plan, target=target
    )
    if side not in {"home", "away"}:
        raise ProjectedOpportunityEvidenceV2Error("side must be home or away")
    expected_team = parsed_schedule[f"{side}_team_id"]
    if _positive_int(team_id, "team_id") != expected_team:
        raise ProjectedOpportunityEvidenceV2Error("side/team identity differs from official schedule")
    reproduced_roster = parse_active_roster_receipt(
        response=RawOfficialRosterResponse(active_roster_raw, str(active_roster_receipt.get("received_at_utc"))),
        requested_date=target_date.isoformat(),
        team_id=expected_team,
        target_horizon_utc=target.entry_target_at_utc,
    )
    if reproduced_roster != dict(active_roster_receipt):
        raise ProjectedOpportunityEvidenceV2Error("active roster differs from retained raw replay")
    try:
        replayed_snapshot = build_pregame_opportunity_snapshot(
            official_game_date=target_date.isoformat(),
            mlb_game_pk=target.mlb_game_pk,
            side=side,
            team_id=expected_team,
            target_horizon_utc=target.entry_target_at_utc,
            assembled_at_utc=prediction_generated_at_utc,
            active_roster_receipt=reproduced_roster,
            active_roster_raw=active_roster_raw,
            history_records=history_records,
            history_raw_by_sha256=history_raw_by_sha256,
            history_coverage=history_coverage,
            schedule_raw_by_sha256=history_schedule_raw_by_sha256,
        )
    except ProspectiveBatterOpportunityError as exc:
        raise ProjectedOpportunityEvidenceV2Error(
            "historical lineup source bytes failed semantic replay"
        ) from exc
    if replayed_snapshot != dict(opportunity_snapshot):
        raise ProjectedOpportunityEvidenceV2Error(
            "historical opportunity snapshot differs from raw replay"
        )
    if (
        replayed_snapshot["history_coverage"].get("complete_coverage") is not True
        or any(row.get("fit_eligible") is not True for row in replayed_snapshot["features"])
    ):
        raise ProjectedOpportunityEvidenceV2Error(
            "historical lineup coverage is incomplete; no fallback is allowed"
        )
    completed_lineups: list[dict[str, Any]] = []
    for record in history_records:
        for player in record["players"]:
            if player["is_starter"]:
                completed_lineups.append({
                    "official_game_date": record["official_game_date"],
                    "mlb_game_pk": record["mlb_game_pk"],
                    "team_id": expected_team,
                    "player_id": player["player_id"],
                    "slot": player["lineup_slot"],
                })
    replayed_store = build_feature_store(
        official_game_date=target_date.isoformat(),
        team_id=expected_team,
        active_roster_receipt=reproduced_roster,
        completed_lineups=completed_lineups,
    )
    lineup_contract = load_contract(root / "config/projected_lineup_contract_v1.json")
    replayed_projection = build_empirical_joint_projection(
        official_game_date=target_date.isoformat(),
        mlb_game_pk=target.mlb_game_pk,
        team_id=expected_team,
        target_horizon_utc=target.entry_target_at_utc,
        projection_receipt_utc=str(projected_lineup_record.get("projection_receipt_utc")),
        active_roster_receipt=reproduced_roster,
        historical_feature_store=replayed_store,
        completed_lineups=completed_lineups,
        contract=lineup_contract,
    )
    if replayed_projection != dict(projected_lineup_record):
        raise ProjectedOpportunityEvidenceV2Error("projected lineup differs from raw/semantic replay")
    receipt_times = [
        _utc(schedule_response.received_at_utc, "schedule received_at_utc"),
        _utc(reproduced_roster["received_at_utc"], "roster received_at_utc"),
        _utc(replayed_projection["projection_receipt_utc"], "projection_receipt_utc"),
    ]
    receipt_times.extend(
        _utc(record["source_received_at_utc"], "history source_received_at_utc")
        for record in history_records
    )
    receipt_times.extend(
        _utc(receipt["source_received_at_utc"], "history schedule source_received_at_utc")
        for receipt in replayed_snapshot["history_coverage"]["schedule_receipts"]
    )
    if any(received > generated for received in receipt_times):
        raise ProjectedOpportunityEvidenceV2Error(
            "prediction generation predates a retained pregame input receipt"
        )
    return {
        "target_date": target_date,
        "horizon": horizon,
        "generated": generated,
        "expected_team": expected_team,
        "reproduced_roster": reproduced_roster,
        "replayed_store": replayed_store,
        "replayed_projection": replayed_projection,
        "replayed_snapshot": replayed_snapshot,
        "completed_lineups": completed_lineups,
        "latest_shared_input_receipt_utc": max(receipt_times).isoformat().replace("+00:00", "Z"),
    }


def build_evidence_envelope_v2(
    *,
    root: Any,
    plan: ShadowCapturePlan,
    target: CaptureTarget,
    side: str,
    team_id: int,
    schedule_response: RawPregameResponse,
    active_roster_receipt: Mapping[str, Any],
    active_roster_raw: bytes,
    history_records: Sequence[Mapping[str, Any]],
    history_raw_by_sha256: Mapping[str, bytes],
    history_coverage: Mapping[str, Any],
    history_schedule_raw_by_sha256: Mapping[str, bytes],
    opportunity_snapshot: Mapping[str, Any],
    projected_lineup_record: Mapping[str, Any],
    player_id: int,
    stats_responses: Sequence[RawDateBoundedStatsResponse],
    prediction_generated_at_utc: str,
) -> dict[str, Any]:
    """Replay all source evidence into one typed, probability-free envelope."""
    context = replay_projected_context_v2(
        root=root,
        plan=plan,
        target=target,
        side=side,
        team_id=team_id,
        schedule_response=schedule_response,
        active_roster_receipt=active_roster_receipt,
        active_roster_raw=active_roster_raw,
        history_records=history_records,
        history_raw_by_sha256=history_raw_by_sha256,
        history_coverage=history_coverage,
        history_schedule_raw_by_sha256=history_schedule_raw_by_sha256,
        opportunity_snapshot=opportunity_snapshot,
        projected_lineup_record=projected_lineup_record,
        prediction_generated_at_utc=prediction_generated_at_utc,
    )
    target_date = context["target_date"]
    horizon = context["horizon"]
    expected_team = context["expected_team"]
    reproduced_roster = context["reproduced_roster"]
    replayed_store = context["replayed_store"]
    replayed_projection = context["replayed_projection"]
    replayed_snapshot = context["replayed_snapshot"]
    player = _positive_int(player_id, "player_id")
    if player not in replayed_projection.get("active_roster_player_ids", []):
        raise ProjectedOpportunityEvidenceV2Error("player is absent from replayed active roster")
    if any(
        _utc(response.request_sent_at_utc, "stats request_sent_at_utc") > context["generated"]
        or _utc(response.received_at_utc, "stats received_at_utc") > context["generated"]
        for response in stats_responses
    ):
        raise ProjectedOpportunityEvidenceV2Error(
            "prediction generation predates a retained stats request or response"
        )
    counts, cutoff = replay_date_bounded_counts(
        stats_responses, player_id=player, target_date=target_date, horizon=horizon
    )
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "plan_sha256": plan.plan_sha256,
        "target_id": target.target_id,
        "official_game_date": target_date.isoformat(),
        "official_start_utc": target.official_start_time_utc,
        "target_horizon_utc": target.entry_target_at_utc,
        "mlb_game_pk": target.mlb_game_pk,
        "side": side,
        "team_id": expected_team,
        "player_id": player,
        "schedule_raw_sha256": schedule_response.sha256,
        "schedule_receipt_utc": schedule_response.received_at_utc,
        "active_roster_raw_sha256": hashlib.sha256(active_roster_raw).hexdigest(),
        "active_roster_receipt_sha256": sha256_value(reproduced_roster),
        "historical_feature_store_sha256": replayed_store["feature_store_sha256"],
        "historical_opportunity_snapshot_sha256": replayed_snapshot["snapshot_sha256"],
        "historical_coverage_sha256": replayed_snapshot["history_coverage"]["coverage_sha256"],
        "historical_raw_sha256s": replayed_snapshot["history_raw_sha256s"],
        "historical_schedule_raw_sha256s": replayed_snapshot["schedule_raw_sha256s"],
        "projected_lineup_content_sha256": replayed_projection["projection_content_sha256"],
        "stats_source": "official_mlb_statsapi_dated_game_log_segmented_v2",
        "stats_request_urls": [response.request_url for response in stats_responses],
        "stats_cutoff_date": cutoff,
        "stats_season": target_date.year,
        "stats_received_at_utc": [response.received_at_utc for response in stats_responses],
        "stats_raw_sha256s": [response.sha256 for response in stats_responses],
        "stats_transport_receipt_sha256s": [
            response.transport_receipt_sha256 for response in stats_responses
        ],
        "stats_counts": counts,
        "prediction_generated_at_utc": prediction_generated_at_utc,
    }
    return {**unsigned, "evidence_envelope_sha256": sha256_value(unsigned)}
