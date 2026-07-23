from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.evaluation.shared_pa_forward_adjudication import (
    OPEN_AFTER,
    SharedPAAdjudicationError,
    assert_outcome_opening_allowed,
    evaluate_market,
    load_adjudication_contract,
    outcome_disposition,
    parse_official_player_outcome,
    probability_for_observed_count,
    project_official_final_feed,
    required_dates,
)


ROOT = Path(__file__).resolve().parents[1]


def payload() -> dict:
    return {
        "gamePk": 123456,
        "officialDate": "2026-09-16",
        "status": {"abstractGameState": "Final"},
        "teams": {
            "away": {
                "teamId": 112,
                "players": [{
                    "personId": 101,
                    "battingOrder": "100",
                    "batting": {
                        "plateAppearances": 5, "atBats": 4, "hits": 3,
                        "doubles": 1, "triples": 0, "homeRuns": 1,
                    },
                }],
            },
            "home": {"teamId": 111, "players": []},
        },
    }


def parse(value: dict | None = None):
    projection = json.dumps(
        value or payload(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return parse_official_player_outcome(
        payload_bytes=projection, side="away", player_id=101,
        source_raw_payload_bytes=b"synthetic raw final",
        source_received_at_utc="2026-09-17T05:00:00Z",
        opened_at_utc=OPEN_AFTER,
    )


def complete_date_dispositions() -> dict[str, str]:
    return {value: "complete_nonempty" for value in required_dates()}


def official_outcome_record(
    *, market: str, official_date: str, game_pk: int, player_id: int,
    observed: int,
) -> dict:
    value = payload()
    value["gamePk"] = game_pk
    value["officialDate"] = official_date
    player = value["teams"]["away"]["players"][0]
    player["personId"] = player_id
    batting = player["batting"]
    batting.update({
        "plateAppearances": 4, "atBats": 4, "hits": observed,
        "doubles": 0, "triples": 0, "homeRuns": 0,
    })
    if market == "home_runs_over_0_5":
        batting["homeRuns"] = observed
    projection = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return parse_official_player_outcome(
        payload_bytes=projection, side="away", player_id=player_id,
        source_raw_payload_bytes=f"raw:{game_pk}".encode(),
        source_received_at_utc="2026-09-17T05:00:00Z",
        opened_at_utc=OPEN_AFTER,
    ).as_record()


def synthetic_live_feed() -> dict:
    batting = {
        "plateAppearances": 5, "atBats": 4, "hits": 3,
        "doubles": 1, "triples": 0, "homeRuns": 1,
    }
    return {
        "gamePk": 123456,
        "gameData": {
            "datetime": {"officialDate": "2026-09-16"},
            "status": {"abstractGameState": "Final"},
        },
        "liveData": {"boxscore": {"teams": {
            "away": {
                "team": {"id": 112},
                "players": {
                    "ID101": {
                        "person": {"id": 101}, "battingOrder": "100",
                        "stats": {"batting": batting},
                    },
                    "ID901": {"person": {"id": 901}, "battingOrder": ""},
                },
            },
            "home": {"team": {"id": 111}, "players": {}},
        }}},
    }


def bind_consumed_probabilities(row: dict) -> None:
    hard_key = (
        f"{row['mlb_game_pk']}:{row['side']}:{row['team_id']}:"
        f"{row['player_id']}:{row['market']}"
    )
    for source, provenance in row["prediction_provenance"].items():
        probability = (
            row["pmfs"][source]
            if source in row["pmfs"]
            else row["binary_probabilities"][source]
        )
        consumed = {
            "source_id": source,
            "prediction_hard_key": hard_key,
            "target_horizon_utc": row["target_horizon_utc"],
            "probability": probability,
        }
        provenance["prediction_hard_key"] = hard_key
        provenance["target_horizon_utc"] = row["target_horizon_utc"]
        provenance["consumed_probability_sha256"] = hashlib.sha256(
            json.dumps(consumed, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def test_contract_is_locked_before_first_date_and_binds_evidence_contract() -> None:
    contract = load_adjudication_contract(
        root=ROOT, path=ROOT / "config/shared_pa_forward_adjudication_v1.json"
    )
    assert contract["prospective_window"]["required_consecutive_calendar_dates"] == 56
    assert len(required_dates()) == 56
    assert required_dates()[0] == "2026-07-23"
    assert required_dates()[-1] == "2026-09-16"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("promotion_gates", "minimum_gradeable_fraction"), 0.0),
        (("promotion_gates", "binary_proper_score_materiality_required_at_every_declared_line"), False),
        (("markets", "home_runs_over_0_5", "binary_lines"), [1.5]),
        (("official_outcome_contract", "total_bases_definition"), "hits + homeRuns"),
        (("metrics", "uncertainty", "resamples"), 10),
    ],
)
def test_locked_contract_mutations_fail_closed(
    tmp_path: Path, path: tuple[str, ...], value: object,
) -> None:
    root = tmp_path / "repo"
    config = root / "config"
    config.mkdir(parents=True)
    evidence_bytes = (ROOT / "config/shared_pa_forward_evidence_contract_v1.json").read_bytes()
    (config / "shared_pa_forward_evidence_contract_v1.json").write_bytes(evidence_bytes)
    contract = json.loads(
        (ROOT / "config/shared_pa_forward_adjudication_v1.json").read_text()
    )
    target = contract
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    contract_path = config / "shared_pa_forward_adjudication_v1.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(SharedPAAdjudicationError):
        load_adjudication_contract(root=root, path=contract_path)


def test_outcomes_remain_sealed_until_time_and_exact_date_population() -> None:
    with pytest.raises(SharedPAAdjudicationError, match="remain sealed"):
        assert_outcome_opening_allowed(
            now=OPEN_AFTER.replace(hour=11), official_dates=list(required_dates())
        )
    with pytest.raises(SharedPAAdjudicationError, match="incomplete or reordered"):
        assert_outcome_opening_allowed(
            now=OPEN_AFTER, official_dates=list(required_dates())[1:]
        )
    assert_outcome_opening_allowed(now=OPEN_AFTER, official_dates=list(required_dates())) is None
    with pytest.raises(SharedPAAdjudicationError, match="timezone-aware"):
        assert_outcome_opening_allowed(
            now=datetime(2026, 9, 17, 12), official_dates=list(required_dates())
        )


def test_official_outcome_counts_and_market_truth_are_exact() -> None:
    outcome = parse()
    assert outcome.singles == 1
    assert outcome.hits == 3
    assert outcome.home_runs == 1
    assert outcome.total_bases == 7
    assert outcome_disposition(outcome) == "gradeable_official_starter"
    assert outcome.as_record()["hr_over_0_5"] is True


def test_actual_shaped_final_feed_projects_only_consumed_truth() -> None:
    raw = json.dumps(synthetic_live_feed(), sort_keys=True).encode()
    projection = project_official_final_feed(
        raw_payload_bytes=raw, expected_game_pk=123456,
        expected_official_date="2026-09-16", opened_at_utc=OPEN_AFTER,
    )
    outcome = parse_official_player_outcome(
        payload_bytes=projection, side="away", player_id=101,
        source_raw_payload_bytes=raw,
        source_received_at_utc="2026-09-17T05:00:00Z",
        opened_at_utc=OPEN_AFTER,
    )
    assert outcome.hits == 3
    assert outcome.total_bases == 7
    projected = json.loads(projection)
    assert set(projected) == {"gamePk", "officialDate", "status", "teams"}
    assert [player["personId"] for player in projected["teams"]["away"]["players"]] == [101]


@pytest.mark.parametrize("mutation", ["not_final", "wrong_game", "bad_player_key", "missing_count", "late_open"])
def test_final_feed_projection_mutations_fail_before_publication(mutation: str) -> None:
    value = synthetic_live_feed()
    opened = OPEN_AFTER
    if mutation == "not_final":
        value["gameData"]["status"]["abstractGameState"] = "Live"
    elif mutation == "wrong_game":
        value["gamePk"] = 999999
    elif mutation == "bad_player_key":
        value["liveData"]["boxscore"]["teams"]["away"]["players"]["ID999"] = (
            value["liveData"]["boxscore"]["teams"]["away"]["players"].pop("ID101")
        )
    elif mutation == "missing_count":
        del value["liveData"]["boxscore"]["teams"]["away"]["players"]["ID101"]["stats"]["batting"]["hits"]
    elif mutation == "late_open":
        opened = OPEN_AFTER.replace(hour=11)
    with pytest.raises(SharedPAAdjudicationError):
        project_official_final_feed(
            raw_payload_bytes=json.dumps(value).encode(), expected_game_pk=123456,
            expected_official_date="2026-09-16", opened_at_utc=opened,
        )


def test_nonstarter_is_retained_as_coverage_failure() -> None:
    value = payload()
    value["teams"]["away"]["players"][0]["battingOrder"] = "101"
    assert outcome_disposition(parse(value)) == "retained_nonstarter_coverage_failure"


@pytest.mark.parametrize("mutation", ["not_final", "extra_field", "bad_counts", "may", "duplicate"])
def test_old_integrity_failures_cannot_enter_outcomes(mutation: str) -> None:
    value = payload()
    if mutation == "not_final":
        value["status"]["abstractGameState"] = "Live"
    elif mutation == "extra_field":
        value["status"]["score"] = "3-2"
    elif mutation == "bad_counts":
        value["teams"]["away"]["players"][0]["batting"]["doubles"] = 4
    elif mutation == "may":
        value["officialDate"] = "2026-05-12"
    elif mutation == "duplicate":
        value["teams"]["away"]["players"].append(
            copy.deepcopy(value["teams"]["away"]["players"][0])
        )
    with pytest.raises(SharedPAAdjudicationError):
        parse(value)


def test_probability_support_breach_is_not_clipped() -> None:
    assert probability_for_observed_count([0.7, 0.3], 1) == pytest.approx(0.3)
    with pytest.raises(SharedPAAdjudicationError, match="breached exact model support"):
        probability_for_observed_count([0.7, 0.3], 2)
    with pytest.raises(SharedPAAdjudicationError, match="breached exact model support"):
        probability_for_observed_count([1.0, 0.0], 1)


def evaluation_rows(market: str) -> list[dict]:
    rows = []
    for index, official_date in enumerate(required_dates()[:12]):
        observed = index % 2
        candidate = [0.99, 0.01] if observed == 0 else [0.01, 0.99]
        baseline = [0.55, 0.45]
        rows.append({
            "market": market,
            "official_game_date": official_date,
            "mlb_game_pk": 500000 + index,
            "side": "away",
            "team_id": 112,
            "player_id": 1000 + index,
            "official_start_utc": f"{official_date}T16:00:00Z",
            "target_horizon_utc": f"{official_date}T12:00:00Z",
            "official_outcome_record": official_outcome_record(
                market=market, official_date=official_date,
                game_pk=500000 + index, player_id=1000 + index,
                observed=observed,
            ),
            "pmfs": {
                "candidate": candidate,
                "league_rate": baseline,
                "player_rate": baseline,
            },
            "binary_probabilities": {
                "frozen_production": {},
                "market_implied": {},
            },
            "prediction_provenance": {},
        })
        lines = {
            "hits": (0.5, 1.5),
            "home_runs_over_0_5": (0.5,),
            "total_bases": (0.5, 1.5, 2.5, 3.5, 4.5, 5.5),
        }[market]
        for source in ("frozen_production", "market_implied"):
            rows[-1]["binary_probabilities"][source] = {
                f"over_{line:.1f}": 0.45 if line == 0.5 else 0.0 for line in lines
            }
        for source in (
            "candidate", "league_rate", "player_rate",
            "frozen_production", "market_implied",
        ):
            rows[-1]["prediction_provenance"][source] = {
                "source_id": source,
                "evidence_mode": "prospective_pre_horizon",
                "input_observed_at_utc": f"{official_date}T11:58:00Z",
                "prediction_generated_at_utc": f"{official_date}T11:59:00Z",
                "model_or_contract_locked_at_utc": "2026-07-22T23:50:00Z",
                "artifact_sha256": "a" * 64,
                "model_or_contract_sha256": "b" * 64,
                "prediction_hard_key": "pending",
                "target_horizon_utc": rows[-1]["target_horizon_utc"],
                "consumed_probability_sha256": "c" * 64,
            }
        bind_consumed_probabilities(rows[-1])
    return rows


def test_markets_are_adjudicated_separately_with_paired_date_uncertainty() -> None:
    rows = evaluation_rows("home_runs_over_0_5")
    report = evaluate_market(
        market="home_runs_over_0_5", rows=rows, planned_rows=len(rows),
        explicit_dispositions={"gradeable_official_starter": len(rows)},
        date_dispositions=complete_date_dispositions(),
        market_implied_available=True, opened_at_utc=OPEN_AFTER,
    )
    assert report["promotion_eligible"] is True
    assert report["paired_proper_score_comparisons"]["league_rate"]["brier"]["passed"] is True
    assert report["metrics"]["binary_lines"]["over_0.5"]["candidate"]["auc"] == 1.0


def test_missing_market_comparator_and_coverage_fail_closed() -> None:
    rows = evaluation_rows("hits")
    for row in rows:
        row["binary_probabilities"].pop("market_implied")
        row["prediction_provenance"].pop("market_implied")
    report = evaluate_market(
        market="hits", rows=rows, planned_rows=len(rows) + 1,
        explicit_dispositions={
            "gradeable_official_starter": len(rows),
            "retained_nonstarter_coverage_failure": 1,
        },
        date_dispositions=complete_date_dispositions(),
        market_implied_available=False, opened_at_utc=OPEN_AFTER,
    )
    assert report["promotion_eligible"] is False
    assert "verified_market_comparator_unavailable" in report["promotion_blockers"]


def test_cross_market_rows_and_missing_required_comparator_fail() -> None:
    rows = evaluation_rows("hits")
    rows[0]["market"] = "total_bases"
    with pytest.raises(SharedPAAdjudicationError, match="pooling"):
        evaluate_market(
            market="hits", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )
    rows = evaluation_rows("hits")
    rows[0]["binary_probabilities"].pop("frozen_production")
    with pytest.raises(SharedPAAdjudicationError, match="binary comparator"):
        evaluate_market(
            market="hits", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )


def test_parse_cannot_bypass_opening_gate_or_locked_date_window() -> None:
    projection = json.dumps(payload(), sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(SharedPAAdjudicationError, match="remain sealed"):
        parse_official_player_outcome(
            payload_bytes=projection, side="away", player_id=101,
            source_raw_payload_bytes=b"synthetic raw final",
            source_received_at_utc="2026-09-17T05:00:00Z",
            opened_at_utc=OPEN_AFTER.replace(hour=11),
        )
    value = payload()
    value["officialDate"] = "2026-09-17"
    with pytest.raises(SharedPAAdjudicationError, match="outside the locked"):
        parse(value)


def test_projection_bytes_and_raw_source_bytes_are_both_bound() -> None:
    outcome = parse()
    expected = json.dumps(payload(), sort_keys=True, separators=(",", ":")).encode()
    assert outcome.source_projection_sha256 == hashlib.sha256(expected).hexdigest()
    assert outcome.source_raw_payload_sha256 == hashlib.sha256(b"synthetic raw final").hexdigest()
    with pytest.raises(SharedPAAdjudicationError, match="raw source"):
        parse_official_player_outcome(
            payload_bytes=expected, side="away", player_id=101,
            source_raw_payload_bytes=b"",
            source_received_at_utc="2026-09-17T05:00:00Z",
            opened_at_utc=OPEN_AFTER,
        )


def test_duplicate_json_key_in_projection_fails_closed() -> None:
    duplicate = (
        b'{"gamePk":1,"gamePk":2,"officialDate":"2026-09-16",'
        b'"status":{"abstractGameState":"Final"},"teams":{"home":{},"away":{}}}'
    )
    with pytest.raises(SharedPAAdjudicationError, match="duplicate JSON key"):
        parse_official_player_outcome(
            payload_bytes=duplicate, side="away", player_id=101,
            source_raw_payload_bytes=b"synthetic raw final",
            source_received_at_utc="2026-09-17T05:00:00Z",
            opened_at_utc=OPEN_AFTER,
        )


def test_post_horizon_probability_and_non_t4_horizon_fail_closed() -> None:
    rows = evaluation_rows("home_runs_over_0_5")
    rows[0]["prediction_provenance"]["candidate"]["prediction_generated_at_utc"] = (
        rows[0]["official_start_utc"]
    )
    with pytest.raises(SharedPAAdjudicationError, match="after the decision horizon"):
        evaluate_market(
            market="home_runs_over_0_5", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )
    rows = evaluation_rows("home_runs_over_0_5")
    rows[0]["target_horizon_utc"] = rows[0]["official_start_utc"]
    with pytest.raises(SharedPAAdjudicationError, match="exactly T-4h"):
        evaluate_market(
            market="home_runs_over_0_5", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )


def test_duplicate_identity_and_false_source_label_fail_closed() -> None:
    rows = evaluation_rows("hits")
    rows.append(copy.deepcopy(rows[0]))
    with pytest.raises(SharedPAAdjudicationError, match="duplicate hard"):
        evaluate_market(
            market="hits", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )


def test_team_identity_and_consumed_probability_mutations_fail_closed() -> None:
    rows = evaluation_rows("hits")
    rows[0]["team_id"] = 999
    with pytest.raises(SharedPAAdjudicationError, match="hard identity"):
        evaluate_market(
            market="hits", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )
    rows = evaluation_rows("home_runs_over_0_5")
    rows[0]["pmfs"]["candidate"] = [0.5, 0.5]
    with pytest.raises(SharedPAAdjudicationError, match="consumed probability differs"):
        evaluate_market(
            market="home_runs_over_0_5", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )


def test_outcome_sealed_replay_is_explicit_and_cannot_replace_live_market_receipts() -> None:
    rows = evaluation_rows("home_runs_over_0_5")
    for row in rows:
        candidate = row["prediction_provenance"]["candidate"]
        candidate["evidence_mode"] = "untouched_outcome_sealed_replay"
        candidate["model_or_contract_locked_at_utc"] = "2026-09-16T00:00:00Z"
        candidate["prediction_generated_at_utc"] = "2026-09-16T23:00:00Z"
    report = evaluate_market(
        market="home_runs_over_0_5", rows=rows, planned_rows=len(rows),
        explicit_dispositions={"gradeable_official_starter": len(rows)},
        date_dispositions=complete_date_dispositions(),
        market_implied_available=True, opened_at_utc=OPEN_AFTER,
    )
    assert report["research_only"] is True
    rows = evaluation_rows("home_runs_over_0_5")
    rows[0]["prediction_provenance"]["market_implied"]["evidence_mode"] = (
        "untouched_outcome_sealed_replay"
    )
    with pytest.raises(SharedPAAdjudicationError, match="prospectively captured"):
        evaluate_market(
            market="home_runs_over_0_5", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )
    rows = evaluation_rows("hits")
    rows[0]["prediction_provenance"]["candidate"]["source_id"] = "league_rate"
    with pytest.raises(SharedPAAdjudicationError, match="source identity changed"):
        evaluate_market(
            market="hits", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )


def test_all_56_date_dispositions_are_required_and_failures_block_promotion() -> None:
    rows = evaluation_rows("home_runs_over_0_5")
    incomplete = complete_date_dispositions()
    incomplete.pop(required_dates()[-1])
    with pytest.raises(SharedPAAdjudicationError, match="all 56"):
        evaluate_market(
            market="home_runs_over_0_5", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=incomplete, market_implied_available=True,
            opened_at_utc=OPEN_AFTER,
        )
    failed = complete_date_dispositions()
    failed[required_dates()[-1]] = "failed_missing_plan"
    report = evaluate_market(
        market="home_runs_over_0_5", rows=rows, planned_rows=len(rows),
        explicit_dispositions={"gradeable_official_starter": len(rows)},
        date_dispositions=failed, market_implied_available=True,
        opened_at_utc=OPEN_AFTER,
    )
    assert report["promotion_eligible"] is False
    assert "prospective_date_block_incomplete" in report["promotion_blockers"]


def test_candidate_fixed_high_tail_is_same_cohort_for_every_comparator() -> None:
    rows = evaluation_rows("home_runs_over_0_5")
    report = evaluate_market(
        market="home_runs_over_0_5", rows=rows, planned_rows=len(rows),
        explicit_dispositions={"gradeable_official_starter": len(rows)},
        date_dispositions=complete_date_dispositions(),
        market_implied_available=True, opened_at_utc=OPEN_AFTER,
    )
    metrics = report["metrics"]["binary_lines"]["over_0.5"]
    assert metrics["candidate_fixed_top_decile_rows"] == 2
    assert "candidate_fixed_top_decile_absolute_bias" in metrics["candidate"]


def test_binary_materiality_is_required_against_player_and_league_rates() -> None:
    rows = evaluation_rows("home_runs_over_0_5")
    for index, row in enumerate(rows):
        observed = index % 2
        row["pmfs"]["candidate"] = [0.55, 0.45]
        row["pmfs"]["league_rate"] = [0.99, 0.01] if observed == 0 else [0.01, 0.99]
        row["pmfs"]["player_rate"] = copy.deepcopy(row["pmfs"]["league_rate"])
        bind_consumed_probabilities(row)
    report = evaluate_market(
        market="home_runs_over_0_5", rows=rows, planned_rows=len(rows),
        explicit_dispositions={"gradeable_official_starter": len(rows)},
        date_dispositions=complete_date_dispositions(),
        market_implied_available=True, opened_at_utc=OPEN_AFTER,
    )
    assert report["promotion_eligible"] is False
    assert report["paired_proper_score_comparisons"]["league_rate"]["binary_lines"]["over_0.5"]["brier"]["passed"] is False


def test_scored_outcome_is_recomputed_from_hash_bound_official_record() -> None:
    rows = evaluation_rows("hits")
    rows[0]["official_outcome_record"]["hits"] = 4
    with pytest.raises(SharedPAAdjudicationError, match="hash mismatch"):
        evaluate_market(
            market="hits", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )
    rows = evaluation_rows("hits")
    record = rows[0]["official_outcome_record"]
    record["hits"] = 4
    unhashed = {key: value for key, value in record.items() if key != "outcome_sha256"}
    record["outcome_sha256"] = hashlib.sha256(
        json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(SharedPAAdjudicationError, match="derived counts|impossible"):
        evaluate_market(
            market="hits", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows)},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )


def test_calibration_and_discrimination_regressions_each_block_promotion() -> None:
    rows = evaluation_rows("home_runs_over_0_5")
    for index, row in enumerate(rows):
        candidate_probability = 0.9 if index % 2 == 0 else 0.8
        row["pmfs"]["candidate"] = [1.0 - candidate_probability, candidate_probability]
        row["pmfs"]["league_rate"] = [0.5, 0.5]
        row["pmfs"]["player_rate"] = [0.5, 0.5]
        row["binary_probabilities"]["frozen_production"]["over_0.5"] = 0.5
        row["binary_probabilities"]["market_implied"]["over_0.5"] = 0.5
        bind_consumed_probabilities(row)
    report = evaluate_market(
        market="home_runs_over_0_5", rows=rows, planned_rows=len(rows),
        explicit_dispositions={"gradeable_official_starter": len(rows)},
        date_dispositions=complete_date_dispositions(),
        market_implied_available=True, opened_at_utc=OPEN_AFTER,
    )
    assert "calibration_nonregression_gate_failed" in report["promotion_blockers"]
    assert "discrimination_nonregression_gate_failed" in report["promotion_blockers"]


def test_unknown_coverage_disposition_and_empty_gradeable_rows_fail_closed() -> None:
    rows = evaluation_rows("hits")
    with pytest.raises(SharedPAAdjudicationError, match="explicit dispositions"):
        evaluate_market(
            market="hits", rows=rows, planned_rows=len(rows),
            explicit_dispositions={"gradeable_official_starter": len(rows), "deleted_players": 0},
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )
    with pytest.raises(SharedPAAdjudicationError, match="no gradeable rows"):
        evaluate_market(
            market="hits", rows=[], planned_rows=1,
            explicit_dispositions={
                "gradeable_official_starter": 0,
                "retained_missing_prediction_coverage_failure": 1,
            },
            date_dispositions=complete_date_dispositions(),
            market_implied_available=True, opened_at_utc=OPEN_AFTER,
        )
