"""
The Odds API provider for live MLB player prop lines.

Uses the v4 events + event-odds endpoints. Requires an API key via config or
environment variable (default: ODDS_API_KEY).

Docs: https://the-odds-api.com/liveapi/guides/v4/
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

from src.data.odds.base import OddsAPISettings, OddsProvider, normalize_category
from src.evaluation.market_economics import MarketEconomicsError, american_odds
from src.models.dataclasses import OddsLine, PropCategory
from src.utils.errors import OddsLoadError
from src.utils.logging import get_logger

logger = get_logger(__name__)


class OddsAPIProvider(OddsProvider):
    """Fetches player prop odds from The Odds API for a slate date."""

    def __init__(
        self,
        settings: OddsAPISettings,
        session: Optional[requests.Session] = None,
    ):
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "baseball-predictor/2.0"})
        self._last_quota: dict[str, str] = {}

    @property
    def source_name(self) -> str:
        return "odds_api"

    def is_available(self) -> bool:
        return self.settings.enabled and bool(self._api_key())

    @property
    def last_quota(self) -> dict[str, str]:
        """Response quota headers from the most recent API call."""
        return dict(self._last_quota)

    def load(self, game_date: Optional[str] = None) -> list[OddsLine]:
        if not self.is_available():
            return []

        game_date = game_date or date.today().isoformat()
        api_key = self._api_key()
        events = self._fetch_events(game_date, api_key)
        if not events:
            logger.info("Odds API: no MLB events for %s", game_date)
            return []

        lines: list[OddsLine] = []
        for event in events:
            event_id = event.get("id")
            if not event_id:
                continue
            try:
                event_lines = self._fetch_event_odds(str(event_id), api_key)
                lines.extend(event_lines)
            except OddsLoadError as exc:
                logger.warning("Odds API: skipped event %s: %s", event_id, exc)

        logger.info(
            "Odds API loaded %d lines for %s (events=%d, quota=%s)",
            len(lines),
            game_date,
            len(events),
            self._last_quota,
        )
        return lines

    def _api_key(self) -> str:
        if self.settings.api_key:
            return self.settings.api_key
        return os.environ.get(self.settings.api_key_env, "").strip()

    def _fetch_events(self, game_date: str, api_key: str) -> list[dict[str, Any]]:
        start = datetime.fromisoformat(game_date).replace(tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        url = f"{self.settings.base_url}/sports/{self.settings.sport_key}/events"
        params = {
            "apiKey": api_key,
            "commenceTimeFrom": start.isoformat().replace("+00:00", "Z"),
            "commenceTimeTo": end.isoformat().replace("+00:00", "Z"),
        }
        data = self._get(url, params)
        return data if isinstance(data, list) else []

    def _fetch_event_odds(self, event_id: str, api_key: str) -> list[OddsLine]:
        url = f"{self.settings.base_url}/sports/{self.settings.sport_key}/events/{event_id}/odds"
        params: dict[str, Any] = {
            "apiKey": api_key,
            "regions": self.settings.regions,
            "markets": ",".join(self.settings.markets),
            "oddsFormat": "american",
        }
        if self.settings.bookmaker:
            params["bookmakers"] = self.settings.bookmaker

        payload = self._get(url, params)
        return self._parse_event_odds(payload)

    def _parse_event_odds(self, payload: dict[str, Any]) -> list[OddsLine]:
        lines: list[OddsLine] = []
        bookmakers = payload.get("bookmakers", [])
        preferred = self.settings.bookmaker.lower() if self.settings.bookmaker else ""

        for bookmaker in bookmakers:
            book_key = str(bookmaker.get("key", "")).lower()
            book_title = str(bookmaker.get("title", bookmaker.get("key", "")))
            if preferred and book_key != preferred:
                continue

            for market in bookmaker.get("markets", []):
                market_key = str(market.get("key", ""))
                category = self._market_to_category(market_key)
                if category is None:
                    continue

                over_by_player: dict[str, tuple[float, int]] = {}
                under_by_player: dict[str, tuple[float, int]] = {}

                for outcome in market.get("outcomes", []):
                    player = self._extract_player_name(outcome)
                    if not player:
                        continue
                    point = float(outcome.get("point", 0))
                    try:
                        price = american_odds(
                            outcome.get("price"),
                            f"Odds API {book_key or book_title} {market_key} price",
                        )
                    except MarketEconomicsError as exc:
                        raise OddsLoadError(
                            f"Invalid American price in Odds API event payload ({book_title}/{market_key})",
                            hint="Prices must be canonical integers with absolute value at least 100",
                        ) from exc
                    name = str(outcome.get("name", "")).lower()

                    if name == "over":
                        over_by_player[player.lower()] = (point, price)
                    elif name == "under":
                        under_by_player[player.lower()] = (point, price)
                    elif point and price:
                        over_by_player[player.lower()] = (point, price)

                for player_key, (point, over_odds) in over_by_player.items():
                    under = under_by_player.get(player_key)
                    # A de-vigged probability requires two real prices at the
                    # same line.  Copying the over price into a missing under
                    # manufactures a market, which can create a false edge and
                    # invalid forward-ledger evidence.  One-sided products
                    # (notably many HR props) are intentionally absent here;
                    # they need a separately validated one-sided evaluator.
                    if (over_odds == 0 or under is None
                            or abs(under[0] - point) >= 0.01 or under[1] == 0):
                        continue
                    under_odds = under[1]
                    display_name = self._title_case_player(player_key)
                    lines.append(
                        OddsLine(
                            player_name=display_name,
                            category=category,
                            line=point,
                            over_odds_american=over_odds,
                            under_odds_american=under_odds,
                            sportsbook=book_title,
                        )
                    )
        return lines

    def _market_to_category(self, market_key: str) -> Optional[PropCategory]:
        mapped = self.settings.market_category_map.get(market_key)
        if not mapped:
            return None
        return normalize_category(mapped)

    @staticmethod
    def _extract_player_name(outcome: dict[str, Any]) -> str:
        for field in ("description", "name", "player"):
            value = str(outcome.get(field, "")).strip()
            if value and value.lower() not in ("over", "under", "yes", "no"):
                return value
        return ""

    @staticmethod
    def _title_case_player(key: str) -> str:
        return " ".join(part.capitalize() for part in key.split())

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        try:
            response = self.session.get(url, params=params, timeout=self.settings.timeout_seconds)
            self._last_quota = {
                k: response.headers.get(k, "")
                for k in ("x-requests-remaining", "x-requests-used", "x-requests-last")
            }
            if response.status_code == 401:
                raise OddsLoadError(
                    "Odds API authentication failed",
                    hint=f"Set {self.settings.api_key_env} or odds.odds_api.api_key in config",
                )
            response.raise_for_status()
            return response.json()
        except requests.Timeout as exc:
            raise OddsLoadError(
                f"Odds API timed out after {self.settings.timeout_seconds}s",
            ) from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise OddsLoadError(f"Odds API returned HTTP {status}") from exc
        except requests.RequestException as exc:
            raise OddsLoadError(f"Odds API request failed: {url}") from exc
        except ValueError as exc:
            raise OddsLoadError("Odds API returned invalid JSON") from exc
