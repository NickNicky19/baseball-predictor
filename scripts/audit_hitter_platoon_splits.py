#!/usr/bin/env python3
"""Rolling-origin audit of hitter splits by opposing-pitcher handedness."""
from __future__ import annotations

import argparse
import json
import re
import sys
from math import isinf
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.errors import EmptyDataError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.handedness_split_skill import (  # noqa: E402
    add_split_probability,
    build_prior_split_profiles,
    fit_rolling_split,
    paired_date_interval,
    score_difference,
    validate_split_rows,
)
from src.utils.provenance import sha256_file, sha256_json  # noqa: E402

RAW_COLUMNS = [
    "game_date", "game_pk", "batter", "pitcher", "p_throws", "type", "events",
    "at_bat_number", "estimated_ba_using_speedangle",
]
TRAINING_COLUMNS = [
    "season", "game_date", "game_pk", "player_id", "opp_sp_throws",
    "pit_pa", "pit_k", "pit_bb", "recent_pa_30", "roll30_k_rate", "roll30_bb_rate",
    "opp_sp_k9", "opp_sp_bb9", "out_pa", "out_k", "out_bb",
]


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def load_protocol(path: Path) -> dict:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if protocol.get("schema_version") != "hits-platoon-split-audit-protocol-v1":
        raise ValueError("unknown platoon split protocol schema")
    if protocol.get("status") != "locked_after_predecision_integrity_amendment":
        raise ValueError("platoon split protocol is not locked")
    if not protocol["scope"].get("may_2026_holdout_must_remain_unread"):
        raise ValueError("platoon split protocol does not protect May 2026")
    return protocol


def verify_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} missing: {path}")
    observed = sha256_file(path)
    if observed.lower() != expected.lower():
        raise ValueError(f"{label} hash mismatch: expected {expected}, observed {observed}")


