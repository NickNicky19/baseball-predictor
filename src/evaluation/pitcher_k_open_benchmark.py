"""Exact, research-only open-2026 benchmark for pitcher strikeout probabilities."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.evaluation.pitcher_k_identity_funnel import (
    sha256,
    unwrap_official_identity_feed,
    validate_published_report,
)


SCHEMA = "pitcher-k-open-benchmark-protocol-v1"
STATUS = "LOCKED_AFTER_OUTCOME_BLIND_IDENTITY_BEFORE_FULL_OPEN_BENCHMARK"
MARKET_KEY = ["mlb_game_pk", "player_id", "game_date", "category", "line"]
PITCHER_GAME_KEY = ["mlb_game_pk", "player_id", "game_date"]
OPEN_MONTHS = {"2026-03", "2026-04", "2026-06"}
TERMINAL_STATES = {
    "started", "not_started", "player_missing_official", "pitching_stat_missing", "malformed"
}


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _bound(root: Path, relative_path: object) -> Path:
    path = (root / str(relative_path)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("bound input path escapes evidence root") from exc
    return path


def _verify_input(root: Path, record: dict[str, Any]) -> Path:
    path = _bound(root, record.get("path"))
    if not path.is_file() or sha256(path) != record.get("sha256"):
        raise ValueError(f"input hash mismatch: {record.get('path')}")
    return path


def validate_protocol(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA or payload.get("status") != STATUS:
        raise ValueError("unrecognized pitcher-K open benchmark protocol")
    if (
        payload.get("betting_authorized") is not False
        or payload.get("production_unchanged") is not True
        or payload.get("may_2026_opened") is not False
        or payload.get("economic_evidence_eligible") is not False
    ):
        raise ValueError("benchmark protocol changed protected scope")
    scope = payload.get("scope") or {}
    if scope != {
        "book": "draftkings", "category": "strikeouts", "sides": ["over", "under"],
        "lines": [4.5, 5.5, 6.5], "months": ["2026-03", "2026-04", "2026-06"],
        "forbidden_month": "2026-05", "historical_executability_verified": False,
        "settlement_truth": "official_mlb_only",
    }:
        raise ValueError("benchmark protocol scope changed")
    locked = payload.get("locked_denominators") or {}
    expected = {
        "identity_complete_paths": 1948, "identity_unique_market_keys": 1291,
        "production_strikeout_rows": 4260, "model_compatible_market_rows": 967,
        "model_compatible_pitcher_games": 766, "dates": 56,
        "market_rows_by_line": {"4.5": 437, "5.5": 350, "6.5": 180},
        "identity_rows_without_production_line_or_key": 324,
        "production_supported_line_identity_rows_without_model_key": 14,
    }
    if locked != expected:
        raise ValueError("benchmark denominator changed")
    outcome = payload.get("official_outcome_contract") or {}
    if (
        outcome.get("source") != "official MLB feed/live boxscore pitching statistics"
        or outcome.get("pitcher_game_key") != PITCHER_GAME_KEY
        or outcome.get("market_key") != MARKET_KEY
        or set(outcome.get("terminal_states") or []) != TERMINAL_STATES
        or len(outcome.get("terminal_states") or []) != len(TERMINAL_STATES)
        or outcome.get("primary_scoring_state") != "started"
        or outcome.get("required_games_started") != 1
        or outcome.get("vendor_result_columns_forbidden") != ["result", "won"]
        or outcome.get("no_silent_drop") is not True
        or outcome.get("reconstruction_crosscheck_required") is not True
    ):
        raise ValueError("benchmark official-outcome contract changed")
    metrics = payload.get("metrics") or {}
    if (
        metrics.get("primary") != ["brier", "log_loss", "calibration_10_bin", "auc"]
        or metrics.get("secondary") != ["mean_probability_bias", "entry_to_close_reference_movement"]
        or metrics.get("log_loss_probability_clip") != 0.000001
        or metrics.get("required_strata") != ["all", "line", "month"]
        or metrics.get("no_policy_selection") is not True
        or metrics.get("no_roi_or_payout_claim") is not True
    ):
        raise ValueError("benchmark metric contract changed")
    uncertainty = metrics.get("uncertainty") or {}
    if uncertainty != {
        "method": "date_cluster_bootstrap_percentile", "replicates": 10000,
        "seed": 20260719, "confidence_level": 0.95,
    }:
        raise ValueError("benchmark uncertainty contract changed")
    protected = payload.get("protected_invariants") or {}
    if len(protected) != 12 or not all(value is True for value in protected.values()):
        raise ValueError("benchmark protected invariant changed")


def load_protocol(path: str | Path, *, code_root: str | Path, evidence_root: str | Path) -> dict[str, Any]:
    payload = _json(Path(path))
    validate_protocol(payload)
    evidence_root = Path(evidence_root)
    for record in (payload.get("inputs") or {}).values():
        if not isinstance(record, dict) or "path" not in record:
            raise ValueError("benchmark input binding malformed")
        _verify_input(evidence_root, record)
    identity = payload["inputs"]["identity_report"]
    validate_published_report(
        _verify_input(evidence_root, identity), code_root=code_root, evidence_root=evidence_root
    )
    manifest = _json(_verify_input(evidence_root, payload["inputs"]["official_feed_manifest"]))
    source = payload["inputs"]["official_feed_manifest"]
    if manifest.get("status") != source["required_status"] or len(manifest.get("official_mlb_feeds") or []) != source["official_feed_count"]:
        raise ValueError("official-feed manifest status or inventory changed")
    dates = list(manifest.get("dates") or [])
    if len(dates) != 56 or any(str(date).startswith("2026-05") for date in dates):
        raise ValueError("official feed manifest opened May or changed date denominator")
    return payload


def _validate_terminal(frame: pd.DataFrame) -> pd.DataFrame:
    required = ["mlb_game_pk", "player_id", "category", "line", "slate_date", "terminal_state",
                "entry_over_odds", "entry_under_odds", "close_over_odds", "close_under_odds"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"identity terminal schema missing {missing}")
    if {"result", "won"}.intersection(frame.columns):
        raise ValueError("vendor outcome field entered benchmark identity terminal")
    frame = frame[frame.terminal_state.eq("mapped_unique")].copy()
    if len(frame) != 1291:
        raise ValueError("identity unique denominator changed")
    frame["category"] = frame.category.astype(str)
    frame["line"] = pd.to_numeric(frame.line, errors="raise")
    if frame[["mlb_game_pk", "player_id", "category", "line"]].isna().any().any() or frame.duplicated(["mlb_game_pk", "player_id", "category", "line"]).any():
        raise ValueError("identity terminal MARKET_KEY is null or duplicated")
    frame["game_date"] = pd.to_datetime(frame.slate_date, errors="coerce").dt.strftime("%Y-%m-%d")
    if frame.game_date.isna().any() or frame.game_date.str[:7].isin(["2026-05"]).any() or not set(frame.game_date.str[:7]).issubset(OPEN_MONTHS):
        raise ValueError("identity terminal date is invalid or includes May")
    odds = ["entry_over_odds", "entry_under_odds", "close_over_odds", "close_under_odds"]
    for column in odds:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if frame[odds].le(1.0).any().any() or frame[odds].isna().any().any():
        raise ValueError("two-sided decimal odds are invalid")
    return frame


def build_model_market_universe(protocol: dict[str, Any], *, evidence_root: Path) -> pd.DataFrame:
    terminal_path = _verify_input(evidence_root, protocol["inputs"]["identity_terminal"])
    production_path = _verify_input(evidence_root, protocol["inputs"]["production_probabilities"])
    terminal = _validate_terminal(pd.read_csv(terminal_path))
    terminal = terminal.rename(columns={"terminal_state": "identity_terminal_state"})
    production = pd.read_csv(production_path)
    required = [*MARKET_KEY, "sim_p_over"]
    if not set(required).issubset(production.columns):
        raise ValueError("production K probability schema changed")
    if {"result", "won", "actual_value"}.intersection(production.columns):
        raise ValueError("outcome field entered production probability artifact")
    production = production[production.category.astype(str).eq("strikeouts")].copy()
    production["line"] = pd.to_numeric(production.line, errors="raise")
    production["game_date"] = pd.to_datetime(production.game_date, errors="coerce").dt.strftime("%Y-%m-%d")
    if len(production) != 4260 or production[MARKET_KEY].isna().any().any() or production.duplicated(MARKET_KEY).any():
        raise ValueError("production K probability denominator/key changed")
    if not set(production.game_date.str[:7]).issubset(OPEN_MONTHS) or production.game_date.str.startswith("2026-05").any():
        raise ValueError("production K probability opened May")
    probability = pd.to_numeric(production.sim_p_over, errors="raise")
    if probability.isna().any() or probability.lt(0).any() or probability.gt(1).any():
        raise ValueError("production K probability outside [0,1]")
    production["sim_p_over"] = probability

    lines = protocol["scope"]["lines"]
    compatible_identity = terminal[terminal.line.isin(lines)].copy()
    merged = compatible_identity.merge(
        production[MARKET_KEY + ["sim_p_over"]], on=MARKET_KEY, how="left", validate="one_to_one", indicator=True
    )
    unmatched_supported = int(merged._merge.ne("both").sum())
    model_rows = merged[merged._merge.eq("both")].drop(columns="_merge").copy()
    if len(terminal) - len(model_rows) != protocol["locked_denominators"]["identity_rows_without_production_line_or_key"]:
        raise ValueError("identity-to-production unmatched denominator changed")
    if unmatched_supported != protocol["locked_denominators"]["production_supported_line_identity_rows_without_model_key"]:
        raise ValueError("production-supported identity gap changed")
    if len(model_rows) != 967 or model_rows.duplicated(MARKET_KEY).any():
        raise ValueError("model-compatible market denominator/key changed")
    if model_rows[PITCHER_GAME_KEY].drop_duplicates().shape[0] != 766 or model_rows.game_date.nunique() != 56:
        raise ValueError("model-compatible pitcher-game/date denominator changed")
    by_line = {str(float(line)): int(count) for line, count in model_rows.groupby("line").size().items()}
    if by_line != protocol["locked_denominators"]["market_rows_by_line"]:
        raise ValueError("model-compatible line denominator changed")
    model_rows["entry_reference_p_over"] = (1.0 / model_rows.entry_over_odds) / (
        (1.0 / model_rows.entry_over_odds) + (1.0 / model_rows.entry_under_odds)
    )
    model_rows["close_reference_p_over"] = (1.0 / model_rows.close_over_odds) / (
        (1.0 / model_rows.close_over_odds) + (1.0 / model_rows.close_under_odds)
    )
    model_rows["reference_probability_movement"] = model_rows.close_reference_p_over - model_rows.entry_reference_p_over
    return model_rows.sort_values(MARKET_KEY).reset_index(drop=True)


def _ip_to_outs(value: object) -> int:
    text = str(value)
    if "." not in text:
        innings, remainder = text, "0"
    else:
        innings, remainder = text.split(".", 1)
    if not innings.lstrip("-").isdigit() or remainder not in {"0", "1", "2"}:
        raise ValueError("invalid MLB inningsPitched representation")
    return int(innings) * 3 + int(remainder)


def build_official_pitcher_outcomes(protocol: dict[str, Any], market: pd.DataFrame, *, evidence_root: Path) -> pd.DataFrame:
    manifest_path = _verify_input(evidence_root, protocol["inputs"]["official_feed_manifest"])
    manifest = _json(manifest_path)
    records = {int(record["mlb_game_pk"]): record for record in manifest["official_mlb_feeds"]}
    rows: list[dict[str, Any]] = []
    for game in market[PITCHER_GAME_KEY].drop_duplicates().sort_values(PITCHER_GAME_KEY).itertuples(index=False):
        game_pk, player_id, game_date = int(game.mlb_game_pk), int(game.player_id), str(game.game_date)
        record = records.get(game_pk)
        if record is None:
            raise ValueError("model-compatible game missing from official feed manifest")
        feed_path = _bound(evidence_root, record["path"])
        if not feed_path.is_file() or sha256(feed_path) != record["sha256"]:
            raise ValueError("official feed hash drift")
        wrapper = _json(feed_path)
        payload, game_data = unwrap_official_identity_feed(wrapper, record, source=feed_path)
        official_date = str((game_data.get("datetime") or {}).get("officialDate", ""))
        if official_date != game_date or official_date.startswith("2026-05"):
            raise ValueError("official game date disagrees with hard market identity")
        teams = ((payload.get("liveData") or {}).get("boxscore") or {}).get("teams") or {}
        players = []
        for side in ("away", "home"):
            players.extend(list((teams.get(side, {}).get("players") or {}).values()))
        matches = [player for player in players if int((player.get("person") or {}).get("id", -1)) == player_id]
        row: dict[str, Any] = {
            "mlb_game_pk": game_pk, "player_id": player_id, "game_date": game_date,
            "official_feed_path": record["path"], "official_feed_sha256": record["sha256"],
            "terminal_state": None, "actual_strikeouts": pd.NA, "actual_outs": pd.NA,
            "actual_batters_faced": pd.NA, "actual_innings_pitched": pd.NA,
            "official_games_started": pd.NA,
        }
        if len(matches) != 1:
            row["terminal_state"] = "player_missing_official"
        else:
            pitching = ((matches[0].get("stats") or {}).get("pitching"))
            if not isinstance(pitching, dict):
                row["terminal_state"] = "pitching_stat_missing"
            else:
                try:
                    games_started = int(pitching.get("gamesStarted"))
                    strikeouts = int(pitching.get("strikeOuts"))
                    outs = int(pitching.get("outs"))
                    batters_faced = int(pitching.get("battersFaced"))
                    innings_outs = _ip_to_outs(pitching.get("inningsPitched"))
                    if min(games_started, strikeouts, outs, batters_faced, innings_outs) < 0 or outs != innings_outs:
                        raise ValueError("invalid official pitching accounting")
                    row.update({
                        "actual_strikeouts": strikeouts, "actual_outs": outs,
                        "actual_batters_faced": batters_faced,
                        "actual_innings_pitched": outs / 3.0,
                        "official_games_started": games_started,
                        "terminal_state": "started" if games_started == 1 else "not_started",
                    })
                except (TypeError, ValueError):
                    row["terminal_state"] = "malformed"
        rows.append(row)
    outcomes = pd.DataFrame(rows)
    if len(outcomes) != 766 or outcomes[PITCHER_GAME_KEY].isna().any().any() or outcomes.duplicated(PITCHER_GAME_KEY).any():
        raise ValueError("official pitcher-outcome denominator/key changed")
    if outcomes.terminal_state.isna().any() or not set(outcomes.terminal_state).issubset(TERMINAL_STATES):
        raise ValueError("official pitcher outcome lacks terminal state")
    started = outcomes.terminal_state.eq("started")
    required = ["actual_strikeouts", "actual_outs", "actual_batters_faced", "actual_innings_pitched", "official_games_started"]
    if outcomes.loc[started, required].isna().any().any():
        raise ValueError("official started pitcher outcome is incomplete")
    return outcomes.sort_values(PITCHER_GAME_KEY).reset_index(drop=True)


def crosscheck_reconstruction_outcomes(protocol: dict[str, Any], outcomes: pd.DataFrame, *, evidence_root: Path) -> None:
    path = _verify_input(evidence_root, protocol["inputs"]["reconstruction_official_outcomes_crosscheck"])
    reconstructed = pd.read_csv(path)
    required = [*PITCHER_GAME_KEY, "category", "actual_value"]
    if not set(required).issubset(reconstructed.columns):
        raise ValueError("immutable reconstruction outcome schema changed")
    reconstructed = reconstructed[reconstructed.category.astype(str).eq("strikeouts")].copy()
    if reconstructed[PITCHER_GAME_KEY].isna().any().any() or reconstructed.duplicated(PITCHER_GAME_KEY).any():
        raise ValueError("immutable reconstruction K outcomes are null or duplicated")
    started = outcomes[outcomes.terminal_state.eq("started")].copy()
    joined = started.merge(
        reconstructed[PITCHER_GAME_KEY + ["actual_value"]], on=PITCHER_GAME_KEY, how="left", validate="one_to_one"
    )
    if joined.actual_value.isna().any() or not np.allclose(
        pd.to_numeric(joined.actual_strikeouts), pd.to_numeric(joined.actual_value)
    ):
        raise ValueError("official pitcher K outcomes disagree with immutable reconstruction outcomes")


def fit_empirical_baseline(protocol: dict[str, Any], *, evidence_root: Path) -> dict[int, float]:
    path = _verify_input(evidence_root, protocol["inputs"]["pre2026_pitcher_training"])
    training = pd.read_csv(path, usecols=["season", "out_ip", "out_k"])
    source = protocol["inputs"]["pre2026_pitcher_training"]
    if set(training.season.unique()) != set(source["seasons"]):
        raise ValueError("pre-2026 pitcher seasons changed")
    values = pd.to_numeric(training.loc[pd.to_numeric(training.out_ip, errors="raise").gt(0), "out_k"], errors="raise")
    if len(values) != source["expected_rows"] or values.isna().any() or values.lt(0).any() or not np.equal(values, np.floor(values)).all():
        raise ValueError("pre-2026 empirical baseline rows changed")
    counts = Counter(values.astype(int).tolist())
    total = sum(counts.values())
    return {value: count / total for value, count in sorted(counts.items())}


def add_comparators(market: pd.DataFrame, outcomes: pd.DataFrame, pmf: dict[int, float]) -> pd.DataFrame:
    joined = market.merge(outcomes, on=PITCHER_GAME_KEY, how="left", validate="many_to_one")
    if len(joined) != len(market) or joined.terminal_state.isna().any():
        raise ValueError("market row silently lost official outcome terminal state")
    tail = {line: sum(prob for value, prob in pmf.items() if value > line) for line in joined.line.unique()}
    joined["empirical_p_over"] = joined.line.map(tail)
    started = joined.terminal_state.eq("started")
    joined["actual_over"] = pd.Series(pd.NA, index=joined.index, dtype="Int64")
    joined.loc[started, "actual_over"] = (
        pd.to_numeric(joined.loc[started, "actual_strikeouts"], errors="raise") > joined.loc[started, "line"]
    ).astype(int)
    return joined.sort_values(MARKET_KEY).reset_index(drop=True)


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    positives = int(y.sum())
    negatives = int(len(y) - positives)
    if positives == 0 or negatives == 0:
        return None
    ranks = pd.Series(p).rank(method="average").to_numpy(float)
    return float((ranks[y.astype(bool)].sum() - positives * (positives + 1) / 2.0) / (positives * negatives))


def _scores(frame: pd.DataFrame, probability_column: str, clip: float) -> dict[str, float | int | None]:
    y = pd.to_numeric(frame.actual_over, errors="raise").to_numpy(float)
    p = pd.to_numeric(frame[probability_column], errors="raise").to_numpy(float)
    if len(y) == 0 or np.isnan(y).any() or np.isnan(p).any() or (p < 0).any() or (p > 1).any():
        raise ValueError("score input is empty or outside probability bounds")
    bounded = np.clip(p, clip, 1.0 - clip)
    return {
        "n": int(len(y)), "brier": float(np.mean((p - y) ** 2)),
        "log_loss": float(-np.mean(y * np.log(bounded) + (1.0 - y) * np.log(1.0 - bounded))),
        "mean_probability_bias": float(np.mean(p - y)), "auc": _auc(y.astype(int), p),
    }


def calibration_rows(frame: pd.DataFrame, probability_column: str) -> list[dict[str, Any]]:
    bins = pd.cut(frame[probability_column], bins=np.linspace(0.0, 1.0, 11), include_lowest=True, labels=False)
    rows: list[dict[str, Any]] = []
    for index in range(10):
        group = frame[bins.eq(index)]
        if group.empty:
            continue
        rows.append({
            "comparator": probability_column, "bin": int(index), "n": int(len(group)),
            "mean_probability": float(group[probability_column].mean()),
            "actual_rate": float(pd.to_numeric(group.actual_over).mean()),
        })
    return rows


def bootstrap_differences(frame: pd.DataFrame, *, baseline: str, candidate: str, clip: float, replicates: int, seed: int) -> dict[str, float | int]:
    dates = sorted(frame.game_date.unique().tolist())
    if len(dates) < 2:
        raise ValueError("need at least two dates for clustered uncertainty")
    by_date = {date: group for date, group in frame.groupby("game_date", sort=True)}
    rng = np.random.default_rng(seed)
    brier, log_loss = [], []
    for _ in range(replicates):
        sample = pd.concat([by_date[date] for date in rng.choice(dates, size=len(dates), replace=True)], ignore_index=True)
        candidate_scores = _scores(sample, candidate, clip)
        baseline_scores = _scores(sample, baseline, clip)
        brier.append(float(baseline_scores["brier"]) - float(candidate_scores["brier"]))
        log_loss.append(float(baseline_scores["log_loss"]) - float(candidate_scores["log_loss"]))
    return {
        "baseline": baseline, "candidate": candidate, "dates": len(dates), "replicates": replicates,
        "brier_improvement_p2_5": float(np.quantile(brier, 0.025)),
        "brier_improvement_p50": float(np.quantile(brier, 0.5)),
        "brier_improvement_p97_5": float(np.quantile(brier, 0.975)),
        "log_loss_improvement_p2_5": float(np.quantile(log_loss, 0.025)),
        "log_loss_improvement_p50": float(np.quantile(log_loss, 0.5)),
        "log_loss_improvement_p97_5": float(np.quantile(log_loss, 0.975)),
    }


def evaluate(protocol: dict[str, Any], market_with_outcomes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    started = market_with_outcomes[market_with_outcomes.terminal_state.eq("started")].copy()
    if started.empty:
        raise ValueError("no official started pitcher rows for primary scoring")
    metrics = protocol["metrics"]
    clip = float(metrics["log_loss_probability_clip"])
    comparators = ["sim_p_over", "empirical_p_over", "entry_reference_p_over"]
    rows: list[dict[str, Any]] = []
    for scope, group in [("all", started), *[(f"line:{line}", value) for line, value in started.groupby("line")], *[(f"month:{month}", value) for month, value in started.groupby(started.game_date.str[:7])]]:
        for column in comparators:
            rows.append({"scope": scope, "comparator": column, **_scores(group, column, clip)})
    calibration: list[dict[str, Any]] = []
    for column in comparators:
        calibration.extend(calibration_rows(started, column))
    uncertainty = metrics["uncertainty"]
    diffs = [
        bootstrap_differences(started, baseline="empirical_p_over", candidate="sim_p_over", clip=clip, replicates=int(uncertainty["replicates"]), seed=int(uncertainty["seed"])),
        bootstrap_differences(started, baseline="entry_reference_p_over", candidate="sim_p_over", clip=clip, replicates=int(uncertainty["replicates"]), seed=int(uncertainty["seed"]) + 1),
    ]
    return pd.DataFrame(rows), pd.DataFrame(calibration), diffs
