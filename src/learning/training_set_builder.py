"""
A3 — Point-in-time training-set builder (2023-2025 player-game rows).

Extends A2's one-date reconstruction into a multi-season builder that emits
RAW, model-agnostic feature rows + observed outcomes — the Phase-B training
table. Raw on purpose: the live model's engineered FeatureVector bakes in
current config coefficients; B1's GBMs should learn from signals, not from
this config's transforms, and the simulator stays the baseline to beat.

Leakage policy (same as A2, canary-tested):
- All player stats via PointInTimeStats: game-log rows strictly BEFORE the
  game date. Doubleheader game 2 therefore excludes game 1 of the same day
  (conservative, safe).
- No Savant CSV anywhere (as-of-NOW snapshot). A4 joins per-game Statcast
  rolling features onto these rows later, keyed by (player_id, game_date).
- Lineups, starters, umpire, weather come from the completed game's own
  feed — facts of that day, pre-game-knowable, not leakage.

One cached feed call per game supplies lineups + boxscore outcomes +
officials + weather. Pitcher rows use the ACTUAL starter (pitchers[0] in
the boxscore) rather than schedule probables, which may not persist
historically; hitters' opposing-SP feature uses the probable when present
and falls back to the actual starter (source is recorded per row).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from src.data.mlb_api import (
    HittingStatsSnapshot,
    MLBStatsAPI,
    PitchingStatsSnapshot,
    _parse_hitting,
    _parse_pitching,
)
from src.data.http_cache import DiskCachedGetMixin
from src.data.point_in_time import PointInTimeStats
from src.utils.logging import get_logger

logger = get_logger(__name__)

BUILDER_SCHEMA = "a3.1"

# Generous windows; the gameType == "R" filter is the real gate.
SEASON_WINDOWS: dict[int, tuple[str, str]] = {
    2023: ("2023-03-15", "2023-10-05"),
    2024: ("2024-03-15", "2024-10-05"),
    2025: ("2025-03-15", "2025-10-05"),
}

HITTER_COLUMNS = [
    # identity / context
    "builder_schema", "season", "game_date", "game_pk", "player_id", "player_name",
    "team", "opponent", "venue", "is_home", "lineup_slot", "bats",
    # as-of season (strictly pre-date)
    "pit_games", "pit_pa", "pit_ab", "pit_hits", "pit_doubles", "pit_triples",
    "pit_hr", "pit_rbi", "pit_runs", "pit_bb", "pit_k",
    "pit_avg", "pit_obp", "pit_slg",
    # as-of recent window (last 15 games pre-date)
    "recent_pa", "recent_avg", "recent_obp", "recent_slg", "recent_hr", "recent_k", "recent_bb",
    # rolling rates (PointInTimeStats.rolling_features)
    "recent_pa_15", "roll15_k_rate", "roll15_bb_rate", "roll15_babip",
    "recent_pa_30", "roll30_k_rate", "roll30_bb_rate", "roll30_babip",
    # opposing starter (as-of)
    "opp_sp_id", "opp_sp_name", "opp_sp_throws", "opp_sp_source",
    "opp_sp_ip", "opp_sp_k9", "opp_sp_bb9", "opp_sp_hr9", "opp_sp_gs",
    "opp_sp_recent_ip", "opp_sp_recent_k9", "opp_sp_recent_bb9", "opp_sp_recent_hr9",
    "platoon_adv",
    # environment
    "park_hits_factor", "park_hr_factor", "park_runs_factor", "park_resolved",
    "weather_temp", "weather_wind", "weather_is_dome", "weather_resolved",
    "umpire_id", "umpire_resolved",
    # flags
    "has_prior_data",
    # outcomes (this game)
    "out_pa", "out_ab", "out_hits", "out_doubles", "out_triples", "out_hr",
    "out_rbi", "out_runs", "out_bb", "out_k",
]

PITCHER_COLUMNS = [
    "builder_schema", "season", "game_date", "game_pk", "player_id", "player_name",
    "team", "opponent", "venue", "is_home", "throws",
    "pit_ip", "pit_k", "pit_bb", "pit_hr", "pit_k9", "pit_bb9", "pit_hr9", "pit_gs",
    "pit_recent_ip", "pit_recent_k9", "pit_recent_bb9", "pit_recent_hr9",
    "pit_recent_gs", "pit_recent_ip_per_gs",
    "park_hits_factor", "park_hr_factor", "park_runs_factor", "park_resolved",
    "weather_temp", "weather_wind", "weather_is_dome", "weather_resolved",
    "umpire_id", "umpire_resolved",
    "has_prior_data",
    "out_ip", "out_k", "out_bb", "out_hr",
]


class CachedMLBAPI(DiskCachedGetMixin, MLBStatsAPI):
    """Disk-cached, rate-limited MLB API for immutable historical lookups."""


@dataclass
class DateResult:
    game_date: str
    games: int = 0
    hitter_rows: int = 0
    pitcher_rows: int = 0
    skipped_non_regular: int = 0
    skipped_not_final: int = 0
    resolution: Counter = field(default_factory=Counter)


class TrainingSetBuilder:
    """
    Builds per-date shards of hitter/pitcher training rows with a manifest
    for resume, then assembles shards into season-range CSVs.
    """

    def __init__(
        self,
        api: CachedMLBAPI,
        pit: PointInTimeStats,
        config: dict[str, Any],
        out_dir: str | Path = "data/training",
    ):
        self.api = api
        self.pit = pit
        self.config = config or {}
        self.out_dir = Path(out_dir)
        self.season = api.season
        self._manifest_path = self.out_dir / f"manifest_{self.season}.json"
        self._shard_dir = self.out_dir / str(self.season)
        self._shard_dir.mkdir(parents=True, exist_ok=True)
        self.manifest: dict[str, Any] = self._load_manifest()

    # ------------------------------------------------------------------
    # Public driver
    # ------------------------------------------------------------------

    def build_dates(
        self,
        dates: Iterable[str],
        rebuild: bool = False,
        limit: Optional[int] = None,
    ) -> list[DateResult]:
        results: list[DateResult] = []
        built = 0
        for game_date in dates:
            if limit is not None and built >= limit:
                break
            entry = self.manifest.get("dates", {}).get(game_date)
            if entry and entry.get("status") in ("done", "empty") and not rebuild:
                continue
            result = self.build_one_date(game_date)
            self._record(result)
            results.append(result)
            built += 1
            logger.info(
                "%s: %d games -> %d hitter rows, %d pitcher rows (cache %s)",
                game_date,
                result.games,
                result.hitter_rows,
                result.pitcher_rows,
                self.api.cache_stats(),
            )
        return results

    def build_one_date(self, game_date: str) -> DateResult:
        result = DateResult(game_date=game_date)
        hitter_rows: list[dict[str, Any]] = []
        pitcher_rows: list[dict[str, Any]] = []

        for game in self.api.get_schedule(game_date):
            game_type = game.get("gameType")
            if game_type is not None and game_type != "R":
                result.skipped_non_regular += 1
                continue
            status = game.get("status", {})
            if not (
                status.get("abstractGameState") == "Final"
                or status.get("codedGameState") == "F"
            ):
                result.skipped_not_final += 1
                continue

            try:
                feed = self.api._get(
                    f"{self.api.FEED_URL}/game/{int(game['gamePk'])}/feed/live"
                )
            except Exception as exc:
                logger.warning("Feed unavailable for game %s: %s", game.get("gamePk"), exc)
                result.resolution["feed_failed"] += 1
                continue

            h_rows, p_rows = self._rows_for_game(game_date, game, feed, result.resolution)
            hitter_rows.extend(h_rows)
            pitcher_rows.extend(p_rows)
            result.games += 1

        self._write_shard("hitters", game_date, HITTER_COLUMNS, hitter_rows)
        self._write_shard("pitchers", game_date, PITCHER_COLUMNS, pitcher_rows)
        result.hitter_rows = len(hitter_rows)
        result.pitcher_rows = len(pitcher_rows)
        return result

    def assemble(self, seasons: list[int], out_dir: Optional[Path] = None) -> dict[str, Path]:
        """Concatenate all shards for the given seasons into two CSV.gz files."""
        import pandas as pd

        out_dir = out_dir or self.out_dir
        span = f"{min(seasons)}_{max(seasons)}" if len(seasons) > 1 else str(seasons[0])
        outputs: dict[str, Path] = {}
        for kind in ("hitters", "pitchers"):
            frames = []
            for season in seasons:
                for shard in sorted((out_dir / str(season)).glob(f"{kind}_*.csv")):
                    frames.append(pd.read_csv(shard))
            target = out_dir / f"training_{kind}_{span}.csv.gz"
            if frames:
                pd.concat(frames, ignore_index=True).to_csv(target, index=False)
                outputs[kind] = target
        return outputs

    # ------------------------------------------------------------------
    # Row building
    # ------------------------------------------------------------------

    def _rows_for_game(
        self,
        game_date: str,
        game: dict[str, Any],
        feed: dict[str, Any],
        resolution: Counter,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        game_pk = int(game["gamePk"])
        venue = game.get("venue", {}).get("name", "Unknown")
        away_name = game["teams"]["away"]["team"]["name"]
        home_name = game["teams"]["home"]["team"]["name"]

        box_teams = feed.get("liveData", {}).get("boxscore", {}).get("teams", {})
        env = self._environment(venue, feed, resolution)

        # Actual starters per side (pitchers[0] in the boxscore listing).
        starters: dict[str, Optional[int]] = {}
        for side in ("away", "home"):
            plist = box_teams.get(side, {}).get("pitchers", []) or []
            starters[side] = int(plist[0]) if plist else None

        hitter_rows: list[dict[str, Any]] = []
        pitcher_rows: list[dict[str, Any]] = []

        for side, team, opp, is_home in (
            ("away", away_name, home_name, 0),
            ("home", home_name, away_name, 1),
        ):
            opp_side = "home" if side == "away" else "away"
            players = box_teams.get(side, {}).get("players", {}) or {}
            order = [int(pid) for pid in (box_teams.get(side, {}).get("battingOrder") or []) if pid][:9]
            if len(order) < 9:
                resolution["lineup_missing_team"] += 1
            else:
                resolution["lineup_confirmed_team"] += 1

            opp_sp_id, opp_sp_source = self._opposing_sp(
                game, opp_side, starters[opp_side], resolution
            )

            for slot, pid in enumerate(order, start=1):
                row = self._hitter_row(
                    game_date, game_pk, pid, team, opp, venue, is_home, slot,
                    opp_sp_id, opp_sp_source, env, players, resolution,
                )
                hitter_rows.append(row)

            sp_id = starters[side]
            if sp_id is None:
                resolution["starter_missing"] += 1
            else:
                pitcher_rows.append(
                    self._pitcher_row(
                        game_date, game_pk, sp_id, team, opp, venue, is_home,
                        env, players, resolution,
                    )
                )

        return hitter_rows, pitcher_rows

    def _hitter_row(
        self,
        game_date: str,
        game_pk: int,
        player_id: int,
        team: str,
        opp: str,
        venue: str,
        is_home: int,
        slot: int,
        opp_sp_id: Optional[int],
        opp_sp_source: str,
        env: dict[str, Any],
        players: dict[str, Any],
        resolution: Counter,
    ) -> dict[str, Any]:
        identity = self.api.get_player_identity(player_id, team=team)
        season, recent = self.pit.get_hitting_stats_as_of(player_id, game_date)
        rolling = self.pit.rolling_features(player_id, game_date)
        resolution["hitting_as_of_resolved" if season.pa > 0 else "hitting_as_of_empty"] += 1

        sp_season, sp_recent, sp_name, sp_throws = self._sp_as_of(
            opp_sp_id, game_date, opp, resolution
        )
        platoon_adv = None
        if sp_throws in ("L", "R") and identity.bats in ("L", "R", "S"):
            platoon_adv = 1 if (identity.bats == "S" or identity.bats != sp_throws) else 0

        out = self._hitting_actual(players, player_id)
        if out is None:
            resolution["hitter_actual_missing"] += 1
            out = HittingStatsSnapshot()
        else:
            resolution["hitter_actual_resolved"] += 1

        return {
            "builder_schema": BUILDER_SCHEMA,
            "season": self.season,
            "game_date": game_date,
            "game_pk": game_pk,
            "player_id": player_id,
            "player_name": identity.name,
            "team": team,
            "opponent": opp,
            "venue": venue,
            "is_home": is_home,
            "lineup_slot": slot,
            "bats": identity.bats,
            "pit_games": season.games, "pit_pa": season.pa, "pit_ab": season.ab,
            "pit_hits": season.hits, "pit_doubles": season.doubles,
            "pit_triples": season.triples, "pit_hr": season.home_runs,
            "pit_rbi": season.rbi, "pit_runs": season.runs,
            "pit_bb": season.walks, "pit_k": season.strikeouts,
            "pit_avg": round(season.avg, 4), "pit_obp": round(season.obp, 4),
            "pit_slg": round(season.slg, 4),
            "recent_pa": recent.pa, "recent_avg": round(recent.avg, 4),
            "recent_obp": round(recent.obp, 4), "recent_slg": round(recent.slg, 4),
            "recent_hr": recent.home_runs, "recent_k": recent.strikeouts,
            "recent_bb": recent.walks,
            "recent_pa_15": rolling.get("recent_pa_15", 0),
            "roll15_k_rate": _round(rolling.get("roll15_k_rate")),
            "roll15_bb_rate": _round(rolling.get("roll15_bb_rate")),
            "roll15_babip": _round(rolling.get("roll15_babip")),
            "recent_pa_30": rolling.get("recent_pa_30", 0),
            "roll30_k_rate": _round(rolling.get("roll30_k_rate")),
            "roll30_bb_rate": _round(rolling.get("roll30_bb_rate")),
            "roll30_babip": _round(rolling.get("roll30_babip")),
            "opp_sp_id": opp_sp_id, "opp_sp_name": sp_name,
            "opp_sp_throws": sp_throws, "opp_sp_source": opp_sp_source,
            "opp_sp_ip": round(sp_season.innings_pitched, 1),
            "opp_sp_k9": round(sp_season.k_per_9, 2),
            "opp_sp_bb9": round(sp_season.bb_per_9, 2),
            "opp_sp_hr9": round(sp_season.hr_per_9, 2),
            "opp_sp_gs": sp_season.games_started,
            "opp_sp_recent_ip": round(sp_recent.innings_pitched, 1),
            "opp_sp_recent_k9": round(sp_recent.k_per_9, 2),
            "opp_sp_recent_bb9": round(sp_recent.bb_per_9, 2),
            "opp_sp_recent_hr9": round(sp_recent.hr_per_9, 2),
            "platoon_adv": platoon_adv,
            **env,
            "has_prior_data": 1 if season.pa > 0 else 0,
            "out_pa": out.pa, "out_ab": out.ab, "out_hits": out.hits,
            "out_doubles": out.doubles, "out_triples": out.triples,
            "out_hr": out.home_runs, "out_rbi": out.rbi, "out_runs": out.runs,
            "out_bb": out.walks, "out_k": out.strikeouts,
        }

    def _pitcher_row(
        self,
        game_date: str,
        game_pk: int,
        player_id: int,
        team: str,
        opp: str,
        venue: str,
        is_home: int,
        env: dict[str, Any],
        players: dict[str, Any],
        resolution: Counter,
    ) -> dict[str, Any]:
        identity = self.api.get_player_identity(player_id, team=team)
        season, recent = self.pit.get_pitching_stats_as_of(player_id, game_date)
        resolution[
            "pitching_as_of_resolved" if season.innings_pitched > 0 else "pitching_as_of_empty"
        ] += 1

        out = self._pitching_actual(players, player_id)
        if out is None:
            resolution["pitcher_actual_missing"] += 1
            out = PitchingStatsSnapshot()
        else:
            resolution["pitcher_actual_resolved"] += 1

        recent_gs = recent.games_started
        return {
            "builder_schema": BUILDER_SCHEMA,
            "season": self.season,
            "game_date": game_date,
            "game_pk": game_pk,
            "player_id": player_id,
            "player_name": identity.name,
            "team": team,
            "opponent": opp,
            "venue": venue,
            "is_home": is_home,
            "throws": identity.throws,
            "pit_ip": round(season.innings_pitched, 1),
            "pit_k": season.strikeouts, "pit_bb": season.walks, "pit_hr": season.home_runs,
            "pit_k9": round(season.k_per_9, 2), "pit_bb9": round(season.bb_per_9, 2),
            "pit_hr9": round(season.hr_per_9, 2), "pit_gs": season.games_started,
            "pit_recent_ip": round(recent.innings_pitched, 1),
            "pit_recent_k9": round(recent.k_per_9, 2),
            "pit_recent_bb9": round(recent.bb_per_9, 2),
            "pit_recent_hr9": round(recent.hr_per_9, 2),
            "pit_recent_gs": recent_gs,
            "pit_recent_ip_per_gs": round(recent.innings_pitched / recent_gs, 2) if recent_gs else 0.0,
            **env,
            "has_prior_data": 1 if season.innings_pitched > 0 else 0,
            "out_ip": round(out.innings_pitched, 1),
            "out_k": out.strikeouts, "out_bb": out.walks, "out_hr": out.home_runs,
        }

    # ------------------------------------------------------------------
    # Feed parsing helpers
    # ------------------------------------------------------------------

    def _environment(
        self, venue: str, feed: dict[str, Any], resolution: Counter
    ) -> dict[str, Any]:
        park = self._park_factors(venue)
        park_resolved = 0 if park == (1.0, 1.0, 1.0) else 1
        resolution["park_resolved" if park_resolved else "park_default"] += 1

        weather = feed.get("gameData", {}).get("weather", {}) or {}
        temp = _to_float(weather.get("temp"))
        wind = _first_float(weather.get("wind"))
        condition = str(weather.get("condition", "")).lower()
        is_dome = 1 if any(w in condition for w in ("dome", "roof", "indoor")) else 0
        weather_resolved = 1 if weather else 0
        resolution["weather_resolved" if weather_resolved else "weather_default"] += 1

        officials = feed.get("liveData", {}).get("boxscore", {}).get("officials", []) or []
        plate = next((o for o in officials if o.get("officialType") == "Home Plate"), None)
        umpire_id = (plate or {}).get("official", {}).get("id")
        resolution["umpire_resolved" if umpire_id else "umpire_default"] += 1

        return {
            "park_hits_factor": park[0], "park_hr_factor": park[1],
            "park_runs_factor": park[2], "park_resolved": park_resolved,
            "weather_temp": temp, "weather_wind": wind,
            "weather_is_dome": is_dome, "weather_resolved": weather_resolved,
            "umpire_id": umpire_id, "umpire_resolved": 1 if umpire_id else 0,
        }

    def _opposing_sp(
        self,
        game: dict[str, Any],
        opp_side: str,
        actual_starter: Optional[int],
        resolution: Counter,
    ) -> tuple[Optional[int], str]:
        probable = (game["teams"][opp_side].get("probablePitcher") or {}).get("id")
        if probable:
            resolution["opp_sp_probable"] += 1
            return int(probable), "probable"
        if actual_starter:
            resolution["opp_sp_actual_starter"] += 1
            return actual_starter, "actual_starter"
        resolution["opp_sp_missing"] += 1
        return None, "missing"

    def _sp_as_of(
        self,
        sp_id: Optional[int],
        game_date: str,
        team: str,
        resolution: Counter,
    ) -> tuple[PitchingStatsSnapshot, PitchingStatsSnapshot, str, str]:
        if sp_id is None:
            return PitchingStatsSnapshot(), PitchingStatsSnapshot(), "", ""
        identity = self.api.get_player_identity(sp_id, team=team)
        season, recent = self.pit.get_pitching_stats_as_of(sp_id, game_date)
        resolution[
            "opp_sp_as_of_resolved" if season.innings_pitched > 0 else "opp_sp_as_of_empty"
        ] += 1
        return season, recent, identity.name, identity.throws

    @staticmethod
    def _hitting_actual(
        players: dict[str, Any], player_id: int
    ) -> Optional[HittingStatsSnapshot]:
        stats = (players.get(f"ID{player_id}", {}) or {}).get("stats", {})
        batting = stats.get("batting")
        return _parse_hitting(batting) if batting else None

    @staticmethod
    def _pitching_actual(
        players: dict[str, Any], player_id: int
    ) -> Optional[PitchingStatsSnapshot]:
        stats = (players.get(f"ID{player_id}", {}) or {}).get("stats", {})
        pitching = stats.get("pitching")
        return _parse_pitching(pitching) if pitching else None

    def _park_factors(self, venue: str) -> tuple[float, float, float]:
        """Config park lookup; mirrors FeatureFactory._park_factors' config branch."""
        park_config = self.config.get("park_factors", {})
        venue_lower = venue.lower()
        for park_name, factors in park_config.items():
            if isinstance(factors, dict) and park_name.lower() in venue_lower:
                return (
                    float(factors.get("hits", 1.0)),
                    float(factors.get("hr", 1.0)),
                    float(factors.get("runs", 1.0)),
                )
        return (1.0, 1.0, 1.0)

    # ------------------------------------------------------------------
    # Shards + manifest
    # ------------------------------------------------------------------

    def _write_shard(
        self, kind: str, game_date: str, columns: list[str], rows: list[dict[str, Any]]
    ) -> None:
        if not rows:
            return
        import csv

        path = self._shard_dir / f"{kind}_{game_date}.csv"
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, restval="")
            writer.writeheader()
            writer.writerows(rows)
        tmp.replace(path)

    def _load_manifest(self) -> dict[str, Any]:
        if self._manifest_path.exists():
            try:
                return json.loads(self._manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning("Corrupt manifest %s; starting fresh", self._manifest_path)
        return {"season": self.season, "builder_schema": BUILDER_SCHEMA, "dates": {}, "resolution": {}}

    def _record(self, result: DateResult) -> None:
        status = "done" if result.games else "empty"
        self.manifest.setdefault("dates", {})[result.game_date] = {
            "status": status,
            "games": result.games,
            "hitter_rows": result.hitter_rows,
            "pitcher_rows": result.pitcher_rows,
        }
        totals = Counter(self.manifest.get("resolution", {}))
        totals.update(result.resolution)
        self.manifest["resolution"] = dict(totals)
        tmp = self._manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        tmp.replace(self._manifest_path)


def season_dates(season: int, start: Optional[str] = None, end: Optional[str] = None) -> list[str]:
    """Every calendar date in the season window (schedule calls are cached)."""
    lo, hi = SEASON_WINDOWS.get(season, (f"{season}-03-15", f"{season}-10-05"))
    d = date.fromisoformat(start or lo)
    stop = date.fromisoformat(end or hi)
    out = []
    while d <= stop:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _round(value: Any, digits: int = 4) -> Any:
    return round(value, digits) if isinstance(value, (int, float)) else ""


def _to_float(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return ""


def _first_float(value: Any) -> Any:
    """MLB wind strings look like '10 mph, Out To CF' — take the leading number."""
    if value is None:
        return ""
    token = str(value).split()[0] if str(value).split() else ""
    return _to_float(token)
