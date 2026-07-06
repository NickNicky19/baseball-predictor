"""File-based odds provider (CSV/JSON with date-aware resolution)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.data.odds.base import (
    FileOddsSettings,
    OddsProvider,
    load_csv_file,
    load_json_file,
    resolve_project_path,
)
from src.models.dataclasses import OddsLine
from src.utils.errors import OddsLoadError
from src.utils.logging import get_logger

logger = get_logger(__name__)


class FileOddsProvider(OddsProvider):
    """
    Loads OddsLine records from local CSV or JSON files.

    Resolution order for a game date:
        1. Date-specific file from date_pattern
        2. csv_path
        3. json_path (if set)
    """

    def __init__(
        self,
        settings: FileOddsSettings,
        project_root: Optional[Path] = None,
        enabled: bool = True,
    ):
        self.settings = settings
        self.project_root = project_root or Path(__file__).resolve().parents[3]
        self._enabled = enabled

    @property
    def source_name(self) -> str:
        return "file"

    def is_available(self) -> bool:
        return self._enabled

    def resolve_path(self, game_date: Optional[str] = None) -> Optional[Path]:
        candidates: list[Path] = []
        if game_date and self.settings.date_pattern:
            candidates.append(resolve_project_path(
                self.project_root,
                self.settings.date_pattern.format(date=game_date),
            ))
        if self.settings.csv_path:
            candidates.append(resolve_project_path(self.project_root, self.settings.csv_path))
        if self.settings.json_path:
            candidates.append(resolve_project_path(self.project_root, self.settings.json_path))

        for path in candidates:
            if path.exists():
                return path
        return None

    def load(self, game_date: Optional[str] = None) -> list[OddsLine]:
        if not self._enabled:
            return []

        path = self.resolve_path(game_date)
        if path is None:
            if self.settings.require_file:
                raise OddsLoadError(
                    f"No odds file found for date {game_date or 'default'}",
                    hint="Set odds.file.csv_path or place a date-specific file via odds.file.date_pattern",
                )
            logger.debug("File odds provider: no file found for %s", game_date)
            return []

        lines = load_json_file(path) if path.suffix.lower() == ".json" else load_csv_file(path)
        logger.info("File odds provider loaded %d lines from %s", len(lines), path.name)
        return lines