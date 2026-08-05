"""Create-once daily Baseball Savant Statcast CSV acquisition.

This module is intentionally limited to source acquisition and structural
validation. It preserves every byte and every returned column. It does not
construct features, fit models, generate probabilities, or decide which
Statcast measurements are predictive.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import tempfile
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
import urllib.error
import urllib.parse
import urllib.request


SOURCE_NAME = "baseball_savant_statcast_search_csv"
SOURCE_ENDPOINT = "https://baseballsavant.mlb.com/statcast_search/csv"
COLLECTOR_VERSION = "statcast-daily-raw-collector-v1"
RECEIPT_SCHEMA = "statcast-daily-raw-receipt-v1"
EXPECTED_GAMES_SCHEMA = "statcast-daily-expected-games-v1"
EXPECTED_GAMES_GENERATOR_VERSION = "mlb-schedule-expected-games-v1"
EXPECTED_GAMES_RECEIPT_SCHEMA = "statcast-daily-expected-games-receipt-v1"
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_ATTEMPTS = 3
RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
ALLOWED_MIME_TYPES = {"text/csv", "application/csv", "application/download"}
SAFE_RESPONSE_HEADERS = {
    "cache-control",
    "content-disposition",
    "content-encoding",
    "content-length",
    "content-type",
    "date",
    "etag",
    "last-modified",
    "retry-after",
}
REQUEST_HEADERS = {
    "Accept": "text/csv,application/csv,application/download;q=0.9",
    "Accept-Encoding": "identity",
    "User-Agent": "baseball-predictor-statcast-daily-raw/1.0",
}
REQUIRED_COLUMNS = {
    "game_date",
    "game_type",
    "batter",
    "pitcher",
    "game_pk",
    "at_bat_number",
    "pitch_number",
}
PITCH_ID_COLUMNS = (
    "game_pk",
    "at_bat_number",
    "pitch_number",
    "pitcher",
    "batter",
)
ERROR_MARKERS = (
    "baseball savant error",
    "statcast error",
    "request failed",
    "too many requests",
)
RETRIEVAL_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\Z")
POSITIVE_INTEGER_RE = re.compile(r"[1-9][0-9]*\Z")
PLAYED_SCHEDULE_STATUSES = {
    ("F", "Final"),
    ("FG", "Completed Early"),
    ("FR", "Completed Early"),
}
UNPLAYED_SCHEDULE_STATUSES = {
    ("CR", "Cancelled"),
    ("DI", "Postponed"),
    ("DR", "Postponed"),
}


class StatcastCollectorError(RuntimeError):
    """A collector safety or structural boundary was violated."""


class TransportFailure(StatcastCollectorError):
    """A sanitized transport failure with no provider response body."""

    def __init__(self, *, kind: str, retryable: bool, requested_at_utc: str, observed_at_utc: str) -> None:
        super().__init__(f"Statcast transport failed: {kind}")
        self.kind = kind
        self.retryable = retryable
        self.requested_at_utc = requested_at_utc
        self.observed_at_utc = observed_at_utc


@dataclass(frozen=True)
class CapturedResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    final_url: str
    requested_at_utc: str
    observed_at_utc: str


class Transport(Protocol):
    def fetch(self, url: str, *, timeout_seconds: float, max_bytes: int) -> CapturedResponse: ...


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_stamp(value: datetime | None = None) -> str:
    observed = value or datetime.now(timezone.utc)
    return observed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_official_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise StatcastCollectorError("official date must be YYYY-MM-DD") from exc
    if value != parsed.isoformat():
        raise StatcastCollectorError("official date is not canonical")
    return parsed


def _parse_utc(value: str, context: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise StatcastCollectorError(f"{context} is not valid UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise StatcastCollectorError(f"{context} is not UTC")
    return parsed.astimezone(timezone.utc)


def _query_items(official_date: date) -> list[tuple[str, str]]:
    day = official_date.isoformat()
    return [
        ("all", "true"),
        ("batter_stands", ""),
        ("game_date_gt", day),
        ("game_date_lt", day),
        ("group_by", "name"),
        ("hfAB", ""),
        ("hfBBL", ""),
        ("hfBBT", ""),
        ("hfFlag", ""),
        ("hfGT", "R|"),
        ("hfInn", ""),
        ("hfNewZones", ""),
        ("hfOuts", ""),
        ("hfPR", ""),
        ("hfPT", ""),
        ("hfRO", ""),
        ("hfSA", ""),
        ("hfSea", ""),
        ("hfSit", ""),
        ("hfZ", ""),
        ("home_road", ""),
        ("metric_1", ""),
        ("min_abs", "0"),
        ("min_pitches", "0"),
        ("min_results", "0"),
        ("opponent", ""),
        ("pitcher_throws", ""),
        ("player_event_sort", "h_launch_speed"),
        ("player_type", "pitcher"),
        ("position", ""),
        ("sort_col", "pitches"),
        ("sort_order", "desc"),
        ("stadium", ""),
        ("team", ""),
        ("type", "details"),
    ]


def build_request_url(official_date: str | date) -> str:
    day = parse_official_date(official_date)
    return f"{SOURCE_ENDPOINT}?{urllib.parse.urlencode(_query_items(day))}"


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).strip().lower()
        if name not in SAFE_RESPONSE_HEADERS:
            continue
        value = str(raw_value).strip()
        if name in result or "\r" in value or "\n" in value:
            raise StatcastCollectorError("unsafe or duplicate response header")
        result[name] = value
    return {name: result[name] for name in sorted(result)}


def _read_limited(handle: Any, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = handle.read(min(1024 * 1024, max_bytes + 1 - total))
        if not block:
            break
        if not isinstance(block, bytes):
            raise StatcastCollectorError("provider response did not return bytes")
        total += len(block)
        if total > max_bytes:
            raise StatcastCollectorError("provider response exceeds the byte limit")
        chunks.append(block)
    return b"".join(chunks)


class HTTPSStatcastTransport:
    """Direct TLS transport with no proxy use or redirect following."""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            return None

    def __init__(self) -> None:
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            self._NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
        )

    def fetch(self, url: str, *, timeout_seconds: float, max_bytes: int) -> CapturedResponse:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "baseballsavant.mlb.com" or parsed.path != "/statcast_search/csv":
            raise StatcastCollectorError("request escaped the exact Savant CSV endpoint")
        requested = utc_stamp()
        request = urllib.request.Request(url, method="GET", headers=REQUEST_HEADERS)
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                return CapturedResponse(
                    status=int(response.status),
                    body=_read_limited(response, max_bytes=max_bytes),
                    headers=_safe_headers(dict(response.headers.items())),
                    final_url=str(response.geturl()),
                    requested_at_utc=requested,
                    observed_at_utc=utc_stamp(),
                )
        except urllib.error.HTTPError as exc:
            return CapturedResponse(
                status=int(exc.code),
                body=_read_limited(exc, max_bytes=max_bytes),
                headers=_safe_headers(dict(exc.headers.items()) if exc.headers else {}),
                final_url=str(exc.geturl()),
                requested_at_utc=requested,
                observed_at_utc=utc_stamp(),
            )
        except urllib.error.URLError as exc:
            tls_failure = isinstance(exc.reason, ssl.SSLError)
            raise TransportFailure(
                kind="tls_failure" if tls_failure else "transport_io",
                retryable=not tls_failure,
                requested_at_utc=requested,
                observed_at_utc=utc_stamp(),
            ) from exc
        except ssl.SSLError as exc:
            raise TransportFailure(
                kind="tls_failure",
                retryable=False,
                requested_at_utc=requested,
                observed_at_utc=utc_stamp(),
            ) from exc
        except OSError as exc:
            raise TransportFailure(
                kind="transport_io",
                retryable=True,
                requested_at_utc=requested,
                observed_at_utc=utc_stamp(),
            ) from exc


def load_expected_games(path: Path) -> tuple[date, list[int], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatcastCollectorError("expected-games file is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "official_date",
        "regular_season_game_pks",
    }:
        raise StatcastCollectorError("expected-games file has missing or unexpected fields")
    if value["schema_version"] != EXPECTED_GAMES_SCHEMA:
        raise StatcastCollectorError("expected-games schema differs")
    official_date = parse_official_date(value["official_date"])
    game_pks = value["regular_season_game_pks"]
    if not isinstance(game_pks, list) or not game_pks:
        raise StatcastCollectorError("at least one expected regular-season game is required")
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in game_pks):
        raise StatcastCollectorError("expected game IDs must be positive integers")
    if game_pks != sorted(set(game_pks)):
        raise StatcastCollectorError("expected game IDs must be unique and sorted")
    return official_date, game_pks, sha256_bytes(raw)


def analyze_schedule_response(body: bytes, *, official_date: str | date) -> dict[str, Any]:
    """Derive the games that actually produced play on one official date.

    A season schedule can contain the same game more than once after a
    postponement. A later terminal played record wins over an earlier
    postponed record for the same gamePk. Cancelled or still-postponed games
    are preserved in the selection evidence but are not expected in Statcast.
    Unknown states fail closed.
    """
    day = parse_official_date(official_date)
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatcastCollectorError("schedule response is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("dates"), list):
        raise StatcastCollectorError("schedule response lacks a dates array")

    records: dict[int, list[dict[str, Any]]] = {}
    matching_entries = 0
    for date_group in value["dates"]:
        if not isinstance(date_group, dict) or not isinstance(date_group.get("games"), list):
            raise StatcastCollectorError("schedule date group is malformed")
        group_date = date_group.get("date")
        if not isinstance(group_date, str):
            raise StatcastCollectorError("schedule date group lacks a date")
        for game in date_group["games"]:
            if not isinstance(game, dict) or game.get("gameType") != "R":
                continue
            if game.get("officialDate") != day.isoformat():
                continue
            game_pk = game.get("gamePk")
            status = game.get("status")
            if isinstance(game_pk, bool) or not isinstance(game_pk, int) or game_pk <= 0:
                raise StatcastCollectorError("schedule gamePk is invalid")
            if not isinstance(status, dict):
                raise StatcastCollectorError("schedule game status is missing")
            status_code = status.get("statusCode")
            detailed_state = status.get("detailedState")
            abstract_state = status.get("abstractGameState")
            if not all(isinstance(item, str) and item for item in (status_code, detailed_state, abstract_state)):
                raise StatcastCollectorError("schedule game status fields are invalid")
            matching_entries += 1
            record = {
                "schedule_group_date": group_date,
                "status_code": status_code,
                "detailed_state": detailed_state,
                "abstract_state": abstract_state,
                "reason": status.get("reason") if isinstance(status.get("reason"), str) else None,
            }
            if record not in records.setdefault(game_pk, []):
                records[game_pk].append(record)

    if not records:
        raise StatcastCollectorError("schedule contains no regular-season games for the official date")

    included: list[int] = []
    excluded: list[dict[str, Any]] = []
    for game_pk in sorted(records):
        game_records = records[game_pk]
        pairs = {(item["status_code"], item["detailed_state"]) for item in game_records}
        unknown = sorted(pairs.difference(PLAYED_SCHEDULE_STATUSES | UNPLAYED_SCHEDULE_STATUSES))
        if unknown:
            raise StatcastCollectorError(
                f"schedule has an unrecognized terminal status for game {game_pk}: {unknown}"
            )
        if pairs.intersection(PLAYED_SCHEDULE_STATUSES):
            included.append(game_pk)
        else:
            excluded.append({"game_pk": game_pk, "records": game_records})

    if not included:
        raise StatcastCollectorError("schedule contains no played regular-season games for the official date")
    return {
        "official_date": day.isoformat(),
        "schedule_response_bytes": len(body),
        "schedule_response_sha256": sha256_bytes(body),
        "matching_schedule_entry_count": matching_entries,
        "unique_regular_season_game_count": len(records),
        "played_game_count": len(included),
        "played_game_pks": included,
        "excluded_unplayed_game_count": len(excluded),
        "excluded_unplayed_games": excluded,
    }


def create_expected_games_bundle_from_schedule(
    *,
    schedule_response_path: Path,
    official_date: str | date,
    output_dir: Path,
) -> dict[str, Any]:
    """Create one atomic, create-only expected-game manifest and receipt bundle."""
    raw = schedule_response_path.read_bytes()
    selection = analyze_schedule_response(raw, official_date=official_date)
    destination = output_dir.resolve()
    if destination.exists():
        raise StatcastCollectorError("create-only expected-games destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        manifest = {
            "schema_version": EXPECTED_GAMES_SCHEMA,
            "official_date": selection["official_date"],
            "regular_season_game_pks": selection["played_game_pks"],
        }
        manifest_bytes = canonical_json_bytes(manifest)
        _write_new(staging / "expected-games.json", manifest_bytes)
        receipt = {
            "schema_version": EXPECTED_GAMES_RECEIPT_SCHEMA,
            "generator_version": EXPECTED_GAMES_GENERATOR_VERSION,
            "source": {
                "path_name": schedule_response_path.name,
                "byte_count": len(raw),
                "sha256": sha256_bytes(raw),
            },
            "manifest": {
                "path": "expected-games.json",
                "byte_count": len(manifest_bytes),
                "sha256": sha256_bytes(manifest_bytes),
            },
            "selection": selection,
        }
        _write_new(staging / "receipt.json", canonical_json_bytes(receipt))
        os.replace(staging, destination)
        return receipt
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_expected_games_bundle(bundle_dir: Path, *, schedule_response_path: Path) -> dict[str, Any]:
    root = bundle_dir.resolve()
    receipt_path = root / "receipt.json"
    manifest_path = root / "expected-games.json"
    if not root.is_dir() or {path.name for path in root.iterdir()} != {"expected-games.json", "receipt.json"}:
        raise StatcastCollectorError("expected-games bundle file set differs")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatcastCollectorError("expected-games receipt is invalid") from exc
    if not isinstance(receipt, dict) or receipt.get("schema_version") != EXPECTED_GAMES_RECEIPT_SCHEMA:
        raise StatcastCollectorError("expected-games receipt schema differs")
    manifest_bytes = manifest_path.read_bytes()
    recorded_manifest = receipt.get("manifest")
    if not isinstance(recorded_manifest, dict) or recorded_manifest != {
        "path": "expected-games.json",
        "byte_count": len(manifest_bytes),
        "sha256": sha256_bytes(manifest_bytes),
    }:
        raise StatcastCollectorError("expected-games manifest identity differs")
    day, game_pks, manifest_sha = load_expected_games(manifest_path)
    if manifest_sha != recorded_manifest["sha256"]:
        raise StatcastCollectorError("expected-games manifest digest differs")
    raw = schedule_response_path.read_bytes()
    selection = analyze_schedule_response(raw, official_date=day)
    if selection != receipt.get("selection") or game_pks != selection["played_game_pks"]:
        raise StatcastCollectorError("expected-games schedule selection differs")
    if receipt.get("source") != {
        "path_name": schedule_response_path.name,
        "byte_count": len(raw),
        "sha256": sha256_bytes(raw),
    }:
        raise StatcastCollectorError("expected-games schedule source identity differs")
    return receipt


def _normalized_mime(headers: Mapping[str, str]) -> str:
    content_type = headers.get("content-type")
    if not isinstance(content_type, str) or not content_type:
        raise StatcastCollectorError("response lacks Content-Type")
    return content_type.split(";", 1)[0].strip().lower()


def _newline_metadata(body: bytes) -> dict[str, Any]:
    return {
        "utf8_bom_present": body.startswith(b"\xef\xbb\xbf"),
        "terminal_newline_present": body.endswith((b"\n", b"\r")),
        "terminal_newline_kind": (
            "CRLF" if body.endswith(b"\r\n") else "LF" if body.endswith(b"\n") else "CR" if body.endswith(b"\r") else "NONE"
        ),
    }


def analyze_csv(body: bytes, *, official_date: date, expected_game_pks: Sequence[int]) -> dict[str, Any]:
    if not body:
        raise StatcastCollectorError("CSV body is empty")
    try:
        text = body.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise StatcastCollectorError("CSV body is not valid UTF-8") from exc
    lowered = text.lower()
    if any(marker in lowered for marker in ("<!doctype html", "<html", "<body", "</html>")):
        raise StatcastCollectorError("provider returned an HTML body")
    if any(marker in lowered for marker in ERROR_MARKERS):
        raise StatcastCollectorError("provider returned a known error body")
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            raise StatcastCollectorError("provider returned JSON instead of CSV")
    try:
        rows = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(rows)
    except (StopIteration, csv.Error) as exc:
        raise StatcastCollectorError("CSV header is absent or malformed") from exc
    if not header or any(not name for name in header) or len(header) != len(set(header)):
        raise StatcastCollectorError("CSV header is empty or duplicated")
    missing_required = sorted(REQUIRED_COLUMNS.difference(header))
    if missing_required:
        raise StatcastCollectorError(f"CSV lacks required identity columns: {missing_required}")
    positions = {name: header.index(name) for name in REQUIRED_COLUMNS}
    identity_positions = [header.index(name) for name in PITCH_ID_COLUMNS]
    expected_games = set(expected_game_pks)
    observed_games: set[int] = set()
    identities: set[tuple[str, ...]] = set()
    row_count = 0
    min_game_date: str | None = None
    max_game_date: str | None = None
    try:
        for csv_row_number, row in enumerate(rows, start=2):
            if not row or all(value == "" for value in row):
                raise StatcastCollectorError(f"CSV contains an empty record at row {csv_row_number}")
            if len(row) != len(header):
                raise StatcastCollectorError(f"CSV field count differs at row {csv_row_number}")
            row_date = row[positions["game_date"]]
            if row_date != official_date.isoformat():
                raise StatcastCollectorError(f"CSV contains another game date at row {csv_row_number}")
            if row[positions["game_type"]] != "R":
                raise StatcastCollectorError(f"CSV contains a non-regular-season row at row {csv_row_number}")
            for name in REQUIRED_COLUMNS.difference({"game_date", "game_type"}):
                if POSITIVE_INTEGER_RE.fullmatch(row[positions[name]]) is None:
                    raise StatcastCollectorError(f"CSV has an invalid {name} at row {csv_row_number}")
            game_pk = int(row[positions["game_pk"]])
            observed_games.add(game_pk)
            identity = tuple(row[position] for position in identity_positions)
            if identity in identities:
                raise StatcastCollectorError(f"CSV has a duplicate pitch identity at row {csv_row_number}")
            identities.add(identity)
            row_count += 1
            min_game_date = row_date if min_game_date is None else min(min_game_date, row_date)
            max_game_date = row_date if max_game_date is None else max(max_game_date, row_date)
    except csv.Error as exc:
        raise StatcastCollectorError("CSV parser did not reach a clean EOF") from exc
    if row_count == 0:
        raise StatcastCollectorError("CSV has no pitch records")
    missing_games = sorted(expected_games.difference(observed_games))
    unexpected_games = sorted(observed_games.difference(expected_games))
    if missing_games or unexpected_games:
        raise StatcastCollectorError(
            f"CSV game universe differs: missing={missing_games}, unexpected={unexpected_games}"
        )
    return {
        "row_count": row_count,
        "column_count": len(header),
        "ordered_columns": header,
        "ordered_columns_sha256": sha256_bytes(("\n".join(header) + "\n").encode("utf-8")),
        "game_pk_count": len(observed_games),
        "game_pks": sorted(observed_games),
        "minimum_game_date": min_game_date,
        "maximum_game_date": max_game_date,
        "duplicate_pitch_identity_count": 0,
        "pitch_identity_columns": list(PITCH_ID_COLUMNS),
        **_newline_metadata(body),
    }


def compare_schema(current_columns: Sequence[str], previous_receipt: Mapping[str, Any] | None) -> dict[str, Any]:
    if previous_receipt is None:
        return {
            "status": "BASELINE_ESTABLISHED_NO_PRIOR_RECEIPT",
            "review_required": False,
            "added_columns": [],
            "removed_columns": [],
            "order_changed": False,
        }
    previous_csv = previous_receipt.get("csv_validation")
    if not isinstance(previous_csv, Mapping) or not isinstance(previous_csv.get("ordered_columns"), list):
        raise StatcastCollectorError("previous receipt lacks a valid ordered-column baseline")
    previous_columns = [str(value) for value in previous_csv["ordered_columns"]]
    added = [value for value in current_columns if value not in previous_columns]
    removed = [value for value in previous_columns if value not in current_columns]
    changed = list(current_columns) != previous_columns
    return {
        "status": "SCHEMA_UNCHANGED" if not changed else "SCHEMA_CHANGE_REQUIRES_REVIEW",
        "review_required": changed,
        "added_columns": added,
        "removed_columns": removed,
        "order_changed": changed and not added and not removed,
    }


def _write_new(path: Path, value: bytes | Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value if isinstance(value, bytes) else canonical_json_bytes(value)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _enumerate_files(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "receipt.json":
            result.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return result


def capture_daily_statcast(
    *,
    official_date: str | date,
    expected_game_pks: Sequence[int],
    expected_games_sha256: str,
    output_root: Path,
    retrieval_id: str,
    transport: Transport,
    previous_receipt: Mapping[str, Any] | None = None,
    timeout_seconds: float = 60.0,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    day = parse_official_date(official_date)
    if RETRIEVAL_ID_RE.fullmatch(retrieval_id or "") is None:
        raise StatcastCollectorError("retrieval ID is invalid")
    if isinstance(timeout_seconds, bool) or not 1 <= float(timeout_seconds) <= 120:
        raise StatcastCollectorError("timeout must be in [1,120] seconds")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise StatcastCollectorError(f"max attempts must be in [1,{MAX_ATTEMPTS}]")
    games = list(expected_game_pks)
    if not games or games != sorted(set(games)) or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in games):
        raise StatcastCollectorError("expected game IDs must be positive, unique, sorted integers")
    if re.fullmatch(r"[0-9a-f]{64}", expected_games_sha256 or "") is None:
        raise StatcastCollectorError("expected-games SHA-256 is invalid")
    destination = (
        output_root
        / "raw"
        / "savant"
        / "statcast"
        / f"game_date={day.isoformat()}"
        / f"retrieval_id={retrieval_id}"
    ).resolve()
    if destination.exists():
        raise StatcastCollectorError("create-only retrieval destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    url = build_request_url(day)
    attempts: list[dict[str, Any]] = []
    selected_attempt: int | None = None
    csv_validation: dict[str, Any] | None = None
    schema_comparison: dict[str, Any] | None = None
    terminal_status = "RAW_STATCAST_TRANSPORT_FAILED"
    try:
        for attempt_number in range(1, max_attempts + 1):
            attempt_dir = staging / f"attempt-{attempt_number:02d}"
            attempt_dir.mkdir()
            try:
                response = transport.fetch(url, timeout_seconds=float(timeout_seconds), max_bytes=MAX_RESPONSE_BYTES)
            except TransportFailure as exc:
                attempt = {
                    "attempt": attempt_number,
                    "result": "TRANSPORT_FAILURE_NO_RESPONSE_BODY",
                    "retryable": exc.retryable,
                    "error_kind": exc.kind,
                    "requested_at_utc": exc.requested_at_utc,
                    "observed_at_utc": exc.observed_at_utc,
                }
                _write_new(attempt_dir / "transport.json", attempt)
                attempts.append(attempt)
                if exc.retryable and attempt_number < max_attempts:
                    sleep(float(attempt_number))
                    continue
                break
            raw_relative = f"attempt-{attempt_number:02d}/response.csv"
            _write_new(staging / raw_relative, response.body)
            attempt = {
                "attempt": attempt_number,
                "result": "RESPONSE_PRESERVED",
                "status": response.status,
                "final_url": response.final_url,
                "requested_at_utc": response.requested_at_utc,
                "observed_at_utc": response.observed_at_utc,
                "headers": dict(response.headers),
                "http_client_read_to_eof": True,
                "body_path": raw_relative,
                "body_bytes": len(response.body),
                "body_sha256": sha256_bytes(response.body),
            }
            validation_error: str | None = None
            try:
                requested_at = _parse_utc(response.requested_at_utc, "requested_at_utc")
                observed_at = _parse_utc(response.observed_at_utc, "observed_at_utc")
                if observed_at < requested_at or observed_at > datetime.now(timezone.utc) + timedelta(minutes=5):
                    raise StatcastCollectorError("response timestamps are contradictory")
                if response.status != 200:
                    raise StatcastCollectorError(f"HTTP status is {response.status}")
                if response.final_url != url:
                    raise StatcastCollectorError("provider redirected or changed the request URL")
                if not isinstance(response.body, bytes) or not response.body or len(response.body) > MAX_RESPONSE_BYTES:
                    raise StatcastCollectorError("response body is empty, malformed, or oversized")
                mime = _normalized_mime(response.headers)
                if mime not in ALLOWED_MIME_TYPES:
                    raise StatcastCollectorError(f"response MIME is not allowed: {mime}")
                encoding = response.headers.get("content-encoding", "identity").lower()
                if encoding not in {"", "identity"}:
                    raise StatcastCollectorError("provider response is unexpectedly content-encoded")
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    if not declared_length.isascii() or not declared_length.isdigit() or int(declared_length) != len(response.body):
                        raise StatcastCollectorError("Content-Length differs from preserved bytes")
                csv_validation = analyze_csv(response.body, official_date=day, expected_game_pks=games)
                schema_comparison = compare_schema(csv_validation["ordered_columns"], previous_receipt)
            except StatcastCollectorError as exc:
                validation_error = str(exc)
            attempt["validation_error"] = validation_error
            _write_new(attempt_dir / "transport.json", attempt)
            attempts.append(attempt)
            if validation_error is None:
                selected_attempt = attempt_number
                terminal_status = (
                    "RAW_STATCAST_DATE_CAPTURED_SCHEMA_REVIEW_REQUIRED"
                    if schema_comparison and schema_comparison["review_required"]
                    else "RAW_STATCAST_DATE_CAPTURED"
                )
                break
            if response.status in RETRYABLE_HTTP_STATUSES and attempt_number < max_attempts:
                retry_after = response.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else float(attempt_number)
                sleep(min(delay, 30.0))
                continue
            terminal_status = "RAW_STATCAST_DATE_QUARANTINED"
            break
        receipt = {
            "schema_version": RECEIPT_SCHEMA,
            "collector_version": COLLECTOR_VERSION,
            "terminal_status": terminal_status,
            "source_name": SOURCE_NAME,
            "official_date": day.isoformat(),
            "retrieval_id": retrieval_id,
            "request": {
                "method": "GET",
                "endpoint": SOURCE_ENDPOINT,
                "full_url": url,
                "query": dict(_query_items(day)),
                "headers": REQUEST_HEADERS,
                "maximum_attempts": max_attempts,
                "timeout_seconds": float(timeout_seconds),
            },
            "expected_games": {
                "sha256": expected_games_sha256,
                "count": len(games),
                "game_pks": games,
            },
            "attempts": attempts,
            "selected_attempt": selected_attempt,
            "csv_validation": csv_validation,
            "schema_comparison": schema_comparison,
            "files": _enumerate_files(staging),
            "raw_response_preserved_unmodified": True,
            "feature_construction_performed": False,
            "model_fitting_performed": False,
            "probabilities_generated": False,
            "model_eligible": False,
        }
        _write_new(staging / "receipt.json", receipt)
        os.replace(staging, destination)
        return receipt
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_capture(capture_dir: Path) -> dict[str, Any]:
    root = capture_dir.resolve()
    receipt_path = root / "receipt.json"
    if not root.is_dir() or not receipt_path.is_file():
        raise StatcastCollectorError("capture directory or receipt is missing")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatcastCollectorError("capture receipt is invalid") from exc
    if not isinstance(receipt, dict) or receipt.get("schema_version") != RECEIPT_SCHEMA:
        raise StatcastCollectorError("capture receipt schema differs")
    if _enumerate_files(root) != receipt.get("files"):
        raise StatcastCollectorError("capture file set, bytes, or hashes differ")
    if receipt.get("source_name") != SOURCE_NAME:
        raise StatcastCollectorError("capture source identity differs")
    expected = receipt.get("expected_games")
    if not isinstance(expected, dict) or expected.get("count") != len(expected.get("game_pks", [])):
        raise StatcastCollectorError("expected-game binding differs")
    selected = receipt.get("selected_attempt")
    if selected is not None:
        raw = root / f"attempt-{int(selected):02d}" / "response.csv"
        if not raw.is_file():
            raise StatcastCollectorError("selected raw response is missing")
        observed = analyze_csv(
            raw.read_bytes(),
            official_date=parse_official_date(receipt["official_date"]),
            expected_game_pks=expected["game_pks"],
        )
        if observed != receipt.get("csv_validation"):
            raise StatcastCollectorError("retained CSV validation result differs")
    return receipt


__all__ = [
    "CapturedResponse",
    "HTTPSStatcastTransport",
    "StatcastCollectorError",
    "TransportFailure",
    "analyze_csv",
    "analyze_schedule_response",
    "build_request_url",
    "capture_daily_statcast",
    "compare_schema",
    "create_expected_games_bundle_from_schedule",
    "load_expected_games",
    "verify_capture",
    "verify_expected_games_bundle",
]
