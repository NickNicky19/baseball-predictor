#!/usr/bin/env python
"""Replay and decompose the exact frozen HR-over baseline on open dates only."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_over_baseline_diagnostic import (  # noqa: E402
    MODEL_KEY,
    at_least_one_probability,
    mean_pa_probability,
    paired_date_block_interval,
    point_metrics,
    realized_pa_probability,
    require_exact_keys,
    score_probability,
    validate_open_dates,
    validate_pa_distribution,
    validate_report,
)
from src.features.feature_store import FeatureStore  # noqa: E402
from src.prediction.prop_engine import PropEngine  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_path(record: dict[str, Any], label: str) -> Path:
    path = (ROOT / str(record["path"])).resolve()
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    actual = sha256_file(path)
    if actual != record["sha256"]:
        raise ValueError(f"{label} hash mismatch: expected {record['sha256']}, got {actual}")
    return path


def _probability_from_input(engine: PropEngine, sim_input: Any) -> float:
    """Use the Monte-Carlo path's exact PA simulator and exact normalized inputs."""
    probabilities = engine.monte_carlo.game_simulator.pa_simulator.expected_outcome_probabilities(
        pitcher_k_pct=sim_input.pitcher_k_pct + sim_input.umpire_k_bias,
        pitcher_bb_pct=sim_input.pitcher_bb_pct,
        pitcher_hr_per_9=sim_input.pitcher_hr_per_9,
        park_hr_factor=sim_input.park_hr_factor * sim_input.weather_hr_factor,
        park_hits_factor=sim_input.park_hits_factor,
        handedness_advantage=sim_input.handedness_advantage,
        recent_form_mult=sim_input.recent_form_mult,
        bvp_ops_factor=sim_input.bvp_ops_factor,
        bvp_hr_factor=sim_input.bvp_hr_factor,
        statcast=sim_input.statcast,
        rich_features=sim_input.rich_features,
    )
    q = float(probabilities["home_run"])
    if not np.isfinite(q) or not 0.0 <= q <= 1.0:
        raise ValueError("active simulator produced an invalid per-PA HR probability")
    return q


def _variant_inputs(engine: PropEngine, bundle: Any) -> dict[str, Any]:
    rich = copy.deepcopy(bundle.metadata.get("rich_features", {}))
    active = engine._bundle_to_sim_input(bundle, rich_features=rich)

    distribution_free_statcast = copy.deepcopy(active.statcast)
    distribution_free_statcast.distribution = None

    no_rolling = copy.deepcopy(active.rich_features or {})
    no_rolling.pop("roll15_xwoba", None)
    no_rolling.pop("recent_pa_15", None)

    # Keep umpire context while neutralizing the opposing pitcher's K rate.
    neutral_pitcher_k = engine.league.k_pct - active.umpire_k_bias
    return {
        "active_exact": active,
        "neutral_park_weather": replace(
            active, park_hr_factor=1.0, weather_hr_factor=1.0
        ),
        "neutral_handedness": replace(active, handedness_advantage=0.0),
        "neutral_recent_form": replace(active, recent_form_mult=1.0),
        "neutral_bvp_hr": replace(active, bvp_hr_factor=1.0),
        "neutral_pitcher_hr9": replace(
            active, pitcher_hr_per_9=engine.league.hr_per_9
        ),
        "neutral_pitcher_kbb": replace(
            active,
            pitcher_k_pct=neutral_pitcher_k,
            pitcher_bb_pct=engine.league.bb_pct,
        ),
        "remove_statcast_distribution_extra": replace(
            active, statcast=distribution_free_statcast
        ),
        "remove_rolling_quality_extra": replace(active, rich_features=no_rolling),
    }


