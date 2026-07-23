from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.evaluation.forward_pitcher_context import ForwardPitcherContext, ProbablePitcher
from src.evaluation.shadow_capture_plan import plan_from_schedule
from src.evaluation.shared_pa_comparator_capture import (
    SharedPAComparatorCaptureError,
    build_frozen_comparator,
    build_market_comparators,
    load_comparator_contract,
    publish_comparator_record,
)
from src.evaluation.shared_pa_forward_collector import (
    RawPregameResponse,
    build_projected_player_snapshot,
)
from src.evaluation.shared_pa_forward_evidence import load_forward_contract


ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 7, 30, 23, tzinfo=timezone.utc)
HORIZON = START - timedelta(hours=4)


def _plan():
    return plan_from_schedule(
        official_game_date="2026-07-30",
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot=[{
            "gamePk": 123456,
            "officialDate": "2026-07-30",
            "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "teams": {
                "home": {"team": {"name": "Home Club"}},
                "away": {"team": {"name": "Away Club"}},
            },
        }],
    )


def _raw(value: object, when: datetime = HORIZON - timedelta(minutes=1)) -> RawPregameResponse:
    return RawPregameResponse(
        body=json.dumps(value, sort_keys=True).encode(),
        received_at_utc=when.isoformat(),
    )


def _player_snapshot() -> dict:
    plan = _plan()
    lineup = _raw({
        "dates": [{"games": [{
            "gamePk": 123456,
            "officialDate": "2026-07-30",
            "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "teams": {
                "home": {"team": {"id": 111, "name": "Home Club"}},
                "away": {"team": {"id": 112, "name": "Away Club"}},
            },
            "lineups": {
                "homePlayers": [{"id": value} for value in range(201, 210)],
                "awayPlayers": [{"id": value} for value in range(101, 110)],
            },
        }]}],
    })
    stats = _raw({
        "people": [{
            "id": 201,
            "stats": [{
                "group": {"displayName": "hitting"},
                "type": {"displayName": "season"},
                "splits": [{"stat": {
                    "plateAppearances": 100,
                    "atBats": 88,
                    "hits": 25,
                    "doubles": 5,
                    "triples": 1,
                    "homeRuns": 3,
                    "baseOnBalls": 10,
                    "strikeOuts": 20,
                }}],
            }],
        }],
    })
    forward = load_forward_contract(
        root=ROOT, contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json"
    )
    return build_projected_player_snapshot(
        plan=plan, target=plan.targets[0], side="home", source_slot=1,
        player_id=201, home_team_id=111, away_team_id=112,
        lineup_response=lineup, stats_response=stats, loaded_contract=forward,
        collector_instance_id="synthetic", collector_code_sha256="b" * 64,
        runtime_manifest_sha256="c" * 64,
    )


def _pitcher_context(*, away_status: str = "resolved", away_id: int | None = 888) -> dict:
    plan = _plan()
    context = ForwardPitcherContext(
        target_id=plan.targets[0].target_id,
        plan_sha256=plan.plan_sha256,
        captured_at_utc=(HORIZON - timedelta(minutes=2)).isoformat(),
        source_name="mlb_statsapi_schedule",
        source_payload_sha256="d" * 64,
        mlb_game_pk=123456,
        official_game_date="2026-07-30",
        official_start_time_utc=START.isoformat(),
        game_type="R",
        home_team="Home Club",
        away_team="Away Club",
        home_probable_pitcher=ProbablePitcher(status="resolved", player_id=777),
        away_probable_pitcher=ProbablePitcher(status=away_status, player_id=away_id),
    )
    return context.to_dict()


def _loaded_contract() -> dict:
    return load_comparator_contract(
        root=ROOT, path=ROOT / "config/shared_pa_comparator_capture_v1.json"
    )


def _projection(category: str, thresholds: list[int], *, pitcher_id: int | None = 888) -> dict:
    return {
        "player_id": 201,
        "player_name": "Exact Player",
        "category": category,
        "game_date": "2026-07-30",
        "mlb_game_pk": 123456,
        "opposing_pitcher_id": pitcher_id,
        "simulation": {
            "p_ge_threshold": {str(value): round(0.7 / value, 8) for value in thresholds}
        },
    }


