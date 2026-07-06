"""
Point-in-time stats — leakage-safe historical stat reconstruction.

NEW MODULE (assessment finding #4): the existing MLBStatsAPI season/lastXGames
hydrations always return stats "as of now", which makes historical
re-simulation leak future information. This module rebuilds a player's stats
as of any past date by fetching their per-game log for the season and
aggregating only rows strictly BEFORE the as-of date.

This single capability unblocks two roadmap items at once:
- Phase 4: true walk-forward backtesting over arbitrary historical dates
  (regenerate features for July 1 using only data through June 30).
- Phase 2: building a training set of point-in-time-correct feature rows for
  the future CatBoost/LightGBM per-PA models.

It also provides rolling-window features (roll15_xwoba proxies, recent PA
counts) for the rich feature layer. Note: true rolling xwOBA needs Statcast
per-game data (pybaseball statcast_batter with date ranges); until that is
wired, rolling OBP/SLG/K%/BB% from the MLB game log are provided under their
own honest names.

Results are cached per (player, season) so a backtest over many dates costs
one API call per player, not one per date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from src.data.mlb_api import HittingStatsSnapshot, PitchingStatsSnapshot, MLBStatsAPI
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class GameLogRow:
    """One game's counting stats for a player."""

    game_date: str  # ISO YYYY-MM-DD
    pa: int = 0
    ab: int = 0
    hits: int = 0
    doubles: int = 0
    triples: int = 0
    home_runs: int = 0
    rbi: int = 0
    runs: int = 0
    walks: int = 0
    strikeouts: int = 0
    # Pitching
    innings_pitched: float = 0.0
    k_pitched: int = 0
    bb_pitched: int = 0
    hr_allowed: int = 0