def _feature_rows(
    manifest: dict[str, Any], market: pd.DataFrame, pa_weights: dict[int, dict[int, float]]
) -> pd.DataFrame:
    config = copy.deepcopy(manifest["effective_config"])
    engine = PropEngine(config=config)
    feature_root = Path(next(iter(manifest["feature_snapshots"].values()))["bundle_path"]).parents[1]
    store = FeatureStore(root=feature_root)
    wanted = {
        (int(row.mlb_game_pk), int(row.player_id))
        for row in market.itertuples(index=False)
    }
    found: dict[tuple[int, int], Any] = {}

    for game_date in validate_open_dates(manifest["dates"]):
        evidence = manifest["feature_snapshots"].get(game_date)
        if not isinstance(evidence, dict):
            raise ValueError(f"frozen manifest lacks feature evidence for {game_date}")
        for key in ("bundle", "manifest"):
            path = Path(evidence[f"{key}_path"])
            actual = sha256_file(path)
            if actual != evidence[f"{key}_sha256"]:
                raise ValueError(f"{game_date} feature {key} hash mismatch")
        bundles = store.load(game_date, prefer="json")
        if len(bundles) != int(evidence["bundle_count"]):
            raise ValueError(f"{game_date} feature count differs from frozen manifest")
        for bundle in bundles:
            identity = (int(bundle.hitter.game.game_pk), int(bundle.hitter.player.mlb_id))
            if identity not in wanted:
                continue
            if identity in found:
                raise ValueError(f"duplicate persisted feature identity {identity}")
            if str(bundle.hitter.game.game_date) != game_date:
                raise ValueError(f"feature bundle date mismatch for {identity}")
            found[identity] = bundle

    missing = wanted - set(found)
    extra = set(found) - wanted
    if missing or extra:
        raise ValueError(
            f"persisted features differ from market keys: missing={len(missing)} extra={len(extra)}"
        )

    rows: list[dict[str, Any]] = []
    for market_row in market.itertuples(index=False):
        identity = (int(market_row.mlb_game_pk), int(market_row.player_id))
        bundle = found[identity]
        slot = int(bundle.hitter.lineup_slot)
        if slot not in pa_weights:
            raise ValueError(f"market feature has invalid lineup slot {slot}")
        inputs = _variant_inputs(engine, bundle)
        q_values = {name: _probability_from_input(engine, value) for name, value in inputs.items()}
        row: dict[str, Any] = {
            "mlb_game_pk": identity[0],
            "player_id": identity[1],
            "category": "home_runs",
            "line": 0.5,
            "lineup_slot": slot,
            "predicted_mean_pa": float(
                sum(pa * weight for pa, weight in pa_weights[slot].items())
            ),
        }
        for name, q in q_values.items():
            row[f"{name}_per_pa"] = q
            row[name] = at_least_one_probability(q, pa_weights[slot])
        row["mean_pa_only"] = mean_pa_probability(q_values["active_exact"], pa_weights[slot])
        row["oracle_realized_pa"] = realized_pa_probability(
            q_values["active_exact"], market_row.official_pa
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(MODEL_KEY).reset_index(drop=True)


def _period_summary(rows: pd.DataFrame, variants: list[str], dates: list[str]) -> dict[str, Any]:
    period = rows[rows.official_game_date.isin(dates)].copy()
    baseline = score_probability(period, "frozen_monte_carlo")
    result: dict[str, Any] = {}
    for name in variants:
        scored = baseline if name == "frozen_monte_carlo" else score_probability(period, name)
        entry: dict[str, Any] = {"point": point_metrics(scored)}
        if name != "frozen_monte_carlo":
            entry["paired_vs_frozen_monte_carlo"] = paired_date_block_interval(
                baseline, scored, dates
            )
        result[name] = entry
    return result


def _per_pa_summary(rows: pd.DataFrame) -> dict[str, Any]:
    gradeable = rows[rows.official_gradeable.astype(bool)].copy()
    selector = gradeable[gradeable.role.eq("diagnostic")]
    edges = np.unique(np.quantile(selector.active_exact_per_pa, np.linspace(0, 1, 6)))
    if len(edges) < 3:
        raise ValueError("active per-PA HR probabilities are degenerate")
    edges[0] = -np.inf
    edges[-1] = np.inf
    gradeable["probability_band"] = pd.cut(
        gradeable.active_exact_per_pa,
        bins=edges,
        labels=[f"Q{i}" for i in range(1, len(edges))],
        include_lowest=True,
    ).astype(str)

    def summarize(group: pd.DataFrame) -> dict[str, Any]:
        total_pa = float(group.official_pa.sum())
        return {
            "player_games": int(len(group)),
            "official_pa": int(total_pa),
            "official_hr": int(group.actual_value.sum()),
            "predicted_hr_per_pa": float(
                (group.active_exact_per_pa * group.official_pa).sum() / total_pa
            ),
            "official_hr_per_pa": float(group.actual_value.sum() / total_pa),
            "predicted_mean_pa": float(group.predicted_mean_pa.mean()),
            "official_mean_pa": float(group.official_pa.mean()),
            "pa_bias": float((group.predicted_mean_pa - group.official_pa).mean()),
            "pa_mae": float((group.predicted_mean_pa - group.official_pa).abs().mean()),
        }

    result: dict[str, Any] = {}
    for role, role_rows in gradeable.groupby("role", sort=True):
        result[str(role)] = {
            "overall": summarize(role_rows),
            "by_lineup_slot": {
                str(int(slot)): summarize(group)
                for slot, group in role_rows.groupby("lineup_slot", sort=True)
            },
            "by_probability_band": {
                str(band): summarize(group)
                for band, group in role_rows.groupby("probability_band", sort=True)
            },
        }
    result["diagnostic_fitted_probability_band_edges"] = [
        None if not np.isfinite(value) else float(value) for value in edges
    ]
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        default="data/analysis/hr_over_contract_v1/baseline_diagnostic_v1/protocol.json",
    )
    parser.add_argument(
        "--out-dir",
        default="data/analysis/hr_over_contract_v1/baseline_diagnostic_v1",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    protocol_path = (ROOT / args.protocol).resolve()
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol.get("predeclared_before_diagnostic_outputs") is not True:
        raise ValueError("HR baseline diagnostic protocol was not predeclared")
    if protocol.get("betting_authorized") is not False or protocol.get("may_opened") is not False:
        raise ValueError("HR baseline diagnostic protocol is not fail-closed")

    inputs = protocol["inputs"]
    manifest_path = verified_path(inputs["frozen_reconstruction_manifest"], "frozen manifest")
    verified_path(inputs["official_settlement_report"], "settlement report")
    bridge_path = verified_path(inputs["official_settlement_bridge"], "settlement bridge")
    regraded_path = verified_path(inputs["official_regraded_scored_rows"], "regraded rows")
    pa_path = verified_path(inputs["fitted_pa_distribution"], "PA distribution")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dates = validate_open_dates(manifest["dates"])
    diagnostic_dates = [value for value in dates if value < "2026-05-01"]
    confirmation_dates = [value for value in dates if value >= "2026-06-01"]
    if len(diagnostic_dates) != 37 or len(confirmation_dates) != 19:
        raise ValueError("HR diagnostic chronology is not the locked 37+19 open-date split")

    for relative, expected in manifest["source_sha256"].items():
        actual = sha256_file(ROOT / relative)
        if actual != expected:
            raise ValueError(f"current source differs from frozen reconstruction: {relative}")
    source_snapshot = Path(manifest["source_snapshot"]["path"])
    if sha256_file(source_snapshot) != manifest["source_snapshot"]["sha256"]:
        raise ValueError("frozen source snapshot hash mismatch")

    pa_payload = json.loads(pa_path.read_text(encoding="utf-8"))
    pa_weights = validate_pa_distribution(pa_payload)
    regraded = pd.read_csv(regraded_path)
    market = regraded[regraded.arm.eq("frozen")].copy()
    if len(market) != 8_854:
        raise ValueError("frozen official market universe is not the certified 8,854 rows")
    bridge = pd.read_csv(bridge_path)
    require_exact_keys(bridge, market, "official settlement bridge")
    if market.duplicated(MODEL_KEY).any():
        raise ValueError("frozen market keys are duplicated")
    official_pa = bridge[[*MODEL_KEY, "official_pa"]].copy()
    if official_pa.official_pa.isna().any():
        raise ValueError("official settlement bridge has unresolved PA")
    market = market.merge(official_pa, on=MODEL_KEY, how="inner", validate="one_to_one")

    features = _feature_rows(manifest, market, pa_weights)
    require_exact_keys(features, market, "frozen persisted features")
    rows = market.merge(features, on=MODEL_KEY, how="inner", validate="one_to_one")
    rows["frozen_monte_carlo"] = pd.to_numeric(rows.model_probability, errors="raise")
    if set(rows.official_game_date.astype(str)) != set(dates):
        raise ValueError("diagnostic rows differ from the exact open chronology")
    if rows.official_gradeable.isna().any():
        raise ValueError("official gradeability is unresolved")

    variants = [
        "frozen_monte_carlo", "active_exact", "mean_pa_only", "oracle_realized_pa",
        "neutral_park_weather", "neutral_handedness", "neutral_recent_form",
        "neutral_bvp_hr", "neutral_pitcher_hr9", "neutral_pitcher_kbb",
        "remove_statcast_distribution_extra", "remove_rolling_quality_extra",
    ]
    gradeable = rows[rows.official_gradeable.astype(bool)].copy()
    summaries = {
        "diagnostic": _period_summary(gradeable, variants, diagnostic_dates),
        "confirmation": _period_summary(gradeable, variants, confirmation_dates),
    }

    def improves_both_blocks(name: str) -> bool:
        return all(
            summaries[role][name]["point"][metric]
            < summaries[role]["frozen_monte_carlo"]["point"][metric]
            for role in ("diagnostic", "confirmation")
            for metric in ("brier", "log_loss")
        )

    def confirmation_upper_below_zero(name: str) -> bool:
        interval = summaries["confirmation"][name]["paired_vs_frozen_monte_carlo"]
        return (
            interval["variant_minus_baseline_brier_95"][1] < 0.0
            and interval["variant_minus_baseline_log_loss_95"][1] < 0.0
        )

    context_variants = variants[4:]
    stable_components = []
    for name in context_variants:
        movement = summaries["confirmation"][name]["point"]["positive_ev_raw_movement"]
        if (
            improves_both_blocks(name)
            and confirmation_upper_below_zero(name)
            and movement is not None
            and movement >= 0.0
        ):
            stable_components.append(name)

    mc_supported = improves_both_blocks("active_exact") and confirmation_upper_below_zero(
        "active_exact"
    )
    pa_headroom = improves_both_blocks("oracle_realized_pa")
    report = {
        "schema_version": "hr-over-frozen-baseline-diagnostic-report-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "RESEARCH_ONLY",
        "betting_authorized": False,
        "may_opened": False,
        "historical_executability_verified": False,
        "protocol": {"path": str(protocol_path), "sha256": sha256_file(protocol_path)},
        "funnel": {
            "certified_market_keys": int(len(rows)),
            "official_gradeable_keys": int(rows.official_gradeable.astype(bool).sum()),
            "void_or_unresolved_keys": int((~rows.official_gradeable.astype(bool)).sum()),
            "persisted_feature_keys": int(len(features)),
        },
        "periods": summaries,
        "per_pa_and_volume": _per_pa_summary(rows),
        "decisions": {
            "monte_carlo_is_supported_limiter": bool(mc_supported),
            "realized_pa_oracle_shows_postgame_headroom": bool(pa_headroom),
            "stable_harmful_active_components": stable_components,
            "challenger_permitted": bool(stable_components),
            "note": "Permission means design one new time-safe challenger on pre-2026/open evidence; it is not promotion.",
        },
        "variants": {
            name: {
                "deployable": False,
                "label": (
                    "POSTGAME_LEAKAGE_ORACLE" if name == "oracle_realized_pa"
                    else "READ_ONLY_DIAGNOSTIC"
                ),
            }
            for name in variants
        },
        "interpretation": "The diagnostic can locate a limiter. It cannot authorize a bet, unseal May, or install an ablation as a model.",
    }
    validate_report(report)

    out_dir = (ROOT / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = out_dir / "diagnostic_rows.csv"
    report_path = out_dir / "report.json"
    rows.to_csv(rows_path, index=False)
    report["outputs"] = {
        "diagnostic_rows": {"path": str(rows_path), "sha256": sha256_file(rows_path)}
    }
    validate_report(report)
    tmp = report_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(report_path)

    print("HR FROZEN BASELINE DIAGNOSTIC COMPLETE — RESEARCH ONLY")
    print(f"  certified keys: {len(rows):,}; official gradeable: {int(rows.official_gradeable.sum()):,}")
    print(f"  Monte Carlo supported limiter: {mc_supported}")
    print(f"  realized-PA postgame headroom: {pa_headroom}")
    print(f"  stable harmful components: {stable_components}")
    print(f"  report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
