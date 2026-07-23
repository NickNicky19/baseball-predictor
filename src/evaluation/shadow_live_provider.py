"""Live The Odds API -> hard-keyed DraftKings Hits shadow evidence.

The network client retains provider responses but does not decide identity.
The resolver separately requires one exact provider/MLB event, one exact
two-sided line, source outcome ids, and one exact player name within the
already hard-game-keyed prediction snapshot.  Every raw candidate group gets
one funnel status; unresolved rows never disappear through an inner join.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.evaluation.live_market_identity import ResolvedLiveMarketQuote
from src.evaluation.shadow_capture_plan import CaptureTarget
from src.evaluation.shadow_prediction_snapshot import (
    ShadowPredictionSnapshotError,
    load_shadow_prediction_snapshot,
)
from src.utils.provenance import sha256_file


SOURCE_NAME = "the_odds_api"
SPORT_KEY = "baseball_mlb"
BOOKMAKER_KEY = "draftkings"
MARKET_KEY = "batter_hits"
SHARED_PA_MARKET_KEYS = (
    "batter_hits",
    "batter_home_runs",
    "batter_total_bases",
)
BASE_URL = "https://api.the-odds-api.com/v4"
_WS = re.compile(r"\s+")
RESOLUTION_STATUSES = frozenset({
    "missing_market_timestamp",
    "post_target_market_timestamp",
    "not_exact_two_sided",
    "missing_or_repeated_outcome_sid",
    "invalid_price",
    "player_not_in_prediction_snapshot",
    "ambiguous_player_name_in_prediction_snapshot",
    "model_tail_unavailable",
    "resolved",
})


class ShadowLiveProviderError(ValueError):
    """A live provider artifact cannot satisfy the locked shadow contract."""


def artifact_bytes(value: object) -> bytes:
    """The one JSON encoding used for every published provider-side artifact."""

    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def artifact_sha256(value: object) -> str:
    return hashlib.sha256(artifact_bytes(value)).hexdigest()


def _utc(value: Any, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ShadowLiveProviderError(f"{label} must be ISO-8601 with timezone") from exc
    if parsed.tzinfo is None:
        raise ShadowLiveProviderError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return _WS.sub(" ", text)


def _line(value: Any) -> str:
    try:
        parsed = Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError) as exc:
        raise ShadowLiveProviderError("provider line must be a finite decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ShadowLiveProviderError("provider line must be finite and non-negative")
    return format(parsed, "f")


def _american(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ShadowLiveProviderError("provider price must be integer American odds") from exc
    if parsed == 0:
        raise ShadowLiveProviderError("provider American odds cannot be zero")
    return parsed


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowLiveProviderError(f"{label} must be an object")
    return dict(value)


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ShadowLiveProviderError(f"{label} must be a list")
    return value


def _derived_player_key(event_id: str, normalized_name: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {"event_id": event_id, "normalized_description": normalized_name},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    # The Odds API exposes selection ids, not a separate player id. State that
    # truth in the identifier instead of pretending a provider player id exists.
    return f"derived-event-description:{digest}"


@dataclass(frozen=True)
class ProviderResponse:
    body: bytes
    received_at_utc: str
    status_code: int
    quota: dict[str, str]

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ShadowLiveProviderError("provider returned malformed JSON") from exc


class TheOddsAPIShadowClient:
    """Small credential-isolated client for exact live shadow snapshots."""

    def __init__(
        self,
        *,
        api_key_env: str = "ODDS_API_KEY",
        base_url: str = BASE_URL,
        timeout_seconds: int = 25,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/") or BASE_URL
        self.timeout_seconds = int(timeout_seconds)
        self.session = session or requests.Session()
        if session is None:
            retry = Retry(
                total=4,
                connect=4,
                read=4,
                status=4,
                allowed_methods=frozenset({"GET"}),
                status_forcelist=(429, 500, 502, 503, 504),
                backoff_factor=1.0,
                respect_retry_after_header=True,
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)
        self.session.headers.update({"User-Agent": "baseball-predictor-shadow/1.0"})

    def _key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise ShadowLiveProviderError(
                f"missing provider credential in environment variable {self.api_key_env}"
            )
        return key

    def _get(self, path: str, params: Mapping[str, str]) -> ProviderResponse:
        safe_params = dict(params)
        safe_params["apiKey"] = self._key()
        try:
            response = self.session.get(
                f"{self.base_url}/{path.lstrip('/')}",
                params=safe_params,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise ShadowLiveProviderError(
                f"provider request failed ({type(exc).__name__}); URL and credential redacted"
            ) from exc
        received = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        quota = {
            key: response.headers.get(key, "")
            for key in ("x-requests-remaining", "x-requests-used", "x-requests-last")
        }
        if response.status_code != 200:
            raise ShadowLiveProviderError(
                f"provider returned HTTP {response.status_code}; URL and credential redacted"
            )
        return ProviderResponse(response.content, received, response.status_code, quota)

    def fetch_events(self, official_game_date: str) -> ProviderResponse:
        slate = date.fromisoformat(official_game_date)
        start = datetime.combine(slate, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(hours=36)
        return self._get(
            f"sports/{SPORT_KEY}/events",
            {
                "commenceTimeFrom": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "commenceTimeTo": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "dateFormat": "iso",
            },
        )

    def fetch_hits(self, event_id: str) -> ProviderResponse:
        if not str(event_id).strip():
            raise ShadowLiveProviderError("provider event id cannot be blank")
        return self._get(
            f"sports/{SPORT_KEY}/events/{event_id}/odds",
            {
                "bookmakers": BOOKMAKER_KEY,
                "markets": MARKET_KEY,
                "oddsFormat": "american",
                "dateFormat": "iso",
                "includeSids": "true",
                "includeLinks": "false",
            },
        )

    def fetch_shared_pa_markets(self, event_id: str) -> ProviderResponse:
        """Fetch the three locked shared-PA markets in one retained response.

        The existing Hits collector continues to call ``fetch_hits`` unchanged.
        This separate method is inert until the new comparator service invokes
        it, and it never interprets a one-sided quote as a fair probability.
        """
        if not str(event_id).strip():
            raise ShadowLiveProviderError("provider event id cannot be blank")
        return self._get(
            f"sports/{SPORT_KEY}/events/{event_id}/odds",
            {
                "bookmakers": BOOKMAKER_KEY,
                "markets": ",".join(SHARED_PA_MARKET_KEYS),
                "oddsFormat": "american",
                "dateFormat": "iso",
                "includeSids": "true",
                "includeLinks": "false",
            },
        )


def exact_game_identity(
    *,
    target: CaptureTarget,
    schedule_snapshot: Mapping[str, Any],
    provider_events: Sequence[Mapping[str, Any]],
    max_event_start_delta_seconds: int,
) -> dict[str, Any]:
    """Resolve one event by exact teams and equal-cardinality slate order.

    Provider and MLB start times are independent source facts.  The measured
    delta bound can reject a mapping, but it never selects the nearest event.
    """

    if (
        isinstance(max_event_start_delta_seconds, bool)
        or int(max_event_start_delta_seconds) <= 0
    ):
        raise ShadowLiveProviderError(
            "max_event_start_delta_seconds must be a positive integer"
        )
    max_delta = int(max_event_start_delta_seconds)

    schedule = _list(schedule_snapshot.get("schedule"), "schedule snapshot.schedule")
    official = [
        _object(row, "schedule row")
        for row in schedule
        if isinstance(row, Mapping) and int(row.get("gamePk", 0)) == target.mlb_game_pk
    ]
    if len(official) != 1:
        raise ShadowLiveProviderError("target MLB game is absent or ambiguous in schedule snapshot")
    game = official[0]
    if str(game.get("officialDate")) != target.official_game_date:
        raise ShadowLiveProviderError("schedule officialDate differs from target")
    if _utc(game.get("gameDate"), "schedule.gameDate") != target.official_start_time_utc:
        raise ShadowLiveProviderError("schedule start differs from target")
    teams = _object(game.get("teams"), "schedule.teams")
    home = str(_object(_object(teams.get("home"), "schedule.home").get("team"), "schedule.home.team").get("name", "")).strip()
    away = str(_object(_object(teams.get("away"), "schedule.away").get("team"), "schedule.away.team").get("name", "")).strip()
    if not home or not away:
        raise ShadowLiveProviderError("schedule team names cannot be blank")

    official_pair: list[dict[str, Any]] = []
    for raw in schedule:
        row = _object(raw, "schedule row")
        row_teams = _object(row.get("teams"), "schedule row.teams")
        row_home = str(_object(
            _object(row_teams.get("home"), "schedule row.home").get("team"),
            "schedule row.home.team",
        ).get("name", "")).strip()
        row_away = str(_object(
            _object(row_teams.get("away"), "schedule row.away").get("team"),
            "schedule row.away.team",
        ).get("name", "")).strip()
        if (
            str(row.get("officialDate")) == target.official_game_date
            and _name(row_home) == _name(home)
            and _name(row_away) == _name(away)
        ):
            official_pair.append(row)

    provider_pair: list[dict[str, Any]] = []
    for raw in provider_events:
        event = _object(raw, "provider event")
        if (
            _name(event.get("home_team")) == _name(home)
            and _name(event.get("away_team")) == _name(away)
        ):
            provider_pair.append(event)
    if not official_pair or len(official_pair) != len(provider_pair):
        raise ShadowLiveProviderError(
            "exact team-pair event cardinality differs between MLB and provider"
        )

    official_pair.sort(key=lambda row: _utc(row.get("gameDate"), "schedule pair gameDate"))
    provider_pair.sort(key=lambda row: _utc(
        row.get("commence_time"), "provider pair commence_time"
    ))
    official_starts = [
        _utc(row.get("gameDate"), "schedule pair gameDate") for row in official_pair
    ]
    provider_starts = [
        _utc(row.get("commence_time"), "provider pair commence_time")
        for row in provider_pair
    ]
    official_game_pks = [int(row.get("gamePk", 0)) for row in official_pair]
    provider_event_ids = [str(row.get("id", "")).strip() for row in provider_pair]
    if (
        len(set(official_starts)) != len(official_starts)
        or len(set(provider_starts)) != len(provider_starts)
        or any(value <= 0 for value in official_game_pks)
        or len(set(official_game_pks)) != len(official_game_pks)
        or any(not value for value in provider_event_ids)
        or len(set(provider_event_ids)) != len(provider_event_ids)
    ):
        raise ShadowLiveProviderError(
            "team-pair chronological identity is tied, blank, or repeated"
        )
    target_positions = [
        index for index, value in enumerate(official_game_pks)
        if value == target.mlb_game_pk
    ]
    if len(target_positions) != 1:
        raise ShadowLiveProviderError("target is ambiguous within exact MLB team pair")
    position = target_positions[0]
    event = provider_pair[position]
    event_id = str(event.get("id", "")).strip()
    source_start = provider_starts[position]
    delta_seconds = int(
        (_utc_dt(source_start) - _utc_dt(target.official_start_time_utc)).total_seconds()
    )
    if abs(delta_seconds) > max_delta:
        raise ShadowLiveProviderError(
            "provider/MLB event start delta exceeds the locked identity bound"
        )
    return {
        "schema_version": "shadow-live-game-identity-v2",
        "resolution_method": "exact_nfkc_casefold_teams_equal_cardinality_chronological_ordinal",
        "source_name": SOURCE_NAME,
        "source_event_id": event_id,
        "source_event_start_time_utc": source_start,
        "source_home_team": str(event.get("home_team")),
        "source_away_team": str(event.get("away_team")),
        "mlb_game_pk": target.mlb_game_pk,
        "official_game_date": target.official_game_date,
        "official_start_time_utc": target.official_start_time_utc,
        "official_home_team": home,
        "official_away_team": away,
        "team_pair_event_count": len(official_pair),
        "team_pair_chronological_ordinal": position + 1,
        "start_delta_seconds": delta_seconds,
        "max_abs_start_delta_seconds": max_delta,
        "time_tolerance_used_for_selection": False,
        "start_delta_bound_used_for_rejection": True,
        "fuzzy_matching_used": False,
    }


def _raw_prediction_rows(path: Path, target: CaptureTarget) -> list[dict[str, Any]]:
    # First validate provenance, hard keys, ranges, and exact simulation tails.
    load_shadow_prediction_snapshot(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ShadowLiveProviderError("prediction archive is malformed") from exc
    rows: list[dict[str, Any]] = []
    for value in raw.get("hitter_projections", []):
        if not isinstance(value, Mapping):
            continue
        row = dict(value)
        if (
            int(row.get("mlb_game_pk") or 0) == target.mlb_game_pk
            and str(row.get("category", "")) == "hits"
        ):
            rows.append(row)
    return rows


def resolve_hits_snapshot(
    *,
    target: CaptureTarget,
    prediction_archive: str | Path,
    raw_provider_artifact: str | Path,
    game_identity: Mapping[str, Any],
    game_identity_artifact_sha256: str,
    observation_phase: str = "entry",
    observation_cutoff_at_utc: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return one exhaustive, hard-keyed entry or prestart quote artifact.

    ``observation_cutoff_at_utc`` is an operational evidence boundary, not a
    betting threshold.  Entry keeps the T-4h cutoff.  A close observation may
    instead use the official first-pitch cutoff, but never a post-start quote.
    """

    phase = str(observation_phase).strip().lower()
    if phase not in {"entry", "prestart_reference"}:
        raise ShadowLiveProviderError("observation_phase must be entry or prestart_reference")
    cutoff_at = _utc(
        observation_cutoff_at_utc or target.entry_target_at_utc,
        "observation_cutoff_at_utc",
    )
    if phase == "entry" and cutoff_at != target.entry_target_at_utc:
        raise ShadowLiveProviderError("entry observations must use the locked T-4h cutoff")
    if phase == "prestart_reference" and _utc_dt(cutoff_at) > _utc_dt(target.official_start_time_utc):
        raise ShadowLiveProviderError("prestart reference cutoff cannot be after official first pitch")

    raw_path = Path(raw_provider_artifact).resolve()
    try:
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ShadowLiveProviderError("raw event-odds artifact is malformed") from exc
    event = _object(payload, "event odds payload")
    expected_event = str(game_identity.get("source_event_id", ""))
    expected_source_start = _utc(
        game_identity.get("source_event_start_time_utc"),
        "game_identity.source_event_start_time_utc",
    )
    if (
        game_identity.get("schema_version") != "shadow-live-game-identity-v2"
        or int(game_identity.get("mlb_game_pk") or 0) != target.mlb_game_pk
        or str(game_identity.get("official_game_date")) != target.official_game_date
        or _utc(
            game_identity.get("official_start_time_utc"),
            "game_identity.official_start_time_utc",
        ) != target.official_start_time_utc
        or game_identity.get("fuzzy_matching_used") is not False
        or game_identity.get("time_tolerance_used_for_selection") is not False
        or game_identity.get("start_delta_bound_used_for_rejection") is not True
        or int(game_identity.get("start_delta_seconds")) != int(
            (_utc_dt(expected_source_start) - _utc_dt(target.official_start_time_utc)).total_seconds()
        )
        or abs(int(game_identity.get("start_delta_seconds")))
        > int(game_identity.get("max_abs_start_delta_seconds"))
        or str(event.get("id", "")) != expected_event
        or _utc(event.get("commence_time"), "event odds commence_time")
        != expected_source_start
        or _name(event.get("home_team")) != _name(game_identity.get("official_home_team"))
        or _name(event.get("away_team")) != _name(game_identity.get("official_away_team"))
    ):
        raise ShadowLiveProviderError("event-odds payload differs from hard game identity")

    invalid_outcomes = 0
    raw_outcomes = 0
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    bookmakers = [
        _object(value, "bookmaker")
        for value in _list(event.get("bookmakers", []), "event bookmakers")
        if isinstance(value, Mapping) and str(value.get("key", "")).lower() == BOOKMAKER_KEY
    ]
    if len(bookmakers) > 1:
        raise ShadowLiveProviderError("DraftKings bookmaker block is ambiguous")
    if bookmakers:
        markets = [
            _object(value, "market")
            for value in _list(bookmakers[0].get("markets", []), "bookmaker markets")
            if isinstance(value, Mapping) and str(value.get("key", "")) == MARKET_KEY
        ]
        if len(markets) > 1:
            raise ShadowLiveProviderError("DraftKings batter_hits market block is ambiguous")
        if markets:
            market = markets[0]
            market_at_raw = market.get("last_update")
            market_at = _utc(market_at_raw, "market.last_update") if market_at_raw else ""
            for raw_outcome in _list(market.get("outcomes", []), "market outcomes"):
                raw_outcomes += 1
                if not isinstance(raw_outcome, Mapping):
                    invalid_outcomes += 1
                    continue
                outcome = dict(raw_outcome)
                description = str(outcome.get("description", "")).strip()
                point = outcome.get("point")
                side = str(outcome.get("name", "")).strip().lower()
                if not description or point is None or side not in {"over", "under"}:
                    invalid_outcomes += 1
                    continue
                try:
                    line = _line(point)
                except ShadowLiveProviderError:
                    invalid_outcomes += 1
                    continue
                key = (_name(description), line)
                group = groups.setdefault(
                    key,
                    {
                        "source_player_name": description,
                        "normalized_player_name": key[0],
                        "line": line,
                        "quote_at_utc": market_at,
                        "over": [],
                        "under": [],
                    },
                )
                group[side].append(outcome)

    prediction_path = Path(prediction_archive).resolve()
    snapshot = load_shadow_prediction_snapshot(prediction_path)
    if snapshot.game_date != target.official_game_date:
        raise ShadowLiveProviderError("prediction date differs from target")
    predictions = _raw_prediction_rows(prediction_path, target)
    prediction_names: dict[str, list[dict[str, Any]]] = {}
    for row in predictions:
        prediction_names.setdefault(_name(row.get("player_name")), []).append(row)

    candidate_rows: list[dict[str, Any]] = []
    resolved_inputs: list[dict[str, Any]] = []
    for (normalized_player, line), group in sorted(groups.items()):
        candidate = {
            "source_player_name": group["source_player_name"],
            "normalized_player_name": normalized_player,
            "source_player_key": _derived_player_key(expected_event, normalized_player),
            "line": line,
            "status": "",
            "player_id": None,
        }
        if not group["quote_at_utc"]:
            candidate["status"] = "missing_market_timestamp"
        elif (
            _utc_dt(group["quote_at_utc"]) > _utc_dt(cutoff_at)
            or (
                phase == "prestart_reference"
                and _utc_dt(group["quote_at_utc"]) == _utc_dt(cutoff_at)
            )
        ):
            candidate["status"] = "post_target_market_timestamp"
        elif len(group["over"]) != 1 or len(group["under"]) != 1:
            candidate["status"] = "not_exact_two_sided"
        else:
            over = group["over"][0]
            under = group["under"][0]
            over_sid = str(over.get("sid", "")).strip()
            under_sid = str(under.get("sid", "")).strip()
            if not over_sid or not under_sid or over_sid == under_sid:
                candidate["status"] = "missing_or_repeated_outcome_sid"
            else:
                try:
                    over_price = _american(over.get("price"))
                    under_price = _american(under.get("price"))
                except ShadowLiveProviderError:
                    candidate["status"] = "invalid_price"
                else:
                    matches = prediction_names.get(normalized_player, [])
                    if not matches:
                        candidate["status"] = "player_not_in_prediction_snapshot"
                    elif len(matches) != 1:
                        candidate["status"] = "ambiguous_player_name_in_prediction_snapshot"
                    else:
                        player_id = int(matches[0].get("player_id") or 0)
                        try:
                            snapshot.probability_for(
                                target.mlb_game_pk, player_id, "hits", line
                            )
                        except ShadowPredictionSnapshotError:
                            candidate["status"] = "model_tail_unavailable"
                        else:
                            candidate["status"] = "resolved"
                            candidate["player_id"] = player_id
                            resolved_inputs.append(
                                {
                                    "candidate": candidate,
                                    "quote_at_utc": group["quote_at_utc"],
                                    "over_sid": over_sid,
                                    "under_sid": under_sid,
                                    "over_price": over_price,
                                    "under_price": under_price,
                                }
                            )
        candidate_rows.append(candidate)

    player_identity = {
        "schema_version": "shadow-live-player-identity-v1",
        "resolution_method": "exact_nfkc_casefold_name_within_hard_game_prediction_snapshot",
        "source_name": SOURCE_NAME,
        "source_event_id": expected_event,
        "mlb_game_pk": target.mlb_game_pk,
        "prediction_artifact_sha256": snapshot.prediction_artifact_sha256,
        "fuzzy_matching_used": False,
        "candidate_rows": candidate_rows,
    }
    player_identity_sha = artifact_sha256(player_identity)

    raw_sha = sha256_file(raw_path)
    quotes: list[ResolvedLiveMarketQuote] = []
    for value in resolved_inputs:
        candidate = value["candidate"]
        quotes.append(
            ResolvedLiveMarketQuote(
                source_name=SOURCE_NAME,
                source_event_id=expected_event,
                source_player_id=candidate["source_player_key"],
                source_over_outcome_id=value["over_sid"],
                source_under_outcome_id=value["under_sid"],
                source_event_start_time_utc=expected_source_start,
                source_quote_at_utc=value["quote_at_utc"],
                source_payload_sha256=raw_sha,
                game_identity_artifact_sha256=game_identity_artifact_sha256,
                player_identity_artifact_sha256=player_identity_sha,
                mlb_game_pk=target.mlb_game_pk,
                player_id=int(candidate["player_id"]),
                game_date=target.official_game_date,
                official_start_time_utc=target.official_start_time_utc,
                sportsbook=BOOKMAKER_KEY,
                category="hits",
                line=candidate["line"],
                over_odds_american=value["over_price"],
                under_odds_american=value["under_price"],
            )
        )

    status_counts: dict[str, int] = {}
    for row in candidate_rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    resolved_artifact = {
        "schema_version": "shadow-live-resolved-hits-v1",
        "source_name": SOURCE_NAME,
        "source_payload_sha256": raw_sha,
        "game_identity_artifact_sha256": game_identity_artifact_sha256,
        "player_identity_artifact_sha256": player_identity_sha,
        "game_identity": dict(game_identity),
        "player_identity": player_identity,
        "mlb_game_pk": target.mlb_game_pk,
        "entry_target_at_utc": target.entry_target_at_utc,
        "observation_phase": phase,
        "observation_cutoff_at_utc": cutoff_at,
        "raw_outcome_count": raw_outcomes,
        "invalid_outcome_count": invalid_outcomes,
        "candidate_group_count": len(candidate_rows),
        "status_counts": status_counts,
        "candidate_rows": candidate_rows,
        "rows": [asdict(quote) for quote in quotes],
        "resolved_quote_count": len(quotes),
        "all_candidates_accounted_for": sum(status_counts.values()) == len(candidate_rows),
        "betting_authorized": False,
    }
    if (
        not resolved_artifact["all_candidates_accounted_for"]
        or status_counts.get("resolved", 0) != len(quotes)
    ):
        raise ShadowLiveProviderError("provider resolution funnel does not reconcile")
    return player_identity, resolved_artifact


