#!/usr/bin/env python3
"""
Gate #4(b) — simulator side. Emit sim_probs.csv on the SAME rows the GBM was
scored on, so run_calibration_gate.py compare can inner-join and judge
"equal-or-better calibration than the simulator" on matched keys.

WHY A RECONSTRUCTION (settled in the B2 chat): the sim cache stores point
projections only. The simulator's P(over) is MonteCarloResult.p_ge_threshold,
which only exists on freshly-reconstructed projections. So we replay a
SAMPLE of the 2024+ walk-forward dates through reconstruct_objects() (the
leakage-safe A2/B1 seam, additive — no live-model change) and read
p_ge_threshold off each projection.

SCOPE — ALL FOUR GATED CATEGORIES (B3 update). Hitters (hits, hrr,
home_runs) as before, PLUS pitcher strikeouts now that B3 populates
PropProjection.simulation with an analytic K distribution (Poisson/NB pinned
to the live point estimate — additive, no model_version fork). K lines
4.5/5.5/6.5 per the B3 KICKOFF.

NOTE ON K: the K simulation is ANALYTIC (n_sims=0, per_game_samples=None),
so only the p_ge_threshold path can serve it — there is no samples fallback.
Its keys follow the same integer-threshold convention as the hitter Monte
Carlo (float(ceil(line)): 4.5 -> 5.0), so _p_over_from_projection works
unchanged. If a K key is missing the row is dropped and flagged, never
fabricated — same policy as hitters.

DATE SAMPLING is still weighted by HITTER row counts on purpose: with the
same --seed it reproduces the SAME date sample as the closed hitter-only gate
run, so one reconstruction pass covers all four categories on a comparable
sample (and K rows come along for free on those dates).

KEY CONVERSION: betting line L (half-integer) -> over means actual >= ceil(L),
so sim P(over L) = p_ge_threshold[ceil(L)]. Required integer keys:
  hits 0.5/1.5 -> {1,2} ; hrr 1.5/2.5 -> {2,3} ; home_runs 0.5 -> {1} ;
  strikeouts 4.5/5.5/6.5 -> {5,6,7}.
If a required key is absent from p_ge_threshold, we RECOMPUTE it from the
MonteCarloResult.per_game_samples (independent of whatever thresholds the live
path happened to request); if samples are also unavailable, the row is dropped
and the date/category flagged (never fabricated).

Usage:
  # sample dates from the GBM pairs (2024+), reconstruct, emit sim P(over)
  python run_gate_reconstruct.py \
      --pairs data/models/gbm/wf_predictions_catboost.csv \
      --config config/config.json \
      --n-dates 18 --seed 17 \
      --out data/models/gbm/calibration/sim_probs.csv

  # then close the gate (now including the K column):
  python run_calibration_gate.py compare \
      --gbm data/models/gbm/calibration/gbm_deployed_probs.csv \
      --sim data/models/gbm/calibration/sim_probs.csv
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import sys
import zipfile
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.learning.retrain_runner import RetrainRunner
from src.data import savant as savant_module
from src.evaluation.chronological_market_protocol import verify_protocol_role
from src.evaluation.hits_policy_source import (
    DEFERRED_FRESHNESS_RULE,
    POLICY_SOURCE_KEY,
    POLICY_SOURCE_KIND,
    validate_policy_source,
)
from src.evaluation.identity_keys import MODEL_KEY, OUTCOME_KEY, require_unique
from src.evaluation.hr_pre2026_level_mapping import (
    load_protocol as load_hr_level_mapping_protocol,
    sha256 as hr_level_mapping_sha256,
)
from src.features.feature_store import FeatureStore
from src.evaluation.strict_market_artifact import (
    artifact_path_for_manifest,
    market_category_for_manifest,
    price_freshness_rule_for_manifest,
    selection_rule_for_manifest,
    validate_manifest_purpose,
)
from src.models.total_bases_contract import TOTAL_BASES_MARKET_LINES, candidate_config
from src.utils.model_version import model_version
from run_reconstruct_date import reconstruct_objects

# all four gated categories (B3: strikeouts added now that the simulator
# ships an analytic K distribution; see module docstring)
STANDARD_LINES = {
    "hits": [0.5, 1.5],
    "hrr": [1.5, 2.5],
    "home_runs": [0.5],
    "strikeouts": [4.5, 5.5, 6.5],
}
GATE_CATEGORIES = tuple(STANDARD_LINES)
# hitter subset — used ONLY to weight date sampling, so the same --seed
# reproduces the same dates as the closed hitter-only gate run
HITTER_CATEGORIES = ("hits", "hrr", "home_runs")


def _p_over_from_projection(proj, line: float) -> Optional[float]:
    """sim P(actual >= line) for a projection, or None if unavailable.

    Primary: MonteCarloResult.p_ge_threshold[ceil(line)].
    Fallback: recompute from per_game_samples (category totals) if present.
    (K is analytic — no samples — so only the primary path can serve it.)
    """
    sim = getattr(proj, "simulation", None)
    if sim is None:
        return None
    k = math.ceil(line)  # over L  <=>  count >= ceil(L)
    thr = getattr(sim, "p_ge_threshold", None) or {}
    # dict keys may be float(1.0) or int(1) or str("1.0"); probe tolerantly
    for cand in (float(k), int(k), k, f"{float(k)}", f"{k}"):
        if cand in thr:
            return float(thr[cand])
    # fallback: empirical from per-game samples (leakage-free; same sims)
    samples = getattr(sim, "per_game_samples", None)
    if samples:
        vals = []
        for s in samples:
            v = getattr(s, "category_total", None)
            if v is None:
                v = getattr(s, "total", None)
            if v is not None:
                vals.append(float(v))
        if vals:
            arr = np.asarray(vals, float)
            return float((arr >= k).mean())
    return None


def _normalise_dates(dates: list[object], *, source: str) -> list[str]:
    """Validate an explicit historical date universe before reconstruction.

    A market evaluation must be driven by its certified crosswalk coverage, not
    by an incidental walk-forward pairs file.  Reject malformed and repeated
    dates instead of silently normalising the universe into something else.
    """
    parsed: list[str] = []
    for raw in dates:
        try:
            value = pd.Timestamp(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{source}: invalid date {raw!r}") from exc
        if pd.isna(value):
            raise ValueError(f"{source}: null date")
        rendered = value.strftime("%Y-%m-%d")
        if str(raw)[:10] != rendered:
            raise ValueError(
                f"{source}: date {raw!r} is not an unambiguous YYYY-MM-DD value"
            )
        parsed.append(rendered)
    if not parsed:
        raise ValueError(f"{source}: date universe is empty")
    if len(set(parsed)) != len(parsed):
        duplicate = next(d for d in parsed if parsed.count(d) > 1)
        raise ValueError(f"{source}: duplicate date {duplicate}")
    return sorted(parsed)


def load_dates_file(
    path: str | Path, *, smoke_date: str | None = None
) -> list[str]:
    """Load a certified explicit date universe from JSON or a one-column CSV.

    JSON accepts either a list of YYYY-MM-DD strings or {"dates": [...]}; CSV
    must contain exactly one of ``game_date`` or ``slate_date``.  The narrow
    contract prevents an audit/report file with merely *mentioned* dates from
    accidentally becoming the reconstruction universe.
    """
    source = Path(path)
    if not source.exists() or not source.is_file():
        raise ValueError(f"dates file does not exist: {source}")
    if source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        values = payload.get("dates") if isinstance(payload, dict) else payload
        if not isinstance(values, list):
            raise ValueError(
                f"{source}: JSON must be a date list or an object with a 'dates' list"
            )
        dates = _normalise_dates(values, source=str(source))
        if smoke_date is None:
            return dates
        selected = _normalise_dates([smoke_date], source="--smoke-date")[0]
        if selected not in dates:
            raise ValueError(
                f"--smoke-date {selected} is not in the certified date universe "
                f"from {source}"
            )
        return [selected]
    if source.suffix.lower() == ".csv":
        frame = pd.read_csv(source)
        candidates = [name for name in ("game_date", "slate_date") if name in frame.columns]
        if len(candidates) != 1:
            raise ValueError(
                f"{source}: CSV must contain exactly one date column named game_date or slate_date"
            )
        dates = _normalise_dates(frame[candidates[0]].tolist(), source=str(source))
        if smoke_date is None:
            return dates
        selected = _normalise_dates([smoke_date], source="--smoke-date")[0]
        if selected not in dates:
            raise ValueError(
                f"--smoke-date {selected} is not in the certified date universe "
                f"from {source}"
            )
        return [selected]
    raise ValueError(f"{source}: expected a .json or .csv date universe")


def load_strict_market_manifest(path: str | Path, *, smoke_date: str | None = None) -> list[str]:
    """Load and verify the certified strict-unique market date universe.

    A market reconstruction must use the exact universe that survived the
    market's final eligibility and duplicate-key safeguards.  Reading a nearby
    crosswalk report's broader date list would still reconstruct successfully,
    but would silently score a different experiment.  The manifest and its
    adjacent strict-unique artifact are therefore both verified before an
    expensive reconstruction starts.
    """

    source = Path(path)
    if not source.exists() or not source.is_file():
        raise ValueError(f"market manifest does not exist: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}: invalid JSON market manifest") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{source}: market manifest must be an object")
    selection_rule_for_manifest(payload)
    price_freshness_rule_for_manifest(payload)
    validate_manifest_purpose(payload)
    expected_category = market_category_for_manifest(payload)

    dates = payload.get("official_date_universe")
    if not isinstance(dates, list):
        raise ValueError(f"{source}: missing official_date_universe list")
    parsed_dates = _normalise_dates(dates, source=str(source))

    hashes = payload.get("hashes")
    expected_hash = hashes.get("artifact") if isinstance(hashes, dict) else None
    if not isinstance(expected_hash, str) or len(expected_hash) < 12:
        raise ValueError(f"{source}: missing strict-unique artifact hash")
    artifact = artifact_path_for_manifest(source)
    if not artifact.is_file():
        raise ValueError(f"{source}: required strict-unique artifact missing: {artifact}")
    actual_hash = _sha256_path(artifact)
    if actual_hash is None or not actual_hash.startswith(expected_hash):
        raise ValueError(
            f"{source}: strict-unique artifact hash mismatch for {artifact}; "
            f"expected prefix {expected_hash}, got {actual_hash}"
        )

    funnel = payload.get("funnel")
    expected_rows = funnel.get("strict_unique") if isinstance(funnel, dict) else None
    markets = payload.get("markets")
    if not isinstance(expected_rows, int) or expected_rows <= 0:
        raise ValueError(f"{source}: invalid strict_unique row count")
    if not isinstance(markets, list) or markets != [expected_category]:
        raise ValueError(
            f"{source}: manifest markets must exactly match the declared product "
            f"category {[expected_category]!r}; got markets={markets!r}"
        )

    frame = pd.read_csv(artifact)
    required = ["mlb_game_pk", "player_id", "category", "line", "official_game_date"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{artifact}: missing MARKET_KEY columns {missing}")
    if len(frame) != expected_rows:
        raise ValueError(
            f"{artifact}: row count {len(frame)} != manifest strict_unique {expected_rows}"
        )
    market_key = ["mlb_game_pk", "player_id", "category", "line"]
    if frame[required].isna().any().any() or frame.duplicated(market_key).any():
        raise ValueError(f"{artifact}: strict-unique MARKET_KEY is null or duplicated")
    if set(frame["category"].astype(str)) != {expected_category}:
        raise ValueError(
            f"{artifact}: expected {expected_category!r}-only rows for the "
            "declared market product"
        )
    # The artifact naturally has many rows per official date.  Only the
    # manifest's *date universe* must reject duplicates; here we derive the
    # unique set represented by the already-validated rows.
    raw_artifact_dates = frame["official_game_date"].astype(str).tolist()
    parsed_artifact_dates = _normalise_dates(
        sorted(set(raw_artifact_dates)), source=str(artifact)
    )
    artifact_dates = parsed_artifact_dates
    if artifact_dates != parsed_dates:
        raise ValueError(
            f"{source}: official_date_universe does not exactly match the "
            f"artifact's canonical official_game_date values.\n"
            f"  manifest: {parsed_dates}\n"
            f"  artifact: {artifact_dates}\n"
            "A vendor market date must never masquerade as an official MLB date."
        )
    if smoke_date is None:
        return parsed_dates
    selected = _normalise_dates([smoke_date], source="--smoke-date")[0]
    if selected not in parsed_dates:
        raise ValueError(
            f"--smoke-date {selected} is not in the certified strict market universe "
            f"from {source}"
        )
    return [selected]


def load_policy_source_manifest(
    path: str | Path, *, smoke_date: str | None = None
) -> list[str]:
    """Verify an uncensored policy-source manifest and return its dates.

    This source is deliberately not a strict scoring universe: duplicate final
    MARKET_KEYs and old-but-nonnegative quotes remain visible so the fit arm can
    evaluate freshness without inheriting the 90-minute censor. Exact vendor-
    fragment identity must still be unique, and every hash/date contract is
    checked before reconstruction begins.
    """
    source = Path(path)
    if not source.exists() or not source.is_file():
        raise ValueError(f"policy-source manifest does not exist: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}: invalid JSON policy-source manifest") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{source}: policy-source manifest must be an object")
    selection_rule_for_manifest(payload)
    if payload.get("artifact_kind") != POLICY_SOURCE_KIND:
        raise ValueError(
            f"{source}: expected artifact_kind {POLICY_SOURCE_KIND!r}; "
            f"got {payload.get('artifact_kind')!r}"
        )
    if payload.get("price_freshness_rule") != DEFERRED_FRESHNESS_RULE:
        raise ValueError(f"{source}: policy source did not defer freshness")
    if payload.get("max_quote_age") is not None:
        raise ValueError(f"{source}: policy source is already censored by max_quote_age")
    if payload.get("source_key") != POLICY_SOURCE_KEY:
        raise ValueError(f"{source}: policy source_key contract differs")
    if payload.get("raw_decimal_odds_included") is not True:
        raise ValueError(f"{source}: raw posted odds are required for policy fitting")
    if payload.get("markets") != ["hits"]:
        raise ValueError(f"{source}: expected a hits-only policy source")

    dates = payload.get("official_date_universe")
    if not isinstance(dates, list):
        raise ValueError(f"{source}: missing official_date_universe list")
    parsed_dates = _normalise_dates(dates, source=str(source))
    artifact = artifact_path_for_manifest(source)
    if not artifact.is_file():
        raise ValueError(f"{source}: required policy-source artifact missing: {artifact}")
    expected_hash = (payload.get("hashes") or {}).get("artifact")
    actual_hash = _sha256_path(artifact)
    if not isinstance(expected_hash, str) or len(expected_hash) < 12:
        raise ValueError(f"{source}: missing policy-source artifact hash")
    if actual_hash is None or not actual_hash.startswith(expected_hash):
        raise ValueError(
            f"{source}: policy-source artifact hash mismatch; expected prefix "
            f"{expected_hash}, got {actual_hash}"
        )

    frame = pd.read_csv(artifact)
    validate_policy_source(frame)
    expected_rows = (payload.get("funnel") or {}).get("policy_source_rows")
    if isinstance(expected_rows, bool) or int(expected_rows or -1) != len(frame):
        raise ValueError(
            f"{artifact}: row count {len(frame)} != policy_source_rows "
            f"{expected_rows!r}"
        )
    artifact_dates = _normalise_dates(
        sorted(set(frame.official_game_date.astype(str))), source=str(artifact)
    )
    if artifact_dates != parsed_dates:
        raise ValueError(
            f"{source}: official_date_universe does not exactly match the "
            "policy source's canonical official_game_date values"
        )
    if smoke_date is None:
        return parsed_dates
    selected = _normalise_dates([smoke_date], source="--smoke-date")[0]
    if selected not in parsed_dates:
        raise ValueError(
            f"--smoke-date {selected} is not in the certified policy-source "
            f"universe from {source}"
        )
    return [selected]


def validate_chronological_source(args: argparse.Namespace) -> dict[str, Any] | None:
    """Fail closed unless a chronological lock and role are supplied together.

    The strict manifest proves the market universe.  The chronological protocol
    separately proves what that universe is allowed to be used for.  Treating
    one as a substitute for the other would let a fit arm masquerade as a
    holdout while every row-level identity check still passed.
    """
    protocol = getattr(args, "chronological_protocol", None)
    role = getattr(args, "chronological_role", None)
    market_manifest = getattr(args, "market_manifest", None)
    policy_source_manifest = getattr(args, "policy_source_manifest", None)
    supplied_manifests = [
        value for value in (market_manifest, policy_source_manifest) if value
    ]
    if bool(protocol) != bool(role):
        raise ValueError(
            "--chronological-protocol and --chronological-role must be supplied together"
        )
    if protocol and len(supplied_manifests) != 1:
        raise ValueError(
            "a chronological protocol requires exactly one market universe manifest"
        )
    if not protocol:
        return None
    return verify_protocol_role(
        protocol,
        role=role,
        market_manifest=supplied_manifests[0],
    )


def validate_hr_level_mapping_source(
    args: argparse.Namespace,
    *,
    config: dict[str, Any],
    dates: list[str],
) -> dict[str, Any] | None:
    """Bind a reconstruction to the locked HR level-mapping experiment.

    This is deliberately separate from the market-policy chronology contract:
    the input is an outcome-blind 2025 research date artifact, not a market
    universe.  The runner verifies every path/hash/flag before any expensive
    historical work begins.
    """
    protocol_arg = getattr(args, "hr_level_mapping_protocol", None)
    if not protocol_arg:
        return None
    protocol_path = Path(protocol_arg).resolve()
    payload = load_hr_level_mapping_protocol(protocol_path, verify_files=True)
    if args.chronological_protocol:
        raise ValueError(
            "--hr-level-mapping-protocol cannot be combined with "
            "--chronological-protocol"
        )
    if not args.dates_file:
        raise ValueError("--hr-level-mapping-protocol requires --dates-file")
    if not args.pa_distribution_artifact:
        raise ValueError(
            "--hr-level-mapping-protocol requires --pa-distribution-artifact"
        )
    for name in ("feature_snapshot_dir", "outcomes_out"):
        if not getattr(args, name, None):
            raise ValueError(f"--hr-level-mapping-protocol requires --{name.replace('_', '-')}")
    if not args.fail_on_flags or not args.require_statcast_profiles:
        raise ValueError(
            "--hr-level-mapping-protocol requires --fail-on-flags and "
            "--require-statcast-profiles"
        )

    inputs = payload["inputs"]

    def require_bound_path(argument: str | Path, input_name: str) -> None:
        supplied = Path(argument).resolve()
        recorded = Path(inputs[input_name]["path"])
        recorded = recorded if recorded.is_absolute() else Path(__file__).resolve().parent / recorded
        if supplied != recorded.resolve():
            raise ValueError(
                f"{input_name} path is not the HR level-mapping protocol's locked input"
            )
        if hr_level_mapping_sha256(supplied) != inputs[input_name]["sha256"]:
            raise ValueError(f"{input_name} hash differs from the locked protocol")

    require_bound_path(args.dates_file, "date_universe")
    require_bound_path(args.pa_distribution_artifact, "pa_distribution")
    if not args.config:
        raise ValueError("--hr-level-mapping-protocol requires explicit --config")
    require_bound_path(args.config, "preserved_model_config")
    if args.simulation_seed != payload["reconstruction"]["simulation_seed"]:
        raise ValueError("simulation seed differs from the HR level-mapping protocol")

    locked_dates = payload["chronology"]["calibration_dates"] + payload["chronology"][
        "confirmation_dates_open_once"
    ]
    if args.smoke_date:
        if dates != [args.smoke_date] or args.smoke_date not in locked_dates:
            raise ValueError("smoke date is not exactly one locked HR reconstruction date")
        run_kind = "smoke"
    else:
        if dates != locked_dates:
            raise ValueError("full HR reconstruction dates differ from the locked protocol")
        run_kind = "full"

    effective_pa = (config.get("base_running", {}) or {}).get("pa_distribution_path")
    if not effective_pa or Path(str(effective_pa)).resolve() != Path(
        args.pa_distribution_artifact
    ).resolve():
        raise ValueError("effective config does not contain the locked PA artifact")
    return {
        "schema_version": payload["schema_version"],
        "status": payload["status"],
        "protocol_path": str(protocol_path),
        "protocol_sha256": hr_level_mapping_sha256(protocol_path),
        "run_kind": run_kind,
        "betting_authorized": False,
        "may_2026_opened": False,
    }


def sample_dates(pairs_path: str, n_dates: int, seed: int) -> list[str]:
    df = pd.read_csv(pairs_path)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df[df["game_date"] >= "2024-01-01"]
    # weight sampling toward dates with more HITTER rows — deliberately
    # unchanged by B3 so the same seed reproduces the hitter-run date sample;
    # K rows are emitted on whatever dates are drawn.
    hit = df[df.category.isin(HITTER_CATEGORIES)]
    counts = hit.groupby(hit.game_date.dt.strftime("%Y-%m-%d")).size()
    dates = counts.index.to_numpy()
    if len(dates) <= n_dates:
        return sorted(dates.tolist())
    rng = np.random.default_rng(seed)
    w = counts.to_numpy(float); w = w / w.sum()
    pick = rng.choice(dates, size=n_dates, replace=False, p=w)
    return sorted(pick.tolist())


def _write_csv_atomic(frame: pd.DataFrame, path: Path, key: list[str], label: str) -> None:
    """Publish a gate artifact only after a non-empty, key-valid readback."""
    require_unique(frame, key, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    frame.to_csv(temp_path, index=False)
    if not temp_path.exists() or temp_path.stat().st_size == 0:
        raise RuntimeError(f"FATAL: reconstruction did not write artifact {temp_path}")
    written = pd.read_csv(temp_path)
    require_unique(written, key, f"written {label}")
    temp_path.replace(path)
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"FATAL: reconstruction artifact missing after write: {path}")


def _sha256_path(path: Path) -> Optional[str]:
    """Return a content hash, or None when an optional provenance input is absent."""
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_fitted_artifact_chronology(config: dict[str, Any], dates: list[str]) -> None:
    """Reject a fitted artifact that can see any reconstruction target date."""

    first_target = min(pd.Timestamp(value) for value in dates)

    def load(path_raw: Any, label: str) -> tuple[Path, dict[str, Any]]:
        if not path_raw:
            raise ValueError(f"{label} path is required")
        path = Path(str(path_raw))
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.exists():
            raise ValueError(f"{label} does not exist: {path}")
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        provenance = raw.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError(f"{label} has no provenance block: {path}")
        return path, provenance

    pa_block = config.get("base_running", {}) or {}
    if pa_block.get("pa_distribution_path"):
        path, provenance = load(pa_block["pa_distribution_path"], "PA artifact")
        date_max = provenance.get("date_max")
        if not date_max or pd.Timestamp(date_max) >= first_target:
            raise ValueError(
                f"PA artifact is not chronology-safe for {first_target.date()}: "
                f"date_max={date_max!r} in {path}"
            )

    kbb_block = config.get("pa_simulator", {}) or {}
    if bool(kbb_block.get("use_fitted_kbb", False)):
        path, provenance = load(kbb_block.get("kbb_artifact_path"), "K/BB artifact")
        expected = str(kbb_block.get("kbb_artifact_sha256") or "").lower()
        actual = _sha256_path(path)
        if len(expected) != 64 or actual != expected:
            raise ValueError(
                f"K/BB artifact hash mismatch: expected {expected!r}, got {actual!r}"
            )
        date_max = provenance.get("fit_universe_date_max")
        if not date_max or pd.Timestamp(date_max) >= first_target:
            raise ValueError(
                f"K/BB artifact is not chronology-safe for {first_target.date()}: "
                f"fit_universe_date_max={date_max!r} in {path}"
            )


def _write_json_atomic(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError(f"FATAL: provenance manifest was not written: {tmp}")
    tmp.replace(path)


def _canonical_config_bytes(config: dict[str, Any]) -> bytes:
    return json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _release_source_files(root: Path) -> list[Path]:
    """Complete repository-owned Python snapshot for a reconstruction release."""
    files = [root / "run_gate_reconstruct.py", root / "run_reconstruct_date.py"]
    files.extend(sorted((root / "src").rglob("*.py")))
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        files.append(pyproject)
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise RuntimeError(f"release source snapshot is missing files: {missing}")
    return sorted(set(files), key=lambda path: path.relative_to(root).as_posix())


def _source_tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _release_source_files(root):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(rel + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _write_source_bundle(
    *, root: Path, manifest_path: Path, config: dict[str, Any]
) -> dict[str, Any]:
    """Persist exact source/config/runtime content, not merely remembered hashes."""
    name = manifest_path.name
    stem = name[:-len(".manifest.json")] if name.endswith(".manifest.json") else manifest_path.stem
    path = manifest_path.parent / f"{stem}.source_snapshot.zip"
    tmp = path.with_suffix(".zip.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)

    packages = sorted(
        {
            str(dist.metadata.get("Name") or ""): str(dist.version)
            for dist in importlib.metadata.distributions()
            if dist.metadata.get("Name")
        }.items()
    )
    environment = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "packages": dict(packages),
    }

    def write_bytes(archive: zipfile.ZipFile, arcname: str, payload: bytes) -> None:
        info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, payload)

    with zipfile.ZipFile(tmp, "w") as archive:
        for source in _release_source_files(root):
            write_bytes(
                archive,
                f"source/{source.relative_to(root).as_posix()}",
                source.read_bytes(),
            )
        write_bytes(archive, "effective_config.json", _canonical_config_bytes(config))
        write_bytes(
            archive,
            "environment.json",
            json.dumps(environment, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
    tmp.replace(path)
    return {
        "path": str(path.resolve()),
        "sha256": _sha256_path(path),
        "source_tree_sha256": _source_tree_sha256(root),
        "files": len(_release_source_files(root)),
    }


def _feature_snapshot_evidence(store: FeatureStore, dates: list[str]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for game_date in dates:
        manifest = store.verify_manifest(game_date)
        if manifest is None:
            raise RuntimeError(f"canonical feature snapshot lacks a manifest: {game_date}")
        artifacts = manifest.get("artifacts") or {}
        bundle = artifacts.get("json") or {}
        bundle_path = store.manifest_path(game_date).parent / str(bundle.get("path") or "")
        if not bundle_path.is_file():
            raise RuntimeError(f"canonical JSON feature snapshot is missing: {bundle_path}")
        evidence[game_date] = {
            "manifest_path": str(store.manifest_path(game_date).resolve()),
            "manifest_sha256": _sha256_path(store.manifest_path(game_date)),
            "bundle_path": str(bundle_path.resolve()),
            "bundle_sha256": _sha256_path(bundle_path),
            "bundle_count": int(manifest.get("bundle_count", -1)),
        }
    return evidence


def _provenance_manifest(
    *,
    args: argparse.Namespace,
    config: dict[str, Any],
    dates: list[str],
    output_path: Path,
    outcomes_path: Optional[Path],
    source_bundle: dict[str, Any],
    feature_snapshots: dict[str, Any],
) -> dict[str, Any]:
    """Inputs that determine a reconstruction artifact's meaning.

    Historical probabilities depend on more than the JSON config: the
    Statcast provider mode, fitted PA artifact, source code and draw seed all
    materially change hitter probabilities. Record them beside every artifact
    so a future comparison cannot silently span different execution modes.
    """
    root = Path(__file__).resolve().parent
    source_files = {
        name: root / name
        for name in (
            "run_gate_reconstruct.py",
            "run_reconstruct_date.py",
            "src/prediction/prop_engine.py",
            "src/simulation/game_simulator.py",
            "src/simulation/pa_simulator.py",
            "src/features/feature_factory.py",
            "src/features/legacy_statcast_features.py",
            "src/data/savant.py",
            "src/data/statcast_batted_ball_rates.py",
            "src/data/statcast_distributions.py",
            "src/data/statcast_roller.py",
            "src/data/mlb_api.py",
            "src/evaluation/chronological_market_protocol.py",
            "src/evaluation/hr_pre2026_level_mapping.py",
            "src/evaluation/hits_policy_source.py",
            "src/evaluation/hits_contact_adapter.py",
            "src/models/total_bases_contract.py",
        )
    }
    pa_path_raw = (config.get("base_running", {}) or {}).get("pa_distribution_path")
    pa_path = Path(str(pa_path_raw)) if pa_path_raw else None
    if pa_path is not None and not pa_path.is_absolute():
        pa_path = (Path.cwd() / pa_path).resolve()
    kbb_path_raw = (config.get("pa_simulator", {}) or {}).get("kbb_artifact_path")
    kbb_path = Path(str(kbb_path_raw)) if kbb_path_raw else None
    if kbb_path is not None and not kbb_path.is_absolute():
        kbb_path = (Path.cwd() / kbb_path).resolve()

    config_bytes = _canonical_config_bytes(config)
    pairs_path = Path(args.pairs).resolve() if args.pairs else None
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "dates": dates,
        "config_argument": args.config,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "effective_config": config,
        "model_version": model_version(config),
        "pairs_path": str(pairs_path) if pairs_path is not None else None,
        "pairs_sha256": _sha256_path(pairs_path) if pairs_path is not None else None,
        "date_source": (
                {
                    "kind": "dates_file",
                    "path": str(Path(args.dates_file).resolve()),
                    "sha256": _sha256_path(Path(args.dates_file).resolve()),
                    "smoke_date": args.smoke_date,
                }
            if args.dates_file
            else (
                {
                    "kind": "strict_market_manifest",
                    "path": str(Path(args.market_manifest).resolve()),
                    "sha256": _sha256_path(Path(args.market_manifest).resolve()),
                    "strict_market_artifact_sha256": _sha256_path(
                        artifact_path_for_manifest(Path(args.market_manifest).resolve())
                    ),
                    "smoke_date": args.smoke_date,
                }
                if args.market_manifest
                else (
                    {
                        "kind": "policy_source_manifest",
                        "path": str(Path(args.policy_source_manifest).resolve()),
                        "sha256": _sha256_path(
                            Path(args.policy_source_manifest).resolve()
                        ),
                        "policy_source_artifact_sha256": _sha256_path(
                            artifact_path_for_manifest(
                                Path(args.policy_source_manifest).resolve()
                            )
                        ),
                        "smoke_date": args.smoke_date,
                    }
                    if args.policy_source_manifest
                    else {"kind": "explicit_cli" if args.dates else "sampled_from_pairs"}
                )
            )
        ),
        "chronological_protocol": getattr(args, "_chronological_lock", None),
        "hr_level_mapping_protocol": getattr(
            args, "_hr_level_mapping_lock", None
        ),
        "simulation_random_seed": (config.get("simulation", {}) or {}).get("random_seed"),
        "fail_on_flags": bool(args.fail_on_flags),
        "require_statcast_profiles": bool(args.require_statcast_profiles),
        "total_bases_candidate": bool(args.include_total_bases_candidate),
        "statcast_provider_available": savant_module.pyb is not None,
        "pa_distribution_path": str(pa_path) if pa_path is not None else None,
        "pa_distribution_sha256": _sha256_path(pa_path) if pa_path is not None else None,
        "kbb_artifact_path": str(kbb_path) if kbb_path is not None else None,
        "kbb_artifact_sha256": _sha256_path(kbb_path) if kbb_path is not None else None,
        "python_version": platform.python_version(),
        "source_sha256": {name: _sha256_path(path) for name, path in source_files.items()},
        "source_snapshot": source_bundle,
        "feature_snapshots": feature_snapshots,
        "probability_artifact": str(output_path.resolve()),
        "probability_artifact_sha256": _sha256_path(output_path),
        "outcome_artifact": str(outcomes_path.resolve()) if outcomes_path is not None else None,
        "outcome_artifact_sha256": _sha256_path(outcomes_path) if outcomes_path is not None else None,
    }
    return manifest


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=None,
                    help="required only for sampled dates; never used to select an explicit market universe")
    ap.add_argument("--config", default=None, help="config.json (default: RetrainRunner default)")
    ap.add_argument("--n-dates", type=int, default=18, help="number of 2024+ dates to reconstruct")
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--simulation-seed", type=int, default=None,
                    help="structural seed for reproducible Monte Carlo gate artifacts")
    ap.add_argument("--out", required=True, help="output sim_probs.csv path")
    ap.add_argument("--outcomes-out", default=None,
                    help="optional game-scoped actuals CSV for the same reconstruction")
    ap.add_argument("--manifest-out", default=None,
                    help="optional provenance JSON (default: beside --out)")
    ap.add_argument(
        "--feature-snapshot-dir",
        default=None,
        help=(
            "persist the exact model-ready PlayerFeatureBundle JSON + hash manifest "
            "for every reconstructed date. Required for chronology-bound canonical runs"
        ),
    )
    ap.add_argument("--dates", nargs="*", default=None,
                    help="explicit date list (overrides sampling), e.g. 2024-06-15 2025-06-20")
    ap.add_argument("--dates-file", default=None,
                    help="certified .json date list or .csv game_date/slate_date universe; overrides sampling")
    ap.add_argument(
        "--market-manifest", default=None,
        help=(
            "certified strict-market manifest. Validates its paired artifact's "
            "hash/key/selection-rule contract and reconstructs exactly its "
            "official_date_universe."
        ),
    )
    ap.add_argument(
        "--policy-source-manifest", default=None,
        help=(
            "certified uncensored hits policy-source manifest. Validates its "
            "fragment identity, raw-odds, hash, and deferred-freshness contracts "
            "and reconstructs exactly its official_date_universe."
        ),
    )
    ap.add_argument(
        "--smoke-date", default=None,
        help=(
            "one date from either market-universe manifest for a cheap "
            "reconstruction smoke test. "
            "The manifest is still verified first; an outside date hard-fails."
        ),
    )
    ap.add_argument(
        "--chronological-protocol", default=None,
        help=(
            "hashed outcome-blind fit/holdout protocol. Must be paired with "
            "--chronological-role and its matching market-universe manifest."
        ),
    )
    ap.add_argument(
        "--chronological-role", choices=("fit", "holdout"), default=None,
        help="the role this reconstruction is permitted to serve in the locked protocol",
    )
    ap.add_argument(
        "--hr-level-mapping-protocol",
        default=None,
        help=(
            "locked pre-2026 HR level-mapping experiment. Requires the exact "
            "bound dates file, preserved config, PA artifact, seed, feature "
            "snapshots, official outcomes, and fail-closed flags"
        ),
    )
    ap.add_argument("--fail-on-flags", action="store_true",
                    help="do not publish partial artifacts when any date/category is incomplete")
    ap.add_argument("--require-statcast-profiles", action="store_true",
                    help="fail a canonical historical run if a slate has zero usable Statcast profiles")
    ap.add_argument(
        "--pa-distribution-artifact",
        default=None,
        help=(
            "explicit chronology-safe PA artifact override for historical "
            "reconstruction; applied to the in-memory config and provenance hash"
        ),
    )
    ap.add_argument(
        "--include-total-bases-candidate",
        action="store_true",
        help=(
            "emit ONLY the unpromoted total-bases candidate on an explicit date "
            "universe. This forks provenance and never changes normal gate output."
        ),
    )
    args = ap.parse_args(argv)

    if args.chronological_protocol and not args.feature_snapshot_dir:
        ap.error(
            "--chronological-protocol requires --feature-snapshot-dir; a canonical "
            "release must preserve its exact model-ready data boundary"
        )

    config = RetrainRunner.load_config(args.config)
    if args.pa_distribution_artifact:
        config = json.loads(json.dumps(config))
        config.setdefault("base_running", {})["pa_distribution_path"] = (
            args.pa_distribution_artifact
        )
    if args.simulation_seed is not None:
        # JSON round-trip keeps the runner-only override isolated from the
        # caller's loaded config object and documents that no coefficient moved.
        config = json.loads(json.dumps(config))
        config.setdefault("simulation", {})["random_seed"] = args.simulation_seed
    if args.include_total_bases_candidate:
        if not args.dates_file:
            ap.error(
                "--include-total-bases-candidate requires --dates-file; "
                "use the hashed accepted-market universe, never ad-hoc or sampled dates"
            )
        if not args.outcomes_out:
            ap.error("--include-total-bases-candidate requires --outcomes-out for official gate scoring")
        if not args.fail_on_flags:
            ap.error("--include-total-bases-candidate requires --fail-on-flags")
        if not args.require_statcast_profiles:
            ap.error("--include-total-bases-candidate requires --require-statcast-profiles")
        if args.simulation_seed is None:
            ap.error("--include-total-bases-candidate requires an explicit --simulation-seed")
        try:
            config = candidate_config(config)
        except ValueError as exc:
            print(f"FATAL: {exc}", file=sys.stderr)
            return 2
        standard_lines = {"total_bases": list(TOTAL_BASES_MARKET_LINES)}
        reconstruction_hitter_categories = ("total_bases",)
    else:
        standard_lines = STANDARD_LINES
        reconstruction_hitter_categories = HITTER_CATEGORIES
    if args.require_statcast_profiles and savant_module.pyb is None:
        print(
            "FATAL: --require-statcast-profiles needs the Statcast provider, "
            "but pybaseball is unavailable.",
            file=sys.stderr,
        )
        return 2
    explicit_sources = sum(bool(value) for value in (
        args.dates,
        args.dates_file,
        args.market_manifest,
        args.policy_source_manifest,
    ))
    if explicit_sources > 1:
        ap.error(
            "pass only one of --dates, --dates-file, --market-manifest, or "
            "--policy-source-manifest"
        )
    if args.smoke_date and not (
        args.market_manifest or args.policy_source_manifest or args.dates_file
    ):
        ap.error("--smoke-date requires a certified manifest or dates file")
    try:
        args._chronological_lock = validate_chronological_source(args)
        if args.market_manifest:
            dates = load_strict_market_manifest(args.market_manifest, smoke_date=args.smoke_date)
        elif args.policy_source_manifest:
            dates = load_policy_source_manifest(
                args.policy_source_manifest, smoke_date=args.smoke_date
            )
        elif args.dates_file:
            dates = load_dates_file(args.dates_file, smoke_date=args.smoke_date)
        elif args.dates:
            dates = _normalise_dates(args.dates, source="--dates")
        else:
            if not args.pairs:
                ap.error("--pairs is required when sampling dates")
            dates = sample_dates(args.pairs, args.n_dates, args.seed)
    except ValueError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2
    try:
        args._hr_level_mapping_lock = validate_hr_level_mapping_source(
            args, config=config, dates=dates
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2
    try:
        _validate_fitted_artifact_chronology(config, dates)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2
    print(f"reconstructing {len(dates)} dates: {', '.join(dates)}")

    feature_store = (
        FeatureStore(root=Path(args.feature_snapshot_dir))
        if args.feature_snapshot_dir else None
    )
    release_root = Path(__file__).resolve().parent
    feature_provenance = {
        "status": "canonical_reconstruction_input_v1",
        "config_sha256": hashlib.sha256(_canonical_config_bytes(config)).hexdigest(),
        "source_tree_sha256": _source_tree_sha256(release_root),
        "date_source_kind": (
            "policy_source_manifest" if args.policy_source_manifest
            else "strict_market_manifest" if args.market_manifest
            else "explicit_or_sampled"
        ),
        "chronological_protocol_sha256": (
            _sha256_path(Path(args.chronological_protocol).resolve())
            if args.chronological_protocol else None
        ),
        "chronological_role": args.chronological_role,
    }

    def persist_bundles(game_date: str, bundles: list[Any]) -> None:
        if feature_store is None:
            return
        feature_store.save(
            bundles,
            game_date,
            write_json=True,
            write_parquet=False,
            provenance={**feature_provenance, "game_date": game_date},
        )

    rows: list[dict[str, Any]] = []
    outcome_rows: list[dict[str, Any]] = []
    flags: list[str] = []
    for i, d in enumerate(dates, 1):
        try:
            projections, _outcomes = reconstruct_objects(
                d,
                config,
                require_statcast_profiles=args.require_statcast_profiles,
                hitter_categories=reconstruction_hitter_categories,
                bundle_sink=persist_bundles if feature_store is not None else None,
            )
        except Exception as exc:  # a bad date shouldn't kill the whole run
            flags.append(f"{d}: reconstruction failed ({exc})")
            print(f"  [{i}/{len(dates)}] {d}  FAILED: {exc}", file=sys.stderr)
            continue

        for actual in _outcomes:
            if actual.mlb_game_pk is None:
                flags.append(
                    f"{d}/{actual.category}/player {actual.player_id}: outcome missing mlb_game_pk"
                )
                continue
            outcome_rows.append(dict(
                mlb_game_pk=int(actual.mlb_game_pk),
                player_id=int(actual.player_id),
                game_date=d,
                category=str(actual.category),
                actual_value=float(actual.actual_value),
            ))

        n_before = len(rows)
        for proj in projections:
            cat = proj.category
            if cat not in standard_lines:      # skip fantasy (ungated)
                continue
            for L in standard_lines[cat]:
                p = _p_over_from_projection(proj, L)
                if p is None:
                    flags.append(f"{d}/{cat}/L{L}: no p_ge_threshold[{math.ceil(L)}] and no samples")
                    continue
                rows.append(dict(
                    mlb_game_pk=int(proj.mlb_game_pk) if proj.mlb_game_pk is not None else None,
                    player_id=int(proj.player_id),
                    game_date=d,
                    category=cat,
                    line=float(L),
                    sim_p_over=float(np.clip(p, 1e-6, 1 - 1e-6)),
                ))
        print(f"  [{i}/{len(dates)}] {d}  +{len(rows) - n_before} sim rows")

    if not rows:
        print("no sim rows produced — check reconstruction/threshold keys", file=sys.stderr)
        return 2

    out = pd.DataFrame(rows)
    outcomes = pd.DataFrame(outcome_rows)
    if outcomes.empty:
        flags.append("no game-scoped outcome rows produced")
    else:
        require_unique(outcomes, OUTCOME_KEY, "reconstruction outcome export")
        expected_outcomes = out[OUTCOME_KEY].drop_duplicates()
        missing_outcomes = expected_outcomes.merge(
            outcomes[OUTCOME_KEY], on=OUTCOME_KEY, how="left", indicator=True,
        )
        missing_n = int((missing_outcomes["_merge"] == "left_only").sum())
        if missing_n:
            # A scheduled player can legitimately never appear in the official
            # box score (late scratch / unused expected pitcher).  There is no
            # target to invent for that projection, so it remains in the model
            # artifact but is explicitly excluded by the one-to-one outcome
            # join at scoring time.  This is not a failed reconstruction date.
            sample = missing_outcomes.loc[
                missing_outcomes["_merge"] == "left_only", OUTCOME_KEY
            ].head(20)
            print(
                f"NOTE: {missing_n} model outcome keys have no official game-scoped "
                f"actual and will be unscored (never imputed):\n"
                f"{sample.to_string(index=False)}"
            )

    if flags and (args.fail_on_flags or args.require_statcast_profiles):
        print(f"\nFATAL: {len(flags)} incomplete reconstruction flags; no artifact published.",
              file=sys.stderr)
        for flag in flags:
            print("  -", flag, file=sys.stderr)
        return 2
    output_path = Path(args.out)
    _write_csv_atomic(out, output_path, MODEL_KEY, "simulator probability export")
    print(f"\nwrote {len(out)} sim P(over) rows -> {output_path}")
    if args.outcomes_out:
        outcomes_path = Path(args.outcomes_out)
        _write_csv_atomic(outcomes, outcomes_path, OUTCOME_KEY, "reconstruction outcome export")
        print(f"wrote {len(outcomes)} game-scoped outcomes -> {outcomes_path}")
    else:
        outcomes_path = None
    manifest_path = (
        Path(args.manifest_out)
        if args.manifest_out
        else output_path.with_suffix(".manifest.json")
    )
    source_bundle = _write_source_bundle(
        root=release_root,
        manifest_path=manifest_path,
        config=config,
    )
    feature_snapshots = (
        _feature_snapshot_evidence(feature_store, dates)
        if feature_store is not None else {}
    )
    _write_json_atomic(
        _provenance_manifest(
            args=args,
            config=config,
            dates=dates,
            output_path=output_path,
            outcomes_path=outcomes_path,
            source_bundle=source_bundle,
            feature_snapshots=feature_snapshots,
        ),
        manifest_path,
    )
    print(f"wrote provenance manifest -> {manifest_path}")
    print(out.groupby("category").size().to_string())
    if flags:
        print(f"\n{len(flags)} flagged (dropped, never fabricated):")
        for f in flags[:20]:
            print("  -", f)
        if len(flags) > 20:
            print(f"  ... and {len(flags) - 20} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
