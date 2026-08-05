from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from src import statcast_daily_collector as collector


DAY = "2026-08-04"
GAMES = [900001, 900002]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def csv_body(*, extra_column: bool = True, omit_second_game: bool = False, duplicate: bool = False) -> bytes:
    header = [
        "pitch_type",
        "game_date",
        "game_type",
        "batter",
        "pitcher",
        "game_pk",
        "at_bat_number",
        "pitch_number",
    ]
    if extra_column:
        header.append("future_statcast_measurement")
    rows = [
        ["FF", DAY, "R", "600001", "700001", "900001", "1", "1"],
        ["SL", DAY, "R", "600002", "700002", "900002", "1", "1"],
    ]
    if omit_second_game:
        rows = rows[:1]
    if duplicate:
        rows.append(list(rows[0]))
    if extra_column:
        for row in rows:
            row.append("retained")
    return ("\n".join([",".join(header), *(",".join(row) for row in rows)]) + "\n").encode("utf-8")


def schedule_body(*, unknown_status: bool = False) -> bytes:
    final_status = {
        "abstractGameState": "Final",
        "detailedState": "Final",
        "statusCode": "F",
    }
    postponed_status = {
        "abstractGameState": "Final",
        "detailedState": "Postponed",
        "statusCode": "DR",
    }
    cancelled_status = {
        "abstractGameState": "Final",
        "detailedState": "Cancelled",
        "statusCode": "CR",
        "reason": "Rain",
    }
    games = [
        {"gamePk": 900001, "gameType": "R", "officialDate": DAY, "status": final_status},
        {"gamePk": 900002, "gameType": "R", "officialDate": DAY, "status": cancelled_status},
        {"gamePk": 900003, "gameType": "R", "officialDate": DAY, "status": postponed_status},
        {"gamePk": 900004, "gameType": "R", "officialDate": DAY, "status": postponed_status},
        {"gamePk": 900005, "gameType": "R", "officialDate": "2026-08-05", "status": final_status},
        {"gamePk": 900006, "gameType": "S", "officialDate": DAY, "status": final_status},
    ]
    if unknown_status:
        games.append(
            {
                "gamePk": 900007,
                "gameType": "R",
                "officialDate": DAY,
                "status": {
                    "abstractGameState": "Preview",
                    "detailedState": "Scheduled",
                    "statusCode": "S",
                },
            }
        )
    value = {
        "dates": [
            {"date": "2026-08-03", "games": games},
            {
                "date": DAY,
                "games": [
                    {"gamePk": 900003, "gameType": "R", "officialDate": DAY, "status": final_status}
                ],
            },
        ]
    }
    return json.dumps(value, sort_keys=True).encode("utf-8")


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def fetch(self, url: str, *, timeout_seconds: float, max_bytes: int):
        self.calls.append((url, timeout_seconds, max_bytes))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def response(body: bytes, *, status: int = 200, content_type: str = "text/csv", url: str | None = None):
    request_url = collector.build_request_url(DAY)
    return collector.CapturedResponse(
        status=status,
        body=body,
        headers={"content-type": content_type, "content-length": str(len(body))},
        final_url=url or request_url,
        requested_at_utc=now(),
        observed_at_utc=now(),
    )


def capture(tmp_path: Path, transport: FakeTransport, **kwargs):
    return collector.capture_daily_statcast(
        official_date=DAY,
        expected_game_pks=GAMES,
        expected_games_sha256="a" * 64,
        output_root=tmp_path,
        retrieval_id=kwargs.pop("retrieval_id", "t-plus-1"),
        transport=transport,
        sleep=lambda _seconds: None,
        **kwargs,
    )


def capture_dir(tmp_path: Path, retrieval_id: str = "t-plus-1") -> Path:
    return (
        tmp_path
        / "raw"
        / "savant"
        / "statcast"
        / f"game_date={DAY}"
        / f"retrieval_id={retrieval_id}"
    )


class DailyStatcastCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_request_is_complete_daily_all_pitch_endpoint(self) -> None:
        url = collector.build_request_url(DAY)
        self.assertTrue(url.startswith("https://baseballsavant.mlb.com/statcast_search/csv?"))
        self.assertIn("game_date_gt=2026-08-04", url)
        self.assertIn("game_date_lt=2026-08-04", url)
        self.assertIn("hfGT=R%7C", url)
        self.assertIn("all=true", url)
        self.assertIn("type=details", url)
        self.assertIn("player_type=pitcher", url)
        self.assertNotIn("batter=", url)
        self.assertNotIn("pitcher=", url)
        source = Path(collector.__file__).read_text(encoding="utf-8").lower()
        self.assertNotIn("import pybaseball", source)
        self.assertNotIn("import savantscraper", source)

    def test_request_matches_the_previously_confirmed_exact_query_shape(self) -> None:
        plan = json.loads(
            (
                Path(__file__).parents[1]
                / "config"
                / "shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            collector.build_request_url("2023-06-28"),
            plan["request"]["full_url"],
        )

    def test_valid_capture_preserves_all_columns_and_verifies(self) -> None:
        body = csv_body()
        receipt = capture(self.tmp_path, FakeTransport([response(body)]))
        self.assertEqual(receipt["terminal_status"], "RAW_STATCAST_DATE_CAPTURED")
        self.assertEqual(receipt["csv_validation"]["column_count"], 9)
        self.assertIn("future_statcast_measurement", receipt["csv_validation"]["ordered_columns"])
        self.assertFalse(receipt["feature_construction_performed"])
        self.assertFalse(receipt["model_fitting_performed"])
        root = capture_dir(self.tmp_path)
        self.assertEqual((root / "attempt-01" / "response.csv").read_bytes(), body)
        self.assertEqual(collector.verify_capture(root), receipt)

    def test_missing_game_is_preserved_and_quarantined(self) -> None:
        body = csv_body(omit_second_game=True)
        receipt = capture(self.tmp_path, FakeTransport([response(body)]))
        self.assertEqual(receipt["terminal_status"], "RAW_STATCAST_DATE_QUARANTINED")
        self.assertIn("game universe differs", receipt["attempts"][0]["validation_error"])
        self.assertEqual((capture_dir(self.tmp_path) / "attempt-01" / "response.csv").read_bytes(), body)

    def test_duplicate_pitch_identity_is_quarantined(self) -> None:
        receipt = capture(self.tmp_path, FakeTransport([response(csv_body(duplicate=True))]))
        self.assertEqual(receipt["terminal_status"], "RAW_STATCAST_DATE_QUARANTINED")
        self.assertIn("duplicate pitch identity", receipt["attempts"][0]["validation_error"])

    def test_html_body_is_preserved_and_quarantined(self) -> None:
        body = b"<!doctype html><html><body>error</body></html>"
        receipt = capture(self.tmp_path, FakeTransport([response(body, content_type="text/csv")]))
        self.assertEqual(receipt["terminal_status"], "RAW_STATCAST_DATE_QUARANTINED")
        self.assertIn("HTML", receipt["attempts"][0]["validation_error"])
        self.assertEqual((capture_dir(self.tmp_path) / "attempt-01" / "response.csv").read_bytes(), body)

    def test_redirected_response_is_preserved_and_quarantined(self) -> None:
        wrong_url = collector.build_request_url(DAY) + "&redirected=true"
        receipt = capture(
            self.tmp_path,
            FakeTransport([response(csv_body(), url=wrong_url)]),
        )
        self.assertEqual(receipt["terminal_status"], "RAW_STATCAST_DATE_QUARANTINED")
        self.assertIn("redirected", receipt["attempts"][0]["validation_error"])

    def test_retryable_http_response_is_preserved_before_success(self) -> None:
        first = response(b"temporary", status=429, content_type="text/plain")
        second = response(csv_body())
        receipt = capture(self.tmp_path, FakeTransport([first, second]))
        self.assertEqual(receipt["terminal_status"], "RAW_STATCAST_DATE_CAPTURED")
        self.assertEqual(len(receipt["attempts"]), 2)
        self.assertEqual((capture_dir(self.tmp_path) / "attempt-01" / "response.csv").read_bytes(), b"temporary")
        self.assertEqual((capture_dir(self.tmp_path) / "attempt-02" / "response.csv").read_bytes(), csv_body())

    def test_transport_failures_are_bounded_and_receipted(self) -> None:
        failures = [
            collector.TransportFailure(
                kind="transport_io",
                retryable=True,
                requested_at_utc=now(),
                observed_at_utc=now(),
            )
            for _ in range(3)
        ]
        receipt = capture(self.tmp_path, FakeTransport(failures))
        self.assertEqual(receipt["terminal_status"], "RAW_STATCAST_TRANSPORT_FAILED")
        self.assertEqual(len(receipt["attempts"]), 3)
        self.assertEqual(list(capture_dir(self.tmp_path).rglob("response.csv")), [])

    def test_create_only_destination_cannot_be_overwritten(self) -> None:
        capture(self.tmp_path, FakeTransport([response(csv_body())]))
        with self.assertRaisesRegex(collector.StatcastCollectorError, "create-only"):
            capture(self.tmp_path, FakeTransport([response(csv_body())]))

    def test_schema_change_is_alerted_without_discarding_new_column(self) -> None:
        first = capture(self.tmp_path, FakeTransport([response(csv_body(extra_column=False))]))
        second = capture(
            self.tmp_path,
            FakeTransport([response(csv_body(extra_column=True))]),
            retrieval_id="t-plus-3",
            previous_receipt=first,
        )
        self.assertEqual(second["terminal_status"], "RAW_STATCAST_DATE_CAPTURED_SCHEMA_REVIEW_REQUIRED")
        self.assertEqual(second["schema_comparison"]["added_columns"], ["future_statcast_measurement"])
        self.assertEqual((capture_dir(self.tmp_path, "t-plus-3") / "attempt-01" / "response.csv").read_bytes(), csv_body())

    def test_verification_detects_raw_mutation(self) -> None:
        capture(self.tmp_path, FakeTransport([response(csv_body())]))
        root = capture_dir(self.tmp_path)
        raw = root / "attempt-01" / "response.csv"
        raw.write_bytes(raw.read_bytes() + b"mutation")
        with self.assertRaisesRegex(collector.StatcastCollectorError, "file set, bytes, or hashes"):
            collector.verify_capture(root)

    def test_schedule_selection_excludes_cancelled_and_unplayed_games(self) -> None:
        selection = collector.analyze_schedule_response(schedule_body(), official_date=DAY)
        self.assertEqual(selection["played_game_pks"], [900001, 900003])
        self.assertEqual(selection["played_game_count"], 2)
        self.assertEqual(selection["excluded_unplayed_game_count"], 2)
        self.assertEqual(
            [item["game_pk"] for item in selection["excluded_unplayed_games"]],
            [900002, 900004],
        )
        self.assertEqual(selection["matching_schedule_entry_count"], 5)
        self.assertEqual(selection["unique_regular_season_game_count"], 4)

    def test_schedule_selection_fails_closed_on_unknown_status(self) -> None:
        with self.assertRaisesRegex(collector.StatcastCollectorError, "unrecognized terminal status"):
            collector.analyze_schedule_response(schedule_body(unknown_status=True), official_date=DAY)

    def test_schedule_selection_accepts_known_completed_early_states(self) -> None:
        body = json.dumps(
            {
                "dates": [
                    {
                        "date": DAY,
                        "games": [
                            {
                                "gamePk": 900010,
                                "gameType": "R",
                                "officialDate": DAY,
                                "status": {
                                    "abstractGameState": "Final",
                                    "detailedState": "Completed Early",
                                    "statusCode": "FG",
                                },
                            },
                            {
                                "gamePk": 900011,
                                "gameType": "R",
                                "officialDate": DAY,
                                "status": {
                                    "abstractGameState": "Final",
                                    "detailedState": "Completed Early",
                                    "statusCode": "FR",
                                },
                            },
                        ],
                    }
                ]
            }
        ).encode("utf-8")
        selection = collector.analyze_schedule_response(body, official_date=DAY)
        self.assertEqual(selection["played_game_pks"], [900010, 900011])

    def test_expected_games_bundle_is_create_only_and_verifiable(self) -> None:
        source = self.tmp_path / "schedule.json"
        source.write_bytes(schedule_body())
        destination = self.tmp_path / "expected"
        receipt = collector.create_expected_games_bundle_from_schedule(
            schedule_response_path=source,
            official_date=DAY,
            output_dir=destination,
        )
        self.assertEqual(receipt["selection"]["played_game_pks"], [900001, 900003])
        day, games, digest = collector.load_expected_games(destination / "expected-games.json")
        self.assertEqual(day.isoformat(), DAY)
        self.assertEqual(games, [900001, 900003])
        self.assertEqual(digest, receipt["manifest"]["sha256"])
        self.assertEqual(
            collector.verify_expected_games_bundle(destination, schedule_response_path=source),
            receipt,
        )
        with self.assertRaisesRegex(collector.StatcastCollectorError, "create-only"):
            collector.create_expected_games_bundle_from_schedule(
                schedule_response_path=source,
                official_date=DAY,
                output_dir=destination,
            )

    def test_expected_games_bundle_verification_detects_tampering(self) -> None:
        source = self.tmp_path / "schedule.json"
        source.write_bytes(schedule_body())
        destination = self.tmp_path / "expected"
        collector.create_expected_games_bundle_from_schedule(
            schedule_response_path=source,
            official_date=DAY,
            output_dir=destination,
        )
        manifest = destination / "expected-games.json"
        manifest.write_bytes(manifest.read_bytes() + b" ")
        with self.assertRaisesRegex(collector.StatcastCollectorError, "manifest identity differs"):
            collector.verify_expected_games_bundle(destination, schedule_response_path=source)

    def test_expected_games_manifest_is_strict(self) -> None:
        path = self.tmp_path / "games.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": collector.EXPECTED_GAMES_SCHEMA,
                    "official_date": DAY,
                    "regular_season_game_pks": GAMES,
                }
            ),
            encoding="utf-8",
        )
        day, games, digest = collector.load_expected_games(path)
        self.assertEqual(day.isoformat(), DAY)
        self.assertEqual(games, GAMES)
        self.assertEqual(len(digest), 64)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["regular_season_game_pks"] = [900002, 900001]
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(collector.StatcastCollectorError, "unique and sorted"):
            collector.load_expected_games(path)


if __name__ == "__main__":
    unittest.main()
