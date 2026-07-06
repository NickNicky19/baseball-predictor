"""Composite odds provider — merges multiple sources with configurable precedence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from src.data.odds.base import OddsSettings, merge_odds_lines
from src.data.odds.file_provider import FileOddsProvider
from src.data.odds.odds_api_provider import OddsAPIProvider
from src.models.dataclasses import OddsLine
from src.utils.errors import OddsLoadError
from src.utils.logging import get_logger

logger = get_logger(__name__)


class CompositeOddsProvider:
    """
    Facade that orchestrates configured OddsProvider implementations.

    This is the primary entry point used by DailyPredictor and the CLI.
    """

    def __init__(
        self,
        settings: OddsSettings,
        project_root: Optional[Path] = None,
        providers: Optional[list] = None,
    ):
        self.settings = settings
        self.project_root = project_root or Path(__file__).resolve().parents[3]
        self._providers = providers or self._build_providers()

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        project_root: Optional[Path] = None,
    ) -> CompositeOddsProvider:
        return cls(OddsSettings.from_config(config), project_root=project_root)

    def is_enabled(self) -> bool:
        return self.settings.enabled

    def active_sources(self) -> list[str]:
        return [p.source_name for p in self._providers if p.is_available()]

    def load(self, game_date: Optional[str] = None) -> list[OddsLine]:
        if not self.settings.enabled:
            return []

        collected: list[tuple[str, list[OddsLine]]] = []
        errors: list[str] = []

        for provider in self._providers:
            if not provider.is_available():
                continue
            try:
                lines = provider.load(game_date)
                if lines:
                    collected.append((provider.source_name, lines))
                    logger.debug("%s returned %d lines", provider.source_name, len(lines))
            except OddsLoadError as exc:
                errors.append(f"{provider.source_name}: {exc}")
                logger.warning("Odds provider %s failed: %s", provider.source_name, exc)

        if not collected:
            if self.settings.require_any_source and errors:
                raise OddsLoadError(
                    "All odds providers failed",
                    hint="; ".join(errors),
                )
            if self.settings.require_any_source:
                raise OddsLoadError(
                    f"No odds available for {game_date or 'default'}",
                    hint="Enable odds.file or odds.odds_api in config",
                )
            return []

        prefer_api = self.settings.prefer_source.lower() == "api"
        if prefer_api:
            collected.sort(key=lambda item: 0 if item[0] == "odds_api" else 1)

        groups = [lines for _, lines in collected]
        merged = merge_odds_lines(*groups, prefer_first=not prefer_api)
        logger.info("Merged %d odds lines from sources %s", len(merged), self.active_sources())
        return merged

    def _available_names(self) -> list[str]:
        return [p.source_name for p in self._providers if p.is_available()]

    def _build_providers(self) -> list:
        providers = []
        sources = {s.lower() for s in self.settings.sources}

        if "file" in sources:
            providers.append(
                FileOddsProvider(
                    settings=self.settings.file,
                    project_root=self.project_root,
                    enabled=self.settings.enabled,
                )
            )
        if "odds_api" in sources or self.settings.odds_api.enabled:
            providers.append(OddsAPIProvider(settings=self.settings.odds_api))
        return providers