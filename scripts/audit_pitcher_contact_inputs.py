#!/usr/bin/env python3
"""Rolling-origin audit of point-in-time opposing-pitcher contact quality."""
from __future__ import annotations

import argparse
import json
import re
import sys
from math import isinf
from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hit_contact_skill import score_totals  # noqa: E402
from src.evaluation.pitcher_contact_skill import (  # noqa: E402
    add_pitcher_probability,
    build_prior_window_profiles,
    fit_rolling_fold,
    paired_interval,
    validate_pitcher_contact_rows,
)
from src.utils.provenance import sha256_file, sha256_json  # noqa: E402

RAW_COLUMNS = [
    "game_date", "game_pk", "batter", "pitcher", "type", "events",
    "at_bat_number", "estimated_ba_using_speedangle",
]
TRAINING_COLUMNS = ["season", "game_date", "game_pk", "player_id", "opp_sp_id"]


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def load_protocol(path: Path) -> dict:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if protocol.get("schema_version") != "pitcher-contact-skill-audit-protocol-v1":
        raise ValueError("unknown pitcher-contact protocol schema")
    if protocol.get("status") != "locked_before_development_audit_results":
        raise ValueError("pitcher-contact protocol is not locked")
    if not protocol["scope"].get("may_2026_holdout_must_remain_unread"):
        raise ValueError("pitcher-contact protocol does not protect May 2026")
    folds = protocol["chronology"]["folds"]
    if folds != [
        {"name": "train_2023_confirm_2024", "fit_seasons": [2023], "evaluation_season": 2024},
        {"name": "train_2023_2024_confirm_2025", "fit_seasons": [2023, 2024], "evaluation_season": 2025},
    ]:
        raise ValueError("pitcher-contact rolling-origin folds differ from the locked contract")
    return protocol


def validate_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} missing: {path}")
    observed = sha256_file(path)
    if observed.lower() != str(expected).lower():
        raise ValueError(f"{label} hash mismatch: expected {expected}, observed {observed}")


def extract_pitcher_contacts(inventory_path: Path, expected_tree_sha: str) -> tuple[pd.DataFrame, dict]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("schema_version") != "statcast-contact-source-inventory-v1":
        raise ValueError("unknown Statcast contact inventory schema")
    if inventory.get("tree_sha256") != expected_tree_sha:
        raise ValueError("Statcast inventory tree hash differs from locked protocol")
    terminal_frames: list[pd.DataFrame] = []
    bytes_checked = 0
    for item in inventory["files"]:
        path = Path(item["path"])
        validate_hash(path, item["sha256"], "raw Statcast cache")
        if path.stat().st_size != int(item["bytes"]):
            raise ValueError(f"raw Statcast cache size mismatch: {path}")
        bytes_checked += path.stat().st_size
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
        observed_batters = set(pd.to_numeric(raw.batter, errors="raise").dropna().astype(int).unique())
        if observed_batters - {expected_batter}:
            raise ValueError(
                f"Statcast cache identity mismatch in {path}: expected {expected_batter}, "
                f"saw {sorted(observed_batters)[:5]}"
            )
        terminal = raw[
            raw.events.notna()
            & raw.type.astype(str).eq("X")
            & raw.estimated_ba_using_speedangle.notna()
        ].copy()
        if terminal.empty:
            continue
        identity = ["game_date", "game_pk", "batter", "pitcher", "at_bat_number"]
        if terminal[identity].isna().any().any():
            raise ValueError(f"pitcher-contact row lacks identity in {path}")
        terminal["contact_date"] = pd.to_datetime(terminal.game_date, errors="raise").dt.strftime("%Y-%m-%d")
        terminal["game_pk"] = pd.to_numeric(terminal.game_pk, errors="raise").astype(int)
        terminal["batter_id"] = pd.to_numeric(terminal.batter, errors="raise").astype(int)
        terminal["pitcher_id"] = pd.to_numeric(terminal.pitcher, errors="raise").astype(int)
        terminal["at_bat_number"] = pd.to_numeric(terminal.at_bat_number, errors="raise").astype(int)
        terminal["xba"] = pd.to_numeric(terminal.estimated_ba_using_speedangle, errors="raise")
        terminal_frames.append(terminal[
            ["contact_date", "game_pk", "batter_id", "pitcher_id", "at_bat_number", "xba"]
        ])
    if bytes_checked != int(inventory["bytes"]):
        raise ValueError("raw Statcast bytes differ from locked source inventory")
    if not terminal_frames:
        raise ValueError("no pitcher-contact evidence found")
    contacts = pd.concat(terminal_frames, ignore_index=True)
    key = ["game_pk", "batter_id", "at_bat_number"]
    duplicate = contacts.duplicated(key, keep=False)
    if duplicate.any():
        raise ValueError(
            "duplicate terminal pitcher-contact events; examples:\n"
            + contacts.loc[duplicate, key + ["pitcher_id"]].head(20).to_string(index=False)
        )
    if not contacts.xba.between(0.0, 1.0).all():
        raise ValueError("raw expected BA lies outside [0,1]")
    return contacts, {
        "source_file_count": int(inventory["file_count"]),
        "source_bytes": int(inventory["bytes"]),
        "source_tree_sha256": inventory["tree_sha256"],
        "terminal_xba_contacts": int(len(contacts)),
        "pitchers": int(contacts.pitcher_id.nunique()),
        "dates": int(contacts.contact_date.nunique()),
    }


