"""Fail-closed contract for chronological market-policy experiments.

The split is an input to an experiment, not a reporting convenience.  This
module locks a parent strict-market universe into an earlier fit arm and a
later holdout arm, verifies that the two arms reproduce the parent *including
price values*, and lets reconstruction prove that it consumed the declared
role.

No outcomes, model probabilities, profit, capture, or residuals are read here.
That is deliberate: the chronology must be fixed without seeing the quantity
the holdout is meant to judge.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.evaluation.identity_keys import MARKET_KEY
from src.evaluation.hits_policy_source import (
    DEFERRED_FRESHNESS_RULE,
    POLICY_SOURCE_KEY,
    POLICY_SOURCE_KIND,
    validate_policy_source,
)
from src.evaluation.strict_market_artifact import (
    artifact_path_for_manifest,
    price_freshness_rule_for_manifest,
    selection_rule_for_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "chronological-market-policy-protocol-v1"
ROLES = ("fit", "holdout")


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _resolve_recorded(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def _read_market_manifest(path: str | Path) -> tuple[dict[str, Any], pd.DataFrame, Path]:
    manifest_path = Path(path).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{manifest_path}: manifest must be an object")
    selection_rule_for_manifest(payload)
    policy_source = payload.get("artifact_kind") == POLICY_SOURCE_KIND
    if policy_source:
        if payload.get("price_freshness_rule") != DEFERRED_FRESHNESS_RULE:
            raise ValueError(
                f"{manifest_path}: uncensored policy source did not defer freshness"
            )
        if payload.get("max_quote_age") is not None:
            raise ValueError(
                f"{manifest_path}: uncensored policy source applied max_quote_age"
            )
    else:
        price_freshness_rule_for_manifest(payload)

    artifact = artifact_path_for_manifest(manifest_path)
    if not artifact.is_file():
        raise ValueError(f"{manifest_path}: paired strict artifact is missing: {artifact}")
    expected = (payload.get("hashes") or {}).get("artifact")
    actual = sha256(artifact)
    if not isinstance(expected, str) or not actual.startswith(expected):
        raise ValueError(
            f"{manifest_path}: strict artifact hash mismatch; expected {expected!r}, "
            f"got {actual}"
        )

    frame = pd.read_csv(artifact)
    identity_key = POLICY_SOURCE_KEY if policy_source else MARKET_KEY
    required = [*MARKET_KEY, *identity_key, "official_game_date"]
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"{artifact}: missing chronological source columns {missing}")
    if policy_source:
        validate_policy_source(frame)
    elif frame[required].isna().any().any() or frame.duplicated(MARKET_KEY).any():
        raise ValueError(f"{artifact}: MARKET_KEY/date is null or MARKET_KEY is duplicated")
    expected_rows = (payload.get("funnel") or {}).get(
        "policy_source_rows" if policy_source else "strict_unique"
    )
    if isinstance(expected_rows, bool) or int(expected_rows or -1) != len(frame):
        raise ValueError(
            f"{artifact}: {len(frame)} rows != manifest row contract {expected_rows!r}"
        )
    dates = sorted(frame.official_game_date.astype(str).unique().tolist())
    if dates != payload.get("official_date_universe"):
        raise ValueError(f"{manifest_path}: artifact and manifest date universes differ")
    return payload, frame, artifact


def _identity_key(payload: dict[str, Any]) -> list[str]:
    return POLICY_SOURCE_KEY if payload.get("artifact_kind") == POLICY_SOURCE_KIND else MARKET_KEY


def _sorted_full(frame: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """Canonical full projection; catches price drift as well as key drift."""
    return frame.sort_values(key).reset_index(drop=True)


def build_protocol(
    *,
    parent_manifest: str | Path,
    fit_manifest: str | Path,
    holdout_manifest: str | Path,
    holdout_start: str,
    output: str | Path,
) -> dict[str, Any]:
    """Validate and write an outcome-blind chronological lock."""
    try:
        boundary = pd.Timestamp(holdout_start)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid holdout_start {holdout_start!r}") from exc
    rendered_boundary = boundary.strftime("%Y-%m-%d")
    if str(holdout_start) != rendered_boundary:
        raise ValueError("holdout_start must be an exact YYYY-MM-DD date")

    parent_payload, parent, parent_artifact = _read_market_manifest(parent_manifest)
    fit_payload, fit, fit_artifact = _read_market_manifest(fit_manifest)
    hold_payload, holdout, hold_artifact = _read_market_manifest(holdout_manifest)

    artifact_kinds = {
        payload.get("artifact_kind", "strict_market_universe_v1")
        for payload in (parent_payload, fit_payload, hold_payload)
    }
    if len(artifact_kinds) != 1:
        raise ValueError("parent/fit/holdout artifact kinds are not identical")
    identity_key = _identity_key(parent_payload)

    contracts = {
        (
            payload.get("selection_rule"),
            payload.get("artifact_kind", "strict_market_universe_v1"),
            payload.get("price_freshness_rule"),
            (payload.get("hashes") or {}).get("policy"),
            tuple(payload.get("markets") or []),
            payload.get("book"),
            payload.get("entry_hours"),
            payload.get("max_quote_age"),
        )
        for payload in (parent_payload, fit_payload, hold_payload)
    }
    if len(contracts) != 1:
        raise ValueError("parent/fit/holdout market contracts are not identical")

    fit_dates = pd.to_datetime(fit.official_game_date, errors="raise")
    holdout_dates = pd.to_datetime(holdout.official_game_date, errors="raise")
    if fit.empty or holdout.empty:
        raise ValueError("fit and holdout must both be non-empty")
    if not (fit_dates < boundary).all():
        raise ValueError("fit contains a date on or after holdout_start")
    if not (holdout_dates >= boundary).all():
        raise ValueError("holdout contains a date before holdout_start")

    fit_keys = set(map(tuple, fit[identity_key].to_numpy()))
    holdout_keys = set(map(tuple, holdout[identity_key].to_numpy()))
    if fit_keys & holdout_keys:
        raise ValueError("fit and holdout chronological source identities overlap")
    union = pd.concat([fit, holdout], ignore_index=True)
    if not _sorted_full(parent, identity_key).equals(_sorted_full(union, identity_key)):
        raise ValueError(
            "fit + holdout do not reproduce the parent strict artifact exactly; "
            "a key or evaluator-consumed value drifted"
        )

    def arm(role: str, manifest_path: str | Path, payload: dict[str, Any],
            artifact_path: Path, frame: pd.DataFrame) -> dict[str, Any]:
        dates = payload["official_date_universe"]
        return {
            "role": role,
            "manifest": _repo_path(manifest_path),
            "manifest_sha256": sha256(manifest_path),
            "artifact": _repo_path(artifact_path),
            "artifact_sha256": sha256(artifact_path),
            "rows": int(len(frame)),
            "dates": int(len(dates)),
            "first_date": dates[0],
            "last_date": dates[-1],
        }

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "LOCKED_RESEARCH_ONLY",
        "locked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market": "hits",
        "book": parent_payload.get("book"),
        "artifact_kind": next(iter(artifact_kinds)),
        "identity_key": identity_key,
        "holdout_start": rendered_boundary,
        "split_basis": (
            "Outcome-blind calendar boundary: all certified rows before 2026-05-01 "
            "are fit; all certified rows on/after 2026-05-01 are holdout."
        ),
        "parent": {
            "manifest": _repo_path(parent_manifest),
            "manifest_sha256": sha256(parent_manifest),
            "artifact": _repo_path(parent_artifact),
            "artifact_sha256": sha256(parent_artifact),
            "rows": int(len(parent)),
            "dates": int(parent.official_game_date.nunique()),
        },
        "arms": {
            "fit": arm("fit", fit_manifest, fit_payload, fit_artifact, fit),
            "holdout": arm(
                "holdout", holdout_manifest, hold_payload, hold_artifact, holdout
            ),
        },
        "invariants": {
            "outcomes_read_to_choose_split": False,
            "model_outputs_read_to_choose_split": False,
            "fit_and_holdout_source_identities_disjoint": True,
            "fit_plus_holdout_full_values_equal_parent": True,
            "holdout_may_be_evaluated_once_after_policy_lock": True,
            "june_is_exploratory_not_fresh_holdout": True,
        },
        "parameter_contract": {
            "max_quote_age": (
                "The fit source is uncensored by max_quote_age. Freshness may be "
                "fitted on the earlier arm only, then locked before the holdout is "
                "opened. Duplicate MARKET_KEY exclusion occurs after each candidate "
                "freshness rule."
                if parent_payload.get("artifact_kind") == POLICY_SOURCE_KIND else
                "The current artifacts are conditional on the inherited 90-minute "
                "placeholder. They cannot be used to justify 90 because rows older "
                "than 90 were already removed. Structural provider evidence or a new "
                "pre-cutoff evidence artifact is required."
            ),
            "min_edge": (
                "Selection method must be committed before fit outcomes are read; it "
                "may use fit only, then is frozen for the one-shot holdout."
            ),
            "min_bets_for_capture": (
                "Must come from interval precision/power, never a round inherited "
                "count or the most favorable observed result."
            ),
        },
        "authorization": {
            "betting_authorized": False,
            "reason": (
                "Chronological locking prevents leakage; it does not validate the "
                "placeholder policy, prove a held-out effect, or replace forward shadow."
            ),
        },
    }

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    digest = sha256(destination)
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        digest + "\n", encoding="utf-8"
    )
    payload["protocol_sha256"] = digest
    return payload


def verify_protocol_role(
    protocol_path: str | Path,
    *,
    role: str,
    market_manifest: str | Path,
) -> dict[str, Any]:
    """Prove a reconstruction manifest is the role locked by the protocol."""
    if role not in ROLES:
        raise ValueError(f"unknown chronological role {role!r}; expected {ROLES}")
    protocol_file = Path(protocol_path).resolve()
    payload = json.loads(protocol_file.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA or payload.get("status") != "LOCKED_RESEARCH_ONLY":
        raise ValueError("chronological protocol schema/status is not recognized")
    sidecar = protocol_file.with_suffix(protocol_file.suffix + ".sha256")
    expected_protocol_hash = sidecar.read_text(encoding="utf-8").strip()
    actual_protocol_hash = sha256(protocol_file)
    if actual_protocol_hash != expected_protocol_hash:
        raise ValueError("chronological protocol hash mismatch")

    arm = (payload.get("arms") or {}).get(role)
    if not isinstance(arm, dict):
        raise ValueError(f"chronological protocol is missing role {role!r}")
    supplied_manifest = Path(market_manifest).resolve()
    if sha256(supplied_manifest) != arm.get("manifest_sha256"):
        raise ValueError(
            f"supplied market manifest is not the protocol's locked {role} manifest"
        )
    recorded_manifest = _resolve_recorded(str(arm.get("manifest"))).resolve()
    if supplied_manifest != recorded_manifest:
        raise ValueError(
            f"locked {role} manifest path differs: {supplied_manifest} != "
            f"{recorded_manifest}"
        )
    manifest_payload, frame, artifact = _read_market_manifest(supplied_manifest)
    if sha256(artifact) != arm.get("artifact_sha256"):
        raise ValueError(f"locked {role} artifact hash mismatch")
    if len(frame) != arm.get("rows"):
        raise ValueError(f"locked {role} artifact row count mismatch")
    dates = manifest_payload.get("official_date_universe") or []
    if len(dates) != arm.get("dates"):
        raise ValueError(f"locked {role} date count mismatch")
    return {
        "role": role,
        "protocol_path": str(protocol_file),
        "protocol_sha256": actual_protocol_hash,
        "market_manifest_sha256": arm["manifest_sha256"],
        "market_artifact_sha256": arm["artifact_sha256"],
        "rows": arm["rows"],
        "dates": arm["dates"],
    }
