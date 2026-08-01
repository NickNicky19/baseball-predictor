from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.evaluation import shared_pa_outcome_label_release_v1 as labels
from src.evaluation.shared_pa_outcome_label_release_v1 import (
    CAPTURE_SCHEMA,
    CAPTURE_STATUS,
    EXPECTED_SCHEDULE_DIGEST,
    OutcomeLabelReleaseError,
    build_release,
    canonical_json_bytes,
    representative_preflight,
    sha256_bytes,
    verify_release,
)


def _batting(pa: int, ab: int, hits: int, doubles: int, triples: int, home_runs: int, walks: int, strikeouts: int, total_bases: int):
    return {
        "plateAppearances": pa, "atBats": ab, "hits": hits, "doubles": doubles,
        "triples": triples, "homeRuns": home_runs, "baseOnBalls": walks,
        "intentionalWalks": 0, "strikeOuts": strikeouts, "hitByPitch": 0,
        "sacFlies": 0, "sacBunts": 0, "totalBases": total_bases,
        "runs": 0, "rbi": 0, "catchersInterference": 0,
    }


def _team(team_id: int, ids: list[int]):
    players = {}
    batters = []
    total = {key: 0 for key in _batting(1, 1, 0, 0, 0, 0, 0, 0, 0)}
    for offset, player_id in enumerate(ids, start=1):
        stats = _batting(4, 4, 1 if offset == 1 else 0, 0, 0, 0, 0, 1 if offset == 2 else 0, 1 if offset == 1 else 0)
        for key, value in stats.items():
            total[key] += value
        batters.append(player_id)
        players[f"ID{player_id}"] = {
            "person": {"id": player_id}, "battingOrder": f"{offset}00",
            "stats": {"batting": stats},
        }
    return {"team": {"id": team_id}, "players": players, "batters": batters, "teamStats": {"batting": total}, "score": 0}


def _capture(tmp_path: Path) -> Path:
    root = tmp_path / "capture-root"
    feeds = root / "capture" / "feeds"
    requests = []
    entries = []
    for index, game_pk in enumerate((100001, 100002, 100003), start=1):
        request_id = f"game-{game_pk}"
        expected = {"game_pk": game_pk, "official_date": f"2023-04-0{index}", "away_team_id": 10 + index, "home_team_id": 20 + index}
        request = {"request_id": request_id, "expected": expected}
        requests.append(request)
        payload = {"teams": {"away": _team(10 + index, list(range(index * 100, index * 100 + 9))), "home": _team(20 + index, list(range(index * 100 + 20, index * 100 + 29)))}}
        raw = canonical_json_bytes(payload)
        target = feeds / request_id
        target.mkdir(parents=True)
        (target / "response.json").write_bytes(raw)
        receipt = {"request": {"full_url": f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"}}
        (target / "receipt.json").write_bytes(canonical_json_bytes(receipt))
        entries.append({"request_id": request_id, "response_sha256": sha256_bytes(raw), "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt))})
    plan = {"requests": requests}
    plan_raw = canonical_json_bytes(plan)
    (root / "capture").mkdir(parents=True, exist_ok=True)
    (root / "capture" / "plan.json").write_bytes(plan_raw)
    manifest = {
        "schema_version": CAPTURE_SCHEMA, "status": CAPTURE_STATUS, "season": 2023,
        "research_only": True, "betting_authorized": False,
        "observed_capture_digest": "a" * 64, "schedule_capture_digest": EXPECTED_SCHEDULE_DIGEST,
        "protected_data": {"may_2026_accessed": False, "prices_accessed": False, "prospective_backfill_performed": False, "prospective_evidence_accessed": False, "selection_2024_accessed": False, "spent_hr_confirmation_2025_accessed": False},
        "entries": entries, "plan_sha256": sha256_bytes(plan_raw),
    }
    (root / "capture" / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    return root


def test_builds_reconciled_deterministic_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(labels, "EXPECTED_GAME_COUNT", 3)
    capture = _capture(tmp_path)
    preflight = representative_preflight(capture, expected_capture_digest="a" * 64)
    assert preflight["status"] == "REPRESENTATIVE_DOWNSTREAM_PREFLIGHT_PASSED"
    release = build_release(capture_root=capture, output_dir=tmp_path / "release", expected_capture_digest="a" * 64)
    verified = verify_release(tmp_path / "release", expected_release_sha256=release["release_sha256"])
    assert verified["player_game_rows"] == 54
    row = json.loads((tmp_path / "release" / "canonical_rows.jsonl").read_text().splitlines()[0])
    assert row["singles"] == 1
    assert row["other_official_pa"] == 0


def test_rejects_bad_team_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(labels, "EXPECTED_GAME_COUNT", 3)
    capture = _capture(tmp_path)
    response = capture / "capture" / "feeds" / "game-100001" / "response.json"
    value = json.loads(response.read_text())
    value["teams"]["away"]["teamStats"]["batting"]["hits"] = 99
    response.write_bytes(canonical_json_bytes(value))
    with pytest.raises(OutcomeLabelReleaseError, match="hash differs"):
        representative_preflight(capture, expected_capture_digest="a" * 64)
