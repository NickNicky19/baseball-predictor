"""
Baseball Savant / Statcast data client.

Loads Statcast metrics from pybaseball or a local Savant CSV export and
produces StatcastProfile objects. Missing values fall back to LeagueBaselines.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import math
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.data.statcast_batted_ball_rates import (
    barrel_rate as derive_barrel_rate,
    hard_hit_rate as derive_hard_hit_rate,
)
from src.data.statcast_integrity import derive_batted_ball_evidence, validate_rate_pair
from src.data.statcast_source_contract import (
    StatcastSourceSchemaError,
    StatcastSourceUnavailableError,
    statcast_frame_content_sha256,
    validate_statcast_source_lineage,
)
from src.models.dataclasses import LeagueBaselines, PitcherStatcastProfile, StatcastProfile
from src.utils.logging import get_logger
from src.utils.provenance import sha256_json

logger = get_logger(__name__)

HITTER_LEAGUE_FALLBACK_FIELDS = (
    "barrel_rate",
    "bb_rate",
    "chase_rate",
    "contact_rate",
    "hard_hit_rate",
    "k_rate",
    "sweet_spot_rate",
    "swing_rate",
    "whiff_rate",
    "xba",
    "xslg",
    "xwoba",
    "zone_rate",
)

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
        if pyb is None:
            raise StatcastSourceUnavailableError(
                "pybaseball is unavailable; refusing to substitute league averages"
            )

        end = date.fromisoformat(end_date or date.today().isoformat())
        days = lookback_days or self.lookback_days
        start = end - timedelta(days=days)

        logger.info("Fetching Statcast %s to %s", start.isoformat(), end.isoformat())
        try:
            df = pyb.statcast(start_dt=start.isoformat(), end_dt=end.isoformat())
        except Exception as exc:
            raise StatcastSourceUnavailableError(
                f"Statcast fetch failed for {start.isoformat()}..{end.isoformat()}"
            ) from exc

        if df is None:
            raise StatcastSourceUnavailableError(
                f"Statcast returned no rows for {start.isoformat()}..{end.isoformat()}"
            )
        if not isinstance(df, pd.DataFrame):
            raise StatcastSourceSchemaError(
                f"Statcast returned {type(df).__name__}, expected DataFrame"
            )
        if df.empty:
            raise StatcastSourceUnavailableError(
                f"Statcast returned no rows for {start.isoformat()}..{end.isoformat()}"
            )
        return df

    def load_savant_csv(self, csv_path: str | Path) -> pd.DataFrame:
        """Load a Baseball Savant CSV export."""
        path = Path(csv_path)
        if not path.exists():
            raise StatcastSourceUnavailableError(f"Savant CSV not found: {path}")
        try:
            df = pd.read_csv(path)
        except Exception as exc:
            raise StatcastSourceUnavailableError(
                f"Savant CSV could not be read: {path}"
            ) from exc
        if df.empty:
            raise StatcastSourceUnavailableError(f"Savant CSV contains no rows: {path}")
        return df

    def build_hitter_profiles_from_statcast(
        self, statcast_df: pd.DataFrame
    ) -> dict[int, StatcastProfile]:
        """Aggregate pitch-level Statcast data into per-batter profiles."""
        if statcast_df.empty:
            return {}
        missing = {"batter", "events"} - set(statcast_df.columns)
        if missing:
            raise StatcastSourceSchemaError(
                "pitch-level Statcast payload missing required columns: "
                + ", ".join(sorted(missing))
            )

        df = statcast_df.copy()
        terminal = df.loc[df["events"].notna() & df["batter"].notna()].copy()
        if terminal.empty:
            raise StatcastSourceSchemaError(
                "pitch-level Statcast payload contains no terminal batter events"
            )

        # A Statcast payload is pitch-level.  ``events`` is populated only on
        # the terminal pitch of a PA, so qualification and sample_pa use those
        # rows while pitch-denominator features must retain the full sequence.
        pa_counts = terminal.groupby("batter").size()
        qualified = pa_counts[pa_counts >= self.min_pa].index
        df = df.loc[df["batter"].notna() & df["batter"].isin(qualified)].copy()
        if df.empty:
            return {}

        profiles: dict[int, StatcastProfile] = {}
        for batter_id, group in df.groupby("batter"):
            player_id = int(batter_id)
            # Statcast ``player_name`` is the pitcher on pitch-level rows.  A
            # batter name is resolved later from the independently keyed slate.
            profile = self._aggregate_hitter_group(group, player_id, "")
            expected_pa = int(pa_counts.loc[batter_id])
            if profile.sample_pa != expected_pa:
                raise StatcastSourceSchemaError(
                    f"batter {player_id} PA count changed between qualification "
                    f"and aggregation: {expected_pa} != {profile.sample_pa}"
                )
            profiles[player_id] = self.apply_league_fallback(profile)
        return profiles

    def build_hitter_profiles_from_csv(
        self, csv_path: str | Path, *, target_date: str | None = None
    ) -> dict[int, StatcastProfile]:
        """Build profiles from a Savant CSV (player-level or pitch-level)."""
        if target_date is None:
            raise StatcastSourceSchemaError(
                "Savant CSV profile construction requires an explicit target_date"
            )
        try:
            target = date.fromisoformat(target_date)
        except (TypeError, ValueError) as exc:
            raise StatcastSourceSchemaError(
                "Savant CSV target_date must be an ISO date"
            ) from exc
        df = self.load_savant_csv(csv_path)
        if "batter" in df.columns and "events" in df.columns:
            source_window_end = None
            if "game_date" not in df.columns:
                raise StatcastSourceSchemaError(
                    "pitch-level Savant CSV lacks game_date for strict-prior use"
                )
            parsed = pd.to_datetime(df["game_date"], errors="coerce")
            if bool(df["game_date"].isna().any()) or bool(
                (df["game_date"].notna() & parsed.isna()).any()
            ):
                raise StatcastSourceSchemaError(
                    "pitch-level Savant CSV contains an invalid game_date"
                )
            cutoff = pd.Timestamp(target)
            df = df.loc[parsed < cutoff].copy()
            parsed = parsed.loc[df.index]
            if df.empty:
                raise StatcastSourceUnavailableError(
                    "pitch-level Savant CSV contains no strict-prior rows"
                )
            source_window_end = parsed.max().date().isoformat()
            profiles = self.build_hitter_profiles_from_statcast(df)
            return {
                player_id: replace(
                    profile,
                    source_kind="savant_pitch_csv",
                    source_window_end=source_window_end,
                )
                for player_id, profile in profiles.items()
            }

        source_window_end = None
        if "source_window_end" not in df.columns:
            raise StatcastSourceSchemaError(
                "player-level Savant CSV lacks source_window_end for strict-prior use"
            )
        parsed = pd.to_datetime(df["source_window_end"], errors="coerce")
        if bool(df["source_window_end"].isna().any()) or bool(
            (df["source_window_end"].notna() & parsed.isna()).any()
        ):
            raise StatcastSourceSchemaError(
                "player-level Savant CSV contains an invalid source_window_end"
            )
        unique = parsed.dropna().dt.date.unique()
        if len(unique) != 1:
            raise StatcastSourceSchemaError(
                "player-level Savant CSV must have one source_window_end"
            )
        if unique[0] >= target:
            raise StatcastSourceSchemaError(
                "player-level Savant CSV source_window_end is not strict-prior"
            )
        source_window_end = unique[0].isoformat()

        return self._profiles_from_player_level_csv(
            df, source_window_end=source_window_end
        )

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
        profile = StatcastProfile(
            player_id=player_id,
            player_name=player_name,
            sample_pa=0,
            xwoba=lg.xwoba,
            xba=lg.xba_on_contact,
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
            source_kind="league_baseline",
            source_status="league_fallback_no_player_profile",
            source_row_count=0,
            source_content_sha256=sha256_json(
                {
                    "artifact_version": "league-statcast-fallback-v1",
                    "values": {
                        "xwoba": lg.xwoba,
                        "xba": lg.xba_on_contact,
                        "xslg": lg.xslg,
                        "barrel_rate": lg.barrel_rate,
                        "sweet_spot_rate": lg.sweet_spot_rate,
                        "hard_hit_rate": lg.hard_hit_rate,
                        "chase_rate": lg.chase_rate,
                        "contact_rate": lg.contact_rate,
                        "whiff_rate": lg.whiff_rate,
                        "swing_rate": lg.swing_rate,
                        "zone_rate": lg.zone_rate,
                        "k_rate": lg.k_pct / 100.0,
                        "bb_rate": lg.bb_pct / 100.0,
                    },
                }
            ),
            fallback_fields=HITTER_LEAGUE_FALLBACK_FIELDS,
        )
        validate_statcast_source_lineage(
            profile,
            context=f"SavantClient.league_fallback_profile[{player_id}]",
        )
        return profile

    def apply_league_fallback(self, profile: StatcastProfile) -> StatcastProfile:
        """Fill any missing metric with the corresponding league baseline."""
        validate_rate_pair(
            profile.barrel_rate,
            profile.hard_hit_rate,
            context=f"StatcastProfile[{profile.player_id}] before fallback",
        )
        lg = self.league
        fallback_values = {
            "xwoba": lg.xwoba,
            "xba": lg.xba_on_contact,
            "xslg": lg.xslg,
            "barrel_rate": lg.barrel_rate,
            "sweet_spot_rate": lg.sweet_spot_rate,
            "hard_hit_rate": lg.hard_hit_rate,
            "chase_rate": lg.chase_rate,
            "contact_rate": lg.contact_rate,
            "whiff_rate": lg.whiff_rate,
            "swing_rate": lg.swing_rate,
            "zone_rate": lg.zone_rate,
            "k_rate": lg.k_pct / 100.0,
            "bb_rate": lg.bb_pct / 100.0,
        }
        newly_fallback = {
            field for field in fallback_values if getattr(profile, field) is None
        }
        fallback_fields = tuple(sorted(set(profile.fallback_fields) | newly_fallback))
        if profile.source_status == "untracked_legacy":
            source_status = "untracked_legacy"
        elif profile.source_status == "league_fallback_no_player_profile":
            source_status = profile.source_status
        else:
            source_status = (
                "observed_with_field_fallback" if fallback_fields else "observed_complete"
            )
        enriched = replace(
            profile,
            **{
                field: getattr(profile, field)
                if getattr(profile, field) is not None
                else value
                for field, value in fallback_values.items()
            },
            source_status=source_status,
            fallback_fields=fallback_fields,
        )
        validate_statcast_source_lineage(
            enriched,
            context=f"SavantClient.apply_league_fallback[{profile.player_id}]",
        )
        return enriched

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
        if "events" not in group.columns:
            raise StatcastSourceSchemaError(
                f"batter {player_id} group lacks terminal-event evidence"
            )
        pa = int(group["events"].notna().sum())
        if pa <= 0:
            raise StatcastSourceSchemaError(
                f"batter {player_id} group contains no terminal PA"
            )

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
            source_kind="pybaseball_statcast",
            source_status="observed_complete",
            source_row_count=len(group),
            source_content_sha256=statcast_frame_content_sha256(group),
        )

    def _profiles_from_player_level_csv(
        self, df: pd.DataFrame, *, source_window_end: str | None = None
    ) -> dict[int, StatcastProfile]:
        """Parse a player-level Savant export (one row per hitter)."""
        id_col = _first_present(df.columns, ["player_id", "batter", "id"])
        name_col = _first_present(df.columns, ["player_name", "last_name, first_name", "name"])
        if id_col is None:
            raise StatcastSourceSchemaError(
                "player-level Savant CSV has no player identity column"
            )

        profiles: dict[int, StatcastProfile] = {}
        for row_number, row in df.iterrows():
            player_id = _required_nonnegative_int(
                row.get(id_col),
                context=f"player-level Savant CSV row {row_number} player identity",
                positive=True,
            )
            if player_id in profiles:
                raise StatcastSourceSchemaError(
                    f"player-level Savant CSV has duplicate player_id {player_id}"
                )
            name_value = row.get(name_col) if name_col else None
            name = "" if pd.isna(name_value) else str(name_value)
            sample_pa = _required_nonnegative_int(
                row.get("pa"),
                context=f"player-level Savant CSV[{player_id}] pa",
            )
            barrel, hard_hit, denominator, barrel_count, hard_hit_count = (
                _parse_player_summary_batted_ball(row, player_id=player_id)
            )
            profile = StatcastProfile(
                player_id=player_id,
                player_name=name,
                sample_pa=sample_pa,
                xwoba=_optional_finite_float(
                    row.get("xwoba"),
                    context=f"player-level Savant CSV[{player_id}] xwoba",
                ),
                xba=_optional_finite_float(
                    row.get("xba"),
                    context=f"player-level Savant CSV[{player_id}] xba",
                ),
                xslg=_optional_finite_float(
                    row.get("xslg"),
                    context=f"player-level Savant CSV[{player_id}] xslg",
                ),
                barrel_rate=barrel,
                sweet_spot_rate=_player_summary_percent(
                    row.get("sweet_spot_percent"),
                    context=f"player-level Savant CSV[{player_id}] sweet_spot_percent",
                ),
                hard_hit_rate=hard_hit,
                batted_ball_denominator=denominator,
                barrel_count=barrel_count,
                hard_hit_count=hard_hit_count,
                batted_ball_rate_definition=(
                    "savant_player_summary_counts_common_bbe"
                    if denominator is not None
                    else None
                ),
                whiff_rate=_player_summary_percent(
                    row.get("whiff_percent"),
                    context=f"player-level Savant CSV[{player_id}] whiff_percent",
                ),
                chase_rate=_player_summary_percent(
                    row.get("chase_percent"),
                    context=f"player-level Savant CSV[{player_id}] chase_percent",
                ),
                contact_rate=_player_summary_percent(
                    row.get("contact_percent"),
                    context=f"player-level Savant CSV[{player_id}] contact_percent",
                ),
                swing_rate=_player_summary_percent(
                    row.get("swing_percent"),
                    context=f"player-level Savant CSV[{player_id}] swing_percent",
                ),
                zone_rate=_player_summary_percent(
                    row.get("zone_percent"),
                    context=f"player-level Savant CSV[{player_id}] zone_percent",
                ),
                source_kind="savant_player_csv",
                source_status="observed_complete",
                source_window_end=source_window_end,
                source_row_count=1,
                source_content_sha256=statcast_frame_content_sha256(
                    df.loc[[row_number]]
                ),
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
        zone = SavantClient._validated_zone(group)
        if zone is None:
            return None
        outside = zone.isin([11, 12, 13, 14])
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
        zone = SavantClient._validated_zone(group)
        if zone is None:
            return None
        classified = zone.notna()
        denominator = int(classified.sum())
        if denominator == 0:
            return None
        in_zone = zone.between(1, 9)
        return float(in_zone.sum() / denominator)

    @staticmethod
    def _validated_zone(group: pd.DataFrame) -> Optional[pd.Series]:
        if "zone" not in group.columns:
            return None
        raw = group["zone"]
        zone = pd.to_numeric(raw, errors="coerce")
        if bool((raw.notna() & zone.isna()).any()):
            raise StatcastSourceSchemaError(
                "Statcast zone contains a nonnumeric nonmissing value"
            )
        if bool((zone.notna() & ~zone.between(1, 14)).any()):
            raise StatcastSourceSchemaError(
                "Statcast zone contains a value outside classified zones 1-14"
            )
        return zone


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


def resolve_configured_savant_csv(
    config: dict[str, Any], *, root: Path
) -> Optional[str]:
    """Resolve an explicitly configured Savant CSV without hiding source loss."""
    savant_cfg = config.get("savant", {})
    csv_path = savant_cfg.get("csv_path")
    if csv_path in (None, ""):
        return None
    if not isinstance(csv_path, str):
        raise StatcastSourceSchemaError("savant.csv_path must be a string")
    path = Path(csv_path)
    if not path.is_absolute():
        path = root / path
    if not path.exists() or not path.is_file():
        raise StatcastSourceUnavailableError(
            f"configured Savant CSV is unavailable: {path}"
        )
    return str(path)


def _optional_finite_float(value: Any, *, context: str) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise StatcastSourceSchemaError(f"{context} is not numeric") from exc
    if not math.isfinite(parsed):
        raise StatcastSourceSchemaError(f"{context} is non-finite")
    return parsed


def _required_nonnegative_int(
    value: Any, *, context: str, positive: bool = False
) -> int:
    parsed = _optional_finite_float(value, context=context)
    if parsed is None or not parsed.is_integer():
        raise StatcastSourceSchemaError(f"{context} must be an integer")
    integer = int(parsed)
    minimum = 1 if positive else 0
    if integer < minimum:
        raise StatcastSourceSchemaError(f"{context} must be >= {minimum}")
    return integer


def _player_summary_percent(value: Any, *, context: str) -> Optional[float]:
    parsed = _optional_finite_float(value, context=context)
    if parsed is None:
        return None
    if not 0.0 <= parsed <= 100.0:
        raise StatcastSourceSchemaError(f"{context} is outside [0,100]")
    return parsed / 100.0


def _one_present_column(row: pd.Series, candidates: tuple[str, ...]) -> Optional[str]:
    present = [
        name
        for name in candidates
        if name in row.index and not pd.isna(row.get(name))
    ]
    if len(present) > 1:
        raise StatcastSourceSchemaError(
            "player-level Savant CSV has ambiguous aliases: " + ", ".join(present)
        )
    return present[0] if present else None


def _parse_player_summary_batted_ball(
    row: pd.Series, *, player_id: int
) -> tuple[
    Optional[float], Optional[float], Optional[int], Optional[int], Optional[int]
]:
    context = f"player-level Savant CSV[{player_id}]"
    if "barrel_rate" in row.index and not pd.isna(row.get("barrel_rate")):
        raise StatcastSourceSchemaError(
            f"{context}: ambiguous barrel_rate alias is not accepted"
        )
    names = {
        "denominator": _one_present_column(row, ("batted_ball", "batted_balls")),
        "barrel_count": _one_present_column(row, ("barrel", "barrels")),
        "hard_hit_count": _one_present_column(row, ("hard_hit", "hard_hits")),
        "barrel_rate": _one_present_column(row, ("barrel_batted_rate",)),
        "hard_hit_rate": _one_present_column(row, ("hard_hit_percent",)),
    }
    if all(value is None for value in names.values()):
        return None, None, None, None, None
    missing = [key for key, value in names.items() if value is None]
    if missing:
        raise StatcastSourceSchemaError(
            f"{context}: partial batted-ball count/rate contract: "
            + ", ".join(missing)
        )
    denominator = _required_nonnegative_int(
        row[names["denominator"]], context=f"{context} batted_ball"
    )
    barrel_count = _required_nonnegative_int(
        row[names["barrel_count"]], context=f"{context} barrel"
    )
    hard_hit_count = _required_nonnegative_int(
        row[names["hard_hit_count"]], context=f"{context} hard_hit"
    )
    if (
        denominator == 0
        or barrel_count > hard_hit_count
        or hard_hit_count > denominator
    ):
        raise StatcastSourceSchemaError(
            f"{context}: impossible batted-ball count ordering"
        )
    reported_barrel = _player_summary_percent(
        row[names["barrel_rate"]], context=f"{context} barrel_batted_rate"
    )
    reported_hard_hit = _player_summary_percent(
        row[names["hard_hit_rate"]], context=f"{context} hard_hit_percent"
    )
    barrel = barrel_count / denominator
    hard_hit = hard_hit_count / denominator
    # Savant leaderboard rates are displayed to one decimal percentage point.
    tolerance = 0.0005000001
    if (
        reported_barrel is None
        or reported_hard_hit is None
        or abs(reported_barrel - barrel) > tolerance
        or abs(reported_hard_hit - hard_hit) > tolerance
    ):
        raise StatcastSourceSchemaError(
            f"{context}: displayed rates disagree with common-denominator counts"
        )
    validate_rate_pair(barrel, hard_hit, context=context, allow_both_missing=False)
    return barrel, hard_hit, denominator, barrel_count, hard_hit_count
