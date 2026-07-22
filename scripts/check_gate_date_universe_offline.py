#!/usr/bin/env python3
"""Offline checks for the explicit canonical reconstruction date universe.

The check exercises the real ``run_gate_reconstruct.load_dates_file`` parser.
It does not reconstruct a slate or contact any API.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_gate_reconstruct import load_dates_file, load_strict_market_manifest  # noqa: E402
from src.evaluation.strict_market_artifact import artifact_path_for_manifest  # noqa: E402


def expect_error(path: Path, text: str) -> None:
    try:
        load_dates_file(path)
    except ValueError as exc:
        assert text in str(exc), (str(exc), text)
    else:
        raise AssertionError(f"expected {path.name} to fail")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert artifact_path_for_manifest(root / "base_rule_strict_hits_manifest.json") == (
            root / "base_rule_strict_hits.csv"
        )
        print("[PASS] strict manifest names its own artifact; no hard-coded universe")
        try:
            artifact_path_for_manifest(root / "report.json")
        except ValueError as exc:
            assert "must end" in str(exc)
        else:
            raise AssertionError("ambiguous strict manifest name must fail")
        print("[PASS] MUTATION ambiguous manifest name cannot select an artifact")
        valid_json = root / "accepted_dates.json"
        valid_json.write_text(json.dumps({"dates": ["2026-06-02", "2026-03-01"]}))
        assert load_dates_file(valid_json) == ["2026-03-01", "2026-06-02"]
        print("[PASS] JSON date universe is explicit, non-empty, and sorted")
        assert load_dates_file(valid_json, smoke_date="2026-03-01") == ["2026-03-01"]
        print("[PASS] JSON smoke date must come from the certified date universe")
        try:
            load_dates_file(valid_json, smoke_date="2026-03-02")
        except ValueError as exc:
            assert "not in the certified date universe" in str(exc)
        else:
            raise AssertionError("outside JSON smoke date must fail")
        print("[PASS] MUTATION outside JSON smoke date cannot expand the universe")

        valid_csv = root / "accepted_dates.csv"
        with valid_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["slate_date"])
            writer.writeheader()
            writer.writerows([{"slate_date": "2026-03-01"}, {"slate_date": "2026-06-02"}])
        assert load_dates_file(valid_csv) == ["2026-03-01", "2026-06-02"]
        print("[PASS] CSV accepts only a named game_date/slate_date universe")

        duplicate = root / "duplicate_dates.json"
        duplicate.write_text(json.dumps({"dates": ["2026-03-01", "2026-03-01"]}))
        expect_error(duplicate, "duplicate date")
        print("[PASS] MUTATION duplicate date hard-fails (no silent dedupe)")

        ambiguous = root / "report_not_universe.json"
        ambiguous.write_text(json.dumps({"provenance": {"dates": ["2026-03-01"]}}))
        expect_error(ambiguous, "'dates' list")
        print("[PASS] MUTATION report-shaped JSON cannot masquerade as a date universe")

        strict_artifact = root / "strict_unique_hits.csv"
        strict_artifact.write_text(
            "mlb_game_pk,player_id,category,line,official_game_date\n"
            "700001,111,hits,0.5,2026-06-01\n",
            encoding="utf-8",
        )
        strict_manifest = root / "strict_unique_hits_manifest.json"
        strict_manifest.write_text(json.dumps({
            "official_date_universe": ["2026-06-01"],
            "markets": ["hits"],
            "selection_rule": "dk_base_pregame_starter_and_pa_v1",
            "price_freshness_rule": "both_entry_sides_fresh_v1",
            "funnel": {"strict_unique": 1},
            "hashes": {"artifact": hashlib.sha256(strict_artifact.read_bytes()).hexdigest()[:16]},
        }))
        assert load_strict_market_manifest(strict_manifest) == ["2026-06-01"]
        print("[PASS] strict market manifest verifies its artifact hash and exact canonical date universe")
        assert load_strict_market_manifest(strict_manifest, smoke_date="2026-06-01") == ["2026-06-01"]
        print("[PASS] smoke date must come from the verified strict universe")

        bad_rule = json.loads(strict_manifest.read_text())
        bad_rule["selection_rule"] = "unknown_rule"
        strict_manifest.write_text(json.dumps(bad_rule))
        try:
            load_strict_market_manifest(strict_manifest)
        except ValueError as exc:
            assert "selection_rule" in str(exc)
        else:
            raise AssertionError("unknown strict selection rule must fail")
        print("[PASS] MUTATION unknown selection rule cannot select reconstruction dates")
        strict_manifest.write_text(json.dumps({
            "official_date_universe": ["2026-06-01"],
            "markets": ["hits"],
            "selection_rule": "dk_base_pregame_starter_and_pa_v1",
            "price_freshness_rule": "both_entry_sides_fresh_v1",
            "funnel": {"strict_unique": 1},
            "hashes": {"artifact": hashlib.sha256(strict_artifact.read_bytes()).hexdigest()[:16]},
        }))
        try:
            load_strict_market_manifest(strict_manifest, smoke_date="2026-06-02")
        except ValueError as exc:
            assert "not in the certified" in str(exc)
        else:
            raise AssertionError("outside smoke date must fail")
        print("[PASS] MUTATION outside smoke date cannot expand the universe")

        strict_artifact.write_text(
            "mlb_game_pk,player_id,category,line,official_game_date\n"
            "700001,111,hits,0.5,2026-06-01\n"
            "700002,222,hits,0.5,2026-06-01\n",
            encoding="utf-8",
        )
        try:
            load_strict_market_manifest(strict_manifest)
        except ValueError as exc:
            assert "hash mismatch" in str(exc)
        else:
            raise AssertionError("tampered strict artifact must fail")
        print("[PASS] MUTATION tampered strict artifact cannot select reconstruction dates")

        # Restore a correctly hashed artifact whose *canonical* date differs
        # from the manifest.  A hash proves bytes, not semantic agreement.
        strict_artifact.write_text(
            "mlb_game_pk,player_id,category,line,official_game_date\n"
            "700001,111,hits,0.5,2026-06-02\n",
            encoding="utf-8",
        )
        strict_manifest.write_text(json.dumps({
            "official_date_universe": ["2026-06-01"],
            "markets": ["hits"],
            "selection_rule": "dk_base_pregame_starter_and_pa_v1",
            "price_freshness_rule": "both_entry_sides_fresh_v1",
            "funnel": {"strict_unique": 1},
            "hashes": {"artifact": hashlib.sha256(strict_artifact.read_bytes()).hexdigest()[:16]},
        }))
        try:
            load_strict_market_manifest(strict_manifest)
        except ValueError as exc:
            assert "does not exactly match" in str(exc)
        else:
            raise AssertionError("artifact date mismatch must fail")
        print("[PASS] MUTATION vendor-date-shaped manifest cannot override canonical artifact dates")

    print("14/14 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
