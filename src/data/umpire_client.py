"""
Umpire tendencies client — K/run environment from MLB game officials.
"""

from __future__ import annotations

from typing import Any, Optional

import requests

from src.models.dataclasses import UmpireContext
from src.utils.cache import TTLCache
from src.utils.logging import get_logger

logger = get_logger(__name__)


class UmpireClient:
    """
    Loads home-plate umpire for a game and applies league-average tendency priors.

    Historical K/run biases can be refined from pairs data in a future calibration pass.
    """

    BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"

    def __init__(self, timeout: int = 15, cache_ttl_seconds: int = 3600):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "baseball-predictor/0.2"})
        self._cache = TTLCache[Optional[UmpireContext]](cache_ttl_seconds)
        self._tendency_cache: dict[int, tuple[float, float]] = {}

    def get_umpire_for_game(self, game_pk: int) -> Optional[UmpireContext]:
        cached = self._cache.get(str(game_pk))
        if cached is not None:
            return cached

        try:
            response = self.session.get(
                self.BOXSCORE_URL.format(game_pk=game_pk),
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            logger.debug("Umpire fetch failed game_pk=%s: %s", game_pk, exc)
            self._cache.set(str(game_pk), None)
            return None

        officials = data.get("officials", []) or []
        plate = next((o for o in officials if o.get("officialType") == "Home Plate"), None)
        if not plate:
            self._cache.set(str(game_pk), None)
            return None

        umpire_id = plate.get("official", {}).get("id")
        name = plate.get("official", {}).get("fullName", "")
        k_bias, runs_bias = self._tendency_cache.get(int(umpire_id or 0), (0.0, 0.0))

        context = UmpireContext(
            umpire_id=int(umpire_id) if umpire_id else None,
            name=name,
            k_bias=k_bias,
            runs_bias=runs_bias,
        )
        self._cache.set(str(game_pk), context)
        return context

    def load_tendencies_from_pairs(self, pairs_summary: dict[int, dict[str, float]]) -> None:
        """Load umpire K/run biases estimated from historical pairs (umpire_id → biases)."""
        for umpire_id, biases in pairs_summary.items():
            self._tendency_cache[int(umpire_id)] = (
                float(biases.get("k_bias", 0.0)),
                float(biases.get("runs_bias", 0.0)),
            )