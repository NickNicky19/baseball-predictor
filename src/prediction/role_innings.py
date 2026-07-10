"""
Role-aware expected_innings estimator (B4).

WHAT THIS FIXES
---------------
The simulator over-projects strikeouts for pitchers who will not actually
face ~23 batters. Pitcher K point estimate is

    projected_k = k_prob * expected_innings * PA_PER_INNING   (prop_engine)

so an inflated expected_innings inflates K directly. The pre-B4 estimator
(mlb_api._estimate_expected_ip) has three failure modes that all bias
expected_innings UP for short-outing arms:

  1. games_started was floored to >=1, so a pure reliever / opener with zero
     starts divided total relief innings by a PHANTOM start and reported a
     starter-length outing.
  2. a hard floor of 4.0 IP meant an opener (typically ~1-2 IP) still
     projected >=4.0 -> ~17 batters of strikeout exposure it never gets.
  3. it was role-BLIND: no starter / opener / bulk distinction at all.

WHY THIS IS A GATED LIVE-MODEL CHANGE (per PROJECT_CONTEXT.md)
-------------------------------------------------------------
Changing expected_innings changes the shipped projected_value for pitchers.
That is a live-model output change -> it must FORK model_version and clear
promotion gate #4 before it touches the live path.

model_version is a pure hash of the config DICT (src/utils/model_version.py).
So B4 forks the version BY CONSTRUCTION only if its parameters live in the
config dict. This module therefore reads its parameters from a required
config["role_innings"] block. This is the deliberate INVERSE of B3, which
used a .get(..., 0.0) fallback specifically to AVOID moving the hash while
staying additive. B4 wants the hash to move, so it does not hide behind a
fallback for its numeric parameters.

COLLECTION SAFETY
-----------------
role_innings.enabled defaults to False. When disabled, estimate() reproduces
the pre-B4 _estimate_expected_ip output BYTE-FOR-BYTE (see the offline
harness), so nothing about the live path moves during collection. B4 only
changes behaviour once someone flips enabled=True in config.json -- at which
point the config hash has ALREADY forked, so the provenance stamp and the
behaviour change move together, never independently.

ROLE DETECTION
--------------
Role is inferred from a MEASURED signal, not a hand-picked guess: the
appearances-vs-starts split. PitchingStatsSnapshot now carries `games`
(total appearances, from the same gamesPlayed field the hitter parser already
reads -- no new API call). start_ratio = games_started / games.

  start_ratio >= starter_min_ratio      -> STARTER
  start_ratio <= opener_max_ratio       -> OPENER / reliever
  in between                            -> BULK / swing

Thin-sample honesty (a recurring PROJECT_CONTEXT discipline): if `games` is
too small to trust the split (games < min_games_for_role), we do NOT invent a
role -- we fall back to the starter path on recent avg IP, which is the
pre-B4 behaviour, and flag it via RoleInningsResult.role == "unknown".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Kept in sync with mlb_api._estimate_expected_ip's historical constants so the
# DISABLED path is byte-identical. These are the LEGACY numbers, used only when
# role_innings is disabled or falls back.
_LEGACY_IP_FLOOR = 4.0
_LEGACY_IP_CEIL = 7.5
_LEGACY_DEFAULT_IP = 5.5


class RoleInningsConfigError(ValueError):
    """Raised when role_innings is enabled but its config block is malformed."""


@dataclass(frozen=True)
class RoleInningsResult:
    """expected_innings plus the reasoning, for logging / harness / audit."""

    expected_innings: float
    role: str                 # "starter" | "bulk" | "opener" | "unknown"
    start_ratio: Optional[float]
    used_b4: bool             # True only when the B4 role logic set the value


@dataclass(frozen=True)
class _RoleInningsParams:
    """Validated view of config["role_innings"]. All numbers required when enabled."""

    enabled: bool
    starter_min_ratio: float
    opener_max_ratio: float
    min_games_for_role: int
    starter_ip_floor: float
    starter_ip_ceil: float
    bulk_innings: float
    opener_innings: float
    default_innings: float

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "_RoleInningsParams":
        block = config.get("role_innings")
        if not block:
            # No block at all -> B4 is simply not present. Disabled, legacy path.
            return cls(
                enabled=False,
                starter_min_ratio=0.0,
                opener_max_ratio=0.0,
                min_games_for_role=0,
                starter_ip_floor=_LEGACY_IP_FLOOR,
                starter_ip_ceil=_LEGACY_IP_CEIL,
                bulk_innings=0.0,
                opener_innings=0.0,
                default_innings=_LEGACY_DEFAULT_IP,
            )

        enabled = bool(block.get("enabled", False))
        if not enabled:
            # Block present but off. Still legacy behaviour; don't demand the
            # numeric keys so a half-authored block can't break collection.
            return cls(
                enabled=False,
                starter_min_ratio=0.0,
                opener_max_ratio=0.0,
                min_games_for_role=0,
                starter_ip_floor=_LEGACY_IP_FLOOR,
                starter_ip_ceil=_LEGACY_IP_CEIL,
                bulk_innings=0.0,
                opener_innings=0.0,
                default_innings=_LEGACY_DEFAULT_IP,
            )

        # Enabled: every parameter is REQUIRED. No silent fallback -- a missing
        # key is a config bug we want to fail loudly, not paper over (and the
        # keys' presence is what forks model_version, so they must be real).
        required = (
            "starter_min_ratio",
            "opener_max_ratio",
            "min_games_for_role",
            "starter_ip_floor",
            "starter_ip_ceil",
            "bulk_innings",
            "opener_innings",
            "default_innings",
        )
        missing = [k for k in required if k not in block]
        if missing:
            raise RoleInningsConfigError(
                "role_innings.enabled=true but missing required keys: "
                + ", ".join(missing)
            )

        params = cls(
            enabled=True,
            starter_min_ratio=float(block["starter_min_ratio"]),
            opener_max_ratio=float(block["opener_max_ratio"]),
            min_games_for_role=int(block["min_games_for_role"]),
            starter_ip_floor=float(block["starter_ip_floor"]),
            starter_ip_ceil=float(block["starter_ip_ceil"]),
            bulk_innings=float(block["bulk_innings"]),
            opener_innings=float(block["opener_innings"]),
            default_innings=float(block["default_innings"]),
        )
        params._validate()
        return params

    def _validate(self) -> None:
        if not (0.0 <= self.opener_max_ratio < self.starter_min_ratio <= 1.0):
            raise RoleInningsConfigError(
                "require 0 <= opener_max_ratio < starter_min_ratio <= 1 "
                f"(got opener_max_ratio={self.opener_max_ratio}, "
                f"starter_min_ratio={self.starter_min_ratio})"
            )
        if self.starter_ip_floor > self.starter_ip_ceil:
            raise RoleInningsConfigError(
                "starter_ip_floor must be <= starter_ip_ceil "
                f"(got {self.starter_ip_floor} > {self.starter_ip_ceil})"
            )
        if self.min_games_for_role < 1:
            raise RoleInningsConfigError("min_games_for_role must be >= 1")
        for name in ("bulk_innings", "opener_innings", "default_innings"):
            if getattr(self, name) <= 0:
                raise RoleInningsConfigError(f"{name} must be > 0")


class RoleAwareInningsEstimator:
    """
    Turns a recent PitchingStatsSnapshot into an expected_innings value.

    When disabled, reproduces the pre-B4 heuristic exactly. When enabled,
    routes on the measured start_ratio to a role-specific innings estimate.
    """

    def __init__(self, config: dict[str, Any]):
        self._params = _RoleInningsParams.from_config(config)

    @property
    def enabled(self) -> bool:
        return self._params.enabled

    # ---- the pre-B4 heuristic, kept verbatim for the disabled path ----------
    @staticmethod
    def _legacy_expected_ip(recent: Any) -> float:
        """Exact reproduction of mlb_api._estimate_expected_ip (pre-B4)."""
        gs = getattr(recent, "games_started", 0)
        ip = getattr(recent, "innings_pitched", 0.0)
        if gs > 0 and ip > 0:
            avg_ip = ip / gs
            return round(max(_LEGACY_IP_FLOOR, min(_LEGACY_IP_CEIL, avg_ip)), 1)
        return _LEGACY_DEFAULT_IP

    def estimate_detailed(self, recent: Any) -> RoleInningsResult:
        p = self._params
        if not p.enabled:
            return RoleInningsResult(
                expected_innings=self._legacy_expected_ip(recent),
                role="legacy",
                start_ratio=None,
                used_b4=False,
            )

        gs = int(getattr(recent, "games_started", 0) or 0)
        ip = float(getattr(recent, "innings_pitched", 0.0) or 0.0)
        games = int(getattr(recent, "games", 0) or 0)

        # No usable innings at all -> nothing to estimate from. Default.
        if ip <= 0 or games <= 0:
            return RoleInningsResult(
                expected_innings=round(p.default_innings, 1),
                role="unknown",
                start_ratio=None,
                used_b4=True,
            )

        # Thin sample: don't invent a role. Fall back to the starter path on
        # recent avg IP (mirrors pre-B4), but flag it as unknown for auditing.
        if games < p.min_games_for_role:
            avg_ip = ip / max(gs, 1)
            val = round(max(p.starter_ip_floor, min(p.starter_ip_ceil, avg_ip)), 1)
            ratio = gs / games if games else None
            return RoleInningsResult(
                expected_innings=val,
                role="unknown",
                start_ratio=ratio,
                used_b4=True,
            )

        start_ratio = gs / games

        if start_ratio >= p.starter_min_ratio:
            # Genuine starter: recent avg IP PER START, clamped to a
            # starter-plausible band. This is the honest per-start length --
            # NOT total IP / phantom-1-start.
            avg_ip_per_start = ip / gs if gs > 0 else ip
            val = max(p.starter_ip_floor, min(p.starter_ip_ceil, avg_ip_per_start))
            role = "starter"
        elif start_ratio <= p.opener_max_ratio:
            # Opener / reliever getting a probable-pitcher nod: short outing.
            val = p.opener_innings
            role = "opener"
        else:
            # Swing / bulk role: neither a full starter nor a 1-inning opener.
            val = p.bulk_innings
            role = "bulk"

        return RoleInningsResult(
            expected_innings=round(val, 1),
            role=role,
            start_ratio=start_ratio,
            used_b4=True,
        )

    def estimate(self, recent: Any) -> float:
        """Convenience: just the innings value."""
        return self.estimate_detailed(recent).expected_innings
