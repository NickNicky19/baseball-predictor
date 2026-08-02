"""Independent, no-transport verifier for a June 28 confirmation package."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
from typing import Any


class VerificationError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VerificationError(f"object required: {path}")
    return value


def write_object_new(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def finite(text: str) -> float | None:
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def verify(output: Path, repository: Path) -> dict[str, Any]:
    plan = read_object(repository / "config/shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json")
    v2 = read_object(repository / "config/shared_pa_statcast_source_contract_v2_proposal.json")
    v1 = read_object(repository / "config/shared_pa_statcast_source_contract_v1.json")
    manifest = read_object(output / "manifest.json")
    request_dir = output / "statcast-v2-confirmation-2023-06-28"
    receipt = read_object(request_dir / "receipt.json")
    validation = read_object(request_dir / "validation-01.json")
    raw_candidates = list(request_dir.glob("response-attempt-01.*"))
    failures: list[str] = []
    if len(raw_candidates) != 1:
        raise VerificationError("exactly one retained response is required")
    raw = raw_candidates[0].read_bytes()
    before = digest(raw)
    if receipt.get("byte_count") != len(raw) or receipt.get("sha256") != before:
        failures.append("receipt_identity")
    if receipt.get("http_status") != 200 or receipt.get("final_url") != plan["request"]["full_url"]:
        failures.append("transport")
    headers = receipt.get("response_headers", {})
    content_type = headers.get("content-type", "")
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime not in v2["conditional_content_type_policy"]["normalized_mime_policies"] or mime == "application/octet-stream":
        failures.append("mime")
    length = headers.get("content-length")
    if length is not None and (not str(length).isdecimal() or int(length) != len(raw)):
        failures.append("content_length")
    if length is None and receipt.get("transport_complete") is not True:
        failures.append("transport_complete")
    try:
        text = raw.decode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
    except UnicodeDecodeError:
        text = ""
        failures.append("utf8")
    lowered, lead = text.lower(), text.lstrip()
    if any(x in lowered for x in ("<!doctype html", "<html", "<body", "</html>")):
        failures.append("html")
    if lead.startswith(("{", "[")):
        try:
            json.loads(lead)
            failures.append("json")
        except json.JSONDecodeError:
            pass
    if any(x in lowered[:8192] for x in ("baseball savant error", "statcast error", "request failed", "too many requests")):
        failures.append("savant_error")
    bare_cr = raw.count(b"\r") - raw.count(b"\r\n")
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    if bare_cr or (crlf and lf != crlf): failures.append("newlines")
    parsed: list[list[str]] = []
    if text:
        try:
            parsed = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        except csv.Error:
            failures.append("csv")
    expected_header = v2["expected_csv_header"]["ordered_columns"]
    if not parsed or parsed[0] != expected_header or len(parsed[0]) != 119 or len(set(parsed[0])) != 119:
        failures.append("header")
    expected_games = {int(x["game_pk"]): x for x in plan["certified_games"]}
    games: set[int] = set()
    sides = {key: set() for key in expected_games}
    identities: set[tuple[int, int, int]] = set()
    known = {item for group in v1["plate_discipline"]["categories"].values() for item in group}
    bbe_events = set(v1["ev_launch_angle"]["terminal_bbe_event_allowlist"])
    total_bbe = measured_ev = measured_la = joint = 0
    if parsed and parsed[0] == expected_header:
        for values in parsed[1:]:
            if len(values) != 119 or not values:
                failures.append("row_width")
                continue
            row = dict(zip(expected_header, values, strict=True))
            try:
                game, at_bat, pitch = int(row["game_pk"]), int(row["at_bat_number"]), int(row["pitch_number"])
                batter, pitcher = int(row["batter"]), int(row["pitcher"])
            except ValueError:
                failures.append("identity")
                continue
            if min(game, at_bat, pitch, batter, pitcher) <= 0 or game not in expected_games:
                failures.append("identity")
            games.add(game)
            event = (game, at_bat, pitch)
            if event in identities: failures.append("duplicate_identity")
            identities.add(event)
            if row["game_date"] != "2023-06-28" or row["game_type"] != "R": failures.append("scope")
            if row["inning_topbot"] not in {"Top", "Bot"}: failures.append("side")
            else: sides.setdefault(game, set()).add(row["inning_topbot"])
            if row["description"] not in known: failures.append("description")
            is_bbe = row["type"] == "X" or row["description"] == "hit_into_play"
            if is_bbe:
                total_bbe += 1
                if not (row["type"] == "X" and row["description"] == "hit_into_play" and row["events"] in bbe_events): failures.append("bbe")
                speed, angle = finite(row["launch_speed"]), finite(row["launch_angle"])
                ev_ok, la_ok = speed is not None and speed > 0, angle is not None and -90 <= angle <= 90
                measured_ev += int(ev_ok); measured_la += int(la_ok); joint += int(ev_ok and la_ok)
    if games != set(expected_games): failures.append("game_universe")
    if any(value != {"Top", "Bot"} for value in sides.values()): failures.append("side_coverage")
    if joint > min(measured_ev, measured_la): failures.append("ev_la")
    if digest(raw) != before: failures.append("raw_mutation")
    if validation.get("status") == "PASS" and failures: failures.append("primary_independent_disagreement")
    if validation.get("status") == "FAIL" and not failures: failures.append("primary_independent_disagreement")
    if manifest.get("source_qualified") is not False or manifest.get("promotable") is not False:
        failures.append("promotion_boundary")
    report = {
        "schema_version": "shared-pa-statcast-v2-confirmation-independent-verifier-v1",
        "status": "PASS" if not failures else "FAIL",
        "failures": sorted(set(failures)),
        "raw_byte_count": len(raw),
        "raw_sha256": before,
        "row_count": max(0, len(parsed) - 1),
        "game_pks": sorted(games),
        "total_bbe": total_bbe,
        "measured_ev": measured_ev,
        "measured_la": measured_la,
        "joint_ev_la": joint,
        "canonical_identity_basis": "game_pk+official_date+side+official_team_id",
        "source_qualified": False,
        "scored_predictions_produced": False,
    }
    write_object_new(output / "independent-verifier.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.output.resolve(), args.repository.resolve())
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
