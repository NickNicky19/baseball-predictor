import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.prediction.daily_predictor import DailyPredictor
from src.prediction.prop_engine import PropEngine

def validate_model(date: str = "2026-07-01", sample_size: int = 50):
    print(f"Validating model for {date}...\n")
    
    predictor = DailyPredictor()
    bundles = predictor.build_feature_bundles(date)[:sample_size]
    
    prop_engine = PropEngine()
    
    total_hits = 0
    total_hr = 0
    total_hrr = 0
    count = 0
    
    for bundle in bundles:
        # Get hits and home runs
        hits_proj = prop_engine.project_hitter(bundle, categories=("hits",))
        hr_proj = prop_engine.project_hitter(bundle, categories=("home_runs",))
        
        hits_val = hits_proj[0].projected_value if hits_proj else 0
        hr_val = hr_proj[0].projected_value if hr_proj else 0
        
        total_hits += hits_val
        total_hr += hr_val
        total_hrr += (hits_val + hr_val)  # rough HRR proxy
        count += 1
    
    print(f"Sample size: {count}")
    print(f"Average Projected Hits: {total_hits / count:.2f}")
    print(f"Average Projected HR:   {total_hr / count:.2f}")
    print(f"Average HRR (approx):   {total_hrr / count:.2f}")
    print()
    
    # Quick realism check
    if 0.9 <= (total_hits / count) <= 1.5:
        print("✓ Hit projections look realistic")
    else:
        print("⚠ Hit projections still look off")

if __name__ == "__main__":
    validate_model()
