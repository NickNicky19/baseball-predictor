import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.evaluation.shadow_capture_plan import plan_from_schedule
from src.evaluation.shared_pa_comparator_runtime import (
    PredictionArtifacts,
    SharedPAComparatorRuntimeError,
    capture_market_artifacts,
    horizon_batch_id,
    load_runtime,
    publish_tree_once,
    prepare_prediction_batches,
)


H = "a" * 64
START = datetime(2026, 7, 30, 23, 10, tzinfo=timezone.utc)
HORIZON = START - timedelta(hours=4)


def _plan(day="2026-07-30"):
    return plan_from_schedule(
        official_game_date=day,
        entry_hours=4,
        policy_sha256=H,
        schedule_snapshot=[{
            "gamePk": 901,
            "officialDate": day,
            "gameDate": START.isoformat().replace("+00:00", "Z"),
            "teams": {
                "home": {"team": {"name": "Home Club"}},
                "away": {"team": {"name": "Away Club"}},
            },
        }],
    )


def _archives(captured=HORIZON - timedelta(minutes=10)):
    body = json.dumps({"game_date": "2026-07-30", "captured": captured.isoformat()}).encode()
    return PredictionArtifacts(body, body)


def _scheduler():
    return {
        "prediction_window_opens_seconds_before_horizon": 2700,
        "prediction_window_closes_seconds_before_horizon": 300,
    }


def test_runtime_manifest_is_exactly_bound() -> None:
    root = Path(__file__).resolve().parents[1]
    payload, digest = load_runtime(
        root / "config/shared_pa_comparator_runtime_v1.json", repository_root=root
    )
    assert payload["invariants"]["late_backfill_forbidden"] is True
    assert len(digest) == 64


def test_prediction_batch_is_immutable_and_exact_retry_does_not_regenerate(tmp_path: Path) -> None:
    plan = _plan()
    calls = 0

    def generate(_):
        nonlocal calls
        calls += 1
        return _archives(), HORIZON - timedelta(minutes=9)

    first = prepare_prediction_batches(
        plan=plan, evidence_root=tmp_path, scheduler=_scheduler(), runtime_sha256=H,
        now=HORIZON - timedelta(minutes=20), generate=generate,
    )
    second = prepare_prediction_batches(
        plan=plan, evidence_root=tmp_path, scheduler=_scheduler(), runtime_sha256=H,
        now=HORIZON - timedelta(minutes=19), generate=generate,
    )
    assert first["prepared"] == 1
    assert second["verified_retry"] == 1
    assert calls == 1
    target = plan.targets[0]
    root = tmp_path / "pregame" / plan.official_game_date / plan.plan_sha256 / "prediction_batches" / horizon_batch_id(plan, target)
    assert (root / "manifest.json").is_file()


def test_missed_prediction_window_is_terminal_and_never_backfilled(tmp_path: Path) -> None:
    called = False

    def generate(_):
        nonlocal called
        called = True
        return _archives(), HORIZON

    plan = _plan()
    result = prepare_prediction_batches(
        plan=plan, evidence_root=tmp_path, scheduler=_scheduler(), runtime_sha256=H,
        now=HORIZON - timedelta(minutes=4), generate=generate,
    )
    assert result["missed"] == 1
    assert called is False
    retry = prepare_prediction_batches(
        plan=plan, evidence_root=tmp_path, scheduler=_scheduler(), runtime_sha256=H,
        now=HORIZON + timedelta(days=1), generate=generate,
    )
    assert retry["missed"] == 1
    assert called is False


def test_late_prediction_completion_is_rejected_without_manifest(tmp_path: Path) -> None:
    plan = _plan()
    with pytest.raises(SharedPAComparatorRuntimeError, match="completed after T-4"):
        prepare_prediction_batches(
            plan=plan, evidence_root=tmp_path, scheduler=_scheduler(), runtime_sha256=H,
            now=HORIZON - timedelta(minutes=20),
            generate=lambda _: (_archives(), HORIZON + timedelta(microseconds=1)),
        )


def test_may_is_rejected_before_generator(tmp_path: Path) -> None:
    # Plan construction itself is the first fail-closed May boundary.
    with pytest.raises(Exception, match="May 2026 is sealed"):
        _plan("2026-05-30")


