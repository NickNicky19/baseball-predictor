#!/usr/bin/env python3
"""Generate config/config.pa.json = live config + the fitted PA distribution path.

Derives from the LIVE config so the candidate cannot drift from it in any other
block. config/config.json is NOT modified.
"""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
live = root / "config" / "config.json"
cand = root / "config" / "config.pa.json"

cfg = json.loads(live.read_text(encoding="utf-8"))

cfg["base_running"] = {
    "_comment": (
        "League base-running params + the FITTED plate-appearance distribution. "
        "pa_distribution_path points at the artifact produced by "
        "scripts/fit_pa_distribution.py -- an EMPIRICAL P(pa | lineup_slot) fitted "
        "from >=16,818 training rows per slot. This is a FIT (rule 2), not a "
        "structural placeholder. It replaces _sample_pa_count's floor/floor+1 "
        "two-point draw, which produced EXACTLY ZERO games at pa<=2 (10.1% of "
        "reality) and ZERO at pa=6 (1.7%). MEASURED to remove +0.0339 of the "
        "+0.0890 P(hits>=1) bias on DK-gradeable rows. "
        "ABSENT -> the simulator is BYTE-IDENTICAL to pre-fix (verified: 3 seeds x "
        "40k games, incl. under the live config and with lineup_slot set). "
        "base_state_mix_rate / run_traffic_boost are NOT set here -- the HRR gate "
        "FAILED and they are not promoted; see config.hrr.json."
    ),
    "p_score_from_base": {
        "walk": 0.27, "single": 0.28, "double": 0.4, "triple": 0.56, "home_run": 1.0
    },
    "pa_distribution_path": "data/learning/pa_distribution.json",
}

with cand.open("w", encoding="utf-8", newline="\n") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
    f.write("\n")

# verify: ONLY base_running may differ from live, and corrections stay off
back = json.loads(cand.read_text(encoding="utf-8"))
orig = json.loads(live.read_text(encoding="utf-8"))
diff = sorted(k for k in set(orig) | set(back) if orig.get(k) != back.get(k))
print(f"wrote {cand}")
print(f"top-level keys differing from live config.json: {diff}")
print(f"pa_distribution_path = {back['base_running']['pa_distribution_path']}")
print(f"learning.apply_corrections = {back['learning']['apply_corrections']}  (must be False)")
assert diff == ["base_running"], f"candidate diverges in {diff} -- expected only base_running"
assert back["learning"]["apply_corrections"] is False
print("OK")
