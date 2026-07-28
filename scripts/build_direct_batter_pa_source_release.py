#!/usr/bin/env python3
"""Build and externally verify an immutable 2023 historical source release.

This is a source-capture boundary only.  It does not build features, fit or
score a model, inspect protected periods, or create prospective evidence.
Network use is terminally disabled while transitive TLS/shared-library authority
is unproven. Tests supply a closed mapping of offline response fixtures.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import functools
import hashlib
import importlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import site
import ssl
import stat
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "config" / "direct_batter_pa_source_release_v1.json"
SCHEMA_PLAN = "direct-batter-pa-source-request-plan-v1"
SCHEMA_LOCK = "direct-batter-pa-source-runtime-lock-v1"
SCHEMA_MANIFEST = "direct-batter-pa-source-release-manifest-v1"
SCHEMA_RECEIPT = "direct-batter-pa-transport-receipt-v1"
SCHEMA_VERIFICATION = "direct-batter-pa-source-release-external-verification-v1"
SCHEMA_SCHEDULE_MANIFEST = "official-mlb-2023-schedule-receipt-manifest-v1"
SCHEMA_IDENTITY_INDEX = "direct-batter-pa-2023-identity-index-v1"
SCHEMA_ZERO_PA_MAP = "direct-batter-pa-existing-zero-pa-identity-map-v1"
EVIDENCE_CLASS = "HISTORICAL_2023_SOURCE_RECONSTRUCTION_ONLY_NONPROSPECTIVE"
AUTHORIZATION = "RESEARCH_ONLY_NO_BETTING"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,119}\Z")
YEAR_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([12]\d{3})(?![A-Za-z0-9])")
COMPACT_DATE_RE = re.compile(
    r"(?<![A-Za-z0-9])([12]\d{3})(0[1-9]|1[0-2])([0-2]\d|3[01])(?![A-Za-z0-9])"
)
MONTH_YEAR_RE = re.compile(
    r"(?i)(?:may[ _./-]*2026|2026[ _./-]*may|05[ _./-]+2026|2026[ _./-]+05)"
)
STATCAST_REQUIRED_COLUMNS = (
    "game_date", "game_type", "batter", "pitcher", "game_pk", "at_bat_number", "pitch_number",
)
RUNTIME_MODULES = (
    "argparse", "csv", "dataclasses", "datetime", "hashlib", "importlib", "json",
    "os", "pathlib", "platform", "re", "shutil", "site", "ssl", "stat", "sys",
    "tempfile", "time", "typing", "urllib.parse",
)
INDEX_FIELDS = ["game_pk", "official_date", "game_type", "away_team_id", "home_team_id"]
ZERO_PA_FIELDS = ["game_pk", "official_date", "player_id", "source_response_sha256"]
MANIFEST_KEYS = {
    "schema_version", "status", "authority_status", "evidence_class", "authorization",
    "season", "research_only", "betting_authorized", "prospective_evidence_claimed",
    "model_fitting_permitted", "network_fetch_permitted", "protected_data",
    "source_counts", "identity_games", "statcast_games_observed",
    "zero_pa_targets_preserved", "bindings", "files", "observed_release_digest",
    "external_expected_release_digest",
}
PROTECTED_STATE = {
    "may_2026_opened": False,
    "selection_2024_opened": False,
    "spent_hr_confirmation_2025_opened": False,
    "prices_opened": False,
    "prospective_evidence_opened": False,
}


class SourceReleaseError(ValueError):
    """A fail-closed historical source-release boundary was violated."""


@dataclass(frozen=True)
class TransportResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    final_url: str
    requested_at_utc: str
    observed_at_utc: str


@dataclass(frozen=True)
class OfflineFixtureTransport:
    responses: Mapping[str, TransportResponse]


@dataclass(frozen=True)
class AuthorityBundle:
    schedule_manifest: dict[str, Any]
    identity_index: dict[str, Any]
    zero_pa_map: dict[str, Any]
    games: tuple[dict[str, Any], ...]
    zero_targets: tuple[dict[str, Any], ...]
    schedule_raw_files: tuple[tuple[str, bytes], ...]
    schedule_manifest_bytes: bytes
    identity_index_bytes: bytes
    zero_pa_map_bytes: bytes
    schedule_manifest_sha256: str
    identity_index_sha256: str
    zero_pa_map_sha256: str


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_reparse_point(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _lexical_absolute(path: Path) -> Path:
    if ".." in path.parts:
        raise SourceReleaseError("caller path contains parent traversal")
    return Path(os.path.abspath(os.fspath(path)))


def _guard_existing_ancestors(path: Path, context: str) -> Path:
    lexical = _lexical_absolute(path)
    chain: list[Path] = []
    current = lexical
    while True:
        if current.exists() or current.is_symlink():
            chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    for entry in reversed(chain):
        if _has_reparse_point(entry):
            raise SourceReleaseError(f"{context} traverses a symlink, junction, or reparse point")
    assert_2023_only_text(lexical.as_posix(), f"{context} resolved path")
    return lexical


def _guard_input_file(path: Path, context: str) -> Path:
    lexical = _guard_existing_ancestors(path, context)
    if not lexical.is_file() or _has_reparse_point(lexical):
        raise SourceReleaseError(f"{context} is missing or not a safe regular file")
    resolved = lexical.resolve(strict=True)
    if resolved != lexical:
        raise SourceReleaseError(f"{context} resolves through an alias")
    return resolved


def _guard_input_dir(path: Path, context: str) -> Path:
    lexical = _guard_existing_ancestors(path, context)
    if not lexical.is_dir() or _has_reparse_point(lexical):
        raise SourceReleaseError(f"{context} is missing or not a safe directory")
    resolved = lexical.resolve(strict=True)
    if resolved != lexical:
        raise SourceReleaseError(f"{context} resolves through an alias")
    return resolved


def _guard_output_path(path: Path, context: str) -> Path:
    lexical = _guard_existing_ancestors(path, context)
    if lexical.parent == lexical:
        raise SourceReleaseError(f"{context} cannot be a filesystem root")
    current = lexical.parent
    while not current.exists():
        if current.parent == current:
            raise SourceReleaseError(f"{context} has no safe existing parent")
        current = current.parent
    _guard_input_dir(current, f"{context} existing parent")
    return lexical


def _guard_child(root: Path, relative: str, context: str, *, require_file: bool = True) -> Path:
    root_safe = _guard_input_dir(root, f"{context} root")
    rel = _safe_relative(relative, context)
    candidate = _guard_existing_ancestors(root_safe / rel, context)
    try:
        candidate.relative_to(root_safe)
    except ValueError as exc:
        raise SourceReleaseError(f"{context} escapes its declared root") from exc
    if require_file and (not candidate.is_file() or _has_reparse_point(candidate)):
        raise SourceReleaseError(f"{context} is missing or not a safe regular file")
    return candidate


def _strict_keys(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    if set(value) != expected:
        raise SourceReleaseError(f"{context} has missing or unexpected fields")


def _canonical_utc(value: Any, context: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SourceReleaseError(f"{context} must be canonical UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SourceReleaseError(f"{context} is not a valid timestamp") from exc
    if parsed.tzinfo != timezone.utc or parsed.isoformat().replace("+00:00", "Z") != value:
        raise SourceReleaseError(f"{context} is not normalized canonical UTC")
    return parsed


def _canonical_date_2023(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise SourceReleaseError(f"{context} must be a canonical ISO date")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SourceReleaseError(f"{context} must be a canonical ISO date") from exc
    if parsed.isoformat() != value or parsed.year != 2023:
        raise SourceReleaseError(f"{context} must be in 2023")
    return value


def assert_2023_only_text(value: str, context: str) -> None:
    """Reject standalone non-2023 years/dates without interpreting hashes or IDs."""
    if MONTH_YEAR_RE.search(value):
        raise SourceReleaseError(f"{context} references sealed May 2026")
    for match in YEAR_TOKEN_RE.finditer(value):
        if match.group(1) != "2023":
            raise SourceReleaseError(f"{context} references a non-2023 year")
    for match in COMPACT_DATE_RE.finditer(value):
        if match.group(1) != "2023":
            raise SourceReleaseError(f"{context} references a non-2023 compact date")


def _positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool):
        raise SourceReleaseError(f"{context} must be a positive canonical integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SourceReleaseError(f"{context} must be a positive canonical integer") from exc
    if parsed <= 0 or str(value).strip() != str(parsed):
        raise SourceReleaseError(f"{context} must be a positive canonical integer")
    return parsed


def load_contract(path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    path = _guard_input_file(path, "source-release contract")
    value = json.loads(path.read_text(encoding="utf-8"))
    _strict_keys(
        value,
        {"schema_version", "status", "season", "research_only", "betting_authorized",
         "prospective_evidence_claimed", "model_fitting_permitted", "protected_data",
         "sources", "release_requirements"},
        "source-release contract",
    )
    if value["schema_version"] != "direct-batter-pa-source-release-contract-v1":
        raise SourceReleaseError("source-release contract schema changed")
    if value["status"] != "LOCKED_HISTORICAL_2023_SOURCE_CAPTURE_ONLY" or value["season"] != 2023:
        raise SourceReleaseError("source-release contract scope changed")
    if value["research_only"] is not True or value["betting_authorized"] is not False:
        raise SourceReleaseError("source-release contract safety state changed")
    if value["prospective_evidence_claimed"] is not False or value["model_fitting_permitted"] is not False:
        raise SourceReleaseError("source-release contract permits a prohibited use")
    if value["protected_data"] != PROTECTED_STATE:
        raise SourceReleaseError("source-release protected-data state changed")
    expected_sources = {"baseball_savant_statcast_csv", "mlb_statsapi_feed_live"}
    if set(value["sources"]) != expected_sources:
        raise SourceReleaseError("source-release source allowlist changed")
    expected_source_contracts = {
        "baseball_savant_statcast_csv": {
            "scheme": "https",
            "host": "baseballsavant.mlb.com",
            "path": "/statcast_search/csv",
            "parser_id": "direct-batter-statcast-csv-2023-v1",
            "content_type_prefixes": ["text/csv", "application/octet-stream"],
            "required_query": {"all": "true", "hfGT": "R|", "type": "details", "player_type": "pitcher"},
        },
        "mlb_statsapi_feed_live": {
            "scheme": "https",
            "host": "statsapi.mlb.com",
            "path_pattern": r"^/api/v1\.1/game/[1-9][0-9]*/feed/live$",
            "parser_id": "direct-batter-mlb-feed-2023-v1",
            "content_type_prefixes": ["application/json"],
        },
    }
    if value["sources"] != expected_source_contracts:
        raise SourceReleaseError("source-release endpoint, parser, or protocol identity changed")
    requirements = value["release_requirements"]
    required_flags = {
        "external_expected_release_digest_required", "external_expected_runtime_lock_digest_required",
        "exact_file_set_required", "raw_response_bytes_retained",
        "request_and_response_receipts_retained", "parser_source_hash_retained",
        "zero_pa_targets_preserved", "external_schedule_manifest_digest_required",
        "external_identity_index_digest_required", "external_zero_pa_identity_map_digest_required",
        "exact_identity_and_statcast_coverage_required",
        "network_fetch_blocked_until_transitive_tls_authority",
    }
    if set(requirements) != required_flags or not all(flag is True for flag in requirements.values()):
        raise SourceReleaseError("source-release requirements were weakened")
    return value


def _canonical_request(
    item: Mapping[str, Any], contract: Mapping[str, Any], *, allow_derived: bool = False,
) -> dict[str, Any]:
    base_keys = {"request_id", "source_kind", "method", "url", "query", "expected"}
    expected_keys = base_keys | ({"full_url", "parser_id"} if allow_derived else set())
    _strict_keys(item, expected_keys, "request")
    request_id = item["request_id"]
    if not isinstance(request_id, str) or REQUEST_ID_RE.fullmatch(request_id) is None:
        raise SourceReleaseError("request_id is unsafe or noncanonical")
    assert_2023_only_text(request_id, "request_id")
    source_kind = item["source_kind"]
    if source_kind not in contract["sources"]:
        raise SourceReleaseError("request source is not allowlisted")
    if item["method"] != "GET":
        raise SourceReleaseError("only GET source requests are permitted")
    split = urlsplit(item["url"])
    source = contract["sources"][source_kind]
    if split.scheme != source["scheme"] or split.hostname != source["host"]:
        raise SourceReleaseError("request source identity differs from allowlist")
    if split.username is not None or split.password is not None or split.port is not None:
        raise SourceReleaseError("request URL authority is noncanonical")
    if split.query or split.fragment:
        raise SourceReleaseError("request URL must not embed query or fragment")
    if source_kind == "baseball_savant_statcast_csv":
        if split.path != source["path"]:
            raise SourceReleaseError("Statcast request path differs from allowlist")
    elif re.fullmatch(source["path_pattern"], split.path) is None:
        raise SourceReleaseError("MLB request path differs from allowlist")
    query = item["query"]
    if not isinstance(query, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in query.items()):
        raise SourceReleaseError("request query must be a string map")
    for key, value in query.items():
        assert_2023_only_text(f"{key}={value}", "request query")
    expected = item["expected"]
    if source_kind == "baseball_savant_statcast_csv":
        _strict_keys(expected, {"date_start", "date_end"}, "Statcast expected identity")
        start = _canonical_date_2023(expected["date_start"], "Statcast date_start")
        end = _canonical_date_2023(expected["date_end"], "Statcast date_end")
        if start > end:
            raise SourceReleaseError("Statcast date range is reversed")
        required_query = dict(source["required_query"])
        if set(query) != set(required_query) | {"game_date_gt", "game_date_lt"}:
            raise SourceReleaseError("Statcast query field set changed")
        if any(query.get(k) != v for k, v in required_query.items()):
            raise SourceReleaseError("Statcast required query changed")
        if query.get("game_date_gt") != start or query.get("game_date_lt") != end:
            raise SourceReleaseError("Statcast query dates disagree with expected identity")
    else:
        _strict_keys(
            expected,
            {"game_pk", "official_date", "away_team_id", "home_team_id", "zero_pa_player_ids"},
            "MLB expected identity",
        )
        game_pk = _positive_int(expected["game_pk"], "expected game_pk")
        official_date = _canonical_date_2023(expected["official_date"], "expected official_date")
        away_team_id = _positive_int(expected["away_team_id"], "expected away_team_id")
        home_team_id = _positive_int(expected["home_team_id"], "expected home_team_id")
        if away_team_id == home_team_id:
            raise SourceReleaseError("MLB expected team identities are duplicated")
        if split.path != f"/api/v1.1/game/{game_pk}/feed/live":
            raise SourceReleaseError("MLB path game identity disagrees with expected identity")
        players = expected["zero_pa_player_ids"]
        if not isinstance(players, list):
            raise SourceReleaseError("zero_pa_player_ids must be a list")
        normalized_players = [_positive_int(v, "zero-PA player id") for v in players]
        if normalized_players != sorted(set(normalized_players)):
            raise SourceReleaseError("zero-PA player ids must be sorted and unique")
        if query:
            raise SourceReleaseError("MLB feed request must not have query parameters")
        expected = {
            "game_pk": game_pk,
            "official_date": official_date,
            "away_team_id": away_team_id,
            "home_team_id": home_team_id,
            "zero_pa_player_ids": normalized_players,
        }
    canonical_query = {key: query[key] for key in sorted(query)}
    full_url = urlunsplit((split.scheme, split.netloc, split.path, urlencode(canonical_query), ""))
    assert_2023_only_text(full_url, "canonical request URL")
    canonical = {
        "request_id": request_id,
        "source_kind": source_kind,
        "method": "GET",
        "url": urlunsplit((split.scheme, split.netloc, split.path, "", "")),
        "query": canonical_query,
        "full_url": full_url,
        "expected": expected,
        "parser_id": source["parser_id"],
    }
    if allow_derived and (
        item.get("full_url") != canonical["full_url"] or item.get("parser_id") != canonical["parser_id"]
    ):
        raise SourceReleaseError("canonical request derived identity changed")
    return canonical


def load_plan(
    path: Path, contract: Mapping[str, Any], *, allow_canonical_requests: bool = False,
) -> dict[str, Any]:
    path = _guard_input_file(path, "request plan")
    raw = path.read_text(encoding="utf-8")
    assert_2023_only_text(path.name, "request-plan filename")
    value = json.loads(raw)
    _strict_keys(
        value,
        {"schema_version", "season", "evidence_class", "research_only", "betting_authorized",
         "prospective_evidence_claimed", "model_fitting_permitted", "protected_data", "requests"},
        "request plan",
    )
    if value["schema_version"] != SCHEMA_PLAN or value["season"] != 2023:
        raise SourceReleaseError("request plan is not exactly 2023")
    if value["evidence_class"] != EVIDENCE_CLASS or value["research_only"] is not True:
        raise SourceReleaseError("request plan evidence class changed")
    if value["betting_authorized"] is not False or value["prospective_evidence_claimed"] is not False:
        raise SourceReleaseError("request plan makes a prohibited claim")
    if isinstance(value["protected_data"], Mapping) and value["protected_data"].get("may_2026_opened") is not False:
        raise SourceReleaseError("request plan would open sealed May 2026")
    if value["model_fitting_permitted"] is not False or value["protected_data"] != PROTECTED_STATE:
        raise SourceReleaseError("request plan permits protected data or model fitting")
    if not isinstance(value["requests"], list) or not value["requests"]:
        raise SourceReleaseError("request plan must contain requests")
    requests = [
        _canonical_request(item, contract, allow_derived=allow_canonical_requests)
        for item in value["requests"]
    ]
    ids = [item["request_id"] for item in requests]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise SourceReleaseError("request ids must be sorted and unique")
    full_urls = [item["full_url"] for item in requests]
    if len(full_urls) != len(set(full_urls)):
        raise SourceReleaseError("request full URLs must be unique")
    logical = [
        (item["source_kind"], canonical_json_bytes(item["expected"]))
        for item in requests
    ]
    if len(logical) != len(set(logical)):
        raise SourceReleaseError("logical source requests must be unique")
    intervals = sorted(
        (item["expected"]["date_start"], item["expected"]["date_end"])
        for item in requests if item["source_kind"] == "baseball_savant_statcast_csv"
    )
    for previous, current in zip(intervals, intervals[1:]):
        if current[0] <= previous[1]:
            raise SourceReleaseError("Statcast request intervals overlap")
    if {item["source_kind"] for item in requests} != set(contract["sources"]):
        raise SourceReleaseError("request plan must cover both required historical sources")
    value = dict(value)
    value["requests"] = requests
    return value


def _parse_schedule_games(payload: bytes) -> tuple[dict[str, Any], ...]:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceReleaseError("official schedule receipt is not valid JSON") from exc
    if not isinstance(document, Mapping) or not isinstance(document.get("dates"), list):
        raise SourceReleaseError("official schedule receipt lacks dates")
    games: list[dict[str, Any]] = []
    for date_group in document["dates"]:
        if not isinstance(date_group, Mapping) or not isinstance(date_group.get("games"), list):
            raise SourceReleaseError("official schedule receipt date group is malformed")
        for raw in date_group["games"]:
            if not isinstance(raw, Mapping):
                raise SourceReleaseError("official schedule game is malformed")
            game_pk = _positive_int(raw.get("gamePk"), "schedule gamePk")
            official_date = _canonical_date_2023(raw.get("officialDate"), "schedule officialDate")
            if raw.get("gameType") != "R":
                raise SourceReleaseError("schedule receipt contains a non-regular-season game")
            teams = raw.get("teams") or {}
            away_team_id = _positive_int(((teams.get("away") or {}).get("team") or {}).get("id"), "schedule away team id")
            home_team_id = _positive_int(((teams.get("home") or {}).get("team") or {}).get("id"), "schedule home team id")
            if away_team_id == home_team_id:
                raise SourceReleaseError("schedule game has duplicate team identities")
            games.append({
                "game_pk": game_pk, "official_date": official_date, "game_type": "R",
                "away_team_id": away_team_id, "home_team_id": home_team_id,
            })
    games.sort(key=lambda game: (game["official_date"], game["game_pk"]))
    if not games or len({game["game_pk"] for game in games}) != len(games):
        raise SourceReleaseError("schedule game identities are empty or duplicated")
    return tuple(games)


def _load_schedule_manifest(path: Path, expected_digest: str) -> tuple[dict[str, Any], bytes, tuple[dict[str, Any], ...], tuple[tuple[str, bytes], ...]]:
    path = _guard_input_file(path, "schedule-receipt manifest")
    if SHA256_RE.fullmatch(expected_digest or "") is None:
        raise SourceReleaseError("external expected schedule-manifest digest is required")
    manifest_bytes = path.read_bytes()
    if sha256_bytes(manifest_bytes) != expected_digest:
        raise SourceReleaseError("schedule-receipt manifest differs from external expected digest")
    value = json.loads(manifest_bytes)
    _strict_keys(
        value,
        {"schema_version", "season", "evidence_class", "research_only", "betting_authorized",
         "protected_data", "source", "receipt"},
        "schedule-receipt manifest",
    )
    if value["schema_version"] != SCHEMA_SCHEDULE_MANIFEST or value["season"] != 2023:
        raise SourceReleaseError("schedule-receipt manifest scope changed")
    if value["evidence_class"] != EVIDENCE_CLASS or value["research_only"] is not True or value["betting_authorized"] is not False:
        raise SourceReleaseError("schedule-receipt manifest safety state changed")
    if value["protected_data"] != PROTECTED_STATE:
        raise SourceReleaseError("schedule-receipt manifest protected state changed")
    source = value["source"]
    if not isinstance(source, Mapping):
        raise SourceReleaseError("schedule source identity is malformed")
    _strict_keys(source, {"method", "url", "query", "full_url", "parser_id"}, "schedule source identity")
    expected_url = "https://statsapi.mlb.com/api/v1/schedule"
    expected_query = {"gameType": "R", "hydrate": "team", "season": "2023", "sportId": "1"}
    expected_full_url = f"{expected_url}?{urlencode(expected_query)}"
    if source != {
        "method": "GET", "url": expected_url, "query": expected_query,
        "full_url": expected_full_url, "parser_id": "official-mlb-schedule-identity-2023-v1",
    }:
        raise SourceReleaseError("schedule source endpoint, query, or parser identity changed")
    receipt = value["receipt"]
    if not isinstance(receipt, Mapping):
        raise SourceReleaseError("schedule receipt is malformed")
    _strict_keys(
        receipt,
        {"body_path", "body_bytes", "body_sha256", "status", "content_type", "observed_at_utc", "final_url"},
        "schedule receipt",
    )
    body_path = _guard_child(path.parent, receipt["body_path"], "schedule receipt body")
    body = body_path.read_bytes()
    if receipt["status"] != 200 or receipt["content_type"] != "application/json" or receipt["final_url"] != expected_full_url:
        raise SourceReleaseError("schedule receipt transport identity changed")
    _canonical_utc(receipt["observed_at_utc"], "schedule observed_at_utc")
    if receipt["body_bytes"] != len(body) or receipt["body_sha256"] != sha256_bytes(body):
        raise SourceReleaseError("schedule receipt body binding changed")
    games = _parse_schedule_games(body)
    return value, manifest_bytes, games, ((receipt["body_path"], body),)


def _load_identity_index(
    path: Path, expected_digest: str, *, schedule_manifest_digest: str,
    schedule_games: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], bytes]:
    path = _guard_input_file(path, "2023 identity index")
    if SHA256_RE.fullmatch(expected_digest or "") is None:
        raise SourceReleaseError("external expected identity-index digest is required")
    payload = path.read_bytes()
    if sha256_bytes(payload) != expected_digest:
        raise SourceReleaseError("identity index differs from external expected digest")
    value = json.loads(payload)
    _strict_keys(
        value,
        {"schema_version", "season", "evidence_class", "research_only", "betting_authorized",
         "protected_data", "fields", "source_schedule_manifest_sha256", "games"},
        "identity index",
    )
    if value["schema_version"] != SCHEMA_IDENTITY_INDEX or value["season"] != 2023:
        raise SourceReleaseError("identity index scope changed")
    if value["evidence_class"] != EVIDENCE_CLASS or value["research_only"] is not True or value["betting_authorized"] is not False:
        raise SourceReleaseError("identity index safety state changed")
    if value["protected_data"] != PROTECTED_STATE or value["fields"] != INDEX_FIELDS:
        raise SourceReleaseError("identity index field allowlist or protected state changed")
    if value["source_schedule_manifest_sha256"] != schedule_manifest_digest:
        raise SourceReleaseError("identity index is not bound to the approved schedule manifest")
    if not isinstance(value["games"], list):
        raise SourceReleaseError("identity index games are malformed")
    games: list[dict[str, Any]] = []
    for row in value["games"]:
        if not isinstance(row, Mapping):
            raise SourceReleaseError("identity index row is malformed")
        _strict_keys(row, set(INDEX_FIELDS), "identity index row")
        canonical = {
            "game_pk": _positive_int(row["game_pk"], "identity index game_pk"),
            "official_date": _canonical_date_2023(row["official_date"], "identity index official_date"),
            "game_type": row["game_type"],
            "away_team_id": _positive_int(row["away_team_id"], "identity index away_team_id"),
            "home_team_id": _positive_int(row["home_team_id"], "identity index home_team_id"),
        }
        if canonical["game_type"] != "R" or canonical["away_team_id"] == canonical["home_team_id"]:
            raise SourceReleaseError("identity index row has invalid game type or team identity")
        games.append(canonical)
    games.sort(key=lambda game: (game["official_date"], game["game_pk"]))
    if games != list(schedule_games) or value["games"] != games:
        raise SourceReleaseError("identity index is not the exact sorted schedule-receipt projection")
    return value, payload


def _load_zero_pa_map(
    path: Path, expected_digest: str, *, game_by_pk: Mapping[int, Mapping[str, Any]],
) -> tuple[dict[str, Any], bytes, tuple[dict[str, Any], ...]]:
    path = _guard_input_file(path, "existing zero-PA identity map")
    if SHA256_RE.fullmatch(expected_digest or "") is None:
        raise SourceReleaseError("external expected zero-PA identity-map digest is required")
    payload = path.read_bytes()
    if sha256_bytes(payload) != expected_digest:
        raise SourceReleaseError("zero-PA identity map differs from external expected digest")
    value = json.loads(payload)
    _strict_keys(
        value,
        {"schema_version", "season", "evidence_class", "research_only", "betting_authorized",
         "protected_data", "fields", "source_zero_pa_bundle_manifest_sha256", "targets"},
        "zero-PA identity map",
    )
    if value["schema_version"] != SCHEMA_ZERO_PA_MAP or value["season"] != 2023:
        raise SourceReleaseError("zero-PA identity map scope changed")
    if value["evidence_class"] != EVIDENCE_CLASS or value["research_only"] is not True or value["betting_authorized"] is not False:
        raise SourceReleaseError("zero-PA identity map safety state changed")
    if value["protected_data"] != PROTECTED_STATE or value["fields"] != ZERO_PA_FIELDS:
        raise SourceReleaseError("zero-PA map positive field allowlist changed")
    if SHA256_RE.fullmatch(str(value["source_zero_pa_bundle_manifest_sha256"])) is None:
        raise SourceReleaseError("zero-PA map lacks its retained bundle-manifest binding")
    if not isinstance(value["targets"], list) or not value["targets"]:
        raise SourceReleaseError("zero-PA identity targets are empty or malformed")
    targets: list[dict[str, Any]] = []
    for row in value["targets"]:
        if not isinstance(row, Mapping):
            raise SourceReleaseError("zero-PA target is malformed")
        _strict_keys(row, set(ZERO_PA_FIELDS), "zero-PA target")
        target = {
            "game_pk": _positive_int(row["game_pk"], "zero-PA game_pk"),
            "official_date": _canonical_date_2023(row["official_date"], "zero-PA official_date"),
            "player_id": _positive_int(row["player_id"], "zero-PA player_id"),
            "source_response_sha256": str(row["source_response_sha256"]),
        }
        if SHA256_RE.fullmatch(target["source_response_sha256"]) is None:
            raise SourceReleaseError("zero-PA target source response hash is invalid")
        game = game_by_pk.get(target["game_pk"])
        if game is None or game["official_date"] != target["official_date"]:
            raise SourceReleaseError("zero-PA target is outside the exact identity index")
        targets.append(target)
    targets.sort(key=lambda row: (row["official_date"], row["game_pk"], row["player_id"]))
    identities = [(row["game_pk"], row["player_id"]) for row in targets]
    if value["targets"] != targets or len(identities) != len(set(identities)):
        raise SourceReleaseError("zero-PA targets must be exactly sorted and unique")
    return value, payload, tuple(targets)


def load_authority_bundle(
    *, schedule_manifest_path: Path, expected_schedule_manifest_digest: str,
    identity_index_path: Path, expected_identity_index_digest: str,
    zero_pa_map_path: Path, expected_zero_pa_map_digest: str,
) -> AuthorityBundle:
    schedule, schedule_bytes, games, raw_files = _load_schedule_manifest(
        schedule_manifest_path, expected_schedule_manifest_digest,
    )
    game_by_pk = {game["game_pk"]: game for game in games}
    index, index_bytes = _load_identity_index(
        identity_index_path, expected_identity_index_digest,
        schedule_manifest_digest=expected_schedule_manifest_digest, schedule_games=games,
    )
    zero, zero_bytes, targets = _load_zero_pa_map(
        zero_pa_map_path, expected_zero_pa_map_digest, game_by_pk=game_by_pk,
    )
    return AuthorityBundle(
        schedule_manifest=schedule, identity_index=index, zero_pa_map=zero,
        games=games, zero_targets=targets, schedule_raw_files=raw_files,
        schedule_manifest_bytes=schedule_bytes, identity_index_bytes=index_bytes,
        zero_pa_map_bytes=zero_bytes,
        schedule_manifest_sha256=expected_schedule_manifest_digest,
        identity_index_sha256=expected_identity_index_digest,
        zero_pa_map_sha256=expected_zero_pa_map_digest,
    )


def validate_plan_authority(plan: Mapping[str, Any], authority: AuthorityBundle) -> None:
    games = {game["game_pk"]: game for game in authority.games}
    targets_by_game: dict[int, list[int]] = {game_pk: [] for game_pk in games}
    for target in authority.zero_targets:
        targets_by_game[target["game_pk"]].append(target["player_id"])
    feed_requests = [request for request in plan["requests"] if request["source_kind"] == "mlb_statsapi_feed_live"]
    if len(feed_requests) != len(games):
        raise SourceReleaseError("official feed requests do not exactly cover the identity index")
    for request in feed_requests:
        expected = request["expected"]
        game = games.get(expected["game_pk"])
        if game is None:
            raise SourceReleaseError("official feed request is outside the identity index")
        if expected != {
            "game_pk": game["game_pk"], "official_date": game["official_date"],
            "away_team_id": game["away_team_id"], "home_team_id": game["home_team_id"],
            "zero_pa_player_ids": targets_by_game[game["game_pk"]],
        }:
            raise SourceReleaseError("official feed request differs from index or zero-PA authority")
    from datetime import date as date_type
    covered_dates: set[str] = set()
    for request in plan["requests"]:
        if request["source_kind"] != "baseball_savant_statcast_csv":
            continue
        cursor = date_type.fromisoformat(request["expected"]["date_start"])
        end = date_type.fromisoformat(request["expected"]["date_end"])
        while cursor <= end:
            covered_dates.add(cursor.isoformat())
            cursor += timedelta(days=1)
    index_dates = {game["official_date"] for game in authority.games}
    if covered_dates != index_dates:
        raise SourceReleaseError("Statcast request intervals do not exactly cover identity-index dates")


def _module_identity(name: str) -> dict[str, str]:
    module = importlib.import_module(name)
    origin = getattr(module, "__file__", None)
    if origin is None:
        spec = getattr(module, "__spec__", None)
        marker = getattr(spec, "origin", None) or "built-in"
        return {"name": name, "origin_kind": marker, "sha256": ""}
    path = Path(origin).resolve(strict=True)
    return {"name": name, "origin_kind": "file", "sha256": sha256_file(path)}


def _runtime_path_identity(path_value: str | None) -> dict[str, Any]:
    if not path_value:
        return {"path": path_value, "exists": False, "kind": "absent", "reparse": False, "sha256": None}
    path = Path(path_value)
    try:
        lexical = Path(os.path.abspath(os.fspath(path)))
    except (OSError, ValueError):
        return {"path": str(path_value), "exists": False, "kind": "invalid", "reparse": False, "sha256": None}
    if not lexical.exists():
        return {"path": lexical.as_posix(), "exists": False, "kind": "missing", "reparse": lexical.is_symlink(), "sha256": None}
    if lexical.is_file():
        return {"path": lexical.as_posix(), "exists": True, "kind": "file", "reparse": _has_reparse_point(lexical), "sha256": sha256_file(lexical)}
    return {"path": lexical.as_posix(), "exists": True, "kind": "directory", "reparse": _has_reparse_point(lexical), "sha256": None}


def _site_injection_identity() -> dict[str, Any]:
    site_roots: list[Path] = []
    for value in list(site.getsitepackages()) + [site.getusersitepackages()]:
        path = Path(value)
        if path not in site_roots:
            site_roots.append(path)
    pth: list[dict[str, Any]] = []
    for root in site_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.pth")):
            pth.append({
                "path": path.resolve().as_posix(), "bytes": path.stat().st_size,
                "reparse": _has_reparse_point(path), "sha256": sha256_file(path),
            })
    customizations: list[dict[str, Any]] = []
    for name in ("sitecustomize", "usercustomize"):
        module = sys.modules.get(name)
        origin = getattr(module, "__file__", None) if module is not None else None
        customizations.append({
            "name": name,
            "loaded": module is not None,
            "origin": Path(origin).resolve().as_posix() if origin else None,
            "reparse": _has_reparse_point(Path(origin)) if origin else False,
            "sha256": sha256_file(Path(origin).resolve()) if origin else None,
        })
    return {
        "sys_path": [_runtime_path_identity(value or os.getcwd()) for value in sys.path],
        "site_roots": [_runtime_path_identity(str(path)) for path in site_roots],
        "pth_files": pth,
        "enable_user_site": bool(site.ENABLE_USER_SITE),
        "customizations": customizations,
    }


def _tls_identity() -> dict[str, Any]:
    defaults = ssl.get_default_verify_paths()
    return {
        "openssl_version": ssl.OPENSSL_VERSION,
        "openssl_version_info": list(ssl.OPENSSL_VERSION_INFO),
        "ssl_module": _module_identity("_ssl"),
        "hashlib_module": _module_identity("_hashlib"),
        "default_verify_paths": {
            "cafile": _runtime_path_identity(defaults.cafile),
            "capath": _runtime_path_identity(defaults.capath),
            "openssl_cafile_env": defaults.openssl_cafile_env,
            "openssl_cafile": defaults.openssl_cafile,
            "openssl_capath_env": defaults.openssl_capath_env,
            "openssl_capath": defaults.openssl_capath,
        },
        "environment_overrides": {
            name: os.environ.get(name)
            for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PYTHONPATH", "PYTHONHOME")
        },
        "transitive_shared_library_resolution_proven": False,
    }


@functools.lru_cache(maxsize=1)
def observed_runtime_lock() -> dict[str, Any]:
    executable = Path(sys.executable).resolve(strict=True)
    return {
        "schema_version": SCHEMA_LOCK,
        "python": {
            "implementation": sys.implementation.name,
            "version": ".".join(str(v) for v in sys.version_info[:3]),
            "executable_sha256": sha256_file(executable),
            "build": list(platform.python_build()),
            "compiler": platform.python_compiler(),
        },
        "platform": {
            "system": platform.system(), "release": platform.release(),
            "version": platform.version(), "machine": platform.machine(),
            "architecture": list(platform.architecture()),
        },
        "third_party_distributions": [],
        "stdlib_modules": [_module_identity(name) for name in RUNTIME_MODULES],
        "site_injection": _site_injection_identity(),
        "tls": _tls_identity(),
        "authority_status": "BLOCKED_UNPROVEN_TRANSITIVE_TLS_SHARED_LIBRARIES",
        "network_fetch_authorized": False,
        "boundary": "EXACT_LOCAL_RUNTIME_WITH_TLS_AND_INJECTION_OBSERVATION_NETWORK_BLOCKED",
    }


def write_runtime_lock(output: Path) -> str:
    output = _guard_output_path(output, "runtime-lock output")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite runtime lock: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(observed_runtime_lock())
    output.write_bytes(payload)
    return sha256_bytes(payload)


def verify_runtime_lock(path: Path, expected_digest: str) -> dict[str, Any]:
    path = _guard_input_file(path, "runtime lock")
    if SHA256_RE.fullmatch(expected_digest or "") is None:
        raise SourceReleaseError("an external expected runtime-lock digest is required")
    if sha256_file(path) != expected_digest:
        raise SourceReleaseError("runtime lock differs from external expected digest")
    supplied = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(supplied, Mapping):
        raise SourceReleaseError("runtime lock root is malformed")
    _validate_locked_runtime_document(supplied)
    if supplied != observed_runtime_lock():
        raise SourceReleaseError("active runtime differs from exact runtime lock")
    return supplied


def _content_type(headers: Mapping[str, str]) -> str:
    for key, value in headers.items():
        if key.lower() == "content-type":
            return str(value).split(";", 1)[0].strip().lower()
    return ""


def _normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_key, raw_value in headers.items():
        key = str(raw_key).strip().lower()
        value = str(raw_value).strip()
        if not key or key in result or "\r" in key or "\n" in key or "\r" in value or "\n" in value:
            raise SourceReleaseError("source response headers are malformed or ambiguous")
        result[key] = value
    return {key: result[key] for key in sorted(result)}


def _validate_statcast(
    body: bytes, expected: Mapping[str, Any], *, game_by_pk: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceReleaseError("Statcast response is not UTF-8 CSV") from exc
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None or any(name not in reader.fieldnames for name in STATCAST_REQUIRED_COLUMNS):
        raise SourceReleaseError("Statcast response lacks required identity columns")
    rows = list(reader)
    if not rows:
        raise SourceReleaseError("Statcast response is empty")
    identities: set[tuple[str, int, int, int, int, int]] = set()
    start = expected["date_start"]
    end = expected["date_end"]
    for index, row in enumerate(rows, start=2):
        game_date = _canonical_date_2023(row.get("game_date"), f"Statcast row {index} game_date")
        if not start <= game_date <= end:
            raise SourceReleaseError("Statcast response contains a row outside its request range")
        if row.get("game_type") != "R":
            raise SourceReleaseError("Statcast response contains a non-regular-season row")
        game_pk = _positive_int(row.get("game_pk"), f"Statcast row {index} game_pk")
        if game_by_pk is not None:
            game = game_by_pk.get(game_pk)
            if game is None or game["official_date"] != game_date:
                raise SourceReleaseError("Statcast row gamePk/date is outside the exact identity index")
        identity = (
            game_date,
            game_pk,
            _positive_int(row.get("batter"), f"Statcast row {index} batter"),
            _positive_int(row.get("pitcher"), f"Statcast row {index} pitcher"),
            _positive_int(row.get("at_bat_number"), f"Statcast row {index} at_bat_number"),
            _positive_int(row.get("pitch_number"), f"Statcast row {index} pitch_number"),
        )
        if identity in identities:
            raise SourceReleaseError("Statcast response has duplicate pitch identity")
        identities.add(identity)
    return {
        "rows": len(rows), "first_date": min(i[0] for i in identities),
        "last_date": max(i[0] for i in identities),
        "game_pks": sorted({i[1] for i in identities}),
    }


def _find_player(feed: Mapping[str, Any], player_id: int) -> Mapping[str, Any]:
    teams = (((feed.get("liveData") or {}).get("boxscore") or {}).get("teams") or {})
    matches: list[Mapping[str, Any]] = []
    for side in ("away", "home"):
        players = ((teams.get(side) or {}).get("players") or {})
        if not isinstance(players, Mapping):
            raise SourceReleaseError("MLB boxscore player collection is malformed")
        for player in players.values():
            if not isinstance(player, Mapping):
                continue
            person = player.get("person") or {}
            try:
                observed = _positive_int(person.get("id"), "MLB player id")
            except SourceReleaseError:
                continue
            if observed == player_id:
                matches.append(player)
    if len(matches) != 1:
        raise SourceReleaseError("MLB zero-PA player identity is missing or ambiguous")
    return matches[0]


def _validate_mlb_feed(body: bytes, expected: Mapping[str, Any]) -> dict[str, Any]:
    try:
        feed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceReleaseError("MLB response is not valid JSON") from exc
    if not isinstance(feed, Mapping):
        raise SourceReleaseError("MLB response root is not an object")
    if _positive_int(feed.get("gamePk"), "MLB gamePk") != expected["game_pk"]:
        raise SourceReleaseError("MLB response game identity mismatch")
    game_data = feed.get("gameData") or {}
    if not isinstance(game_data, Mapping):
        raise SourceReleaseError("MLB response lacks gameData")
    official_date = _canonical_date_2023(
        (game_data.get("datetime") or {}).get("officialDate"), "MLB officialDate"
    )
    if official_date != expected["official_date"]:
        raise SourceReleaseError("MLB response official date mismatch")
    if (game_data.get("game") or {}).get("type") != "R":
        raise SourceReleaseError("MLB response is not a regular-season game")
    teams = game_data.get("teams") or {}
    if _positive_int((teams.get("away") or {}).get("id"), "MLB away team id") != expected["away_team_id"]:
        raise SourceReleaseError("MLB response away-team identity mismatch")
    if _positive_int((teams.get("home") or {}).get("id"), "MLB home team id") != expected["home_team_id"]:
        raise SourceReleaseError("MLB response home-team identity mismatch")
    status = game_data.get("status") or {}
    if status.get("abstractGameState") != "Final":
        raise SourceReleaseError("MLB historical response is not final")
    zero_players: list[int] = []
    zero_fields = (
        "plateAppearances", "atBats", "hits", "doubles", "triples", "homeRuns",
        "baseOnBalls", "strikeOuts",
    )
    for player_id in expected["zero_pa_player_ids"]:
        player = _find_player(feed, player_id)
        batting = ((player.get("stats") or {}).get("batting") or {})
        if not isinstance(batting, Mapping):
            raise SourceReleaseError("MLB zero-PA batting object is malformed")
        for field in zero_fields:
            value = batting.get(field)
            if isinstance(value, bool) or value not in (0, "0"):
                raise SourceReleaseError(f"MLB zero-PA field is not verified zero: {field}")
        zero_players.append(player_id)
    return {
        "game_pk": expected["game_pk"], "official_date": official_date,
        "away_team_id": expected["away_team_id"], "home_team_id": expected["home_team_id"],
        "zero_pa_player_ids": zero_players,
    }


def _safe_relative(path: str, context: str) -> Path:
    value = Path(path)
    if value.is_absolute() or ".." in value.parts or not value.parts:
        raise SourceReleaseError(f"{context} is not a safe relative path")
    assert_2023_only_text(path, context)
    return value


def _write_bytes(root: Path, relative: str, payload: bytes) -> None:
    rel = _safe_relative(relative, "release path")
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _guard_child(root, relative, "release write target", require_file=False)
    if target.exists():
        raise SourceReleaseError(f"duplicate release path: {relative}")
    target.write_bytes(payload)


def _enumerate_release_files(root: Path, *, exclude_manifest: bool = True) -> list[dict[str, Any]]:
    root = _guard_input_dir(root, "release enumeration")
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if _has_reparse_point(path):
            raise SourceReleaseError("release cannot contain symlinks, junctions, or reparse points")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if exclude_manifest and relative == "manifest.json":
            continue
        path = _guard_child(root, relative, "release file")
        rows.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def _manifest_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    _strict_keys(manifest, MANIFEST_KEYS, "release manifest")
    if manifest["schema_version"] != SCHEMA_MANIFEST or manifest["status"] != "AWAITING_EXTERNAL_EXPECTED_RELEASE_DIGEST":
        raise SourceReleaseError("release manifest schema or status changed")
    if manifest["authority_status"] != "NETWORK_FETCH_BLOCKED_UNPROVEN_TRANSITIVE_TLS_SHARED_LIBRARIES":
        raise SourceReleaseError("release manifest authority status changed")
    if manifest["evidence_class"] != EVIDENCE_CLASS or manifest["authorization"] != AUTHORIZATION:
        raise SourceReleaseError("release manifest evidence class or authorization changed")
    if manifest["season"] != 2023 or manifest["research_only"] is not True:
        raise SourceReleaseError("release manifest season or research state changed")
    if manifest["betting_authorized"] is not False or manifest["prospective_evidence_claimed"] is not False:
        raise SourceReleaseError("release manifest makes a prohibited claim")
    if manifest["model_fitting_permitted"] is not False or manifest["network_fetch_permitted"] is not False:
        raise SourceReleaseError("release manifest permits fitting or network fetch")
    if manifest["protected_data"] != PROTECTED_STATE:
        raise SourceReleaseError("release manifest protected-data state changed")
    bindings = manifest["bindings"]
    if not isinstance(bindings, Mapping):
        raise SourceReleaseError("release bindings are malformed")
    _strict_keys(
        bindings,
        {"contract_sha256", "parser_source_sha256", "request_plan_sha256", "runtime_lock_sha256",
         "schedule_manifest_sha256", "identity_index_sha256", "zero_pa_identity_map_sha256"},
        "release bindings",
    )
    if any(SHA256_RE.fullmatch(str(value)) is None for value in bindings.values()):
        raise SourceReleaseError("release binding hash is invalid")
    if not isinstance(manifest["source_counts"], Mapping) or set(manifest["source_counts"]) != {
        "baseball_savant_statcast_csv", "mlb_statsapi_feed_live"
    }:
        raise SourceReleaseError("release source counts are malformed")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in manifest["source_counts"].values()
    ):
        raise SourceReleaseError("release source count value is invalid")
    for name in ("identity_games", "statcast_games_observed", "zero_pa_targets_preserved"):
        if isinstance(manifest[name], bool) or not isinstance(manifest[name], int) or manifest[name] < 1:
            raise SourceReleaseError(f"release manifest {name} is invalid")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise SourceReleaseError("release manifest file set is empty or malformed")
    paths: list[str] = []
    for entry in files:
        if not isinstance(entry, Mapping):
            raise SourceReleaseError("release file entry is malformed")
        _strict_keys(entry, {"path", "bytes", "sha256"}, "release file entry")
        _safe_relative(entry["path"], "release manifest file")
        if isinstance(entry["bytes"], bool) or not isinstance(entry["bytes"], int) or entry["bytes"] < 0:
            raise SourceReleaseError("release file byte count is invalid")
        if SHA256_RE.fullmatch(str(entry["sha256"])) is None:
            raise SourceReleaseError("release file hash is invalid")
        paths.append(entry["path"])
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise SourceReleaseError("release manifest paths must be exactly sorted and unique")
    if manifest["external_expected_release_digest"] is not None:
        raise SourceReleaseError("release contains a self-declared expected digest")
    identity = {
        key: manifest[key]
        for key in sorted(MANIFEST_KEYS - {"observed_release_digest", "external_expected_release_digest"})
    }
    return identity


def _release_digest(manifest: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes({
        "schema_version": "direct-batter-pa-source-release-external-identity-v1",
        "manifest_identity": _manifest_identity(manifest),
    }))


def build_release(
    *, plan_path: Path, contract_path: Path, runtime_lock_path: Path,
    expected_runtime_lock_digest: str, output_dir: Path, transport: OfflineFixtureTransport,
    schedule_manifest_path: Path, expected_schedule_manifest_digest: str,
    identity_index_path: Path, expected_identity_index_digest: str,
    zero_pa_map_path: Path, expected_zero_pa_map_digest: str,
    request_delay_seconds: float = 0.0, offline_fixture_transport: bool = False,
) -> dict[str, Any]:
    if isinstance(request_delay_seconds, bool) or not isinstance(request_delay_seconds, (int, float)):
        raise SourceReleaseError("request delay must be numeric")
    if request_delay_seconds < 0 or request_delay_seconds > 60:
        raise SourceReleaseError("request delay must be in [0,60] seconds")
    plan_path = _guard_input_file(plan_path, "request plan")
    contract_path = _guard_input_file(contract_path, "source-release contract")
    runtime_lock_path = _guard_input_file(runtime_lock_path, "runtime lock")
    schedule_manifest_path = _guard_input_file(schedule_manifest_path, "schedule-receipt manifest")
    identity_index_path = _guard_input_file(identity_index_path, "2023 identity index")
    zero_pa_map_path = _guard_input_file(zero_pa_map_path, "existing zero-PA identity map")
    output_dir = _guard_output_path(output_dir, "source-release output")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite source release: {output_dir}")
    if offline_fixture_transport is not True:
        raise SourceReleaseError("network fetch remains blocked: transitive TLS shared-library authority is unproven")
    contract = load_contract(contract_path)
    plan = load_plan(plan_path, contract)
    runtime_lock = verify_runtime_lock(runtime_lock_path, expected_runtime_lock_digest)
    if runtime_lock["network_fetch_authorized"] is not False:
        raise SourceReleaseError("runtime lock network safety state changed")
    authority = load_authority_bundle(
        schedule_manifest_path=schedule_manifest_path,
        expected_schedule_manifest_digest=expected_schedule_manifest_digest,
        identity_index_path=identity_index_path,
        expected_identity_index_digest=expected_identity_index_digest,
        zero_pa_map_path=zero_pa_map_path,
        expected_zero_pa_map_digest=expected_zero_pa_map_digest,
    )
    validate_plan_authority(plan, authority)
    if type(transport) is not OfflineFixtureTransport or type(transport.responses) is not dict:
        raise SourceReleaseError("only a closed offline fixture-response mapping is permitted")
    planned_urls = {request["full_url"] for request in plan["requests"]}
    if set(transport.responses) != planned_urls:
        raise SourceReleaseError("offline fixture-response mapping does not exactly match request URLs")
    game_by_pk = {game["game_pk"]: game for game in authority.games}
    prior_zero_by_identity = {
        (target["game_pk"], target["player_id"]): target["source_response_sha256"]
        for target in authority.zero_targets
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        _write_bytes(staging, "request_plan.json", canonical_json_bytes(plan))
        _write_bytes(staging, "runtime_lock.json", canonical_json_bytes(runtime_lock))
        _write_bytes(staging, "authority/schedule/manifest.json", authority.schedule_manifest_bytes)
        for relative, payload in authority.schedule_raw_files:
            _write_bytes(staging, f"authority/schedule/{relative}", payload)
        _write_bytes(staging, "authority/identity_index.json", authority.identity_index_bytes)
        _write_bytes(staging, "authority/zero_pa_identity_map.json", authority.zero_pa_map_bytes)
        parser_bytes = Path(__file__).read_bytes()
        contract_bytes = contract_path.read_bytes()
        _write_bytes(staging, "authority/parser_source.py", parser_bytes)
        _write_bytes(staging, "authority/source_contract.json", contract_bytes)
        parser_sha = sha256_bytes(parser_bytes)
        contract_sha = sha256_bytes(contract_bytes)
        zero_rows: list[dict[str, Any]] = []
        observed_statcast_games: set[int] = set()
        source_counts = {name: 0 for name in contract["sources"]}
        headers = {"User-Agent": "baseball-predictor-historical-source-release/1.0", "Accept-Encoding": "identity"}
        for request_index, request in enumerate(plan["requests"]):
            if request_index and request_delay_seconds:
                time.sleep(request_delay_seconds)
            response = transport.responses[request["full_url"]]
            requested_at = _canonical_utc(response.requested_at_utc, "response requested_at_utc")
            observed_at = _canonical_utc(response.observed_at_utc, "response observed_at_utc")
            if observed_at < requested_at:
                raise SourceReleaseError(f"source response timestamp precedes request: {request['request_id']}")
            if observed_at > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise SourceReleaseError(f"source response timestamp is implausibly future: {request['request_id']}")
            if response.status != 200:
                raise SourceReleaseError(f"source response status is not 200: {request['request_id']}")
            if not isinstance(response.body, bytes) or not response.body:
                raise SourceReleaseError(f"source response is empty: {request['request_id']}")
            if response.final_url != request["full_url"]:
                raise SourceReleaseError(f"source redirected or changed effective URL: {request['request_id']}")
            response_headers = _normalized_headers(response.headers)
            content_type = _content_type(response_headers)
            allowed_types = contract["sources"][request["source_kind"]]["content_type_prefixes"]
            if content_type not in allowed_types:
                raise SourceReleaseError(f"source content type is not allowlisted: {request['request_id']}")
            if request["source_kind"] == "baseball_savant_statcast_csv":
                semantics = _validate_statcast(response.body, request["expected"], game_by_pk=game_by_pk)
                observed_statcast_games.update(semantics["game_pks"])
                extension = "csv"
            else:
                semantics = _validate_mlb_feed(response.body, request["expected"])
                extension = "json"
                for player_id in semantics["zero_pa_player_ids"]:
                    zero_rows.append({
                        "game_pk": semantics["game_pk"], "official_date": semantics["official_date"],
                        "player_id": player_id, "request_id": request["request_id"],
                        "response_sha256": sha256_bytes(response.body),
                        "prior_source_response_sha256": prior_zero_by_identity[(semantics["game_pk"], player_id)],
                    })
            raw_relative = f"transport/{request['source_kind']}/{request['request_id']}/response.{extension}"
            receipt_relative = f"transport/{request['source_kind']}/{request['request_id']}/receipt.json"
            _write_bytes(staging, raw_relative, response.body)
            receipt = {
                "schema_version": SCHEMA_RECEIPT,
                "evidence_class": EVIDENCE_CLASS,
                "authorization": AUTHORIZATION,
                "prospective_evidence_claimed": False,
                "request": request,
                "request_headers": headers,
                "response": {
                    "status": response.status,
                    "content_type": content_type,
                    "observed_at_utc": observed_at.isoformat().replace("+00:00", "Z"),
                    "requested_at_utc": requested_at.isoformat().replace("+00:00", "Z"),
                    "final_url": response.final_url,
                    "headers": response_headers,
                    "body_path": raw_relative,
                    "body_bytes": len(response.body),
                    "body_sha256": sha256_bytes(response.body),
                },
                "parser": {"parser_id": request["parser_id"], "source_sha256": parser_sha},
                "semantics": semantics,
                "protected_data": PROTECTED_STATE,
            }
            _write_bytes(staging, receipt_relative, canonical_json_bytes(receipt))
            source_counts[request["source_kind"]] += 1
        if observed_statcast_games != set(game_by_pk):
            raise SourceReleaseError("Statcast responses do not exactly cover every identity-index gamePk")
        zero_rows.sort(key=lambda row: (row["official_date"], row["game_pk"], row["player_id"]))
        _write_bytes(staging, "derived/zero_pa_evidence.json", canonical_json_bytes({
            "schema_version": "direct-batter-pa-zero-pa-preservation-v1",
            "rows": zero_rows,
        }))
        files = _enumerate_release_files(staging)
        manifest = {
            "schema_version": SCHEMA_MANIFEST,
            "status": "AWAITING_EXTERNAL_EXPECTED_RELEASE_DIGEST",
            "authority_status": "NETWORK_FETCH_BLOCKED_UNPROVEN_TRANSITIVE_TLS_SHARED_LIBRARIES",
            "evidence_class": EVIDENCE_CLASS,
            "authorization": AUTHORIZATION,
            "season": 2023,
            "research_only": True,
            "betting_authorized": False,
            "prospective_evidence_claimed": False,
            "model_fitting_permitted": False,
            "network_fetch_permitted": False,
            "protected_data": PROTECTED_STATE,
            "source_counts": source_counts,
            "identity_games": len(authority.games),
            "statcast_games_observed": len(observed_statcast_games),
            "zero_pa_targets_preserved": len(zero_rows),
            "bindings": {
                "contract_sha256": contract_sha,
                "parser_source_sha256": parser_sha,
                "request_plan_sha256": sha256_file(staging / "request_plan.json"),
                "runtime_lock_sha256": sha256_file(staging / "runtime_lock.json"),
                "schedule_manifest_sha256": authority.schedule_manifest_sha256,
                "identity_index_sha256": authority.identity_index_sha256,
                "zero_pa_identity_map_sha256": authority.zero_pa_map_sha256,
            },
            "files": files,
            "observed_release_digest": None,
            "external_expected_release_digest": None,
        }
        manifest["observed_release_digest"] = _release_digest(manifest)
        _write_bytes(staging, "manifest.json", canonical_json_bytes(manifest))
        os.replace(staging, output_dir)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validate_locked_runtime_document(value: Mapping[str, Any]) -> None:
    _strict_keys(
        value,
        {"schema_version", "python", "platform", "third_party_distributions", "stdlib_modules",
         "site_injection", "tls", "authority_status", "network_fetch_authorized", "boundary"},
        "runtime lock",
    )
    if value["schema_version"] != SCHEMA_LOCK or value["boundary"] != "EXACT_LOCAL_RUNTIME_WITH_TLS_AND_INJECTION_OBSERVATION_NETWORK_BLOCKED":
        raise SourceReleaseError("runtime lock schema or boundary changed")
    if value["authority_status"] != "BLOCKED_UNPROVEN_TRANSITIVE_TLS_SHARED_LIBRARIES" or value["network_fetch_authorized"] is not False:
        raise SourceReleaseError("runtime lock overstates network authority")
    if value["third_party_distributions"] != []:
        raise SourceReleaseError("runtime lock unexpectedly depends on third-party distributions")
    python = value["python"]
    if not isinstance(python, Mapping):
        raise SourceReleaseError("runtime lock Python identity is malformed")
    _strict_keys(python, {"implementation", "version", "executable_sha256", "build", "compiler"}, "runtime lock Python identity")
    if python["implementation"] != "cpython" or SHA256_RE.fullmatch(str(python["executable_sha256"])) is None:
        raise SourceReleaseError("runtime lock Python identity is invalid")
    if re.fullmatch(r"3\.[0-9]+\.[0-9]+", str(python["version"])) is None:
        raise SourceReleaseError("runtime lock Python version is invalid")
    platform_identity = value["platform"]
    if not isinstance(platform_identity, Mapping):
        raise SourceReleaseError("runtime platform identity is malformed")
    _strict_keys(platform_identity, {"system", "release", "version", "machine", "architecture"}, "runtime platform identity")
    modules = value["stdlib_modules"]
    if not isinstance(modules, list) or [entry.get("name") for entry in modules if isinstance(entry, Mapping)] != list(RUNTIME_MODULES):
        raise SourceReleaseError("runtime lock module set or order changed")
    for entry in modules:
        if not isinstance(entry, Mapping):
            raise SourceReleaseError("runtime lock module identity is malformed")
        _strict_keys(entry, {"name", "origin_kind", "sha256"}, "runtime lock module identity")
        if entry["origin_kind"] == "file":
            if SHA256_RE.fullmatch(str(entry["sha256"])) is None:
                raise SourceReleaseError("runtime lock module hash is invalid")
        elif entry["sha256"] != "" or entry["origin_kind"] not in {"built-in", "frozen"}:
            raise SourceReleaseError("runtime lock non-file module identity is invalid")
    site_identity = value["site_injection"]
    if not isinstance(site_identity, Mapping):
        raise SourceReleaseError("runtime site-injection identity is malformed")
    _strict_keys(site_identity, {"sys_path", "site_roots", "pth_files", "enable_user_site", "customizations"}, "runtime site-injection identity")
    for group in ("sys_path", "site_roots"):
        if not isinstance(site_identity[group], list):
            raise SourceReleaseError(f"runtime {group} identity is malformed")
        for entry in site_identity[group]:
            if not isinstance(entry, Mapping):
                raise SourceReleaseError(f"runtime {group} entry is malformed")
            _strict_keys(entry, {"path", "exists", "kind", "reparse", "sha256"}, f"runtime {group} entry")
    if not isinstance(site_identity["pth_files"], list):
        raise SourceReleaseError("runtime pth-file identity is malformed")
    for entry in site_identity["pth_files"]:
        if not isinstance(entry, Mapping):
            raise SourceReleaseError("runtime pth-file entry is malformed")
        _strict_keys(entry, {"path", "bytes", "reparse", "sha256"}, "runtime pth-file entry")
    if not isinstance(site_identity["customizations"], list):
        raise SourceReleaseError("runtime customization identity is malformed")
    for entry in site_identity["customizations"]:
        if not isinstance(entry, Mapping):
            raise SourceReleaseError("runtime customization entry is malformed")
        _strict_keys(entry, {"name", "loaded", "origin", "reparse", "sha256"}, "runtime customization entry")
    tls = value["tls"]
    if not isinstance(tls, Mapping):
        raise SourceReleaseError("runtime TLS identity is malformed")
    _strict_keys(
        tls,
        {"openssl_version", "openssl_version_info", "ssl_module", "hashlib_module",
         "default_verify_paths", "environment_overrides", "transitive_shared_library_resolution_proven"},
        "runtime TLS identity",
    )
    if tls["transitive_shared_library_resolution_proven"] is not False:
        raise SourceReleaseError("runtime lock falsely claims transitive TLS authority")
    verify_paths = tls["default_verify_paths"]
    if not isinstance(verify_paths, Mapping):
        raise SourceReleaseError("runtime TLS verify-path identity is malformed")
    _strict_keys(
        verify_paths,
        {"cafile", "capath", "openssl_cafile_env", "openssl_cafile", "openssl_capath_env", "openssl_capath"},
        "runtime TLS verify-path identity",
    )
    for name in ("cafile", "capath"):
        entry = verify_paths[name]
        if not isinstance(entry, Mapping):
            raise SourceReleaseError("runtime TLS CA path identity is malformed")
        _strict_keys(entry, {"path", "exists", "kind", "reparse", "sha256"}, "runtime TLS CA path identity")


def _revalidate_release_semantics(
    release_root: Path, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    contract_path = _guard_child(release_root, "authority/source_contract.json", "release contract")
    parser_path = _guard_child(release_root, "authority/parser_source.py", "release parser source")
    plan_path = _guard_child(release_root, "request_plan.json", "release request plan")
    runtime_path = _guard_child(release_root, "runtime_lock.json", "release runtime lock")
    bindings = manifest.get("bindings")
    if not isinstance(bindings, Mapping):
        raise SourceReleaseError("release bindings are malformed")
    _strict_keys(
        bindings,
        {"contract_sha256", "parser_source_sha256", "request_plan_sha256", "runtime_lock_sha256",
         "schedule_manifest_sha256", "identity_index_sha256", "zero_pa_identity_map_sha256"},
        "release bindings",
    )
    expected_bindings = {
        "contract_sha256": sha256_file(contract_path),
        "parser_source_sha256": sha256_file(parser_path),
        "request_plan_sha256": sha256_file(plan_path),
        "runtime_lock_sha256": sha256_file(runtime_path),
        "schedule_manifest_sha256": sha256_file(_guard_child(release_root, "authority/schedule/manifest.json", "release schedule manifest")),
        "identity_index_sha256": sha256_file(_guard_child(release_root, "authority/identity_index.json", "release identity index")),
        "zero_pa_identity_map_sha256": sha256_file(_guard_child(release_root, "authority/zero_pa_identity_map.json", "release zero-PA map")),
    }
    if dict(bindings) != expected_bindings:
        raise SourceReleaseError("release authority binding differs from retained bytes")
    if sha256_file(Path(__file__)) != expected_bindings["parser_source_sha256"]:
        raise SourceReleaseError("active verifier source differs from retained parser source")
    contract = load_contract(contract_path)
    plan = load_plan(plan_path, contract, allow_canonical_requests=True)
    authority = load_authority_bundle(
        schedule_manifest_path=release_root / "authority/schedule/manifest.json",
        expected_schedule_manifest_digest=expected_bindings["schedule_manifest_sha256"],
        identity_index_path=release_root / "authority/identity_index.json",
        expected_identity_index_digest=expected_bindings["identity_index_sha256"],
        zero_pa_map_path=release_root / "authority/zero_pa_identity_map.json",
        expected_zero_pa_map_digest=expected_bindings["zero_pa_identity_map_sha256"],
    )
    validate_plan_authority(plan, authority)
    game_by_pk = {game["game_pk"]: game for game in authority.games}
    prior_zero_by_identity = {
        (target["game_pk"], target["player_id"]): target["source_response_sha256"]
        for target in authority.zero_targets
    }
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    if not isinstance(runtime, Mapping):
        raise SourceReleaseError("release runtime lock root is malformed")
    _validate_locked_runtime_document(runtime)
    if runtime != observed_runtime_lock():
        raise SourceReleaseError("active verifier runtime differs from retained exact runtime lock")
    source_counts = {name: 0 for name in contract["sources"]}
    zero_rows: list[dict[str, Any]] = []
    observed_statcast_games: set[int] = set()
    expected_headers = {
        "User-Agent": "baseball-predictor-historical-source-release/1.0",
        "Accept-Encoding": "identity",
    }
    for request in plan["requests"]:
        extension = "csv" if request["source_kind"] == "baseball_savant_statcast_csv" else "json"
        base = f"transport/{request['source_kind']}/{request['request_id']}"
        raw_relative = f"{base}/response.{extension}"
        receipt_relative = f"{base}/receipt.json"
        raw_path = _guard_child(release_root, raw_relative, "release response")
        receipt_path = _guard_child(release_root, receipt_relative, "release receipt")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not isinstance(receipt, Mapping):
            raise SourceReleaseError("release receipt root is malformed")
        _strict_keys(
            receipt,
            {"schema_version", "evidence_class", "authorization", "prospective_evidence_claimed",
             "request", "request_headers", "response", "parser", "semantics", "protected_data"},
            "release receipt",
        )
        if receipt["schema_version"] != SCHEMA_RECEIPT or receipt["evidence_class"] != EVIDENCE_CLASS:
            raise SourceReleaseError("release receipt schema or evidence class changed")
        if receipt["authorization"] != AUTHORIZATION or receipt["prospective_evidence_claimed"] is not False:
            raise SourceReleaseError("release receipt makes a prohibited claim")
        if receipt["protected_data"] != PROTECTED_STATE or receipt["request"] != request:
            raise SourceReleaseError("release receipt scope or request identity changed")
        if receipt["request_headers"] != expected_headers:
            raise SourceReleaseError("release request headers changed")
        response = receipt["response"]
        if not isinstance(response, Mapping):
            raise SourceReleaseError("release response receipt is malformed")
        _strict_keys(
            response,
            {"status", "content_type", "observed_at_utc", "requested_at_utc", "final_url", "headers",
             "body_path", "body_bytes", "body_sha256"},
            "release response receipt",
        )
        requested_at = _canonical_utc(response["requested_at_utc"], "receipt requested_at_utc")
        observed_at = _canonical_utc(response["observed_at_utc"], "receipt observed_at_utc")
        if observed_at < requested_at:
            raise SourceReleaseError("release receipt response precedes request")
        if response["status"] != 200 or response["final_url"] != request["full_url"]:
            raise SourceReleaseError("release receipt transport identity changed")
        if response["body_path"] != raw_relative:
            raise SourceReleaseError("release receipt body path changed")
        body = raw_path.read_bytes()
        if response["body_bytes"] != len(body) or response["body_sha256"] != sha256_bytes(body):
            raise SourceReleaseError("release receipt body binding changed")
        if not isinstance(response["headers"], Mapping) or _content_type(response["headers"]) != response["content_type"]:
            raise SourceReleaseError("release receipt response headers disagree with content type")
        allowed_types = contract["sources"][request["source_kind"]]["content_type_prefixes"]
        if response["content_type"] not in allowed_types:
            raise SourceReleaseError("release receipt content type is not allowlisted")
        parser = receipt["parser"]
        if parser != {"parser_id": request["parser_id"], "source_sha256": expected_bindings["parser_source_sha256"]}:
            raise SourceReleaseError("release receipt parser identity changed")
        if request["source_kind"] == "baseball_savant_statcast_csv":
            semantics = _validate_statcast(body, request["expected"], game_by_pk=game_by_pk)
            observed_statcast_games.update(semantics["game_pks"])
        else:
            semantics = _validate_mlb_feed(body, request["expected"])
            for player_id in semantics["zero_pa_player_ids"]:
                zero_rows.append({
                    "game_pk": semantics["game_pk"], "official_date": semantics["official_date"],
                    "player_id": player_id, "request_id": request["request_id"],
                    "response_sha256": sha256_bytes(body),
                    "prior_source_response_sha256": prior_zero_by_identity[(semantics["game_pk"], player_id)],
                })
        if receipt["semantics"] != semantics:
            raise SourceReleaseError("release receipt claimed semantics differ from parsed bytes")
        source_counts[request["source_kind"]] += 1
    if manifest.get("source_counts") != source_counts:
        raise SourceReleaseError("release source counts changed")
    if observed_statcast_games != set(game_by_pk):
        raise SourceReleaseError("release Statcast responses do not exactly cover the identity index")
    if manifest.get("identity_games") != len(authority.games) or manifest.get("statcast_games_observed") != len(observed_statcast_games):
        raise SourceReleaseError("release manifest identity coverage changed")
    zero_rows.sort(key=lambda row: (row["official_date"], row["game_pk"], row["player_id"]))
    zero_path = _guard_child(release_root, "derived/zero_pa_evidence.json", "release zero-PA evidence")
    zero = json.loads(zero_path.read_text(encoding="utf-8"))
    expected_zero = {"schema_version": "direct-batter-pa-zero-pa-preservation-v1", "rows": zero_rows}
    if zero != expected_zero or manifest.get("zero_pa_targets_preserved") != len(zero_rows):
        raise SourceReleaseError("release zero-PA evidence differs from official response semantics")
    return {
        "requests_revalidated": len(plan["requests"]),
        "identity_games_revalidated": len(authority.games),
        "statcast_games_revalidated": len(observed_statcast_games),
        "zero_pa_targets_revalidated": len(zero_rows),
    }


def verify_release(*, release_dir: Path, expected_release_digest: str, output_path: Path) -> dict[str, Any]:
    release_root = _guard_input_dir(release_dir, "source release")
    output_path = _guard_output_path(output_path, "external verification output")
    if SHA256_RE.fullmatch(expected_release_digest or "") is None:
        raise SourceReleaseError("an external expected release digest is required")
    try:
        output_path.relative_to(release_root)
    except ValueError:
        pass
    else:
        raise SourceReleaseError("external verification must be written outside the release")
    manifest_path = _guard_child(release_root, "manifest.json", "release manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise SourceReleaseError("release manifest root is malformed")
    _manifest_identity(manifest)
    files = _enumerate_release_files(release_root)
    if files != manifest.get("files"):
        raise SourceReleaseError("release exact file set, bytes, or hashes changed")
    observed = _release_digest(manifest)
    if observed != manifest.get("observed_release_digest"):
        raise SourceReleaseError("release manifest observed digest is internally inconsistent")
    if observed != expected_release_digest:
        raise SourceReleaseError("release differs from external expected digest")
    semantic_validation = _revalidate_release_semantics(release_root, manifest)
    verification = {
        "schema_version": SCHEMA_VERIFICATION,
        "decision": "SOURCE_RELEASE_EXTERNALLY_DIGEST_VERIFIED_NOT_MODEL_QUALIFIED",
        "release_dir_name": release_root.name,
        "expected_release_digest": expected_release_digest,
        "observed_release_digest": observed,
        "manifest_sha256": sha256_file(manifest_path),
        "verifier_source_sha256": sha256_file(Path(__file__)),
        "file_count": len(files),
        "semantic_validation": semantic_validation,
        "season": 2023,
        "evidence_class": EVIDENCE_CLASS,
        "authorization": AUTHORIZATION,
        "model_fitting_authorized": False,
        "betting_authorized": False,
        "protected_data": PROTECTED_STATE,
    }
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite external verification: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(canonical_json_bytes(verification))
    return verification


def _network_transport_allowed(allow_network: bool) -> OfflineFixtureTransport:
    del allow_network
    raise SourceReleaseError(
        "network fetch remains blocked: transitive TLS shared-library authority is unproven"
    )


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    lock = sub.add_parser("make-runtime-lock", help="observe the exact stdlib runtime boundary")
    lock.add_argument("--output", required=True, type=Path)
    build = sub.add_parser("build", help="capture a new immutable 2023 source release")
    build.add_argument("--request-plan", required=True, type=Path)
    build.add_argument("--contract", default=DEFAULT_CONTRACT, type=Path)
    build.add_argument("--runtime-lock", required=True, type=Path)
    build.add_argument("--expected-runtime-lock-digest", required=True)
    build.add_argument("--schedule-manifest", required=True, type=Path)
    build.add_argument("--expected-schedule-manifest-digest", required=True)
    build.add_argument("--identity-index", required=True, type=Path)
    build.add_argument("--expected-identity-index-digest", required=True)
    build.add_argument("--zero-pa-map", required=True, type=Path)
    build.add_argument("--expected-zero-pa-map-digest", required=True)
    build.add_argument("--output-dir", required=True, type=Path)
    build.add_argument("--allow-network", action="store_true")
    build.add_argument("--request-delay-seconds", type=float, default=1.0)
    verify = sub.add_parser("verify", help="verify against an independently supplied digest")
    verify.add_argument("--release-dir", required=True, type=Path)
    verify.add_argument("--expected-release-digest", required=True)
    verify.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "make-runtime-lock":
        digest = write_runtime_lock(args.output)
        print(json.dumps({"status": "RUNTIME_LOCK_OBSERVED_NOT_EXTERNALLY_TRUSTED", "sha256": digest}, sort_keys=True))
        return 0
    if args.command == "build":
        manifest = build_release(
            plan_path=args.request_plan, contract_path=args.contract,
            runtime_lock_path=args.runtime_lock,
            expected_runtime_lock_digest=args.expected_runtime_lock_digest,
            schedule_manifest_path=args.schedule_manifest,
            expected_schedule_manifest_digest=args.expected_schedule_manifest_digest,
            identity_index_path=args.identity_index,
            expected_identity_index_digest=args.expected_identity_index_digest,
            zero_pa_map_path=args.zero_pa_map,
            expected_zero_pa_map_digest=args.expected_zero_pa_map_digest,
            output_dir=args.output_dir, transport=_network_transport_allowed(args.allow_network),
            request_delay_seconds=args.request_delay_seconds,
        )
        print(json.dumps({"status": manifest["status"], "observed_release_digest": manifest["observed_release_digest"]}, sort_keys=True))
        return 0
    verification = verify_release(
        release_dir=args.release_dir, expected_release_digest=args.expected_release_digest,
        output_path=args.output,
    )
    print(json.dumps({"decision": verification["decision"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
