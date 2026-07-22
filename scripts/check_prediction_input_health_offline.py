#!/usr/bin/env python3
"""Offline/mutation harness for prediction-input health reporting."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.prediction_health import health_for_bundle, health_rows, summarize_health
from src.models.dataclasses import (
    GameContext,
    HitterGameContext,
    LeagueBaselines,
    MatchupContext,
    ParkFactors,
    PitcherStatcastProfile,
    PlayerFeatureBundle,
    PlayerIdentity,
    StatcastProfile,
    WeatherContext,
)


PASS = 0
FAIL = 0


def check(condition: bool, label: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


def bundle(**overrides) -> PlayerFeatureBundle:
    game = GameContext(700001, "2026-07-13", "Test Park", True, "Away", "confirmed")
    player = PlayerIdentity(111, "Test Hitter", "HOME")
    hitter = HitterGameContext(player, game, lineup_slot=3, opposing_pitcher_id=222)
    statcast = StatcastProfile(111, "Test Hitter", sample_pa=250, xwoba=0.350)
    pitcher = PitcherStatcastProfile(
        222, "Test Pitcher", sample_pa=300, k_rate=0.25, bb_rate=0.08, hr_per_9=1.1
    )
    payload = {
        "hitter": hitter,
        "statcast": statcast,
        "park": ParkFactors("Test Park"),
        "weather": WeatherContext("Test Park", "2026-07-13"),
        "matchup": MatchupContext(),
        "pitcher_statcast": pitcher,
        "expected_pa": LeagueBaselines().pa_per_game,
        "metadata": {"rich_features": {"recent_pa_15": 50, "roll15_xwoba": 0.350}},
    }
    payload.update(overrides)
    return PlayerFeatureBundle(**payload)


def main() -> int:
    healthy = health_for_bundle(bundle())
    check("hitter_advanced_statcast" in healthy.flags, "advanced hitter Statcast is recorded")
    check("opposing_pitcher_kbb_observed_profile" in healthy.flags,
          "observed opposing-pitcher K/BB profile is recorded")
    check("fitted_lineup_slot_pa" in healthy.flags, "valid lineup slot records fitted PA path")
    check("rich_features_payload_present" in healthy.flags,
          "rich-feature payload transport is recorded")
    check("rolling_features_observed" in healthy.flags,
          "observed rolling-feature history is recorded separately")

    # PropEngine accepts a numeric string at its own boundary.  The audit must
    # record the same code path, rather than imposing a stricter type rule.
    string_slot_hitter = HitterGameContext(
        PlayerIdentity(111, "Test Hitter", "HOME"),
        GameContext(700001, "2026-07-13", "Test Park", True, "Away", "confirmed"),
        lineup_slot="3",  # type: ignore[arg-type]
        opposing_pitcher_id=222,
    )
    coerced_slot = health_for_bundle(bundle(hitter=string_slot_hitter))
    check("fitted_lineup_slot_pa" in coerced_slot.flags and coerced_slot.lineup_slot == 3,
          "numeric lineup-slot strings follow the simulation's fitted PA path")

    # Mutation: remove the advanced-statcast signal.  The audit must expose the
    # actual fallback rather than retaining the healthy label.
    fallback_profile = StatcastProfile(111, "Test Hitter", sample_pa=0, xwoba=None)
    fallback = health_for_bundle(bundle(statcast=fallback_profile))
    check("hitter_statcast_league_fallback" in fallback.flags,
          "mutation: missing Statcast triggers visible fallback flag")
    check("hitter_advanced_statcast" not in fallback.flags,
          "mutation: missing Statcast cannot retain advanced label")

    # Mutation: a missing pitcher profile is exactly the path where the
    # simulation supplies league K/BB rates.  The audit must see it.
    no_pitcher = health_for_bundle(bundle(pitcher_statcast=None))
    check("opposing_pitcher_kbb_league_fallback" in no_pitcher.flags,
          "mutation: missing pitcher profile exposes league K/BB fallback")
    check("opposing_pitcher_payload_present" not in no_pitcher.flags,
          "mutation: missing pitcher cannot retain payload label")

    # Mutation: numeric league defaults can arrive inside a non-null dataclass.
    # Object presence and numeric values must not be mislabelled as an observed
    # pitcher sample when sample_pa proves there was no observed profile.
    numeric_fallback_pitcher = PitcherStatcastProfile(
        222, "League fallback", sample_pa=0, k_rate=0.22, bb_rate=0.08, hr_per_9=1.2
    )
    numeric_fallback = health_for_bundle(bundle(pitcher_statcast=numeric_fallback_pitcher))
    check("opposing_pitcher_payload_present" in numeric_fallback.flags,
          "numeric fallback retains the factual payload-present label")
    check("opposing_pitcher_league_fallback_profile" in numeric_fallback.flags,
          "mutation: sample_pa=0 is labelled league fallback despite numeric rates")
    check("opposing_pitcher_kbb_observed_profile" not in numeric_fallback.flags,
          "mutation: numeric fallback cannot masquerade as observed K/BB")
    check(not numeric_fallback.opposing_pitcher_profile_present,
          "mutation: profile-present boolean requires an observed sample")

    nonempty_without_rolling = health_for_bundle(
        bundle(metadata={"rich_features": {"xwoba": 0.350, "recent_pa_15": 0}})
    )
    check("rich_features_payload_present" in nonempty_without_rolling.flags,
          "nonempty enriched payload is recorded as transport")
    check("rolling_features_unavailable" in nonempty_without_rolling.flags,
          "mutation: nonempty payload cannot invent rolling-feature history")

    # Mutation: an invalid slot makes PropEngine pass None to the simulator,
    # activating its legacy PA route.
    invalid_hitter = HitterGameContext(
        PlayerIdentity(111, "Test Hitter", "HOME"),
        GameContext(700001, "2026-07-13", "Test Park", True, "Away", "unknown"),
        lineup_slot=0,
        opposing_pitcher_id=222,
    )
    invalid = health_for_bundle(bundle(hitter=invalid_hitter))
    check("legacy_lineup_slot_pa_fallback" in invalid.flags,
          "mutation: invalid lineup slot exposes legacy PA fallback")
    check("fitted_lineup_slot_pa" not in invalid.flags,
          "mutation: invalid lineup slot cannot retain fitted PA label")

    rows = health_rows([bundle(), bundle(statcast=fallback_profile)], categories=("hits", "home_runs"))
    summary = summarize_health(rows)
    check(summary["projection_rows"] == 4, "explicit category scope expands each player-game exactly once")
    check(summary["by_category"]["hits"]["flag_counts"]["hitter_statcast_league_fallback"] == 1,
          "per-category summary sees the fallback")
    check(summary["by_category"]["hits"] == summary["by_category"]["home_runs"],
          "shared hitter inputs yield matching category health summaries")

    try:
        health_rows([bundle()], categories=())
    except ValueError:
        check(True, "missing category scope hard-fails rather than inferring a universe")
    else:
        check(False, "missing category scope hard-fails rather than inferring a universe")

    print(f"\n{PASS}/{PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
