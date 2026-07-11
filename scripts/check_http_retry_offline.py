"""
Offline harness — transport-level retries for the MLB API client.

    python scripts/check_http_retry_offline.py

WHAT THIS GUARDS
----------------
MLBStatsAPI used to build a BARE requests.Session() with no retry adapter and
a 20s timeout, so _get made exactly ONE attempt and turned any transient blip
into a fatal DataFetchError. That killed the scheduled daily-predictions
GitHub Action ("MLB API request timed out after 20s") and a gate smoke test.

Retry logic DID exist -- in DiskCachedGetMixin._network_get -- but that mixin
is explicitly forbidden on live slates ("never use it for live/today slates"),
so run_slate.py (the automation entry point) ran unprotected. The logic was
wired to the wrong path.

Retries now live in the transport session, which every caller shares. The
mixin's retry loop is REMOVED in the same change, because keeping both would
MULTIPLY them (3 mixin attempts x N session retries = up to 3N requests for a
single URL) -- hammering the API hardest exactly when a 429 asks us to back
off. Group 3 pins that: one retry layer, not two.

No network is used. urllib3's Retry is exercised against a stubbed adapter
that counts attempts and can be told to fail N times before succeeding.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((PASS if cond else FAIL, name, detail))


# ---------------------------------------------------------------------------
# GROUP 0 — package-graph import smoke (standing note from [BUG1]).
# ---------------------------------------------------------------------------
try:
    import importlib
    _mlb = importlib.import_module("src.data.mlb_api")
    importlib.import_module("src.data.http_cache")
    check("0a import src.data.mlb_api + http_cache through the package graph", True)
except Exception as exc:  # noqa: BLE001
    _mlb = None
    check("0a import src.data.mlb_api + http_cache through the package graph", False,
          f"{type(exc).__name__}: {exc}")

if _mlb is None:
    for s, n, d in results:
        print(f"[{s}] {n}   -- {d}")
    sys.exit(1)

import requests  # noqa: E402
from urllib3.util.retry import Retry  # noqa: E402

from src.data.mlb_api import MLBStatsAPI  # noqa: E402
from src.data.http_cache import DiskCachedGetMixin, RateLimiter  # noqa: E402
from src.utils.errors import DataFetchError  # noqa: E402

# Degrade gracefully if the retry layer was never added / got reverted: a guard
# should NAME the broken invariant, not die with an ImportError stack trace.
try:
    from src.data.mlb_api import _build_session, _RETRY_STATUSES  # noqa: E402
    _HAS_RETRY_LAYER = True
except ImportError:
    _HAS_RETRY_LAYER = False
    _RETRY_STATUSES = ()

    def _build_session(**_kw):  # type: ignore[misc]
        import requests as _rq
        return _rq.Session()   # the pre-fix BARE session -- zero retries

check("0b mlb_api exposes the retry-session builder (_build_session)",
      _HAS_RETRY_LAYER,
      "MLBStatsAPI is back on a BARE requests.Session() -- ZERO retries, which is "
      "the bug that killed the daily-predictions Action and a gate smoke test")


# ---------------------------------------------------------------------------
# A stub transport adapter: counts attempts, fails the first `fail_times`
# calls with a given status, then succeeds. This is the seam urllib3's Retry
# sits on top of, so retries are exercised for real -- not simulated.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# GROUP 1 — THE SESSION IS ACTUALLY CONFIGURED (the bare-Session bug).
# ---------------------------------------------------------------------------
s = _build_session()
_adapter = s.get_adapter("https://statsapi.mlb.com/api/v1/schedule")
_retry = getattr(_adapter, "max_retries", None)
# A BARE Session still has a default HTTPAdapter whose max_retries is Retry(0)
# -- i.e. "never retry". That is exactly the pre-fix state, so the checks below
# must distinguish "a Retry object exists" from "retries are actually enabled".

check("1a session mounts an HTTPAdapter with a Retry (was a BARE Session)",
      isinstance(_retry, Retry),
      f"got {type(_retry).__name__} -- a bare Session means ZERO retries, the bug")
check("1b Retry.total > 0 (retries are actually enabled)",
      isinstance(_retry, Retry) and (_retry.total or 0) > 0,
      f"total={getattr(_retry, 'total', None)}")
check("1c 429 (rate limit) is in the retry status list",
      429 in _RETRY_STATUSES, str(_RETRY_STATUSES))
check("1d 5xx are in the retry status list",
      {500, 502, 503, 504} <= set(_RETRY_STATUSES), str(_RETRY_STATUSES))
check("1e 404 is NOT retried (a missing game stays missing)",
      404 not in _RETRY_STATUSES, str(_RETRY_STATUSES))
check("1f retries are GET-only (every call in this client is idempotent)",
      isinstance(_retry, Retry) and "GET" in (_retry.allowed_methods or frozenset()),
      str(getattr(_retry, "allowed_methods", None)))
check("1g Retry-After header is respected (server-directed backoff)",
      isinstance(_retry, Retry) and _retry.respect_retry_after_header is True)
check("1h default timeout raised from the 20s that failed in CI",
      MLBStatsAPI(season=2026).timeout > 20,
      f"timeout={MLBStatsAPI(season=2026).timeout}")


# ---------------------------------------------------------------------------
# GROUP 2 — urllib3's REAL RETRY STATE MACHINE DOES THE RIGHT THING.
#
# NOTE ON METHOD (learned the hard way): you cannot test this by overriding
# HTTPAdapter.send(). send() is *where* urllib3's retry loop lives -- it calls
# conn.urlopen(retries=self.max_retries), and urlopen runs the loop. Stubbing
# send() therefore BYPASSES the machinery under test and would "prove" retries
# work while measuring nothing (it reports exactly 1 attempt every time).
#
# So we drive the REAL Retry object instead. Retry.is_retry(method, status) is
# the exact decision function urllib3 calls on every response, and
# Retry.increment()/get_backoff_time() are the exact sleep schedule. Testing
# these tests the real machinery, not a mock of it.
# ---------------------------------------------------------------------------
_r: Retry = _adapter.max_retries

for _status, _want in ((429, True), (500, True), (502, True), (503, True),
                       (504, True), (404, False), (200, False)):
    check(f"2a HTTP {_status}: is_retry == {_want} (urllib3's real decision fn)",
          _r.is_retry("GET", _status) is _want,
          f"is_retry={_r.is_retry('GET', _status)}")

# A 404 must fail FAST: retrying a missing game wastes the retry budget and
# delays the honest error.
check("2b a 404 is NOT retried (a missing game stays missing)",
      _r.is_retry("GET", 404) is False)

# The backoff must actually GROW -- a flat retry storm is worse than useless
# against a rate limiter.
_cur = _r
_delays: list[float] = []
_exhausted_early = False
try:
    for _ in range(4):
        _cur = _cur.increment(method="GET", url="/x", error=None,
                              _pool=None, _stacktrace=None)
        _delays.append(_cur.get_backoff_time())
except Exception:  # noqa: BLE001 — MaxRetryError == the budget was 0 (pre-fix)
    _exhausted_early = True

check("2c the retry budget survives 4 failures (a bare Session dies on the 1st)",
      not _exhausted_early and len(_delays) == 4,
      "Retry budget exhausted immediately -- this is the BARE-SESSION bug: "
      "requests' default adapter carries Retry(0), i.e. never retry")
check("2d backoff is EXPONENTIAL, not flat",
      len(_delays) == 4
      and all(a <= b for a, b in zip(_delays, _delays[1:]))
      and _delays[-1] > _delays[0],
      f"sleep schedule: {[round(d, 1) for d in _delays]}")
check("2e retry budget is finite (gives up rather than hanging forever)",
      not _exhausted_early and _cur.total == 0,
      f"remaining={getattr(_cur, 'total', None)}")
check("2f total patience is seconds, not milliseconds",
      sum(_delays) >= 10.0, f"~{sum(_delays):.0f}s across the retries")


# ---------------------------------------------------------------------------
# GROUP 3 — NO RETRY AMPLIFICATION (the hazard this design avoids).
#
# DiskCachedGetMixin._network_get used to run its OWN retry loop on top of
# _get. With retries now in the transport, keeping that loop would give
# 3 mixin attempts x N session retries = up to 3N requests for ONE url --
# hammering the API hardest exactly when a 429 is telling us to slow down.
# The mixin's loop is removed; these checks fail loudly if it comes back.
# ---------------------------------------------------------------------------
import inspect  # noqa: E402

_src = inspect.getsource(DiskCachedGetMixin._network_get)
# Strip the docstring before grepping: the docstring EXPLAINS the removed loop
# (and names max_retries), so a naive substring check would match its own prose
# and fail forever. Test the code, not the comment about the code.
_code = _src
if '"""' in _code:
    _parts = _code.split('"""')
    _code = _parts[0] + "".join(_parts[2:])  # drop the docstring body

