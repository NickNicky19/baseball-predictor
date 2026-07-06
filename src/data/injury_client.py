"""
Injury and roster status client — IL / inactive flags from MLB Stats API.
"""

from __future__ import annotations

from typing import Any

import requests

from src.models.dataclasses import InjuryStatus
from src.utils.cache import TTLCache
from src.utils.logging import get_logger

logger = get_logger(__name__)


class InjuryClient:
    """Checks player active status via MLB people endpoint."""

    PEOPLE_URL = "https://statsapi.mlb.com/api/v1/people/{player_id}"

    def __init__(self, timeout: int = 15, cache_ttl_seconds: int = 1800):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "baseball-predictor/0.2"})
        self._cache = TTLCache[InjuryStatus](cache_ttl_seconds)

    def get_injury_status(self, player_id: int) -> InjuryStatus:
        key = f"injury:{player_id}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        try:
            response = self.session.get(
                self.PEOPLE_URL.format(player_id=player_id),
                timeout=self.timeout,
            )
            response.raise_for_status()
            people = response.json().get("people", [])
            payload = people[0] if people else {}
        except requests.RequestException as exc:
            logger.debug("Injury status fetch failed player_id=%s: %s", player_id, exc)
            status = InjuryStatus(player_id=player_id, status="unknown", is_active=True)
            self._cache.set(key, status)
            return status

        status = self._parse_status(player_id, payload)
        self._cache.set(key, status)
        return status

    def is_available(self, player_id: int) -> bool:
        return self.get_injury_status(player_id).is_active

    @staticmethod
    def _parse_status(player_id: int, payload: dict[str, Any]) -> InjuryStatus:
        active = bool(payload.get("active", True))
        if active:
            return InjuryStatus(player_id=player_id, status="active", is_active=True)

        note = str(payload.get("status", {}).get("description", "inactive"))

        return InjuryStatus(
            player_id=player_id,
            status="inactive",
            is_active=False,
            note=note,
        )