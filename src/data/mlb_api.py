"""
MLB Stats API client.

Fetches schedule, lineups, and player statistics from the public MLB Stats API.
Returns domain objects from src.models.dataclasses where possible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.utils.errors import DataFetchError
from src.models.dataclasses import (
    GameContext,
    Handedness,
    HitterGameContext,
    LineupStatus,
    PitcherGameContext,
    PlayerIdentity,
)
from src.utils.cache import TTLCache
from src.utils.logging import get_logger

logger = get_logger(__name__)


# Status codes worth retrying: 429 = rate limited (the MLB API DOES throttle a
# heavy caller), 5xx = transient server-side failure. A 404 is NOT retried --
# a missing game is missing however many times we ask.
_RETRY_STATUSES = (429, 500, 502, 503, 504)


def _build_session(
    max_retries: int = 4,
    backoff_factor: float = 1.0,
    user_agent: str = "baseball-predictor/2.0",
) -> requests.Session:
    """A requests.Session with transport-level retries and exponential backoff.

    WHY THIS EXISTS
    ---------------
    The pre-fix client built a BARE `requests.Session()` with no retry adapter
    and a 20s timeout, so `_get` made exactly ONE attempt and turned any blip
    into a fatal DataFetchError. That is what killed the scheduled
    daily-predictions GitHub Action ("MLB API request timed out after 20s"),
    and it is what killed a single-date gate smoke test mid-run.

    Retry logic DID already exist -- in DiskCachedGetMixin._network_get -- but
    that mixin's own docstring forbids using it for live slates ("never use it
    for live/today slates, where a cached pre-game feed would mask the final
    boxscore"). So run_slate.py, the automation entry point, ran on the
    UNPROTECTED path. The logic existed; it was just wired to the wrong one.

    ONE RETRY LAYER, DELIBERATELY
    -----------------------------
    Retries now live HERE, at the transport, which is the only layer every
    caller shares. DiskCachedGetMixin._network_get's retry loop is removed in
    the same change -- keeping both would MULTIPLY (3 mixin attempts x N
    session retries = up to 3N requests), which hammers the API hardest exactly
    when a 429 is asking us to back off. Retry amplification makes rate limits
    worse, not better.

    urllib3 sleeps {backoff_factor * (2 ** (attempt-1))} seconds between tries:
    with backoff_factor=1.0 that is 0s, 2s, 4s, 8s -- ~14s of patience across 4
    retries, and it honors a Retry-After header if the server sends one.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    retry = Retry(
        total=max_retries,
        connect=max_retries,
        read=max_retries,
        status=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=_RETRY_STATUSES,
        # GET-only API; every call here is idempotent and safe to repeat.
        allowed_methods=frozenset(["GET"]),
        # Return the final response instead of raising urllib3's own
        # MaxRetryError, so _get's existing raise_for_status() -> DataFetchError
        # path still produces the project's structured error with its hint.
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


@dataclass(frozen=True)
class HittingStatsSnapshot:
    """Season or recent hitting stats from the MLB API."""

    avg: float = 0.0
    obp: float = 0.0
    slg: float = 0.0
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
    games: int = 0


@dataclass(frozen=True)
class PitchingStatsSnapshot:
    """Season or recent pitching stats from the MLB API."""

    era: float = 0.0
    whip: float = 0.0
    innings_pitched: float = 0.0
    strikeouts: int = 0
    walks: int = 0
    home_runs: int = 0
    k_per_9: float = 0.0
    bb_per_9: float = 0.0
    hr_per_9: float = 0.0
    games_started: int = 0
    # Total appearances (gamesPlayed). Used by B4's role-aware innings estimator
    # to compute a true start_ratio = games_started / games. Kept UNFLOORED
    # (unlike games_started) so a reliever/opener with zero starts is visible as
    # such instead of being masked by a phantom start. Defaults to 0 = unknown.
    games: int = 0

    @property
    def k_pct(self) -> float:
        """Approximate K% from K/9 and IP (for PA simulator inputs)."""
        if self.innings_pitched <= 0:
            return 0.0
        estimated_pa = self.innings_pitched * 4.2
        if estimated_pa <= 0:
            return 0.0
        return (self.strikeouts / estimated_pa) * 100.0

    @property
    def bb_pct(self) -> float:
        """Approximate BB% from BB/9 and IP."""
        if self.innings_pitched <= 0:
            return 0.0
        estimated_pa = self.innings_pitched * 4.2
        if estimated_pa <= 0:
            return 0.0
        return (self.walks / estimated_pa) * 100.0


class MLBStatsAPI:
    """
    Client for the MLB Stats API (no API key required).

    Caching is in-memory TTL; disk cache can be layered on in a later phase.
    """

    BASE_URL = "https://statsapi.mlb.com/api/v1"
    FEED_URL = "https://statsapi.mlb.com/api/v1.1"

    def __init__(
        self,
        season: int = 2026,
        timeout: int = 30,
        cache_ttl_seconds: int = 900,
        config: Optional[dict[str, Any]] = None,
        max_retries: int = 4,
        backoff_factor: float = 1.0,
    ):
        self.season = season
        self.timeout = timeout
        self.session = _build_session(
            max_retries=max_retries, backoff_factor=backoff_factor
        )
        self._schedule_cache = TTLCache[list[dict[str, Any]]](cache_ttl_seconds)
        self._hitters_cache = TTLCache[list[HitterGameContext]](cache_ttl_seconds)
        self._pitchers_cache = TTLCache[list[PitcherGameContext]](cache_ttl_seconds)
        self._player_cache = TTLCache[dict[str, Any]](cache_ttl_seconds)
        # B4: role-aware expected_innings. Imported lazily (NOT at module top
        # level) to break a circular import: mlb_api -> src.prediction package
        # __init__ -> correction_manager -> src.learning package __init__ ->
        # outcome_recorder -> mlb_api. By the time an MLBStatsAPI is
        # constructed, all modules are fully loaded, so the cycle never forms.
        # With no config (or role_innings absent / disabled) this estimator
        # reproduces the pre-B4 heuristic exactly, so the live path is unchanged
        # until config.json enables it.
        from src.prediction.role_innings import RoleAwareInningsEstimator
        self._innings_estimator = RoleAwareInningsEstimator(config or {})

    def get_schedule(
        self,
        game_date: Optional[str] = None,
        include_lineups: bool = False,
    ) -> list[dict[str, Any]]:
        """Return raw game objects for a date."""
        game_date = game_date or date.today().isoformat()
        cache_key = f"{game_date}:lineups" if include_lineups else game_date
        cached = self._schedule_cache.get(cache_key)
        if cached is not None:
            return cached

        hydrate = "probablePitcher,team,venue"
        if include_lineups:
            hydrate += ",lineups"

        data = self._get(
            f"{self.BASE_URL}/schedule",
            params={
                "sportId": 1,
                "date": game_date,
                "hydrate": hydrate,
            },
        )
        games: list[dict[str, Any]] = []
        if data.get("dates"):
            games = data["dates"][0].get("games", [])
        self._schedule_cache.set(cache_key, games)
        return games

    def get_game_contexts(self, game_date: Optional[str] = None) -> list[GameContext]:
        """Return one GameContext per scheduled game (home and away perspectives)."""
        game_date = game_date or date.today().isoformat()
        contexts: list[GameContext] = []
        for game in self.get_schedule(game_date):
            game_pk = int(game["gamePk"])
            venue = game.get("venue", {}).get("name", "Unknown")
            away = game["teams"]["away"]["team"]["name"]
            home = game["teams"]["home"]["team"]["name"]
            contexts.append(
                GameContext(
                    game_pk=game_pk,
                    game_date=game_date,
                    venue=venue,
                    is_home=False,
                    opponent=home,
                    lineup_status="unknown",
                )
            )
            contexts.append(
                GameContext(
                    game_pk=game_pk,
                    game_date=game_date,
                    venue=venue,
                    is_home=True,
                    opponent=away,
                    lineup_status="unknown",
                )
            )
        return contexts

    def get_batting_order(self, game_pk: int) -> dict[str, list[int]]:
        """Return away/home batting orders as lists of MLB player IDs (slots 1–9)."""
        try:
            feed = self._get(f"{self.FEED_URL}/game/{game_pk}/feed/live")
        except requests.HTTPError:
            logger.warning("Live feed unavailable for game_pk=%s", game_pk)
            return {"away": [], "home": []}

        teams = feed.get("liveData", {}).get("boxscore", {}).get("teams", {})
        result: dict[str, list[int]] = {}
        for side in ("away", "home"):
            raw_order = teams.get(side, {}).get("battingOrder", []) or []
            player_ids = [int(pid) for pid in raw_order if pid]
            result[side] = player_ids[:9]
        return result

    def get_player_raw(self, player_id: int) -> dict[str, Any]:
        """Fetch raw MLB people endpoint payload for a player."""
        key = f"player:{player_id}"
        cached = self._player_cache.get(key)
        if cached is not None:
            return cached
        data = self._get(f"{self.BASE_URL}/people/{player_id}")
        people = data.get("people", [])
        payload = people[0] if people else {}
        self._player_cache.set(key, payload)
        return payload

    def get_player_identity(self, player_id: int, team: str = "") -> PlayerIdentity:
        info = self.get_player_raw(player_id)
        return PlayerIdentity(
            mlb_id=player_id,
            name=info.get("fullName", "Unknown"),
            team=team,
            bats=_normalize_hand(info.get("batHand", {}).get("code", "R")),
            throws=_normalize_hand(info.get("pitchHand", {}).get("code", "R")),
        )

    def get_hitting_stats(
        self, player_id: int
    ) -> tuple[HittingStatsSnapshot, HittingStatsSnapshot]:
        """Return (season, recent) hitting stat snapshots."""
        hydrate = f"stats(group=[hitting],type=[season,lastXGames],season={self.season})"
        data = self._get(f"{self.BASE_URL}/people/{player_id}", params={"hydrate": hydrate})
        people = data.get("people", [])
        if not people:
            return HittingStatsSnapshot(), HittingStatsSnapshot()

        season = HittingStatsSnapshot()
        recent = HittingStatsSnapshot()
        for block in people[0].get("stats", []):
            if not isinstance(block, dict):
                continue
            label = block.get("type", {}).get("displayName", "")
            parsed = _parse_hitting(_stat_from_splits(block.get("splits")))
            if label == "season":
                season = parsed
            elif label == "lastXGames":
                recent = parsed
        return season, recent

    def get_pitching_stats(
        self, player_id: int
    ) -> tuple[PitchingStatsSnapshot, PitchingStatsSnapshot]:
        """Return (season, recent) pitching stat snapshots."""
        hydrate = f"stats(group=[pitching],type=[season,lastXGames],season={self.season})"
        data = self._get(f"{self.BASE_URL}/people/{player_id}", params={"hydrate": hydrate})
        people = data.get("people", [])
        if not people:
            return PitchingStatsSnapshot(), PitchingStatsSnapshot()

        season = PitchingStatsSnapshot()
        recent = PitchingStatsSnapshot()
        for block in people[0].get("stats", []):
            if not isinstance(block, dict):
                continue
            label = block.get("type", {}).get("displayName", "")
            parsed = _parse_pitching(_stat_from_splits(block.get("splits")))
            if label == "season":
                season = parsed
            elif label == "lastXGames":
                recent = parsed
        return season, recent

    def get_platoon_splits(
        self, player_id: int
    ) -> tuple[Optional[HittingStatsSnapshot], Optional[HittingStatsSnapshot]]:
        """
        Return (vs LHP, vs RHP) hitting splits from the MLB Stats API.

        Returns (None, None) when splits are unavailable.
        """
        hydrate = (
            f"stats(group=[hitting],type=[statSplits],sitCodes=[vl,vr],season={self.season})"
        )
        try:
            data = self._get(
                f"{self.BASE_URL}/people/{player_id}",
                params={"hydrate": hydrate},
            )
        except DataFetchError:
            return None, None

        people = data.get("people", [])
        if not people:
            return None, None

        vs_lhp: Optional[HittingStatsSnapshot] = None
        vs_rhp: Optional[HittingStatsSnapshot] = None
        for block in people[0].get("stats", []):
            if not isinstance(block, dict):
                continue
            for entry in block.get("splits", []) or []:
                if not isinstance(entry, dict):
                    continue
                split_info = entry.get("split", {})
                code = split_info.get("code", "")
                parsed = _parse_hitting(entry.get("stat", {}))
                if code == "vl":
                    vs_lhp = parsed
                elif code == "vr":
                    vs_rhp = parsed
        return vs_lhp, vs_rhp

    def get_bvp_stats(
        self, hitter_id: int, pitcher_id: int
    ) -> Optional[HittingStatsSnapshot]:
        """
        Return hitter career stats vs a specific pitcher (BvP).

        Returns None when the matchup has no recorded plate appearances.
        """
        hydrate = (
            f"stats(group=[hitting],type=[vsPlayer],opposingPlayerId={pitcher_id},"
            f"season={self.season})"
        )
        try:
            data = self._get(
                f"{self.BASE_URL}/people/{hitter_id}",
                params={"hydrate": hydrate},
            )
        except DataFetchError:
            return None

        people = data.get("people", [])
        if not people:
            return None

        for block in people[0].get("stats", []):
            if not isinstance(block, dict):
                continue
            label = block.get("type", {}).get("displayName", "")
            if label == "vsPlayer":
                parsed = _parse_hitting(_stat_from_splits(block.get("splits")))
                return parsed if parsed.pa > 0 else None
        return None

    def get_hitters_for_date(
        self,
        game_date: Optional[str] = None,
        include_projected: bool = False,
    ) -> list[HitterGameContext]:
        """
        Return hitters for a slate date.

        By default only confirmed lineups (live feed battingOrder) are included.
        When include_projected=True, falls back to schedule-hydrated projected
        lineups (homePlayers/awayPlayers) for games without a posted order.
        """
        game_date = game_date or date.today().isoformat()
        cache_key = self._hitters_cache_key(game_date, include_projected)
        cached = self._hitters_cache.get(cache_key)
        if cached is not None:
            return cached

        games = self.get_schedule(game_date, include_lineups=include_projected)
        hitters: list[HitterGameContext] = []
        teams_total = 0
        teams_skipped = 0

        for game in games:
            game_pk = int(game["gamePk"])
            venue = game.get("venue", {}).get("name", "Unknown")
            confirmed_orders = self.get_batting_order(game_pk)

            away_name = game["teams"]["away"]["team"]["name"]
            home_name = game["teams"]["home"]["team"]["name"]

            for side, team_name, opp_name, is_home in (
                ("away", away_name, home_name, False),
                ("home", home_name, away_name, True),
            ):
                confirmed = confirmed_orders.get(side, [])
                projected = (
                    _projected_order_from_game(game, side) if include_projected else []
                )

                if confirmed:
                    order = confirmed
                    lineup_status: LineupStatus = (
                        "confirmed" if len(order) >= 9 else "unknown"
                    )
                elif include_projected and projected:
                    order = projected
                    lineup_status = "projected"
                else:
                    teams_total += 1
                    teams_skipped += 1
                    # No lineup available for this team. This is almost always
                    # the MLB API simply not having posted (or projected) a
                    # lineup yet, not a code fault — projected lineups in
                    # particular are frequently absent until ~1-2h pre-game.
                    # Log it so a thin slate is explained rather than mysterious.
                    team_side = away_name if side == "away" else home_name
                    if include_projected:
                        logger.info(
                            "No confirmed or projected lineup for %s (game %s); "
                            "skipped. MLB has not posted one yet.",
                            team_side,
                            game_pk,
                        )
                    else:
                        logger.info(
                            "No confirmed lineup for %s (game %s); skipped. "
                            "Re-run closer to game time, or use "
                            "--include-projected-lineups for earlier (noisier) coverage.",
                            team_side,
                            game_pk,
                        )
                    continue

                opp_side = "home" if side == "away" else "away"
                opp_probable = game["teams"][opp_side].get("probablePitcher") or {}

                teams_total += 1
                hitters.extend(
                    self._hitters_from_order(
                        order=order,
                        lineup_status=lineup_status,
                        game_pk=game_pk,
                        game_date=game_date,
                        venue=venue,
                        team_name=team_name,
                        opp_name=opp_name,
                        is_home=is_home,
                        opp_probable=opp_probable,
                    )
                )

        self._hitters_cache.set(cache_key, hitters)
        if teams_skipped:
            logger.info(
                "Loaded %d hitters for %s (include_projected=%s) — "
                "%d of %d teams had no lineup available and were skipped.",
                len(hitters),
                game_date,
                include_projected,
                teams_skipped,
                teams_total,
            )
        else:
            logger.info(
                "Loaded %d hitters for %s (include_projected=%s)",
                len(hitters),
                game_date,
                include_projected,
            )
        return hitters

    def _hitters_from_order(
        self,
        order: list[int],
        lineup_status: LineupStatus,
        game_pk: int,
        game_date: str,
        venue: str,
        team_name: str,
        opp_name: str,
        is_home: bool,
        opp_probable: dict[str, Any],
    ) -> list[HitterGameContext]:
        """Build HitterGameContext rows from a batting order list."""
        hitters: list[HitterGameContext] = []
        for slot, player_id in enumerate(order, start=1):
            try:
                identity = self.get_player_identity(player_id, team=team_name)
                game_ctx = GameContext(
                    game_pk=game_pk,
                    game_date=game_date,
                    venue=venue,
                    is_home=is_home,
                    opponent=opp_name,
                    lineup_status=lineup_status,
                )
                hitters.append(
                    HitterGameContext(
                        player=identity,
                        game=game_ctx,
                        lineup_slot=slot,
                        opposing_pitcher_id=opp_probable.get("id"),
                        opposing_pitcher_name=opp_probable.get("fullName", ""),
                        opposing_pitcher_throws=_normalize_hand(
                            (opp_probable.get("pitchHand") or {}).get("code", "R")
                        ),
                    )
                )
            except Exception as exc:
                logger.debug("Skipping hitter %s: %s", player_id, exc)
        return hitters

    @staticmethod
    def _hitters_cache_key(game_date: str, include_projected: bool) -> str:
        return f"{game_date}:all" if include_projected else f"{game_date}:confirmed"

    def get_pitchers_for_date(
        self, game_date: Optional[str] = None
    ) -> list[PitcherGameContext]:
        """Return probable starting pitchers for a date."""
        game_date = game_date or date.today().isoformat()
        cached = self._pitchers_cache.get(game_date)
        if cached is not None:
            return cached

        pitchers: list[PitcherGameContext] = []
        for game in self.get_schedule(game_date):
            game_pk = int(game["gamePk"])
            venue = game.get("venue", {}).get("name", "Unknown")
            away_name = game["teams"]["away"]["team"]["name"]
            home_name = game["teams"]["home"]["team"]["name"]

            for side, team_name, opp_name, is_home in (
                ("away", away_name, home_name, False),
                ("home", home_name, away_name, True),
            ):
                probable = game["teams"][side].get("probablePitcher")
                if not probable or not probable.get("id"):
                    continue

                pitcher_id = int(probable["id"])
                identity = self.get_player_identity(pitcher_id, team=team_name)
                _, recent = self.get_pitching_stats(pitcher_id)
                # B4: role-aware when enabled in config; otherwise byte-identical
                # to the legacy _estimate_expected_ip heuristic.
                expected_ip = self._innings_estimator.estimate(recent)

                game_ctx = GameContext(
                    game_pk=game_pk,
                    game_date=game_date,
                    venue=venue,
                    is_home=is_home,
                    opponent=opp_name,
                    lineup_status="confirmed",
                )
                pitchers.append(
                    PitcherGameContext(
                        player=identity,
                        game=game_ctx,
                        expected_innings=expected_ip,
                    )
                )

        self._pitchers_cache.set(game_date, pitchers)
        return pitchers

    def get_final_game_pks(self, game_date: Optional[str] = None) -> list[int]:
        """Return game_pk values for completed games on a date."""
        game_date = game_date or date.today().isoformat()
        finals: list[int] = []
        for game in self.get_schedule(game_date):
            status = game.get("status", {})
            if status.get("abstractGameState") == "Final" or status.get("codedGameState") == "F":
                finals.append(int(game["gamePk"]))
        return finals

    def get_game_boxscore_stats(
        self, game_pk: int
    ) -> tuple[dict[int, HittingStatsSnapshot], dict[int, PitchingStatsSnapshot]]:
        """
        Return per-player hitting and pitching stat snapshots from a game boxscore.

        Stats reflect that single game only (not season totals).
        """
        try:
            feed = self._get(f"{self.FEED_URL}/game/{game_pk}/feed/live")
        except DataFetchError:
            raise
        except Exception as exc:
            raise DataFetchError(f"Failed to load boxscore for game_pk={game_pk}") from exc

        teams = feed.get("liveData", {}).get("boxscore", {}).get("teams", {})
        hitting: dict[int, HittingStatsSnapshot] = {}
        pitching: dict[int, PitchingStatsSnapshot] = {}

        for side in ("away", "home"):
            players = teams.get(side, {}).get("players", {}) or {}
            for player_key, player_data in players.items():
                if not str(player_key).startswith("ID"):
                    continue
                player_id = int(str(player_key).replace("ID", ""))
                stats = player_data.get("stats", {})
                if "batting" in stats and stats["batting"]:
                    hitting[player_id] = _parse_hitting(stats["batting"])
                if "pitching" in stats and stats["pitching"]:
                    pitching[player_id] = _parse_pitching(stats["pitching"])

        return hitting, pitching

    def get_actuals_for_date(
        self, game_date: Optional[str] = None
    ) -> tuple[dict[int, HittingStatsSnapshot], dict[int, PitchingStatsSnapshot]]:
        """Aggregate single-game actuals for all final games on a date."""
        game_date = game_date or date.today().isoformat()
        all_hitting: dict[int, HittingStatsSnapshot] = {}
        all_pitching: dict[int, PitchingStatsSnapshot] = {}

        for game_pk in self.get_final_game_pks(game_date):
            game_hitting, game_pitching = self.get_game_boxscore_stats(game_pk)
            all_hitting.update(game_hitting)
            all_pitching.update(game_pitching)

        return all_hitting, all_pitching

    def clear_cache(self, game_date: Optional[str] = None) -> None:
        """Invalidate cached schedule/lineup data for a date."""
        game_date = game_date or date.today().isoformat()
        self._schedule_cache.delete(game_date)
        self._schedule_cache.delete(f"{game_date}:lineups")
        self._hitters_cache.delete(self._hitters_cache_key(game_date, False))
        self._hitters_cache.delete(self._hitters_cache_key(game_date, True))
        self._pitchers_cache.delete(game_date)

    def _get(self, url: str, params: Optional[dict[str, Any]] = None) -> Any:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.Timeout as exc:
            raise DataFetchError(
                f"MLB API request timed out after {self.timeout}s",
                hint="Retry with --refresh or check network connectivity",
            ) from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise DataFetchError(
                f"MLB API returned HTTP {status} for {url}",
                hint="Verify game date and MLB API availability",
            ) from exc
        except requests.RequestException as exc:
            raise DataFetchError(
                f"MLB API request failed: {url}",
                hint="Check network connectivity",
            ) from exc
        except ValueError as exc:
            raise DataFetchError(
                f"MLB API returned invalid JSON from {url}",
            ) from exc


def _projected_order_from_game(game: dict[str, Any], side: Literal["away", "home"]) -> list[int]:
    """
    Extract projected batting order from schedule hydrate lineups.

    MLB returns homePlayers/awayPlayers arrays in posted order when lineups
    are not yet confirmed in the live feed.
    """
    lineups = game.get("lineups")
    if not isinstance(lineups, dict):
        return []
    key = "homePlayers" if side == "home" else "awayPlayers"
    players = lineups.get(key) or []
    if not isinstance(players, list):
        return []
    order: list[int] = []
    for entry in players:
        if not isinstance(entry, dict):
            continue
        player_id = entry.get("id")
        if player_id:
            order.append(int(player_id))
    return order[:9]


def _normalize_hand(code: str) -> Handedness:
    code = (code or "R").upper()
    if code in ("L", "R", "S"):
        return code  # type: ignore[return-value]
    return "R"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "-"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "", "-"):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _stat_from_splits(splits: Any) -> dict[str, Any]:
    """
    Extract the stat dict from an MLB Stats API splits array.

    The hydrate response nests stats under splits[0].stat, not on the block itself.
    Returns an empty dict when splits is missing, empty, or malformed.
    """
    if not isinstance(splits, list):
        return {}
    for entry in splits:
        if not isinstance(entry, dict):
            continue
        stat = entry.get("stat")
        if isinstance(stat, dict):
            return stat
    return {}