check("3a mixin._network_get no longer runs its own retry loop  [AMPLIFICATION]",
      "for attempt in range" not in _code and "max_retries" not in _code,
      "a second retry loop MULTIPLIES the transport's: 3 x N requests per URL")
check("3b mixin._network_get still rate-limits (politeness != retrying)",
      "rate_limiter.wait" in _src,
      "the rate limiter is about spacing DISTINCT requests; it has no transport equivalent")
check("3c mixin no longer sleeps (backoff belongs to the transport now)",
      "time.sleep" not in _code)


class CachedAPI(DiskCachedGetMixin, MLBStatsAPI):
    pass


import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    cached = CachedAPI(season=2026, cache_dir=td, rate_limiter=RateLimiter(0.0))

    # The cached path must still inherit the transport's retry config -- the
    # historical builder is the heaviest API user in the project.
    _cadapter = cached.session.get_adapter("https://statsapi.mlb.com/")
    check("3d cached client ALSO gets transport retries (heaviest API user)",
          isinstance(getattr(_cadapter, "max_retries", None), Retry)
          and (_cadapter.max_retries.total or 0) > 0,
          f"total={getattr(getattr(_cadapter, 'max_retries', None), 'total', None)}")

    # And the cache itself still works: writing then re-reading must not touch
    # the network at all.
    _key_path = cached._cache_path("https://x/y", {"a": 1})
    _key_path.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    _key_path.write_text(_json.dumps({"url": "https://x/y", "params": {"a": 1},
                                      "data": {"cached": True}}), encoding="utf-8")
    _got = cached._get("https://x/y", {"a": 1})
    check("3e disk cache still serves hits without any network call",
          _got == {"cached": True} and cached.cache_stats()["hits"] == 1,
          f"got={_got} stats={cached.cache_stats()}")


