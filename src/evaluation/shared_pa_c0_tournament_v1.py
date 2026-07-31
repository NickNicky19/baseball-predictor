"""Strict, deterministic C0 shared-PA hitter tournament on official 2023 labels.

This is a research-development implementation, not a production predictor.  It
uses only rows dated *before* each target date, has no network code, and never
imports the frozen simulator.  Its only model is a time-safe empirical-Bayes
per-PA categorical distribution mixed through a separately time-safe
start/slot/PA opportunity distribution.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import random
from typing import Any, Mapping

from src.evaluation.shared_pa_outcome_label_release_v1 import (
    OutcomeLabelReleaseError,
    canonical_json_bytes,
    sha256_file,
    verify_release,
)


CANDIDATE_ID = "shared_pa_c0_batter_only_v1"
SCHEMA_VERSION = "shared-pa-c0-2023-tournament-v1"
OUTCOMES = (
    "strikeout", "non_intentional_walk", "intentional_walk", "hit_by_pitch",
    "home_run", "single", "double", "triple", "bip_out", "sac_fly",
    "sac_bunt", "catcher_interference", "other_official_pa",
)
MARKET_INCREMENTS = {
    "hits": {"single": 1, "double": 1, "triple": 1, "home_run": 1},
    "home_runs": {"home_run": 1},
    "total_bases": {"single": 1, "double": 2, "triple": 3, "home_run": 4},
    "hitter_strikeouts": {"strikeout": 1},
    "hitter_walks": {"non_intentional_walk": 1, "intentional_walk": 1},
}
OBSERVED_FIELD = {
    "hits": "hits", "home_runs": "home_runs", "total_bases": "total_bases",
    "hitter_strikeouts": "strikeouts", "hitter_walks": "base_on_balls",
}
FEATURE_SCHEMA = {
    "schema_version": "shared-pa-c0-pit-feature-row-v1",
    "required": [
        "candidate_id", "official_date", "game_pk", "team_side", "team_id", "player_id",
        "decision_cutoff", "player_prior_pa", "team_player_prior_opportunities",
        "start_probability", "slot_probabilities", "opportunity_pmf", "per_pa_probability",
        "feature_sha256",
    ],
}


class C0TournamentError(ValueError):
    """The locked C0 protocol or its source release is unsafe to use."""


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise C0TournamentError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise C0TournamentError(f"{label} must be an object")
    return value


def _safe_existing_directory(path: Path, label: str) -> Path:
    resolved = Path(os.path.abspath(os.fspath(path)))
    if not resolved.is_dir() or resolved.is_symlink():
        raise C0TournamentError(f"{label} must be a non-symlink directory")
    return resolved


def _safe_new_directory(path: Path) -> Path:
    resolved = Path(os.path.abspath(os.fspath(path)))
    if resolved.exists() or resolved.parent == resolved:
        raise C0TournamentError("tournament output already exists or is unsafe")
    cursor = resolved.parent
    while not cursor.exists():
        cursor = cursor.parent
    while True:
        if cursor.is_symlink():
            raise C0TournamentError("tournament output traverses a symlink")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return resolved


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(canonical_json_bytes(value))
    os.replace(temporary, path)


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        for row in rows:
            handle.write(canonical_json_bytes(row))
    os.replace(temporary, path)


def _validate_protocol(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != "shared-pa-c0-protocol-v1":
        raise C0TournamentError("C0 protocol schema differs")
    if value.get("candidate_id") != CANDIDATE_ID or value.get("status") != "LOCKED_BEFORE_2023_OUTCOME_EVALUATION":
        raise C0TournamentError("C0 protocol candidate identity differs")
    if value.get("research_only") is not True or value.get("betting_authorized") is not False:
        raise C0TournamentError("C0 protocol research boundary differs")
    source = value.get("source")
    if not isinstance(source, Mapping) or source.get("season") != 2023 or source.get("strict_cutoff") != "official_date_before_target_date":
        raise C0TournamentError("C0 protocol source cutoff differs")
    if value.get("outcome_categories") != list(OUTCOMES):
        raise C0TournamentError("C0 outcome category contract differs")
    opportunity = value.get("opportunity")
    if not isinstance(opportunity, Mapping) or opportunity.get("pa_support") != list(range(8)):
        raise C0TournamentError("C0 PA support contract differs")
    if opportunity.get("hierarchical_slot_raw_weight") != 0.2:
        raise C0TournamentError("C0 hierarchical opportunity weight differs")
    per_pa = value.get("per_pa")
    if not isinstance(per_pa, Mapping) or per_pa.get("prior_strength_pa") != 200.0:
        raise C0TournamentError("C0 empirical-Bayes prior differs")
    protected = value.get("protected_boundaries")
    if not isinstance(protected, Mapping) or any(protected.get(key) is not False for key in (
        "may_2026_access_allowed", "selection_2024_accessed", "spent_2025_hr_confirmation_accessed",
        "prospective_evidence_accessed", "economic_evidence_accessed", "frozen_simulator_used",
        "frozen_monte_carlo_used", "betting_authorized",
    )):
        raise C0TournamentError("C0 protected boundary differs")


def _category_counts(row: Mapping[str, Any]) -> Counter[str]:
    result = Counter({
        "strikeout": int(row["strikeouts"]),
        "non_intentional_walk": int(row["non_intentional_walks"]),
        "intentional_walk": int(row["intentional_walks"]),
        "hit_by_pitch": int(row["hit_by_pitch"]),
        "home_run": int(row["home_runs"]),
        "single": int(row["singles"]),
        "double": int(row["doubles"]),
        "triple": int(row["triples"]),
        "bip_out": int(row["non_hit_non_strikeout_at_bats"]),
        "sac_fly": int(row["sac_flies"]),
        "sac_bunt": int(row["sac_bunts"]),
        "catcher_interference": int(row["catcher_interference"]),
        "other_official_pa": int(row["other_official_pa"]),
    })
    if any(value < 0 for value in result.values()) or sum(result.values()) != int(row["plate_appearances"]):
        raise C0TournamentError("outcome label PA partition differs")
    return result


def _probability(counts: Mapping[str, int], total: int) -> dict[str, float]:
    if total <= 0:
        raise C0TournamentError("per-PA denominator is empty")
    probability = {name: float(counts.get(name, 0)) / total for name in OUTCOMES}
    if any(value < 0.0 or not math.isfinite(value) for value in probability.values()) or not math.isclose(sum(probability.values()), 1.0, abs_tol=1e-12):
        raise C0TournamentError("per-PA distribution is invalid")
    return probability


def _empirical_bayes(player: Mapping[str, int], player_pa: int, league: Mapping[str, int], league_pa: int, strength: float) -> dict[str, float]:
    prior = _probability(league, league_pa)
    denominator = player_pa + strength
    probability = {name: (float(player.get(name, 0)) + strength * prior[name]) / denominator for name in OUTCOMES}
    if not math.isclose(sum(probability.values()), 1.0, abs_tol=1e-12):
        raise C0TournamentError("empirical-Bayes distribution is invalid")
    return probability


def _pmf(counter: Mapping[int, int], support: tuple[int, ...]) -> dict[int, float]:
    total = sum(int(counter.get(value, 0)) for value in support)
    if total <= 0:
        raise C0TournamentError("opportunity PMF denominator is empty")
    result = {value: float(counter.get(value, 0)) / total for value in support}
    if not math.isclose(sum(result.values()), 1.0, abs_tol=1e-12):
        raise C0TournamentError("opportunity PMF is invalid")
    return result


def _mix(parts: list[tuple[float, Mapping[int, float]]], support: tuple[int, ...]) -> dict[int, float]:
    result = {value: 0.0 for value in support}
    for weight, probability in parts:
        if weight < 0.0 or not math.isfinite(weight):
            raise C0TournamentError("mixture weight is invalid")
        for value in support:
            result[value] += weight * float(probability.get(value, 0.0))
    if not math.isclose(sum(result.values()), 1.0, abs_tol=1e-10):
        raise C0TournamentError("opportunity mixture does not sum to one")
    return result


def _market_pmf(per_pa: Mapping[str, float], pa_pmf: Mapping[int, float], increments: Mapping[str, int]) -> list[float]:
    maximum_increment = max(increments.values(), default=0)
    maximum_pa = max(pa_pmf)
    one_pa = [0.0] * (maximum_increment + 1)
    for outcome, probability in per_pa.items():
        one_pa[increments.get(outcome, 0)] += probability
    if not math.isclose(sum(one_pa), 1.0, abs_tol=1e-12):
        raise C0TournamentError("one-PA market distribution is invalid")
    result = [0.0] * (maximum_pa * maximum_increment + 1)
    for pa, pa_mass in pa_pmf.items():
        convolved = [1.0]
        for _ in range(pa):
            next_pmf = [0.0] * (len(convolved) + maximum_increment)
            for left, left_mass in enumerate(convolved):
                for right, right_mass in enumerate(one_pa):
                    next_pmf[left + right] += left_mass * right_mass
            convolved = next_pmf
        for index, value in enumerate(convolved):
            result[index] += pa_mass * value
    if any(value < -1e-14 or not math.isfinite(value) for value in result) or not math.isclose(sum(result), 1.0, abs_tol=1e-10):
        raise C0TournamentError("exact market PMF is invalid")
    return result


def _mean(pmf: list[float]) -> float:
    return sum(index * value for index, value in enumerate(pmf))


def _market_record(per_pa: Mapping[str, float], pa_pmf: Mapping[int, float]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for market, increments in MARKET_INCREMENTS.items():
        pmf = _market_pmf(per_pa, pa_pmf, increments)
        result[market] = {
            "market": market,
            "projected_value": _mean(pmf),
            "pmf": pmf,
            "threshold_probabilities": {"over_0_5": 1.0 - pmf[0]},
        }
    return result


def _losses(pmf: list[float], observed: int) -> tuple[float, float, float, float, float]:
    if observed < 0 or observed >= len(pmf):
        raise C0TournamentError("observed market result lies outside emitted PMF support")
    multiclass_brier = sum(value * value for value in pmf) - 2.0 * pmf[observed] + 1.0
    multiclass_log = -math.log(max(pmf[observed], 1e-15))
    probability = 1.0 - pmf[0]
    outcome = float(observed >= 1)
    brier = (probability - outcome) ** 2
    log = -(outcome * math.log(max(probability, 1e-15)) + (1.0 - outcome) * math.log(max(1.0 - probability, 1e-15)))
    return multiclass_brier, multiclass_log, brier, log, probability


def _init_score() -> dict[str, Any]:
    return {"n": 0, "sums": [0.0, 0.0, 0.0, 0.0], "dates": defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0]), "bins": [[0, 0, 0.0] for _ in range(10)], "pairs": []}


def _add_score(score: dict[str, Any], official_date: str, pmf: list[float], observed: int) -> tuple[float, float]:
    values = _losses(pmf, observed)
    score["n"] += 1
    for index in range(4):
        score["sums"][index] += values[index]
    daily = score["dates"][official_date]
    daily[0] += 1
    for index in range(4):
        daily[index + 1] += values[index]
    probability = values[4]
    outcome = int(observed >= 1)
    bin_index = min(9, int(probability * 10.0))
    score["bins"][bin_index][0] += 1
    score["bins"][bin_index][1] += outcome
    score["bins"][bin_index][2] += probability
    score["pairs"].append((probability, outcome))
    return values[0], values[1]


def _auc(pairs: list[tuple[float, int]]) -> float | None:
    positives = sum(outcome for _, outcome in pairs)
    negatives = len(pairs) - positives
    if positives == 0 or negatives == 0:
        return None
    ordered = sorted(pairs, key=lambda item: item[0])
    rank_sum = 0.0
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][0] == ordered[cursor][0]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        rank_sum += average_rank * sum(outcome for _, outcome in ordered[cursor:end])
        cursor = end
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def _score_summary(score: dict[str, Any]) -> dict[str, Any]:
    n = score["n"]
    calibration = []
    for index, (count, events, probability_sum) in enumerate(score["bins"]):
        if count:
            calibration.append({"bin": index, "count": count, "mean_probability": probability_sum / count, "observed_rate": events / count})
    return {
        "scored_rows": n,
        "multiclass_brier": score["sums"][0] / n,
        "multiclass_log_loss": score["sums"][1] / n,
        "over_0_5_brier": score["sums"][2] / n,
        "over_0_5_log_loss": score["sums"][3] / n,
        "over_0_5_auc": _auc(score["pairs"]),
        "over_0_5_calibration": calibration,
    }


def _bootstrap_delta(daily: Mapping[str, list[float]], draws: int, seed: int) -> dict[str, Any]:
    dates = sorted(daily)
    if not dates:
        raise C0TournamentError("paired uncertainty has no date clusters")
    point = [sum(item[index] for item in daily.values()) / sum(item[0] for item in daily.values()) for index in (1, 2)]
    rng = random.Random(seed)
    samples = [[], []]
    for _ in range(draws):
        chosen = [daily[dates[rng.randrange(len(dates))]] for _ in dates]
        denominator = sum(item[0] for item in chosen)
        for index in (1, 2):
            samples[index - 1].append(sum(item[index] for item in chosen) / denominator)
    lower_index = int(math.floor(0.025 * (draws - 1)))
    upper_index = int(math.ceil(0.975 * (draws - 1)))
    return {
        "unit": "official_date",
        "draws": draws,
        "brier_delta_candidate_minus_baseline": {"point": point[0], "lower_95": sorted(samples[0])[lower_index], "upper_95": sorted(samples[0])[upper_index]},
        "log_loss_delta_candidate_minus_baseline": {"point": point[1], "lower_95": sorted(samples[1])[lower_index], "upper_95": sorted(samples[1])[upper_index]},
    }


def _opportunity_losses(pmf: Mapping[int, float], observed: int) -> tuple[float, float, float]:
    if observed not in pmf:
        raise C0TournamentError("observed PA lies outside locked opportunity support")
    brier = sum(value * value for value in pmf.values()) - 2 * pmf[observed] + 1.0
    log = -math.log(max(pmf[observed], 1e-15))
    mae = abs(sum(value * probability for value, probability in pmf.items()) - observed)
    return brier, log, mae


def _summarize_opportunity(parts: Mapping[str, dict[str, Any]], pairs: Mapping[str, Mapping[str, list[float]]], draws: int, seed: int) -> dict[str, Any]:
    result: dict[str, Any] = {"arms": {}, "paired_official_date_bootstrap": {}}
    for arm, state in parts.items():
        n = state["n"]
        result["arms"][arm] = {key: state["sums"][index] / n for index, key in enumerate(("multiclass_brier", "multiclass_log_loss", "expected_pa_mae"))} | {"scored_rows": n}
    for name, daily in pairs.items():
        dates = sorted(daily)
        if not dates:
            continue
        rng = random.Random(seed)
        estimates = [[], [], []]
        original = []
        for index in range(3):
            original.append(sum(item[index + 1] for item in daily.values()) / sum(item[0] for item in daily.values()))
        for _ in range(draws):
            chosen = [daily[dates[rng.randrange(len(dates))]] for _ in dates]
            denominator = sum(item[0] for item in chosen)
            for index in range(3):
                estimates[index].append(sum(item[index + 1] for item in chosen) / denominator)
        lo, hi = int(math.floor(.025 * (draws - 1))), int(math.ceil(.975 * (draws - 1)))
        result["paired_official_date_bootstrap"][name] = {
            metric: {"point": original[index], "lower_95": sorted(estimates[index])[lo], "upper_95": sorted(estimates[index])[hi]}
            for index, metric in enumerate(("multiclass_brier", "multiclass_log_loss", "expected_pa_mae"))
        }
    return result


def _read_rows(label_release: Path, protocol: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _load_json(label_release / "release_manifest.json", "outcome label manifest")
    expected = protocol["source"]["outcome_release_sha256"]
    if manifest.get("release_sha256") != expected:
        raise C0TournamentError("outcome label release identity differs from locked protocol")
    try:
        verify_release(label_release, expected_release_sha256=expected)
    except OutcomeLabelReleaseError as exc:
        raise C0TournamentError("outcome label release verification failed") from exc
    rows = []
    for line in (label_release / "canonical_rows.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["official_date"].startswith("2026-05") or not row["official_date"].startswith("2023-"):
            raise C0TournamentError("protected chronology boundary differs")
        rows.append(row)
    return manifest, rows


def run_tournament(*, label_release: Path, protocol_path: Path, output_dir: Path) -> dict[str, Any]:
    """Fit C0 through expanding 2023 history and write predictions/evaluation."""
    release_dir = _safe_existing_directory(label_release, "outcome label release")
    protocol_file = Path(os.path.abspath(os.fspath(protocol_path)))
    protocol = _load_json(protocol_file, "C0 protocol")
    _validate_protocol(protocol)
    _, rows = _read_rows(release_dir, protocol)
    target = _safe_new_directory(output_dir)
    target.mkdir(parents=True)
    support = tuple(int(value) for value in protocol["opportunity"]["pa_support"])
    eval_start = date.fromisoformat(protocol["source"]["evaluation_start"])
    strength = float(protocol["per_pa"]["prior_strength_pa"])
    start_strength = float(protocol["opportunity"]["start_prior_strength_team_games"])
    slot_strength = float(protocol["opportunity"]["slot_prior_strength_starts"])
    slot_weight = float(protocol["opportunity"]["hierarchical_slot_raw_weight"])
    draws = int(protocol["evaluation"]["bootstrap_draws"])
    seed = int(protocol["evaluation"]["bootstrap_seed"])
    protocol_sha = sha256_file(protocol_file)
    code_sha = sha256_file(Path(__file__))
    feature_schema_sha = _sha(FEATURE_SCHEMA)

    games: dict[tuple[str, int, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        games[(row["official_date"], int(row["game_pk"]), row["team_side"], int(row["team_id"]))].append(row)
    by_date: dict[str, list[tuple[tuple[str, int, str, int], list[dict[str, Any]]]]] = defaultdict(list)
    for key, game_rows in games.items():
        by_date[key[0]].append((key, game_rows))

    player_state: dict[tuple[int, int], dict[str, Any]] = {}
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
    score_states = {model: {market: _init_score() for market in MARKET_INCREMENTS} for model in ("c0_hierarchical", "league_rate", "player_rate")}
    pair_daily: dict[str, dict[str, dict[str, list[float]]]] = {
        name: {market: defaultdict(lambda: [0, 0.0, 0.0]) for market in MARKET_INCREMENTS}
        for name in ("c0_minus_league", "c0_minus_player")
    }
    opportunity_states = {arm: {"n": 0, "sums": [0.0, 0.0, 0.0]} for arm in ("pooled", "raw_slot", "hierarchical_slot")}
    opportunity_pairs: dict[str, dict[str, list[float]]] = {"hierarchical_minus_pooled": defaultdict(lambda: [0, 0.0, 0.0, 0.0]), "hierarchical_minus_raw_slot": defaultdict(lambda: [0, 0.0, 0.0, 0.0])}
    prediction_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    abstentions: Counter[str] = Counter()
    eligible_rows = 0
    represented_actual_starters = 0
    total_actual_starters = sum(1 for row in rows if row["starter"])

    for official_date in sorted(by_date):
        day_games = sorted(by_date[official_date], key=lambda item: (item[0][1], item[0][2]))
        day_predictions: list[tuple[int, int, Mapping[str, Any], dict[str, Any]]] = []
        for (_, game_pk, side, team_id), game_rows in day_games:
            actual_by_player = {int(row["player_id"]): row for row in game_rows}
            pool = sorted(team_pool[team_id])
            for player_id in pool:
                state = player_state[(team_id, player_id)]
                actual = actual_by_player.get(player_id)
                if date.fromisoformat(official_date) < eval_start:
                    continue
                if state["pa"] <= 0:
                    abstentions["NO_PRIOR_PLAYER_PA"] += 1
                    continue
                if global_pa <= 0 or global_starters <= 0 or opportunity_total <= 0 or sum(nonstart_pa_counts.values()) <= 0 or any(sum(slot_pa_counts[slot].values()) <= 0 for slot in range(1, 10)):
                    abstentions["INSUFFICIENT_PRIOR_OPPORTUNITY_HISTORY"] += 1
                    continue
                league = _probability(global_counts, global_pa)
                player = _probability(state["counts"], state["pa"])
                c0 = _empirical_bayes(state["counts"], state["pa"], global_counts, global_pa, strength)
                global_start_rate = opportunity_starts / opportunity_total
                start_probability = (state["starts"] + start_strength * global_start_rate) / (state["opportunities"] + start_strength)
                global_slot = {slot: global_slot_counts[slot] / global_starters for slot in range(1, 10)}
                slot_probability = {
                    slot: (state["slots"][slot] + slot_strength * global_slot[slot]) / (state["starts"] + slot_strength)
                    for slot in range(1, 10)
                }
                pooled = _pmf(pooled_pa_counts, support)
                raw_slots = {slot: _pmf(slot_pa_counts[slot], support) for slot in range(1, 10)}
                hierarchical_slots = {
                    slot: {value: slot_weight * raw_slots[slot][value] + (1.0 - slot_weight) * pooled[value] for value in support}
                    for slot in range(1, 10)
                }
                nonstart = _pmf(nonstart_pa_counts, support)
                opportunity = {
                    "pooled": _mix([(1.0 - start_probability, nonstart), (start_probability, pooled)], support),
                    "raw_slot": _mix([(1.0 - start_probability, nonstart)] + [(start_probability * slot_probability[slot], raw_slots[slot]) for slot in range(1, 10)], support),
                    "hierarchical_slot": _mix([(1.0 - start_probability, nonstart)] + [(start_probability * slot_probability[slot], hierarchical_slots[slot]) for slot in range(1, 10)], support),
                }
                actual_pa = int(actual["plate_appearances"]) if actual else 0
                actual_outcomes = _category_counts(actual) if actual else Counter()
                for arm, pmf in opportunity.items():
                    losses = _opportunity_losses(pmf, actual_pa)
                    state_arm = opportunity_states[arm]
                    state_arm["n"] += 1
                    for index, value in enumerate(losses):
                        state_arm["sums"][index] += value
                for name, other in (("hierarchical_minus_pooled", "pooled"), ("hierarchical_minus_raw_slot", "raw_slot")):
                    daily = opportunity_pairs[name][official_date]
                    daily[0] += 1
                    left = _opportunity_losses(opportunity["hierarchical_slot"], actual_pa)
                    right = _opportunity_losses(opportunity[other], actual_pa)
                    for index in range(3):
                        daily[index + 1] += left[index] - right[index]
                c0_markets = _market_record(c0, opportunity["hierarchical_slot"])
                league_markets = _market_record(league, opportunity["hierarchical_slot"])
                player_markets = _market_record(player, opportunity["hierarchical_slot"])
                for market, field in OBSERVED_FIELD.items():
                    observed = int(actual[field]) if actual else 0
                    c0_losses = _add_score(score_states["c0_hierarchical"][market], official_date, c0_markets[market]["pmf"], observed)
                    league_losses = _add_score(score_states["league_rate"][market], official_date, league_markets[market]["pmf"], observed)
                    player_losses = _add_score(score_states["player_rate"][market], official_date, player_markets[market]["pmf"], observed)
                    for name, baseline in (("c0_minus_league", league_losses), ("c0_minus_player", player_losses)):
                        daily = pair_daily[name][market][official_date]
                        daily[0] += 1
                        daily[1] += c0_losses[0] - baseline[0]
                        daily[2] += c0_losses[1] - baseline[1]
                eligible_rows += 1
                feature = {
                    "schema_version": "shared-pa-c0-pit-feature-row-v1", "candidate_id": CANDIDATE_ID,
                    "official_date": official_date, "game_pk": game_pk, "team_side": side, "team_id": team_id,
                    "player_id": player_id, "decision_cutoff": f"{official_date}T00:00:00Z",
                    "player_prior_pa": state["pa"], "team_player_prior_opportunities": state["opportunities"],
                    "start_probability": start_probability, "slot_probabilities": slot_probability,
                    "opportunity_pmf": opportunity["hierarchical_slot"], "per_pa_probability": c0,
                    "feature_schema_sha256": feature_schema_sha, "candidate_code_sha256": code_sha,
                    "feature_sha256": "",
                }
                feature["feature_sha256"] = _sha({key: value for key, value in feature.items() if key != "feature_sha256"})
                feature_rows.append(feature)
                if actual is not None:
                    prediction_rows.append({
                        "schema_version": "shared-pa-c0-historical-prediction-row-v1", "candidate_id": CANDIDATE_ID,
                        "official_date": official_date, "game_pk": game_pk, "team_side": side, "team_id": team_id,
                        "player_id": player_id, "observed_starter": bool(actual["starter"]), "observed_lineup_slot": actual["lineup_slot"],
                        "observed": {market: int(actual[field]) for market, field in OBSERVED_FIELD.items()},
                        "feature_sha256": feature["feature_sha256"], "feature_schema_sha256": feature_schema_sha,
                        "candidate_code_sha256": code_sha, "protocol_sha256": protocol_sha,
                        "candidate_markets": c0_markets,
                        "league_rate_baseline_markets": league_markets, "player_rate_baseline_markets": player_markets,
                        "opportunity_arms": opportunity,
                    })
                    if actual["starter"]:
                        represented_actual_starters += 1
                day_predictions.append((team_id, player_id, actual or {}, {"opportunity": opportunity, "actual_pa": actual_pa}))

        # Apply all final outcomes for this date only after its predictions are frozen.
        for (_, _, _, team_id), game_rows in day_games:
            actual_by_player = {int(row["player_id"]): row for row in game_rows}
            existing_pool = set(team_pool[team_id])
            for player_id in existing_pool:
                state = player_state[(team_id, player_id)]
                actual = actual_by_player.get(player_id)
                state["opportunities"] += 1
                opportunity_total += 1
                if actual and actual["starter"]:
                    state["starts"] += 1
                    state["slots"][int(actual["lineup_slot"])] += 1
                    opportunity_starts += 1
                else:
                    nonstart_pa_counts[int(actual["plate_appearances"]) if actual else 0] += 1
            for row in game_rows:
                player_id = int(row["player_id"])
                key = (team_id, player_id)
                if key not in player_state:
                    player_state[key] = {"pa": 0, "counts": Counter(), "opportunities": 0, "starts": 0, "slots": Counter()}
                    team_pool[team_id].add(player_id)
                state = player_state[key]
                outcome = _category_counts(row)
                state["counts"].update(outcome)
                state["pa"] += int(row["plate_appearances"])
                global_counts.update(outcome)
                global_pa += int(row["plate_appearances"])
                if row["starter"]:
                    slot = int(row["lineup_slot"])
                    global_slot_counts[slot] += 1
                    global_starters += 1
                    pooled_pa_counts[int(row["plate_appearances"])] += 1
                    slot_pa_counts[slot][int(row["plate_appearances"])] += 1
                    if player_id not in existing_pool:
                        state["opportunities"] += 1
                        opportunity_total += 1
                        state["starts"] += 1
                        state["slots"][slot] += 1
                        opportunity_starts += 1

    if not prediction_rows or not feature_rows:
        raise C0TournamentError("C0 tournament produced no eligible historical predictions")
    _write_jsonl(target / "pit_feature_panel.jsonl", feature_rows)
    _write_jsonl(target / "historical_prediction_rows.jsonl", prediction_rows)
    evaluation = {
        "schema_version": "shared-pa-c0-market-evaluation-v1", "candidate_id": CANDIDATE_ID,
        "research_only": True, "betting_authorized": False, "evaluation_scope": "previously_observed_exact_team_player_ids; implicit zero outcomes included in scores",
        "markets": {market: {model: _score_summary(score_states[model][market]) for model in score_states} for market in MARKET_INCREMENTS},
        "paired_clustered_uncertainty": {name: {market: _bootstrap_delta(daily, draws, seed) for market, daily in markets.items()} for name, markets in pair_daily.items()},
        "unavailable_required_comparators": ["frozen_baseline: authorized final-only source cannot replay frozen live/savant path", "market_implied_probability: economic evidence not opened"],
        "coverage": {"eligible_team_player_rows": eligible_rows, "archived_observed_player_rows": len(prediction_rows), "actual_original_starters_represented": represented_actual_starters, "actual_original_starters_total": total_actual_starters, "abstentions": dict(sorted(abstentions.items()))},
    }
    opportunity_report = _summarize_opportunity(opportunity_states, opportunity_pairs, draws, seed)
    final_fit = {
        "schema_version": "shared-pa-c0-final-fit-v1", "candidate_id": CANDIDATE_ID, "research_only": True,
        "source_release_sha256": protocol["source"]["outcome_release_sha256"], "protocol_sha256": protocol_sha,
        "candidate_code_sha256": code_sha, "feature_schema_sha256": feature_schema_sha,
        "global_counts": {name: global_counts[name] for name in OUTCOMES}, "global_pa": global_pa,
        "player_team_artifacts": [
            {"team_id": team_id, "player_id": player_id, "pa": state["pa"], "counts": {name: state["counts"][name] for name in OUTCOMES}, "opportunities": state["opportunities"], "starts": state["starts"], "slot_counts": dict(sorted(state["slots"].items()))}
            for (team_id, player_id), state in sorted(player_state.items())
        ],
        "parameter_inventory": {"learned": ["time-safe player outcome counts", "time-safe team-player start and slot counts", "time-safe PA counts"], "structural": ["13-category PA simplex", "exact convolution", "date-blocked chronology"], "locked": {"per_pa_prior_strength": strength, "start_prior_strength": start_strength, "slot_prior_strength": slot_strength, "hierarchical_slot_raw_weight": slot_weight}},
    }
    _write_json(target / "evaluation.json", evaluation)
    _write_json(target / "opportunity_arm_comparison.json", opportunity_report)
    _write_json(target / "c0_final_fit.json", final_fit)
    readme = "\n".join((
        "# Shared-PA C0 2023 research tournament", "",
        "This release contains retrospective, date-blocked development evidence only. It is not a prospective predictor, promotion, or betting artifact.", "",
        "- Source: immutable official 2023 outcome-label release.",
        "- Candidate: one empirical-Bayes 13-category per-PA simplex mixed with start/slot/PA opportunity marginals.",
        "- Strict cutoff: every target date uses only earlier official dates.",
        "- Limitation: final-only source lacks point-in-time roster receipts and team-constrained lineup scenarios; this C0 result cannot authorize a forward deployment.",
        "- Frozen and market comparators are intentionally reported unavailable rather than synthesized.", "",
    ))
    (target / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    files = [path for path in sorted(target.iterdir()) if path.name != "release_manifest.json"]
    unsigned = {
        "schema_version": SCHEMA_VERSION, "status": "COMPLETE_2023_C0_DEVELOPMENT_TOURNAMENT", "candidate_id": CANDIDATE_ID,
        "research_only": True, "betting_authorized": False, "protocol_sha256": protocol_sha,
        "outcome_release_sha256": protocol["source"]["outcome_release_sha256"], "capture_digest": protocol["source"]["capture_digest"],
        "candidate_code_sha256": code_sha, "feature_schema_sha256": feature_schema_sha,
        "protected_boundaries": protocol["protected_boundaries"],
        "files": [{"path": item.name, "size": item.stat().st_size, "sha256": sha256_file(item)} for item in files],
    }
    manifest = {**unsigned, "release_sha256": _sha(unsigned)}
    _write_json(target / "release_manifest.json", manifest)
    return manifest


def verify_tournament_release(path: Path, *, expected_release_sha256: str | None = None) -> dict[str, Any]:
    root = _safe_existing_directory(path, "C0 tournament release")
    manifest = _load_json(root / "release_manifest.json", "C0 tournament manifest")
    unsigned = dict(manifest)
    supplied = unsigned.pop("release_sha256", None)
    observed = _sha(unsigned)
    if supplied != observed or (expected_release_sha256 and observed != expected_release_sha256):
        raise C0TournamentError("C0 tournament manifest identity differs")
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("status") != "COMPLETE_2023_C0_DEVELOPMENT_TOURNAMENT":
        raise C0TournamentError("C0 tournament manifest schema differs")
    if not isinstance(manifest.get("candidate_code_sha256"), str) or not isinstance(manifest.get("feature_schema_sha256"), str):
        raise C0TournamentError("C0 code or feature-schema binding is missing")
    for item in manifest.get("files", []):
        file = root / str(item.get("path"))
        if not file.is_file() or file.is_symlink() or file.stat().st_size != item.get("size") or sha256_file(file) != item.get("sha256"):
            raise C0TournamentError("C0 tournament file binding differs")
    evaluation = _load_json(root / "evaluation.json", "C0 evaluation")
    if evaluation.get("candidate_id") != CANDIDATE_ID or evaluation.get("research_only") is not True:
        raise C0TournamentError("C0 evaluation boundary differs")
    for market, values in evaluation["markets"].items():
        if market not in MARKET_INCREMENTS or values["c0_hierarchical"]["scored_rows"] <= 0:
            raise C0TournamentError("C0 market evaluation is incomplete")
    rows_checked = 0
    for line in (root / "historical_prediction_rows.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("schema_version") != "shared-pa-c0-historical-prediction-row-v1" or row.get("candidate_id") != CANDIDATE_ID:
            raise C0TournamentError("C0 historical prediction identity differs")
        if row.get("candidate_code_sha256") != manifest["candidate_code_sha256"] or row.get("feature_schema_sha256") != manifest["feature_schema_sha256"]:
            raise C0TournamentError("C0 historical prediction code or schema binding differs")
        if not isinstance(row.get("official_date"), str) or not row["official_date"].startswith("2023-"):
            raise C0TournamentError("C0 historical prediction crosses chronology boundary")
        for source in ("candidate_markets", "league_rate_baseline_markets", "player_rate_baseline_markets"):
            markets = row.get(source)
            if not isinstance(markets, Mapping) or set(markets) != set(MARKET_INCREMENTS):
                raise C0TournamentError("C0 market route is incomplete")
            for market, record in markets.items():
                if record.get("market") != market or not isinstance(record.get("pmf"), list) or not record["pmf"]:
                    raise C0TournamentError("C0 market PMF is malformed")
                pmf = [float(value) for value in record["pmf"]]
                if any(value < 0.0 or not math.isfinite(value) for value in pmf) or not math.isclose(sum(pmf), 1.0, abs_tol=1e-10):
                    raise C0TournamentError("C0 market PMF does not reconcile")
                if not math.isclose(float(record.get("projected_value")), _mean(pmf), abs_tol=1e-12):
                    raise C0TournamentError("C0 projected value does not derive from its PMF")
                thresholds = record.get("threshold_probabilities")
                if not isinstance(thresholds, Mapping) or not math.isclose(float(thresholds.get("over_0_5")), 1.0 - pmf[0], abs_tol=1e-12):
                    raise C0TournamentError("C0 threshold probability does not derive from its PMF")
        rows_checked += 1
    return {"schema_version": "shared-pa-c0-tournament-verification-v1", "status": "C0_TOURNAMENT_RELEASE_VERIFIED", "release_sha256": observed, "historical_prediction_rows_checked": rows_checked, "research_only": True, "betting_authorized": False}
