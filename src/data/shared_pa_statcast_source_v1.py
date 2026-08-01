"""Fail-closed parser and count contracts for the shared-PA Statcast source.

This module does not fetch data, fit a model, or score a market.  It validates
raw captured CSV bytes and constructs strictly-prior, count-bearing engineering
feature rows for the source-package preflight.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


class StatcastSourceError(ValueError):
    """Raised when raw source evidence fails its locked contract."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "shared-pa-statcast-source-contract-v1":
        raise StatcastSourceError("Statcast source contract identity differs")
    if value.get("status") != "PREPARED_NOT_AUTHORIZED":
        raise StatcastSourceError("Statcast source contract status differs")
    if value.get("research_only") is not True or value.get("betting_authorized") is not False:
        raise StatcastSourceError("Statcast source contract boundary differs")
    if value.get("protected_boundaries", {}).get("external_requests_allowed_before_authorization") is not False:
        raise StatcastSourceError("Statcast source contract permits unauthorized requests")
    return value


def _positive_int(value: Any, label: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StatcastSourceError(f"{label} is not numeric") from exc
    if not math.isfinite(number) or number <= 0 or not number.is_integer():
        raise StatcastSourceError(f"{label} is not a positive integer")
    return int(number)


def _optional_float(value: Any, label: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StatcastSourceError(f"{label} is not numeric") from exc
    if not math.isfinite(number):
        raise StatcastSourceError(f"{label} is not finite")
    return number


def _zone(value: Any) -> int | None:
    number = _optional_float(value, "zone")
    if number is None:
        return None
    if not number.is_integer() or int(number) not in {*range(1, 10), *range(11, 15)}:
        raise StatcastSourceError("zone lies outside the locked 1-9 or 11-14 domain")
    return int(number)


def validate_raw_receipt(
    *, raw: bytes, receipt: Mapping[str, Any], expected_request: Mapping[str, Any],
    contract_sha256: str, parser_sha256: str, request_plan_sha256: str,
) -> None:
    required = {
        "schema_version", "request", "request_plan_sha256", "source_contract_sha256",
        "parser_sha256", "attempt_number", "request_started_at_utc", "observed_at_utc",
        "http_status", "response_headers", "byte_count", "sha256", "terminal_state",
    }
    if set(receipt) != required:
        raise StatcastSourceError("raw receipt has missing or unexpected fields")
    if receipt["schema_version"] != "shared-pa-statcast-raw-receipt-v1":
        raise StatcastSourceError("raw receipt schema differs")
    if receipt["request"] != expected_request:
        raise StatcastSourceError("raw receipt request identity differs")
    if receipt["source_contract_sha256"] != contract_sha256:
        raise StatcastSourceError("raw receipt contract binding differs")
    if receipt["request_plan_sha256"] != request_plan_sha256:
        raise StatcastSourceError("raw receipt request-plan binding differs")
    if receipt["parser_sha256"] != parser_sha256:
        raise StatcastSourceError("raw receipt parser binding differs")
    if receipt["terminal_state"] != "SUCCESS" or receipt["http_status"] != 200:
        raise StatcastSourceError("raw receipt is not a successful terminal receipt")
    if not isinstance(receipt["attempt_number"], int) or not 1 <= receipt["attempt_number"] <= 4:
        raise StatcastSourceError("raw receipt attempt number is invalid")
    try:
        started = datetime.fromisoformat(receipt["request_started_at_utc"].replace("Z", "+00:00"))
        observed = datetime.fromisoformat(receipt["observed_at_utc"].replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise StatcastSourceError("raw receipt timestamp is invalid") from exc
    if started.tzinfo is None or observed.tzinfo is None or started.utcoffset() != timezone.utc.utcoffset(started) or observed < started:
        raise StatcastSourceError("raw receipt timestamps are unordered or non-UTC")
    if "csv" not in str(receipt["response_headers"].get("content-type", "")).lower():
        raise StatcastSourceError("raw receipt content type is not CSV")
    if receipt["byte_count"] != len(raw) or receipt["sha256"] != sha256_bytes(raw):
        raise StatcastSourceError("raw receipt byte binding differs")
    content_length = receipt["response_headers"].get("content-length")
    if content_length not in (None, "") and int(content_length) != len(raw):
        raise StatcastSourceError("raw receipt Content-Length differs from retained bytes")


def parse_csv_bytes(
    raw: bytes,
    *,
    contract: Mapping[str, Any],
    expected_date: str,
    certified_games: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Parse one exact daily response and enforce event/source identity."""

    if not raw or b"\x00" in raw:
        raise StatcastSourceError("raw CSV is empty or contains NUL")
    if not raw.endswith((b"\n", b"\r")):
        raise StatcastSourceError("raw CSV lacks a terminal newline")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StatcastSourceError("raw CSV is not valid UTF-8") from exc
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise StatcastSourceError("raw CSV has no header") from exc
    if not header or len(header) != len(set(header)):
        raise StatcastSourceError("raw CSV header is empty or duplicated")
    if header != list(contract["fixture_observed_complete_header"]):
        raise StatcastSourceError("raw CSV ordered source schema differs from the prepared contract")
    required = set(contract["required_raw_fields"])
    if not required.issubset(header):
        raise StatcastSourceError("raw CSV lacks required fields")
    rows: list[dict[str, Any]] = []
    identities: set[tuple[int, int, int]] = set()
    observed_games: set[int] = set()
    observed_sides: dict[int, set[str]] = {}
    for index, values in enumerate(reader, 2):
        if len(values) != len(header):
            raise StatcastSourceError(f"raw CSV row {index} is truncated or ragged")
        row = dict(zip(header, values))
        if row["game_type"] != "R":
            raise StatcastSourceError("raw CSV contains a non-regular-season row")
        if row["game_date"] != expected_date:
            raise StatcastSourceError("raw CSV date differs from request partition")
        game_pk = _positive_int(row["game_pk"], "game_pk")
        at_bat = _positive_int(row["at_bat_number"], "at_bat_number")
        pitch = _positive_int(row["pitch_number"], "pitch_number")
        identity = (game_pk, at_bat, pitch)
        if identity in identities:
            raise StatcastSourceError("duplicate Statcast pitch event identity")
        identities.add(identity)
        game = certified_games.get(game_pk)
        if game is None:
            raise StatcastSourceError("game_pk is absent from the certified game universe")
        if game.get("official_date") != expected_date:
            raise StatcastSourceError("Statcast game date differs from certified official date")
        if row["home_team"] != game.get("home_team_code") or row["away_team"] != game.get("away_team_code"):
            raise StatcastSourceError("Statcast team codes differ from certified game identity")
        batter = _positive_int(row["batter"], "batter")
        pitcher = _positive_int(row["pitcher"], "pitcher")
        if not row["home_team"] or not row["away_team"] or row["home_team"] == row["away_team"]:
            raise StatcastSourceError("home/away source identities are missing or contradictory")
        if row["inning_topbot"] not in {"Top", "Bot"}:
            raise StatcastSourceError("inning_topbot is missing or invalid")
        if row.get("stand") not in {"R", "L"} or row.get("p_throws") not in {"R", "L"}:
            raise StatcastSourceError("batter or pitcher handedness is missing or invalid")
        observed_games.add(game_pk)
        observed_sides.setdefault(game_pk, set()).add(str(row["inning_topbot"]))
        parsed = dict(row)
        parsed.update({
            "game_pk": game_pk,
            "at_bat_number": at_bat,
            "pitch_number": pitch,
            "batter": batter,
            "pitcher": pitcher,
            "zone": _zone(row["zone"]),
            "launch_speed": _optional_float(row["launch_speed"], "launch_speed"),
            "launch_angle": _optional_float(row["launch_angle"], "launch_angle"),
            "launch_speed_angle": _optional_float(row["launch_speed_angle"], "launch_speed_angle"),
        })
        rows.append(parsed)
    if not rows:
        raise StatcastSourceError("raw CSV contains no source rows")
    if observed_games != set(certified_games):
        raise StatcastSourceError("daily response does not exactly cover certified games")
    if any(observed_sides.get(game_pk) != {"Top", "Bot"} for game_pk in certified_games):
        raise StatcastSourceError("daily response does not cover both batting sides for every game")
    return rows


def plate_discipline_counts(rows: Iterable[Mapping[str, Any]], contract: Mapping[str, Any]) -> dict[str, int]:
    categories = contract["plate_discipline"]["categories"]
    inverse: dict[str, str] = {}
    for category, descriptions in categories.items():
        for description in descriptions:
            if description in inverse:
                raise StatcastSourceError("description mapping is not mutually exclusive")
            inverse[description] = category
    counts = Counter({
        "pitch_rows": 0, "physical_pitch_rows": 0, "swing_count": 0,
        "whiff_count": 0, "contact_count": 0, "take_count": 0,
        "called_strike_count": 0, "called_ball_count": 0,
        "hbp_take_count": 0, "pitchout_take_count": 0,
        "automatic_ball_count": 0, "automatic_strike_count": 0,
        "zone_opportunity_count": 0, "zone_swing_count": 0,
        "chase_opportunity_count": 0, "chase_swing_count": 0,
        "missing_zone_count": 0,
    })
    for row in rows:
        description = row.get("description")
        category = inverse.get(str(description))
        if category is None:
            raise StatcastSourceError(f"unknown pitch description: {description!r}")
        counts["pitch_rows"] += 1
        automatic = category.startswith("RESIDUAL_AUTOMATIC_")
        swing = category.startswith("SWING_")
        if automatic:
            counts["automatic_ball_count" if category.endswith("BALL") else "automatic_strike_count"] += 1
            if row.get("zone") is not None:
                raise StatcastSourceError("automatic ball/strike unexpectedly has a physical zone")
            continue
        counts["physical_pitch_rows"] += 1
        if swing:
            counts["swing_count"] += 1
            counts["whiff_count" if category == "SWING_WHIFF" else "contact_count"] += 1
        else:
            counts["take_count"] += 1
            if category == "TAKE_CALLED_STRIKE":
                counts["called_strike_count"] += 1
            elif category == "TAKE_CALLED_BALL":
                counts["called_ball_count"] += 1
            elif category == "TAKE_HBP":
                counts["hbp_take_count"] += 1
            elif category == "TAKE_PITCHOUT":
                counts["pitchout_take_count"] += 1
        zone = row.get("zone")
        if zone is None:
            counts["missing_zone_count"] += 1
        elif 1 <= int(zone) <= 9:
            counts["zone_opportunity_count"] += 1
            counts["zone_swing_count"] += int(swing)
        elif 11 <= int(zone) <= 14:
            counts["chase_opportunity_count"] += 1
            counts["chase_swing_count"] += int(swing)
        else:  # pragma: no cover - parser guards this path
            raise StatcastSourceError("zone classification is outside the locked domain")
    if counts["contact_count"] + counts["whiff_count"] != counts["swing_count"]:
        raise StatcastSourceError("plate-discipline swing identity does not reconcile")
    return dict(counts)


def ev_launch_angle_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    allowed_events = {
        "double", "double_play", "field_error", "field_out", "fielders_choice",
        "fielders_choice_out", "force_out", "grounded_into_double_play", "home_run",
        "sac_bunt", "sac_fly", "sac_fly_double_play", "single", "triple", "triple_play",
    }
    bbe: list[Mapping[str, Any]] = []
    for row in rows:
        if row.get("type") == "X":
            if row.get("description") != "hit_into_play" or row.get("events") not in allowed_events:
                raise StatcastSourceError("type-X BBE contradicts description or terminal event")
            bbe.append(row)
        elif row.get("description") == "hit_into_play" and row.get("events") != "catcher_interf":
            raise StatcastSourceError("hit_into_play row is not a BBE and is not catcher interference")
    ev_values: list[float] = []
    joint: list[tuple[float, float, int | None]] = []
    measured_la = 0
    for row in bbe:
        ev = row.get("launch_speed")
        la = row.get("launch_angle")
        cls_raw = row.get("launch_speed_angle")
        if ev is not None:
            if float(ev) <= 0:
                raise StatcastSourceError("measured EV is not positive")
            ev_values.append(float(ev))
        if la is not None:
            if not -90 <= float(la) <= 90:
                raise StatcastSourceError("measured launch angle lies outside [-90,90]")
            measured_la += 1
        cls: int | None = None
        if cls_raw is not None:
            if not float(cls_raw).is_integer() or not 1 <= int(cls_raw) <= 6:
                raise StatcastSourceError("launch_speed_angle classification is invalid")
            cls = int(cls_raw)
        if ev is not None and la is not None:
            joint.append((float(ev), float(la), cls))
        elif cls is not None:
            raise StatcastSourceError("contact classification exists without joint EV/LA")
    classified = [value for value in joint if value[2] is not None]
    hard_hit_ev = [value for value in ev_values if value >= 95.0]
    classified_hard_hit = [value for value in classified if value[0] >= 95.0]
    barrel = [value for value in classified if value[2] == 6]
    if any(value[0] < 95.0 for value in barrel):
        raise StatcastSourceError("barrel classification is not a hard-hit subset")
    classified_hard_nonbarrel = len([value for value in classified_hard_hit if value[2] != 6])
    ev_sorted = sorted(ev_values, reverse=True)
    ev50_support = math.ceil(len(ev_sorted) / 2) if ev_sorted else 0
    ev50 = sum(ev_sorted[:ev50_support]) / ev50_support if ev50_support else None
    counts: dict[str, Any] = {
        "total_bbe": len(bbe),
        "measured_ev_count": len(ev_values),
        "missing_ev_count": len(bbe) - len(ev_values),
        "measured_la_count": measured_la,
        "missing_la_count": len(bbe) - measured_la,
        "joint_ev_la_count": len(joint),
        "joint_missing_count": len(bbe) - len(joint),
        "classified_count": len(classified),
        "unclassified_joint_count": len(joint) - len(classified),
        "barrel_count": len(barrel),
        "hard_hit_ev_count": len(hard_hit_ev),
        "classified_hard_hit_count": len(classified_hard_hit),
        "classified_hard_hit_non_barrel_count": classified_hard_nonbarrel,
        "other_classified_contact_count": len(classified) - len(classified_hard_hit),
        "sweet_spot_count": sum(8 <= value[1] <= 32 for value in joint),
        "ev50_support_count": ev50_support,
        "ev50_robust_tail_mean": ev50,
    }
    if counts["classified_hard_hit_non_barrel_count"] + counts["barrel_count"] != counts["classified_hard_hit_count"]:
        raise StatcastSourceError("classified hard-hit population does not reconcile")
    return counts


def build_point_in_time_feature(
    rows: Iterable[Mapping[str, Any]], *, target_date: str, player_id: int,
    official_pa_count: int, contract: Mapping[str, Any], source_release_sha256: str,
    official_pa_source_sha256: str,
) -> dict[str, Any]:
    target = date.fromisoformat(target_date)
    if target.year != 2023:
        raise StatcastSourceError("target date lies outside the registered 2023 protocol")
    player = _positive_int(player_id, "player_id")
    prior = [
        row for row in rows
        if int(row["batter"]) == player and date.fromisoformat(str(row["game_date"])) < target
    ]
    if not prior:
        raise StatcastSourceError("no strictly prior source events exist for player target")
    if any(date.fromisoformat(str(row["game_date"])) >= target for row in prior):
        raise StatcastSourceError("point-in-time feature includes a future or same-date event")
    discipline = plate_discipline_counts(prior, contract)
    contact = ev_launch_angle_counts(prior)
    if official_pa_count < 0:
        raise StatcastSourceError("official PA denominator is negative")
    contact["official_pa_count"] = official_pa_count
    contact["contact_opportunities_per_pa"] = (
        contact["total_bbe"] / official_pa_count if official_pa_count else None
    )
    feature = {
        "schema_version": "shared-pa-statcast-pit-feature-row-v1",
        "player_id": player,
        "target_date": target_date,
        "max_source_date": max(str(row["game_date"]) for row in prior),
        "source_release_sha256": source_release_sha256,
        "official_pa_source_sha256": official_pa_source_sha256,
        "plate_discipline": discipline,
        "ev_launch_angle": contact,
        "direct_savant_expected_fields_status": "INELIGIBLE",
        "feature_sha256": "",
    }
    feature["feature_sha256"] = sha256_bytes(canonical_json_bytes({
        key: value for key, value in feature.items() if key != "feature_sha256"
    }))
    return feature