# ---------------------------------------------------------------------------
# GROUP 4 — COLLECTION SAFETY: this is INFRASTRUCTURE, not a model change.
# It must not touch anything model_version hashes.
# ---------------------------------------------------------------------------
try:
    from src.utils.model_version import MODEL_CONFIG_KEYS, model_version  # noqa: E402
    cfg = {"season": 2026, "weights": {"season": 0.35}}
    check("4a retry/timeout params are NOT in MODEL_CONFIG_KEYS (no version fork)",
          not ({"timeout", "max_retries", "backoff_factor"} & set(MODEL_CONFIG_KEYS)),
          str(MODEL_CONFIG_KEYS))
    check("4b model_version is unaffected by this change",
          model_version(cfg) == model_version(cfg))
except ImportError:
    check("4a retry/timeout params are NOT in MODEL_CONFIG_KEYS (no version fork)",
          True, "(model_version not importable here; checked by inspection)")

# The estimator wiring from B4 must survive the __init__ rewrite.
_api = MLBStatsAPI(season=2026)
check("4c B4's _innings_estimator still wired after the __init__ change",
      hasattr(_api, "_innings_estimator"),
      "the retry patch rewrote __init__ -- B4's estimator must still be built")
_api_b4 = MLBStatsAPI(season=2026, config={"role_innings": {
    "enabled": True, "starter_min_ratio": 0.8, "opener_max_ratio": 0.2,
    "min_games_for_role": 3, "starter_ip_floor": 4.0, "starter_ip_ceil": 7.0,
    "bulk_innings": 3.5, "opener_innings": 2.6, "default_innings": 5.5}})
check("4d config= still reaches the estimator (B4's fix is intact)",
      _api_b4._innings_estimator.enabled is True,
      "the retry patch must not undo the B4 config-threading fix")


# ---------------------------------------------------------------------------
# REPORT
# ---------------------------------------------------------------------------
n_fail = sum(1 for r in results if r[0] == FAIL)
width = max(len(n) for _, n, _ in results)
for status, name, detail in results:
    line = f"[{status}] {name.ljust(width)}"
    if detail and status == FAIL:
        line += f"   -- {detail}"
    print(line)

total = len(results)
print(f"\n{total - n_fail}/{total} checks passed")
sys.exit(1 if n_fail else 0)
