"""
Weather context client — game-day conditions for park and HR adjustments.

Primary source: MLB Stats API live game feed (gameData.weather).
Falls back to neutral defaults when unavailable.
"""

from __future__ import annotations

from typing import Any, Optional

import requests

from src.models.dataclasses import WeatherContext
from src.utils.cache import TTLCache
from src.utils.logging import get_logger

logger = get_logger(__name__)


class WeatherClient:
    """Fetches per-game weather from the MLB Stats API."""

    FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"

    def __init__(self, timeout: int = 15, cache_ttl_seconds: int = 3600):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "baseball-predictor/0.2"})
        self._cache = TTLCache[WeatherContext](cache_ttl_seconds)

    def get_weather_for_game(
        self,
        game_pk: int,
        venue: str,
        game_date: str,
    ) -> WeatherContext:
        cache_key = f"{game_pk}:{game_date}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        context = self._fetch_from_feed(game_pk, venue, game_date)
        self._cache.set(cache_key, context)
        return context

    def hr_factor_from_weather(self, weather: WeatherContext) -> float:
        """
        Data-driven HR adjustment from temperature and wind.

        +1°F ≈ +0.15% HR probability (empirical MLB studies, linearized).
        Wind toward/out of park uses direction when available; otherwise neutral.
        """
        if weather.is_dome:
            return 1.0

        temp_delta = (weather.temperature_f - 72.0) * 0.0015
        wind_delta = 0.0
        if weather.wind_mph >= 8.0 and weather.wind_direction_deg is not None:
            wind_delta = min(0.06, weather.wind_mph * 0.003)

        humidity_penalty = max(0.0, (weather.precip_probability - 0.30) * 0.05)
        factor = 1.0 + temp_delta + wind_delta - humidity_penalty
        return round(max(0.88, min(1.15, factor)), 4)

    def _fetch_from_feed(self, game_pk: int, venue: str, game_date: str) -> WeatherContext:
        try:
            response = self.session.get(
                self.FEED_URL.format(game_pk=game_pk),
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            logger.debug("Weather fetch failed for game_pk=%s: %s", game_pk, exc)
            return WeatherContext(venue=venue, game_date=game_date)

        weather = data.get("gameData", {}).get("weather", {}) or {}
        condition = str(weather.get("condition", "")).lower()
        is_dome = "dome" in condition or "roof" in condition or "indoor" in condition

        return WeatherContext(
            venue=venue,
            game_date=game_date,
            temperature_f=_safe_float(weather.get("temp"), 72.0),
            wind_mph=_safe_float(weather.get("wind"), 5.0),
            wind_direction_deg=_optional_float(weather.get("windDirection")),
            precip_probability=1.0 if "rain" in condition or "storm" in condition else 0.0,
            is_dome=is_dome,
        )


def _safe_float(value: Any, default: float) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None