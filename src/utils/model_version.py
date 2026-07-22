"""
Model version tagging (discipline #5: data provenance).

Every recorded prediction->outcome pair is tagged with a short hash of the
config blocks that actually drive the model's numbers. When any coefficient
changes, the hash changes, so calibration and backtesting can filter to a
single model version instead of silently mixing behavior across a change.

Why a subset of config, not the whole file or a single block:
- The whole config includes odds paths, logging, retraining schedules, and
  other operational keys that don't affect a projection. Hashing those would
  churn the version for changes that don't alter model output.
- There is no single "model" block — the coefficients live across weights,
  pitcher_matchup, park_factors, recency, pitcher_regression, etc. Hashing
  only one would miss most real changes.

MODEL_CONFIG_KEYS is the allowlist of blocks that shape a projection. If you
add a new coefficient block to config, add its key here so version tracking
stays honest.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Config blocks whose values change model output. Keep in sync with config.json.
MODEL_CONFIG_KEYS: tuple[str, ...] = (
    "season",
    "league_avg",
    "weights",
    "recency",
    "pitcher_matchup",
    "simulation_slot_pa",
    "park_factors",
    "lineup_intelligence",
    "savant",
    "base_running",
    "pitcher_regression",
    "simulation",
    # HybridPASimulator coefficients and hash-bound fitted artifacts directly
    # change every hitter probability.  Omitting this block made the frozen
    # and K/BB research models share an identity despite different outputs.
    "pa_simulator",
    "feature_factory",
    "fantasy_scoring",
    # Candidate-only until its own hard-keyed market gate promotes it.  The
    # contract changes emitted probability support, so it must fork version.
    "total_bases",
    # B4: role-aware expected_innings shapes the pitcher K point estimate, so
    # its block must be in the allowlist for enabling it to fork model_version.
    "role_innings",
)


def model_version(config: dict[str, Any], length: int = 12) -> str:
    """Deterministic short hash of the model-relevant config blocks.

    Stable across runs (sorted keys, canonical JSON) so the same coefficients
    always produce the same tag. Missing blocks are simply omitted, so an
    older config without a newer block still hashes cleanly.
    """
    subset = {k: config[k] for k in MODEL_CONFIG_KEYS if k in config}
    canonical = json.dumps(subset, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]
