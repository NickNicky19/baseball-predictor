#!/usr/bin/env python3
"""Mutation checks for the opposing-pitcher contact-quality audit."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_pitcher_contact_inputs import extract_pitcher_contacts, validate_hash  # noqa: E402
from src.evaluation.pitcher_contact_skill import (  # noqa: E402
    add_pitcher_probability,
    build_prior_window_profiles,
    fit_rolling_fold,
    paired_interval,
    validate_pitcher_contact_rows,
)
from src.utils.provenance import sha256_file  # noqa: E402


def fixture_rows() -> pd.DataFrame:
    rows = []
    for day in range(1, 9):
        date = f"2024-04-{day:02d}"
        for player in (1, 2):
            good_pitcher = (day + player) % 2 == 0
            pitcher_xba = 0.16 if good_pitcher else 0.44
            hits = 0 if good_pitcher else 2
            rows.append({
                "game_pk": 1000 + day,
                "player_id": player,
                "game_date": date,
                "opp_sp_id": 11 if good_pitcher else 22,
                "baseline_p": 0.30,
                "pitcher_xba": pitcher_xba,
                "pitcher_bip": 40.0,
                "league_xba": 0.30,
                "league_bip": 400.0,
                "contact_events": 4,
                "hits_on_contact": hits,
                "lineup_slot": player,
                "roll15_xba": 0.30,
                "roll15_bip": 20,
                "recent_pa_15": 40,
                "roll30_xba": 0.30,
                "roll30_bip": 40,
                "recent_pa_30": 80,
            })
    return validate_pitcher_contact_rows(pd.DataFrame(rows))


def expect_failure(callable_) -> bool:
    try:
        callable_()
    except (ValueError, FileNotFoundError):
        return True
    return False


def main() -> int:
    contacts = pd.DataFrame([
        {"contact_date": "2024-03-30", "pitcher_id": 11, "xba": 0.10},
        {"contact_date": "2024-03-31", "pitcher_id": 11, "xba": 0.30},
        {"contact_date": "2024-04-01", "pitcher_id": 11, "xba": 1.00},
    ])
    target = pd.DataFrame([{"game_date": "2024-04-01", "opp_sp_id": 11}])
    profile = build_prior_window_profiles(contacts, target, window_days=45)
    assert profile.pitcher_bip.iloc[0] == 2
    assert abs(profile.pitcher_xba.iloc[0] - 0.20) < 1e-12
    print("[OK] MUTATION target-date contact sentinel is excluded")

    rows = fixture_rows()
    neutral = rows.copy()
    neutral["pitcher_xba"] = neutral.league_xba
    neutral = add_pitcher_probability(neutral, prior_strength=30, alpha=3.0)
    assert np.array_equal(neutral.candidate_p.to_numpy(), neutral.baseline_p.to_numpy())
    print("[OK] neutral pitcher signal reproduces baseline exactly")

    missing = rows.copy()
    missing["pitcher_bip"] = 0.0
    missing["pitcher_xba"] = missing.league_xba
    missing = add_pitcher_probability(missing, prior_strength=0.0, alpha=3.0)
    assert len(missing) == len(rows)
    assert np.array_equal(missing.candidate_p.to_numpy(), missing.baseline_p.to_numpy())
    print("[OK] missing pitcher history is retained with exactly neutral fallback")

    null_identity = rows.copy()
    null_identity.loc[null_identity.index[0], "opp_sp_id"] = np.nan
    assert expect_failure(lambda: validate_pitcher_contact_rows(null_identity))
    print("[OK] MUTATION null opponent-pitcher identity fails")

    fit_rows = rows[rows.game_date <= "2024-04-04"].copy()
    fit_rows["season"] = 2023
    fit_rows["game_date"] = fit_rows.game_date.str.replace("2024-", "2023-", regex=False)
    evaluation = rows[rows.game_date > "2024-04-04"].copy()
    evaluation["season"] = 2024
    combined = pd.concat([fit_rows, evaluation], ignore_index=True)
    fitted, evaluation = fit_rolling_fold(
        combined, fit_seasons=[2023], evaluation_season=2024
    )
    changed = combined.copy()
    changed.loc[changed.season.eq(2024), "hits_on_contact"] = (
        changed.loc[changed.season.eq(2024), "contact_events"]
        - changed.loc[changed.season.eq(2024), "hits_on_contact"]
    )
    refit, _ = fit_rolling_fold(changed, fit_seasons=[2023], evaluation_season=2024)
    assert fitted == refit
    print("[OK] MUTATION evaluation truth cannot move fold-fit parameters")

    scored = add_pitcher_probability(evaluation, prior_strength=fitted.prior_strength, alpha=fitted.alpha)
    swapped = evaluation.copy()
    swapped[["pitcher_xba", "pitcher_bip"]] = swapped.groupby("game_date")[["pitcher_xba", "pitcher_bip"]].transform(
        lambda column: column.iloc[::-1].to_numpy()
    )
    swapped = add_pitcher_probability(swapped, prior_strength=fitted.prior_strength, alpha=fitted.alpha)
    assert not np.array_equal(scored.candidate_p.to_numpy(), swapped.candidate_p.to_numpy())
    before = paired_interval(
        scored, metric="brier", declared_dates=scored.game_date.unique(), bootstrap=1000, seed=17
    )
    after = paired_interval(
        swapped, metric="brier", declared_dates=swapped.game_date.unique(), bootstrap=1000, seed=17
    )
    assert (before.lower, before.upper) != (after.lower, after.upper)
    print("[OK] MUTATION swapped pitcher identities move probabilities and score evidence")

    identical = scored.copy()
    identical["candidate_p"] = identical.baseline_p
    zero = paired_interval(
        identical, metric="log_loss", declared_dates=identical.game_date.unique(), bootstrap=1000, seed=17
    )
    assert zero.lower == zero.upper == 0.0
    print("[OK] identical arm yields an exactly zero paired interval")

    with tempfile.TemporaryDirectory(prefix="pitcher_contact_") as tmp:
        root = Path(tmp)
        cache = root / "batter_111.csv"
        pd.DataFrame([{
            "game_date": "2024-04-01", "game_pk": 1, "batter": 222,
            "pitcher": 333, "type": "X", "events": "single", "at_bat_number": 1,
            "estimated_ba_using_speedangle": 0.8,
        }]).to_csv(cache, index=False)
        files = [{"path": str(cache), "bytes": cache.stat().st_size, "sha256": sha256_file(cache)}]
        canonical = json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        inventory = {
            "schema_version": "statcast-contact-source-inventory-v1",
            "files": files,
            "tree_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "file_count": 1,
            "bytes": cache.stat().st_size,
        }
        inventory_path = root / "inventory.json"
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        assert expect_failure(lambda: extract_pitcher_contacts(inventory_path, inventory["tree_sha256"]))
    print("[OK] MUTATION raw Statcast batter/file identity mismatch fails")

    with tempfile.TemporaryDirectory(prefix="pitcher_hash_") as tmp:
        path = Path(tmp) / "source.txt"
        path.write_text("locked", encoding="utf-8")
        assert expect_failure(lambda: validate_hash(path, "0" * 64, "fixture source"))
    print("[OK] MUTATION protocol/source hash mismatch fails before scoring")

    print("9/9")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