def _archive(*, captured: datetime = HORIZON - timedelta(minutes=3), pitcher_id: int | None = 888) -> bytes:
    return json.dumps({
        "game_date": "2026-07-30",
        "prediction_provenance": {
            "schema_version": "daily-prediction-provenance-v1",
            "captured_at_utc": captured.isoformat(),
            "model_version": "8c7eed9bb0c3",
            "effective_config_sha256": "3a62fc507956752086919b757ae683e60f9df8fb2143f83292ac61f07830a60c",
            "run_options": {
                "game_date": "2026-07-30",
                "hitter_categories": ["hits", "hrr", "home_runs"],
                "include_pitchers": True,
                "corrections_active": False,
                "edges_requested": False,
                "use_projected_lineups": True,
            },
            "code": {
                "status": "available", "head": "e" * 40,
                "snapshot_sha256": "f" * 64,
                "tracked_patch_sha256": hashlib.sha256(b"").hexdigest(),
                "untracked": [],
            },
        },
        "hitter_projections": [
            _projection("hits", [1, 2], pitcher_id=pitcher_id),
            _projection("home_runs", [1], pitcher_id=pitcher_id),
        ],
    }, sort_keys=True).encode()


def _tb_archive(*, captured: datetime = HORIZON - timedelta(minutes=2), pitcher_id: int | None = 888) -> bytes:
    return json.dumps({
        "game_date": "2026-07-30",
        "prediction_provenance": {
            "schema_version": "daily-prediction-provenance-v1",
            "captured_at_utc": captured.isoformat(),
            "model_version": "de116c7c2f33",
            "effective_config_sha256": "2563cfc1b8ebd71e787d8adeb0c89849c2732c92b6d7368ba7b149885fdc2abc",
            "run_options": {
                "game_date": "2026-07-30",
                "hitter_categories": ["total_bases"],
                "include_pitchers": False,
                "corrections_active": False,
                "edges_requested": False,
                "use_projected_lineups": True,
            },
            "code": {
                "status": "available", "head": "e" * 40,
                "snapshot_sha256": "f" * 64,
                "tracked_patch_sha256": hashlib.sha256(b"").hexdigest(),
                "untracked": [],
            },
        },
        "hitter_projections": [
            _projection("total_bases", [1, 2, 3, 4, 5, 6], pitcher_id=pitcher_id),
        ],
    }, sort_keys=True).encode()


def _frozen(**kwargs) -> dict:
    return build_frozen_comparator(
        archive_bytes=kwargs.pop("archive_bytes", _archive()),
        total_bases_archive_bytes=kwargs.pop("total_bases_archive_bytes", _tb_archive()),
        target=_plan().targets[0], player_snapshot=_player_snapshot(),
        pitcher_context_record=kwargs.pop("pitcher_context_record", _pitcher_context()),
        loaded_contract=_loaded_contract(), **kwargs,
    )


def _market(market_key: str, lines: list[float], *, one_sided: bool = False) -> dict:
    outcomes = []
    for index, line in enumerate(lines):
        outcomes.append({
            "name": "Over", "description": "Exact Player", "point": line,
            "price": 120 + index, "sid": f"{market_key}-o-{index}",
        })
        if not one_sided:
            outcomes.append({
                "name": "Under", "description": "Exact Player", "point": line,
                "price": -140 - index, "sid": f"{market_key}-u-{index}",
            })
    return {
        "key": market_key,
        "last_update": (HORIZON - timedelta(seconds=5)).isoformat(),
        "outcomes": outcomes,
    }


def _provider(*, hr_one_sided: bool = False, start: datetime = START) -> bytes:
    return json.dumps({
        "id": "provider-event-1",
        "sport_key": "baseball_mlb",
        "commence_time": start.isoformat(),
        "home_team": "Home Club",
        "away_team": "Away Club",
        "bookmakers": [{
            "key": "draftkings",
            "markets": [
                _market("batter_hits", [0.5, 1.5]),
                _market("batter_home_runs", [0.5], one_sided=hr_one_sided),
                _market("batter_total_bases", [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]),
            ],
        }],
    }, sort_keys=True).encode()


