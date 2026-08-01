"""Capture only raw official 2023 MLB final-boxscore bytes.

The certified PA-volume schedule index supplies the exact game universe.  This
runner neither recaptures the schedule nor constructs labels, features,
models, scores, probabilities, or prospective evidence.  It reuses the
already-tested immutable receipt, retry, pacing, atomic-publication, and
runtime-attestation machinery from the PA-volume collector under a distinct
plan/receipt/capture schema.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Iterator, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.capture_pa_volume_official_source_v1 as base
from scripts.capture_direct_batter_pa_source_transport_v2 import (
    HTTPSHistoricalTransport,
    authorize_runtime,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from src.evaluation.pa_volume_historical_source_access_v1 import (
    VerifiedHistoricalSourceAccess,
)
from src.evaluation.shared_pa_outcome_historical_source_access_v1 import (
    verify_outcome_historical_source_access_authorization,
)


PLAN_SCHEMA = "shared-pa-outcome-official-boxscore-capture-plan-v1"
CAPTURE_SCHEMA = "shared-pa-outcome-official-boxscore-capture-v1"
RECEIPT_SCHEMA = "shared-pa-outcome-official-boxscore-http-receipt-v1"
PLAN_STATUS = "LOCKED_OFFICIAL_2023_FINAL_BOXSCORE_CAPTURE_PLAN"
EXPECTED_GAMES = 2430
MINIMUM_REQUEST_INTERVAL_SECONDS = 1.10
MAXIMUM_ATTEMPTS = 4
MAX_RESPONSE_BYTES = 5_000_000
DEFAULT_OVERALL_TIMEOUT_SECONDS = 21_600.0
ENDPOINT_TEMPLATE = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
CERTIFIED_SCHEDULE_INDEX_SCHEMA = "pa-volume-2023-schedule-index-v1"
PROTECTED = dict(base.PROTECTED)


class OutcomeBoxscoreCaptureError(ValueError):
    """The exact raw-only boxscore capture contract was violated."""


def source_files() -> list[dict[str, str]]:
    paths = [
        ROOT / "scripts/capture_direct_batter_pa_source_transport_v2.py",
        ROOT / "scripts/capture_pa_volume_official_source_v1.py",
        Path(__file__).resolve(),
        ROOT / "src/evaluation/pa_volume_historical_source_access_v1.py",
        ROOT / "src/evaluation/shared_pa_outcome_historical_source_access_v1.py",
    ]
    return [
        {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256_file(path)}
        for path in paths
    ]


def source_bundle_sha256() -> str:
    return sha256_bytes(canonical_json_bytes(source_files()))


def _safe_input(path: Path, label: str) -> Path:
    candidate = Path(os.path.abspath(os.fspath(path)))
    if ".." in path.parts or not candidate.is_file() or candidate.is_symlink():
        raise OutcomeBoxscoreCaptureError(f"{label} is missing or unsafe")
    return candidate


def _load_mapping(path: Path, label: str) -> Mapping[str, Any]:
    source = _safe_input(path, label)
    try:
        value = json.loads(source.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OutcomeBoxscoreCaptureError(f"{label} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise OutcomeBoxscoreCaptureError(f"{label} root is malformed")
    return value


def _canonical_date(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 10 or value[:4] != "2023":
        raise OutcomeBoxscoreCaptureError("schedule official_date is outside 2023")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise OutcomeBoxscoreCaptureError("schedule official_date is invalid") from exc
    if parsed.isoformat() != value:
        raise OutcomeBoxscoreCaptureError("schedule official_date is not canonical")
    return value


def build_boxscore_plan(*, certified_schedule_index: Path) -> dict[str, Any]:
    path = _safe_input(certified_schedule_index, "certified schedule index")
    document = _load_mapping(path, "certified schedule index")
    if (
        document.get("schema_version") != CERTIFIED_SCHEDULE_INDEX_SCHEMA
        or document.get("season") != 2023
    ):
        raise OutcomeBoxscoreCaptureError("certified schedule index identity differs")
    games = document.get("games")
    if not isinstance(games, list) or len(games) != EXPECTED_GAMES:
        raise OutcomeBoxscoreCaptureError("certified schedule game count differs")
    requests: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in games:
        if not isinstance(raw, Mapping) or set(raw) != {
            "game_pk",
            "official_date",
            "away_team_id",
            "home_team_id",
            "game_type",
        }:
            raise OutcomeBoxscoreCaptureError("certified schedule row schema differs")
        game_pk = raw.get("game_pk")
        away = raw.get("away_team_id")
        home = raw.get("home_team_id")
        if (
            isinstance(game_pk, bool)
            or not isinstance(game_pk, int)
            or game_pk <= 0
            or game_pk in seen
            or raw.get("game_type") != "R"
            or any(
                isinstance(team_id, bool)
                or not isinstance(team_id, int)
                or team_id <= 0
                for team_id in (away, home)
            )
            or away == home
        ):
            raise OutcomeBoxscoreCaptureError("certified schedule identity differs")
        seen.add(game_pk)
        expected = {
            "game_pk": game_pk,
            "official_date": _canonical_date(raw.get("official_date")),
            "away_team_id": away,
            "home_team_id": home,
        }
        requests.append(
            base._request(
                f"game-{game_pk}", ENDPOINT_TEMPLATE.format(game_pk=game_pk), expected
            )
        )
    requests.sort(key=lambda row: int(row["request_id"].split("-", 1)[1]))
    return {
        "schema_version": PLAN_SCHEMA,
        "status": PLAN_STATUS,
        "authorization": base.AUTHORIZATION,
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "protected_data": PROTECTED,
        "certified_schedule_index_path": path.name,
        "certified_schedule_index_sha256": sha256_file(path),
        # Compatibility key used by the immutable capture manifest.  Its value
        # is the certified schedule-index digest, not a new schedule capture.
        "schedule_capture_digest": sha256_file(path),
        "endpoint_template": ENDPOINT_TEMPLATE,
        "query": None,
        "expected_game_count": EXPECTED_GAMES,
        "requests": requests,
    }


def validate_boxscore_plan(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "status",
        "authorization",
        "season",
        "research_only",
        "betting_authorized",
        "protected_data",
        "certified_schedule_index_path",
        "certified_schedule_index_sha256",
        "schedule_capture_digest",
        "endpoint_template",
        "query",
        "expected_game_count",
        "requests",
    }:
        raise OutcomeBoxscoreCaptureError("boxscore plan positive schema differs")
    if (
        value.get("schema_version") != PLAN_SCHEMA
        or value.get("status") != PLAN_STATUS
        or value.get("authorization") != base.AUTHORIZATION
        or value.get("season") != 2023
        or value.get("research_only") is not True
        or value.get("betting_authorized") is not False
        or value.get("protected_data") != PROTECTED
        or value.get("endpoint_template") != ENDPOINT_TEMPLATE
        or value.get("query") is not None
        or value.get("expected_game_count") != EXPECTED_GAMES
        or value.get("schedule_capture_digest")
        != value.get("certified_schedule_index_sha256")
    ):
        raise OutcomeBoxscoreCaptureError("boxscore plan scope or identity differs")
    base._sha(value.get("certified_schedule_index_sha256"), "schedule index")
    requests = value.get("requests")
    if not isinstance(requests, list) or len(requests) != EXPECTED_GAMES:
        raise OutcomeBoxscoreCaptureError("boxscore plan game count differs")
    observed: list[int] = []
    for request in requests:
        if not isinstance(request, Mapping):
            raise OutcomeBoxscoreCaptureError("boxscore request is malformed")
        expected = request.get("expected")
        if not isinstance(expected, Mapping) or set(expected) != {
            "game_pk",
            "official_date",
            "away_team_id",
            "home_team_id",
        }:
            raise OutcomeBoxscoreCaptureError("boxscore expected identity differs")
        game_pk = expected.get("game_pk")
        _canonical_date(expected.get("official_date"))
        canonical = base._request(
            f"game-{game_pk}", ENDPOINT_TEMPLATE.format(game_pk=game_pk), expected
        )
        if dict(request) != canonical:
            raise OutcomeBoxscoreCaptureError("boxscore endpoint or identity differs")
        observed.append(int(game_pk))
    if observed != sorted(observed) or len(set(observed)) != EXPECTED_GAMES:
        raise OutcomeBoxscoreCaptureError("boxscore requests are not unique and sorted")
    return [dict(row) for row in requests]


@contextmanager
def _boxscore_profile() -> Iterator[None]:
    """Apply a process-local profile to the proven generic capture machinery."""
    names = {
        "PLAN_SCHEMA": PLAN_SCHEMA,
        "FEED_SCHEMA": CAPTURE_SCHEMA,
        "RECEIPT_SCHEMA": RECEIPT_SCHEMA,
        "EXPECTED_GAMES": EXPECTED_GAMES,
        "FEED_MAX_RESPONSE_BYTES": MAX_RESPONSE_BYTES,
        "MIN_REQUEST_INTERVAL_SECONDS": MINIMUM_REQUEST_INTERVAL_SECONDS,
        "_plan": validate_boxscore_plan,
    }
    old = {name: getattr(base, name) for name in names}
    try:
        for name, replacement in names.items():
            setattr(base, name, replacement)
        yield
    finally:
        for name, original in old.items():
            setattr(base, name, original)


def _adapt_source_access(access: Any) -> VerifiedHistoricalSourceAccess:
    return VerifiedHistoricalSourceAccess(
        authorization_id=access.authorization_id,
        authorization_path=access.authorization_path,
        authorization_file_sha256=access.authorization_file_sha256,
        runtime_policy_sha256=access.runtime_policy_sha256,
        source_bundle_sha256=access.source_bundle_sha256,
        authorized_at_utc=access.authorized_at_utc,
        valid_from_utc=access.valid_from_utc,
        expires_at_utc=access.expires_at_utc,
    )


def capture_boxscores(**kwargs: Any) -> dict[str, Any]:
    with _boxscore_profile():
        return base.capture_feeds(**kwargs)


def verify_boxscore_capture(
    root: Path, expected_digest: str | None = None
) -> dict[str, Any]:
    with _boxscore_profile():
        return base.verify_feed_capture(root, expected_digest)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("build-plan")
    plan.add_argument("--certified-schedule-index", required=True, type=Path)
    plan.add_argument("--output-plan", required=True, type=Path)
    capture = sub.add_parser("capture")
    capture.add_argument("--plan", required=True, type=Path)
    capture.add_argument("--runtime-policy", required=True, type=Path)
    capture.add_argument("--runtime-attestation", required=True, type=Path)
    capture.add_argument("--expected-runtime-attestation-sha256", required=True)
    capture.add_argument("--source-access-authorization", required=True, type=Path)
    capture.add_argument("--expected-source-access-authorization-sha256", required=True)
    capture.add_argument("--output-dir", required=True, type=Path)
    capture.add_argument("--work-dir", required=True, type=Path)
    capture.add_argument("--timeout-seconds", default=30.0, type=float)
    capture.add_argument(
        "--minimum-request-interval-seconds",
        default=MINIMUM_REQUEST_INTERVAL_SECONDS,
        type=float,
    )
    capture.add_argument("--maximum-attempts", default=MAXIMUM_ATTEMPTS, type=int)
    capture.add_argument(
        "--overall-timeout-seconds",
        default=DEFAULT_OVERALL_TIMEOUT_SECONDS,
        type=float,
    )
    verify = sub.add_parser("verify")
    verify.add_argument("--capture-dir", required=True, type=Path)
    verify.add_argument("--expected-capture-digest", required=True)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "build-plan":
        result = build_boxscore_plan(
            certified_schedule_index=args.certified_schedule_index
        )
        target = base._safe_new_file(args.output_plan, "boxscore plan")
        base._write_new(target, canonical_json_bytes(result))
        status = "BOXSCORE_PLAN_WRITTEN_NETWORK_NOT_ACCESSED"
        digest = sha256_bytes(canonical_json_bytes(result))
    elif args.command == "verify":
        result = verify_boxscore_capture(
            args.capture_dir, args.expected_capture_digest
        )
        status = str(result["status"])
        digest = str(result["observed_capture_digest"])
    else:
        plan_path = _safe_input(args.plan, "boxscore plan")
        plan = _load_mapping(plan_path, "boxscore plan")
        validate_boxscore_plan(plan)
        plan_sha256 = sha256_file(plan_path)
        runtime = authorize_runtime(
            attestation_path=args.runtime_attestation,
            expected_attestation_sha256=args.expected_runtime_attestation_sha256,
            policy_path=args.runtime_policy,
        )
        access_time = datetime.now(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        access = verify_outcome_historical_source_access_authorization(
            authorization_path=args.source_access_authorization,
            expected_authorization_sha256=(
                args.expected_source_access_authorization_sha256
            ),
            expected_runtime_policy_sha256=runtime.policy_sha256,
            expected_source_bundle_sha256=source_bundle_sha256(),
            expected_request_plan_sha256=plan_sha256,
            access_time_utc=access_time,
        )
        result = capture_boxscores(
            plan=plan,
            output_dir=args.output_dir,
            work_dir=args.work_dir,
            runtime=runtime,
            source_access=_adapt_source_access(access),
            source_bundle_sha256=source_bundle_sha256(),
            transport=HTTPSHistoricalTransport(),
            timeout_seconds=args.timeout_seconds,
            minimum_request_interval_seconds=(
                args.minimum_request_interval_seconds
            ),
            maximum_attempts=args.maximum_attempts,
            overall_timeout_seconds=args.overall_timeout_seconds,
        )
        status = str(result["status"])
        digest = str(result["observed_capture_digest"])
    print(
        json.dumps(
            {
                "schema_version": result.get("schema_version"),
                "status": status,
                "digest": digest,
                "network_accessed": args.command == "capture",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
