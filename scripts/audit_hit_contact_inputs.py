#!/usr/bin/env python3
"""Chronological audit of recency and shrinkage in the hitter contact input."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from math import inf, isinf
from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hit_contact_skill import (  # noqa: E402
    add_daily_anchors,
    add_probability,
    choose_window_on_selector,
    fit_prior_strength_on_selector,
    paired_date_score_interval,
    score_totals,
    validate_contact_rows,
)
from src.utils.provenance import sha256_file, sha256_json  # noqa: E402

HIT_EVENTS = {"single", "double", "triple", "home_run"}
RAW_COLUMNS = ["game_date", "game_pk", "batter", "type", "events", "at_bat_number"]
TRAINING_COLUMNS = [
    "season", "game_date", "game_pk", "player_id", "lineup_slot",
    "recent_pa_15", "roll15_xba", "roll15_bip",
    "recent_pa_30", "roll30_xba", "roll30_bip",
    "out_pa", "out_ab", "out_k", "out_hits",
]


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def load_protocol(path: Path) -> dict:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if protocol.get("schema_version") != "hits-contact-skill-audit-protocol-v1":
        raise ValueError("unknown hitter contact audit protocol schema")
    if protocol.get("status") != "locked_before_confirmation":
        raise ValueError("hitter contact audit protocol is not locked")
    if not protocol["scope"].get("may_2026_holdout_must_remain_unread"):
        raise ValueError("protocol does not protect the May 2026 holdout")
    return protocol


def extract_contact_outcomes(
    roots: list[Path],
) -> tuple[pd.DataFrame, dict]:
    terminal_frames: list[pd.DataFrame] = []
    inventory: list[dict] = []
    for root in roots:
        if not root.is_dir():
            raise FileNotFoundError(f"Statcast cache root missing: {root}")
        for path in sorted(root.glob("batter_*.csv")):
            match = re.fullmatch(r"batter_(\d+)\.csv", path.name)
            if not match:
                raise ValueError(f"unexpected Statcast cache name: {path}")
            expected_batter = int(match.group(1))
            inventory.append({
                "path": path.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
            try:
                raw = pd.read_csv(path, usecols=RAW_COLUMNS, low_memory=False)
            except EmptyDataError:
                continue
            if raw.empty:
                continue
            batter = pd.to_numeric(raw.batter, errors="raise")
            observed = set(batter.dropna().astype(int).unique())
            if observed - {expected_batter}:
                raise ValueError(
                    f"Statcast cache identity mismatch in {path}: "
                    f"expected {expected_batter}, saw {sorted(observed)[:5]}"
                )
            terminal = raw[raw.events.notna() & raw.type.astype(str).eq("X")].copy()
            if terminal.empty:
                continue
            if terminal[["game_pk", "batter", "game_date", "at_bat_number"]].isna().any().any():
                raise ValueError(f"terminal Statcast row lacks identity in {path}")
            terminal["game_pk"] = pd.to_numeric(terminal.game_pk, errors="raise").astype(int)
            terminal["player_id"] = pd.to_numeric(terminal.batter, errors="raise").astype(int)
            terminal["at_bat_number"] = pd.to_numeric(
                terminal.at_bat_number, errors="raise"
            ).astype(int)
            terminal["game_date"] = pd.to_datetime(
                terminal.game_date, errors="raise"
            ).dt.strftime("%Y-%m-%d")
            terminal["is_hit"] = terminal.events.astype(str).isin(HIT_EVENTS).astype(int)
            terminal_frames.append(terminal[
                ["game_pk", "player_id", "game_date", "at_bat_number", "events", "is_hit"]
            ])
    if not inventory:
        raise ValueError("no Statcast cache files found")
    terminal = pd.concat(terminal_frames, ignore_index=True)
    pa_key = ["game_pk", "player_id", "at_bat_number"]
    duplicate = terminal.duplicated(pa_key, keep=False)
    if duplicate.any():
        raise ValueError(
            "duplicate terminal Statcast contact events; examples:\n"
            + terminal.loc[duplicate, pa_key + ["events"]].head(20).to_string(index=False)
        )
    dates_per_game = terminal.groupby("game_pk").game_date.nunique()
    if (dates_per_game > 1).any():
        raise ValueError("a raw Statcast game_pk maps to multiple game dates")
    outcomes = terminal.groupby(["game_pk", "player_id", "game_date"], as_index=False).agg(
        contact_events=("is_hit", "size"),
        hits_on_contact=("is_hit", "sum"),
    )
    inventory_payload = {
        "schema_version": "statcast-contact-source-inventory-v1",
        "files": inventory,
        "tree_sha256": hashlib.sha256(canonical_json(inventory).encode("utf-8")).hexdigest(),
        "file_count": len(inventory),
        "bytes": sum(int(item["bytes"]) for item in inventory),
    }
    return outcomes, inventory_payload


def build_scored_frame(training: pd.DataFrame, outcomes: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    key = ["game_pk", "player_id"]
    if training[key].isna().any().any() or training.duplicated(key).any():
        raise ValueError("historical training input has a null or duplicate player-game key")
    if outcomes[key].isna().any().any() or outcomes.duplicated(key).any():
        raise ValueError("raw contact outcomes have a null or duplicate player-game key")
    joined = training.merge(outcomes, on=key, how="left", validate="one_to_one", suffixes=("", "_raw"))
    raw_date = joined.game_date_raw.dropna().astype(str)
    if not raw_date.equals(joined.loc[raw_date.index, "game_date"].astype(str)):
        raise ValueError("training and raw Statcast game dates disagree")
    joined["raw_outcome_present"] = joined.contact_events.notna()
    joined["contact_events"] = pd.to_numeric(joined.contact_events, errors="coerce").fillna(0).astype(int)
    joined["hits_on_contact"] = pd.to_numeric(joined.hits_on_contact, errors="coerce").fillna(0).astype(int)
    official_hits = pd.to_numeric(joined.out_hits, errors="raise").astype(int)
    hit_mismatch = joined.hits_on_contact.ne(official_hits)
    if hit_mismatch.any():
        raise ValueError(
            f"raw Statcast hits disagree with official MLB hits on {int(hit_mismatch.sum())} rows; examples:\n"
            + joined.loc[hit_mismatch, key + ["game_date", "hits_on_contact", "out_hits"]]
            .head(20).to_string(index=False)
        )
    official_contact_floor = (
        pd.to_numeric(joined.out_ab, errors="raise") - pd.to_numeric(joined.out_k, errors="raise")
    )
    missing_contact = official_contact_floor.gt(0) & ~joined.raw_outcome_present
    if missing_contact.any():
        raise ValueError(
            f"raw Statcast contact is absent for {int(missing_contact.sum())} rows with official AB-K > 0"
        )
    coverage = {
        "training_player_games": int(len(joined)),
        "raw_contact_player_games": int(joined.raw_outcome_present.sum()),
        "raw_contact_rate": float(joined.raw_outcome_present.mean()),
        "official_hit_alignment_mismatches": 0,
    }
    feature_columns = [
        "roll15_xba", "roll15_bip", "recent_pa_15",
        "roll30_xba", "roll30_bip", "recent_pa_30",
    ]
    eligible = joined[
        joined.contact_events.gt(0)
        & joined.lineup_slot.between(1, 9)
        & joined[feature_columns].notna().all(axis=1)
    ].copy()
    eligible = eligible.rename(columns={"game_date": "game_date_training"})
    eligible["game_date"] = eligible.game_date_training.astype(str)
    eligible = eligible.drop(columns=["game_date_raw"])
    validate_contact_rows(eligible)
    coverage.update({
        "scored_player_games": int(len(eligible)),
        "scored_contact_events": int(eligible.contact_events.sum()),
        "scored_official_dates": int(eligible.game_date.nunique()),
    })
    return add_daily_anchors(eligible), coverage


def add_all_arms(rows: pd.DataFrame, chosen_window: int, fitted_strength: float) -> pd.DataFrame:
    out = rows.copy()
    definitions = [
        ("w30_pa_k120", 30, "recent_pa_30", 120.0),
        ("w30_bip_k120", 30, "roll30_bip", 120.0),
        ("w15_bip_k120", 15, "roll15_bip", 120.0),
        (
            f"w{chosen_window}_bip_kfit",
            chosen_window,
            f"roll{chosen_window}_bip",
            fitted_strength,
        ),
    ]
    for name, window, evidence, strength in definitions:
        out = add_probability(
            out,
            output=name,
            window=window,
            evidence_column=evidence,
            prior_strength=strength,
        )
    return out


def comparison(
    rows: pd.DataFrame,
    *,
    name: str,
    candidate: str,
    baseline: str,
    dates: list[str],
    bootstrap: int,
    seed: int,
) -> dict:
    candidate_scores = score_totals(rows, candidate)
    baseline_scores = score_totals(rows, baseline)
    result = {
        "comparison": name,
        "candidate": candidate,
        "baseline": baseline,
        "contact_events": int(candidate_scores["contact_events"]),
    }
    supports: list[bool] = []
    for metric, field in (("brier", "contact_weighted_brier"), ("log_loss", "contact_weighted_log_loss")):
        interval = paired_date_score_interval(
            rows,
            candidate_column=candidate,
            baseline_column=baseline,
            metric=metric,
            declared_dates=dates,
            bootstrap=bootstrap,
            seed=seed,
        )
        result[f"{metric}_difference"] = float(candidate_scores[field] - baseline_scores[field])
        result[f"{metric}_lower_95"] = interval.lower
        result[f"{metric}_upper_95"] = interval.upper
        result[f"{metric}_valid_draws"] = interval.valid_draws
        supports.append(interval.valid_draws == bootstrap and interval.upper < 0.0)
    result["support"] = all(supports)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        default="data/analysis/hitter_skill_contact_audit_v1/protocol.json",
    )
    parser.add_argument(
        "--out-dir",
        default="data/analysis/hitter_skill_contact_audit_v1",
    )
    args = parser.parse_args(argv)
    protocol_path = Path(args.protocol)
    out_dir = Path(args.out_dir)
    protocol = load_protocol(protocol_path)
    training_path = Path(protocol["inputs"]["training_artifact"])
    if sha256_file(training_path) != protocol["inputs"]["training_artifact_sha256"]:
        raise ValueError("historical hitter training artifact hash mismatch")
    roots = [Path(value) for value in protocol["inputs"]["statcast_cache_roots"]]

    print("extracting and hashing raw Statcast contact outcomes...")
    outcomes, inventory = extract_contact_outcomes(roots)
    training = pd.read_csv(training_path, usecols=TRAINING_COLUMNS, low_memory=False)
    selector_seasons = set(protocol["chronology"]["selector_seasons"])
    confirmation_seasons = set(protocol["chronology"]["confirmation_seasons"])
    if selector_seasons & confirmation_seasons or max(selector_seasons) >= min(confirmation_seasons):
        raise ValueError("selector/confirmation seasons are not disjoint and ordered")
    if set(training.season.astype(int).unique()) != selector_seasons | confirmation_seasons:
        raise ValueError("training artifact season universe differs from the locked protocol")
    scored, coverage = build_scored_frame(training, outcomes)
    selector = scored[scored.season.astype(int).isin(selector_seasons)].copy()
    confirmation = scored[scored.season.astype(int).isin(confirmation_seasons)].copy()
    if selector.empty or confirmation.empty or max(selector.game_date) >= min(confirmation.game_date):
        raise ValueError("selector/confirmation scoring chronology is invalid")

    chosen_window = choose_window_on_selector(selector, prior_strength=120.0)
    fitted_strength = fit_prior_strength_on_selector(selector, chosen_window)
    scored = add_all_arms(scored, chosen_window, fitted_strength)
    selector = scored[scored.season.astype(int).isin(selector_seasons)].copy()
    confirmation = scored[scored.season.astype(int).isin(confirmation_seasons)].copy()
    confirmation_dates = sorted(confirmation.game_date.unique())
    bootstrap = int(protocol["confirmation_gate"]["bootstrap_draws"])
    seed = int(protocol["confirmation_gate"]["seed"])
    other_window = 30 if chosen_window == 15 else 15
    comparisons = [
        comparison(
            confirmation,
            name="bip_evidence_count_vs_pa_count",
            candidate="w30_bip_k120",
            baseline="w30_pa_k120",
            dates=confirmation_dates,
            bootstrap=bootstrap,
            seed=seed,
        ),
        comparison(
            confirmation,
            name="selector_chosen_recency_window",
            candidate=f"w{chosen_window}_bip_k120",
            baseline=f"w{other_window}_bip_k120",
            dates=confirmation_dates,
            bootstrap=bootstrap,
            seed=seed,
        ),
        comparison(
            confirmation,
            name="selector_fitted_shrinkage_strength",
            candidate=f"w{chosen_window}_bip_kfit",
            baseline=f"w{chosen_window}_bip_k120",
            dates=confirmation_dates,
            bootstrap=bootstrap,
            seed=seed,
        ),
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    outcomes_path = out_dir / "contact_outcomes.csv.gz"
    outcomes.to_csv(outcomes_path, index=False)
    inventory_path = out_dir / "statcast_source_inventory.json"
    atomic_json(inventory_path, inventory)
    comparisons_path = out_dir / "confirmation_comparisons.csv"
    pd.DataFrame(comparisons).to_csv(comparisons_path, index=False)
    scored_path = out_dir / "scored_contact_rows.csv.gz"
    scored.to_csv(scored_path, index=False)
    fitted_strength_json: float | str = "infinity" if isinf(fitted_strength) else fitted_strength
    support = [row["comparison"] for row in comparisons if row["support"]]
    report = {
        "schema_version": "hits-contact-skill-audit-report-v1",
        "status": "complete_research_only",
        "betting_authorized": False,
        "may_2026_holdout_read": False,
        "protocol": {"path": str(protocol_path), "sha256": sha256_file(protocol_path)},
        "chronology": {
            "selector_seasons": sorted(selector_seasons),
            "selector_dates": [selector.game_date.min(), selector.game_date.max()],
            "confirmation_seasons": sorted(confirmation_seasons),
            "confirmation_dates": [confirmation.game_date.min(), confirmation.game_date.max()],
            "confirmation_outcomes_used_for_selection": False,
        },
        "coverage": coverage,
        "selector_fit": {
            "chosen_recency_window_games": chosen_window,
            "fitted_prior_strength_bip": fitted_strength_json,
            "candidate_strength_source": "distinct positive selector BIP counts plus 0 and infinity",
        },
        "confirmation_comparisons": comparisons,
        "supported_diagnostic_levers": support,
        "production_candidate_supported": bool(support),
        "production_change_installed": False,
        "decision": (
            "At least one isolated contact-input lever earned confirmation support. "
            "Build a separate point-in-time production adapter candidate and gate it on open 2026 evidence."
            if support else
            "No isolated contact-input lever cleared both confirmation score gates. Do not change production contact logic."
        ),
        "scope_note": (
            "The 30-game PA-count arm is an isolation proxy for production's 45-calendar-day profile, "
            "not an exact historical reconstruction. This report can justify a candidate experiment, never betting."
        ),
        "artifacts": {
            "training": {"path": str(training_path), "sha256": sha256_file(training_path)},
            "statcast_inventory": {"path": str(inventory_path), "sha256": sha256_file(inventory_path), "tree_sha256": inventory["tree_sha256"]},
            "contact_outcomes": {"path": str(outcomes_path), "sha256": sha256_file(outcomes_path)},
            "scored_rows": {"path": str(scored_path), "sha256": sha256_file(scored_path)},
            "comparisons": {"path": str(comparisons_path), "sha256": sha256_file(comparisons_path)},
        },
    }
    report["report_content_sha256"] = sha256_json(report)
    report_path = out_dir / "report.json"
    atomic_json(report_path, report)

    print("HITTER CONTACT-SKILL AUDIT COMPLETE — RESEARCH ONLY")
    print(f"  selector window: {chosen_window} games")
    print(f"  fitted BIP prior strength: {fitted_strength_json}")
    for row in comparisons:
        print(
            f"  {row['comparison']}: support={row['support']} "
            f"Brier {row['brier_difference']:+.8f} "
            f"[{row['brier_lower_95']:+.8f}, {row['brier_upper_95']:+.8f}] "
            f"logloss {row['log_loss_difference']:+.8f} "
            f"[{row['log_loss_lower_95']:+.8f}, {row['log_loss_upper_95']:+.8f}]"
        )
    print(f"  wrote {report_path}")
    print("  MAY 2026 UNREAD. BETTING NOT AUTHORIZED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
