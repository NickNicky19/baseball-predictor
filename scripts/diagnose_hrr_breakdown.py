"""
Diagnose HRR overshoot: break a day's projections into hits / runs / RBI
components so we can see whether the safeguard cap is wrong (false alarms)
or the model is genuinely running hot (real overshoot).

Usage:
    python scripts/diagnose_hrr_breakdown.py 2026-07-03
    python scripts/diagnose_hrr_breakdown.py 2026-07-03 "Heliot Ramos" "Austin Hedges"

Prints, per player: expected_pa, mean hits, mean runs, mean RBI, mean HR,
mean HRR, the OLD cap, and a PROPOSED cap — plus the raw per-PA outcome
probabilities so we can see if HR/PA or hit/PA is being pinned at a clamp.
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.dataclasses import LeagueBaselines  # noqa: E402
from src.data.mlb_api import MLBStatsAPI  # noqa: E402
from src.features.feature_factory import FeatureFactory  # noqa: E402
from src.prediction.prop_engine import PropEngine  # noqa: E402


def load_config() -> dict:
    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "config" / "config.json").read_text(encoding="utf-8"))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("usage: python scripts/diagnose_hrr_breakdown.py YYYY-MM-DD [player names...]")
        return
    game_date = args[0]
    wanted = {a.lower() for a in args[1:]}

    config = load_config()
    league = LeagueBaselines.from_config(config)
    mlb_api = MLBStatsAPI(season=int(config.get("season", 2026)))
    factory = FeatureFactory(config=config, league_baselines=league, mlb_api=mlb_api)
    engine = PropEngine(config=config, league_baselines=league)

    bundles = factory.build_bundles(game_date)
    if wanted:
        bundles = [b for b in bundles if b.hitter.player.name.lower() in wanted]

    print(f"{'player':22s} {'PA':>4s} {'hits':>5s} {'runs':>5s} {'rbi':>5s} "
          f"{'HR':>5s} {'HRR':>6s} {'oldcap':>6s} {'newcap':>6s} {'hit/PA':>6s} {'HR/PA':>6s}  flag")
    print("-" * 100)

    for b in sorted(bundles, key=lambda x: x.hitter.player.name):
        sim_input = engine._bundle_to_sim_input(b, rich_features=b.metadata.get("rich_features", {}))
        gs = engine.monte_carlo.game_simulator
        gs.seed(12345)

        n = 4000
        th = tr = trbi = thr = 0
        for _ in range(n):
            r = gs.simulate_game(sim_input)
            th += r.hits
            tr += r.runs
            trbi += r.rbi
            thr += r.home_runs
        hits, runs, rbi, hr = th / n, tr / n, trbi / n, thr / n
        hrr = hits + runs + rbi

        probs = gs.pa_simulator.expected_outcome_probabilities(
            statcast=sim_input.statcast,
            rich_features=sim_input.rich_features,
            pitcher_k_pct=sim_input.pitcher_k_pct,
            pitcher_bb_pct=sim_input.pitcher_bb_pct,
            park_hr_factor=sim_input.park_hr_factor * sim_input.weather_hr_factor,
            park_hits_factor=sim_input.park_hits_factor,
            pitcher_hr_per_9=sim_input.pitcher_hr_per_9,
        )
        hit_pa = probs["single"] + probs["double"] + probs["triple"] + probs["home_run"]

        pa = b.expected_pa
        old_cap = league.pa_per_game * min(0.50, (league.hits_per_game / league.pa_per_game) * 1.75) + pa * 0.35
        hrr_rate = (league.hits_per_game / league.pa_per_game) + (0.47 / league.pa_per_game) * 2
        new_cap = hrr_rate * pa * 1.9

        flag = ""
        if hrr > new_cap:
            flag = "*** REAL OVERSHOOT"
        elif hrr > old_cap:
            flag = "false alarm (old cap)"

        print(f"{b.hitter.player.name:22s} {pa:4.1f} {hits:5.2f} {runs:5.2f} {rbi:5.2f} "
              f"{hr:5.3f} {hrr:6.2f} {old_cap:6.2f} {new_cap:6.2f} {hit_pa:6.3f} "
              f"{probs['home_run']:6.4f}  {flag}")


if __name__ == "__main__":
    main()

