#!/usr/bin/env python3
"""Offline harness for run_shadow_compare.py -- no network, synthetic fixtures.

Validates the failure-prone pieces: de-vig math, the ceil(line) threshold
bridge, name normalization (accents/suffixes), the honest matched/dropped
accounting, game filtering, and the CSV write. Run from anywhere.
"""
from __future__ import annotations

import csv
import json
import math
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_shadow_compare as sc  # noqa: E402

PASS = 0
FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


# --- name normalization -----------------------------------------------------
check(sc.norm_name("Fernando Tatis Jr.") == "fernando tatis", "norm strips Jr.")
check(sc.norm_name("Luis Urías") == "luis urias", "norm strips accent")
check(sc.norm_name("Vladimir Guerrero Jr.") == "vladimir guerrero", "norm strips Jr. (2)")
check(sc.norm_name("LaMonte Wade Jr.") == "lamonte wade", "norm mixed case + Jr.")
check(sc.norm_name("J.J. Wetherholt") == "jj wetherholt", "norm strips periods")
check(sc.norm_name("Michael Harris II") == "michael harris", "norm strips II")

# --- odds math --------------------------------------------------------------
check(abs(sc.american_to_prob(-200) - 0.6667) < 1e-3, "american -200 -> 0.667")
check(abs(sc.american_to_prob(+150) - 0.4) < 1e-3, "american +150 -> 0.400")
# balanced-ish two-way with vig: -120/+100 -> over implied 0.5455, under 0.5 -> devig ~0.5217
dv = sc.devig_two_way(-120, +100)
check(dv is not None and abs(dv - 0.5217) < 1e-3, "devig -120/+100 -> ~0.522")
# symmetric -110/-110 must devig to exactly 0.5
check(abs(sc.devig_two_way(-110, -110) - 0.5) < 1e-9, "devig -110/-110 -> exactly 0.5")
check(sc.devig_two_way(-110, None) is None, "one-sided market -> None (no dishonest de-vig)")

# --- ceil threshold bridge (the model-key vs market-line convention) --------
check(math.ceil(0.5) == 1 and math.ceil(1.5) == 2 and math.ceil(4.5) == 5,
      "ceil bridges 0.5->1, 1.5->2, 4.5->5")

# --- end-to-end on synthetic fixtures ---------------------------------------
tmp = Path(tempfile.mkdtemp())
pred = {
    "game_date": "2026-01-01",
    "hitter_projections": [
        {"player_name": "Luis Urías", "category": "hits", "projected_value": 0.9,
         "confidence": 0.7, "team": "Padres", "opponent": "Blue Jays",
         "simulation": {"p_ge_threshold": {"1.0": 0.68, "2.0": 0.25}}},
        {"player_name": "Fernando Tatis Jr.", "category": "hits", "projected_value": 1.1,
         "confidence": 0.8, "team": "Padres", "opponent": "Blue Jays",
         "simulation": {"p_ge_threshold": {"1.0": 0.72, "2.0": 0.30}}},
    ],
    "pitcher_projections": [
        {"player_name": "Shane Bieber", "category": "strikeouts", "projected_value": 4.6,
         "confidence": 0.72, "team": "Blue Jays", "opponent": "Padres",
         "simulation": {"p_ge_threshold": {"5.0": 0.26, "6.0": 0.15}}},
    ],
}
pred_path = tmp / "predictions_2026-01-01.json"
pred_path.write_text(json.dumps(pred), encoding="utf-8")

