"""
Prediction archive — persists daily projections for later outcome pairing.

FIX (this revision): _dict_to_projection SET simulation=None UNCONDITIONALLY.

    def _dict_to_projection(data):
        return PropProjection(
            ...,
            simulation=None,          # <-- ALWAYS None, on EVERY load
        )

The simulation block IS on disk -- _projection_to_dict writes n_sims, mean,
median, p10, p90, and p_ge_threshold, and a spot check of the live archives
confirms it (786/786 projections in predictions_2026-07-10.json carry a
populated p_ge_threshold). The WRITE side was never the problem. The READ side
threw it away.

WHAT THAT BROKE
  * EdgeCalculator._model_prob_over reads
    projection.simulation.p_ge_threshold to get the model's P(over line). With
    simulation=None it falls back to a normal approximation around the point
    estimate using a HARD-CODED default spread (edge.default_spreads) -- i.e.
    it silently swaps the Monte Carlo tail probability, which is the whole
    product, for a Gaussian guess. Every archived-prediction edge calculation
    has been running on that fallback.
  * Anything downstream that needs the distribution (CLV, calibration against
    the p_ge thresholds, shadow grading) gets None and either crashes or
    silently degrades.

THE THREE THINGS THIS FIX HAS TO GET RIGHT  (rule 8 -- read the write side
before writing the read side; do not assume the shape)

  1. p_ge_threshold KEYS ARE FLOATS IN MEMORY, STRINGS ON DISK.
     _projection_to_dict does `dict(sim.p_ge_threshold)` on a
     dict[float, float] -> {1.0: 0.68}. json.dump STRINGIFIES those keys ->
     {"1.0": 0.68}. If the loader does not convert them BACK to float, then
     `p_ge_threshold.get(1.0)` returns None on every lookup and the block is
     present-but-useless -- a silent failure worse than the original bug,
     because it LOOKS restored.

  2. MonteCarloResult REQUIRES `category`, WHICH THE SIMULATION BLOCK DOES NOT
     CARRY. It is only on the parent projection. Take it from there.

  3. per_game_samples IS NOT SERIALIZED (deliberately -- it would be enormous).
     It loads as None. That is correct and expected; callers that need raw
     samples must re-simulate, not read the archive.

KNOWN REMAINING GAP (stated, not silently ignored -- rule 9):
  `outcome_probs` (OutcomeProbabilities) is on PropProjection but is NEVER
  SERIALIZED by _projection_to_dict. So it CANNOT be restored here -- there is
  nothing on disk to restore. This fix does not pretend otherwise; it leaves
  outcome_probs=None and this note. Fixing that requires changing the WRITE
  side too, which would change the archive format.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from src.models.dataclasses import DailyPrediction, MonteCarloResult, PropProjection
from src.utils.errors import ConfigError
from src.utils.logging import get_logger

logger = get_logger(__name__)


class PredictionArchive:
    """Saves and loads DailyPrediction snapshots by game date."""

    def __init__(
        self,
        archive_dir: str = "data/learning/predictions",
        project_root: Optional[Path] = None,
    ):
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        self.archive_dir = self.project_root / archive_dir

    @classmethod
    def from_config(cls, config: dict[str, Any], project_root: Optional[Path] = None) -> PredictionArchive:
        block = config.get("outcome_recording", {})
        return cls(
            archive_dir=str(block.get("predictions_dir", "data/learning/predictions")),
            project_root=project_root,
        )

    def save(self, prediction: DailyPrediction) -> Path:
        """Persist a DailyPrediction snapshot."""
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        path = self.archive_dir / f"predictions_{prediction.game_date.isoformat()}.json"
        path.write_text(json.dumps(prediction.to_dict(), indent=2), encoding="utf-8")
        logger.info("Archived predictions for %s to %s", prediction.game_date, path.name)
        return path

    def load(self, game_date: str) -> Optional[DailyPrediction]:
        """Load an archived prediction if it exists."""
        path = self.archive_dir / f"predictions_{game_date}.json"
        if not path.exists():
            return None
        # utf-8-sig transparently strips a BOM if an editor wrote one. Mirrors
        # BiasCorrector.load, which hit exactly this.
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return _dict_to_prediction(data)

    def exists(self, game_date: str) -> bool:
        return (self.archive_dir / f"predictions_{game_date}.json").exists()

    def list_dates(self) -> list[str]:
        if not self.archive_dir.exists():
            return []
        dates: list[str] = []
        for path in sorted(self.archive_dir.glob("predictions_*.json")):
            dates.append(path.stem.replace("predictions_", ""))
        return dates


def _dict_to_prediction(data: dict[str, Any]) -> DailyPrediction:
    from datetime import date as date_cls

    hitter = [_dict_to_projection(p) for p in data.get("hitter_projections", [])]
    pitcher = [_dict_to_projection(p) for p in data.get("pitcher_projections", [])]
    return DailyPrediction(
        game_date=date_cls.fromisoformat(data["game_date"]),
        hitter_projections=hitter,
        pitcher_projections=pitcher,
        value_plays=[],
    )


def _thresholds_to_float_keys(raw: Any) -> dict[float, float]:
    """JSON stringifies the float keys of p_ge_threshold. Convert them BACK.

    In memory the dict is {1.0: 0.68}; on disk it is {"1.0": 0.68}. Every
    consumer looks up by FLOAT (EdgeCalculator does
    `p_ge_threshold.get(line)` with line=1.0; run_gate_reconstruct probes
    float(k) first). A str key silently misses EVERY lookup -- the block would
    be present but dead, which is worse than absent because it looks fine.

    A key that cannot be parsed as a float is DROPPED with a warning, never
    coerced to a guess.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[float, float] = {}
    for k, v in raw.items():
        try:
            out[float(k)] = float(v)
        except (TypeError, ValueError):
            logger.warning(
                "Archive: dropping un-parseable p_ge_threshold entry %r: %r", k, v
            )
    return out