def extract_terminal_pa(inventory_path: Path, expected_tree: str) -> tuple[pd.DataFrame, dict]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("schema_version") != "statcast-contact-source-inventory-v1":
        raise ValueError("unknown Statcast inventory schema")
    if inventory.get("tree_sha256") != expected_tree:
        raise ValueError("Statcast tree hash differs from locked protocol")
    frames: list[pd.DataFrame] = []
    excluded_truncated_pa = 0
    for item in inventory["files"]:
        path = Path(item["path"])
        verify_hash(path, item["sha256"], "raw Statcast cache")
        if path.stat().st_size != int(item["bytes"]):
            raise ValueError(f"raw Statcast size mismatch: {path}")
        match = re.fullmatch(r"batter_(\d+)\.csv", path.name)
        if not match:
            raise ValueError(f"unexpected Statcast cache name: {path}")
        expected_batter = int(match.group(1))
        try:
            raw = pd.read_csv(path, usecols=RAW_COLUMNS, low_memory=False)
        except EmptyDataError:
            continue
        if raw.empty:
            continue
        observed = set(pd.to_numeric(raw.batter, errors="raise").dropna().astype(int).unique())
        if observed - {expected_batter}:
            raise ValueError(
                f"Statcast batter/file identity mismatch in {path}: "
                f"expected {expected_batter}, saw {sorted(observed)[:5]}"
            )
        terminal = raw[raw.events.notna()].copy()
        truncated = terminal.events.astype(str).eq("truncated_pa")
        excluded_truncated_pa += int(truncated.sum())
        terminal = terminal[~truncated].copy()
        if terminal.empty:
            continue
        identity = ["game_date", "game_pk", "batter", "pitcher", "p_throws", "at_bat_number"]
        if terminal[identity].isna().any().any():
            raise ValueError(f"terminal PA lacks identity in {path}")
        terminal["event_date"] = pd.to_datetime(terminal.game_date, errors="raise").dt.strftime("%Y-%m-%d")
        terminal["game_pk"] = pd.to_numeric(terminal.game_pk, errors="raise").astype(int)
        terminal["batter_id"] = pd.to_numeric(terminal.batter, errors="raise").astype(int)
        terminal["pitcher_id"] = pd.to_numeric(terminal.pitcher, errors="raise").astype(int)
        terminal["at_bat_number"] = pd.to_numeric(terminal.at_bat_number, errors="raise").astype(int)
        terminal["p_throws"] = terminal.p_throws.astype(str).str.upper()
        if not terminal.p_throws.isin({"L", "R"}).all():
            raise ValueError(f"terminal PA has invalid pitcher hand in {path}")
        event = terminal.events.astype(str)
        terminal["is_k"] = event.isin({"strikeout", "strikeout_double_play"}).astype(int)
        terminal["is_bb"] = event.isin({"walk", "intent_walk"}).astype(int)
        terminal["contact_xba"] = np.where(
            terminal.type.astype(str).eq("X"),
            pd.to_numeric(terminal.estimated_ba_using_speedangle, errors="coerce"),
            np.nan,
        )
        frames.append(terminal[
            ["event_date", "game_pk", "batter_id", "pitcher_id", "p_throws", "at_bat_number", "events", "is_k", "is_bb", "contact_xba"]
        ])
    if not frames:
        raise ValueError("no terminal Statcast plate appearances found")
    terminal = pd.concat(frames, ignore_index=True)
    key = ["game_pk", "batter_id", "at_bat_number"]
    duplicate = terminal.duplicated(key, keep=False)
    if duplicate.any():
        raise ValueError(
            "duplicate terminal PA identity; examples:\n"
            + terminal.loc[duplicate, key + ["pitcher_id"]].head(20).to_string(index=False)
        )
    return terminal, {
        "source_file_count": int(inventory["file_count"]),
        "source_bytes": int(inventory["bytes"]),
        "source_tree_sha256": inventory["tree_sha256"],
        "terminal_pa": int(len(terminal)),
        "terminal_contact_xba": int(terminal.contact_xba.notna().sum()),
        "dates": int(terminal.event_date.nunique()),
        "batters": int(terminal.batter_id.nunique()),
        "excluded_truncated_pa_rows": excluded_truncated_pa,
    }


def sigmoid(value: pd.Series | np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(value, dtype=float), -35, 35)))


def validate_terminal_outcomes(terminal: pd.DataFrame, training: pd.DataFrame) -> dict:
    raw = terminal.groupby(["game_pk", "batter_id"], as_index=False).agg(
        raw_pa=("events", "size"), raw_k=("is_k", "sum"), raw_bb=("is_bb", "sum")
    ).rename(columns={"batter_id": "player_id"})
    official = training[["game_pk", "player_id", "out_pa", "out_k", "out_bb"]].copy()
    joined = official.merge(raw, on=["game_pk", "player_id"], how="left", validate="one_to_one")
    joined[["raw_pa", "raw_k", "raw_bb"]] = joined[["raw_pa", "raw_k", "raw_bb"]].fillna(0)
    mismatch = (
        joined.raw_pa.ne(joined.out_pa)
        | joined.raw_k.ne(joined.out_k)
        | joined.raw_bb.ne(joined.out_bb)
    )
    if mismatch.any():
        raise ValueError(
            f"raw terminal PA/K/BB disagree with official outcomes on {int(mismatch.sum())} player-games; examples:\n"
            + joined.loc[mismatch, [
                "game_pk", "player_id", "raw_pa", "out_pa", "raw_k", "out_k", "raw_bb", "out_bb"
            ]].head(20).to_string(index=False)
        )
    return {
        "official_player_games_checked": int(len(joined)),
        "terminal_outcome_alignment_mismatches": 0,
    }


