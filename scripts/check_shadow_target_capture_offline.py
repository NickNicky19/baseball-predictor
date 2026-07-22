#!/usr/bin/env python3
"""Synthetic end-to-end and mutation checks for one T-horizon capture."""

from __future__ import annotations

import json
import hashlib
import sys
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.live_market_identity import ResolvedLiveMarketQuote  # noqa: E402
from src.evaluation.shadow_capture_plan import plan_from_schedule  # noqa: E402
from src.evaluation.shadow_target_capture import (  # noqa: E402
    ShadowTargetCaptureError,
    finalize_target_capture,
    load_target_capture_bundle,
    publish_bundle,
)
from src.utils.provenance import sha256_file  # noqa: E402


H = "a" * 64
CONFIG = "b" * 64


def fails(fn) -> bool:
    try:
        fn()
    except (ValueError, ShadowTargetCaptureError):
        return True
    return False


def archive(path: Path, captured: str = "2026-07-16T19:05:00Z", config: str = CONFIG) -> None:
    payload = {
        "game_date": "2026-07-16",
        "prediction_provenance": {
            "schema_version": "daily-prediction-provenance-v1",
            "captured_at_utc": captured,
            "model_version": "fixture-model",
            "effective_config_sha256": config,
            "code": {"status": "available", "snapshot_sha256": "c" * 64},
        },
        "hitter_projections": [{
            "mlb_game_pk": 901, "player_id": 123, "game_date": "2026-07-16",
            "category": "hits", "simulation": {"p_ge_threshold": {"1": 0.61, "2": 0.24}},
        }],
        "pitcher_projections": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def quote(raw_sha: str, **overrides) -> ResolvedLiveMarketQuote:
    values = {
        "source_name": "provider",
        "source_event_id": "event-901",
        "source_player_id": "player-123",
        "source_over_outcome_id": "over-123",
        "source_under_outcome_id": "under-123",
        "source_event_start_time_utc": "2026-07-16T23:10:00Z",
        "source_quote_at_utc": "2026-07-16T19:05:00Z",
        "source_payload_sha256": raw_sha,
        "game_identity_artifact_sha256": "d" * 64,
        "player_identity_artifact_sha256": "e" * 64,
        "mlb_game_pk": 901,
        "player_id": 123,
        "game_date": "2026-07-16",
        "official_start_time_utc": "2026-07-16T23:10:00Z",
        "sportsbook": "draftkings",
        "category": "hits",
        "line": "0.5",
        "over_odds_american": -115,
        "under_odds_american": -105,
    }
    values.update(overrides)
    return ResolvedLiveMarketQuote(**values)


def main() -> int:
    plan = plan_from_schedule(
        official_game_date="2026-07-16", entry_hours=4, policy_sha256=H,
        schedule_snapshot=[{"gamePk": 901, "officialDate": "2026-07-16", "gameDate": "2026-07-16T23:10:00Z"}],
    )
    target = plan.targets[0]
    with tempfile.TemporaryDirectory(prefix="shadow_target_") as temp:
        root = Path(temp)
        prediction = root / "prediction.json"
        raw = root / "raw.json"
        resolved = root / "resolved.json"
        output = root / "bundle.json"
        archive(prediction)
        raw.write_text('{"provider":"immutable-fixture"}', encoding="utf-8")
        base_quote = quote(sha256_file(raw))
        resolved.write_text(json.dumps({"rows": [asdict(base_quote)]}), encoding="utf-8")

        def finalize(**overrides):
            args = {
                "plan": plan, "target_id": target.target_id,
                "started_at_utc": "2026-07-16T19:04:00Z",
                "completed_at_utc": "2026-07-16T19:06:00Z",
                "source_name": "provider", "expected_config_sha256": CONFIG,
                "prediction_archive": prediction, "raw_provider_artifact": raw,
                "resolved_quote_artifact": resolved,
            }
            args.update(overrides)
            return finalize_target_capture(**args)

        bundle = finalize()
        assert bundle.attempt["outcome"] == "captured" and len(bundle.quote_sha256) == 1
        print("[OK] synthetic plan/model/raw quote/hard identity bundle validates")
        assert publish_bundle(bundle, output) is True and publish_bundle(bundle, output) is False
        assert load_target_capture_bundle(output).bundle_sha256 == bundle.bundle_sha256
        print("[OK] atomic publish is idempotent and revalidates every bound artifact")

        changed_target = bundle.to_dict()
        changed_target["target"]["official_game_date"] = "2026-07-15"
        unsigned = {key: value for key, value in changed_target.items() if key != "bundle_sha256"}
        changed_target["bundle_sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        changed_path = root / "changed_target_bundle.json"
        changed_path.write_text(json.dumps(changed_target), encoding="utf-8")
        assert fails(lambda: load_target_capture_bundle(changed_path))
        print("[OK] MUTATION rehashed target still cannot differ from its bound plan")

        archive(prediction, captured="2026-07-16T19:11:00Z")
        assert fails(finalize)
        archive(prediction)
        print("[OK] MUTATION post-target model snapshot fails")

        assert fails(lambda: finalize(expected_config_sha256="d" * 64))
        print("[OK] MUTATION wrong effective config fails")

        late = replace(base_quote, source_quote_at_utc="2026-07-16T19:11:00Z")
        resolved.write_text(json.dumps({"rows": [asdict(late)]}), encoding="utf-8")
        assert fails(finalize)
        print("[OK] MUTATION post-target quote fails")

        wrong_game = replace(base_quote, mlb_game_pk=902)
        resolved.write_text(json.dumps({"rows": [asdict(wrong_game)]}), encoding="utf-8")
        assert fails(finalize)
        print("[OK] MUTATION wrong MLB game identity fails")

        missing_side = asdict(base_quote)
        missing_side.pop("under_odds_american")
        resolved.write_text(json.dumps({"rows": [missing_side]}), encoding="utf-8")
        assert fails(finalize)
        print("[OK] MUTATION missing genuine Under side fails")

        duplicate = replace(base_quote, source_event_id="duplicate-event", source_quote_at_utc="2026-07-16T19:04:00Z")
        resolved.write_text(json.dumps({"rows": [asdict(base_quote), asdict(duplicate)]}), encoding="utf-8")
        assert fails(finalize)
        print("[OK] MUTATION duplicate final market fails with no row-order selection")

        resolved.write_text(json.dumps({"rows": [asdict(base_quote)]}), encoding="utf-8")
        raw.write_text('{"provider":"tampered"}', encoding="utf-8")
        assert fails(finalize)
        raw.write_text('{"provider":"immutable-fixture"}', encoding="utf-8")
        print("[OK] MUTATION tampered raw payload breaks the quote binding")

        no_tail = json.loads(prediction.read_text(encoding="utf-8"))
        no_tail["hitter_projections"][0]["simulation"]["p_ge_threshold"] = {"2": 0.24}
        prediction.write_text(json.dumps(no_tail), encoding="utf-8")
        assert fails(finalize)
        archive(prediction)
        print("[OK] MUTATION missing exact model tail fails")

        resolved.write_text(json.dumps({"rows": []}), encoding="utf-8")
        empty = finalize()
        assert empty.attempt["outcome"] == "no_eligible_market" and not empty.quote_sha256
        print("[OK] observed no-market response is distinct from collector failure")

        conflict = replace(empty, bundle_sha256="0" * 64)
        assert fails(lambda: publish_bundle(conflict, output))
        print("[OK] MUTATION conflicting retry cannot overwrite published evidence")

    print("12/12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