def build_audit_rows(protocol: dict, contacts: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    inputs = protocol["inputs"]
    training = pd.read_csv(inputs["training_artifact"], usecols=TRAINING_COLUMNS, low_memory=False)
    scored = pd.read_csv(inputs["hitter_scored_rows"], low_memory=False)
    key = ["game_pk", "player_id"]
    if training[key].isna().any().any() or training.duplicated(key).any():
        raise ValueError("training artifact has a null or duplicate player-game key")
    if scored[key].isna().any().any() or scored.duplicated(key).any():
        raise ValueError("hitter scored artifact has a null or duplicate player-game key")
    identity = training[TRAINING_COLUMNS].rename(columns={
        "season": "source_season",
        "game_date": "source_game_date",
    })
    joined = scored.merge(identity, on=key, how="left", validate="one_to_one")
    if joined.opp_sp_id.isna().any():
        raise ValueError("a scored hitter row has no opponent pitcher identity")
    if not joined.game_date.astype(str).equals(joined.source_game_date.astype(str)):
        raise ValueError("scored and training game dates disagree")
    if not joined.season.astype(int).equals(joined.source_season.astype(int)):
        raise ValueError("scored and training seasons disagree")
    targets = joined[["game_date", "opp_sp_id"]].drop_duplicates()
    profiles = build_prior_window_profiles(
        contacts[["contact_date", "pitcher_id", "xba"]],
        targets,
        window_days=int(protocol["chronology"]["profile_window_calendar_days"]),
    )
    rows = joined.merge(profiles, on=["game_date", "opp_sp_id"], how="left", validate="many_to_one")
    if rows[["pitcher_xba", "pitcher_bip", "league_xba", "league_bip"]].isna().any().any():
        raise ValueError("pitcher-contact profile join lost a target")
    rows = rows.rename(columns={"w30_pa_k120": "baseline_p"})
    validate_pitcher_contact_rows(rows)
    coverage = {
        "baseline_player_games": int(len(rows)),
        "candidate_player_games": int(len(rows)),
        "profile_targets": int(len(profiles)),
        "pitcher_history_player_games": int(rows.pitcher_history_present.sum()),
        "neutral_fallback_player_games": int((~rows.pitcher_history_present).sum()),
        "neutral_fallback_rate": float((~rows.pitcher_history_present).mean()),
        "official_dates": int(rows.game_date.nunique()),
        "contact_events": int(rows.contact_events.sum()),
    }
    return rows, coverage


def score_comparison(rows: pd.DataFrame, protocol: dict) -> dict:
    baseline = score_totals(rows, "baseline_p")
    candidate = score_totals(rows, "candidate_p")
    result: dict[str, float | int | bool] = {
        "player_games": int(len(rows)),
        "contact_events": int(candidate["contact_events"]),
        "brier_difference": float(candidate["contact_weighted_brier"] - baseline["contact_weighted_brier"]),
        "log_loss_difference": float(candidate["contact_weighted_log_loss"] - baseline["contact_weighted_log_loss"]),
    }
    for metric in ("brier", "log_loss"):
        interval = paired_interval(
            rows,
            metric=metric,
            declared_dates=sorted(rows.game_date.unique()),
            bootstrap=int(protocol["development_gate"]["bootstrap_draws"]),
            seed=int(protocol["development_gate"]["seed"]),
        )
        result[f"{metric}_lower_95"] = interval.lower
        result[f"{metric}_upper_95"] = interval.upper
        result[f"{metric}_valid_draws"] = interval.valid_draws
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", default="data/analysis/pitcher_contact_audit_v1/protocol.json")
    parser.add_argument("--out-dir", default="data/analysis/pitcher_contact_audit_v1")
    args = parser.parse_args(argv)
    protocol_path = Path(args.protocol)
    out_dir = Path(args.out_dir)
    protocol = load_protocol(protocol_path)
    inputs = protocol["inputs"]
    for field in ("training_artifact", "hitter_contact_report", "hitter_scored_rows", "statcast_inventory"):
        validate_hash(Path(inputs[field]), inputs[f"{field}_sha256"], field)

    print("verifying and extracting hash-bound pitcher contact evidence...")
    contacts, source_summary = extract_pitcher_contacts(
        Path(inputs["statcast_inventory"]), inputs["statcast_tree_sha256"]
    )
    print("building strict-prior 45-day pitcher profiles...")
    rows, coverage = build_audit_rows(protocol, contacts)
    fold_reports: list[dict] = []
    evaluation_frames: list[pd.DataFrame] = []
    for fold in protocol["chronology"]["folds"]:
        fitted, evaluation = fit_rolling_fold(
            rows,
            fit_seasons=fold["fit_seasons"],
            evaluation_season=int(fold["evaluation_season"]),
        )
        evaluation = add_pitcher_probability(
            evaluation, prior_strength=fitted.prior_strength, alpha=fitted.alpha
        )
        comparison = score_comparison(evaluation, protocol)
        comparison.update({
            "fold": fold["name"],
            "fit_seasons": fold["fit_seasons"],
            "evaluation_season": fold["evaluation_season"],
            "fit_prior_strength": "infinity" if isinf(fitted.prior_strength) else fitted.prior_strength,
            "fit_alpha": fitted.alpha,
            "fit_log_loss": fitted.log_loss,
            "fit_brier": fitted.brier,
        })
        fold_reports.append(comparison)
        evaluation["fold"] = fold["name"]
        evaluation_frames.append(evaluation)
    pooled = pd.concat(evaluation_frames, ignore_index=True)
    pooled_report = score_comparison(pooled, protocol)
    benchmark = protocol["development_gate"]["pooled_materiality_benchmark"]
    direction_ok = all(float(row["fit_alpha"]) > 0.0 for row in fold_reports)
    fold_consistency = all(
        float(row["brier_difference"]) < 0.0 and float(row["log_loss_difference"]) < 0.0
        for row in fold_reports
    )
    materiality = (
        float(pooled_report["brier_upper_95"]) < -float(benchmark["minimum_brier_improvement"])
        and float(pooled_report["log_loss_upper_95"]) < -float(benchmark["minimum_log_loss_improvement"])
    )
    coverage_ok = coverage["baseline_player_games"] == coverage["candidate_player_games"]
    supported = direction_ok and fold_consistency and materiality and coverage_ok

    out_dir.mkdir(parents=True, exist_ok=True)
    profiles_path = out_dir / "pitcher_profiles_by_target.csv.gz"
    profile_columns = [
        "game_date", "opp_sp_id", "pitcher_xba", "pitcher_bip",
        "league_xba", "league_bip", "pitcher_history_present",
    ]
    rows[profile_columns].drop_duplicates().to_csv(profiles_path, index=False)
    evaluation_path = out_dir / "rolling_origin_evaluation_rows.csv.gz"
    pooled.to_csv(evaluation_path, index=False)
    fold_path = out_dir / "fold_comparisons.csv"
    pd.DataFrame(fold_reports).to_csv(fold_path, index=False)
    report = {
        "schema_version": "pitcher-contact-skill-audit-report-v1",
        "status": "complete_research_only",
        "betting_authorized": False,
        "may_2026_holdout_read": False,
        "protocol": {"path": str(protocol_path), "sha256": sha256_file(protocol_path)},
        "source": source_summary,
        "coverage": coverage,
        "rolling_origin_folds": fold_reports,
        "pooled_evaluation": pooled_report,
        "gate": {
            "directional_stability": direction_ok,
            "fold_consistency": fold_consistency,
            "coverage_preserved": coverage_ok,
            "pooled_minimum_worthwhile_effect": materiality,
            "all_pass": supported,
        },
        "production_candidate_supported": supported,
        "production_change_installed": False,
        "decision": (
            "Opposing-pitcher contact quality earned exactly one separately versioned production candidate experiment on open March-April 2026 evidence."
            if supported else
            "Opposing-pitcher contact quality did not clear the predeclared stable-material development gate. Do not add it to production."
        ),
        "artifacts": {
            "audit_script": {"path": str(Path(__file__)), "sha256": sha256_file(Path(__file__))},
            "evaluation_module": {
                "path": "src/evaluation/pitcher_contact_skill.py",
                "sha256": sha256_file(Path("src/evaluation/pitcher_contact_skill.py")),
            },
            "mutation_script": {
                "path": "scripts/check_pitcher_contact_input_audit_offline.py",
                "sha256": sha256_file(Path("scripts/check_pitcher_contact_input_audit_offline.py")),
            },
            "profiles": {"path": str(profiles_path), "sha256": sha256_file(profiles_path)},
            "evaluation_rows": {"path": str(evaluation_path), "sha256": sha256_file(evaluation_path)},
            "fold_comparisons": {"path": str(fold_path), "sha256": sha256_file(fold_path)},
        },
    }
    report["report_content_sha256"] = sha256_json(report)
    report_path = out_dir / "report.json"
    atomic_json(report_path, report)
    print("PITCHER CONTACT AUDIT COMPLETE - RESEARCH ONLY")
    for row in fold_reports:
        print(
            f"  {row['fold']}: alpha={row['fit_alpha']:+.6f} "
            f"Brier={row['brier_difference']:+.8f} logloss={row['log_loss_difference']:+.8f}"
        )
    print(
        f"  pooled: Brier={pooled_report['brier_difference']:+.8f} "
        f"[{pooled_report['brier_lower_95']:+.8f}, {pooled_report['brier_upper_95']:+.8f}] "
        f"logloss={pooled_report['log_loss_difference']:+.8f} "
        f"[{pooled_report['log_loss_lower_95']:+.8f}, {pooled_report['log_loss_upper_95']:+.8f}]"
    )
    print(f"  gate: {report['gate']}")
    print(f"  wrote {report_path}")
    print("  MAY 2026 UNREAD. BETTING NOT AUTHORIZED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