def build_baseline_arms(
    training: pd.DataFrame,
    scored_contact: pd.DataFrame,
    profiles: pd.DataFrame,
    kbb: dict,
) -> dict[str, pd.DataFrame]:
    key = ["game_pk", "player_id"]
    identity = training.rename(columns={"game_date": "source_game_date", "season": "source_season"})
    contact = scored_contact.merge(identity[key + ["opp_sp_throws", "source_game_date", "source_season"]], on=key, how="left", validate="one_to_one")
    if contact.opp_sp_throws.isna().any() or not contact.game_date.astype(str).equals(contact.source_game_date.astype(str)):
        raise ValueError("contact arm lost or disagreed with training identity")
    contact = contact.merge(profiles, left_on=["game_date", "player_id", "opp_sp_throws"], right_on=["game_date", "player_id", "opp_sp_throws"], how="left", validate="many_to_one")
    contact = contact.rename(columns={
        "w30_pa_k120": "baseline_p", "split_contact_rate": "split_rate",
        "split_contact_n": "split_n", "pooled_contact_rate": "pooled_rate",
        "pooled_contact_n": "pooled_n", "contact_events": "exposure",
        "hits_on_contact": "successes",
    })
    contact["season"] = contact.source_season

    kbb_rows = training[
        training.out_pa.gt(0) & training.pit_pa.gt(0)
        & training[["roll30_k_rate", "roll30_bb_rate", "opp_sp_k9", "opp_sp_bb9"]].notna().all(axis=1)
        & training.opp_sp_throws.isin({"L", "R"})
    ].copy()
    kbb_rows = kbb_rows.merge(profiles, on=["game_date", "player_id", "opp_sp_throws"], how="left", validate="many_to_one")
    season_k = kbb_rows.pit_k / kbb_rows.pit_pa
    season_bb = kbb_rows.pit_bb / kbb_rows.pit_pa
    pitcher_k = (kbb_rows.opp_sp_k9 / 9.0) / 4.2 * 100.0
    pitcher_bb = (kbb_rows.opp_sp_bb9 / 9.0) / 4.2 * 100.0
    kfit = kbb["fits"]["K"]
    bbfit = kbb["fits"]["BB"]
    kbb_rows["baseline_k"] = sigmoid(
        kfit["intercept"] + kfit["hitter_season"]*season_k
        + kfit["hitter_recent"]*kbb_rows.roll30_k_rate + kfit["pitcher"]*pitcher_k
    )
    kbb_rows["baseline_bb"] = sigmoid(
        bbfit["intercept"] + bbfit["hitter_season"]*season_bb
        + bbfit["hitter_recent"]*kbb_rows.roll30_bb_rate + bbfit["pitcher"]*pitcher_bb
    )

    arms: dict[str, pd.DataFrame] = {"contact_xba": contact}
    for name, metric, baseline, success in (
        ("strikeout_rate", "k", "baseline_k", "out_k"),
        ("walk_rate", "bb", "baseline_bb", "out_bb"),
    ):
        arm = kbb_rows.rename(columns={
            baseline: "baseline_p", f"split_{metric}_rate": "split_rate",
            f"split_{metric}_n": "split_n", f"pooled_{metric}_rate": "pooled_rate",
            f"pooled_{metric}_n": "pooled_n", "out_pa": "exposure", success: "successes",
        }).copy()
        arms[name] = arm
    required = ["game_pk", "player_id", "game_date", "season", "baseline_p", "split_rate", "split_n", "pooled_rate", "pooled_n", "exposure", "successes"]
    validated = {}
    for name, frame in arms.items():
        try:
            validated[name] = validate_split_rows(
                frame[required + [c for c in ("opp_sp_throws",) if c in frame]]
            )
        except ValueError as exc:
            raise ValueError(f"{name} baseline universe is invalid: {exc}") from exc
    return validated


def compare(frame: pd.DataFrame, protocol: dict) -> dict:
    result = score_difference(frame)
    gate = protocol["development_gate"]
    for metric in ("brier", "log_loss"):
        interval = paired_date_interval(
            frame, metric=metric, declared_dates=frame.game_date.unique(),
            bootstrap=int(gate["bootstrap_draws"]), seed=int(gate["seed"]),
            family_size=int(gate["isolated_family_size"]),
        )
        result[f"{metric}_lower_family"] = interval.lower
        result[f"{metric}_upper_family"] = interval.upper
        result[f"{metric}_valid_draws"] = interval.valid_draws
    return result


