"""
Offline harness — B4 gate tools (fitter, verdict, roster builder).

Validates the NEW gate tooling on synthetic data before it touches real
pairs. Run from repo root:
    python scripts/check_b4_gate_tools_offline.py

Groups:
  F — fit_role_innings.fit_constants: fits from adequate data, keeps
      placeholders on thin data (thin-sample honesty), ordering guards,
      block validates through the REAL estimator, hash-fork preview.
  V — run_b4_gate_verdict: known-answer (a genuinely better candidate is
      called CANDIDATE with CI < 0), identical probs are a TIE with
      dbrier == 0, the over-threshold uses ceil(line), unmatched keys are
      dropped, bootstrap is deterministic under a seed.
  E — end-to-end main(): a passing scenario exits 0; an "K probs never
      moved" plumbing failure exits 2 (verdict refused, not misread).
  R — build_pitcher_roster: id extraction and row shaping via a stub client.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.fit_role_innings import fit_constants  # noqa: E402
from run_analyze_k_error import DEFAULT_ROLE_INNINGS_BLOCK  # noqa: E402
from src.prediction.role_innings import RoleAwareInningsEstimator  # noqa: E402
from src.utils.model_version import model_version  # noqa: E402
import run_b4_gate_verdict as verdict  # noqa: E402
from scripts.build_pitcher_roster import build_roster, extract_pitcher_ids  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((PASS if cond else FAIL, name, detail))


def _merged(role_ips: dict[str, list[float]]) -> pd.DataFrame:
    rows = []
    for role, ips in role_ips.items():
        rows += [{"role": role, "actual_ip": ip} for ip in ips]
    return pd.DataFrame(rows)


PLACEHOLDER = dict(DEFAULT_ROLE_INNINGS_BLOCK)

# ---------------------------------------------------------------- GROUP F
rng = np.random.default_rng(3)
rich = _merged({
    "opener": list(rng.normal(1.4, 0.4, 40).clip(0.3, 3.0)),
    "bulk": list(rng.normal(3.2, 0.6, 40).clip(1.5, 4.5)),
    "starter": list(rng.normal(5.6, 1.0, 120).clip(2.0, 9.0)),
})
block, report = fit_constants(rich, PLACEHOLDER, min_n=15)
check("F1 opener_innings fitted from adequate data",
      "opener_innings" in report["fitted"]
      and abs(block["opener_innings"] - rich[rich.role == "opener"].actual_ip.mean()) < 0.06,
      f"got {block['opener_innings']}")
check("F1 bulk_innings fitted from adequate data",
      "bulk_innings" in report["fitted"], str(block["bulk_innings"]))
check("F1 fitted block enabled=True with all required keys",
      block.get("enabled") is True and all(k in block for k in PLACEHOLDER))
check("F1 structural keys untouched",
      all(block[k] == PLACEHOLDER[k] for k in
          ("starter_min_ratio", "opener_max_ratio", "min_games_for_role",
           "starter_ip_floor", "starter_ip_ceil", "default_innings")))

thin = _merged({"opener": [1.2, 1.5], "bulk": [3.0, 3.3, 2.9],
                "starter": list(rng.normal(5.6, 1.0, 40))})
block_t, report_t = fit_constants(thin, PLACEHOLDER, min_n=15)
check("F2 thin roles keep placeholders",
      block_t["opener_innings"] == PLACEHOLDER["opener_innings"]
      and block_t["bulk_innings"] == PLACEHOLDER["bulk_innings"])
check("F2 both-thin is flagged (the DO-NOT-GATE-YET condition)",
      {"opener_innings", "bulk_innings"} <= set(report_t["kept_placeholder"]))

hot_bulk = _merged({"opener": list(rng.normal(1.4, 0.3, 30)),
                    "bulk": list(rng.normal(4.6, 0.3, 30)),   # empirically above floor 4.0
                    "starter": list(rng.normal(5.6, 1.0, 60))})
block_h, report_h = fit_constants(hot_bulk, PLACEHOLDER, min_n=15)
check("F3 bulk clamped to starter_ip_floor when empirical mean exceeds it",
      block_h["bulk_innings"] == PLACEHOLDER["starter_ip_floor"],
      str(block_h["bulk_innings"]))

inverted = _merged({"opener": list(rng.normal(3.5, 0.2, 30)),
                    "bulk": list(rng.normal(1.5, 0.2, 30)),
                    "starter": list(rng.normal(5.6, 1.0, 60))})
block_i, report_i = fit_constants(inverted, PLACEHOLDER, min_n=15)
check("F4 inverted opener>=bulk reverts both to placeholders",
      block_i["opener_innings"] == PLACEHOLDER["opener_innings"]
      and block_i["bulk_innings"] == PLACEHOLDER["bulk_innings"]
      and "ordering_note" in report_i)

try:
    RoleAwareInningsEstimator({"role_innings": block})
    check("F5 fitted block constructs the REAL estimator cleanly", True)
except Exception as exc:  # noqa: BLE001
    check("F5 fitted block constructs the REAL estimator cleanly", False, repr(exc))

base_cfg = {"season": 2026, "weights": {"season": 0.35, "recent": 0.65}}
check("F6 fitted block forks model_version vs base config",
      model_version(dict(base_cfg, role_innings=block)) != model_version(base_cfg))

# ---------------------------------------------------------------- GROUP V
def synth(n_dates=30, rows_per_date=40, seed=11, bias=0.15):
    """Frozen/candidate prob frames + outcomes; candidate = true p, frozen biased."""
    r = np.random.default_rng(seed)
    recs, outs = [], []
    pid = 0
    for d in range(n_dates):
        date = f"2024-05-{(d % 28) + 1:02d}x{d}"  # unique string per block
        for _ in range(rows_per_date):
            pid += 1
            p = r.uniform(0.25, 0.75)
            over = r.random() < p
            recs.append(dict(player_id=pid, game_date=date, category="strikeouts",
                             line=5.5, p_true=p,
                             p_frozen=float(np.clip(p + bias, 0.01, 0.99))))
            outs.append(dict(player_id=pid, game_date=date, category="strikeouts",
                             actual_value=6 if over else 4))
    df = pd.DataFrame(recs)
    frozen = df[["player_id", "game_date", "category", "line"]].assign(sim_p_over=df.p_frozen)
    candidate = df[["player_id", "game_date", "category", "line"]].assign(sim_p_over=df.p_true)
    return frozen, candidate, pd.DataFrame(outs).rename(columns={"actual_value": "actual"})

frozen, candidate, outcomes = synth()
metrics, drift = verdict.compare(frozen, candidate, outcomes, "sim_p_over", b=1500, seed=5)
row = metrics.iloc[0]
check("V1 known-answer: better candidate verdict = CANDIDATE",
      row["verdict"] == "CANDIDATE", row["verdict"])
check("V1 known-answer: CI strictly below zero", row["ci_hi"] < 0,
      f"[{row['ci_lo']}, {row['ci_hi']}]")
check("V1 known-answer: Murphy skill higher for candidate",
      row["murphy_candidate"] > row["murphy_frozen"])

metrics_tie, drift_tie = verdict.compare(frozen, frozen.copy(), outcomes, "sim_p_over",
                                         b=1500, seed=5)
r_tie = metrics_tie.iloc[0]
check("V2 identical probs: dbrier == 0 and verdict TIE",
      r_tie["dbrier"] == 0 and r_tie["verdict"] == "TIE",
      f"{r_tie['dbrier']} {r_tie['verdict']}")
check("V2 identical probs: drift is exactly zero",
      float(drift_tie["mean_abs_dp"].iloc[0]) == 0.0)

# ceil(line) convention: integer line 2.0 -> over means actual >= 2 (NOT > 2).
tiny_keys = dict(player_id=[1, 2, 3, 4], game_date=["d1", "d1", "d2", "d2"],
                 category=["hits"] * 4, line=[2.0] * 4)
tiny = pd.DataFrame(dict(**tiny_keys, sim_p_over=[0.5] * 4))
tiny_out = pd.DataFrame(dict(player_id=[1, 2, 3, 4], game_date=["d1", "d1", "d2", "d2"],
                             category=["hits"] * 4, actual=[2, 1, 2, 1]))
m_tiny, _ = verdict.compare(tiny, tiny.copy(), tiny_out, "sim_p_over", b=100, seed=1)
check("V3 over-threshold uses ceil(line): actual==2 is over for line 2.0",
      float(m_tiny.iloc[0]["base_rate"]) == 0.5, str(m_tiny.iloc[0]["base_rate"]))

cand_missing = candidate.iloc[:-100]
m_part, _ = verdict.compare(frozen, cand_missing, outcomes, "sim_p_over", b=200, seed=5)
check("V4 unmatched keys are dropped (inner-join)",
      int(m_part["n"].sum()) == len(cand_missing), str(int(m_part["n"].sum())))

m_a, _ = verdict.compare(frozen, candidate, outcomes, "sim_p_over", b=800, seed=42)
m_b, _ = verdict.compare(frozen, candidate, outcomes, "sim_p_over", b=800, seed=42)
check("V5 bootstrap deterministic under seed",
      m_a[["ci_lo", "ci_hi"]].equals(m_b[["ci_lo", "ci_hi"]]))

# ---------------------------------------------------------------- GROUP E
tmp = Path(tempfile.mkdtemp())
hit = pd.DataFrame(dict(player_id=[9001, 9002, 9001, 9002],
                        game_date=["2024-05-01x0", "2024-05-01x0", "2024-05-02x1", "2024-05-02x1"],
                        category=["hits"] * 4, line=[1.5] * 4,
                        sim_p_over=[0.4, 0.6, 0.5, 0.3]))
hit_out = hit.rename(columns={"sim_p_over": "actual_value"}).drop(columns=["line"]).assign(
    actual_value=[2, 1, 2, 0])
frozen_full = pd.concat([frozen, hit], ignore_index=True)
candidate_full = pd.concat([candidate, hit], ignore_index=True)  # hitters identical
outcomes_full = pd.concat(
    [outcomes.rename(columns={"actual": "actual_value"}), hit_out], ignore_index=True)
fz, cd, pr = tmp / "frozen.csv", tmp / "cand.csv", tmp / "pairs.csv"
frozen_full.to_csv(fz, index=False)
candidate_full.to_csv(cd, index=False)
outcomes_full.to_csv(pr, index=False)

rc_pass = verdict.main(["--frozen", str(fz), "--candidate", str(cd), "--pairs", str(pr),
                        "--b", "1000", "--out", str(tmp / "metrics.csv")])
check("E1 end-to-end passing scenario exits 0", rc_pass == 0, f"rc={rc_pass}")
check("E1 metrics CSV written", (tmp / "metrics.csv").exists())

rc_plumb = verdict.main(["--frozen", str(fz), "--candidate", str(fz), "--pairs", str(pr),
                         "--b", "500"])
check("E2 identical-K plumbing failure exits 2 (verdict refused)",
      rc_plumb == 2, f"rc={rc_plumb}")

# ---------------------------------------------------------------- GROUP R
pairs_csv = tmp / "pairs_ids.csv"
pd.DataFrame(dict(
    player_id=["777", "555", "777", "111", "999"],
    category=["strikeouts", "strikeouts", "strikeouts", "hits", "strikeouts"],
    game_date=["2026-07-01"] * 5,
)).to_csv(pairs_csv, index=False)
ids = extract_pitcher_ids(pairs_csv)
check("R1 id extraction: unique, sorted, strikeouts-only",
      ids == [555, 777, 999], str(ids))


class _StubSnap:
    def __init__(self, g, gs, ip):
        self.games, self.games_started, self.innings_pitched = g, gs, ip


class _StubClient:
    def get_pitching_stats(self, pid):
        if pid == 999:
            raise RuntimeError("boom")
        return _StubSnap({555: 58, 777: 12}[pid], {555: 1, 777: 12}[pid],
                         {555: 61.2, 777: 70.0}[pid]), None


roster = build_roster(ids, _StubClient(), sleep_seconds=0.0)
check("R2 roster rows shaped correctly; failed fetch skipped",
      len(roster) == 2
      and list(roster.columns) == ["player_id", "games", "games_started", "innings_pitched"]
      and int(roster.loc[roster.player_id == 555, "games"].iloc[0]) == 58
      and float(roster.loc[roster.player_id == 777, "innings_pitched"].iloc[0]) == 70.0)

# fitted opener plugs into A6's --role-innings-config loader shape
cand_json = tmp / "candidate_role_innings.json"
cand_json.write_text(json.dumps({"role_innings": block}), encoding="utf-8")
from run_analyze_k_error import load_role_innings_block  # noqa: E402
loaded, _src = load_role_innings_block(cand_json)
check("R3 emitted JSON round-trips through A6's own loader",
      loaded["opener_innings"] == block["opener_innings"] and loaded["enabled"] is True)

# ---------------------------------------------------------------- REPORT
n_fail = sum(1 for r in results if r[0] == FAIL)
width = max(len(name) for _, name, _ in results)
for status, name, detail in results:
    line = f"[{status}] {name.ljust(width)}"
    if detail and status == FAIL:
        line += f"   -- {detail}"
    print(line)
print(f"\n{len(results) - n_fail}/{len(results)} checks passed")
sys.exit(1 if n_fail else 0)
