from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_protocol_is_research_only_and_chronological() -> None:
    value = json.loads((ROOT / "config/direct_batter_pa_foundation_v1.json").read_text(encoding="utf-8"))
    assert value["status"] == "LOCKED_BEFORE_DIRECT_BATTER_PA_BUILD_OR_2024_SELECTION"
    assert value["research_only"] is True
    assert value["betting_authorized"] is False
    assert value["chronology"]["fit_year"] == 2023
    assert value["chronology"]["selection_year"] == 2024
    assert value["chronology"]["confirmation_years_opened"] == []
    assert value["chronology"]["spent_hr_confirmation_year"] == 2025
    assert value["protected_invariants"]["may_2026_sealed"] is True
    forbidden = set(value["candidate"]["forbidden_inputs"])
    assert {"opposing pitcher", "probable or actual starter", "market price"}.issubset(forbidden)


def test_repaired_protocol_locks_raw_truth_before_reselection() -> None:
    value = json.loads((ROOT / "config/direct_batter_pa_foundation_v2.json").read_text(encoding="utf-8"))
    assert value["status"] == "LOCKED_BEFORE_REPAIRED_BUILD_OR_2024_RESELECTION"
    assert value["candidate"]["model_and_features_unchanged_from_v1"] is True
    assert value["repair"]["source_of_truth"].startswith("Unique regular-season raw terminal PA events")
    assert value["protected_invariants"]["may_2026_sealed"] is True
    assert value["betting_authorized"] is False


def test_v3_protocol_locks_source_truth_and_market_boundary() -> None:
    value = json.loads((ROOT / "config/direct_batter_pa_foundation_v3.json").read_text(encoding="utf-8"))
    assert value["status"] == "LOCKED_BEFORE_SOURCE_TRUTH_BUILD_OR_2024_SELECTION"
    assert value["chronology"]["fit_year"] == 2023
    assert value["chronology"]["selection_year"] == 2024
    assert value["chronology"]["selection_runs"] == 1
    assert value["chronology"]["confirmation_years_opened"] == []
    assert value["protected_invariants"]["may_2026_sealed"] is True
    assert value["protected_invariants"]["july_22_pitcher_collector_untouched"] is True
    assert "realized target-game PA as a prediction input" in value["single_intervention"]["prohibited"]
    assert value["betting_authorized"] is False
