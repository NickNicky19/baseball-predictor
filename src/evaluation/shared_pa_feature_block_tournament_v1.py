"""Finite, date-blocked feature tournament on the frozen 2023 C0 parent.

Only the predeclared exponentially-decayed-count block is scoreable from the
immutable official outcome-label release.  Other proposed feature blocks are
represented as explicit unavailable evidence, never approximated from mutable
or postgame data.  This module does not import a frozen simulator or network
client and is development-only.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from src.evaluation import shared_pa_c0_tournament_v1 as c0
from src.evaluation.shared_pa_outcome_label_release_v1 import canonical_json_bytes, sha256_file


SCHEMA_VERSION = "shared-pa-feature-block-tournament-v1"
CANDIDATE_PREFIX = "shared_pa_c0_recency_decay"
MAX_MUTATION_PROOFS = 3


class FeatureBlockTournamentError(ValueError):
    """The locked parent, evidence boundary, or feature protocol is invalid."""


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeatureBlockTournamentError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise FeatureBlockTournamentError(f"{label} must be an object")
    return value


def _safe_existing_directory(path: Path, label: str) -> Path:
    resolved = Path(os.path.abspath(os.fspath(path)))
    if not resolved.is_dir() or resolved.is_symlink():
        raise FeatureBlockTournamentError(f"{label} must be a non-symlink directory")
    return resolved


def _safe_new_directory(path: Path) -> Path:
    resolved = Path(os.path.abspath(os.fspath(path)))
    if resolved.exists() or resolved.parent == resolved:
        raise FeatureBlockTournamentError("feature-tournament output already exists or is unsafe")
    cursor = resolved.parent
    while not cursor.exists():
        cursor = cursor.parent
    while True:
        if cursor.is_symlink():
            raise FeatureBlockTournamentError("feature-tournament output traverses a symlink")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return resolved


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(canonical_json_bytes(value))
    os.replace(temporary, path)


def _validate_protocol(protocol: Mapping[str, Any]) -> None:
    if protocol.get("schema_version") != SCHEMA_VERSION or protocol.get("status") != "LOCKED_BEFORE_STANDALONE_BLOCK_SCORING":
        raise FeatureBlockTournamentError("feature protocol identity differs")
    if protocol.get("research_only") is not True or protocol.get("betting_authorized") is not False:
        raise FeatureBlockTournamentError("feature protocol research boundary differs")
    parent = protocol.get("parent")
    if not isinstance(parent, Mapping) or parent.get("candidate_id") != c0.CANDIDATE_ID:
        raise FeatureBlockTournamentError("feature protocol parent differs")
    if parent.get("opportunity_arm") != "hierarchical_slot" or parent.get("opportunity_policy") != "PRESERVE_LOCKED_C0_PARENT_DO_NOT_SWITCH_TO_RAW_SLOT":
        raise FeatureBlockTournamentError("feature protocol opportunity boundary differs")
    evaluation = protocol.get("evaluation")
    if not isinstance(evaluation, Mapping) or evaluation.get("markets") != list(c0.MARKET_INCREMENTS):
        raise FeatureBlockTournamentError("feature protocol market contract differs")
    if evaluation.get("cluster") != "official_date" or evaluation.get("time_period") != "calendar_month":
        raise FeatureBlockTournamentError("feature protocol evaluation boundary differs")
    block = protocol.get("standalone_blocks", {}).get("learned_recency_decay")
    if not isinstance(block, Mapping) or block.get("status") != "ELIGIBLE_FOR_SCORING":
        raise FeatureBlockTournamentError("recency block is not locked for scoring")
    if block.get("candidate_half_lives_days") != [30, 90, 180, 360]:
        raise FeatureBlockTournamentError("recency finite candidate family differs")
    boundary = protocol.get("protected_boundaries")
    if not isinstance(boundary, Mapping) or any(boundary.get(key) is not False for key in (
        "may_2026_access_allowed", "selection_2024_accessed", "spent_2025_hr_confirmation_accessed",
        "prospective_evidence_accessed", "economic_evidence_accessed", "frozen_simulator_used",
        "frozen_monte_carlo_used", "betting_authorized",
    )):
        raise FeatureBlockTournamentError("feature protocol protected boundary differs")


def _weighted_counts(history: list[tuple[str, Counter[str]]], target_date: str, half_life_days: int) -> tuple[dict[str, float], float]:
    """Return only pre-target exponentially weighted outcome counts.

    Raw observations remain in ``history`` and the caller archives the raw
    denominator separately.  Fractional effective counts are deliberate and
    are never substituted for raw evidence identity or missingness.
    """
    target = date.fromisoformat(target_date)
    factor = math.log(2.0) / float(half_life_days)
    result = {name: 0.0 for name in c0.OUTCOMES}
    denominator = 0.0
    for observed_date, counts in history:
        delta = (target - date.fromisoformat(observed_date)).days
        if delta <= 0:
            raise FeatureBlockTournamentError("recency history crosses target cutoff")
        weight = math.exp(-factor * delta)
        for outcome in c0.OUTCOMES:
            result[outcome] += weight * float(counts.get(outcome, 0))
        denominator += weight * float(sum(counts.values()))
    if denominator <= 0.0 or not math.isfinite(denominator):
        raise FeatureBlockTournamentError("recency effective denominator is empty")
    return result, denominator


def _month(value: str) -> str:
    return value[:7]


def _empty_delta_state() -> dict[str, Any]:
    return {"dates": defaultdict(lambda: [0, 0.0, 0.0]), "months": defaultdict(lambda: [0, 0.0, 0.0])}


def _add_delta(state: dict[str, Any], official_date: str, candidate_losses: tuple[float, float], parent_losses: tuple[float, float]) -> None:
    for target in (state["dates"][official_date], state["months"][_month(official_date)]):
        target[0] += 1
        target[1] += candidate_losses[0] - parent_losses[0]
        target[2] += candidate_losses[1] - parent_losses[1]


def _month_summary(months: Mapping[str, list[float]]) -> list[dict[str, Any]]:
    return [
        {
            "month": month,
            "scored_rows": values[0],
            "brier_delta_candidate_minus_parent": values[1] / values[0],
            "log_loss_delta_candidate_minus_parent": values[2] / values[0],
        }
        for month, values in sorted(months.items()) if values[0]
    ]


def _decision(delta: Mapping[str, Any], monthly: list[Mapping[str, Any]], evaluation: Mapping[str, Any]) -> dict[str, Any]:
    retain = evaluation["retain"]
    reject = evaluation["reject"]
    brier = delta["brier_delta_candidate_minus_baseline"]
    log = delta["log_loss_delta_candidate_minus_baseline"]
    consistent = sum(
        1 for item in monthly
        if item["brier_delta_candidate_minus_parent"] <= 0.0 and item["log_loss_delta_candidate_minus_parent"] <= 0.0
    )
    retained = (
        brier["point"] <= -float(retain["minimum_absolute_brier_improvement"])
        and log["point"] <= -float(retain["minimum_absolute_log_loss_improvement"])
        and brier["upper_95"] < 0.0
        and log["upper_95"] < 0.0
        and consistent >= int(retain["minimum_consistent_months"])
    )
    rejected = brier["lower_95"] > 0.0 or log["lower_95"] > 0.0
    return {
        "status": "RETAIN" if retained else "REJECT" if rejected else "INCONCLUSIVE",
        "consistent_months_both_nonpositive": consistent,
        "total_months": len(monthly),
        "criteria": {
            "brier_point_at_most": -float(retain["minimum_absolute_brier_improvement"]),
            "log_loss_point_at_most": -float(retain["minimum_absolute_log_loss_improvement"]),
            "both_upper_95_below_zero": True,
            "minimum_consistent_months": int(retain["minimum_consistent_months"]),
            "reject_if_either_lower_95_above_zero": bool(reject["reject_if_either_95_lower_bound_above_zero"]),
        },
    }


def _source_inventory(protocol: Mapping[str, Any]) -> dict[str, Any]:
    blocks = protocol["standalone_blocks"]
    result = {}
    for name, details in sorted(blocks.items()):
        result[name] = {
            "status": details["status"],
            "scientific_hypothesis": details["scientific_hypothesis"],
            "source_fields": details.get("source_fields", details.get("required_fields", [])),
            "point_in_time_evidence": "VERIFIED_PRETARGET_OFFICIAL_LABEL_HISTORY" if name == "learned_recency_decay" else "NOT_RETAINED_NO_EXTERNAL_CAPTURE_AUTHORIZED",
            "expected_markets": details.get("affects_markets", []),
            "limitation": details.get("reason"),
        }
    return result


def _diagnose_count_classes(parent_release: Path) -> dict[str, Any]:
    """A bounded frozen-C0 diagnostic using archived observed rows only."""
    result: dict[str, dict[int, dict[str, float]]] = {market: {} for market in c0.MARKET_INCREMENTS}
    limitation = "Observed archived C0 rows only; implicit zero rows are outside the bounded diagnostic archive. This diagnostic neither refits nor selects a model."
    with (parent_release / "historical_prediction_rows.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            for market in c0.MARKET_INCREMENTS:
                observed = int(row["observed"][market])
                bucket = result[market].setdefault(observed, {"count": 0.0, "brier_delta_c0_minus_player": 0.0, "log_loss_delta_c0_minus_player": 0.0})
                parent = c0._losses(row["candidate_markets"][market]["pmf"], observed)
                player = c0._losses(row["player_rate_baseline_markets"][market]["pmf"], observed)
                bucket["count"] += 1.0
                bucket["brier_delta_c0_minus_player"] += parent[0] - player[0]
                bucket["log_loss_delta_c0_minus_player"] += parent[1] - player[1]
    return {
        "schema_version": "shared-pa-c0-count-class-diagnostic-v1",
        "frozen_c0_release": "d6bf5bd3517b34c2b54dde7ff89ab4148f1095a5f7ad98447a1004debcd85736",
        "markets": {
            market: [
                {
                    "observed_count_class": count,
                    "rows": int(values["count"]),
                    "mean_brier_delta_c0_minus_player": values["brier_delta_c0_minus_player"] / values["count"],
                    "mean_log_loss_delta_c0_minus_player": values["log_loss_delta_c0_minus_player"] / values["count"],
                }
                for count, values in sorted(buckets.items())
            ]
            for market, buckets in result.items() if market in ("hits", "total_bases")
        },
        "limitation": limitation,
        "mutates_c0": False,
        "used_for_selection": False,
    }


def run_feature_block_tournament(*, label_release: Path, parent_release: Path, c0_protocol_path: Path, feature_protocol_path: Path, output_dir: Path) -> dict[str, Any]:
    """Score the locked finite recency family against a reproduced C0 parent."""
    release_dir = _safe_existing_directory(label_release, "outcome label release")
    frozen_parent = _safe_existing_directory(parent_release, "C0 parent release")
    c0_protocol = _load(c0_protocol_path, "C0 protocol")
    c0._validate_protocol(c0_protocol)
    protocol_file = Path(os.path.abspath(os.fspath(feature_protocol_path)))
    protocol = _load(protocol_file, "feature-block protocol")
    _validate_protocol(protocol)
    parent_identity = str(protocol["parent"]["c0_tournament_release_sha256"])
    try:
        c0.verify_tournament_release(frozen_parent, expected_release_sha256=parent_identity)
    except c0.C0TournamentError as exc:
        raise FeatureBlockTournamentError("frozen C0 parent verification failed") from exc
    if protocol["parent"]["outcome_label_release_sha256"] != c0_protocol["source"]["outcome_release_sha256"]:
        raise FeatureBlockTournamentError("feature protocol label identity differs from parent protocol")
    _, rows = c0._read_rows(release_dir, c0_protocol)
    target = _safe_new_directory(output_dir)
    target.mkdir(parents=True)

    support = tuple(int(value) for value in c0_protocol["opportunity"]["pa_support"])
    evaluation_start = date.fromisoformat(c0_protocol["source"]["evaluation_start"])
    prior_strength = float(c0_protocol["per_pa"]["prior_strength_pa"])
    start_strength = float(c0_protocol["opportunity"]["start_prior_strength_team_games"])
    slot_strength = float(c0_protocol["opportunity"]["slot_prior_strength_starts"])
    slot_weight = float(c0_protocol["opportunity"]["hierarchical_slot_raw_weight"])
    half_lives = tuple(int(value) for value in protocol["standalone_blocks"]["learned_recency_decay"]["candidate_half_lives_days"])
    draws = int(protocol["evaluation"]["bootstrap_draws"])
    seed = int(protocol["evaluation"]["bootstrap_seed"])
    code_sha = sha256_file(Path(__file__))
    feature_schema = {
        "schema_version": "shared-pa-recency-feature-row-v1",
        "parent_candidate_id": c0.CANDIDATE_ID,
        "block": "learned_recency_decay",
        "half_lives_days": list(half_lives),
        "source_fields": protocol["standalone_blocks"]["learned_recency_decay"]["source_fields"],
        "opportunity_arm": "hierarchical_slot",
    }
    feature_schema_sha = _sha(feature_schema)

    games: dict[tuple[str, int, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        games[(row["official_date"], int(row["game_pk"]), row["team_side"], int(row["team_id"]))].append(row)
    by_date: dict[str, list[tuple[tuple[str, int, str, int], list[dict[str, Any]]]]] = defaultdict(list)
    for key, game_rows in games.items():
        by_date[key[0]].append((key, game_rows))

    state: dict[tuple[int, int], dict[str, Any]] = {}
    team_pool: dict[int, set[int]] = defaultdict(set)
    global_counts: Counter[str] = Counter()
    global_pa = 0
    global_slot_counts: Counter[int] = Counter()
    global_starters = 0
    pooled_pa_counts: Counter[int] = Counter()
    slot_pa_counts: dict[int, Counter[int]] = {slot: Counter() for slot in range(1, 10)}
    nonstart_pa_counts: Counter[int] = Counter()
    opportunity_total = 0
    opportunity_starts = 0
    score = {model: {market: c0._init_score() for market in c0.MARKET_INCREMENTS} for model in ("c0_parent", *[f"half_life_{value}" for value in half_lives])}
    deltas = {value: {market: _empty_delta_state() for market in c0.MARKET_INCREMENTS} for value in half_lives}
    mutation_proofs: dict[int, list[dict[str, Any]]] = {value: [] for value in half_lives}
    abstentions: Counter[str] = Counter()
    eligible_rows = 0
    archived_observed_rows = 0

    for official_date in sorted(by_date):
        day_games = sorted(by_date[official_date], key=lambda value: (value[0][1], value[0][2]))
        for (_, game_pk, side, team_id), game_rows in day_games:
            actual_by_player = {int(row["player_id"]): row for row in game_rows}
            for player_id in sorted(team_pool[team_id]):
                player_state = state[(team_id, player_id)]
                actual = actual_by_player.get(player_id)
                if date.fromisoformat(official_date) < evaluation_start:
                    continue
                if player_state["pa"] <= 0:
                    abstentions["NO_PRIOR_PLAYER_PA"] += 1
                    continue
                if global_pa <= 0 or global_starters <= 0 or opportunity_total <= 0 or sum(nonstart_pa_counts.values()) <= 0 or any(sum(slot_pa_counts[slot].values()) <= 0 for slot in range(1, 10)):
                    abstentions["INSUFFICIENT_PRIOR_OPPORTUNITY_HISTORY"] += 1
                    continue
                league = c0._probability(global_counts, global_pa)
                parent_per_pa = c0._empirical_bayes(player_state["counts"], player_state["pa"], global_counts, global_pa, prior_strength)
                start_probability = (player_state["starts"] + start_strength * (opportunity_starts / opportunity_total)) / (player_state["opportunities"] + start_strength)
                global_slot = {slot: global_slot_counts[slot] / global_starters for slot in range(1, 10)}
                slot_probability = {slot: (player_state["slots"][slot] + slot_strength * global_slot[slot]) / (player_state["starts"] + slot_strength) for slot in range(1, 10)}
                pooled = c0._pmf(pooled_pa_counts, support)
                raw_slots = {slot: c0._pmf(slot_pa_counts[slot], support) for slot in range(1, 10)}
                hierarchical_slots = {slot: {pa: slot_weight * raw_slots[slot][pa] + (1.0 - slot_weight) * pooled[pa] for pa in support} for slot in range(1, 10)}
                nonstart = c0._pmf(nonstart_pa_counts, support)
                opportunity = c0._mix([(1.0 - start_probability, nonstart)] + [(start_probability * slot_probability[slot], hierarchical_slots[slot]) for slot in range(1, 10)], support)
                parent_markets = c0._market_record(parent_per_pa, opportunity)
                candidate_per_pa: dict[int, dict[str, float]] = {}
                candidate_markets: dict[int, dict[str, dict[str, Any]]] = {}
                for half_life in half_lives:
                    counts, effective_pa = _weighted_counts(player_state["history"], official_date, half_life)
                    candidate_per_pa[half_life] = c0._empirical_bayes(counts, effective_pa, global_counts, global_pa, prior_strength)
                    candidate_markets[half_life] = c0._market_record(candidate_per_pa[half_life], opportunity)
                for market, field in c0.OBSERVED_FIELD.items():
                    observed = int(actual[field]) if actual else 0
                    parent_losses = c0._add_score(score["c0_parent"][market], official_date, parent_markets[market]["pmf"], observed)
                    for half_life in half_lives:
                        candidate_losses = c0._add_score(score[f"half_life_{half_life}"][market], official_date, candidate_markets[half_life][market]["pmf"], observed)
                        _add_delta(deltas[half_life][market], official_date, candidate_losses, parent_losses)
                if actual is not None:
                    archived_observed_rows += 1
                    for half_life in half_lives:
                        if len(mutation_proofs[half_life]) >= MAX_MUTATION_PROOFS:
                            continue
                        changed = any(
                            parent_markets[market]["pmf"] != candidate_markets[half_life][market]["pmf"]
                            for market in c0.MARKET_INCREMENTS
                        )
                        if changed:
                            raw_counts = {name: int(player_state["counts"][name]) for name in c0.OUTCOMES}
                            effective_counts, effective_pa = _weighted_counts(player_state["history"], official_date, half_life)
                            proof = {
                                "schema_version": "shared-pa-feature-block-mutation-proof-v1",
                                "candidate_id": f"{CANDIDATE_PREFIX}_{half_life}d_v1",
                                "official_date": official_date,
                                "game_pk": game_pk,
                                "team_side": side,
                                "team_id": team_id,
                                "player_id": player_id,
                                "raw_player_counts": raw_counts,
                                "raw_player_pa": player_state["pa"],
                                "effective_weighted_counts": effective_counts,
                                "effective_weighted_pa": effective_pa,
                                "parent_per_pa": parent_per_pa,
                                "candidate_per_pa": candidate_per_pa[half_life],
                                "parent_markets": parent_markets,
                                "candidate_markets": candidate_markets[half_life],
                                "pmf_changed": True,
                                "archive_record_sha256": "",
                            }
                            proof["archive_record_sha256"] = _sha({key: value for key, value in proof.items() if key != "archive_record_sha256"})
                            mutation_proofs[half_life].append(proof)
                eligible_rows += 1

        # Freeze all predictions for the date before incorporating outcomes.
        for (_, _, _, team_id), game_rows in day_games:
            actual_by_player = {int(row["player_id"]): row for row in game_rows}
            existing = set(team_pool[team_id])
            for player_id in existing:
                player_state = state[(team_id, player_id)]
                actual = actual_by_player.get(player_id)
                player_state["opportunities"] += 1
                opportunity_total += 1
                if actual and actual["starter"]:
                    player_state["starts"] += 1
                    player_state["slots"][int(actual["lineup_slot"])] += 1
                    opportunity_starts += 1
                else:
                    nonstart_pa_counts[int(actual["plate_appearances"]) if actual else 0] += 1
            for row in game_rows:
                player_id = int(row["player_id"])
                key = (team_id, player_id)
                if key not in state:
                    state[key] = {"pa": 0, "counts": Counter(), "history": [], "opportunities": 0, "starts": 0, "slots": Counter()}
                    team_pool[team_id].add(player_id)
                player_state = state[key]
                outcomes = c0._category_counts(row)
                player_state["counts"].update(outcomes)
                player_state["pa"] += int(row["plate_appearances"])
                player_state["history"].append((row["official_date"], outcomes))
                global_counts.update(outcomes)
                global_pa += int(row["plate_appearances"])
                if row["starter"]:
                    slot = int(row["lineup_slot"])
                    global_slot_counts[slot] += 1
                    global_starters += 1
                    pooled_pa_counts[int(row["plate_appearances"])] += 1
                    slot_pa_counts[slot][int(row["plate_appearances"])] += 1
                    if player_id not in existing:
                        player_state["opportunities"] += 1
                        opportunity_total += 1
                        player_state["starts"] += 1
                        player_state["slots"][slot] += 1
                        opportunity_starts += 1

    if eligible_rows <= 0 or archived_observed_rows <= 0:
        raise FeatureBlockTournamentError("feature tournament produced no eligible predictions")
    parent_eval = _load(frozen_parent / "evaluation.json", "frozen parent evaluation")
    parent_reproduction: dict[str, Any] = {}
    for market in c0.MARKET_INCREMENTS:
        observed = c0._score_summary(score["c0_parent"][market])
        expected = parent_eval["markets"][market]["c0_hierarchical"]
        fields = ("scored_rows", "multiclass_brier", "multiclass_log_loss", "over_0_5_brier", "over_0_5_log_loss", "over_0_5_auc")
        matches = all(math.isclose(float(observed[field]), float(expected[field]), abs_tol=1e-12) for field in fields if field != "scored_rows") and observed["scored_rows"] == expected["scored_rows"]
        if not matches:
            raise FeatureBlockTournamentError(f"C0 parent reproduction differs for {market}")
        parent_reproduction[market] = {"status": "EXACT_REPRODUCTION", "metrics": {field: observed[field] for field in fields}}

    candidates: dict[str, Any] = {}
    retained_by_market: dict[str, list[dict[str, Any]]] = {market: [] for market in c0.MARKET_INCREMENTS}
    for half_life in half_lives:
        candidate_id = f"{CANDIDATE_PREFIX}_{half_life}d_v1"
        markets = {}
        for market in c0.MARKET_INCREMENTS:
            summary = c0._score_summary(score[f"half_life_{half_life}"][market])
            delta = c0._bootstrap_delta(deltas[half_life][market]["dates"], draws, seed + half_life)
            monthly = _month_summary(deltas[half_life][market]["months"])
            decision = _decision(delta, monthly, protocol["evaluation"])
            markets[market] = {
                "candidate": summary,
                "parent": c0._score_summary(score["c0_parent"][market]),
                "paired_date_clustered_uncertainty_candidate_minus_parent": delta,
                "calendar_month_consistency": monthly,
                "decision": decision,
            }
            if decision["status"] == "RETAIN":
                retained_by_market[market].append({"half_life_days": half_life, "brier_delta": delta["brier_delta_candidate_minus_baseline"]["point"], "log_loss_delta": delta["log_loss_delta_candidate_minus_baseline"]["point"]})
        candidates[candidate_id] = {
            "candidate_id": candidate_id,
            "block": "learned_recency_decay",
            "half_life_days": half_life,
            "source_fields": protocol["standalone_blocks"]["learned_recency_decay"]["source_fields"],
            "feature_schema_sha256": feature_schema_sha,
            "candidate_code_sha256": code_sha,
            "mutation_proofs": mutation_proofs[half_life],
            "markets": markets,
        }
    selected = {}
    for market, rows_for_market in retained_by_market.items():
        # Selection was predeclared: strongest joint absolute proper-score reduction, then shorter half-life.
        ordered = sorted(rows_for_market, key=lambda item: (item["brier_delta"] / float(protocol["evaluation"]["retain"]["minimum_absolute_brier_improvement"]) + item["log_loss_delta"] / float(protocol["evaluation"]["retain"]["minimum_absolute_log_loss_improvement"]), item["half_life_days"]))
        selected[market] = ({"status": "LOCKED_DEVELOPMENT_CHALLENGER", "candidate_id": f"{CANDIDATE_PREFIX}_{ordered[0]['half_life_days']}d_v1", "selection_rule": "largest predeclared joint normalized proper-score improvement among retained finite variants"} if ordered else {"status": "NO_LOCKED_CHALLENGER", "reason": "No finite recency variant met both market-specific retain criteria"})

    evaluation = {
        "schema_version": "shared-pa-feature-block-market-evaluation-v1",
        "status": "COMPLETE_FINITE_STANDALONE_FEATURE_BLOCK_TOURNAMENT",
        "research_only": True,
        "betting_authorized": False,
        "parent": {"candidate_id": c0.CANDIDATE_ID, "release_sha256": parent_identity, "opportunity_arm": "hierarchical_slot", "reproduction": parent_reproduction},
        "coverage": {"eligible_team_player_rows": eligible_rows, "archived_observed_player_rows": archived_observed_rows, "abstentions": dict(sorted(abstentions.items()))},
        "source_inventory": _source_inventory(protocol),
        "candidates": candidates,
        "locked_challenger_by_market": selected,
        "combinations": {"status": "NOT_RUN", "reason": "No independently scoreable non-redundant second block exists in the authorized retained evidence."},
        "delete_one_ablations": {"status": "NOT_RUN", "reason": "No supported combination was eligible."},
        "protected_boundaries": protocol["protected_boundaries"],
    }
    final_fit = {
        "schema_version": "shared-pa-feature-block-final-fit-v1",
        "research_only": True,
        "parent_release_sha256": parent_identity,
        "outcome_label_release_sha256": protocol["parent"]["outcome_label_release_sha256"],
        "protocol_sha256": sha256_file(protocol_file),
        "candidate_code_sha256": code_sha,
        "feature_schema_sha256": feature_schema_sha,
        "fitted_artifacts": [
            {"candidate_id": f"{CANDIDATE_PREFIX}_{half_life}d_v1", "block": "learned_recency_decay", "half_life_days": half_life, "model": "time-safe exponentially weighted outcome counts plus locked 200-PA league empirical-Bayes prior"}
            for half_life in half_lives
        ],
        "raw_count_and_denominator_policy": "Raw outcome counts and raw PA remain retained in the immutable label release; each candidate derives a separate strictly pretarget effective weighted count and denominator for fitting only.",
        "parameters": {"learned": ["pretarget player outcome counts"], "structural": ["13-category PA simplex", "exact convolution", "locked hierarchical opportunity mixture"], "predeclared_finite": {"half_lives_days": list(half_lives), "prior_strength_pa": prior_strength}},
    }
    _write_json(target / "evaluation.json", evaluation)
    _write_json(target / "feature_block_final_fit.json", final_fit)
    _write_json(target / "hits_tb_count_class_diagnostic.json", _diagnose_count_classes(frozen_parent))
    _write_json(target / "block_source_inventory.json", _source_inventory(protocol))
    readme = "\n".join((
        "# Finite Shared-PA Feature Block Tournament", "",
        "Development-only, date-blocked comparison against frozen C0. The locked hierarchical opportunity arm is unchanged.",
        "Only learned recency decay is scoreable from the retained official 2023 labels. Other requested blocks are explicitly unavailable, not approximated.",
        "No result authorizes deployment, prospective use, production replacement, or betting.", "",
    ))
    (target / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    files = [item for item in sorted(target.iterdir()) if item.name != "release_manifest.json"]
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "status": "COMPLETE_FINITE_STANDALONE_FEATURE_BLOCK_TOURNAMENT",
        "research_only": True,
        "betting_authorized": False,
        "parent_c0_release_sha256": parent_identity,
        "outcome_label_release_sha256": protocol["parent"]["outcome_label_release_sha256"],
        "protocol_sha256": sha256_file(protocol_file),
        "candidate_code_sha256": code_sha,
        "feature_schema_sha256": feature_schema_sha,
        "protected_boundaries": protocol["protected_boundaries"],
        "files": [{"path": item.name, "size": item.stat().st_size, "sha256": sha256_file(item)} for item in files],
    }
    manifest = {**unsigned, "release_sha256": _sha(unsigned)}
    _write_json(target / "release_manifest.json", manifest)
    return manifest


def verify_feature_block_release(path: Path, *, expected_release_sha256: str | None = None) -> dict[str, Any]:
    root = _safe_existing_directory(path, "feature tournament release")
    manifest = _load(root / "release_manifest.json", "feature tournament manifest")
    unsigned = dict(manifest)
    supplied = unsigned.pop("release_sha256", None)
    observed = _sha(unsigned)
    if supplied != observed or (expected_release_sha256 and expected_release_sha256 != observed):
        raise FeatureBlockTournamentError("feature tournament manifest identity differs")
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("status") != "COMPLETE_FINITE_STANDALONE_FEATURE_BLOCK_TOURNAMENT":
        raise FeatureBlockTournamentError("feature tournament manifest schema differs")
    for item in manifest.get("files", []):
        file = root / str(item.get("path"))
        if not file.is_file() or file.is_symlink() or file.stat().st_size != item.get("size") or sha256_file(file) != item.get("sha256"):
            raise FeatureBlockTournamentError("feature tournament file binding differs")
    evaluation = _load(root / "evaluation.json", "feature tournament evaluation")
    if evaluation.get("research_only") is not True or evaluation.get("betting_authorized") is not False:
        raise FeatureBlockTournamentError("feature tournament boundary differs")
    if evaluation["parent"]["opportunity_arm"] != "hierarchical_slot":
        raise FeatureBlockTournamentError("feature tournament changed locked opportunity arm")
    for candidate in evaluation["candidates"].values():
        if not candidate["mutation_proofs"] or not all(proof.get("pmf_changed") is True for proof in candidate["mutation_proofs"]):
            raise FeatureBlockTournamentError("feature block lacks PMF mutation proof")
        for proof in candidate["mutation_proofs"]:
            for route in ("parent_markets", "candidate_markets"):
                for market, record in proof[route].items():
                    if market not in c0.MARKET_INCREMENTS:
                        raise FeatureBlockTournamentError("mutation proof contains an unsupported market")
                    pmf = [float(value) for value in record.get("pmf", [])]
                    if not pmf or any(value < 0.0 or not math.isfinite(value) for value in pmf) or not math.isclose(sum(pmf), 1.0, abs_tol=1e-10):
                        raise FeatureBlockTournamentError("mutation proof PMF is invalid")
                    if not math.isclose(float(record.get("projected_value")), c0._mean(pmf), abs_tol=1e-12):
                        raise FeatureBlockTournamentError("mutation proof mean does not derive from PMF")
                    if not math.isclose(float(record.get("threshold_probabilities", {}).get("over_0_5")), 1.0 - pmf[0], abs_tol=1e-12):
                        raise FeatureBlockTournamentError("mutation proof threshold does not derive from PMF")
        for market, values in candidate["markets"].items():
            if market not in c0.MARKET_INCREMENTS or values["candidate"]["scored_rows"] <= 0:
                raise FeatureBlockTournamentError("feature tournament market evaluation is incomplete")
    return {"schema_version": "shared-pa-feature-block-tournament-verification-v1", "status": "FEATURE_BLOCK_TOURNAMENT_RELEASE_VERIFIED", "release_sha256": observed, "research_only": True, "betting_authorized": False}
