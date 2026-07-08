"""
Outcome recorder — closes the self-improvement loop.

Archives predictions, fetches MLB actuals when games are final, and appends
aligned prediction-outcome pairs to the retraining CSV.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.data.mlb_api import HittingStatsSnapshot, MLBStatsAPI, PitchingStatsSnapshot
from src.learning.prediction_archive import PredictionArchive
from src.models.dataclasses import DailyPrediction, PropCategory, PropProjection
from src.simulation.monte_carlo import FantasyScoring
from src.utils.errors import DataFetchError, RetrainError
from src.utils.logging import get_logger

logger = get_logger(__name__)

PAIR_COLUMNS = [
    "player_id",
    "player_name",
    "game_date",
    "category",
    "predicted_value",
    "actual_value",
    "confidence",
    # --- Additive detail columns (collection-safe: record-keeping only). ---
    # Hitter rows fill the batting fields; pitcher rows fill ip/bb/hr-allowed.
    # Raw components let any category actual be re-derived later and let the
    # calibration/IP work separate "rate was wrong" from "opportunity was
    # short" (e.g. K over-projection vs short outing).
    "actual_pa",
    "actual_hits",
    "actual_home_runs",
    "actual_runs",
    "actual_rbi",
    "actual_walks",
    "actual_strikeouts",
    "actual_ip",
    "actual_bb_allowed",
    "actual_hr_allowed",
]

# The original schema, kept for one-time migration of pre-existing CSVs.
LEGACY_PAIR_COLUMNS = PAIR_COLUMNS[:7]


@dataclass
class OutcomeRecordingSettings:
    """Configuration for automatic outcome pairing."""

    enabled: bool = False
    pairs_csv_path: str = "data/learning/prediction_outcomes.csv"
    predictions_dir: str = "data/learning/predictions"
    archive_on_predict: bool = True
    require_final_games: bool = True

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> OutcomeRecordingSettings:
        block = config.get("outcome_recording", {})
        retraining = config.get("retraining", {})
        return cls(
            enabled=bool(block.get("enabled", False)),
            pairs_csv_path=str(
                block.get("pairs_csv_path", retraining.get("pairs_csv_path", "data/learning/prediction_outcomes.csv"))
            ),
            predictions_dir=str(block.get("predictions_dir", "data/learning/predictions")),
            archive_on_predict=bool(block.get("archive_on_predict", True)),
            require_final_games=bool(block.get("require_final_games", True)),
        )


@dataclass
class OutcomeRecordingReport:
    """Summary of an outcome recording run."""

    game_date: str
    final_games: int
    pairs_appended: int
    pairs_skipped_duplicate: int
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_date": self.game_date,
            "final_games": self.final_games,
            "pairs_appended": self.pairs_appended,
            "pairs_skipped_duplicate": self.pairs_skipped_duplicate,
            "notes": self.notes,
        }


class OutcomeRecorder:
    """
    Archives predictions and records actual outcomes for retraining.

    Workflow:
        1. DailyPredictor archives predictions after predict() (optional)
        2. After slate completes: recorder.record_for_date(game_date)
        3. run_retrain.py consumes prediction_outcomes.csv
    """

    def __init__(
        self,
        mlb_api: Optional[MLBStatsAPI] = None,
        archive: Optional[PredictionArchive] = None,
        settings: Optional[OutcomeRecordingSettings] = None,
        fantasy_scoring: Optional[FantasyScoring] = None,
        project_root: Optional[Path] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        self.config = config or {}
        self.settings = settings or OutcomeRecordingSettings.from_config(self.config)
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        season = int(self.config.get("season", 2026))
        self.mlb_api = mlb_api or MLBStatsAPI(season=season)
        self.archive = archive or PredictionArchive.from_config(self.config, self.project_root)
        self.fantasy_scoring = fantasy_scoring or FantasyScoring.from_config(self.config)

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        project_root: Optional[Path] = None,
    ) -> OutcomeRecorder:
        return cls(config=config, project_root=project_root)

    def should_archive_predictions(self) -> bool:
        return self.settings.enabled and self.settings.archive_on_predict

    def archive_prediction(self, prediction: DailyPrediction) -> Optional[Path]:
        if not self.should_archive_predictions():
            return None
        return self.archive.save(prediction)

    def record_for_date(self, game_date: str) -> OutcomeRecordingReport:
        """
        Pair archived predictions with MLB actuals and append to pairs CSV.

        Raises RetrainError when no archive exists. Returns a report with 0
        pairs when games are not final yet (unless require_final_games=False).
        """
        prediction = self.archive.load(game_date)
        if prediction is None:
            raise RetrainError(
                f"No archived predictions for {game_date}",
                hint="Run predictions with outcome_recording.enabled and archive_on_predict",
            )

        final_games = self.mlb_api.get_final_game_pks(game_date)
        if self.settings.require_final_games and not final_games:
            return OutcomeRecordingReport(
                game_date=game_date,
                final_games=0,
                pairs_appended=0,
                pairs_skipped_duplicate=0,
                notes="No final games yet; try again after the slate completes",
            )

        try:
            hitting, pitching = self.mlb_api.get_actuals_for_date(game_date)
        except DataFetchError:
            raise

        rows, skipped = self._build_pair_rows(prediction, hitting, pitching)
        appended = self._append_pairs(rows)

        return OutcomeRecordingReport(
            game_date=game_date,
            final_games=len(final_games),
            pairs_appended=appended,
            pairs_skipped_duplicate=skipped,
            notes=f"Recorded {appended} new pairs for {game_date}",
        )

    def _build_pair_rows(
        self,
        prediction: DailyPrediction,
        hitting: dict[int, HittingStatsSnapshot],
        pitching: dict[int, PitchingStatsSnapshot],
    ) -> tuple[list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        skipped = 0
        existing = self._load_existing_keys()

        for projection in prediction.hitter_projections + prediction.pitcher_projections:
            actual = self._actual_for_projection(projection, hitting, pitching)
            if actual is None:
                continue
            key = (projection.player_id, projection.game_date, projection.category)
            if key in existing:
                skipped += 1
                continue
            row = {
                "player_id": projection.player_id,
                "player_name": projection.player_name,
                "game_date": projection.game_date,
                "category": projection.category,
                "predicted_value": projection.projected_value,
                "actual_value": round(actual, 3),
                "confidence": projection.confidence,
            }
            row.update(self._actual_detail_fields(projection, hitting, pitching))
            rows.append(row)
        return rows, skipped

    @staticmethod
    def _actual_detail_fields(
        projection: PropProjection,
        hitting: dict[int, HittingStatsSnapshot],
        pitching: dict[int, PitchingStatsSnapshot],
    ) -> dict[str, Any]:
        """
        Raw boxscore components for the row's player (additive columns).

        Purely record-keeping: values come from the same snapshots already
        fetched for actual_value. Fields that don't apply stay blank.
        """
        detail: dict[str, Any] = {col: "" for col in PAIR_COLUMNS[7:]}

        if projection.category == "strikeouts":
            stats = pitching.get(projection.player_id)
            if stats is not None:
                detail["actual_ip"] = round(float(stats.innings_pitched), 2)
                detail["actual_strikeouts"] = int(stats.strikeouts)
                detail["actual_bb_allowed"] = int(stats.walks)
                detail["actual_hr_allowed"] = int(stats.home_runs)
            return detail

        stats = hitting.get(projection.player_id)
        if stats is not None:
            detail["actual_pa"] = int(stats.pa)
            detail["actual_hits"] = int(stats.hits)
            detail["actual_home_runs"] = int(stats.home_runs)
            detail["actual_runs"] = int(stats.runs)
            detail["actual_rbi"] = int(stats.rbi)
            detail["actual_walks"] = int(stats.walks)
            detail["actual_strikeouts"] = int(stats.strikeouts)
        return detail

    def _actual_for_projection(
        self,
        projection: PropProjection,
        hitting: dict[int, HittingStatsSnapshot],
        pitching: dict[int, PitchingStatsSnapshot],
    ) -> Optional[float]:
        category = projection.category
        if category == "strikeouts":
            stats = pitching.get(projection.player_id)
            if stats is None:
                return None
            return float(stats.strikeouts)

        stats = hitting.get(projection.player_id)
        if stats is None:
            return None
        return compute_actual_value(stats, category, self.fantasy_scoring)

    def _append_pairs(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        path = self._pairs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_schema(path)
        write_header = not path.exists() or path.stat().st_size == 0

        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=PAIR_COLUMNS, restval="")
            if write_header:
                writer.writeheader()
            writer.writerows(rows)

        logger.info("Appended %d pairs to %s", len(rows), path)
        return len(rows)

    @staticmethod
    def _migrate_legacy_schema(path: Path) -> None:
        """
        One-time upgrade of a legacy 7-column pairs CSV to the current schema.

        Existing rows are preserved verbatim with blanks in the new detail
        columns. No-op when the file is absent, empty, or already current.
        Appending new-schema rows to a legacy-header file would silently
        misalign columns, so this must run before any append.
        """
        if not path.exists() or path.stat().st_size == 0:
            return
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                return
            if header == PAIR_COLUMNS:
                return
            if header != LEGACY_PAIR_COLUMNS:
                logger.warning(
                    "Pairs CSV %s has unrecognized header; leaving untouched", path
                )
                return
        # Re-read rows as dicts against the legacy header.
        with path.open(newline="", encoding="utf-8") as handle:
            dict_reader = csv.DictReader(handle)
            legacy_rows = list(dict_reader)

        tmp_path = path.with_suffix(".migrating.tmp")
        with tmp_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=PAIR_COLUMNS, restval="")
            writer.writeheader()
            writer.writerows(legacy_rows)
        tmp_path.replace(path)
        logger.info(
            "Migrated pairs CSV %s from legacy schema (%d rows preserved)",
            path,
            len(legacy_rows),
        )

    def _pairs_path(self) -> Path:
        path = Path(self.settings.pairs_csv_path)
        if not path.is_absolute():
            path = self.project_root / path
        return path

    def _load_existing_keys(self) -> set[tuple[int, str, str]]:
        path = self._pairs_path()
        if not path.exists():
            return set()
        keys: set[tuple[int, str, str]] = set()
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    keys.add((int(row["player_id"]), row["game_date"], row["category"]))
                except (KeyError, ValueError):
                    continue
        return keys


def compute_actual_value(
    stats: HittingStatsSnapshot,
    category: PropCategory,
    fantasy: FantasyScoring,
) -> float:
    """Map boxscore hitting stats to a prop category actual value."""
    if category == "hits":
        return float(stats.hits)
    if category == "home_runs":
        return float(stats.home_runs)
    if category == "hrr":
        return float(stats.hits + stats.runs + stats.rbi)
    if category == "fantasy":
        singles = max(0, stats.hits - stats.doubles - stats.triples - stats.home_runs)
        return float(
            singles * fantasy.single
            + stats.doubles * fantasy.double
            + stats.triples * fantasy.triple
            + stats.home_runs * fantasy.home_run
            + stats.rbi * fantasy.rbi
            + stats.runs * fantasy.run
            + stats.walks * fantasy.walk
        )
    return float(stats.hits)
