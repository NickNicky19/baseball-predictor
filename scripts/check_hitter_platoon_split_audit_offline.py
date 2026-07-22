#!/usr/bin/env python3
"""Mutation checks for the hitter handedness-split audit."""
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

from scripts.audit_hitter_platoon_splits import (  # noqa: E402
    extract_terminal_pa,
    validate_terminal_outcomes,
    verify_hash,
)
from src.evaluation.handedness_split_skill import (  # noqa: E402
    add_split_probability,
    build_prior_split_profiles,
    fit_rolling_split,
    paired_date_interval,
    validate_split_rows,
)
from src.utils.provenance import sha256_file  # noqa: E402


def expect_failure(callable_) -> bool:
    try:
        callable_()
    except (ValueError, FileNotFoundError):
        return True
    return False


def rows_fixture() -> pd.DataFrame:
    rows = []
    for day in range(1, 9):
        for player in (1, 2):
            favorable = (day + player) % 2 == 0
            rows.append({
                "game_pk": 1000 + day, "player_id": player,
                "game_date": f"2024-04-{day:02d}", "season": 2024,
                "baseline_p": 0.25, "split_rate": 0.12 if favorable else 0.42,
                "split_n": 30, "pooled_rate": 0.25, "pooled_n": 60,
                "exposure": 4, "successes": 0 if favorable else 2,
            })
    return validate_split_rows(pd.DataFrame(rows))