def audit_arm(name: str, rows: pd.DataFrame, protocol: dict) -> tuple[dict, pd.DataFrame]:
    fold_reports = []
    evaluations = []
    for fold in protocol["chronology"]["folds"]:
        fitted, evaluation = fit_rolling_split(
            rows, fit_seasons=fold["fit_seasons"], evaluation_season=fold["evaluation_season"]
        )
        evaluation = add_split_probability(
            evaluation, prior_strength=fitted.prior_strength, alpha=fitted.alpha
        )
        report = compare(evaluation, protocol)
        report.update({
            "fold": fold["name"], "fit_alpha": fitted.alpha,
            "fit_prior_strength": "infinity" if isinf(fitted.prior_strength) else fitted.prior_strength,
            "fit_log_loss": fitted.log_loss, "fit_brier": fitted.brier,
        })
        fold_reports.append(report)
        evaluation["fold"] = fold["name"]
        evaluations.append(evaluation)
    pooled = pd.concat(evaluations, ignore_index=True)
    pooled_report = compare(pooled, protocol)
    benchmark = protocol["development_gate"]["minimum_worthwhile_effect"]
    direction = all(row["fit_alpha"] > 0 for row in fold_reports)
    consistency = all(row["brier_difference"] < 0 and row["log_loss_difference"] < 0 for row in fold_reports)
    material = (
        pooled_report["brier_upper_family"] < -benchmark["minimum_brier_improvement_per_event"]
        and pooled_report["log_loss_upper_family"] < -benchmark["minimum_log_loss_improvement_per_event"]
    )
    coverage = len(pooled) == sum(row["player_games"] for row in fold_reports)
    report = {
        "lever": name, "folds": fold_reports, "pooled": pooled_report,
        "coverage": {
            "baseline_rows": int(len(rows)), "candidate_rows": int(len(rows)),
            "split_history_rows": int((rows.split_n.gt(0) & rows.pooled_n.gt(0)).sum()),
            "neutral_fallback_rows": int((~(rows.split_n.gt(0) & rows.pooled_n.gt(0))).sum()),
        },
        "gate": {
            "directional_stability": direction, "fold_consistency": consistency,
            "coverage_preserved": coverage, "minimum_worthwhile_effect": material,
            "all_pass": direction and consistency and coverage and material,
        },
    }
    return report, pooled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", default="data/analysis/platoon_split_audit_v1/protocol.json")
    parser.add_argument("--out-dir", default="data/analysis/platoon_split_audit_v1")
    args = parser.parse_args(argv)
    protocol_path = Path(args.protocol)
    protocol = load_protocol(protocol_path)
    inputs = protocol["inputs"]
    for field in ("training_artifact", "hitter_contact_report", "hitter_scored_rows", "statcast_inventory", "kbb_artifact"):
        verify_hash(Path(inputs[field]), inputs[f"{field}_sha256"], field)
    print("verifying and extracting hash-bound terminal PA evidence...")
    terminal, source = extract_terminal_pa(Path(inputs["statcast_inventory"]), inputs["statcast_tree_sha256"])
    training = pd.read_csv(inputs["training_artifact"], usecols=TRAINING_COLUMNS, low_memory=False)
    if training[["game_pk", "player_id"]].isna().any().any() or training.duplicated(["game_pk", "player_id"]).any():
        raise ValueError("training artifact has null or duplicate player-game identity")
    training["opp_sp_throws"] = training.opp_sp_throws.astype(str).str.upper()
    if not training.opp_sp_throws.isin({"L", "R"}).all():
        raise ValueError("training artifact contains invalid opposing-pitcher hand")
    print("building strict-prior 45-day hitter handedness profiles...")
    alignment = validate_terminal_outcomes(terminal, training)
    profiles = build_prior_split_profiles(
        terminal[["event_date", "batter_id", "p_throws", "contact_xba", "is_k", "is_bb"]],
        training[["game_date", "player_id", "opp_sp_throws"]],
        window_days=int(protocol["chronology"]["history_window_calendar_days"]),
    )
    scored_contact = pd.read_csv(inputs["hitter_scored_rows"], low_memory=False)
    kbb = json.loads(Path(inputs["kbb_artifact"]).read_text(encoding="utf-8"))
    arms = build_baseline_arms(training, scored_contact, profiles, kbb)
    reports = []
    pooled_frames = []
    for name in ("contact_xba", "strikeout_rate", "walk_rate"):
        report, pooled = audit_arm(name, arms[name], protocol)
        reports.append(report)
        pooled["lever"] = name
        pooled_frames.append(pooled)
    supported = [row for row in reports if row["gate"]["all_pass"]]
    supported.sort(key=lambda row: (row["pooled"]["log_loss_difference"], row["pooled"]["brier_difference"], row["lever"]))
    winner = supported[0]["lever"] if supported else None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    profiles_path = out_dir / "prior_handedness_profiles.csv.gz"
    profiles.to_csv(profiles_path, index=False)
    rows_path = out_dir / "rolling_origin_evaluation_rows.csv.gz"
    pd.concat(pooled_frames, ignore_index=True).to_csv(rows_path, index=False)
    comparisons_path = out_dir / "lever_comparisons.json"
    atomic_json(comparisons_path, reports)
    report = {
        "schema_version": "hits-platoon-split-audit-report-v1",
        "status": "complete_research_only", "betting_authorized": False,
        "may_2026_holdout_read": False,
        "protocol": {"path": str(protocol_path), "sha256": sha256_file(protocol_path)},
        "source": {**source, **alignment}, "levers": reports, "supported_levers": [row["lever"] for row in supported],
        "selected_production_candidate": winner,
        "production_candidate_supported": winner is not None,
        "production_change_installed": False,
        "decision": (
            f"{winner} earned exactly one separately versioned March-April production candidate experiment."
            if winner else
            "No handedness-split lever cleared the stable, family-adjusted, minimum-worthwhile development gate. Do not change production."
        ),
        "artifacts": {
            "audit_script": {"path": str(Path(__file__)), "sha256": sha256_file(Path(__file__))},
            "evaluation_module": {"path": "src/evaluation/handedness_split_skill.py", "sha256": sha256_file(Path("src/evaluation/handedness_split_skill.py"))},
            "mutation_script": {"path": "scripts/check_hitter_platoon_split_audit_offline.py", "sha256": sha256_file(Path("scripts/check_hitter_platoon_split_audit_offline.py"))},
            "profiles": {"path": str(profiles_path), "sha256": sha256_file(profiles_path)},
            "evaluation_rows": {"path": str(rows_path), "sha256": sha256_file(rows_path)},
            "comparisons": {"path": str(comparisons_path), "sha256": sha256_file(comparisons_path)},
        },
    }
    report["report_content_sha256"] = sha256_json(report)
    report_path = out_dir / "report.json"
    atomic_json(report_path, report)
    print("HITTER PLATOON SPLIT AUDIT COMPLETE - RESEARCH ONLY")
    for row in reports:
        p = row["pooled"]
        print(
            f"  {row['lever']}: pass={row['gate']['all_pass']} "
            f"Brier={p['brier_difference']:+.8f} [{p['brier_lower_family']:+.8f}, {p['brier_upper_family']:+.8f}] "
            f"logloss={p['log_loss_difference']:+.8f} [{p['log_loss_lower_family']:+.8f}, {p['log_loss_upper_family']:+.8f}]"
        )
    print(f"  selected candidate: {winner}")
    print("  MAY 2026 UNREAD. BETTING NOT AUTHORIZED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
