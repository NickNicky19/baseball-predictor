"""Point-in-time hitter-contact adapter selected on pre-2026 evidence.

This module is candidate-only.  With no explicit configuration, callers keep
the existing production path unchanged.  It never reads outcomes and it
refuses target-date Statcast rows rather than silently filtering leakage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_COLUMNS = {"batter", "game_date", "estimated_ba_using_speedangle"}
REQUIRED_CONFIG_KEYS = {
    "enabled",
    "candidate_id",
    "market",
    "window_games",
    "prior_strength_bip",
    "minimum_player_bip",
    "selection_evidence_path",
    "selection_evidence_sha256",
    "forbidden_date_start",
    "forbidden_date_end",
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class HitsContactAdapterSettings:
    candidate_id: str
    market: str
    window_games: int
    prior_strength_bip: float
    minimum_player_bip: int
    selection_evidence_path: Path
    selection_evidence_sha256: str
    forbidden_date_start: date
    forbidden_date_end: date

    @classmethod
    def from_config(
        cls, config: dict[str, Any]
    ) -> "HitsContactAdapterSettings | None":
        block = (config.get("feature_factory", {}) or {}).get(
            "hits_contact_adapter"
        )
        if block is None:
            return None
        if not isinstance(block, dict):
            raise ValueError("hits_contact_adapter must be an object")
        if block.get("enabled") is not True:
            return None
        missing = sorted(REQUIRED_CONFIG_KEYS - set(block))
        unknown = sorted(set(block) - REQUIRED_CONFIG_KEYS)
        if missing or unknown:
            raise ValueError(
                "hits_contact_adapter has invalid keys: "
                f"missing={missing}, unknown={unknown}"
            )

        candidate_id = str(block["candidate_id"])
        market = str(block["market"])
        window_games = int(block["window_games"])
        prior_strength = float(block["prior_strength_bip"])
        minimum_bip = int(block["minimum_player_bip"])
        evidence_path = Path(str(block["selection_evidence_path"]))
        if not evidence_path.is_absolute():
            evidence_path = (PROJECT_ROOT / evidence_path).resolve()
        expected_sha = str(block["selection_evidence_sha256"]).lower()
        forbidden_start = date.fromisoformat(str(block["forbidden_date_start"]))
        forbidden_end = date.fromisoformat(str(block["forbidden_date_end"]))

        if candidate_id != "hits_point_in_time_hitter_contact_adapter_v1":
            raise ValueError("unexpected hits-contact candidate_id")
        if market != "hits":
            raise ValueError("hits-contact adapter is scoped only to hits")
        if window_games != 30:
            raise ValueError("window_games must equal the pre-2026 selection: 30")
        if prior_strength != 135.0:
            raise ValueError(
                "prior_strength_bip must equal the pre-2026 selection: 135"
            )
        if minimum_bip != 1:
            raise ValueError("minimum_player_bip must equal the locked value: 1")
        if forbidden_end < forbidden_start:
            raise ValueError("forbidden date interval is reversed")
        if not evidence_path.is_file():
            raise FileNotFoundError(
                f"hits-contact selection evidence is missing: {evidence_path}"
            )
        actual_sha = sha256_path(evidence_path)
        if actual_sha != expected_sha:
            raise ValueError(
                "hits-contact selection-evidence hash mismatch: "
                f"expected {expected_sha}, got {actual_sha}"
            )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        selected = evidence.get("selector_fit") or {}
        if (
            evidence.get("production_candidate_supported") is not True
            or int(selected.get("chosen_recency_window_games", -1)) != window_games
            or float(selected.get("fitted_prior_strength_bip", -1.0))
            != prior_strength
        ):
            raise ValueError(
                "selection evidence does not authorize the configured experiment"
            )
        return cls(
            candidate_id=candidate_id,
            market=market,
            window_games=window_games,
            prior_strength_bip=prior_strength,
            minimum_player_bip=minimum_bip,
            selection_evidence_path=evidence_path,
            selection_evidence_sha256=expected_sha,
            forbidden_date_start=forbidden_start,
            forbidden_date_end=forbidden_end,
        )

    def assert_target_allowed(self, target_date: str) -> date:
        target = date.fromisoformat(target_date)
        if self.forbidden_date_start <= target <= self.forbidden_date_end:
            raise ValueError(
                f"target date {target} is in the sealed interval; refusing to read it"
            )
        return target


def build_contact_adapter_evidence(
    statcast_df: pd.DataFrame,
    *,
    active_player_ids: Iterable[int],
    target_date: str,
    settings: HitsContactAdapterSettings,
) -> dict[int, dict[str, Any]]:
    """Return one exhaustive, factual adapter status for every active hitter."""
    target = settings.assert_target_allowed(target_date)
    active = sorted({int(player_id) for player_id in active_player_ids})
    if not active:
        return {}
    missing = sorted(REQUIRED_COLUMNS - set(statcast_df.columns))
    if missing:
        raise ValueError(f"Statcast frame lacks adapter columns: {missing}")

    frame = statcast_df.loc[:, sorted(REQUIRED_COLUMNS)].copy()
    frame["batter"] = pd.to_numeric(frame["batter"], errors="coerce")
    if frame["batter"].isna().any():
        raise ValueError("Statcast batter identity contains null/non-numeric values")
    frame["batter"] = frame["batter"].astype("int64")
    frame["game_date"] = pd.to_datetime(
        frame["game_date"], errors="coerce"
    ).dt.date
    if frame["game_date"].isna().any():
        raise ValueError("Statcast game_date contains invalid values")
    contaminated = frame["game_date"] >= target
    if contaminated.any():
        sample = frame.loc[contaminated, ["batter", "game_date"]].head(5)
        raise ValueError(
            "target/future Statcast rows entered the point-in-time adapter: "
            + sample.to_dict(orient="records").__repr__()
        )
    frame["xba"] = pd.to_numeric(
        frame["estimated_ba_using_speedangle"], errors="coerce"
    )
    invalid = frame["xba"].notna() & ~frame["xba"].between(0.0, 1.0)
    if invalid.any():
        raise ValueError("Statcast estimated BA lies outside [0,1]")
    frame = frame.loc[frame["batter"].isin(active) & frame["xba"].notna()].copy()

    raw: dict[int, tuple[int, float, str, str, str]] = {}
    for player_id, group in frame.groupby("batter", sort=True):
        dates = sorted(group["game_date"].unique())
        selected_dates = dates[-settings.window_games :]
        selected = group.loc[group["game_date"].isin(selected_dates)]
        bip = int(selected["xba"].notna().sum())
        if bip < settings.minimum_player_bip:
            continue
        player_xba = float(selected["xba"].mean())
        raw[int(player_id)] = (
            bip,
            player_xba,
            selected_dates[0].isoformat(),
            selected_dates[-1].isoformat(),
            _contact_source_hash(selected),
        )

    total_bip = sum(values[0] for values in raw.values())
    anchor = (
        sum(values[0] * values[1] for values in raw.values()) / total_bip
        if total_bip > 0
        else None
    )
    if anchor is not None and not (0.0 <= anchor <= 1.0):
        raise ValueError("daily contact-xBA anchor lies outside [0,1]")

    evidence: dict[int, dict[str, Any]] = {}
    for player_id in active:
        values = raw.get(player_id)
        if values is None:
            evidence[player_id] = {
                "status": "baseline_fallback",
                "reason": "no_valid_player_bip",
                "candidate_id": settings.candidate_id,
                "window_games": settings.window_games,
                "player_bip": 0,
            }
            continue
        if anchor is None:
            evidence[player_id] = {
                "status": "baseline_fallback",
                "reason": "no_daily_anchor",
                "candidate_id": settings.candidate_id,
                "window_games": settings.window_games,
                "player_bip": values[0],
            }
            continue
        bip, raw_xba, first_date, last_date, source_hash = values
        fitted = (
            bip * raw_xba + settings.prior_strength_bip * anchor
        ) / (bip + settings.prior_strength_bip)
        if not np.isfinite(fitted) or not 0.0 <= fitted <= 1.0:
            raise ValueError("fitted contact xBA is invalid")
        evidence[player_id] = {
            "status": "adapter_applied",
            "reason": None,
            "candidate_id": settings.candidate_id,
            "window_games": settings.window_games,
            "prior_strength_bip": settings.prior_strength_bip,
            "player_bip": bip,
            "raw_player_xba": raw_xba,
            "daily_anchor_xba": float(anchor),
            "fitted_contact_xba": float(fitted),
            "first_evidence_date": first_date,
            "last_evidence_date": last_date,
            "target_date": target.isoformat(),
            "source_cutoff_date": (target - timedelta(days=1)).isoformat(),
            "source_hash": source_hash,
            "selection_evidence_sha256": settings.selection_evidence_sha256,
        }
    if set(evidence) != set(active):
        raise RuntimeError("contact adapter failed exhaustive active-player accounting")
    return evidence


def _contact_source_hash(frame: pd.DataFrame) -> str:
    rows = []
    for row in frame.loc[:, ["batter", "game_date", "xba"]].to_dict(
        orient="records"
    ):
        rows.append(
            json.dumps(
                {
                    "batter": int(row["batter"]),
                    "game_date": row["game_date"].isoformat(),
                    "xba": float(row["xba"]),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return hashlib.sha256(("[" + ",".join(sorted(rows)) + "]").encode()).hexdigest()


def consume_fitted_contact_xba(
    *,
    fitted_contact_xba: Any,
    legacy_xba_shrunk: float,
) -> float:
    """Use an already-shrunk candidate value exactly once, else the legacy value."""
    if fitted_contact_xba is None:
        return float(legacy_xba_shrunk)
    fitted = float(fitted_contact_xba)
    if not np.isfinite(fitted) or not 0.0 <= fitted <= 1.0:
        raise ValueError("contact_xba_fitted must be a finite probability")
    return fitted
