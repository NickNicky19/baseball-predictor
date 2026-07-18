#!/usr/bin/env python3
"""Synthetic end-to-end checks for the provider-facing shadow boundary.

No network client, secret, live schedule, historical holdout, or wagering
authorization is used here.  The fixture exists to prove that retained
provider evidence can traverse every fail-closed boundary without relying on
names, row order, heuristic confidence, or mutable source files.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import run_shadow_ledger_report as ledger_report  # noqa: E402
from src.evaluation.live_market_identity import ResolvedLiveMarketQuote  # noqa: E402
from src.evaluation.shadow_capture_plan import plan_from_schedule  # noqa: E402
from src.evaluation.shadow_ledger import ForwardShadowLedger  # noqa: E402
from src.evaluation.shadow_lifecycle import commit_target_entries  # noqa: E402
from src.evaluation.shadow_provider_adapter import (  # noqa: E402
    OUTCOME_SCHEMA_VERSION,
    POLICY_SCHEMA_VERSION,
    build_graded_hits_resolution,
    build_shadow_entry_from_capture,
)
from src.evaluation.shadow_target_capture import (  # noqa: E402
    finalize_target_capture,
    publish_bundle,
)
from src.utils.provenance import sha256_file  # noqa: E402


PASS = 0
FAIL = 0
CONFIG_SHA = "b" * 64


def check(condition: bool, label: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def raises(fn) -> bool:
    try:
        fn()
    except (ValueError, SystemExit):
        return True
    return False


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prediction_archive(path: Path) -> None:
    write_json(path, {
        "game_date": "2026-07-16",
        "prediction_provenance": {
            "schema_version": "daily-prediction-provenance-v1",
            "captured_at_utc": "2026-07-16T19:04:00Z",
            "model_version": "synthetic-shadow-model-v1",
            "effective_config_sha256": CONFIG_SHA,
            "code": {"status": "available", "snapshot_sha256": "c" * 64},
        },
        "hitter_projections": [{
            "mlb_game_pk": 901,
            "player_id": 123,
            "game_date": "2026-07-16",
            "category": "hits",
            "simulation": {"p_ge_threshold": {"1": 0.70, "2": 0.31}},
        }],
        "pitcher_projections": [],
    })


def resolved_quote(raw_sha: str, game_identity_sha: str, player_identity_sha: str, **overrides) -> ResolvedLiveMarketQuote:
    values = {
        "source_name": "synthetic-provider",
        "source_event_id": "provider-event-901",
        "source_player_id": "provider-player-123",
        "source_over_outcome_id": "provider-over-123",
        "source_under_outcome_id": "provider-under-123",
        "source_event_start_time_utc": "2026-07-16T23:10:00Z",
        "source_quote_at_utc": "2026-07-16T19:05:00Z",
        "source_payload_sha256": raw_sha,
        "game_identity_artifact_sha256": game_identity_sha,
        "player_identity_artifact_sha256": player_identity_sha,
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
    protocol = json.loads(
        (ROOT / "data/analysis/forward_shadow_adapter_v1/protocol.json").read_text(encoding="utf-8")
    )
    check(
        protocol["locked_before_implementation"] is True
        and protocol["betting_authorized"] is False
        and protocol["network_calls_permitted"] is False
        and protocol["may_2026_permitted"] is False,
        "protocol predeclares research-only, no-May, no-authorization boundaries",
    )

    with tempfile.TemporaryDirectory(prefix="shadow_provider_e2e_") as temporary:
        root = Path(temporary)
        policy_path = root / "policy.json"
        write_json(policy_path, {
            "schema_version": POLICY_SCHEMA_VERSION,
            "policy_id": "synthetic-fixed-over-v1",
            "status": "RESEARCH_ONLY",
            "betting_authorized": False,
            "sportsbook": "draftkings",
            "category": "hits",
            "selection_side": "over",
        })
        plan = plan_from_schedule(
            official_game_date="2026-07-16",
            entry_hours=4,
            policy_sha256=sha256_file(policy_path),
            schedule_snapshot=[{
                "gamePk": 901,
                "officialDate": "2026-07-16",
                "gameDate": "2026-07-16T23:10:00Z",
            }],
        )
        target = plan.targets[0]

        prediction = root / "prediction.json"
        entry_raw = root / "entry_raw.json"
        entry_resolved = root / "entry_resolved.json"
        game_identity = root / "game_identity.json"
        player_identity = root / "player_identity.json"
        prediction_archive(prediction)
        write_json(entry_raw, {"provider": "synthetic", "phase": "entry", "rows": 1})
        write_json(game_identity, {"source_event_id": "provider-event-901", "mlb_game_pk": 901})
        write_json(player_identity, {"source_player_id": "provider-player-123", "player_id": 123})
        entry_quote = resolved_quote(
            sha256_file(entry_raw), sha256_file(game_identity), sha256_file(player_identity)
        )
        write_json(entry_resolved, {"rows": [asdict(entry_quote)]})

        bundle = finalize_target_capture(
            plan=plan,
            target_id=target.target_id,
            started_at_utc="2026-07-16T19:03:00Z",
            completed_at_utc="2026-07-16T19:06:00Z",
            source_name="synthetic-provider",
            expected_config_sha256=CONFIG_SHA,
            prediction_archive=prediction,
            raw_provider_artifact=entry_raw,
            resolved_quote_artifact=entry_resolved,
        )
        bundle_path = root / "target_bundle.json"
        publish_bundle(bundle, bundle_path)
        entry = build_shadow_entry_from_capture(
            bundle_path=bundle_path,
            selection_policy_path=policy_path,
            quote_sha256=entry_quote.quote_sha256,
        )
        check(
            entry.mlb_game_pk == 901
            and entry.player_id == 123
            and entry.model_p_over == 0.70
            and entry.expected_profit_per_unit > 0,
            "plan -> prediction -> retained raw quote -> hard identity -> exact ledger entry",
        )

        ledger_path = root / "forward_ledger.jsonl"
        lifecycle_root = root / "lifecycle"
        commit_path = commit_target_entries(
            bundle_path=bundle_path,
            selection_policy_path=policy_path,
            ledger_path=ledger_path,
            artifact_root=lifecycle_root,
            clock=lambda: "2026-07-16T19:08:00Z",
            ledger_clock=lambda: datetime(2026, 7, 16, 19, 8, tzinfo=timezone.utc),
        )
        ledger = ForwardShadowLedger(
            ledger_path,
            clock=lambda: datetime(2026, 7, 16, 19, 8, tzinfo=timezone.utc),
        )
        check(
            commit_path.is_file()
            and ledger.verify().total_records == 1
            and ledger.recorded_entry_ids() == {entry.entry_id},
            "selection funnel commits the exact entry once to a verified hash chain",
        )
        again = commit_target_entries(
            bundle_path=bundle_path,
            selection_policy_path=policy_path,
            ledger_path=ledger_path,
            artifact_root=lifecycle_root,
            ledger_clock=lambda: datetime(2026, 7, 16, 19, 9, tzinfo=timezone.utc),
        )
        check(again == commit_path and ledger.verify().total_records == 1, "restart is idempotent")

        close_raw = root / "close_raw.json"
        close_resolved = root / "close_resolved.json"
        write_json(close_raw, {"provider": "synthetic", "phase": "close", "rows": 1})
        close_quote = resolved_quote(
            sha256_file(close_raw),
            sha256_file(game_identity),
            sha256_file(player_identity),
            source_quote_at_utc="2026-07-16T23:00:00Z",
            over_odds_american=-145,
            under_odds_american=120,
        )
        write_json(close_resolved, {"rows": [asdict(close_quote)]})
        outcome = root / "official_outcome.json"
        write_json(outcome, {
            "schema_version": OUTCOME_SCHEMA_VERSION,
            "mlb_game_pk": 901,
            "player_id": 123,
            "game_date": "2026-07-16",
            "category": "hits",
            "line": 0.5,
            "selection_side": "over",
            "game_status": "final",
            "settlement_status": "graded",
            "official_starter": True,
            "official_pa": 4,
            "actual_value": 1,
            "settlement_rule_artifact_sha256": "f" * 64,
            "official_game_feed_artifact_sha256": "a" * 64,
        })
        resolution = build_graded_hits_resolution(
            entry=entry,
            settled_at_utc="2026-07-17T04:00:00Z",
            close_raw_provider_artifact=close_raw,
            close_resolved_quote_artifact=close_resolved,
            close_quote_sha256=close_quote.quote_sha256,
            official_outcome_artifact=outcome,
        )
        ledger.append_resolutions([resolution])
        report_path = root / "report.csv"
        rc = ledger_report.main([
            "--ledger", str(ledger_path), "--capture-bar", "0.10", "--b", "100", "--out", str(report_path),
        ])
        report = pd.read_csv(report_path)
        market = report[report["row_type"] == "market_capture"].iloc[0]
        check(
            rc == 0
            and market["verdict"] == "SHADOW_ONLY_NOT_PROMOTED"
            and int(market["n_selected_forward_entries"]) == 1
            and market["capture_ci_status"] == "INSUFFICIENT_DATES_FOR_BLOCK_INTERVAL",
            "close/outcome/report completes but remains explicitly shadow-only",
        )

        wrong_policy = root / "wrong_policy.json"
        write_json(wrong_policy, {
            "schema_version": POLICY_SCHEMA_VERSION,
            "policy_id": "different-policy",
            "status": "RESEARCH_ONLY",
            "betting_authorized": False,
            "sportsbook": "draftkings",
            "category": "hits",
            "selection_side": "over",
        })
        check(
            raises(lambda: build_shadow_entry_from_capture(
                bundle_path=bundle_path,
                selection_policy_path=wrong_policy,
                quote_sha256=entry_quote.quote_sha256,
            )),
            "MUTATION selection policy hash drift fails",
        )
        check(
            raises(lambda: build_shadow_entry_from_capture(
                bundle_path=bundle_path,
                selection_policy_path=policy_path,
                quote_sha256="0" * 64,
            )),
            "MUTATION unknown quote hash cannot be selected by row order",
        )

        original_raw = entry_raw.read_bytes()
        entry_raw.write_bytes(b'{"tampered":true}\n')
        check(
            raises(lambda: build_shadow_entry_from_capture(
                bundle_path=bundle_path,
                selection_policy_path=policy_path,
                quote_sha256=entry_quote.quote_sha256,
            )),
            "MUTATION post-publish raw entry tamper breaks the bundle",
        )
        entry_raw.write_bytes(original_raw)

        late_ledger = ForwardShadowLedger(
            root / "late_ledger.jsonl",
            clock=lambda: datetime(2026, 7, 16, 19, 11, tzinfo=timezone.utc),
        )
        check(raises(lambda: late_ledger.append_entries([entry])), "MUTATION post-target ledger append fails")

        wrong_close = replace(close_quote, player_id=124)
        write_json(close_resolved, {"rows": [asdict(wrong_close)]})
        check(
            raises(lambda: build_graded_hits_resolution(
                entry=entry,
                settled_at_utc="2026-07-17T04:00:00Z",
                close_raw_provider_artifact=close_raw,
                close_resolved_quote_artifact=close_resolved,
                close_quote_sha256=wrong_close.quote_sha256,
                official_outcome_artifact=outcome,
            )),
            "MUTATION close quote with different hard MARKET_KEY fails",
        )

        early_close = replace(close_quote, source_quote_at_utc="2026-07-16T19:00:00Z")
        write_json(close_resolved, {"rows": [asdict(early_close)]})
        early_resolution = build_graded_hits_resolution(
            entry=entry,
            settled_at_utc="2026-07-17T04:00:00Z",
            close_raw_provider_artifact=close_raw,
            close_resolved_quote_artifact=close_resolved,
            close_quote_sha256=early_close.quote_sha256,
            official_outcome_artifact=outcome,
        )
        early_ledger = ForwardShadowLedger(
            root / "early.jsonl",
            clock=lambda: datetime(2026, 7, 16, 19, 8, tzinfo=timezone.utc),
        )
        early_ledger.append_entries([entry])
        check(
            raises(lambda: early_ledger.append_resolutions([early_resolution])),
            "MUTATION close before entry cannot become linked evidence",
        )

        write_json(close_resolved, {"rows": [asdict(close_quote)]})
        wrong_outcome = json.loads(outcome.read_text(encoding="utf-8"))
        wrong_outcome["player_id"] = 124
        write_json(outcome, wrong_outcome)
        check(
            raises(lambda: build_graded_hits_resolution(
                entry=entry,
                settled_at_utc="2026-07-17T04:00:00Z",
                close_raw_provider_artifact=close_raw,
                close_resolved_quote_artifact=close_resolved,
                close_quote_sha256=close_quote.quote_sha256,
                official_outcome_artifact=outcome,
            )),
            "MUTATION official outcome with different hard identity fails",
        )

        post_start = asdict(close_quote)
        post_start["source_quote_at_utc"] = "2026-07-16T23:11:00Z"
        write_json(close_resolved, {"rows": [post_start]})
        check(
            raises(lambda: build_graded_hits_resolution(
                entry=entry,
                settled_at_utc="2026-07-17T04:00:00Z",
                close_raw_provider_artifact=close_raw,
                close_resolved_quote_artifact=close_resolved,
                close_quote_sha256="0" * 64,
                official_outcome_artifact=outcome,
            )),
            "MUTATION post-start close is rejected before scoring",
        )

        check(
            raises(lambda: ledger_report.main([
                "--ledger", str(root / "empty.jsonl"), "--capture-bar", "0.10", "--out", str(root / "empty.csv"),
            ])),
            "MUTATION empty ledger cannot manufacture a shadow report",
        )

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
