import sys
sys.path.insert(0, '.')

from src.prediction.daily_predictor import DailyPredictor
from src.prediction.prop_engine import PropEngine

def diagnose_player_probabilities(player_name: str = "Steven Kwan", date: str = "2026-07-01"):
    print(f"\n=== Diagnosing {player_name} on {date} ===\n")

    predictor = DailyPredictor()
    bundles = predictor.build_feature_bundles(date)

    target_bundle = next((b for b in bundles if player_name.lower() in b.hitter.player.name.lower()), None)

    if not target_bundle:
        print(f"Player '{player_name}' not found.")
        return

    prop_engine = PropEngine()

    # === Raw Statcast Inputs ===
    sc = target_bundle.statcast
    print("Underlying Statcast Inputs")
    print("-" * 45)
    print(f"xwOBA            : {sc.xwoba}")
    print(f"xSLG             : {sc.xslg}")
    print(f"Barrel Rate      : {sc.barrel_rate}")
    print(f"Hard Hit Rate    : {sc.hard_hit_rate}")
    print(f"Avg Exit Velocity: {getattr(sc, 'avg_exit_velocity', None)}")
    print(f"Avg Launch Angle : {getattr(sc, 'avg_launch_angle', None)}")
    print(f"Sample PA        : {sc.sample_pa}")
    print()

    # === Outcome Probabilities (fixed access) ===
    outcome_probs = prop_engine.probability_engine.from_bundle(target_bundle)

    # Convert dataclass to dict for easier handling
    probs = {
        "strikeout": outcome_probs.strikeout,
        "walk": outcome_probs.walk,
        "home_run": outcome_probs.home_run,
        "single": outcome_probs.single,
        "double": outcome_probs.double,
        "triple": outcome_probs.triple,
        "out_on_bip": outcome_probs.out_on_bip,
    }

    print("Per-PA Outcome Probabilities")
    print("-" * 45)
    for key, value in probs.items():
        print(f"{key:15}: {value:.4f} ({value*100:.2f}%)")

    total = sum(probs.values())
    print("-" * 45)
    print(f"{'TOTAL':15}: {total:.4f}")

    # Additional diagnostics
    hit_prob = probs["single"] + probs["double"] + probs["triple"] + probs["home_run"]
    non_out_prob = total - probs["out_on_bip"]

    print("\nProbability Diagnostics")
    print("-" * 45)
    print(f"Hit Probability/PA : {hit_prob:.4f}")
    print(f"Non-Out Probability: {non_out_prob:.4f}")
    print(f"Out Probability    : {probs['out_on_bip']:.4f}")

    # === Compare Probability Engine vs Monte Carlo ===
    projections = prop_engine.project_hitter(
        target_bundle,
        categories=("hits", "home_runs", "hrr")
    )

    print("\nProjected Outputs (Monte Carlo)")
    print("-" * 45)
    for p in projections:
        print(f"{p.category:12}: {p.projected_value}")

    expected_hits_from_probs = hit_prob * target_bundle.expected_pa
    print(f"\nExpected Hits (from probabilities × PA): {expected_hits_from_probs:.2f}")

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    diagnose_player_probabilities("Steven Kwan")