def validate_resolved_hits_payload(payload: Mapping[str, Any]) -> None:
    """Revalidate an exhaustive live resolver artifact after publication."""

    row = dict(payload)
    if row.get("schema_version") != "shadow-live-resolved-hits-v1":
        raise ShadowLiveProviderError("unknown live resolved Hits schema")
    if row.get("source_name") != SOURCE_NAME or row.get("betting_authorized") is not False:
        raise ShadowLiveProviderError("live resolved artifact changed source/authorization scope")
    game_identity = _object(row.get("game_identity"), "resolved game_identity")
    player_identity = _object(row.get("player_identity"), "resolved player_identity")
    if artifact_sha256(game_identity) != row.get("game_identity_artifact_sha256"):
        raise ShadowLiveProviderError("embedded game identity hash mismatch")
    if artifact_sha256(player_identity) != row.get("player_identity_artifact_sha256"):
        raise ShadowLiveProviderError("embedded player identity hash mismatch")
    candidates = _list(row.get("candidate_rows"), "resolved candidate_rows")
    if int(row.get("candidate_group_count", -1)) != len(candidates):
        raise ShadowLiveProviderError("resolved candidate count does not reconcile")
    counts: dict[str, int] = {}
    resolved_keys: set[tuple[str, str, int]] = set()
    for raw in candidates:
        candidate = _object(raw, "resolved candidate")
        status = str(candidate.get("status", ""))
        if status not in RESOLUTION_STATUSES:
            raise ShadowLiveProviderError(f"unknown resolver status {status!r}")
        counts[status] = counts.get(status, 0) + 1
        if status == "resolved":
            resolved_keys.add((
                str(candidate.get("source_player_key", "")),
                _line(candidate.get("line")),
                int(candidate.get("player_id") or 0),
            ))
    if counts != row.get("status_counts") or sum(counts.values()) != len(candidates):
        raise ShadowLiveProviderError("resolver status funnel does not reconcile")
    quotes = _list(row.get("rows"), "resolved rows")
    if int(row.get("resolved_quote_count", -1)) != len(quotes):
        raise ShadowLiveProviderError("resolved quote count does not reconcile")
    if counts.get("resolved", 0) != len(quotes):
        raise ShadowLiveProviderError("resolved funnel status differs from quote rows")
    quote_keys: set[tuple[str, str, int]] = set()
    quote_hashes: set[str] = set()
    for raw in quotes:
        quote = ResolvedLiveMarketQuote(**_object(raw, "resolved quote"))
        if quote.source_payload_sha256 != row.get("source_payload_sha256"):
            raise ShadowLiveProviderError("quote differs from resolved raw payload hash")
        if quote.game_identity_artifact_sha256 != row.get("game_identity_artifact_sha256"):
            raise ShadowLiveProviderError("quote differs from embedded game identity")
        if quote.player_identity_artifact_sha256 != row.get("player_identity_artifact_sha256"):
            raise ShadowLiveProviderError("quote differs from embedded player identity")
        key = (quote.source_player_id, quote.line, quote.player_id)
        quote_keys.add(key)
        if quote.quote_sha256 in quote_hashes:
            raise ShadowLiveProviderError("resolved artifact repeats quote identity")
        quote_hashes.add(quote.quote_sha256)
    if quote_keys != resolved_keys:
        raise ShadowLiveProviderError("resolved quote rows differ from exhaustive candidate funnel")