def _parse_hitting(stat: dict[str, Any]) -> HittingStatsSnapshot:
    return HittingStatsSnapshot(
        avg=_safe_float(stat.get("avg")),
        obp=_safe_float(stat.get("obp")),
        slg=_safe_float(stat.get("slg")),
        pa=_safe_int(stat.get("plateAppearances")),
        ab=_safe_int(stat.get("atBats")),
        hits=_safe_int(stat.get("hits")),
        doubles=_safe_int(stat.get("doubles")),
        triples=_safe_int(stat.get("triples")),
        home_runs=_safe_int(stat.get("homeRuns")),
        rbi=_safe_int(stat.get("rbi")),
        runs=_safe_int(stat.get("runs")),
        walks=_safe_int(stat.get("baseOnBalls")),
        strikeouts=_safe_int(stat.get("strikeOuts")),
        games=max(_safe_int(stat.get("gamesPlayed")), 1),
    )


def _parse_pitching(stat: dict[str, Any]) -> PitchingStatsSnapshot:
    ip = _safe_float(stat.get("inningsPitched"))
    strikeouts = _safe_int(stat.get("strikeOuts"))
    hits = _safe_int(stat.get("hits"))
    home_runs = _safe_int(stat.get("homeRuns"))
    walks = _safe_int(stat.get("baseOnBalls"))

    k9 = _safe_float(stat.get("strikeoutsPer9Inn"))
    h9 = _safe_float(stat.get("hitsPer9Inn"))
    hr9 = _safe_float(stat.get("homeRunsPer9"))
    bb9 = _safe_float(stat.get("walksPer9Inn"))

    if ip > 0:
        if k9 <= 0:
            k9 = strikeouts / ip * 9.0
        if h9 <= 0:
            h9 = hits / ip * 9.0
        if hr9 <= 0:
            hr9 = home_runs / ip * 9.0
        if bb9 <= 0:
            bb9 = walks / ip * 9.0

    return PitchingStatsSnapshot(
        era=_safe_float(stat.get("era")),
        whip=_safe_float(stat.get("whip")),
        innings_pitched=ip,
        strikeouts=strikeouts,
        walks=walks,
        home_runs=home_runs,
        k_per_9=k9,
        bb_per_9=bb9,
        hr_per_9=hr9,
        games_started=max(_safe_int(stat.get("gamesStarted")), 1),
        # UNFLOORED on purpose: the true appearance count is what lets B4
        # separate an opener/reliever from a starter. gamesPlayed is already in
        # this same stat block (the hitter parser reads it), so no new API call.
        games=_safe_int(stat.get("gamesPlayed")),
    )


def _estimate_expected_ip(recent: PitchingStatsSnapshot) -> float:
    """
    Pre-B4 starter-IP heuristic. RETAINED as the disabled-path reference and
    for any caller that estimates without a config. Delegates to the estimator's
    legacy branch so there is exactly ONE definition of the legacy numbers
    (guards against the two-copies-drift class of bug).
    """
    # Lazy import (see MLBStatsAPI.__init__) to avoid the circular import at
    # module load; this function runs well after all modules are initialized.
    from src.prediction.role_innings import RoleAwareInningsEstimator
    return RoleAwareInningsEstimator._legacy_expected_ip(recent)
