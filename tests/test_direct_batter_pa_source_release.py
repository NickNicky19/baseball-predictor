from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from scripts import build_direct_batter_pa_source_release as release


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config" / "direct_batter_pa_source_release_v1.json"


def positive_plan() -> dict:
    return {
        "schema_version": release.SCHEMA_PLAN,
        "season": 2023,
        "evidence_class": release.EVIDENCE_CLASS,
        "research_only": True,
        "betting_authorized": False,
        "prospective_evidence_claimed": False,
        "model_fitting_permitted": False,
        "protected_data": dict(release.PROTECTED_STATE),
        "requests": [
            {
                "request_id": "mlb-feed-718780-2023-04-01",
                "source_kind": "mlb_statsapi_feed_live",
                "method": "GET",
                "url": "https://statsapi.mlb.com/api/v1.1/game/718780/feed/live",
                "query": {},
                "expected": {
                    "game_pk": 718780,
                    "official_date": "2023-04-01",
                    "away_team_id": 10,
                    "home_team_id": 20,
                    "zero_pa_player_ids": [100],
                },
            },
            {
                "request_id": "statcast-2023-04-01",
                "source_kind": "baseball_savant_statcast_csv",
                "method": "GET",
                "url": "https://baseballsavant.mlb.com/statcast_search/csv",
                "query": {
                    "all": "true",
                    "game_date_gt": "2023-04-01",
                    "game_date_lt": "2023-04-01",
                    "hfGT": "R|",
                    "player_type": "pitcher",
                    "type": "details",
                },
                "expected": {"date_start": "2023-04-01", "date_end": "2023-04-01"},
            },
        ],
    }


def statcast_body(
    *, date: str = "2023-04-01", game_type: str = "R", game_pk: int = 718780,
    duplicate: bool = False,
) -> bytes:
    header = "game_date,game_type,batter,pitcher,game_pk,at_bat_number,pitch_number,events\n"
    row = f"{date},{game_type},100,200,{game_pk},1,1,home_run\n"
    return (header + row + (row if duplicate else "")).encode("utf-8")


def mlb_body(
    *, player_pa: int = 0, game_pk: int = 718780, date: str = "2023-04-01",
    away_team_id: int = 10,
) -> bytes:
    batting = {
        "plateAppearances": player_pa,
        "atBats": 0,
        "hits": 0,
        "doubles": 0,
        "triples": 0,
        "homeRuns": 0,
        "baseOnBalls": 0,
        "strikeOuts": 0,
    }
    value = {
        "gamePk": game_pk,
        "gameData": {
            "datetime": {"officialDate": date},
            "game": {"type": "R"},
            "teams": {"away": {"id": away_team_id}, "home": {"id": 20}},
            "status": {"abstractGameState": "Final"},
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {"players": {"ID100": {"person": {"id": 100}, "stats": {"batting": batting}}}},
                    "home": {"players": {}},
                }
            }
        },
    }
    return release.canonical_json_bytes(value)


def schedule_body() -> bytes:
    return release.canonical_json_bytes({
        "dates": [{
            "date": "2023-04-01",
            "games": [{
                "gamePk": 718780,
                "officialDate": "2023-04-01",
                "gameType": "R",
                "teams": {
                    "away": {"team": {"id": 10}},
                    "home": {"team": {"id": 20}},
                },
                "status": {"abstractGameState": "Final"},
                "teams_with_scores_ignored": {"away": 2, "home": 1},
            }],
        }],
    })