# lines: Urias 0.5 (matches model 1.0 via ceil), Tatis 1.5 (matches 2.0),
# Bieber 4.5 K (matches 5.0), plus an UNMATCHED player and a ONE-SIDED market.
lines_rows = [
    # Urias hits 0.5 both sides -> devig
    dict(player_name="Luis Urias", category="hits", side="Over", line_point="0.5",
         price_american="-140", home_team="San Diego Padres", away_team="Toronto Blue Jays", event_id="E1"),
    dict(player_name="Luis Urias", category="hits", side="Under", line_point="0.5",
         price_american="+110", home_team="San Diego Padres", away_team="Toronto Blue Jays", event_id="E1"),
    # Tatis hits 1.5 both sides (accent/suffix: feed says 'Fernando Tatis Jr.')
    dict(player_name="Fernando Tatis Jr.", category="hits", side="Over", line_point="1.5",
         price_american="+250", home_team="San Diego Padres", away_team="Toronto Blue Jays", event_id="E1"),
    dict(player_name="Fernando Tatis Jr.", category="hits", side="Under", line_point="1.5",
         price_american="-320", home_team="San Diego Padres", away_team="Toronto Blue Jays", event_id="E1"),
    # Bieber K 4.5 both sides
    dict(player_name="Shane Bieber", category="strikeouts", side="Over", line_point="4.5",
         price_american="-115", home_team="San Diego Padres", away_team="Toronto Blue Jays", event_id="E1"),
    dict(player_name="Shane Bieber", category="strikeouts", side="Under", line_point="4.5",
         price_american="-105", home_team="San Diego Padres", away_team="Toronto Blue Jays", event_id="E1"),
    # UNMATCHED player -> must show up in dropped
    dict(player_name="Nobody Here", category="hits", side="Over", line_point="0.5",
         price_american="-130", home_team="San Diego Padres", away_team="Toronto Blue Jays", event_id="E1"),
    dict(player_name="Nobody Here", category="hits", side="Under", line_point="0.5",
         price_american="+100", home_team="San Diego Padres", away_team="Toronto Blue Jays", event_id="E1"),
    # ONE-SIDED market -> must be skipped (can't de-vig)
    dict(player_name="Luis Urias", category="hits", side="Over", line_point="1.5",
         price_american="+300", home_team="San Diego Padres", away_team="Toronto Blue Jays", event_id="E1"),
    # a DIFFERENT game, to test --games filtering
    dict(player_name="Some Guy", category="hits", side="Over", line_point="0.5",
         price_american="-120", home_team="Houston Astros", away_team="Texas Rangers", event_id="E2"),
    dict(player_name="Some Guy", category="hits", side="Under", line_point="0.5",
         price_american="+100", home_team="Houston Astros", away_team="Texas Rangers", event_id="E2"),
]
lines_path = tmp / "lines_2026-01-01.csv"
with lines_path.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(lines_rows[0].keys()))
    w.writeheader()
    for r in lines_rows:
        w.writerow(r)

preds = sc.load_predictions(pred_path)
books, meta = sc.load_lines(lines_path)

# name+accent join: the Tatis Jr. line must find the model row
matched, dropped = sc.build_rows(preds, books, meta, None)
mnames = {(r["player"], r["line"]) for r in matched}
check(("Fernando Tatis Jr.", 1.5) in mnames, "accent+Jr. line joins to model row")
check(("Luis Urias", 0.5) in mnames, "0.5 hits line joins to model P(>=1) via ceil")
check(("Shane Bieber", 4.5) in mnames, "4.5 K line joins to model P(>=5) via ceil")

# the Urias 0.5 model_p_over must equal the model's 1.0 key (0.68), not 0.5-key
urias = next(r for r in matched if r["player"] == "Luis Urias" and r["line"] == 0.5)
check(abs(urias["model_p_over"] - 0.68) < 1e-9, "ceil maps 0.5 hits to model P(>=1)=0.68")
# and its devig: over -140 (0.5833) / under +110 (0.4762) -> 0.5504
check(abs(urias["market_p_over_devig"] - 0.5504) < 1e-3, "urias devig ~0.550")
check(abs(urias["gap"] - (0.68 - 0.5504)) < 1e-3, "urias gap = model - market")

# unmatched player must be in dropped, NOT silently gone
dnames = {d[1] for d in dropped}
check("Nobody Here" in dnames, "unmatched market player appears in dropped (honest)")
check("Some Guy" in dnames, "other-game player with no model row IS dropped (honest, no filter)")

# one-sided Urias 1.5 must be skipped entirely (not matched, not counted as dropped-for-no-model)
check(not any(r["player"] == "Luis Urias" and r["line"] == 1.5 for r in matched),
      "one-sided market is skipped, never matched")

# --games filter: restrict to Astros@Rangers -> only 'Some Guy' in scope
m2, d2 = sc.build_rows(preds, books, meta, sc.parse_game_filter("Astros@Rangers"))
scope_games = {r["game"] for r in m2} | {d[0] for d in d2}
check(all("Astros" in g or "Rangers" in g for g in scope_games) and scope_games,
      "--games filter restricts to the named matchup")

# main() writes a CSV and exits 0
out_csv = tmp / "out.csv"
rc = sc.main(["--date", "2026-01-01", "--pred", str(pred_path), "--lines", str(lines_path),
              "--out", str(out_csv)])
check(rc == 0, "main() exits 0 on good inputs")
check(out_csv.exists(), "main() wrote the comparison CSV")
with out_csv.open() as f:
    got = list(csv.DictReader(f))
check(len(got) == 3, "CSV has exactly the 3 matched props")
check(all("gap" in r and "model_p_over" in r for r in got), "CSV carries model/market/gap columns")

# missing input files -> exit 2, not a crash
rc_missing = sc.main(["--date", "2099-01-01", "--pred", str(tmp / "nope.json"),
                      "--lines", str(lines_path), "--out", str(tmp / "x.csv")])
check(rc_missing == 2, "missing prediction file -> exit 2 (clean, not a traceback)")

print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(0 if FAIL == 0 else 1)