class _Response:
    def __init__(self, value, received):
        self.body = json.dumps(value, sort_keys=True).encode()
        self.received_at_utc = received.isoformat()
        self.status_code = 200
        self.quota = {}

    def json(self):
        return json.loads(self.body)


def _schedule():
    return {"schedule": [{
        "gamePk": 901,
        "officialDate": "2026-07-30",
        "gameDate": START.isoformat().replace("+00:00", "Z"),
        "teams": {
            "home": {"team": {"name": "Home Club"}},
            "away": {"team": {"name": "Away Club"}},
        },
    }]}


def _events():
    return [{
        "id": "event-901", "sport_key": "baseball_mlb",
        "commence_time": START.isoformat().replace("+00:00", "Z"),
        "home_team": "Home Club", "away_team": "Away Club",
    }]


def _odds():
    return {
        **_events()[0],
        "bookmakers": [{"key": "draftkings", "markets": []}],
    }


def test_three_market_artifact_is_captured_once_before_t4(tmp_path: Path) -> None:
    plan = _plan()
    calls = {"events": 0, "markets": 0}

    def events(_):
        calls["events"] += 1
        return _Response(_events(), HORIZON - timedelta(minutes=2))

    def markets(event_id):
        calls["markets"] += 1
        assert event_id == "event-901"
        return _Response(_odds(), HORIZON - timedelta(minutes=1))

    scheduler = {"market_window_opens_seconds_before_horizon": 180}
    first = capture_market_artifacts(
        plan=plan, schedule_snapshot=_schedule(), evidence_root=tmp_path,
        scheduler=scheduler, runtime_sha256=H, now=HORIZON - timedelta(minutes=2),
        fetch_events=events, fetch_markets=markets,
    )
    second = capture_market_artifacts(
        plan=plan, schedule_snapshot=_schedule(), evidence_root=tmp_path,
        scheduler=scheduler, runtime_sha256=H, now=HORIZON - timedelta(minutes=1),
        fetch_events=events, fetch_markets=markets,
    )
    assert first["captured"] == 1
    assert second["verified_retry"] == 1
    assert calls == {"events": 1, "markets": 1}


def test_post_horizon_market_tick_is_terminal_without_network(tmp_path: Path) -> None:
    called = False

    def forbidden(_):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    result = capture_market_artifacts(
        plan=_plan(), schedule_snapshot=_schedule(), evidence_root=tmp_path,
        scheduler={"market_window_opens_seconds_before_horizon": 180},
        runtime_sha256=H, now=HORIZON + timedelta(microseconds=1),
        fetch_events=forbidden, fetch_markets=forbidden,
    )
    assert result["missed"] == 1
    assert called is False


def test_atomic_tree_retry_rejects_partial_or_mutated_artifact_set(tmp_path: Path) -> None:
    path = tmp_path / "capture"
    assert publish_tree_once(path, {"one.json": b"1", "two.json": b"2"}) is True
    assert publish_tree_once(path, {"one.json": b"1", "two.json": b"2"}) is False
    (path / "two.json").write_bytes(b"changed")
    with pytest.raises(SharedPAComparatorRuntimeError, match="tree differs"):
        publish_tree_once(path, {"one.json": b"1", "two.json": b"2"})


def test_late_provider_response_is_not_published_as_capture(tmp_path: Path) -> None:
    result = capture_market_artifacts(
        plan=_plan(), schedule_snapshot=_schedule(), evidence_root=tmp_path,
        scheduler={"market_window_opens_seconds_before_horizon": 180},
        runtime_sha256=H, now=HORIZON - timedelta(minutes=1),
        fetch_events=lambda _: _Response(_events(), HORIZON + timedelta(microseconds=1)),
        fetch_markets=lambda _: (_ for _ in ()).throw(AssertionError()),
    )
    assert result["attempt_failed"] == 1
    base = tmp_path / "pregame" / "2026-07-30" / _plan().plan_sha256 / "markets" / _plan().targets[0].target_id
    assert not (base / "capture" / "manifest.json").exists()