def write_plan(tmp_path: Path, value: dict | None = None, name: str = "request_plan_2023.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(value or positive_plan(), sort_keys=True), encoding="utf-8")
    return path


def make_lock(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "runtime_lock_2023.json"
    digest = release.write_runtime_lock(path)
    return path, digest


def make_authority(tmp_path: Path) -> dict[str, object]:
    schedule_root = tmp_path / "schedule_authority_2023"
    raw_path = schedule_root / "raw" / "schedule_2023.json"
    raw_path.parent.mkdir(parents=True)
    raw = schedule_body()
    raw_path.write_bytes(raw)
    query = {"gameType": "R", "hydrate": "team", "season": "2023", "sportId": "1"}
    from urllib.parse import urlencode
    full_url = f"https://statsapi.mlb.com/api/v1/schedule?{urlencode(query)}"
    schedule_manifest = {
        "schema_version": release.SCHEMA_SCHEDULE_MANIFEST,
        "season": 2023,
        "evidence_class": release.EVIDENCE_CLASS,
        "research_only": True,
        "betting_authorized": False,
        "protected_data": dict(release.PROTECTED_STATE),
        "source": {
            "method": "GET", "url": "https://statsapi.mlb.com/api/v1/schedule",
            "query": query, "full_url": full_url,
            "parser_id": "official-mlb-schedule-identity-2023-v1",
        },
        "receipt": {
            "body_path": "raw/schedule_2023.json", "body_bytes": len(raw),
            "body_sha256": release.sha256_bytes(raw), "status": 200,
            "content_type": "application/json", "observed_at_utc": "2026-07-28T12:00:00Z",
            "final_url": full_url,
        },
    }
    schedule_path = schedule_root / "manifest_2023.json"
    schedule_path.write_bytes(release.canonical_json_bytes(schedule_manifest))
    schedule_digest = release.sha256_file(schedule_path)
    games = [{
        "game_pk": 718780, "official_date": "2023-04-01", "game_type": "R",
        "away_team_id": 10, "home_team_id": 20,
    }]
    index = {
        "schema_version": release.SCHEMA_IDENTITY_INDEX, "season": 2023,
        "evidence_class": release.EVIDENCE_CLASS, "research_only": True,
        "betting_authorized": False, "protected_data": dict(release.PROTECTED_STATE),
        "fields": list(release.INDEX_FIELDS),
        "source_schedule_manifest_sha256": schedule_digest,
        "games": games,
    }
    index_path = tmp_path / "identity_index_2023.json"
    index_path.write_bytes(release.canonical_json_bytes(index))
    zero_map = {
        "schema_version": release.SCHEMA_ZERO_PA_MAP, "season": 2023,
        "evidence_class": release.EVIDENCE_CLASS, "research_only": True,
        "betting_authorized": False, "protected_data": dict(release.PROTECTED_STATE),
        "fields": list(release.ZERO_PA_FIELDS),
        "source_zero_pa_bundle_manifest_sha256": "a" * 64,
        "targets": [{
            "game_pk": 718780, "official_date": "2023-04-01", "player_id": 100,
            "source_response_sha256": release.sha256_bytes(mlb_body()),
        }],
    }
    zero_path = tmp_path / "zero_pa_identity_map_2023.json"
    zero_path.write_bytes(release.canonical_json_bytes(zero_map))
    return {
        "schedule_manifest_path": schedule_path,
        "expected_schedule_manifest_digest": schedule_digest,
        "identity_index_path": index_path,
        "expected_identity_index_digest": release.sha256_file(index_path),
        "zero_pa_map_path": zero_path,
        "expected_zero_pa_map_digest": release.sha256_file(zero_path),
    }


def build_inputs(tmp_path: Path) -> dict[str, object]:
    plan = write_plan(tmp_path)
    lock, lock_digest = make_lock(tmp_path)
    return {
        "plan_path": plan,
        "contract_path": CONTRACT,
        "runtime_lock_path": lock,
        "expected_runtime_lock_digest": lock_digest,
        **make_authority(tmp_path),
        "offline_fixture_transport": True,
    }


def fake_transport(
    *, statcast: bytes | None = None, mlb: bytes | None = None,
    content_type_override: str | None = None, redirect: bool = False,
    observed_at_utc: str | None = None, response_headers: dict[str, str] | None = None,
) -> release.OfflineFixtureTransport:
    contract = release.load_contract(CONTRACT)
    plan_path_value = positive_plan()
    requests = [release._canonical_request(item, contract) for item in plan_path_value["requests"]]
    responses: dict[str, release.TransportResponse] = {}
    fixture_time = observed_at_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for request in requests:
        url = request["full_url"]
        is_statcast = request["source_kind"] == "baseball_savant_statcast_csv"
        content_type = content_type_override or ("text/csv" if is_statcast else "application/json")
        body = (statcast or statcast_body()) if is_statcast else (mlb or mlb_body())
        responses[url] = release.TransportResponse(
            status=200,
            body=body,
            headers=response_headers or {"Content-Type": content_type},
            final_url=url + ("&redirected=1" if redirect else ""),
            requested_at_utc=fixture_time,
            observed_at_utc=fixture_time,
        )
    return release.OfflineFixtureTransport(responses=responses)


def build_positive(tmp_path: Path) -> tuple[Path, dict]:
    output = tmp_path / "source_release_2023"
    manifest = release.build_release(
        **build_inputs(tmp_path),
        output_dir=output,
        transport=fake_transport(),
    )
    return output, manifest


def test_positive_release_is_complete_but_not_self_certified(tmp_path: Path) -> None:
    output, manifest = build_positive(tmp_path)
    assert manifest["status"] == "AWAITING_EXTERNAL_EXPECTED_RELEASE_DIGEST"
    assert manifest["external_expected_release_digest"] is None
    assert manifest["source_counts"] == {
        "baseball_savant_statcast_csv": 1,
        "mlb_statsapi_feed_live": 1,
    }
    assert manifest["zero_pa_targets_preserved"] == 1
    assert (output / "transport/mlb_statsapi_feed_live/mlb-feed-718780-2023-04-01/response.json").read_bytes() == mlb_body()
    zero = json.loads((output / "derived/zero_pa_evidence.json").read_text(encoding="utf-8"))
    assert zero["rows"][0]["player_id"] == 100
    assert zero["rows"][0]["response_sha256"] == release.sha256_bytes(mlb_body())
    assert zero["rows"][0]["prior_source_response_sha256"] == release.sha256_bytes(mlb_body())


def test_external_matching_digest_verifies_outside_release(tmp_path: Path) -> None:
    output, manifest = build_positive(tmp_path)
    verification_path = tmp_path / "trusted" / "verification.json"
    result = release.verify_release(
        release_dir=output,
        expected_release_digest=manifest["observed_release_digest"],
        output_path=verification_path,
    )
    assert result["decision"] == "SOURCE_RELEASE_EXTERNALLY_DIGEST_VERIFIED_NOT_MODEL_QUALIFIED"
    assert result["model_fitting_authorized"] is False
    assert verification_path.is_file()


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda p: p.update({"season": 2024}), "non-2023|exactly 2023"),
        (lambda p: p["protected_data"].update({"may_2026_opened": True}), "May 2026"),
        (lambda p: p["requests"][0].update({"request_id": "mlb-feed-may-2026"}), "May 2026"),
        (lambda p: p["requests"][1]["expected"].update({"date_end": "2023-05-01"}), "disagree"),
        (lambda p: p["requests"][1]["query"].update({"game_date_lt": "2024-04-01"}), "non-2023"),
        (lambda p: p["requests"][0]["expected"].update({"game_pk": 999}), "game identity"),
        (lambda p: p["requests"][1].update({"url": "http://baseballsavant.mlb.com/statcast_search/csv"}), "source identity"),
    ],
)
def test_plan_scope_and_identity_mutations_fail_closed(
    tmp_path: Path, mutator, match: str,
) -> None:
    value = positive_plan()
    mutator(value)
    path = write_plan(tmp_path, value)
    with pytest.raises(release.SourceReleaseError, match=match):
        release.load_plan(path, release.load_contract(CONTRACT))


