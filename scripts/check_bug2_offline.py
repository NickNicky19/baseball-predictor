"""Offline harness for BUG 2 fixes. Uses only stdlib + the real model_version /
migration / backfill logic (copied minimally where repo imports are unavailable).
Checks:
 1. model_version never returns '' (empty config still hashes)
 2. migration PRESERVES an existing stamp (not blanked)
 3. migration leaves genuine legacy (no-column) rows blank -> backfill's job
 4. the write-path guard raises on a row missing model_version
 5. backfill fills only blanks, is idempotent, and refuses on conflicting version
"""
import csv, sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.utils.model_version import model_version, MODEL_CONFIG_KEYS

PAIR_COLUMNS = [
    "player_id","player_name","game_date","category","predicted_value",
    "actual_value","confidence","model_version",
    "actual_pa","actual_hits","actual_doubles","actual_triples","actual_home_runs","actual_runs","actual_rbi",
    "actual_walks","actual_strikeouts","actual_ip","actual_bb_allowed","actual_hr_allowed",
]

def migrate(path: Path):
    if not path.exists() or path.stat().st_size == 0: return
    with path.open(newline="") as h:
        header = next(csv.reader(h), None)
    if header == PAIR_COLUMNS or header is None: return
    if not set(header).issubset(set(PAIR_COLUMNS)): return
    with path.open(newline="") as h:
        rows = list(csv.DictReader(h))
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=PAIR_COLUMNS, restval=""); w.writeheader(); w.writerows(rows)
    tmp.replace(path)

def append_guard(rows):
    missing = [r for r in rows if not r.get("model_version")]
    if missing:
        raise ValueError("blank model_version")
    return len(rows)

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)

REAL = {"season":2026,"weights":{"a":1},"park_factors":{"x":1.0}}  # stand-in real config
TARGET = model_version(REAL)

# 1
check("model_version({}) is non-empty", model_version({}) != "")
check("model_version(real) is non-empty & stable", TARGET and TARGET == model_version(REAL))

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    # 2 existing 8-col with a stamp
    p = d/"c2.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PAIR_COLUMNS[:8]); w.writeheader()
        w.writerow(dict(player_id=1,player_name="A",game_date="2026-07-01",category="hits",predicted_value=1.2,actual_value=2,confidence=0.6,model_version=TARGET))
    migrate(p)
    got = list(csv.DictReader(p.open()))[0]["model_version"]
    check("migration preserves existing stamp", got == TARGET)

    # 3 legacy 7-col -> blank after migration
    p2 = d/"c3.csv"
    with p2.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PAIR_COLUMNS[:7]); w.writeheader()
        w.writerow(dict(player_id=1,player_name="A",game_date="2026-07-01",category="hits",predicted_value=1.2,actual_value=2,confidence=0.6))
    migrate(p2)
    check("legacy row is blank after migration (needs backfill)", list(csv.DictReader(p2.open()))[0]["model_version"] == "")

# 4 write guard
try:
    append_guard([{"player_id":1,"model_version":""}]); check("write-guard raises on blank", False)
except ValueError:
    check("write-guard raises on blank", True)
try:
    check("write-guard passes when stamped", append_guard([{"player_id":1,"model_version":TARGET}]) == 1)
except ValueError:
    check("write-guard passes when stamped", False)

# 5 backfill semantics (inline mimic)
def backfill(rows, target):
    conflicting = {(r.get("model_version") or "").strip() for r in rows
                   if (r.get("model_version") or "").strip() and (r.get("model_version") or "").strip() != target}
    if conflicting: raise RuntimeError("conflict")
    n = 0
    for r in rows:
        if not (r.get("model_version") or "").strip():
            r["model_version"] = target; n += 1
    return n

rows = [{"model_version":""},{"model_version":""},{"model_version":TARGET}]
n1 = backfill(rows, TARGET)
check("backfill fills only blanks", n1 == 2 and all(r["model_version"]==TARGET for r in rows))
n2 = backfill(rows, TARGET)
check("backfill idempotent", n2 == 0)
try:
    backfill([{"model_version":"deadbeef0000"}], TARGET); check("backfill refuses conflicting version", False)
except RuntimeError:
    check("backfill refuses conflicting version", True)

passed = sum(1 for _,c in results if c)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
