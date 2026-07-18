#!/usr/bin/env python3
"""Hard-keyed total-bases candidate gate (research only; never promotes).

This is deliberately separate from the normal simulator/GBM and market A/B
tools. Total bases is a new model-output category, and it must earn its own
evidence rather than disappearing into an aggregate score.

The runner accepts only:
  * a provenance-forked total-bases model artifact from
    ``run_gate_reconstruct.py --include-total-bases-candidate``;
  * official game-keyed outcomes, not vendor results;
  * a hard-crosswalk market artifact at the actual eligible entry/close
    universe; and
  * a predeclared selected-side file.  It never searches edge cutoffs or
    decides what to bet after seeing close movement.

It reports per-line model-vs-close Brier evidence and capture on the supplied
selection policy. A result is always RESEARCH ONLY: this script contains no
promotion path and does not change the live total-bases status.

Market CSV contract (one exact book, prior to this scorer):
  ``mlb_game_pk, player_id, game_date, category, line, book, entry_p_over,
  close_p_over, entry_quote_at_utc, close_quote_at_utc,
  official_start_time_utc, void_status``.
Its JSON manifest must hash the consumer crosswalk, the accepted date-universe
file and list, the exact eligibility definition, and this market CSV itself.

The selected-side CSV is a frozen subset keyed by MODEL_KEY and carries one
``policy_id`` and ``policy_sha256``.  Its separate manifest must hash the
selection CSV and policy source and repeat the crosswalk/date/eligibility
hashes.  These are deliberately strict handoffs: a coverage or identity
change is a new evaluation universe, not a row to silently discard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.identity_keys import MARKET_KEY, MODEL_KEY, OUTCOME_KEY, require_unique
from src.models.total_bases_contract import require_supported_total_bases_line


MARKET_COLUMNS = {
    *MARKET_KEY,
    "game_date",
    "book",
    "entry_p_over",
    "close_p_over",
    "entry_quote_at_utc",
    "close_quote_at_utc",
    "official_start_time_utc",
    "void_status",
}
SELECTION_COLUMNS = {*MODEL_KEY, "side", "policy_id", "policy_sha256"}
_SHA256_HEX_LENGTH = 64


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(message: str) -> None:
    print(f"FATAL: {message}", file=sys.stderr)
    raise SystemExit(2)


def _require_sha256(value: object, label: str) -> str:
    digest = str(value).strip().lower()
    if len(digest) != _SHA256_HEX_LENGTH or any(char not in "0123456789abcdef" for char in digest):
        _fail(f"{label} must be a 64-character SHA-256 hex digest")
    return digest


def _read_csv(path: str, label: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        _fail(f"{label} does not exist: {target}")
    try:
        return pd.read_csv(target)
    except Exception as exc:
        _fail(f"cannot read {label} {target}: {exc}")
    raise AssertionError("unreachable")


def _require_columns(frame: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        _fail(f"{label} is missing required columns {missing}")


def _strict_total_bases(frame: pd.DataFrame, label: str, *, require_lines: bool = True) -> None:
    if frame.empty:
        _fail(f"{label} is empty")
    categories = set(frame["category"].astype(str))
    if categories != {"total_bases"}:
        _fail(f"{label} must contain only category='total_bases', got {sorted(categories)}")
    if not require_lines:
        return
    for line in sorted(pd.to_numeric(frame["line"], errors="coerce").dropna().unique()):
        try:
            require_supported_total_bases_line(float(line))
        except ValueError as exc:
            _fail(f"{label}: {exc}")


def _probabilities(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or (~np.isfinite(values)).any() or ((values < 0) | (values > 1)).any():
            _fail(f"{label}.{column} must be finite probabilities in [0,1]")
        frame[column] = values.astype(float)


def _normalise_game_dates(frame: pd.DataFrame, label: str) -> None:
    raw = frame["game_date"].astype(str)
    try:
        parsed = pd.to_datetime(raw, format="%Y-%m-%d", errors="raise")
    except Exception as exc:
        _fail(f"{label}.game_date must be YYYY-MM-DD: {exc}")
    normalised = parsed.dt.strftime("%Y-%m-%d")
    if not raw.eq(normalised).all():
        _fail(f"{label}.game_date must be canonical YYYY-MM-DD strings")
    frame["game_date"] = normalised


def _normalise_manifest_dates(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        _fail(f"{label} must be a non-empty list of YYYY-MM-DD dates")
    frame = pd.DataFrame({"game_date": value})
    _normalise_game_dates(frame, label)
    dates = frame["game_date"].tolist()
    if len(dates) != len(set(dates)):
        _fail(f"{label} contains duplicate dates")
    return sorted(dates)


def _validate_market_times(frame: pd.DataFrame) -> None:
    try:
        entry = pd.to_datetime(frame["entry_quote_at_utc"], utc=True, errors="raise")
        close = pd.to_datetime(frame["close_quote_at_utc"], utc=True, errors="raise")
        start = pd.to_datetime(frame["official_start_time_utc"], utc=True, errors="raise")
    except Exception as exc:
        _fail(f"market timestamps must be explicit ISO UTC values: {exc}")
    bad = (entry >= start) | (close >= start) | (entry > close)
    if bad.any():
        sample = frame.loc[bad, [*MARKET_KEY, "entry_quote_at_utc", "close_quote_at_utc", "official_start_time_utc"]].head(10)
        _fail(
            "market quote timing is impossible (entry <= close < official start required):\n"
            + sample.to_string(index=False)
        )


def _date_bootstrap_ratio(
    numerator: np.ndarray,
    denominator: np.ndarray,
    dates: np.ndarray,
    b: int,
    seed: int,
) -> tuple[float, float]:
    """Block-bootstrap ratio of mean CLV to mean claimed edge by slate date."""
    unique, inverse = np.unique(dates, return_inverse=True)
    if len(unique) < 2:
        _fail("capture uncertainty needs at least two distinct game dates")
    sums_num = np.zeros(len(unique))
    sums_den = np.zeros(len(unique))
    counts = np.zeros(len(unique))
    np.add.at(sums_num, inverse, numerator)
    np.add.at(sums_den, inverse, denominator)
    np.add.at(counts, inverse, 1.0)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(unique), size=(b, len(unique)))
    mean_num = sums_num[picks].sum(axis=1) / counts[picks].sum(axis=1)
    mean_den = sums_den[picks].sum(axis=1) / counts[picks].sum(axis=1)
    if (mean_den <= 0).any():
        _fail("bootstrap encountered non-positive mean claimed edge; selection contract is invalid")
    ratios = mean_num / mean_den
    return float(np.percentile(ratios, 2.5)), float(np.percentile(ratios, 97.5))


def _date_bootstrap_diff(diff: np.ndarray, dates: np.ndarray, b: int, seed: int) -> tuple[float, float]:
    unique, inverse = np.unique(dates, return_inverse=True)
    if len(unique) < 2:
        _fail("Brier uncertainty needs at least two distinct game dates")
    sums = np.zeros(len(unique))
    counts = np.zeros(len(unique))
    np.add.at(sums, inverse, diff)
    np.add.at(counts, inverse, 1.0)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(unique), size=(b, len(unique)))
    reps = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    return float(np.percentile(reps, 2.5)), float(np.percentile(reps, 97.5))


def _read_market_manifest(path: str) -> dict:
    target = Path(path)
    if not target.exists():
        _fail(f"market manifest does not exist: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        _fail(f"cannot read market manifest: {exc}")
    required = {
        "crosswalk_consumer_sha256",
        "date_universe_sha256",
        "date_universe_dates",
        "eligibility_definition",
        "eligibility_definition_sha256",
        "market_artifact_sha256",
    }
    if not isinstance(payload, dict) or not required <= set(payload):
        _fail(f"market manifest must include {sorted(required)}")
    if not str(payload["eligibility_definition"]).strip():
        _fail("market manifest has a blank eligibility_definition")
    for field in (
        "crosswalk_consumer_sha256",
        "date_universe_sha256",
        "eligibility_definition_sha256",
        "market_artifact_sha256",
    ):
        _require_sha256(payload[field], f"market manifest.{field}")
    definition_hash = hashlib.sha256(
        str(payload["eligibility_definition"]).encode("utf-8")
    ).hexdigest()
    if definition_hash != str(payload["eligibility_definition_sha256"]).lower():
        _fail("market manifest eligibility_definition_sha256 does not match its definition")
    _normalise_manifest_dates(payload["date_universe_dates"], "market manifest.date_universe_dates")
    return payload


def _read_selection_manifest(path: str) -> dict:
    target = Path(path)
    if not target.exists():
        _fail(f"selection manifest does not exist: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        _fail(f"cannot read selection manifest: {exc}")
    required = {
        "selection_artifact_sha256",
        "policy_id",
        "policy_sha256",
        "policy_source_sha256",
        "date_universe_sha256",
        "crosswalk_consumer_sha256",
        "eligibility_definition_sha256",
    }
    if not isinstance(payload, dict) or not required <= set(payload):
        _fail(f"selection manifest must include {sorted(required)}")
    if not str(payload["policy_id"]).strip():
        _fail("selection manifest has blank policy_id")
    for field in required - {"policy_id"}:
        _require_sha256(payload[field], f"selection manifest.{field}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hard-keyed total-bases candidate gate (research only)")
    parser.add_argument("--model", required=True, help="candidate simulator probability CSV")
    parser.add_argument("--model-manifest", required=True, help="sidecar manifest from candidate reconstruction")
    parser.add_argument("--market", required=True, help="hard-crosswalk market CSV at the eligible entry/close universe")
    parser.add_argument("--market-manifest", required=True, help="market artifact provenance JSON")
    parser.add_argument("--outcomes", required=True, help="official game-keyed outcome CSV")
    parser.add_argument("--selection", required=True, help="predeclared selected-side CSV; no threshold search occurs here")
    parser.add_argument("--selection-manifest", required=True,
                        help="hash/provenance sidecar for the frozen selected-side CSV")
    parser.add_argument("--book", required=True, help="one exact sportsbook key; markets are never pooled")
    parser.add_argument("--capture-bar", required=True, type=float,
                        help="predeclared capture-ratio bar, e.g. 0.10; recorded, never tuned here")
    parser.add_argument("--b", type=int, default=4000, help="date-block bootstrap repetitions")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", required=True, help="research metrics CSV")
    args = parser.parse_args(argv)

    if not np.isfinite(args.capture_bar) or args.capture_bar < 0:
        _fail("capture bar must be finite and non-negative")
    if args.b <= 0:
        _fail("--b must be positive")
    model_manifest_path = Path(args.model_manifest)
    try:
        model_manifest = json.loads(model_manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _fail(f"cannot read model manifest: {exc}")
    model_required = {
        "total_bases_candidate", "dates", "date_source", "config_sha256", "model_version",
        "probability_artifact_sha256", "outcome_artifact_sha256", "fail_on_flags",
        "require_statcast_profiles", "simulation_random_seed",
    }
    if not isinstance(model_manifest, dict) or not model_required <= set(model_manifest):
        _fail(f"model manifest must include {sorted(model_required)}")
    if model_manifest.get("total_bases_candidate") is not True:
        _fail("model manifest is not an explicit total-bases candidate artifact")
    if model_manifest.get("fail_on_flags") is not True:
        _fail("candidate model did not require complete reconstruction flags to fail")
    if model_manifest.get("require_statcast_profiles") is not True:
        _fail("candidate model did not require historical Statcast profiles")
    if model_manifest.get("simulation_random_seed") is None:
        _fail("candidate model did not declare an explicit simulation random seed")
    for field in ("config_sha256", "probability_artifact_sha256", "outcome_artifact_sha256"):
        _require_sha256(model_manifest[field], f"model manifest.{field}")
    if not str(model_manifest["model_version"]).strip():
        _fail("model manifest has blank model_version")
    model_dates = _normalise_manifest_dates(model_manifest["dates"], "model manifest.dates")
    date_source = model_manifest["date_source"]
    if not isinstance(date_source, dict) or date_source.get("kind") != "dates_file":
        _fail("candidate model must use a hashed --dates-file date universe, not ad-hoc CLI dates")
    model_date_universe_sha = _require_sha256(
        date_source.get("sha256"), "model manifest.date_source.sha256"
    )
    market_manifest = _read_market_manifest(args.market_manifest)
    selection_manifest = _read_selection_manifest(args.selection_manifest)

    model = _read_csv(args.model, "model artifact")
    market = _read_csv(args.market, "market artifact")
    outcomes = _read_csv(args.outcomes, "official outcomes")
    selection = _read_csv(args.selection, "selection policy")
    if _sha256_path(Path(args.model)) != model_manifest["probability_artifact_sha256"].lower():
        _fail("model artifact hash does not match model manifest")
    if _sha256_path(Path(args.outcomes)) != model_manifest["outcome_artifact_sha256"].lower():
        _fail("official outcomes hash does not match model manifest")
    if _sha256_path(Path(args.market)) != market_manifest["market_artifact_sha256"].lower():
        _fail("market artifact hash does not match market manifest")
    if _sha256_path(Path(args.selection)) != selection_manifest["selection_artifact_sha256"].lower():
        _fail("selection artifact hash does not match selection manifest")
    market_dates = _normalise_manifest_dates(
        market_manifest["date_universe_dates"], "market manifest.date_universe_dates"
    )
    if market_dates != model_dates:
        _fail("market and candidate model manifests name different date universes")
    if model_date_universe_sha != market_manifest["date_universe_sha256"].lower():
        _fail("model and market manifests name different date-universe artifacts")
    for field in ("date_universe_sha256", "crosswalk_consumer_sha256", "eligibility_definition_sha256"):
        if selection_manifest[field].lower() != market_manifest[field].lower():
            _fail(f"selection and market manifests disagree on {field}")
    _require_columns(model, {*MODEL_KEY, "game_date", "sim_p_over"}, "model artifact")
    _require_columns(market, MARKET_COLUMNS, "market artifact")
    _require_columns(outcomes, {*OUTCOME_KEY, "game_date", "actual_value"}, "official outcomes")
    _require_columns(selection, SELECTION_COLUMNS, "selection policy")
    for frame, label in (
        (model, "model artifact"),
        (market, "market artifact"),
        (outcomes, "official outcomes"),
    ):
        _normalise_game_dates(frame, label)
    _strict_total_bases(model, "model artifact")
    _strict_total_bases(market, "market artifact")
    _strict_total_bases(outcomes, "official outcomes", require_lines=False)
    _strict_total_bases(selection, "selection policy")
    _probabilities(model, ["sim_p_over"], "model artifact")
    _probabilities(market, ["entry_p_over", "close_p_over"], "market artifact")
    require_unique(model, MODEL_KEY, "total-bases model artifact")
    require_unique(outcomes, OUTCOME_KEY, "total-bases official outcomes")
    if set(model["game_date"]) != set(model_dates):
        _fail("model artifact dates do not exactly match its manifest date universe")
    if not set(outcomes["game_date"]).issubset(set(model_dates)):
        _fail("official outcomes contain dates outside the candidate model universe")

    market["book"] = market["book"].astype(str).str.lower()
    book = args.book.strip().lower()
    market = market[market["book"] == book].copy()
    if market.empty:
        _fail(f"market artifact has no rows for book={book!r}")
    market_key = [*MARKET_KEY, "book"]
    require_unique(market, market_key, f"total-bases market artifact ({book})")
    if not set(market["game_date"]).issubset(set(model_dates)):
        _fail("market artifact contains dates outside the candidate model universe")
    selection["side"] = selection["side"].astype(str).str.lower().str.strip()
    if not set(selection["side"]) <= {"over", "under"}:
        _fail("selection.side must be over or under")
    if selection.empty:
        _fail("selection policy has no rows; capture is undefined")
    require_unique(selection, MODEL_KEY, "total-bases predeclared selection")
    if selection["policy_id"].astype(str).nunique() != 1 or selection["policy_sha256"].astype(str).nunique() != 1:
        _fail("selection policy mixes policy ids/hashes; one evaluation needs one predeclared policy")
    if str(selection["policy_id"].iloc[0]).strip() != str(selection_manifest["policy_id"]).strip():
        _fail("selection policy_id does not match its manifest")
    if _require_sha256(selection["policy_sha256"].iloc[0], "selection.policy_sha256") != selection_manifest["policy_sha256"].lower():
        _fail("selection policy_sha256 does not match its manifest")

    market["void_status"] = market["void_status"].astype(str).str.lower().str.strip()
    if not set(market["void_status"]) <= {"graded", "void", "unscored"}:
        _fail("market void_status must be graded, void, or unscored")
    scored_market = market[market["void_status"] == "graded"].copy()
    excluded = len(market) - len(scored_market)
    if scored_market.empty:
        _fail("all market rows are void/unscored; no evaluable total-bases universe")
    _validate_market_times(scored_market)
    model_market_dates = model.merge(
        scored_market[[*MODEL_KEY, "game_date"]], on=MODEL_KEY, how="inner", validate="one_to_one",
        suffixes=("_model", "_market"),
    )
    if not model_market_dates["game_date_model"].eq(model_market_dates["game_date_market"]).all():
        _fail("model and market disagree on game_date for the same hard key")
    paired = model.merge(scored_market, on=MODEL_KEY + ["game_date"], how="inner", validate="one_to_one")
    paired = paired.merge(
        outcomes[OUTCOME_KEY + ["game_date", "actual_value"]], on=OUTCOME_KEY, how="inner", validate="many_to_one",
        suffixes=("", "_outcome"),
    )
    if not paired["game_date"].eq(paired["game_date_outcome"]).all():
        _fail("model and official outcomes disagree on game_date for the same hard key")
    paired = paired.drop(columns=["game_date_outcome"])
    if paired.empty:
        _fail("no hard-keyed model/market/outcome rows matched")
    actual = pd.to_numeric(paired["actual_value"], errors="coerce")
    if actual.isna().any() or (actual < 0).any() or (~np.isclose(actual, np.round(actual))).any():
        _fail("official total-bases actuals must be non-negative integers")
    paired["actual_value"] = actual.astype(float)
    paired["over_outcome"] = (paired["actual_value"] > paired["line"]).astype(float)

    # Selection is frozen outside this scorer. An unmatched selected key means
    # the policy changed universe or a hard identity disappeared; both are data
    # defects, not rows to quietly drop.
    picked = selection.merge(paired, on=MODEL_KEY, how="left", validate="one_to_one", indicator=True)
    if (picked["_merge"] != "both").any():
        sample = picked.loc[picked["_merge"] != "both", MODEL_KEY].head(10)
        _fail("predeclared selection contains keys outside the eligible hard-key universe:\n" + sample.to_string(index=False))
    picked = picked.drop(columns=["_merge"])
    picked["claimed_edge"] = np.where(
        picked["side"] == "over",
        picked["sim_p_over"] - picked["entry_p_over"],
        picked["entry_p_over"] - picked["sim_p_over"],
    )
    if (picked["claimed_edge"] <= 0).any():
        sample = picked.loc[picked["claimed_edge"] <= 0, [*MODEL_KEY, "side", "claimed_edge"]].head(10)
        _fail("selection contains a non-positive claimed edge; it was not predeclared coherently:\n" + sample.to_string(index=False))
    picked["realized_clv"] = np.where(
        picked["side"] == "over",
        picked["close_p_over"] - picked["entry_p_over"],
        picked["entry_p_over"] - picked["close_p_over"],
    )

    rows: list[dict] = [
        {
            "row_type": "hard_universe_coverage",
            "category": "total_bases",
            "book": book,
            "n_model_rows": len(model),
            "n_graded_market_rows": len(scored_market),
            "n_official_outcome_rows": len(outcomes),
            "n_model_market_outcome_rows": len(paired),
            "model_row_coverage_after_hard_market_and_official_join": len(paired) / len(model),
            "definition": "denominator=model candidate rows on the certified date universe; numerator=rows with one hard-keyed graded market quote and one official outcome",
        }
    ]
    for line, group in paired.groupby("line", sort=True):
        y = group["over_outcome"].to_numpy(float)
        model_p = group["sim_p_over"].to_numpy(float)
        close_p = group["close_p_over"].to_numpy(float)
        brier_model = float(np.mean((model_p - y) ** 2))
        brier_close = float(np.mean((close_p - y) ** 2))
        base = float(y.mean())
        brier_constant = float(np.mean((base - y) ** 2))
        if brier_close >= brier_constant:
            _fail(
                f"close market fails the constant-predictor sanity control at line {line}: "
                f"{brier_close:.6f} >= {brier_constant:.6f}"
            )
        diff = (model_p - y) ** 2 - (close_p - y) ** 2
        ci_lo, ci_hi = _date_bootstrap_diff(diff, group["game_date"].to_numpy(), args.b, args.seed)
        rows.append(
            {
                "row_type": "line_brier",
                "category": "total_bases",
                "book": book,
                "line": float(line),
                "n_rows": len(group),
                "n_dates": int(group["game_date"].nunique()),
                "base_rate": base,
                "brier_model": brier_model,
                "brier_close": brier_close,
                "brier_constant": brier_constant,
                "delta_brier_model_minus_close": float(diff.mean()),
                "delta_brier_ci_lo": ci_lo,
                "delta_brier_ci_hi": ci_hi,
            }
        )

    capture = float(picked["realized_clv"].mean() / picked["claimed_edge"].mean())
    cap_lo, cap_hi = _date_bootstrap_ratio(
        picked["realized_clv"].to_numpy(float),
        picked["claimed_edge"].to_numpy(float),
        picked["game_date"].to_numpy(),
        args.b,
        args.seed,
    )
    rows.append(
        {
            "row_type": "capture",
            "category": "total_bases",
            "book": book,
            "line": np.nan,
            "n_rows": len(picked),
            "n_dates": int(picked["game_date"].nunique()),
            "policy_id": str(selection["policy_id"].iloc[0]),
            "policy_sha256": str(selection["policy_sha256"].iloc[0]),
            "mean_claimed_edge": float(picked["claimed_edge"].mean()),
            "mean_realized_clv": float(picked["realized_clv"].mean()),
            "capture_ratio": capture,
            "capture_ci_lo": cap_lo,
            "capture_ci_hi": cap_hi,
            "predeclared_capture_bar": float(args.capture_bar),
            "market_void_or_unscored_rows_excluded": excluded,
            "model_manifest_sha256": _sha256_path(model_manifest_path),
            "market_manifest_sha256": _sha256_path(Path(args.market_manifest)),
            "market_crosswalk_consumer_sha256": market_manifest["crosswalk_consumer_sha256"],
            "market_date_universe_sha256": market_manifest["date_universe_sha256"],
            "selection_manifest_sha256": _sha256_path(Path(args.selection_manifest)),
            "selection_policy_source_sha256": selection_manifest["policy_source_sha256"],
            "verdict": "RESEARCH_ONLY_NOT_PROMOTED",
        }
    )
    output = pd.DataFrame(rows)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    output.to_csv(tmp, index=False)
    if not tmp.exists() or tmp.stat().st_size == 0:
        _fail(f"failed to write metrics artifact {tmp}")
    tmp.replace(target)

    print(f"hard-keyed eligible rows: {len(paired):,}; void/unscored market rows excluded: {excluded:,}")
    print(f"predeclared selected rows: {len(picked):,} on {picked.game_date.nunique()} dates")
    print(f"capture ratio: {capture:+.5f}  95% date-block CI [{cap_lo:+.5f}, {cap_hi:+.5f}]")
    print(f"predeclared bar: {args.capture_bar:+.5f}; verdict: RESEARCH ONLY -- NOT PROMOTED")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
