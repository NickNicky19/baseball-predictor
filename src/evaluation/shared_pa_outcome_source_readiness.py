"""Read-only eligibility check for a historical shared-PA outcome source.

The qualified 2023 opportunity release intentionally retained a narrow MLB
feed projection.  This checker prevents that release from being mistaken for
an outcome-complete source merely because its hashes and game coverage pass.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit


SCHEMA_VERSION = "shared-pa-outcome-source-readiness-v1"
REQUIRED_BATTING_FIELDS = (
    "plateAppearances",
    "atBats",
    "hits",
    "doubles",
    "triples",
    "homeRuns",
    "baseOnBalls",
    "intentionalWalks",
    "strikeOuts",
    "hitByPitch",
    "sacFlies",
    "sacBunts",
    "totalBases",
    "runs",
    "rbi",
    "catchersInterference",
)


class OutcomeSourceError(ValueError):
    """The supplied source release is malformed or crosses a protected boundary."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object(path: Path, context: str) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise OutcomeSourceError(f"{context} must be a regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise OutcomeSourceError(f"{context} root must be an object")
    return value


def _official_date(feed: Mapping[str, Any]) -> date:
    raw = feed.get("gameData", {}).get("datetime", {}).get("officialDate")
    if not isinstance(raw, str):
        raise OutcomeSourceError("feed officialDate is missing")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise OutcomeSourceError("feed officialDate is not canonical ISO") from exc
    if parsed.isoformat() != raw:
        raise OutcomeSourceError("feed officialDate is not canonical ISO")
    if parsed.year != 2023:
        raise OutcomeSourceError("outcome source contains a non-2023 feed")
    return parsed