def _game_identity(*, start: datetime = START) -> bytes:
    delta = int((start - START).total_seconds())
    return json.dumps({
        "schema_version": "shadow-live-game-identity-v2",
        "resolution_method": "exact_nfkc_casefold_teams_equal_cardinality_chronological_ordinal",
        "source_name": "the_odds_api",
        "source_event_id": "provider-event-1",
        "source_event_start_time_utc": start.isoformat(),
        "source_home_team": "Home Club",
        "source_away_team": "Away Club",
        "mlb_game_pk": 123456,
        "official_game_date": "2026-07-30",
        "official_start_time_utc": START.isoformat(),
        "official_home_team": "Home Club",
        "official_away_team": "Away Club",
        "team_pair_event_count": 1,
        "team_pair_chronological_ordinal": 1,
        "start_delta_seconds": delta,
        "max_abs_start_delta_seconds": 60,
        "time_tolerance_used_for_selection": False,
        "start_delta_bound_used_for_rejection": True,
        "fuzzy_matching_used": False,
    }, sort_keys=True).encode()


def _markets(**kwargs) -> list[dict]:
    return build_market_comparators(
        provider_bytes=kwargs.pop("provider_bytes", _provider()),
        received_at_utc=kwargs.pop("received_at_utc", (HORIZON - timedelta(seconds=1)).isoformat()),
        game_identity_bytes=kwargs.pop("game_identity_bytes", _game_identity()),
        target=_plan().targets[0],
        frozen_comparators=[kwargs.pop("frozen", _frozen())],
        pitcher_context_record=kwargs.pop("pitcher_context_record", _pitcher_context()),
        loaded_contract=_loaded_contract(),
        **kwargs,
    )


def test_contract_binds_frozen_config_and_three_separate_markets() -> None:
    loaded = _loaded_contract()
    assert set(loaded["contract"]["markets"]) == {
        "hits", "home_runs_over_0_5", "total_bases"
    }
    assert len(loaded["contract_sha256"]) == 64


def test_loaded_contract_mutation_cannot_change_a_capture_rule() -> None:
    loaded = _loaded_contract()
    loaded["contract"]["provider"]["maximum_event_start_delta_seconds"] = 61
    with pytest.raises(SharedPAComparatorCaptureError, match="contract was mutated"):
        build_frozen_comparator(
            archive_bytes=_archive(), total_bases_archive_bytes=_tb_archive(),
            target=_plan().targets[0],
            player_snapshot=_player_snapshot(), pitcher_context_record=_pitcher_context(),
            loaded_contract=loaded,
        )


def test_frozen_comparator_resolves_all_exact_lines_and_pitcher_receipt() -> None:
    result = _frozen()
    assert result["terminal_state"] == "resolved"
    assert result["opposing_pitcher_id"] == 888
    assert set(result["markets"]) == {"hits", "home_runs_over_0_5", "total_bases"}
    assert set(result["markets"]["total_bases"]["binary_probabilities"]) == {
        f"over_{value:.1f}" for value in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)
    }
    assert result["markets"]["hits"]["terminal_state"] == "resolved"
    assert result["markets"]["hits"]["prediction_provenance"]["source_id"] == "frozen_production"


def test_missing_pitcher_receipt_is_explicit_and_never_substituted() -> None:
    result = _frozen(
        pitcher_context_record=_pitcher_context(away_status="unavailable", away_id=None)
    )
    assert result["terminal_state"] == "pitcher_receipt_unavailable"
    assert {row["terminal_state"] for row in result["markets"].values()} == {
        "pitcher_receipt_unavailable"
    }
    assert result["player_name"] == "Exact Player"


def test_actual_or_guessed_pitcher_identity_cannot_replace_receipt_identity() -> None:
    result = _frozen(
        archive_bytes=_archive(pitcher_id=999),
        total_bases_archive_bytes=_tb_archive(pitcher_id=999),
    )
    assert result["terminal_state"] == "pitcher_identity_mismatch"
    assert {row["terminal_state"] for row in result["markets"].values()} == {
        "pitcher_identity_mismatch"
    }


def test_post_horizon_frozen_archive_is_rejected() -> None:
    with pytest.raises(SharedPAComparatorCaptureError, match="after T-4"):
        _frozen(archive_bytes=_archive(captured=HORIZON + timedelta(microseconds=1)))