def temp_inventory(root: Path, records: list[dict]) -> tuple[Path, str]:
    path = root / "batter_111.csv"
    pd.DataFrame(records).to_csv(path, index=False)
    files = [{"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}]
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tree = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    payload = {
        "schema_version": "statcast-contact-source-inventory-v1", "files": files,
        "tree_sha256": tree, "file_count": 1, "bytes": path.stat().st_size,
    }
    inventory = root / "inventory.json"
    inventory.write_text(json.dumps(payload), encoding="utf-8")
    return inventory, tree


def raw_row(**overrides) -> dict:
    row = {
        "game_date": "2024-03-30", "game_pk": 1, "batter": 111, "pitcher": 222,
        "p_throws": "L", "type": "X", "events": "single", "at_bat_number": 1,
        "estimated_ba_using_speedangle": 0.2,
    }
    row.update(overrides)
    return row


def main() -> int:
    terminal = pd.DataFrame([
        {"event_date": "2024-03-30", "batter_id": 1, "p_throws": "L", "contact_xba": .10, "is_k": 1, "is_bb": 0},
        {"event_date": "2024-03-31", "batter_id": 1, "p_throws": "R", "contact_xba": .40, "is_k": 0, "is_bb": 1},
        {"event_date": "2024-04-01", "batter_id": 1, "p_throws": "L", "contact_xba": 1.0, "is_k": 0, "is_bb": 0},
    ])
    target_l = pd.DataFrame([{"game_date": "2024-04-01", "player_id": 1, "opp_sp_throws": "L"}])
    target_r = target_l.assign(opp_sp_throws="R")
    left = build_prior_split_profiles(terminal, target_l, window_days=45)
    right = build_prior_split_profiles(terminal, target_r, window_days=45)
    assert left.split_contact_n.iloc[0] == 1 and abs(left.split_contact_rate.iloc[0] - .10) < 1e-12
    print("[OK] MUTATION target-date handedness sentinel is excluded")
    assert left.split_contact_rate.iloc[0] != right.split_contact_rate.iloc[0]
    print("[OK] MUTATION swapping opposing hand moves split evidence")

    rows = rows_fixture()
    neutral = rows.copy()
    neutral["split_rate"] = neutral.pooled_rate
    neutral = add_split_probability(neutral, prior_strength=20, alpha=3)
    assert np.array_equal(neutral.candidate_p.to_numpy(), neutral.baseline_p.to_numpy())
    missing = rows.copy()
    missing["split_n"] = 0
    missing["split_rate"] = np.nan
    missing = add_split_probability(missing, prior_strength=0, alpha=3)
    assert len(missing) == len(rows) and np.array_equal(missing.candidate_p, missing.baseline_p)
    print("[OK] zero signal and missing history are exactly neutral")

    fit_rows = rows[rows.game_date <= "2024-04-04"].copy()
    fit_rows["season"] = 2023
    fit_rows["game_date"] = fit_rows.game_date.str.replace("2024-", "2023-", regex=False)
    evaluation = rows[rows.game_date > "2024-04-04"].copy()
    combined = pd.concat([fit_rows, evaluation], ignore_index=True)
    fitted, evaluation = fit_rolling_split(combined, fit_seasons=[2023], evaluation_season=2024)
    changed = combined.copy()
    changed.loc[changed.season.eq(2024), "successes"] = (
        changed.loc[changed.season.eq(2024), "exposure"] - changed.loc[changed.season.eq(2024), "successes"]
    )
    refit, _ = fit_rolling_split(changed, fit_seasons=[2023], evaluation_season=2024)
    assert fitted == refit
    print("[OK] MUTATION evaluation truth cannot move fold-fit parameters")

    scored = add_split_probability(evaluation, prior_strength=fitted.prior_strength, alpha=fitted.alpha)
    swapped = evaluation.copy()
    swapped["split_rate"] = swapped.groupby("game_date").split_rate.transform(lambda x: x.iloc[::-1].to_numpy())
    swapped = add_split_probability(swapped, prior_strength=fitted.prior_strength, alpha=fitted.alpha)
    assert not np.array_equal(scored.candidate_p, swapped.candidate_p)
    print("[OK] MUTATION opposing-hand swap moves candidate probabilities")

    identical = scored.copy()
    identical["candidate_p"] = identical.baseline_p
    zero = paired_date_interval(
        identical, metric="brier", declared_dates=identical.game_date.unique(),
        bootstrap=1000, seed=17, family_size=3,
    )
    assert zero.lower == zero.upper == 0.0
    print("[OK] identical arm gives an exactly zero family-adjusted interval")

    changed_k = terminal.copy()
    changed_k.loc[0, "is_k"] = 0
    original_profile = build_prior_split_profiles(terminal, target_l, window_days=45)
    changed_profile = build_prior_split_profiles(changed_k, target_l, window_days=45)
    assert original_profile.split_k_sum.iloc[0] != changed_profile.split_k_sum.iloc[0]
    assert original_profile.split_bb_sum.iloc[0] == changed_profile.split_bb_sum.iloc[0]
    assert original_profile.split_contact_sum.iloc[0] == changed_profile.split_contact_sum.iloc[0]
    print("[OK] MUTATION K truth moves only K split evidence")

    official = pd.DataFrame([{
        "game_pk": 1, "player_id": 111, "out_pa": 1, "out_k": 0, "out_bb": 0,
    }])
    raw_alignment = pd.DataFrame([{
        "game_pk": 1, "batter_id": 111, "events": "single", "is_k": 0, "is_bb": 0,
    }])
    assert validate_terminal_outcomes(raw_alignment, official)["terminal_outcome_alignment_mismatches"] == 0
    mutated_official = official.copy()
    mutated_official["out_k"] = 1
    assert expect_failure(lambda: validate_terminal_outcomes(raw_alignment, mutated_official))
    print("[OK] MUTATION raw terminal K/BB disagreement with official truth fails")

    with tempfile.TemporaryDirectory(prefix="platoon_source_") as tmp:
        root = Path(tmp)
        inv, tree = temp_inventory(root, [raw_row(batter=999)])
        assert expect_failure(lambda: extract_terminal_pa(inv, tree))
    print("[OK] MUTATION raw batter/file identity mismatch fails")

    with tempfile.TemporaryDirectory(prefix="platoon_hand_") as tmp:
        root = Path(tmp)
        inv, tree = temp_inventory(root, [raw_row(p_throws="S")])
        assert expect_failure(lambda: extract_terminal_pa(inv, tree))
    print("[OK] MUTATION invalid pitcher hand fails")

    with tempfile.TemporaryDirectory(prefix="platoon_dup_") as tmp:
        root = Path(tmp)
        inv, tree = temp_inventory(root, [raw_row(), raw_row()])
        assert expect_failure(lambda: extract_terminal_pa(inv, tree))
    print("[OK] MUTATION duplicate terminal PA identity fails")

    with tempfile.TemporaryDirectory(prefix="platoon_truncated_") as tmp:
        root = Path(tmp)
        inv, tree = temp_inventory(root, [
            raw_row(),
            raw_row(events="truncated_pa", at_bat_number=2, type="B", estimated_ba_using_speedangle=np.nan),
        ])
        extracted, summary = extract_terminal_pa(inv, tree)
        assert len(extracted) == 1 and summary["excluded_truncated_pa_rows"] == 1
    print("[OK] MUTATION Statcast truncated_pa is excluded from official PA evidence")

    with tempfile.TemporaryDirectory(prefix="platoon_hash_") as tmp:
        path = Path(tmp) / "source.txt"
        path.write_text("locked", encoding="utf-8")
        assert expect_failure(lambda: verify_hash(path, "0"*64, "fixture"))
    print("[OK] MUTATION source hash mismatch fails")

    print("13/13")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
