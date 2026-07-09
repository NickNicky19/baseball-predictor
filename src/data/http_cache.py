"""
Disk-cached, rate-limited HTTP layer for historical MLB API pulls (A3).

Historical data is immutable: a 2024 game log or completed-game feed never
changes. So responses are cached to disk forever, keyed by (url, params).
This makes the A3 training-set builder crash-resumable and re-runnable at
near-zero API cost, and A4's Statcast pulls can reuse the same seam.

Design: MLBStatsAPI funnels every request through _get(url, params). The
mixin overrides _get with cache-then-network; the network step is rate
limited (min interval between real requests) and retried with exponential
backoff. Cache layout: <cache_dir>/<hh>/<sha256>.json where hh is the first
two hash chars (keeps directories small at ~20k+ entries). Each file stores
the url/params alongside the payload for debuggability.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

from src.utils.errors import DataFetchError
from src.utils.logging import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """Minimum-interval limiter. interval=0 disables (offline tests)."""

    def __init__(self, min_interval_seconds: float = 0.4):
        self.min_interval = max(0.0, float(min_interval_seconds))
        self._last_request = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        remaining = self.min_interval - (now - self._last_request)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request = time.monotonic()


def cache_key(url: str, params: Optional[dict[str, Any]]) -> str:
    canonical = json.dumps({"url": url, "params": params or {}}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DiskCachedGetMixin:
    """
    Mix into an MLBStatsAPI subclass to add disk caching + rate limiting at
    the _get seam. Class order matters: mixin first.

        class CachedMLBAPI(DiskCachedGetMixin, MLBStatsAPI): ...

    Only immutable historical lookups should flow through an instance of
    such a class — never use it for live/today slates, where a cached
    pre-game feed would mask the final boxscore.
    """

    def __init__(
        self,
        *args: Any,
        cache_dir: str | Path = "data/cache/http",
        rate_limiter: Optional[RateLimiter] = None,
        max_retries: int = 3,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.max_retries = max(1, int(max_retries))
        self.cache_hits = 0
        self.cache_misses = 0

    # -- seam override ------------------------------------------------

    def _get(self, url: str, params: Optional[dict[str, Any]] = None) -> Any:
        path = self._cache_path(url, params)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.cache_hits += 1
                return payload["data"]
            except (json.JSONDecodeError, KeyError, OSError):
                logger.warning("Corrupt cache entry %s; refetching", path.name)
                path.unlink(missing_ok=True)

        self.cache_misses += 1
        data = self._network_get(url, params)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"url": url, "params": params or {}, "data": data}),
            encoding="utf-8",
        )
        tmp.replace(path)  # atomic: a crash mid-write never corrupts the cache
        return data

    def _network_get(self, url: str, params: Optional[dict[str, Any]]) -> Any:
        """Rate-limited fetch with retries. Overridden by offline harnesses."""
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            self.rate_limiter.wait()
            try:
                return super()._get(url, params)  # type: ignore[misc]
            except DataFetchError as exc:
                last_exc = exc
                backoff = 2.0**attempt
                logger.warning(
                    "Fetch failed (attempt %d/%d), retrying in %.0fs: %s",
                    attempt + 1,
                    self.max_retries,
                    backoff,
                    exc,
                )
                time.sleep(backoff)
        raise last_exc  # type: ignore[misc]

    # -- helpers --------------------------------------------------------

    def _cache_path(self, url: str, params: Optional[dict[str, Any]]) -> Path:
        key = cache_key(url, params)
        subdir = self.cache_dir / key[:2]
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / f"{key}.json"

    def cache_stats(self) -> dict[str, int]:
        return {"hits": self.cache_hits, "misses": self.cache_misses}