def test_prediction_generated_before_comparator_lock_cannot_be_recast_as_prospective() -> None:
    with pytest.raises(SharedPAComparatorCaptureError, match="locked after prediction"):
        _frozen(archive_bytes=_archive(captured=datetime(2026, 7, 22, 23, tzinfo=timezone.utc)))


def test_total_bases_extension_after_horizon_is_rejected_independently() -> None:
    with pytest.raises(SharedPAComparatorCaptureError, match="after T-4"):
        _frozen(
            total_bases_archive_bytes=_tb_archive(
                captured=HORIZON + timedelta(microseconds=1)
            )
        )


def test_duplicate_archive_key_is_rejected_before_probability_consumption() -> None:
    raw = _archive().decode().replace('"game_date": "2026-07-30"', '"game_date": "2026-07-30", "game_date": "2026-07-30"', 1)
    with pytest.raises(SharedPAComparatorCaptureError, match="duplicate JSON key"):
        _frozen(archive_bytes=raw.encode())


def test_archive_without_projected_lineup_permission_is_not_the_locked_t4_run() -> None:
    archive = json.loads(_archive())
    archive["prediction_provenance"]["run_options"]["use_projected_lineups"] = False
    with pytest.raises(SharedPAComparatorCaptureError, match="run options"):
        _frozen(archive_bytes=json.dumps(archive).encode())


def test_valid_market_devigs_only_same_book_same_line_two_sided_quotes() -> None:
    records = _markets()
    assert len(records) == 3
    assert {row["terminal_state"] for row in records} == {"resolved"}
    hr = next(row for row in records if row["market"] == "home_runs_over_0_5")
    over_raw = 100 / 220
    under_raw = 140 / 240
    assert hr["binary_probabilities"]["over_0.5"] == pytest.approx(
        over_raw / (over_raw + under_raw)
    )
    assert hr["executable_price_claimed"] is False
    assert hr["betting_authorized"] is False


def test_one_sided_hr_is_retained_as_unassessable_not_fake_devig() -> None:
    records = _markets(provider_bytes=_provider(hr_one_sided=True))
    hr = next(row for row in records if row["market"] == "home_runs_over_0_5")
    assert hr["terminal_state"] == "one_sided"
    assert hr["binary_probabilities"] == {}
    assert "prediction_provenance" not in hr


def test_cross_book_under_cannot_complete_draftkings_one_sided_quote() -> None:
    provider = json.loads(_provider(hr_one_sided=True))
    provider["bookmakers"].append({
        "key": "other_book",
        "markets": [_market("batter_home_runs", [0.5])],
    })
    records = _markets(provider_bytes=json.dumps(provider).encode())
    hr = next(row for row in records if row["market"] == "home_runs_over_0_5")
    assert hr["terminal_state"] == "one_sided"


def test_post_horizon_market_receipt_is_rejected() -> None:
    with pytest.raises(SharedPAComparatorCaptureError, match="arrived after T-4"):
        _markets(received_at_utc=(HORIZON + timedelta(microseconds=1)).isoformat())


def test_market_timestamp_after_transport_receipt_is_rejected() -> None:
    with pytest.raises(SharedPAComparatorCaptureError, match="after its receipt"):
        _markets(received_at_utc=(HORIZON - timedelta(seconds=10)).isoformat())


def test_sides_from_different_market_timestamps_cannot_be_paired() -> None:
    provider = json.loads(_provider())
    hr = provider["bookmakers"][0]["markets"][1]
    over = copy.deepcopy(hr)
    under = copy.deepcopy(hr)
    over["outcomes"] = [row for row in over["outcomes"] if row["name"] == "Over"]
    under["outcomes"] = [row for row in under["outcomes"] if row["name"] == "Under"]
    under["last_update"] = (HORIZON - timedelta(seconds=4)).isoformat()
    provider["bookmakers"][0]["markets"][1:2] = [over, under]
    records = _markets(provider_bytes=json.dumps(provider).encode())
    result = next(row for row in records if row["market"] == "home_runs_over_0_5")
    assert result["terminal_state"] == "ambiguous_group"
    assert result["binary_probabilities"] == {}


def test_provider_event_outside_measured_start_bound_is_rejected() -> None:
    shifted = START + timedelta(seconds=61)
    with pytest.raises(SharedPAComparatorCaptureError, match="start delta changed"):
        _markets(
            provider_bytes=_provider(start=shifted),
            game_identity_bytes=_game_identity(start=shifted),
        )


