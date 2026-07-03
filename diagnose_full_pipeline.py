import sys
sys.path.insert(0, '.')

from src.prediction.daily_predictor import DailyPredictor
from src.prediction.prop_engine import PropEngine

def diagnose_full_pipeline(player_name: str = "Steven Kwan", date: str = "2026-07-01"):
    print(f"\n{'='*70}")
    print(f" FULL PIPELINE DIAGNOSTIC — {player_name} on {date}")
    print(f"{'='*70}\n")

    predictor = DailyPredictor()
    bundles = predictor.build_feature_bundles(date)

    target_bundle = next((b for b in bundles if player_name.lower() in b.hitter.player.name.lower()), None)

    if not target_bundle:
        print(f"Player '{player_name}' not found.")
        return

    prop_engine = PropEngine()

    # 1. Player & Bundle Info
    print("1. PLAYER & BUNDLE INFO")
    print("-" * 50)
    print(f"Player           : {target_bundle.hitter.player.name}")
    print(f"Team             : {target_bundle.hitter.player.team}")
    print(f"Opponent         : {target_bundle.hitter.game.opponent}")
    print(f"Expected PA      : {target_bundle.expected_pa:.2f}")
    print(f"Lineup Status    : {target_bundle.hitter.game.lineup_status}")
    print()

    # 2. Raw Statcast Inputs
    sc = target_bundle.statcast
    print("2. RAW STATCAST INPUTS")
    print("-" * 50)
    print(f"xwOBA            : {sc.xwoba}")
    print(f"xSLG             : {sc.xslg}")
    print(f"Barrel Rate      : {sc.barrel_rate}")
    print(f"Hard Hit Rate    : {sc.hard_hit_rate}")
    print(f"Avg Exit Velocity: {getattr(sc, 'avg_exit_velocity', None)}")
    print(f"Avg Launch Angle : {getattr(sc, 'avg_launch_angle', None)}")
    print(f"Sample PA        : {sc.sample_pa}")
    print()

    # 3. Context Multipliers
    print("3. CONTEXT MULTIPLIERS")
    print("-" * 50)
    print(f"Park Hits Factor   : {target_bundle.park.hits_factor:.3f}")
    print(f"Park HR Factor     : {target_bundle.park.hr_factor:.3f}")
    print(f"Weather HR Factor  : {target_bundle.metadata.get('weather_hr_factor', 1.0):.3f}")
    print(f"Platoon Advantage  : {target_bundle.matchup.platoon_advantage:.3f}")
    print(f"Recent Form Mult   : {target_bundle.matchup.recent_form_multiplier:.3f}")
    print(f"BvP OPS Factor     : {target_bundle.matchup.bvp_ops_factor:.3f}")
    print(f"BvP HR Factor      : {target_bundle.matchup.bvp_hr_factor:.3f}")
    print()

    # 4. Final Outcome Probabilities
    outcome_probs = prop_engine.probability_engine.from_bundle(target_bundle)

    probs = {
        "strikeout": outcome_probs.strikeout,
        "walk": outcome_probs.walk,
        "home_run": outcome_probs.home_run,
        "single": outcome_probs.single,
        "double": outcome_probs.double,
        "triple": outcome_probs.triple,
        "out_on_bip": outcome_probs.out_on_bip,
    }

    print("4. FINAL OUTCOME PROBABILITIES (Per PA)")
    print("-" * 50)
    for key, value in probs.items():
        print(f"{key:15}: {value:.4f} ({value*100:.2f}%)")

    total = sum(probs.values())
    print("-" * 50)
    print(f"{'TOTAL':15}: {total:.4f}")
    print()

    # 5. Probability Diagnostics
    hit_prob = probs["single"] + probs["double"] + probs["triple"] + probs["home_run"]
    non_out = total - probs["out_on_bip"]

    print("5. PROBABILITY DIAGNOSTICS")
    print("-" * 50)
    print(f"Hit Probability/PA : {hit_prob:.4f}")
    print(f"Non-Out Probability: {non_out:.4f}")
    print(f"Out Probability    : {probs['out_on_bip']:.4f}")
    print()

    # 6. Monte Carlo vs Probability Engine Comparison
    projections = prop_engine.project_hitter(
        target_bundle,
        categories=("hits", "home_runs", "hrr")
    )

    print("6. MONTE CARLO OUTPUT vs PROBABILITY ENGINE")
    print("-" * 50)

    expected_hits = hit_prob * target_bundle.expected_pa
    expected_hr = probs["home_run"] * target_bundle.expected_pa

    for p in projections:
        print(f"{p.category:12}: {p.projected_value}")

    print()
    print(f"Expected Hits (from probabilities) : {expected_hits:.2f}")
    print(f"Expected HR   (from probabilities) : {expected_hr:.2f}")
    print()

    # 7. Red Flags
    print("7. RED FLAGS & OBSERVATIONS")
    print("-" * 50)

    if probs["double"] > 0.10:
        print("⚠️  DOUBLE probability is extremely high (>10%)")
    if probs["single"] < 0.10:
        print("⚠️  SINGLE probability is extremely low (<10%)")
    if hit_prob > 0.32:
        print(f"⚠️  Hit probability per PA is high ({hit_prob:.1%})")
    if projections[0].projected_value > expected_hits * 1.4:
        print("⚠️  Monte Carlo is producing significantly more hits than probability engine predicts")

    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    diagnose_full_pipeline("Steven Kwan")
