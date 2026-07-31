"""Deterministic 2023 shared-PA outcome-label release builder.

The builder is deliberately offline.  It accepts only the already certified
official MLB boxscore capture and turns its retained raw response bytes into
reconciled player-game labels.  It never fetches a URL, constructs point in
time features, fits a model, or emits a prediction.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping
from urllib.parse import urlsplit


RELEASE_SCHEMA = "shared-pa-official-2023-outcome-label-release-v1"
ROW_SCHEMA = "shared-pa-official-2023-outcome-label-row-v1"
CAPTURE_SCHEMA = "shared-pa-outcome-official-boxscore-capture-v1"
CAPTURE_STATUS = "COMPLETE_IMMUTABLE_OFFICIAL_2023_FEED_CAPTURE"
EXPECTED_GAME_COUNT = 2430
EXPECTED_CAPTURE_DIGEST = "09d6d3cf04dd6bc145cf000ce0ca1c5eb30d349393c866d22eda1b77b5c4ddb7"
EXPECTED_SCHEDULE_DIGEST = "0f3d68d002f5fa87b0d6cb9daa4d51606f37547bbca0a28bce066e6e7c09c96f"
ENDPOINT_RE = re.compile(r"^/api/v1/game/([1-9][0-9]*)/boxscore$")
SIDES = ("away", "home")

_BATTING_FIELDS = {
    "plate_appearances": "plateAppearances",
    "at_bats": "atBats",
    "hits": "hits",
    "doubles": "doubles",
    "triples": "triples",
    "home_runs": "homeRuns",
    "base_on_balls": "baseOnBalls",
    "intentional_walks": "intentionalWalks",
    "strikeouts": "strikeOuts",
    "hit_by_pitch": "hitByPitch",
    "sac_flies": "sacFlies",
    "sac_bunts": "sacBunts",
    "total_bases": "totalBases",
    "runs": "runs",
    "rbi": "rbi",
    "catcher_interference": "catchersInterference",
}


class OutcomeLabelReleaseError(ValueError):
    """The captured evidence cannot safely form a label release."""


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(path: Path, label: str) -> Path:
    resolved = Path(os.path.abspath(os.fspath(path)))
    if not resolved.is_file() or resolved.is_symlink():
        raise OutcomeLabelReleaseError(f"{label} must be a regular file")
    return resolved


def _safe_directory(path: Path, label: str) -> Path:
    resolved = Path(os.path.abspath(os.fspath(path)))
    if not resolved.is_dir() or resolved.is_symlink():
        raise OutcomeLabelReleaseError(f"{label} must be a regular directory")
    return resolved


def _safe_new_directory(path: Path) -> Path:
    candidate = Path(os.path.abspath(os.fspath(path)))
    if candidate.exists() or candidate.parent == candidate:
        raise OutcomeLabelReleaseError("release output already exists or is unsafe")
    cursor = candidate.parent
    while not cursor.exists():
        cursor = cursor.parent
    while True:
        if cursor.is_symlink():
            raise OutcomeLabelReleaseError("release output traverses a symlink")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return candidate


def _load_object(path: Path, label: str) -> Mapping[str, Any]:
    source = _safe_file(path, label)
    try:
        value = json.loads(source.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OutcomeLabelReleaseError(f"{label} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise OutcomeLabelReleaseError(f"{label} must be a JSON object")
    return value


def _nonnegative_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OutcomeLabelReleaseError(f"{label} must be a nonnegative integer")
    return value


def _positive_integer(value: Any, label: str) -> int:
    parsed = _nonnegative_integer(value, label)
    if parsed <= 0:
        raise OutcomeLabelReleaseError(f"{label} must be positive")
    return parsed


def _official_date(value: Any) -> str:
    if not isinstance(value, str):
        raise OutcomeLabelReleaseError("official date is missing")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise OutcomeLabelReleaseError("official date is not canonical ISO") from exc
    if parsed.isoformat() != value or parsed.year != 2023:
        raise OutcomeLabelReleaseError("official date is outside canonical 2023")
    return value


def _load_capture(capture_root: Path, expected_capture_digest: str) -> tuple[dict[str, Any], dict[int, Mapping[str, Any]]]:
    root = _safe_directory(capture_root, "capture root")
    manifest_path = root / "capture" / "manifest.json"
    manifest = _load_object(manifest_path, "capture manifest")
    if (
        manifest.get("schema_version") != CAPTURE_SCHEMA
        or manifest.get("status") != CAPTURE_STATUS
        or manifest.get("season") != 2023
        or manifest.get("research_only") is not True
        or manifest.get("betting_authorized") is not False
        or manifest.get("observed_capture_digest") != expected_capture_digest
        or manifest.get("schedule_capture_digest") != EXPECTED_SCHEDULE_DIGEST
    ):
        raise OutcomeLabelReleaseError("capture manifest identity differs")
    protected = manifest.get("protected_data")
    if not isinstance(protected, Mapping) or any(bool(value) for value in protected.values()):
        raise OutcomeLabelReleaseError("capture manifest crosses a protected boundary")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or len(entries) != EXPECTED_GAME_COUNT:
        raise OutcomeLabelReleaseError("capture manifest game count differs")
    plan_path = root / "capture" / "plan.json"
    if sha256_file(_safe_file(plan_path, "capture plan")) != manifest.get("plan_sha256"):
        raise OutcomeLabelReleaseError("capture plan hash differs")
    plan = _load_object(plan_path, "capture plan")
    requests = plan.get("requests")
    if not isinstance(requests, list) or len(requests) != EXPECTED_GAME_COUNT:
        raise OutcomeLabelReleaseError("capture request plan game count differs")
    requests_by_id: dict[str, Mapping[str, Any]] = {}
    for request in requests:
        if not isinstance(request, Mapping):
            raise OutcomeLabelReleaseError("capture request is malformed")
        request_id = request.get("request_id")
        expected = request.get("expected")
        if not isinstance(request_id, str) or not isinstance(expected, Mapping):
            raise OutcomeLabelReleaseError("capture request identity is malformed")
        game_pk = _positive_integer(expected.get("game_pk"), "request game_pk")
        if request_id != f"game-{game_pk}" or request_id in requests_by_id:
            raise OutcomeLabelReleaseError("capture request identity is duplicated")
        if _official_date(expected.get("official_date")) is None:
            raise OutcomeLabelReleaseError("unreachable date guard")
        _positive_integer(expected.get("away_team_id"), "request away team")
        _positive_integer(expected.get("home_team_id"), "request home team")
        if expected["away_team_id"] == expected["home_team_id"]:
            raise OutcomeLabelReleaseError("capture request has identical teams")
        requests_by_id[request_id] = request
    entries_by_id: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise OutcomeLabelReleaseError("capture manifest entry is malformed")
        request_id = entry.get("request_id")
        if request_id not in requests_by_id or request_id in entries_by_id:
            raise OutcomeLabelReleaseError("capture manifest request coverage differs")
        for field in ("response_sha256", "receipt_sha256"):
            value = entry.get(field)
            if not isinstance(value, str) or len(value) != 64:
                raise OutcomeLabelReleaseError("capture manifest hash is malformed")
        entries_by_id[request_id] = entry
    if set(entries_by_id) != set(requests_by_id):
        raise OutcomeLabelReleaseError("capture manifest and plan request sets differ")
    return dict(manifest), {int(item["expected"]["game_pk"]): item for item in requests_by_id.values()}


def _source_receipt(feed_dir: Path, request: Mapping[str, Any], entry: Mapping[str, Any]) -> tuple[bytes, str, str]:
    response_path = _safe_file(feed_dir / "response.json", "captured response")
    receipt_path = _safe_file(feed_dir / "receipt.json", "captured receipt")
    response = response_path.read_bytes()
    response_sha = sha256_bytes(response)
    receipt_sha = sha256_file(receipt_path)
    if response_sha != entry["response_sha256"] or receipt_sha != entry["receipt_sha256"]:
        raise OutcomeLabelReleaseError("captured response or receipt hash differs")
    receipt = _load_object(receipt_path, "captured receipt")
    request_identity = receipt.get("request")
    if not isinstance(request_identity, Mapping):
        raise OutcomeLabelReleaseError("captured receipt request is absent")
    url = request_identity.get("full_url")
    if not isinstance(url, str):
        raise OutcomeLabelReleaseError("captured receipt URL is absent")
    split = urlsplit(url)
    expected_game_pk = request["expected"]["game_pk"]
    match = ENDPOINT_RE.fullmatch(split.path)
    if (
        split.scheme != "https"
        or split.netloc != "statsapi.mlb.com"
        or split.query
        or match is None
        or int(match.group(1)) != expected_game_pk
    ):
        raise OutcomeLabelReleaseError("captured receipt endpoint differs")
    return response, response_sha, receipt_sha


def _batting_row(*, player: Mapping[str, Any], game_pk: int, official_date: str, side: str, team_id: int, response_sha: str, receipt_sha: str) -> dict[str, Any]:
    person = player.get("person")
    if not isinstance(person, Mapping):
        raise OutcomeLabelReleaseError("boxscore player identity is missing")
    player_id = _positive_integer(person.get("id"), "player ID")
    order = player.get("battingOrder")
    if isinstance(order, int):
        batting_order = order
    elif isinstance(order, str) and order.isdigit():
        batting_order = int(order)
    else:
        raise OutcomeLabelReleaseError("boxscore batting order is malformed")
    if batting_order < 100 or batting_order > 999:
        raise OutcomeLabelReleaseError("boxscore batting order is outside slots 1 through 9")
    lineup_slot = batting_order // 100
    if lineup_slot not in range(1, 10):
        raise OutcomeLabelReleaseError("boxscore batting slot is invalid")
    starter = batting_order % 100 == 0
    stats = player.get("stats")
    batting = stats.get("batting") if isinstance(stats, Mapping) else None
    if not isinstance(batting, Mapping):
        raise OutcomeLabelReleaseError("boxscore batting stats are missing")
    row = {
        "schema_version": ROW_SCHEMA,
        "game_pk": game_pk,
        "official_date": official_date,
        "team_side": side,
        "team_id": team_id,
        "player_id": player_id,
        "starter": starter,
        "lineup_slot": lineup_slot,
        **{
            internal: _nonnegative_integer(batting.get(external), f"player {internal}")
            for internal, external in _BATTING_FIELDS.items()
        },
        "source_response_sha256": response_sha,
        "source_receipt_sha256": receipt_sha,
    }
    row["singles"] = row["hits"] - row["doubles"] - row["triples"] - row["home_runs"]
    row["non_intentional_walks"] = row["base_on_balls"] - row["intentional_walks"]
    row["non_hit_non_strikeout_at_bats"] = row["at_bats"] - row["hits"] - row["strikeouts"]
    row["other_official_pa"] = row["plate_appearances"] - (
        row["at_bats"]
        + row["base_on_balls"]
        + row["hit_by_pitch"]
        + row["sac_flies"]
        + row["sac_bunts"]
        + row["catcher_interference"]
    )
    if any(row[key] < 0 for key in ("singles", "non_intentional_walks", "non_hit_non_strikeout_at_bats", "other_official_pa")):
        raise OutcomeLabelReleaseError("boxscore batting totals cannot form nonnegative PA categories")
    categories = (
        row["strikeouts"], row["non_intentional_walks"], row["intentional_walks"],
        row["hit_by_pitch"], row["home_runs"], row["singles"], row["doubles"],
        row["triples"], row["non_hit_non_strikeout_at_bats"], row["sac_flies"],
        row["sac_bunts"], row["catcher_interference"], row["other_official_pa"],
    )
    if sum(categories) != row["plate_appearances"]:
        raise OutcomeLabelReleaseError("PA categories do not reconcile")
    if row["total_bases"] != row["singles"] + 2 * row["doubles"] + 3 * row["triples"] + 4 * row["home_runs"]:
        raise OutcomeLabelReleaseError("player total bases do not reconcile")
    return row


def _has_batting_order(player: Mapping[str, Any]) -> bool:
    """Return true only for a player with an official numbered batting slot.

    MLB's boxscore ``batters`` array also includes pitchers, whose batting
    structures are empty and whose ``battingOrder`` is null.  The numbered
    player map is the authoritative batting-line selector.
    """
    order = player.get("battingOrder")
    return isinstance(order, int) or (isinstance(order, str) and order.isdigit())


def _rows_for_side(*, response: Mapping[str, Any], request: Mapping[str, Any], side: str, response_sha: str, receipt_sha: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected = request["expected"]
    game_pk = _positive_integer(expected["game_pk"], "request game_pk")
    official = _official_date(expected["official_date"])
    teams = response.get("teams")
    if not isinstance(teams, Mapping) or set(teams) != set(SIDES):
        raise OutcomeLabelReleaseError("boxscore teams are incomplete")
    team = teams.get(side)
    if not isinstance(team, Mapping):
        raise OutcomeLabelReleaseError("boxscore team side is malformed")
    expected_team = expected[f"{side}_team_id"]
    team_identity = team.get("team")
    if not isinstance(team_identity, Mapping) or team_identity.get("id") != expected_team:
        raise OutcomeLabelReleaseError("boxscore team identity differs from plan")
    players = team.get("players")
    batters = team.get("batters")
    if not isinstance(players, Mapping) or not isinstance(batters, list) or not batters:
        raise OutcomeLabelReleaseError("boxscore batter map is missing")
    if len(batters) != len(set(batters)):
        raise OutcomeLabelReleaseError("boxscore batter list is duplicated")
    batter_ids = {_positive_integer(item, "boxscore batter ID") for item in batters}
    numbered_players: list[tuple[int, Mapping[str, Any]]] = []
    for item in players.values():
        if not isinstance(item, Mapping):
            raise OutcomeLabelReleaseError("boxscore player map is malformed")
        if _has_batting_order(item):
            person = item.get("person")
            player_id = _positive_integer(
                person.get("id") if isinstance(person, Mapping) else None,
                "mapped batting player ID",
            )
            numbered_players.append((player_id, item))
        else:
            stats = item.get("stats")
            batting = stats.get("batting") if isinstance(stats, Mapping) else None
            if isinstance(batting, Mapping):
                nonzero = [
                    external for external in _BATTING_FIELDS.values()
                    if batting.get(external) not in (None, 0)
                ]
                if nonzero:
                    person = item.get("person")
                    player_id = person.get("id") if isinstance(person, Mapping) else None
                    raise OutcomeLabelReleaseError(
                        "unnumbered player has batting statistics: "
                        f"game={game_pk} side={side} player={player_id} fields={nonzero}"
                    )
    if not numbered_players:
        raise OutcomeLabelReleaseError("boxscore has no numbered batting players")
    if len({player_id for player_id, _ in numbered_players}) != len(numbered_players):
        raise OutcomeLabelReleaseError("boxscore numbered batting players are duplicated")
    if not {player_id for player_id, _ in numbered_players} <= batter_ids:
        raise OutcomeLabelReleaseError("numbered batting player is absent from batter list")
    rows: list[dict[str, Any]] = []
    for player_id, player in sorted(numbered_players):
        row = _batting_row(
            player=player, game_pk=game_pk, official_date=official, side=side,
            team_id=expected_team, response_sha=response_sha, receipt_sha=receipt_sha,
        )
        if row["player_id"] != player_id:
            raise OutcomeLabelReleaseError("boxscore batter identity contradicts player map")
        rows.append(row)
    all_batting_order_ids = {player_id for player_id, _ in numbered_players}
    if all_batting_order_ids != {row["player_id"] for row in rows}:
        raise OutcomeLabelReleaseError("boxscore batting-order map contradicts batter list")
    starters = [row for row in rows if row["starter"]]
    if len(starters) != 9 or {row["lineup_slot"] for row in starters} != set(range(1, 10)):
        raise OutcomeLabelReleaseError("boxscore does not retain nine unique original starters")
    batting_total = team.get("teamStats", {}).get("batting")
    if not isinstance(batting_total, Mapping):
        raise OutcomeLabelReleaseError("boxscore team batting total is missing")
    team_totals = {
        internal: _nonnegative_integer(batting_total.get(external), f"team {internal}")
        for internal, external in _BATTING_FIELDS.items()
    }
    for field, expected_total in team_totals.items():
        observed_total = sum(row[field] for row in rows)
        if observed_total != expected_total:
            raise OutcomeLabelReleaseError(f"team reconciliation differs for {field}")
    score = team.get("score")
    if score is None:
        # The authorized `/boxscore` representation supplies the official team
        # batting-runs total but not a separate linescore/final-score field.
        # That is sufficient for the five C0 PA markets, while Runs/RBI remain
        # labels for a later run-context phase rather than a qualified model.
        final_score_state = "OFFICIAL_TEAM_BATTING_RUNS_ONLY"
    else:
        final_score = _nonnegative_integer(score, "team final score")
        if sum(row["runs"] for row in rows) != final_score:
            raise OutcomeLabelReleaseError("team runs do not reconcile to final score")
        final_score_state = "INDEPENDENT_FINAL_SCORE_RECONCILED"
    return rows, {**team_totals, "final_score_state": final_score_state}


def representative_preflight(capture_root: Path, *, expected_capture_digest: str = EXPECTED_CAPTURE_DIGEST) -> dict[str, Any]:
    """Exercise the full parser against first/middle/last captured games only."""
    manifest, requests = _load_capture(capture_root, expected_capture_digest)
    game_pks = sorted(requests)
    selected = (game_pks[0], game_pks[len(game_pks) // 2], game_pks[-1])
    rows = 0
    for game_pk in selected:
        request = requests[game_pk]
        feed_dir = Path(capture_root) / "capture" / "feeds" / request["request_id"]
        entry = next(item for item in manifest["entries"] if item["request_id"] == request["request_id"])
        raw, response_sha, receipt_sha = _source_receipt(feed_dir, request, entry)
        try:
            response = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OutcomeLabelReleaseError("captured boxscore is not valid JSON") from exc
        if not isinstance(response, Mapping):
            raise OutcomeLabelReleaseError("captured boxscore root is malformed")
        for side in SIDES:
            side_rows, _ = _rows_for_side(
                response=response, request=request, side=side,
                response_sha=response_sha, receipt_sha=receipt_sha,
            )
            rows += len(side_rows)
    return {
        "schema_version": "shared-pa-outcome-label-preflight-v1",
        "status": "REPRESENTATIVE_DOWNSTREAM_PREFLIGHT_PASSED",
        "capture_digest": expected_capture_digest,
        "representative_game_pks": list(selected),
        "parsed_player_rows": rows,
        "research_only": True,
        "model_fitting_performed": False,
        "prediction_generation_performed": False,
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_release(*, capture_root: Path, output_dir: Path, expected_capture_digest: str = EXPECTED_CAPTURE_DIGEST) -> dict[str, Any]:
    """Build a new immutable label release from the certified raw boxscores."""
    manifest, requests = _load_capture(capture_root, expected_capture_digest)
    destination = _safe_new_directory(output_dir)
    rows: list[dict[str, Any]] = []
    raw_inputs: list[dict[str, Any]] = []
    team_summaries: list[dict[str, Any]] = []
    for game_pk in sorted(requests):
        request = requests[game_pk]
        feed_dir = Path(capture_root) / "capture" / "feeds" / request["request_id"]
        entry = next(item for item in manifest["entries"] if item["request_id"] == request["request_id"])
        raw, response_sha, receipt_sha = _source_receipt(feed_dir, request, entry)
        try:
            response = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OutcomeLabelReleaseError("captured boxscore is not valid JSON") from exc
        if not isinstance(response, Mapping):
            raise OutcomeLabelReleaseError("captured boxscore root is malformed")
        raw_inputs.append({
            "game_pk": game_pk,
            "request_id": request["request_id"],
            "official_date": request["expected"]["official_date"],
            "response_sha256": response_sha,
            "receipt_sha256": receipt_sha,
        })
        for side in SIDES:
            side_rows, totals = _rows_for_side(
                response=response, request=request, side=side,
                response_sha=response_sha, receipt_sha=receipt_sha,
            )
            rows.extend(side_rows)
            team_summaries.append({
                "game_pk": game_pk,
                "team_side": side,
                "team_id": request["expected"][f"{side}_team_id"],
                "player_rows": len(side_rows),
                "original_starters": sum(row["starter"] for row in side_rows),
                "zero_pa_original_starters": sum(row["starter"] and row["plate_appearances"] == 0 for row in side_rows),
                "final_score_state": totals.pop("final_score_state"),
                "batting_totals": totals,
            })
    rows.sort(key=lambda row: (row["official_date"], row["game_pk"], row["team_side"], row["player_id"]))
    identities = [(row["game_pk"], row["team_side"], row["team_id"], row["player_id"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise OutcomeLabelReleaseError("label release contains duplicate hard player identities")
    release_dir = destination
    release_dir.mkdir(parents=False)
    rows_path = release_dir / "canonical_rows.jsonl"
    rows_bytes = b"".join(canonical_json_bytes(row) for row in rows)
    _atomic_write(rows_path, rows_bytes)
    raw_path = release_dir / "raw_input_manifest.json"
    raw_payload = {
        "schema_version": "shared-pa-outcome-label-raw-input-manifest-v1",
        "capture_digest": expected_capture_digest,
        "capture_manifest_sha256": sha256_file(Path(capture_root) / "capture" / "manifest.json"),
        "entries": raw_inputs,
    }
    _atomic_write(raw_path, canonical_json_bytes(raw_payload))
    coverage_path = release_dir / "coverage.json"
    coverage = {
        "schema_version": "shared-pa-outcome-label-coverage-v1",
        "game_count": len(requests),
        "team_side_count": len(team_summaries),
        "player_game_rows": len(rows),
        "original_starter_rows": sum(row["starter"] for row in rows),
        "zero_pa_original_starter_rows": sum(row["starter"] and row["plate_appearances"] == 0 for row in rows),
        "date_min": min(row["official_date"] for row in rows),
        "date_max": max(row["official_date"] for row in rows),
        "outcome_totals": {
            key: sum(row[key] for row in rows)
            for key in (
                "plate_appearances", "at_bats", "hits", "singles", "doubles", "triples",
                "home_runs", "base_on_balls", "intentional_walks", "strikeouts", "hit_by_pitch",
                "sac_flies", "sac_bunts", "total_bases", "runs", "rbi", "catcher_interference",
                "other_official_pa",
            )
        },
    }
    _atomic_write(coverage_path, canonical_json_bytes(coverage))
    reconciliation_path = release_dir / "reconciliation.json"
    reconciliation = {
        "schema_version": "shared-pa-outcome-label-reconciliation-v1",
        "status": "ALL_PLAYER_TEAM_AND_GAME_RECONCILIATIONS_PASSED",
        "team_side_summaries": team_summaries,
        "reconciliation_failures": [],
    }
    _atomic_write(reconciliation_path, canonical_json_bytes(reconciliation))
    schema_path = Path(__file__).resolve().parents[2] / "config" / "shared_pa_outcome_label_release_v1.schema.json"
    files = [rows_path, raw_path, coverage_path, reconciliation_path]
    file_manifest = [
        {"path": item.name, "size": item.stat().st_size, "sha256": sha256_file(item)}
        for item in files
    ]
    unsigned = {
        "schema_version": RELEASE_SCHEMA,
        "status": "COMPLETE_DETERMINISTIC_OUTCOME_LABEL_RELEASE",
        "research_only": True,
        "betting_authorized": False,
        "model_fitting_performed": False,
        "prediction_generation_performed": False,
        "capture_digest": expected_capture_digest,
        "capture_manifest_sha256": sha256_file(Path(capture_root) / "capture" / "manifest.json"),
        "schedule_capture_digest": EXPECTED_SCHEDULE_DIGEST,
        "row_schema_sha256": sha256_file(schema_path),
        "parser_sha256": sha256_file(Path(__file__)),
        "coverage": {
            "game_count": coverage["game_count"],
            "team_side_count": coverage["team_side_count"],
            "player_game_rows": coverage["player_game_rows"],
            "original_starter_rows": coverage["original_starter_rows"],
            "zero_pa_original_starter_rows": coverage["zero_pa_original_starter_rows"],
        },
        "protected_boundaries": {
            "years_opened": [2023],
            "may_2026_opened": False,
            "selection_2024_opened": False,
            "spent_2025_hr_opened": False,
            "economic_evidence_opened": False,
        },
        "limitations": {
            "independent_game_linescore": "not present in the authorized boxscore bytes; official team batting-runs totals reconcile player runs",
            "qualified_markets": ["hits", "home_runs", "total_bases", "hitter_strikeouts", "hitter_walks"],
            "deferred_markets": ["rbi", "hrr", "pitcher_earned_runs"],
        },
        "files": file_manifest,
    }
    release_manifest = {**unsigned, "release_sha256": sha256_bytes(canonical_json_bytes(unsigned))}
    _atomic_write(release_dir / "release_manifest.json", canonical_json_bytes(release_manifest))
    return release_manifest


def verify_release(release_dir: Path, *, expected_release_sha256: str | None = None) -> dict[str, Any]:
    """Independently replay output equations and immutable file bindings."""
    root = _safe_directory(release_dir, "label release")
    manifest = _load_object(root / "release_manifest.json", "label release manifest")
    supplied = manifest.get("release_sha256")
    unsigned = dict(manifest)
    unsigned.pop("release_sha256", None)
    observed = sha256_bytes(canonical_json_bytes(unsigned))
    if supplied != observed or (expected_release_sha256 and observed != expected_release_sha256):
        raise OutcomeLabelReleaseError("label release manifest identity differs")
    if manifest.get("schema_version") != RELEASE_SCHEMA or manifest.get("status") != "COMPLETE_DETERMINISTIC_OUTCOME_LABEL_RELEASE":
        raise OutcomeLabelReleaseError("label release schema or status differs")
    for item in manifest.get("files", []):
        if not isinstance(item, Mapping):
            raise OutcomeLabelReleaseError("label release file manifest is malformed")
        path = _safe_file(root / str(item.get("path")), "label release file")
        if path.name != item.get("path") or path.stat().st_size != item.get("size") or sha256_file(path) != item.get("sha256"):
            raise OutcomeLabelReleaseError("label release file binding differs")
    rows_path = root / "canonical_rows.jsonl"
    rows: list[Mapping[str, Any]] = []
    for line in _safe_file(rows_path, "label release rows").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if not isinstance(row, Mapping) or row.get("schema_version") != ROW_SCHEMA:
            raise OutcomeLabelReleaseError("label row schema differs")
        values = tuple(row.get(key) for key in ("game_pk", "team_side", "team_id", "player_id"))
        if any(value is None for value in values):
            raise OutcomeLabelReleaseError("label row hard identity is missing")
        categories = (
            row["strikeouts"], row["non_intentional_walks"], row["intentional_walks"],
            row["hit_by_pitch"], row["home_runs"], row["singles"], row["doubles"],
            row["triples"], row["non_hit_non_strikeout_at_bats"], row["sac_flies"],
            row["sac_bunts"], row["catcher_interference"], row["other_official_pa"],
        )
        if sum(categories) != row["plate_appearances"]:
            raise OutcomeLabelReleaseError("label row PA partition differs")
        if row["hits"] != row["singles"] + row["doubles"] + row["triples"] + row["home_runs"]:
            raise OutcomeLabelReleaseError("label row hit reconciliation differs")
        if row["total_bases"] != row["singles"] + 2 * row["doubles"] + 3 * row["triples"] + 4 * row["home_runs"]:
            raise OutcomeLabelReleaseError("label row total-base reconciliation differs")
        rows.append(row)
    identities = [(row["game_pk"], row["team_side"], row["team_id"], row["player_id"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise OutcomeLabelReleaseError("label release duplicate hard identity")
    if len(rows) != manifest["coverage"]["player_game_rows"]:
        raise OutcomeLabelReleaseError("label release row coverage differs")
    return {
        "schema_version": "shared-pa-outcome-label-release-verification-v1",
        "status": "OUTCOME_LABEL_RELEASE_VERIFIED",
        "release_sha256": observed,
        "player_game_rows": len(rows),
        "research_only": True,
        "model_fitting_performed": False,
        "prediction_generation_performed": False,
    }