def test_provider_event_cannot_change_after_identity_artifact_is_locked() -> None:
    with pytest.raises(SharedPAComparatorCaptureError, match="identity artifact"):
        _markets(provider_bytes=_provider(start=START + timedelta(seconds=1)))


def test_market_capture_remains_separate_when_frozen_pitcher_gate_fails() -> None:
    context = _pitcher_context(away_status="unavailable", away_id=None)
    frozen = _frozen(pitcher_context_record=context)
    records = _markets(frozen=frozen, pitcher_context_record=context)
    assert {row["terminal_state"] for row in records} == {"resolved"}


def test_duplicate_selection_sid_makes_line_unassessable() -> None:
    provider = json.loads(_provider())
    outcomes = provider["bookmakers"][0]["markets"][1]["outcomes"]
    outcomes[1]["sid"] = outcomes[0]["sid"]
    records = _markets(provider_bytes=json.dumps(provider).encode())
    hr = next(row for row in records if row["market"] == "home_runs_over_0_5")
    assert hr["terminal_state"] == "invalid_selection"
    assert hr["binary_probabilities"] == {}


def test_missing_market_is_explicit_for_every_player_market() -> None:
    provider = json.loads(_provider())
    provider["bookmakers"][0]["markets"] = [provider["bookmakers"][0]["markets"][0]]
    records = _markets(provider_bytes=json.dumps(provider).encode())
    dispositions = {row["market"]: row["terminal_state"] for row in records}
    assert dispositions == {
        "hits": "resolved", "home_runs_over_0_5": "missing_market",
        "total_bases": "missing_market",
    }


def test_unmatched_provider_player_is_retained_in_the_candidate_funnel() -> None:
    provider = json.loads(_provider())
    provider["bookmakers"][0]["markets"][1]["outcomes"].extend([
        {
            "name": "Over", "description": "Unknown Player", "point": 0.5,
            "price": 250, "sid": "unknown-o",
        },
        {
            "name": "Under", "description": "Unknown Player", "point": 0.5,
            "price": -320, "sid": "unknown-u",
        },
    ])
    records = _markets(provider_bytes=json.dumps(provider).encode())
    unmatched = [row for row in records if row["terminal_state"] == "unmatched_player"]
    assert len(unmatched) == 1
    assert unmatched[0]["market"] == "home_runs_over_0_5"
    assert unmatched[0]["player_id"] is None
    assert unmatched[0]["binary_probabilities"] == {}


def test_immutable_publication_allows_exact_retry_but_never_overwrite(tmp_path: Path) -> None:
    record = _frozen()
    path = tmp_path / "frozen.json"
    assert publish_comparator_record(
        record_type="frozen_bundle", record=record, path=path
    ) is True
    original = path.read_bytes()
    assert publish_comparator_record(
        record_type="frozen_bundle", record=record, path=path
    ) is False
    mutated = copy.deepcopy(record)
    mutated["player_name"] = "Different Player"
    with pytest.raises(SharedPAComparatorCaptureError, match="cannot overwrite"):
        publish_comparator_record(
            record_type="frozen_bundle", record=mutated, path=path
        )
    assert path.read_bytes() == original


def test_publication_rejects_nonresearch_or_may_record_before_write(tmp_path: Path) -> None:
    record = _frozen()
    record["betting_authorized"] = True
    with pytest.raises(SharedPAComparatorCaptureError, match="not research-only"):
        publish_comparator_record(
            record_type="frozen_bundle", record=record, path=tmp_path / "unsafe.json"
        )
    assert not (tmp_path / "unsafe.json").exists()


def test_may_target_is_rejected_before_any_artifact_parse() -> None:
    target = copy.copy(_plan().targets[0])
    object.__setattr__(target, "official_game_date", "2026-05-30")
    with pytest.raises(SharedPAComparatorCaptureError, match="May 2026 is sealed"):
        build_frozen_comparator(
            archive_bytes=b"not json", total_bases_archive_bytes=b"not json",
            target=target, player_snapshot={},
            pitcher_context_record={}, loaded_contract=_loaded_contract(),
        )