def test_statcast_cross_year_response_is_rejected(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    with pytest.raises(release.SourceReleaseError, match="2023"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_2023",
            transport=fake_transport(statcast=statcast_body(date="2024-04-01")),
        )


def test_statcast_duplicate_pitch_identity_is_rejected(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    with pytest.raises(release.SourceReleaseError, match="duplicate"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_2023",
            transport=fake_transport(statcast=statcast_body(duplicate=True)),
        )


def test_statcast_non_regular_game_and_official_team_mismatch_are_rejected(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    with pytest.raises(release.SourceReleaseError, match="non-regular"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_a_2023",
            transport=fake_transport(statcast=statcast_body(game_type="P")),
        )
    with pytest.raises(release.SourceReleaseError, match="away-team"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_b_2023",
            transport=fake_transport(mlb=mlb_body(away_team_id=99)),
        )


def test_zero_pa_contradiction_is_rejected(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    with pytest.raises(release.SourceReleaseError, match="zero-PA"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_2023",
            transport=fake_transport(mlb=mlb_body(player_pa=1)),
        )


@pytest.mark.parametrize("transport,match", [
    (fake_transport(content_type_override="text/html"), "content type"),
    (fake_transport(redirect=True), "redirected"),
])
def test_transport_semantic_mutations_fail_closed(tmp_path: Path, transport, match: str) -> None:
    inputs = build_inputs(tmp_path)
    with pytest.raises(release.SourceReleaseError, match=match):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_2023",
            transport=transport,
        )


def test_future_timestamp_and_ambiguous_headers_fail_closed(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    with pytest.raises(release.SourceReleaseError, match="future"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_a_2023",
            transport=fake_transport(observed_at_utc="2099-01-01T00:00:00Z"),
        )
    with pytest.raises(release.SourceReleaseError, match="ambiguous"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_b_2023",
            transport=fake_transport(response_headers={"Content-Type": "application/json", "content-type": "application/json"}),
        )


def test_runtime_lock_requires_external_digest_and_exact_bytes(tmp_path: Path) -> None:
    lock, digest = make_lock(tmp_path)
    with pytest.raises(release.SourceReleaseError, match="external expected"):
        release.verify_runtime_lock(lock, "")
    with pytest.raises(release.SourceReleaseError, match="differs"):
        release.verify_runtime_lock(lock, "0" * 64)
    assert release.verify_runtime_lock(lock, digest)["third_party_distributions"] == []
    mutated = json.loads(lock.read_text(encoding="utf-8"))
    mutated["python"]["version"] = "3.99.99"
    lock.write_bytes(release.canonical_json_bytes(mutated))
    with pytest.raises(release.SourceReleaseError, match="active runtime differs"):
        release.verify_runtime_lock(lock, release.sha256_file(lock))


def test_release_mutations_and_wrong_external_digest_fail_closed(tmp_path: Path) -> None:
    output, manifest = build_positive(tmp_path)
    with pytest.raises(release.SourceReleaseError, match="external expected"):
        release.verify_release(release_dir=output, expected_release_digest="", output_path=tmp_path / "v1.json")
    with pytest.raises(release.SourceReleaseError, match="external expected"):
        release.verify_release(release_dir=output, expected_release_digest="0" * 64, output_path=tmp_path / "v2.json")
    response = next(output.glob("transport/baseball_savant_statcast_csv/*/response.csv"))
    response.write_bytes(response.read_bytes() + b"\n")
    with pytest.raises(release.SourceReleaseError, match="file set, bytes, or hashes"):
        release.verify_release(
            release_dir=output, expected_release_digest=manifest["observed_release_digest"],
            output_path=tmp_path / "v3.json",
        )


def test_extra_release_file_and_embedded_verification_are_rejected(tmp_path: Path) -> None:
    output, manifest = build_positive(tmp_path)
    (output / "extra.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(release.SourceReleaseError, match="file set"):
        release.verify_release(
            release_dir=output, expected_release_digest=manifest["observed_release_digest"],
            output_path=tmp_path / "verification.json",
        )
    with pytest.raises(release.SourceReleaseError, match="outside"):
        release.verify_release(
            release_dir=output, expected_release_digest=manifest["observed_release_digest"],
            output_path=output / "verification.json",
        )


def test_self_declared_digest_is_rejected_even_when_file_digest_matches(tmp_path: Path) -> None:
    output, manifest = build_positive(tmp_path)
    manifest["external_expected_release_digest"] = manifest["observed_release_digest"]
    (output / "manifest.json").write_bytes(release.canonical_json_bytes(manifest))
    with pytest.raises(release.SourceReleaseError, match="self-declared"):
        release.verify_release(
            release_dir=output, expected_release_digest=manifest["observed_release_digest"],
            output_path=tmp_path / "verification.json",
        )


def test_rehashed_semantic_forgery_is_rejected_after_byte_integrity_passes(tmp_path: Path) -> None:
    output, manifest = build_positive(tmp_path)
    receipt_path = next(output.glob("transport/mlb_statsapi_feed_live/*/receipt.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["semantics"]["official_date"] = "2023-04-02"
    receipt_path.write_bytes(release.canonical_json_bytes(receipt))
    files = release._enumerate_release_files(output)
    manifest["files"] = files
    manifest["observed_release_digest"] = release._release_digest(manifest)
    (output / "manifest.json").write_bytes(release.canonical_json_bytes(manifest))
    with pytest.raises(release.SourceReleaseError, match="claimed semantics"):
        release.verify_release(
            release_dir=output, expected_release_digest=manifest["observed_release_digest"],
            output_path=tmp_path / "verification.json",
        )


def test_network_capture_is_disabled_by_default() -> None:
    with pytest.raises(release.SourceReleaseError, match="blocked"):
        release._network_transport_allowed(False)
    with pytest.raises(release.SourceReleaseError, match="blocked"):
        release._network_transport_allowed(True)


def test_contract_endpoint_and_protocol_mutations_fail_closed(tmp_path: Path) -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    value["sources"]["baseball_savant_statcast_csv"]["host"] = "example.com"
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(release.SourceReleaseError, match="endpoint, parser, or protocol"):
        release.load_contract(path)


def test_output_and_plan_paths_reject_cross_year_and_may_tokens(tmp_path: Path) -> None:
    contract = release.load_contract(CONTRACT)
    with pytest.raises(release.SourceReleaseError, match="non-2023"):
        release.load_plan(write_plan(tmp_path, name="request_plan_2024.json"), contract)
    inputs = build_inputs(tmp_path)
    with pytest.raises(release.SourceReleaseError, match="May 2026"):
        release.build_release(
            **inputs, output_dir=tmp_path / "may-2026",
            transport=fake_transport(),
        )


@pytest.mark.parametrize(
    "bad_relative",
    [
        "caller_1999/input_2023.json",
        "caller_2022/input_2023.json",
        "caller_2024/input_2023.json",
        "caller_2100/input_2023.json",
        "caller_19990101/input_2023.json",
        "caller_20221231/input_2023.json",
        "caller_20240101/input_2023.json",
        "caller_21001231/input_2023.json",
    ],
)
def test_all_explicit_cross_year_path_forms_are_rejected_before_read(
    tmp_path: Path, bad_relative: str,
) -> None:
    with pytest.raises(release.SourceReleaseError, match="non-2023"):
        release.load_plan(tmp_path / bad_relative, release.load_contract(CONTRACT))


@pytest.mark.parametrize(
    "bad_token",
    ["request-1999", "request-2022", "request-2024", "request-2100",
     "request-19990101", "request-20221231", "request-20240101", "request-21001231"],
)
def test_all_explicit_cross_year_text_forms_are_rejected(bad_token: str) -> None:
    with pytest.raises(release.SourceReleaseError, match="non-2023"):
        release.assert_2023_only_text(bad_token, "mutation")


def test_year_like_sequences_inside_hashes_and_ids_are_not_false_positives() -> None:
    digest_with_year_digits = "a" * 20 + "2024" + "b" * 40
    assert len(digest_with_year_digits) == 64
    release.assert_2023_only_text(digest_with_year_digits, "hash")
    release.assert_2023_only_text("player1999alpha", "opaque ID")
    release.assert_2023_only_text("request_2023.json", "allowed path")
    release.assert_2023_only_text("request_20230101.json", "allowed compact date")


@pytest.mark.parametrize("delay", [-0.1, 60.1, True, "1"])
def test_request_delay_mutations_fail_closed(tmp_path: Path, delay) -> None:
    with pytest.raises(release.SourceReleaseError, match="request delay"):
        release.build_release(
            plan_path=tmp_path / "unread_2023.json", contract_path=CONTRACT,
            runtime_lock_path=tmp_path / "unread_lock_2023.json",
            expected_runtime_lock_digest="0" * 64,
            schedule_manifest_path=tmp_path / "unread_schedule_2023.json",
            expected_schedule_manifest_digest="0" * 64,
            identity_index_path=tmp_path / "unread_index_2023.json",
            expected_identity_index_digest="0" * 64,
            zero_pa_map_path=tmp_path / "unread_zero_2023.json",
            expected_zero_pa_map_digest="0" * 64,
            output_dir=tmp_path / "release_2023", transport=fake_transport(),
            request_delay_seconds=delay,
        )


def rewrite_json(path: Path, mutate) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_bytes(release.canonical_json_bytes(value))
    return release.sha256_file(path)


@pytest.mark.parametrize(
    "field",
    [
        "plan_path", "contract_path", "runtime_lock_path", "schedule_manifest_path",
        "identity_index_path", "zero_pa_map_path", "output_dir",
    ],
)
def test_every_build_caller_path_is_guarded_before_read(tmp_path: Path, field: str) -> None:
    inputs = build_inputs(tmp_path)
    bad_root = tmp_path / "caller_2024"
    bad_root.mkdir()
    if field == "output_dir":
        bad = bad_root / "release_2023"
    else:
        source = Path(inputs[field])
        bad = bad_root / source.name
        bad.write_bytes(source.read_bytes())
    inputs[field] = bad
    output = inputs.pop("output_dir", tmp_path / "release_2023")
    with pytest.raises(release.SourceReleaseError, match="non-2023"):
        release.build_release(
            **inputs, output_dir=output,
            transport=fake_transport(),
        )


def test_missing_may_path_is_rejected_before_open(tmp_path: Path) -> None:
    with pytest.raises(release.SourceReleaseError, match="May 2026"):
        release.load_plan(tmp_path / "may-2026" / "missing_2023.json", release.load_contract(CONTRACT))


def test_reparse_ancestor_child_and_root_escape_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "safe_2023"
    root.mkdir()
    child = root / "child.json"
    child.write_text("{}", encoding="utf-8")
    original = release._has_reparse_point
    monkeypatch.setattr(release, "_has_reparse_point", lambda path: path == root or original(path))
    with pytest.raises(release.SourceReleaseError, match="symlink, junction, or reparse"):
        release._guard_input_file(child, "mutation")
    monkeypatch.setattr(release, "_has_reparse_point", original)
    with pytest.raises(release.SourceReleaseError, match="safe relative|parent traversal"):
        release._guard_child(root, "../escape.json", "mutation")


def test_duplicate_full_url_logical_request_and_overlapping_intervals_fail_closed(tmp_path: Path) -> None:
    contract = release.load_contract(CONTRACT)
    duplicate = positive_plan()
    clone = json.loads(json.dumps(duplicate["requests"][1]))
    clone["request_id"] = "statcast-2023-04-01-copy"
    duplicate["requests"].append(clone)
    path = write_plan(tmp_path, duplicate)
    with pytest.raises(release.SourceReleaseError, match="full URLs|logical"):
        release.load_plan(path, contract)

    overlap = positive_plan()
    clone = json.loads(json.dumps(overlap["requests"][1]))
    clone["request_id"] = "statcast-2023-04-01-to-02"
    clone["expected"]["date_end"] = "2023-04-02"
    clone["query"]["game_date_lt"] = "2023-04-02"
    overlap["requests"].append(clone)
    path.write_text(json.dumps(overlap, sort_keys=True), encoding="utf-8")
    with pytest.raises(release.SourceReleaseError, match="overlap"):
        release.load_plan(path, contract)


@pytest.mark.parametrize(
    "authority_field,mutate,match",
    [
        ("schedule_manifest_path", lambda v: v.update({"unknown": 1}), "missing or unexpected"),
        ("identity_index_path", lambda v: v["games"][0].update({"out_hr": 1}), "missing or unexpected"),
        ("zero_pa_map_path", lambda v: v["targets"][0].update({"out_pa": 0}), "missing or unexpected"),
        ("identity_index_path", lambda v: v["games"][0].update({"home_team_id": 30}), "exact sorted schedule"),
        ("zero_pa_map_path", lambda v: v["targets"][0].update({"game_pk": 999}), "outside the exact identity index"),
    ],
)
def test_external_authority_schema_outcome_and_identity_mutations_fail_closed(
    tmp_path: Path, authority_field: str, mutate, match: str,
) -> None:
    inputs = build_inputs(tmp_path)
    digest_field = {
        "schedule_manifest_path": "expected_schedule_manifest_digest",
        "identity_index_path": "expected_identity_index_digest",
        "zero_pa_map_path": "expected_zero_pa_map_digest",
    }[authority_field]
    inputs[digest_field] = rewrite_json(Path(inputs[authority_field]), mutate)
    with pytest.raises(release.SourceReleaseError, match=match):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_2023", transport=fake_transport(),
        )


def test_external_authority_digest_and_schedule_body_binding_mutations_fail_closed(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    with pytest.raises(release.SourceReleaseError, match="external expected digest"):
        release.build_release(
            **{**inputs, "expected_identity_index_digest": "0" * 64},
            output_dir=tmp_path / "release_a_2023", transport=fake_transport(),
        )
    schedule_body_path = Path(inputs["schedule_manifest_path"]).parent / "raw/schedule_2023.json"
    schedule_body_path.write_bytes(schedule_body_path.read_bytes() + b"\n")
    with pytest.raises(release.SourceReleaseError, match="body binding"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_b_2023", transport=fake_transport(),
        )


def test_plan_must_exactly_match_index_zero_map_and_date_coverage(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    plan = json.loads(Path(inputs["plan_path"]).read_text(encoding="utf-8"))
    plan["requests"][0]["expected"]["zero_pa_player_ids"] = []
    Path(inputs["plan_path"]).write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
    with pytest.raises(release.SourceReleaseError, match="zero-PA authority"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_a_2023", transport=fake_transport(),
        )
    plan = positive_plan()
    plan["requests"][1]["expected"] = {"date_start": "2023-04-02", "date_end": "2023-04-02"}
    plan["requests"][1]["query"]["game_date_gt"] = "2023-04-02"
    plan["requests"][1]["query"]["game_date_lt"] = "2023-04-02"
    Path(inputs["plan_path"]).write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
    with pytest.raises(release.SourceReleaseError, match="exactly cover"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_b_2023", transport=fake_transport(),
        )


def test_statcast_gamepk_must_be_exact_index_member(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    with pytest.raises(release.SourceReleaseError, match="outside the exact identity index"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_2023",
            transport=fake_transport(statcast=statcast_body(game_pk=999)),
        )


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda v: v["platform"].update({"machine": "mutated"}), "active runtime differs"),
        (lambda v: v["site_injection"]["sys_path"].append({"path": "injected", "exists": False, "kind": "missing", "reparse": False, "sha256": None}), "active runtime differs"),
        (lambda v: v["tls"].update({"transitive_shared_library_resolution_proven": True}), "falsely claims"),
        (lambda v: v.update({"network_fetch_authorized": True}), "overstates"),
    ],
)
def test_platform_site_tls_and_network_runtime_mutations_fail_closed(tmp_path: Path, mutate, match: str) -> None:
    lock, _ = make_lock(tmp_path)
    digest = rewrite_json(lock, mutate)
    with pytest.raises(release.SourceReleaseError, match=match):
        release.verify_runtime_lock(lock, digest)


def test_offline_fixture_flag_cannot_authorize_live_transport(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    inputs["offline_fixture_transport"] = False
    with pytest.raises(release.SourceReleaseError, match="network fetch remains blocked"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_2023", transport=fake_transport(),
        )


def test_manifest_unknown_safety_and_reanchored_coverage_mutations_fail_closed(tmp_path: Path) -> None:
    output, manifest = build_positive(tmp_path)
    unknown = dict(manifest)
    unknown["unknown"] = 1
    with pytest.raises(release.SourceReleaseError, match="missing or unexpected"):
        release._release_digest(unknown)
    unknown_binding = json.loads(json.dumps(manifest))
    unknown_binding["bindings"]["unknown"] = "0" * 64
    with pytest.raises(release.SourceReleaseError, match="missing or unexpected"):
        release._release_digest(unknown_binding)
    unknown_file = json.loads(json.dumps(manifest))
    unknown_file["files"][0]["unknown"] = 1
    with pytest.raises(release.SourceReleaseError, match="missing or unexpected"):
        release._release_digest(unknown_file)
    weakened = dict(manifest)
    weakened["research_only"] = False
    with pytest.raises(release.SourceReleaseError, match="research state"):
        release._release_digest(weakened)
    changed = dict(manifest)
    changed["identity_games"] = 2
    changed_digest = release._release_digest(changed)
    assert changed_digest != manifest["observed_release_digest"]
    changed["observed_release_digest"] = changed_digest
    (output / "manifest.json").write_bytes(release.canonical_json_bytes(changed))
    with pytest.raises(release.SourceReleaseError, match="identity coverage"):
        release.verify_release(
            release_dir=output, expected_release_digest=changed_digest,
            output_path=tmp_path / "verification_2023.json",
        )


def test_verify_and_runtime_output_caller_paths_are_pre_guarded(tmp_path: Path) -> None:
    output, manifest = build_positive(tmp_path)
    bad = tmp_path / "caller_2024"
    bad.mkdir()
    with pytest.raises(release.SourceReleaseError, match="non-2023"):
        release.verify_release(
            release_dir=bad, expected_release_digest=manifest["observed_release_digest"],
            output_path=tmp_path / "verify_2023.json",
        )
    with pytest.raises(release.SourceReleaseError, match="non-2023"):
        release.verify_release(
            release_dir=output, expected_release_digest=manifest["observed_release_digest"],
            output_path=bad / "verify_2023.json",
        )
    with pytest.raises(release.SourceReleaseError, match="non-2023"):
        release.write_runtime_lock(bad / "runtime_lock_2023.json")


def test_only_exact_closed_fixture_mapping_can_reach_parser(tmp_path: Path) -> None:
    inputs = build_inputs(tmp_path)
    fixture = fake_transport()
    missing = dict(fixture.responses)
    missing.pop(next(iter(missing)))
    with pytest.raises(release.SourceReleaseError, match="exactly match"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_a_2023",
            transport=release.OfflineFixtureTransport(missing),
        )
    with pytest.raises(release.SourceReleaseError, match="closed offline"):
        release.build_release(
            **inputs, output_dir=tmp_path / "release_b_2023",
            transport=lambda *_: None,
        )
