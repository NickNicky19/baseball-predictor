#!/usr/bin/env python3
"""
Offline harness for A6 (run_analyze_k_error.py) -- synthetic data only, no
network, no real pairs CSV required. Validates against the REAL
src/prediction/role_innings.RoleAwareInningsEstimator (not a
reimplementation), so this also doubles as a regression check that the
analysis tool's math stays consistent with B4's actual code.

Run from repo root:
    python scripts/check_a6_offline.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

# run_*.py entry points live at repo root; this harness assumes it's invoked
# from the repo root, same convention as scripts/check_b4_offline.py etc.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_analyze_k_error as a6  # noqa: E402
from src.prediction.role_innings import RoleAwareInningsEstimator  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


# ---------------------------------------------------------------------------
# Synthetic data: three season-role archetypes with a FIXED per-inning K rate,
# so predicted_value is built from the REAL legacy heuristic (reproducing the
# documented phantom-start bug for the opener) and actual outcomes are built
# from realized (short) outings. This lets every downstream number be
# hand-checked against the real RoleAwareInningsEstimator's own output.
# ---------------------------------------------------------------------------

K_RATE_PER_IP = 1.0  # synthetic constant K/inning, chosen for clean arithmetic

ROLE_ARCHETYPES = {
    # true reliever/opener: effectively 0 real starts, appears often, short
    # outings. games_started=1 here (not 0) deliberately mirrors production:
    # mlb_api._parse_pitching floors games_started to >=1 upstream (games,
    # the new UNFLOORED field, is what actually carries the "zero starts"
    # signal) -- so this is the realistic input both the legacy heuristic and
    # RoleAwareInningsEstimator would actually receive for such a pitcher.
    "reliever": dict(games=40, games_started=1, season_ip=48.0,   # legacy: ip/gs=48/1=48 -> clamped to 7.5
                      actual_ip_mean=1.3, actual_ip_sd=0.3),
    # swing/bulk arm: some starts, some relief, moderate outings.
    "swing":    dict(games=40, games_started=15, season_ip=110.0,  # legacy: 110/15=7.33 -> clamped to 7.5
                      actual_ip_mean=3.4, actual_ip_sd=0.5),
    # genuine starter: almost every appearance is a start, full outings.
    "starter":  dict(games=30, games_started=29, season_ip=170.0,  # legacy: 170/29=5.86 (no clamp needed)
                      actual_ip_mean=5.8, actual_ip_sd=0.7),
}

ROLE_INNINGS_BLOCK = {
    "enabled": True,
    "starter_min_ratio": 0.80,
    "opener_max_ratio": 0.20,
    "min_games_for_role": 3,
    "starter_ip_floor": 4.0,
    "starter_ip_ceil": 7.0,
    "bulk_innings": 3.5,
    "opener_innings": 1.5,
    "default_innings": 5.5,
}


def make_synthetic_pairs(rng: np.random.Generator, n_per_role: int = 50):
    """Returns (pairs_df, roster_df) with legacy-consistent predicted_value."""
    estimator = RoleAwareInningsEstimator({"role_innings": ROLE_INNINGS_BLOCK})
    rows = []
    roster_rows = []
    pid = 2000
    for role_name, cfg in ROLE_ARCHETYPES.items():
        stub = a6._RecentStub(games=cfg["games"], games_started=cfg["games_started"],
                               innings_pitched=cfg["season_ip"])
        legacy_ip = RoleAwareInningsEstimator._legacy_expected_ip(stub)
        for i in range(n_per_role):
            pid += 1
            actual_ip = max(0.1, rng.normal(cfg["actual_ip_mean"], cfg["actual_ip_sd"]))
            actual_k = max(0.0, actual_ip * K_RATE_PER_IP + rng.normal(0, 0.3))
            # predicted_value AS THE LIVE MODEL WOULD HAVE SHIPPED IT: built
            # from the same legacy_ip every real prediction for this pitcher
            # this season would have used (prop_engine's k_prob is pinned to
            # K_RATE_PER_IP here for a clean, checkable synthetic).
            predicted = max(0.0, legacy_ip * K_RATE_PER_IP + rng.normal(0, 0.2))
            rows.append({
                "player_id": pid, "player_name": f"{role_name}_{i}",
                "game_date": "2025-06-01", "category": "strikeouts",
                "predicted_value": round(predicted, 2),
                "actual_value": round(actual_k, 2), "confidence": 0.6,
                "model_version": "dab23f4fbac8",
                "actual_pa": "", "actual_hits": "", "actual_home_runs": "",
                "actual_runs": "", "actual_rbi": "", "actual_walks": "",
                "actual_strikeouts": round(actual_k, 2),
                "actual_ip": round(actual_ip, 2),
                "actual_bb_allowed": 1, "actual_hr_allowed": 0,
            })
            roster_rows.append({
                "player_id": pid, "games": cfg["games"],
                "games_started": cfg["games_started"], "innings_pitched": cfg["season_ip"],
            })
    pairs_df = pd.DataFrame(rows)[a6.PAIR_COLUMNS]
    roster_df = pd.DataFrame(roster_rows)
    return pairs_df, roster_df


def main() -> int:
    rng = np.random.default_rng(17)
    pairs_df, roster_df = make_synthetic_pairs(rng)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pairs_csv = tmp_path / "prediction_outcomes.csv"
        roster_csv = tmp_path / "roster.csv"
        role_cfg_json = tmp_path / "role_innings.json"
        out_dir = tmp_path / "out"
        pairs_df.to_csv(pairs_csv, index=False)
        roster_df.to_csv(roster_csv, index=False)
        role_cfg_json.write_text(json.dumps({"role_innings": ROLE_INNINGS_BLOCK}), encoding="utf-8")

        # --- 1. Loading -----------------------------------------------------
        print("\n[1] load_k_rows")
        k = a6.load_k_rows(pairs_csv)
        check("loads expected row count", len(k) == len(pairs_df),
              f"got {len(k)}, expected {len(pairs_df)}")
        check("k_error column present and correct sign",
              (k["k_error"] == (k["actual_strikeouts"] - k["predicted_value"])).all())
        check("actual_ip > 0 filter applied", (k["actual_ip"] > 0).all())

        bad_csv = tmp_path / "bad.csv"
        pd.DataFrame({"foo": [1, 2]}).to_csv(bad_csv, index=False)
        try:
            a6.load_k_rows(bad_csv)
            check("raises on missing columns", False, "no exception raised")
        except ValueError:
            check("raises on missing columns", True)
        try:
            a6.load_k_rows(tmp_path / "does_not_exist.csv")
            check("raises on missing file", False, "no exception raised")
        except FileNotFoundError:
            check("raises on missing file", True)

        # --- 2. actual_ip bucket report (layer 1, no roster) -----------------
        print("\n[2] ip_bucket_report")
        ip_bins = a6.DEFAULT_IP_BINS
        ip_report = a6.ip_bucket_report(k, ip_bins)
        check("bucket report non-empty", not ip_report.empty)
        check("bucket ns sum to total rows", int(ip_report["n"].sum()) == len(k))
        opener_bucket = ip_report[ip_report["ip_bucket"] == "[1,2)"]
        starter_bucket = ip_report[ip_report["ip_bucket"] == "[5,6)"]
        if not opener_bucket.empty and not starter_bucket.empty:
            opener_err = float(opener_bucket["mean_error"].iloc[0])
            starter_err = float(starter_bucket["mean_error"].iloc[0])
            check("short-outing bucket shows over-projection (mean_error << 0)",
                  opener_err < -3.0, f"mean_error={opener_err}")
            check("bias magnitude decreases from short-outing to starter-length bucket",
                  abs(opener_err) > abs(starter_err),
                  f"opener={opener_err} starter={starter_err}")

        # --- 3. real estimator: legacy reproduction --------------------------
        print("\n[3] RoleAwareInningsEstimator._legacy_expected_ip reproduction")
        for role_name, cfg in ROLE_ARCHETYPES.items():
            stub = a6._RecentStub(games=cfg["games"], games_started=cfg["games_started"],
                                   innings_pitched=cfg["season_ip"])
            legacy = RoleAwareInningsEstimator._legacy_expected_ip(stub)
            expected_raw = cfg["season_ip"] / cfg["games_started"]
            expected_clamped = round(max(4.0, min(7.5, expected_raw)), 1)
            check(f"legacy expected_ip for {role_name} matches hand calc",
                  legacy == expected_clamped, f"got {legacy}, expected {expected_clamped}")
        # the documented bug, reproduced directly: the reliever archetype has
        # effectively zero real starts (games=40 appearances) but
        # games_started=1 (floored upstream, per mlb_api._parse_pitching), so
        # the legacy heuristic divides ~a full season of RELIEF innings by a
        # single phantom start and reports a starter-length outing (>5.0)
        # for a pitcher who actually throws ~1.3 IP per appearance.
        reliever_legacy = RoleAwareInningsEstimator._legacy_expected_ip(
            a6._RecentStub(games=40, games_started=1, innings_pitched=48.0)
        )
        check("reliever archetype reproduces the phantom-start bug (legacy >> actual outing)",
              reliever_legacy > 5.0, f"got {reliever_legacy}")

        # --- 4. real estimator: role classification --------------------------
        print("\n[4] RoleAwareInningsEstimator.estimate_detailed role routing")
        estimator = RoleAwareInningsEstimator({"role_innings": ROLE_INNINGS_BLOCK})
        for role_name, cfg in ROLE_ARCHETYPES.items():
            stub = a6._RecentStub(games=cfg["games"], games_started=cfg["games_started"],
                                   innings_pitched=cfg["season_ip"])
            result = estimator.estimate_detailed(stub)
            expected_role = {"reliever": "opener", "swing": "bulk", "starter": "starter"}[role_name]
            check(f"{role_name} archetype classifies as role={expected_role}",
                  result.role == expected_role, f"got {result.role}")
        thin_stub = a6._RecentStub(games=2, games_started=0, innings_pitched=3.0)
        thin_result = estimator.estimate_detailed(thin_stub)
        check("thin sample (games < min_games_for_role) -> unknown",
              thin_result.role == "unknown", f"got {thin_result.role}")
        zero_ip_stub = a6._RecentStub(games=40, games_started=0, innings_pitched=0.0)
        zero_result = estimator.estimate_detailed(zero_ip_stub)
        check("zero innings_pitched -> unknown (no div-by-zero, no route)",
              zero_result.role == "unknown", f"got {zero_result.role}")
        check("zero innings_pitched falls back to default_innings",
              zero_result.expected_innings == ROLE_INNINGS_BLOCK["default_innings"])

        # --- 5. add_role_and_recompute + before/after math --------------------
        print("\n[5] add_role_and_recompute (exact rescale math)")
        roster = a6.load_roster(roster_csv)
        merged = a6.add_role_and_recompute(k, roster, estimator)
        check("every matched row got a role", (merged["role"] != "no_roster_row").all())
        check("role labels match expected archetypes",
              set(merged["role"]) == {"opener", "bulk", "starter"},
              f"got {sorted(set(merged['role']))}")

        # Hand-verify the rescale for one specific row.
        row0 = merged.iloc[0]
        expected_new = row0["predicted_value"] * (row0["b4_expected_innings"] / row0["legacy_expected_innings"])
        check("new_predicted_k matches the hand-computed rescale for a sample row",
              abs(row0["new_predicted_k"] - expected_new) < 1e-9,
              f"got {row0['new_predicted_k']}, expected {expected_new}")

        role_ip_df = a6.role_ip_recompute_report(merged, ip_bins)
        check("role x ip recompute report non-empty", not role_ip_df.empty)
        role_sum = a6.per_role_summary(merged)
        check("per-role summary has 3 roles", len(role_sum) == 3, f"got {len(role_sum)}")

        opener_row = role_sum[role_sum["role"] == "opener"].iloc[0]
        bulk_row = role_sum[role_sum["role"] == "bulk"].iloc[0]
        starter_row = role_sum[role_sum["role"] == "starter"].iloc[0]

        # THE key claim B4 needs evidence for: role-aware rescaling reduces
        # RMSE for the roles the legacy heuristic mis-sizes (opener, bulk),
        # and does not blow up RMSE for starters (whose legacy estimate was
        # already close to their real per-start length).
        check("opener role: new_rmse << old_rmse (fix reduces short-outing bias)",
              opener_row["new_rmse"] < opener_row["old_rmse"] * 0.5,
              f"old={opener_row['old_rmse']} new={opener_row['new_rmse']}")
        check("bulk role: new_rmse < old_rmse (fix reduces bias)",
              bulk_row["new_rmse"] < bulk_row["old_rmse"],
              f"old={bulk_row['old_rmse']} new={bulk_row['new_rmse']}")
        check("starter role: new_rmse not much worse than old_rmse (no regression)",
              starter_row["new_rmse"] <= starter_row["old_rmse"] + 0.5,
              f"old={starter_row['old_rmse']} new={starter_row['new_rmse']}")
        check("opener config_expected_innings matches ROLE_INNINGS_BLOCK.opener_innings",
              abs(opener_row["config_expected_innings"] - ROLE_INNINGS_BLOCK["opener_innings"]) < 1e-9)
        check("bulk config_expected_innings matches ROLE_INNINGS_BLOCK.bulk_innings",
              abs(bulk_row["config_expected_innings"] - ROLE_INNINGS_BLOCK["bulk_innings"]) < 1e-9)

        # --- 6. load_role_innings_block: explicit path, auto-discover, fallback
        print("\n[6] load_role_innings_block sourcing")
        block, source = a6.load_role_innings_block(role_cfg_json)
        check("explicit --role-innings-config path is used", "role_innings.json" in source)
        check("enabled forced True on in-memory copy", block["enabled"] is True)
        check("original file on disk is untouched (still enabled=true here, but by our own "
              "authored content, not mutated)",
              json.loads(role_cfg_json.read_text())["role_innings"]["enabled"] is True)

        block2, source2 = a6.load_role_innings_block(None)
        check("no path given falls back to auto-discover or built-in mirror",
              "role_innings.example.json" in source2 or "built-in placeholder" in source2,
              f"source={source2}")

        # a config with enabled=false should still get forced to True in-memory
        disabled_json = tmp_path / "disabled.json"
        disabled_block = dict(ROLE_INNINGS_BLOCK)
        disabled_block["enabled"] = False
        disabled_json.write_text(json.dumps({"role_innings": disabled_block}), encoding="utf-8")
        block3, _ = a6.load_role_innings_block(disabled_json)
        check("enabled=false source still forced to True in-memory", block3["enabled"] is True)
        check("disabled.json on disk remains untouched",
              json.loads(disabled_json.read_text())["role_innings"]["enabled"] is False)

        # a malformed config should raise RoleInningsConfigError when built into an estimator
        bad_block_json = tmp_path / "bad_role_innings.json"
        bad_block = dict(ROLE_INNINGS_BLOCK)
        bad_block["opener_max_ratio"] = 0.9  # invalid: not < starter_min_ratio
        bad_block_json.write_text(json.dumps({"role_innings": bad_block}), encoding="utf-8")
        block4, _ = a6.load_role_innings_block(bad_block_json)
        try:
            RoleAwareInningsEstimator({"role_innings": block4})
            check("malformed role_innings config raises RoleInningsConfigError", False,
                  "no exception raised")
        except Exception as exc:
            check("malformed role_innings config raises RoleInningsConfigError",
                  type(exc).__name__ == "RoleInningsConfigError", f"got {type(exc).__name__}")

        # --- 7. load_roster: CSV, JSON, missing innings_pitched ---------------
        print("\n[7] load_roster variants")
        roster_json = tmp_path / "roster.json"
        roster_json.write_text(
            json.dumps({str(int(r.player_id)): {"games": int(r.games),
                                                  "games_started": int(r.games_started),
                                                  "innings_pitched": float(r.innings_pitched)}
                        for r in roster_df.itertuples()}),
            encoding="utf-8",
        )
        roster_from_json = a6.load_roster(roster_json)
        check("JSON roster loads same row count as CSV roster",
              len(roster_from_json) == len(roster_df))

        no_ip_csv = tmp_path / "roster_no_ip.csv"
        roster_df[["player_id", "games", "games_started"]].to_csv(no_ip_csv, index=False)
        roster_no_ip = a6.load_roster(no_ip_csv)
        check("missing innings_pitched column defaults to 0.0",
              (roster_no_ip["innings_pitched"] == 0.0).all())

        # --- 8. end-to-end CLI (main()) ---------------------------------------
        print("\n[8] main() end-to-end, with --roster and --role-innings-config")
        rc = a6.main([
            "--pairs", str(pairs_csv),
            "--roster", str(roster_csv),
            "--role-innings-config", str(role_cfg_json),
            "--out-dir", str(out_dir),
            "--no-plot",
        ])
        check("main() exits 0", rc == 0, f"got {rc}")
        for fname in ("k_error_by_actual_ip.csv", "k_error_role_ip_recompute.csv",
                      "k_error_role_summary.csv", "k_error_summary.json"):
            check(f"{fname} written", (out_dir / fname).exists())
        payload = json.loads((out_dir / "k_error_summary.json").read_text())
        check("summary json has n_rows matching input", payload["n_rows"] == len(pairs_df))
        check("summary json records the role_innings block used",
              payload.get("role_innings_block_used", {}).get("opener_innings") ==
              ROLE_INNINGS_BLOCK["opener_innings"])

        # --- 9. main() without --roster still works (headline alone) ---------
        print("\n[9] main() without --roster")
        out_dir2 = tmp_path / "out2"
        rc2 = a6.main(["--pairs", str(pairs_csv), "--out-dir", str(out_dir2), "--no-plot"])
        check("main() without --roster exits 0", rc2 == 0, f"got {rc2}")
        check("ip bucket csv still written without --roster",
              (out_dir2 / "k_error_by_actual_ip.csv").exists())
        check("role csv NOT written without --roster",
              not (out_dir2 / "k_error_role_summary.csv").exists())

        # --- 10. model_version filter ------------------------------------------
        print("\n[10] --model-version filter")
        k_filtered = a6.load_k_rows(pairs_csv, model_version="dab23f4fbac8")
        check("model_version filter keeps matching rows", len(k_filtered) == len(pairs_df))
        try:
            a6.load_k_rows(pairs_csv, model_version="nonexistent_hash")
            check("model_version filter raises when nothing matches", False)
        except ValueError:
            check("model_version filter raises when nothing matches", True)

    print(f"\n{'=' * 40}\n{PASSED}/{PASSED + FAILED} checks passed\n{'=' * 40}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
