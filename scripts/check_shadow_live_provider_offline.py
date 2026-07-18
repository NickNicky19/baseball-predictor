#!/usr/bin/env python3
"""Credential-free mutations for the live DraftKings Hits provider boundary."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shadow_capture_plan import plan_from_schedule  # noqa: E402
from src.evaluation.shadow_live_provider import (  # noqa: E402
    ShadowLiveProviderError,
    TheOddsAPIShadowClient,
    artifact_bytes,
    artifact_sha256,
    exact_game_identity,
    resolve_hits_snapshot,
    validate_resolved_hits_payload,
)


PASS = 0
FAIL = 0


def check(value: bool, label: str) -> None:
    global PASS, FAIL
    if value:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def raises(fn) -> bool:
    try:
        fn()
    except (ValueError, TypeError):
        return True
    return False


def write(path: Path, value: object) -> None:
    path.write_bytes(artifact_bytes(value))


def prediction(player_name: str = "Mookie Betts", *, duplicate_name: bool = False) -> dict:
    rows = [{
        "mlb_game_pk": 901,
        "player_id": 605141,
        "player_name": player_name,
        "game_date": "2026-07-18",
        "category": "hits",
        "simulation": {"p_ge_threshold": {"1": 0.70, "2": 0.31}},
    }]
    if duplicate_name:
        rows.append({
            **rows[0],
            "player_id": 999999,
        })
    return {
        "game_date": "2026-07-18",
        "prediction_provenance": {
            "schema_version": "daily-prediction-provenance-v1",
            "captured_at_utc": "2026-07-18T18:55:00Z",
            "model_version": "synthetic-live-provider-v1",
            "effective_config_sha256": "a" * 64,
            "code": {"status": "available", "snapshot_sha256": "b" * 64},
        },
        "hitter_projections": rows,
        "pitcher_projections": [],
    }


def odds(*, market_at: str = "2026-07-18T19:05:00Z", under: bool = True,
         over_sid: str = "over-1", under_sid: str = "under-1",
         player_name: str = "Mookie Betts", under_line: float = 0.5) -> dict:
    outcomes = [{
        "name": "Over", "description": player_name, "point": 0.5,
        "price": -120, "sid": over_sid,
    }]
    if under:
        outcomes.append({
            "name": "Under", "description": player_name, "point": under_line,
            "price": 100, "sid": under_sid,
        })
    return {
        "id": "provider-event-901",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-07-18T23:10:00Z",
        "home_team": "Los Angeles Dodgers",
        "away_team": "San Francisco Giants",
        "bookmakers": [{
            "key": "draftkings",
            "title": "DraftKings",
            "markets": [{
                "key": "batter_hits",
                "last_update": market_at,
                "outcomes": outcomes,
            }],
        }],
    }


def main() -> int:
    schedule_row = {
        "gamePk": 901,
        "officialDate": "2026-07-18",
        "gameDate": "2026-07-18T23:10:00Z",
        "teams": {
            "home": {"team": {"name": "Los Angeles Dodgers"}},
            "away": {"team": {"name": "San Francisco Giants"}},
        },
    }
    schedule = {"schedule": [schedule_row]}
    plan = plan_from_schedule(
        official_game_date="2026-07-18",
        entry_hours=4,
        policy_sha256="c" * 64,
        schedule_snapshot=[schedule_row],
    )
    target = plan.targets[0]
    provider_events = [{
        "id": "provider-event-901",
        "commence_time": "2026-07-18T23:10:00Z",
        "home_team": "Los Angeles Dodgers",
        "away_team": "San Francisco Giants",
    }]
    game = exact_game_identity(
        target=target,
        schedule_snapshot=schedule,
        provider_events=provider_events,
    )
    check(
        game["mlb_game_pk"] == 901 and game["fuzzy_matching_used"] is False,
        "exact team/start identity resolves without fuzzy matching",
    )
    changed_start = copy.deepcopy(provider_events)
    changed_start[0]["commence_time"] = "2026-07-18T23:11:00Z"
    check(
        raises(lambda: exact_game_identity(
            target=target, schedule_snapshot=schedule, provider_events=changed_start
        )),
        "MUTATION one-minute provider start drift does not fuzzy-join",
    )

    with tempfile.TemporaryDirectory(prefix="shadow_live_provider_") as tmp:
        root = Path(tmp)
        prediction_path = root / "prediction.json"
        raw_path = root / "raw.json"
        write(prediction_path, prediction())
        write(raw_path, odds())
        player, resolved = resolve_hits_snapshot(
            target=target,
            prediction_archive=prediction_path,
            raw_provider_artifact=raw_path,
            game_identity=game,
            game_identity_artifact_sha256=artifact_sha256(game),
        )
        validate_resolved_hits_payload(resolved)
        check(
            resolved["resolved_quote_count"] == 1
            and resolved["status_counts"] == {"resolved": 1}
            and player["fuzzy_matching_used"] is False,
            "one exact two-sided row resolves and the funnel reconciles",
        )
        check(
            resolved["rows"][0]["source_over_outcome_id"] == "over-1"
            and resolved["rows"][0]["source_under_outcome_id"] == "under-1"
            and resolved["rows"][0]["source_player_id"].startswith(
                "derived-event-description:"
            ),
            "real outcome sids remain distinct and derived player key is labelled honestly",
        )

        tampered = copy.deepcopy(resolved)
        tampered["candidate_rows"] = []
        check(
            raises(lambda: validate_resolved_hits_payload(tampered)),
            "MUTATION raw candidate cannot disappear from the funnel",
        )
        tampered = copy.deepcopy(resolved)
        tampered["game_identity"]["mlb_game_pk"] = 902
        check(
            raises(lambda: validate_resolved_hits_payload(tampered)),
            "MUTATION embedded game identity tamper fails its hash",
        )

        write(raw_path, odds(under=False))
        _, one_sided = resolve_hits_snapshot(
            target=target,
            prediction_archive=prediction_path,
            raw_provider_artifact=raw_path,
            game_identity=game,
            game_identity_artifact_sha256=artifact_sha256(game),
        )
        check(
            one_sided["resolved_quote_count"] == 0
            and one_sided["status_counts"] == {"not_exact_two_sided": 1},
            "MUTATION one-sided market is counted and never fabricated",
        )

        write(raw_path, odds(under_line=1.5))
        _, different_lines = resolve_hits_snapshot(
            target=target,
            prediction_archive=prediction_path,
            raw_provider_artifact=raw_path,
            game_identity=game,
            game_identity_artifact_sha256=artifact_sha256(game),
        )
        check(
            different_lines["resolved_quote_count"] == 0
            and different_lines["status_counts"] == {"not_exact_two_sided": 2},
            "MUTATION Over and Under at different lines cannot pair",
        )

        write(raw_path, odds(under_sid="over-1"))
        _, repeated_sid = resolve_hits_snapshot(
            target=target,
            prediction_archive=prediction_path,
            raw_provider_artifact=raw_path,
            game_identity=game,
            game_identity_artifact_sha256=artifact_sha256(game),
        )
        check(
            repeated_sid["status_counts"] == {"missing_or_repeated_outcome_sid": 1},
            "MUTATION repeated source sid cannot identify two selections",
        )

        write(raw_path, odds(market_at="2026-07-18T19:11:00Z"))
        _, late = resolve_hits_snapshot(
            target=target,
            prediction_archive=prediction_path,
            raw_provider_artifact=raw_path,
            game_identity=game,
            game_identity_artifact_sha256=artifact_sha256(game),
        )
        check(
            late["status_counts"] == {"post_target_market_timestamp": 1},
            "MUTATION post-target market update is excluded and counted",
        )

        write(prediction_path, prediction("Mookie Bettz"))
        write(raw_path, odds())
        _, unmatched = resolve_hits_snapshot(
            target=target,
            prediction_archive=prediction_path,
            raw_provider_artifact=raw_path,
            game_identity=game,
            game_identity_artifact_sha256=artifact_sha256(game),
        )
        check(
            unmatched["status_counts"] == {"player_not_in_prediction_snapshot": 1},
            "MUTATION near-name is not fuzzy matched",
        )

        write(prediction_path, prediction(duplicate_name=True))
        _, ambiguous = resolve_hits_snapshot(
            target=target,
            prediction_archive=prediction_path,
            raw_provider_artifact=raw_path,
            game_identity=game,
            game_identity_artifact_sha256=artifact_sha256(game),
        )
        check(
            ambiguous["status_counts"] == {
                "ambiguous_player_name_in_prediction_snapshot": 1
            },
            "MUTATION ambiguous exact player name fails closed",
        )

        wrong_event = odds()
        wrong_event["id"] = "other-event"
        write(raw_path, wrong_event)
        check(
            raises(lambda: resolve_hits_snapshot(
                target=target,
                prediction_archive=prediction_path,
                raw_provider_artifact=raw_path,
                game_identity=game,
                game_identity_artifact_sha256=artifact_sha256(game),
            )),
            "MUTATION event-odds payload cannot cross hard game identity",
        )

    prior = os.environ.pop("MISSING_SHADOW_TEST_KEY", None)
    try:
        check(
            raises(lambda: TheOddsAPIShadowClient(
                api_key_env="MISSING_SHADOW_TEST_KEY"
            )._key()),
            "missing secret fails before any network request",
        )
    finally:
        if prior is not None:
            os.environ["MISSING_SHADOW_TEST_KEY"] = prior

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
