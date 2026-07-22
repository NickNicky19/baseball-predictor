"""
The archive fix loaded 513 projections with 0 simulations. Two possible causes:
  (a) src/learning/prediction_archive.py was not actually overwritten
  (b) the JSON on disk genuinely has "simulation": null for hitters

(b) would mean the fix is correct but there is nothing to restore -- and the
REAL bug is upstream, in whatever wrote that file. Different, worse bug.

Do not guess. Check both.
"""
import inspect
import json
from pathlib import Path

from src.learning import prediction_archive as pa

print("=" * 60)
print("(a) Is the NEW loader actually in place?")
print("=" * 60)
src = inspect.getsource(pa._dict_to_projection)
has_fix = "_dict_to_simulation" in src
print(f"  loader calls _dict_to_simulation: {has_fix}")
if not has_fix:
    print("  -> the file was NOT overwritten. Save prediction_archive.py again.")

print()
print("=" * 60)
print("(b) What is actually ON DISK?")
print("=" * 60)
p = Path("data/learning/predictions/predictions_2026-07-11.json")
d = json.loads(p.read_text(encoding="utf-8"))

for label in ("hitter_projections", "pitcher_projections"):
    rows = d.get(label, []) or []
    with_sim = sum(1 for x in rows if x.get("simulation"))
    print(f"  {label:22s} {len(rows):4d} rows, {with_sim:4d} with a non-null simulation")

hp = d.get("hitter_projections", []) or []
if hp:
    print()
    print("  first hitter row:")
    print(f"    keys       : {list(hp[0].keys())}")
    print(f"    category   : {hp[0].get('category')}")
    print(f"    simulation : {hp[0].get('simulation')}")

# which categories DO carry a simulation?
print()
print("  simulation present, by category:")
from collections import Counter
have, lack = Counter(), Counter()
for label in ("hitter_projections", "pitcher_projections"):
    for x in d.get(label, []) or []:
        (have if x.get("simulation") else lack)[x.get("category")] += 1
for cat in sorted(set(have) | set(lack)):
    print(f"    {str(cat):12s} with={have[cat]:4d}  without={lack[cat]:4d}")