class PointInTimeStats:
    """
    Reconstructs season-to-date and recent-window stats as of a historical
    date, using MLB Stats API game logs. Everything is computed from rows
    with game_date < as_of_date (strict), so the slate date itself never
    leaks into its own features.
    """

    def __init__(self, mlb_api: Optional[MLBStatsAPI] = None, season: Optional[int] = None):
        self.mlb_api = mlb_api or MLBStatsAPI(season=season or date.today().year)
        self.season = season or self.mlb_api.season
        self._hitting_logs: dict[int, list[GameLogRow]] = {}
        self._pitching_logs: dict[int, list[GameLogRow]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_hitting_stats_as_of(
        self,
        player_id: int,
        as_of_date: str,
        recent_games: int = 15,
    ) -> tuple[HittingStatsSnapshot, HittingStatsSnapshot]:
        """
        (season_to_date, recent) hitting snapshots using only games strictly
        before as_of_date.
        """
        rows = self._rows_before(self._hitting_log(player_id), as_of_date)
        season = self._aggregate_hitting(rows)
        recent = self._aggregate_hitting(rows[-recent_games:]) if rows else HittingStatsSnapshot()
        return season, recent

    def get_pitching_stats_as_of(
        self,
        player_id: int,
        as_of_date: str,
        recent_games: int = 5,
    ) -> tuple[PitchingStatsSnapshot, PitchingStatsSnapshot]:
        """
        (season_to_date, recent) pitching snapshots using only games strictly
        before as_of_date.
        """
        rows = self._rows_before(self._pitching_log(player_id), as_of_date)
        season = self._aggregate_pitching(rows)
        recent = (
            self._aggregate_pitching(rows[-recent_games:]) if rows else PitchingStatsSnapshot()
        )
        return season, recent

    def rolling_features(
        self,
        player_id: int,
        as_of_date: str,
    ) -> dict[str, Any]:
        """
        Rolling-window features for the rich feature layer, leakage-safe.

        Provides MLB-gamelog-derived rolling rates. Statcast-derived rolling
        metrics (roll15_xwoba etc.) require per-game Statcast data and are
        left absent rather than approximated dishonestly.
        """
        rows = self._rows_before(self._hitting_log(player_id), as_of_date)
        out: dict[str, Any] = {}
        for window, prefix in ((15, "roll15"), (30, "roll30")):
            recent = rows[-window:]
            pa = sum(r.pa for r in recent)
            ab = sum(r.ab for r in recent)
            out[f"recent_pa_{window}"] = pa
            if pa > 0:
                out[f"{prefix}_k_rate"] = sum(r.strikeouts for r in recent) / pa
                out[f"{prefix}_bb_rate"] = sum(r.walks for r in recent) / pa
            if ab > 0:
                hits = sum(r.hits for r in recent)
                hr = sum(r.home_runs for r in recent)
                bip = ab - sum(r.strikeouts for r in recent) - hr
                if bip > 0:
                    out[f"{prefix}_babip"] = max(0.0, (hits - hr)) / bip
        return out

    def clear_cache(self) -> None:
        self._hitting_logs.clear()
        self._pitching_logs.clear()

    # ------------------------------------------------------------------
    # Fetch + parse
    # ------------------------------------------------------------------

    def _hitting_log(self, player_id: int) -> list[GameLogRow]:
        if player_id not in self._hitting_logs:
            self._hitting_logs[player_id] = self._fetch_game_log(player_id, "hitting")
        return self._hitting_logs[player_id]

    def _pitching_log(self, player_id: int) -> list[GameLogRow]:
        if player_id not in self._pitching_logs:
            self._pitching_logs[player_id] = self._fetch_game_log(player_id, "pitching")
        return self._pitching_logs[player_id]

    def _fetch_game_log(self, player_id: int, group: str) -> list[GameLogRow]:
        url = f"{self.mlb_api.BASE_URL}/people/{player_id}/stats"
        params = {
            "stats": "gameLog",
            "group": group,
            "season": self.season,
        }
        try:
            data = self.mlb_api._get(url, params=params)
        except Exception as exc:  # DataFetchError or network issues
            logger.warning("Game log fetch failed for %s (%s): %s", player_id, group, exc)
            return []

        rows: list[GameLogRow] = []
        for block in data.get("stats", []):
            for split in block.get("splits", []):
                stat = split.get("stat", {}) or {}
                game_date = str(split.get("date", ""))
                if not game_date:
                    continue
                if group == "hitting":
                    rows.append(
                        GameLogRow(
                            game_date=game_date,
                            pa=_int(stat.get("plateAppearances")),
                            ab=_int(stat.get("atBats")),
                            hits=_int(stat.get("hits")),
                            doubles=_int(stat.get("doubles")),
                            triples=_int(stat.get("triples")),
                            home_runs=_int(stat.get("homeRuns")),
                            rbi=_int(stat.get("rbi")),
                            runs=_int(stat.get("runs")),
                            walks=_int(stat.get("baseOnBalls")),
                            strikeouts=_int(stat.get("strikeOuts")),
                        )
                    )
                else:
                    rows.append(
                        GameLogRow(
                            game_date=game_date,
                            innings_pitched=_ip(stat.get("inningsPitched")),
                            k_pitched=_int(stat.get("strikeOuts")),
                            bb_pitched=_int(stat.get("baseOnBalls")),
                            hr_allowed=_int(stat.get("homeRuns")),
                        )
                    )
        rows.sort(key=lambda r: r.game_date)
        return rows

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _rows_before(rows: list[GameLogRow], as_of_date: str) -> list[GameLogRow]:
        cutoff = _parse_date(as_of_date)
        return [r for r in rows if _parse_date(r.game_date) < cutoff]

    @staticmethod
    def _aggregate_hitting(rows: list[GameLogRow]) -> HittingStatsSnapshot:
        if not rows:
            return HittingStatsSnapshot()
        pa = sum(r.pa for r in rows)
        ab = sum(r.ab for r in rows)
        hits = sum(r.hits for r in rows)
        doubles = sum(r.doubles for r in rows)
        triples = sum(r.triples for r in rows)
        hr = sum(r.home_runs for r in rows)
        walks = sum(r.walks for r in rows)
        singles = hits - doubles - triples - hr
        total_bases = singles + 2 * doubles + 3 * triples + 4 * hr
        return HittingStatsSnapshot(
            avg=hits / ab if ab else 0.0,
            obp=(hits + walks) / pa if pa else 0.0,  # HBP/SF not in log rows; slight understatement
            slg=total_bases / ab if ab else 0.0,
            pa=pa,
            ab=ab,
            hits=hits,
            doubles=doubles,
            triples=triples,
            home_runs=hr,
            rbi=sum(r.rbi for r in rows),
            runs=sum(r.runs for r in rows),
            walks=walks,
            strikeouts=sum(r.strikeouts for r in rows),
            games=len(rows),
        )

    @staticmethod
    def _aggregate_pitching(rows: list[GameLogRow]) -> PitchingStatsSnapshot:
        if not rows:
            return PitchingStatsSnapshot()
        ip = sum(r.innings_pitched for r in rows)
        k = sum(r.k_pitched for r in rows)
        bb = sum(r.bb_pitched for r in rows)
        hr = sum(r.hr_allowed for r in rows)
        return PitchingStatsSnapshot(
            innings_pitched=ip,
            strikeouts=k,
            walks=bb,
            home_runs=hr,
            k_per_9=(k * 9.0 / ip) if ip else 0.0,
            bb_per_9=(bb * 9.0 / ip) if ip else 0.0,
            hr_per_9=(hr * 9.0 / ip) if ip else 0.0,
            games_started=len(rows),
        )


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ip(value: Any) -> float:
    """MLB innings notation: '5.2' means 5 and 2/3 innings."""
    try:
        text = str(value)
        if "." in text:
            whole, outs = text.split(".", 1)
            return int(whole) + int(outs[0]) / 3.0
        return float(text)
    except (TypeError, ValueError, IndexError):
        return 0.0


def _parse_date(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()

