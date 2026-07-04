import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.prediction.daily_predictor import DailyPredictor
from src.prediction.prop_engine import PropEngine
from src.simulation.game_simulator import GameSimulatorInput

def diagnose_simulation_count(player_name: str = "Steven Kwan", date: str = "2026-07-01", n_sims: int = 5):
    print(f"\n=== Diagnosing Simulation Count & Hit Accumulation for {player_name} ===\n")

    predictor = DailyPredictor()
    bundles = predictor.build_feature_bundles(date)

    target_bundle = next((b for b in bundles if player_name.lower() in b.hitter.player.name.lower()), None)

    if not target_bundle:
        print(f"Player '{player_name}' not found.")
        return

    prop_engine = PropEngine()
    sim_input = prop_engine._bundle_to_sim_input(target_bundle)

    print(f"Player: {target_bundle.hitter.player.name}")
    print(f"Expected PA from bundle: {target_bundle.expected_pa:.2f}")
    print(f"Running {n_sims} individual game simulations...\n")

    total_hits = 0
    total_pa = 0

    for i in range(n_sims):
        # Simulate one game
        result = prop_engine.monte_carlo.game_simulator.simulate_game(sim_input)

        print(f"Simulation {i+1}:")
        print(f"  Actual PAs simulated : {result.plate_appearances}")
        print(f"  Hits recorded        : {result.hits}")
        print(f"  Home Runs            : {result.home_runs}")
        print(f"  Runs                 : {result.runs}")
        print(f"  RBI                  : {result.rbi}")
        print()

        total_hits += result.hits
        total_pa += result.plate_appearances

    avg_pa = total_pa / n_sims
    avg_hits = total_hits / n_sims

    print("=== Summary ===")
    print(f"Average PAs simulated per game : {avg_pa:.2f}")
    print(f"Average hits per game          : {avg_hits:.2f}")
    print(f"Expected hits (from bundle)    : {target_bundle.expected_pa * 0.3463:.2f}")
    print()

    if avg_pa > target_bundle.expected_pa * 1.3:
        print("⚠️  WARNING: Average simulated PAs are significantly higher than expected.")
    if avg_hits > (target_bundle.expected_pa * 0.3463) * 1.4:
        print("⚠️  WARNING: Average hits are significantly higher than probability engine predicts.")


if __name__ == "__main__":
    diagnose_simulation_count("Steven Kwan", n_sims=5)