def inspect_release(root: Path) -> dict[str, Any]:
    """Inspect source capability without constructing features or fitting a model."""
    root = root.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise OutcomeSourceError("source root must be a regular directory")
    manifest_path = root / "source-release" / "source_manifest.json"
    projection_path = root / "source-release" / "projection.json"
    manifest = _object(manifest_path, "source manifest")
    projection = _object(projection_path, "source projection")
    feed_paths = sorted((root / "feeds" / "feeds").glob("game-*/response.json"))
    if not feed_paths:
        return {
            "schema_version": SCHEMA_VERSION,
            "decision": "RAW_BYTES_NOT_RETAINED",
            "status": "BLOCKED_RAW_BYTES_NOT_RETAINED",
            "research_only": True,
            "betting_authorized": False,
            "source_root": str(root),
        }

    missing = Counter()
    present_key_sets = Counter()
    player_rows = 0
    complete_rows = 0
    game_pks: set[int] = set()
    dates: set[str] = set()
    all_plays_present = 0
    all_plays_nonempty = 0
    all_plays_total = 0
    live_data_key_sets = Counter()
    request_field_sets = Counter()
    representative_players: dict[str, Any] = {}
    for path in feed_paths:
        feed = _object(path, f"feed {path.parent.name}")
        official = _official_date(feed)
        dates.add(official.isoformat())
        game_pk = feed.get("gamePk")
        if isinstance(game_pk, bool) or not isinstance(game_pk, int) or game_pk <= 0:
            raise OutcomeSourceError("feed gamePk is invalid")
        if game_pk in game_pks:
            raise OutcomeSourceError("feed gamePk is duplicated")
        game_pks.add(game_pk)
        live_data = feed.get("liveData", {})
        if not isinstance(live_data, Mapping):
            raise OutcomeSourceError("feed liveData is malformed")
        live_data_key_sets[tuple(sorted(str(key) for key in live_data))] += 1
        plays = live_data.get("plays")
        if isinstance(plays, Mapping) and "allPlays" in plays:
            all_plays_present += 1
            all_plays = plays.get("allPlays")
            if isinstance(all_plays, list):
                all_plays_total += len(all_plays)
                if all_plays:
                    all_plays_nonempty += 1
        teams = live_data.get("boxscore", {}).get("teams", {})
        if not isinstance(teams, Mapping) or set(teams) != {"away", "home"}:
            raise OutcomeSourceError("feed boxscore team sides are incomplete")
        for side in ("away", "home"):
            players = teams[side].get("players")
            if not isinstance(players, Mapping):
                raise OutcomeSourceError("feed player map is missing")
            for player_key, player in players.items():
                if not isinstance(player, Mapping) or not player.get("battingOrder"):
                    continue
                batting = player.get("stats", {}).get("batting", {})
                if not isinstance(batting, Mapping):
                    batting = {}
                keys = tuple(sorted(str(key) for key in batting))
                present_key_sets[keys] += 1
                player_rows += 1
                row_missing = [field for field in REQUIRED_BATTING_FIELDS if field not in batting]
                missing.update(row_missing)
                if not row_missing:
                    complete_rows += 1
                if side not in representative_players:
                    person = player.get("person") if isinstance(player.get("person"), Mapping) else {}
                    representative_players[side] = {
                        "game_pk": game_pk,
                        "player_key": str(player_key),
                        "player_id": person.get("id"),
                        "batting_order": player.get("battingOrder"),
                        "batting_fields": list(keys),
                    }
        receipt_path = path.with_name("receipt.json")
        receipt = _object(receipt_path, f"receipt {path.parent.name}")
        request = receipt.get("request")
        if not isinstance(request, Mapping) or not isinstance(request.get("full_url"), str):
            raise OutcomeSourceError("retained request identity is missing")
        split = urlsplit(request["full_url"])
        query = parse_qs(split.query, keep_blank_values=True)
        fields = tuple(query.get("fields", []))
        request_field_sets[fields] += 1

    projection_body = projection.get("projection")
    projection_rows = (
        projection_body.get("rows") if isinstance(projection_body, Mapping) else None
    )
    if not isinstance(projection_rows, list):
        raise OutcomeSourceError("source projection rows are missing")
    if complete_rows == player_rows and player_rows > 0:
        decision = "RAW_BYTES_OUTCOME_COMPLETE"
        status = "OUTCOME_COMPLETE_SOURCE_ELIGIBLE_FOR_PANEL_CONSTRUCTION"
    elif all_plays_nonempty == len(feed_paths) and all_plays_total > 0:
        decision = "RAW_BYTES_PLAY_BY_PLAY_DERIVABLE"
        status = "PLAY_BY_PLAY_DERIVATION_REQUIRES_SEMANTIC_VALIDATION"
    else:
        decision = "RAW_BYTES_OUTCOME_INCOMPLETE"
        status = "BLOCKED_SOURCE_LACKS_REQUIRED_PA_OUTCOME_FIELDS"
    return {
        "schema_version": SCHEMA_VERSION,
        "decision": decision,
        "status": status,
        "research_only": True,
        "betting_authorized": False,
        "model_fitting_performed": False,
        "prediction_generation_performed": False,
        "source_root": str(root),
        "source_identity": {
            "manifest_sha256": sha256_file(manifest_path),
            "projection_sha256": sha256_file(projection_path),
            "manifest_schema_version": manifest.get("schema_version"),
        },
        "coverage": {
            "retained_feed_files": len(feed_paths),
            "distinct_game_pks": len(game_pks),
            "projection_rows": len(projection_rows),
            "batting_order_player_rows": player_rows,
            "outcome_complete_player_rows": complete_rows,
            "feeds_with_all_plays_key": all_plays_present,
            "feeds_with_nonempty_all_plays": all_plays_nonempty,
            "retained_all_plays_count": all_plays_total,
            "date_min": min(dates),
            "date_max": max(dates),
        },
        "required_batting_fields": list(REQUIRED_BATTING_FIELDS),
        "missing_field_occurrences": dict(sorted(missing.items())),
        "observed_batting_key_sets": [
            {"keys": list(keys), "rows": count}
            for keys, count in sorted(present_key_sets.items())
        ],
        "observed_live_data_key_sets": [
            {"keys": list(keys), "feeds": count}
            for keys, count in sorted(live_data_key_sets.items())
        ],
        "retained_request_field_sets": [
            {"fields_query_values": list(keys), "feeds": count}
            for keys, count in sorted(request_field_sets.items())
        ],
        "representative_players": representative_players,
        "eligibility": {
            "opportunity_model_input": True,
            "shared_pa_outcome_target": status.startswith("OUTCOME_COMPLETE"),
            "time_safe_outcome_history": status.startswith("OUTCOME_COMPLETE"),
            "c0_fit": status.startswith("OUTCOME_COMPLETE"),
            "feature_tournament": False,
            "limitation": (
                "The release is qualified for PA opportunity, but a shared-PA outcome "
                "panel additionally requires complete outcome targets and lawful "
                "point-in-time feature histories."
            ),
        },
        "protected_boundaries": {
            "years_opened": [2023],
            "may_2026_opened": False,
            "selection_2024_opened": False,
            "spent_2025_hr_opened": False,
            "economic_evidence_opened": False,
        },
    }