def _dict_to_simulation(
    raw: Optional[dict[str, Any]], category: str
) -> Optional[MonteCarloResult]:
    """Rebuild MonteCarloResult from the archived simulation block.

    `category` comes from the PARENT projection: the serialized block does NOT
    carry it (see _projection_to_dict), but MonteCarloResult requires it.

    per_game_samples is NOT serialized (it would be enormous) and loads as
    None. Callers needing raw samples must re-simulate.
    """
    if not raw:
        return None
    try:
        return MonteCarloResult(
            n_sims=int(raw.get("n_sims", 0)),
            category=category,               # not in the block; from the parent
            mean=float(raw.get("mean", 0.0)),
            median=float(raw.get("median", 0.0)),
            p10=float(raw.get("p10", 0.0)),
            p90=float(raw.get("p90", 0.0)),
            p_ge_threshold=_thresholds_to_float_keys(raw.get("p_ge_threshold")),
            per_game_samples=None,
        )
    except (TypeError, ValueError) as exc:
        # A malformed block must not kill a whole day's load. Degrade to None
        # (the pre-fix behaviour) and say so LOUDLY, rather than crash.
        logger.warning(
            "Archive: simulation block for category=%s is malformed (%s); "
            "loading it as None. Downstream P(over) will fall back to a normal "
            "approximation.",
            category,
            exc,
        )
        return None


def _dict_to_projection(data: dict[str, Any]) -> PropProjection:
    category = data["category"]
    return PropProjection(
        player_id=int(data["player_id"]),
        player_name=str(data["player_name"]),
        category=category,
        game_date=str(data["game_date"]),
        projected_value=float(data["projected_value"]),
        confidence=float(data.get("confidence", 0.5)),
        # THE FIX. Was: simulation=None (unconditionally, discarding the block
        # that is right there on disk).
        simulation=_dict_to_simulation(data.get("simulation"), category),
        # outcome_probs is NOT serialized by _projection_to_dict, so there is
        # nothing on disk to restore. Left None deliberately; see the module
        # docstring. Restoring it requires a WRITE-side change.
        outcome_probs=None,
        team=str(data.get("team", "")),
        opponent=str(data.get("opponent", "")),
        opposing_pitcher=str(data.get("opposing_pitcher", "")),
        lineup_status=data.get("lineup_status", "unknown"),
    )
