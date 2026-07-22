"""
Baseball Savant / Statcast data client.

Loads Statcast metrics from pybaseball or a local Savant CSV export and
produces StatcastProfile objects. Any league substitution is bound to explicit
field-level lineage on the profile consumed downstream.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.data.statcast_batted_ball_rates import (
    barrel_rate as derive_barrel_rate,
    hard_hit_rate as derive_hard_hit_rate,
)
from src.data.statcast_integrity import (
    STATCAST_LEAGUE_FALLBACK_FIELDS,
    derive_batted_ball_evidence,
    validate_rate_pair,
)
from src.models.dataclasses import LeagueBaselines, PitcherStatcastProfile, StatcastProfile
from src.utils.errors import DataFetchError
from src.utils.logging import get_logger

logger = get_logger(__name__)


_REQUIRED_PITCH_LEVEL_COLUMNS = {
    "batter",
    "game_date",
    "events",
    "type",
    "description",
    "zone",
    "launch_speed",
    "launch_angle",
    "launch_speed_angle",
    "estimated_woba_using_speedangle",
    "estimated_ba_using_speedangle",
    "estimated_slg_using_speedangle",
}

try:
    import pybaseball as pyb

    pyb.cache.enable()
except ImportError:
    pyb = None


class SavantClient:
    """
    Fetches and normalizes Statcast/Savant data into StatcastProfile objects.

    Does not inject arbitrary player-level defaults; league averages are used
    only through apply_league_fallback().
    """

    def __init__(
        self,
        league_baselines: Optional[LeagueBaselines] = None,
        lookback_days: int = 45,
        min_pa: int = 8,
        derive_batted_ball_rates: bool = False,
    ):
        self.league = league_baselines or LeagueBaselines()
        self.lookback_days = lookback_days
        self.min_pa = min_pa
        self.derive_batted_ball_rates = bool(derive_batted_ball_rates)

    def fetch_statcast_range(
        self,
        end_date: Optional[str] = None,
        lookback_days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Fetch raw Statcast pitch-level data for a date range."""
        end = date.fromisoformat(end_date or date.today().isoformat())
        days = self.lookback_days if lookback_days is None else lookback_days
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
            raise ValueError("Statcast lookback_days must be a positive integer")
        start = end - timedelta(days=days)
        sealed_start = date(2026, 5, 1)
        sealed_end = date(2026, 5, 31)
        if start <= sealed_end and end >= sealed_start:
            raise DataFetchError(
                "Statcast request intersects sealed May 2026; source access refused"
            )
        if pyb is None:
            raise DataFetchError(
                "Statcast source unavailable: pybaseball is not installed"
            )

        logger.info("Fetching Statcast %s to %s", start.isoformat(), end.isoformat())
        try:
            df = pyb.statcast(start_dt=start.isoformat(), end_dt=end.isoformat())
        except Exception as exc:
            raise DataFetchError(
                f"Statcast source request failed for {start.isoformat()} through "
                f"{end.isoformat()}"
            ) from exc

        if not isinstance(df, pd.DataFrame):
            raise DataFetchError("Statcast source returned a non-tabular payload")
        if df.empty:
            raise DataFetchError(
                f"Statcast source returned no rows for {start.isoformat()} through "
                f"{end.isoformat()}"
            )
        identity_fields = {"batter", "game_date"}
        missing = sorted(identity_fields.difference(df.columns))
        if missing:
            raise DataFetchError(f"Statcast source is missing identity fields: {missing}")
        missing = sorted(_REQUIRED_PITCH_LEVEL_COLUMNS.difference(df.columns))
        if missing:
            raise DataFetchError(f"Statcast source is missing required fields: {missing}")
        return df

    def load_savant_csv(self, csv_path: str | Path) -> pd.DataFrame:
        """Load a Baseball Savant CSV export."""
        path = Path(csv_path)
        if not path.exists():
            raise DataFetchError(f"Savant CSV source does not exist: {path}")
        frame = pd.read_csv(path)
        if frame.empty:
            raise DataFetchError(f"Savant CSV source is empty: {path}")
        return frame

    def build_hitter_profiles_from_statcast(
        self, statcast_df: pd.DataFrame
    ) -> dict[int, StatcastProfile]:
        """Aggregate pitch-level Statcast data into per-batter profiles."""
        if not isinstance(statcast_df, pd.DataFrame) or statcast_df.empty:
            raise DataFetchError("cannot build hitter profiles from an empty Statcast source")
        if "batter" not in statcast_df.columns:
            raise DataFetchError("Statcast hitter source is missing batter identity")

        df = statcast_df.copy()
        if "events" in df.columns:
            df = df[df["events"].notna()]

        pa_counts = df.groupby("batter").size()
        qualified = pa_counts[pa_counts >= self.min_pa].index
        df = df[df["batter"].isin(qualified)]
        if df.empty:
            return {}

        profiles: dict[int, StatcastProfile] = {}
        for batter_id, group in df.groupby("batter"):
            player_id = int(batter_id)
            name = str(group["player_name"].iloc[0]) if "player_name" in group.columns else ""
            profile = self._aggregate_hitter_group(group, player_id, name)
            source_max_game_date = _max_game_date(group)
            profile = replace(
                profile,
                source_hash=_canonical_frame_sha256(group),
                source_max_game_date=source_max_game_date,
                source_cutoff_date=source_max_game_date,
            )
            profiles[player_id] = self.apply_league_fallback(profile)
        return profiles

    def build_hitter_profiles_from_csv(
        self, csv_path: str | Path
    ) -> dict[int, StatcastProfile]:
        """Build profiles from a Savant CSV (player-level or pitch-level)."""
        df = self.load_savant_csv(csv_path)
        if df.empty:
            return {}

        if "batter" in df.columns:
            return self.build_hitter_profiles_from_statcast(df)

        return self._profiles_from_player_level_csv(df)

    def get_hitter_profile(
        self,
        player_id: int,
        player_name: str,
        profiles: dict[int, StatcastProfile],
    ) -> StatcastProfile:
        """Return a profile for a player, applying league fallback if missing."""
        if player_id in profiles:
            return profiles[player_id]
        return self.league_fallback_profile(player_id, player_name)

    def league_fallback_profile(self, player_id: int, player_name: str) -> StatcastProfile:
        """Create a full profile using league-average values (no advanced sample)."""
        lg = self.league
        return StatcastProfile(
            player_id=player_id,
            player_name=player_name,
            sample_pa=0,
            xwoba=lg.xwoba,
            xba=None,
            xslg=lg.xslg,
            barrel_rate=lg.barrel_rate,
            sweet_spot_rate=lg.sweet_spot_rate,
            hard_hit_rate=lg.hard_hit_rate,
            chase_rate=lg.chase_rate,
            contact_rate=lg.contact_rate,
            whiff_rate=lg.whiff_rate,
            swing_rate=lg.swing_rate,
            zone_rate=lg.zone_rate,
            k_rate=lg.k_pct / 100.0,
            bb_rate=lg.bb_pct / 100.0,
            source_status="league_fallback",
            fallback_fields=tuple(sorted(STATCAST_LEAGUE_FALLBACK_FIELDS)),
        )

    def apply_league_fallback(self, profile: StatcastProfile) -> StatcastProfile:
        """Fill missing metrics while binding every substitution to the profile."""
        validate_rate_pair(
            profile.barrel_rate,
            profile.hard_hit_rate,
            context=f"StatcastProfile[{profile.player_id}] before fallback",
        )
        lg = self.league
        newly_fallback = {
            field
            for field in STATCAST_LEAGUE_FALLBACK_FIELDS
            if getattr(profile, field) is None
        }
        fallback_fields = tuple(sorted(set(profile.fallback_fields) | newly_fallback))
        if profile.sample_pa <= 0:
            source_status = "league_fallback"
        elif fallback_fields:
            source_status = "partial_league_fallback"
        else:
            source_status = "observed"
        return replace(
            profile,
            xwoba=profile.xwoba if profile.xwoba is not None else lg.xwoba,
            xslg=profile.xslg if profile.xslg is not None else lg.xslg,
            barrel_rate=profile.barrel_rate if profile.barrel_rate is not None else lg.barrel_rate,
            sweet_spot_rate=(
                profile.sweet_spot_rate if profile.sweet_spot_rate is not None else lg.sweet_spot_rate
            ),
            hard_hit_rate=(
                profile.hard_hit_rate if profile.hard_hit_rate is not None else lg.hard_hit_rate
            ),
            chase_rate=profile.chase_rate if profile.chase_rate is not None else lg.chase_rate,
            contact_rate=(
                profile.contact_rate if profile.contact_rate is not None else lg.contact_rate
            ),
            whiff_rate=profile.whiff_rate if profile.whiff_rate is not None else lg.whiff_rate,
            swing_rate=profile.swing_rate if profile.swing_rate is not None else lg.swing_rate,
            zone_rate=profile.zone_rate if profile.zone_rate is not None else lg.zone_rate,
            k_rate=profile.k_rate if profile.k_rate is not None else lg.k_pct / 100.0,
            bb_rate=profile.bb_rate if profile.bb_rate is not None else lg.bb_pct / 100.0,
            source_status=source_status,
            fallback_fields=fallback_fields,
        )

    def build_pitcher_profile_from_rates(
        self,
        player_id: int,
        player_name: str,
        k_pct: float,
        bb_pct: float,
        hr_per_9: float,
        sample_pa: int = 0,
    ) -> PitcherStatcastProfile:
        """Build a pitcher profile from MLB rate stats (Statcast pitcher pull in Phase 3)."""
        return PitcherStatcastProfile(
            player_id=player_id,
            player_name=player_name,
            sample_pa=sample_pa,
            k_rate=k_pct / 100.0,
            bb_rate=bb_pct / 100.0,
            hr_per_9=hr_per_9,
        )

    def _aggregate_hitter_group(
        self, group: pd.DataFrame, player_id: int, player_name: str
    ) -> StatcastProfile:
        pa = len(group)

        def _mean(col: str) -> Optional[float]:
            if col not in group.columns:
                return None
            series = pd.to_numeric(group[col], errors="coerce").dropna()
            return float(series.mean()) if not series.empty else None

        def _rate(col: str) -> Optional[float]:
            val = _mean(col)
            if val is None:
                return None
            return _normalize_rate(val)

        # Sparse source-level ``barrel``/``hard_hit`` columns do not establish
        # compatible denominators.  Prefer a single count-bearing derivation
        # whenever raw batted-ball evidence is available, regardless of the
        # legacy candidate flag.  The flag remains only for version identity.
        evidence = derive_batted_ball_evidence(group)
        if evidence is not None:
            barrel = evidence.barrel_rate
            hard_hit = evidence.hard_hit_rate
        else:
            barrel = _rate("barrel")
            hard_hit = _rate("hard_hit")
            validate_rate_pair(
                barrel,
                hard_hit,
                context=f"Savant aggregate fallback[{player_id}]",
            )

        return StatcastProfile(
            player_id=player_id,
            player_name=player_name,
            sample_pa=pa,
            xwoba=_mean("estimated_woba_using_speedangle"),
            xba=_mean("estimated_ba_using_speedangle"),
            xslg=_mean("estimated_slg_using_speedangle"),
            barrel_rate=barrel,
            sweet_spot_rate=_rate("sweet_spot_percent") if "sweet_spot_percent" in group.columns else None,
            hard_hit_rate=hard_hit,
            batted_ball_denominator=(
                evidence.measured_batted_balls if evidence is not None else None
            ),
            barrel_count=evidence.barrel_count if evidence is not None else None,
            hard_hit_count=evidence.hard_hit_count if evidence is not None else None,
            batted_ball_rate_definition=(
                evidence.classification if evidence is not None else None
            ),
            avg_exit_velocity=_mean("launch_speed"),
            avg_launch_angle=_mean("launch_angle"),
            whiff_rate=self._compute_whiff_rate(group),
            chase_rate=self._compute_chase_rate(group),
            contact_rate=self._compute_contact_rate(group),
            swing_rate=self._compute_swing_rate(group),
            zone_rate=self._compute_zone_rate(group),
        )

    def _profiles_from_player_level_csv(self, df: pd.DataFrame) -> dict[int, StatcastProfile]:
        """Parse a player-level Savant export (one row per hitter)."""
        id_col = _first_present(df.columns, ["player_id", "batter", "id"])
        name_col = _first_present(df.columns, ["player_name", "last_name, first_name", "name"])
        if id_col is None:
            raise DataFetchError("player-level Savant CSV is missing player identity")

        profiles: dict[int, StatcastProfile] = {}
        for _, row in df.iterrows():
            player_id = int(row[id_col])
            name = str(row[name_col]) if name_col else ""
            profile = StatcastProfile(
                player_id=player_id,
                player_name=name,
                sample_pa=_safe_int(row.get("pa")),
                xwoba=_safe_float(row.get("xwoba")),
                xba=_safe_float(row.get("xba")),
                xslg=_safe_float(row.get("xslg")),
                barrel_rate=_normalize_rate(_safe_float(row.get("barrel_rate") or row.get("barrel_batted_rate"))),
                sweet_spot_rate=_normalize_rate(_safe_float(row.get("sweet_spot_percent"))),
                hard_hit_rate=_normalize_rate(_safe_float(row.get("hard_hit_percent"))),
                whiff_rate=_normalize_rate(_safe_float(row.get("whiff_percent"))),
                chase_rate=_normalize_rate(_safe_float(row.get("chase_percent"))),
                contact_rate=_normalize_rate(_safe_float(row.get("contact_percent"))),
                swing_rate=_normalize_rate(_safe_float(row.get("swing_percent"))),
                zone_rate=_normalize_rate(_safe_float(row.get("zone_percent"))),
                source_hash=_canonical_frame_sha256(pd.DataFrame([row])),
            )
            validate_rate_pair(
                profile.barrel_rate,
                profile.hard_hit_rate,
                context=f"player-level Savant CSV[{player_id}]",
            )
            profiles[player_id] = self.apply_league_fallback(profile)
        return profiles

    @staticmethod
    def _compute_whiff_rate(group: pd.DataFrame) -> Optional[float]:
        if "description" not in group.columns:
            return None
        swings = group["description"].isin(
            ["swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "hit_into_play"]
        )
        whiffs = group["description"].isin(["swinging_strike", "swinging_strike_blocked"])
        swing_count = swings.sum()
        if swing_count == 0:
            return None
        return float(whiffs.sum() / swing_count)

    @staticmethod
    def _compute_swing_rate(group: pd.DataFrame) -> Optional[float]:
        if "description" not in group.columns:
            return None
        swings = group["description"].isin(
            ["swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "hit_into_play"]
        )
        return float(swings.sum() / len(group)) if len(group) else None

    @staticmethod
    def _compute_contact_rate(group: pd.DataFrame) -> Optional[float]:
        if "description" not in group.columns:
            return None
        swings = group["description"].isin(
            ["swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "hit_into_play"]
        )
        contact = group["description"].isin(["foul", "foul_tip", "hit_into_play"])
        swing_count = swings.sum()
        if swing_count == 0:
            return None
        return float(contact.sum() / swing_count)

    @staticmethod
    def _compute_chase_rate(group: pd.DataFrame) -> Optional[float]:
        if "description" not in group.columns or "zone" not in group.columns:
            return None
        outside = group["zone"].isin([11, 12, 13, 14])
        chased = outside & group["description"].isin(
            ["swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "hit_into_play"]
        )
        outside_count = outside.sum()
        if outside_count == 0:
            return None
        return float(chased.sum() / outside_count)

    @staticmethod
    def _compute_zone_rate(group: pd.DataFrame) -> Optional[float]:
        if "zone" not in group.columns:
            return None
        in_zone = group["zone"].between(1, 9)
        return float(in_zone.sum() / len(group)) if len(group) else None


def _canonical_frame_sha256(frame: pd.DataFrame) -> str:
    """Hash the exact source rows consumed, independent of row/column order."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise DataFetchError("cannot hash an empty Statcast source frame")
    ordered = frame.reindex(sorted(frame.columns, key=str), axis=1)
    records: list[str] = []
    for record in ordered.to_dict(orient="records"):
        normalized = {
            str(key): _canonical_scalar(value) for key, value in record.items()
        }
        records.append(
            json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        )
    payload = "[" + ",".join(sorted(records)) + "]"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_scalar(value: Any) -> Any:
    if value is None or bool(pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _max_game_date(frame: pd.DataFrame) -> Optional[str]:
    if "game_date" not in frame.columns:
        return None
    parsed = pd.to_datetime(frame["game_date"], errors="coerce")
    if parsed.isna().any() or parsed.empty:
        raise DataFetchError("Statcast source contains an invalid game_date")
    return parsed.max().date().isoformat()


def _normalize_rate(value: Optional[float]) -> Optional[float]:
    """Convert percentage-scale values (0–100) to fractions (0–1)."""
    if value is None:
        return None
    if value > 1.0:
        return value / 100.0
    return value


def _first_present(columns: Any, candidates: list[str]) -> Optional[str]:
    for col in candidates:
        if col in columns:
            return col
    return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0
