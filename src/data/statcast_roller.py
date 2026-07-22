"""
A4 — Point-in-time rolling Statcast features (the main signal upgrade).

For any (player_id, as_of_date), computes rolling xwOBA / exit velocity /
barrel / hard-hit / whiff etc. over that hitter's most recent N GAMES that
occurred STRICTLY BEFORE as_of_date. Windows are game-based (roll15, roll30),
not calendar-based, because "last 15 games" is the baseball-meaningful unit
and its calendar span varies per player.

Why this is the signal A3 lacked: A3's rows carry season-to-date counting
stats (as-of AVG/OBP/SLG) but no batted-ball quality. Rolling xwOBA/EV/barrel
are the features that actually separate a hot process from a lucky line, and
are leakage-safe here because every pitch used is from a game before the row's
date.

Leakage discipline (mirrors A2/A3, canary-tested):
- One Statcast pull per (player, season) over a generous calendar range,
  disk-cached forever (historical pitch data is immutable). For a target
  date we slice the cached frame to game_date < as_of_date, then take the
  last N distinct game_dates. No pitch from the target date or later can
  enter a feature.
- Contact-conditional aggregation matches the simulator's convention: xwOBA
  and exit velo are means over batted-ball events; barrel/hard-hit are rates
  over the same. This is deliberately the same math savant.SavantClient uses
  so A4 features and live profiles are measured on one ruler.

This module is ADDITIVE and OFFLINE-ONLY: it reads pybaseball + a disk cache
and writes feature rows. It never touches the live model, archives, or pairs.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.data.statcast_batted_ball_rates import (
    barrel_rate as derive_barrel_rate,
    hard_hit_rate as derive_hard_hit_rate,
)

from src.utils.logging import get_logger

logger = get_logger(__name__)

ROLLER_SCHEMA = "a4.1"

# Statcast batted-ball / plate-discipline columns we roll. Kept close to
# SavantClient._aggregate_hitter_group so features match live profiles.
_MEAN_COLS = {
    "roll_xwoba": "estimated_woba_using_speedangle",
    "roll_xba": "estimated_ba_using_speedangle",
    "roll_xslg": "estimated_slg_using_speedangle",
    "roll_ev": "launch_speed",
    "roll_la": "launch_angle",
}
_RATE_COLS = {
    "roll_barrel_rate": "barrel",
    "roll_hardhit_rate": "hard_hit",
}

SWING_DESCRIPTIONS = {
    "swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "hit_into_play",
}
WHIFF_DESCRIPTIONS = {"swinging_strike", "swinging_strike_blocked"}

# The full set of feature column names A4 emits per window (roll15_*, roll30_*).
_WINDOW_FEATURES = list(_MEAN_COLS) + list(_RATE_COLS) + ["roll_whiff_rate", "roll_bip"]


def rolling_feature_columns(windows: tuple[int, ...] = (15, 30)) -> list[str]:
    """Column names A4 adds, e.g. roll15_xwoba, roll30_barrel_rate, ..."""
    cols: list[str] = []
    for w in windows:
        cols.append(f"roll{w}_games")
        for feat in _WINDOW_FEATURES:
            cols.append(feat.replace("roll_", f"roll{w}_"))
    return cols


@dataclass
class RollerConfig:
    windows: tuple[int, ...] = (15, 30)
    # Calendar span pulled per player-season. 30 games rarely exceeds ~50
    # calendar days; 210 covers a full season so one cached pull serves every
    # date. Whole-season pull is cheaper than many overlapping range pulls.
    season_pull_days: int = 210
    min_bip: int = 5  # below this a window's rates are too noisy -> emitted blank
    rate_limit_seconds: float = 2.0  # pybaseball is heavy; be a good citizen


class StatcastRoller:
    """
    Per-player, point-in-time rolling Statcast aggregator with a disk cache.

    fetch_fn is injectable so offline tests can supply canned Statcast frames
    without pybaseball or network. In production it defaults to pybaseball's
    per-player statcast_batter pull.
    """

    def __init__(
        self,
        cache_dir: str | Path = "data/cache/statcast",
        config: Optional[RollerConfig] = None,
        fetch_fn: Optional[Any] = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or RollerConfig()
        self._fetch_fn = fetch_fn or self._pybaseball_fetch
        self._mem: dict[tuple[int, int], pd.DataFrame] = {}
        self._last_fetch = 0.0
        self.cache_hits = 0
        self.cache_misses = 0
        self.fetch_calls = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rolling_features(self, player_id: int, as_of_date: str) -> dict[str, Any]:
        """
        Rolling features for one player as of a date. Every value is derived
        only from games strictly before as_of_date. Missing/blank when the
        player has too few batted balls in a window.
        """
        season = int(as_of_date[:4])
        frame = self._player_season_frame(player_id, season)
        out: dict[str, Any] = {}
        if frame.empty or "game_date" not in frame.columns:
            for col in rolling_feature_columns(self.config.windows):
                out[col] = ""
            return out

        before = frame[frame["game_date"] < as_of_date]
        game_days = sorted(before["game_date"].unique())

        for w in self.config.windows:
            recent_days = set(game_days[-w:])
            window_df = before[before["game_date"].isin(recent_days)]
            out.update(self._aggregate_window(window_df, w, len(recent_days)))
        return out

    def join_onto_rows(
        self, rows: list[dict[str, Any]], id_key: str = "player_id", date_key: str = "game_date"
    ) -> list[dict[str, Any]]:
        """Attach rolling features to A3 rows in place (returns the same list)."""
        for row in rows:
            pid = row.get(id_key)
            gdate = row.get(date_key)
            if pid in (None, "") or not gdate:
                row.update({c: "" for c in rolling_feature_columns(self.config.windows)})
                continue
            row.update(self.rolling_features(int(pid), str(gdate)))
        return rows

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def _aggregate_window(self, df: pd.DataFrame, window: int, n_games: int) -> dict[str, Any]:
        prefix = f"roll{window}_"
        out: dict[str, Any] = {f"{prefix}games": n_games}

        # Batted-ball events: rows where the ball was put in play.
        bip = df
        if "type" in df.columns:
            bip = df[df["type"] == "X"]
        elif "description" in df.columns:
            bip = df[df["description"] == "hit_into_play"]
        n_bip = len(bip)
        out[f"{prefix}bip"] = n_bip

        blank = n_bip < self.config.min_bip
        for feat, col in _MEAN_COLS.items():
            out[f"{prefix}{feat[5:]}"] = "" if blank else _mean(bip, col)
        # Barrel and hard-hit aren't native columns in the statcast_batter
        # feed — derive them. Hard-hit = EV >= 95 mph. Barrel = Statcast's
        # launch_speed_angle bucket 6 (falls back to a launch-speed/angle
        # approximation if that column is absent).
        out[f"{prefix}barrel_rate"] = "" if blank else _barrel_rate(bip)
        out[f"{prefix}hardhit_rate"] = "" if blank else _hardhit_rate(bip)

        # Whiff rate uses all pitches in the window (not just BiP).
        out[f"{prefix}whiff_rate"] = "" if df.empty else _whiff_rate(df)
        return out

    # ------------------------------------------------------------------
    # Fetch + cache (one immutable pull per player-season)
    # ------------------------------------------------------------------

    def _player_season_frame(self, player_id: int, season: int) -> pd.DataFrame:
        key = (player_id, season)
        if key in self._mem:
            return self._mem[key]

        path = self._cache_path(player_id, season)
        if path.exists():
            try:
                frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
                self.cache_hits += 1
                self._mem[key] = frame
                return frame
            except Exception:
                logger.warning("Corrupt statcast cache %s; refetching", path.name)
                path.unlink(missing_ok=True)

        self.cache_misses += 1
        frame = self._fetch_player_season(player_id, season)
        tmp = path.with_suffix(".tmp")
        try:
            frame.to_csv(tmp, index=False)
            tmp.replace(path)
        except Exception as exc:
            logger.warning("Failed to cache statcast for %s/%s: %s", player_id, season, exc)
        self._mem[key] = frame
        return frame

    def _fetch_player_season(self, player_id: int, season: int) -> pd.DataFrame:
        start = f"{season}-03-01"
        end = f"{season}-11-15"
        self._respect_rate_limit()
        self.fetch_calls += 1
        try:
            frame = self._fetch_fn(player_id, start, end)
        except Exception as exc:
            logger.warning("Statcast fetch failed for %s (%s): %s", player_id, season, exc)
            return pd.DataFrame()
        if frame is None or frame.empty:
            return pd.DataFrame()
        # Normalize game_date to ISO strings for reliable < comparison.
        if "game_date" in frame.columns:
            frame = frame.copy()
            frame["game_date"] = pd.to_datetime(frame["game_date"]).dt.strftime("%Y-%m-%d")
        return frame

    @staticmethod
    def _pybaseball_fetch(player_id: int, start: str, end: str) -> pd.DataFrame:
        try:
            import pybaseball as pyb

            pyb.cache.enable()
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "pybaseball is required for A4 Statcast features. "
                "Install it: pip install pybaseball"
            ) from exc
        return pyb.statcast_batter(start, end, player_id)

    def _respect_rate_limit(self) -> None:
        interval = self.config.rate_limit_seconds
        if interval <= 0:
            return
        wait = interval - (time.monotonic() - self._last_fetch)
        if wait > 0:
            time.sleep(wait)
        self._last_fetch = time.monotonic()

    def _cache_path(self, player_id: int, season: int) -> Path:
        sub = self.cache_dir / str(season)
        sub.mkdir(parents=True, exist_ok=True)
        return sub / f"batter_{player_id}.csv"

    def cache_stats(self) -> dict[str, int]:
        return {"hits": self.cache_hits, "misses": self.cache_misses, "fetches": self.fetch_calls}


# ---------------------------------------------------------------------------
# Column-level helpers (blank -> "" so CSV stays clean, GBMs read NaN)
# ---------------------------------------------------------------------------


def _mean(df: pd.DataFrame, col: str) -> Any:
    if col not in df.columns:
        return ""
    series = pd.to_numeric(df[col], errors="coerce").dropna()
    return round(float(series.mean()), 4) if not series.empty else ""


def _rate(df: pd.DataFrame, col: str) -> Any:
    if col not in df.columns:
        return ""
    series = pd.to_numeric(df[col], errors="coerce").dropna()
    if series.empty:
        return ""
    return round(float(series.mean()), 4)


def _whiff_rate(df: pd.DataFrame) -> Any:
    if "description" not in df.columns:
        return ""
    desc = df["description"]
    swings = desc.isin(SWING_DESCRIPTIONS).sum()
    if swings == 0:
        return ""
    whiffs = desc.isin(WHIFF_DESCRIPTIONS).sum()
    return round(float(whiffs / swings), 4)


def _hardhit_rate(bip: pd.DataFrame) -> Any:
    """Hard-hit = batted balls at >= 95 mph exit velocity, over all BiP."""
    value = derive_hard_hit_rate(bip)
    return "" if value is None else round(value, 4)


def _barrel_rate(bip: pd.DataFrame) -> Any:
    """
    Barrel rate over batted balls. Prefers Statcast's own classification:
    a pre-computed `barrel` column if present, else launch_speed_angle == 6
    (Statcast's barrel bucket). Falls back to the public barrel definition
    (EV >= 98 with a launch-angle band that widens with EV) when neither
    Statcast field is available.
    """
    value = derive_barrel_rate(bip)
    return "" if value is None else round(value, 4)
