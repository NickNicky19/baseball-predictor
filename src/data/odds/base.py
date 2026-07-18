"""
Odds provider interface and shared parsing utilities.

All odds sources implement OddsProvider so DailyPredictor and EdgeCalculator
remain decoupled from ingestion details.
"""

from __future__ import annotations

import csv
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.models.dataclasses import OddsLine, PropCategory
from src.utils.errors import OddsLoadError
from src.utils.logging import get_logger

logger = get_logger(__name__)

CATEGORY_ALIASES: dict[str, PropCategory] = {
    "hr": "home_runs",
    "home_run": "home_runs",
    "home_runs": "home_runs",
    "hits": "hits",
    "hrr": "hrr",
    "fantasy": "fantasy",
    "strikeouts": "strikeouts",
    "pitcher_k": "strikeouts",
    "k": "strikeouts",
    "batter_hits": "hits",
    "batter_home_runs": "home_runs",
    "total_bases": "total_bases",
    "batter_total_bases": "total_bases",
    "player_bases": "total_bases",
    "pitcher_strikeouts": "strikeouts",
}


@dataclass
class FileOddsSettings:
    """Local CSV/JSON odds file configuration."""

    csv_path: str = "data/odds/lines.csv"
    json_path: str = ""
    date_pattern: str = "data/odds/lines_{date}.csv"
    require_file: bool = False


@dataclass
class OddsAPISettings:
    """The Odds API (https://the-odds-api.com) configuration."""

    enabled: bool = False
    api_key_env: str = "ODDS_API_KEY"
    api_key: str = ""
    base_url: str = "https://api.the-odds-api.com/v4"
    sport_key: str = "baseball_mlb"
    regions: str = "us"
    bookmaker: str = ""
    markets: list[str] = field(
        default_factory=lambda: [
            "batter_hits",
            "batter_home_runs",
            "pitcher_strikeouts",
        ]
    )
    market_category_map: dict[str, str] = field(
        default_factory=lambda: {
            "batter_hits": "hits",
            "batter_home_runs": "home_runs",
            "pitcher_strikeouts": "strikeouts",
        }
    )
    timeout_seconds: int = 25


@dataclass
class OddsSettings:
    """Top-level odds configuration from config.json."""

    enabled: bool = False
    sources: list[str] = field(default_factory=lambda: ["file"])
    prefer_source: str = "api"
    min_edge_pct: float = 4.0
    require_any_source: bool = False
    file: FileOddsSettings = field(default_factory=FileOddsSettings)
    odds_api: OddsAPISettings = field(default_factory=OddsAPISettings)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> OddsSettings:
        odds = config.get("odds", {})
        api_block = odds.get("odds_api", {})
        file_block = odds.get("file", odds)

        return cls(
            enabled=bool(odds.get("enabled", False)),
            sources=list(odds.get("sources", ["file"])),
            prefer_source=str(odds.get("prefer_source", "api")),
            min_edge_pct=float(odds.get("min_edge_pct", 4.0)),
            require_any_source=bool(odds.get("require_any_source", odds.get("require_file", False))),
            file=FileOddsSettings(
                csv_path=str(file_block.get("csv_path", odds.get("csv_path", "data/odds/lines.csv"))),
                json_path=str(file_block.get("json_path", odds.get("json_path", ""))),
                date_pattern=str(
                    file_block.get("date_pattern", odds.get("date_pattern", "data/odds/lines_{date}.csv"))
                ),
                require_file=bool(file_block.get("require_file", odds.get("require_file", False))),
            ),
            odds_api=OddsAPISettings(
                enabled=bool(api_block.get("enabled", False)),
                api_key_env=str(api_block.get("api_key_env", "ODDS_API_KEY")),
                api_key=str(api_block.get("api_key", "")),
                base_url=str(api_block.get("base_url", "https://api.the-odds-api.com/v4")),
                sport_key=str(api_block.get("sport_key", "baseball_mlb")),
                regions=str(api_block.get("regions", "us")),
                bookmaker=str(api_block.get("bookmaker", "")),
                markets=list(api_block.get("markets", ["batter_hits", "batter_home_runs", "pitcher_strikeouts"])),
                market_category_map=dict(
                    api_block.get(
                        "market_category_map",
                        {
                            "batter_hits": "hits",
                            "batter_home_runs": "home_runs",
                            "pitcher_strikeouts": "strikeouts",
                        },
                    )
                ),
                timeout_seconds=int(api_block.get("timeout_seconds", 25)),
            ),
        )


class OddsProvider(ABC):
    """Abstract odds source — file, API, or future integrations."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Short identifier for logging and merge precedence."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True when this provider can attempt a load."""

    @abstractmethod
    def load(self, game_date: Optional[str] = None) -> list[OddsLine]:
        """Load odds lines for a slate date."""


def resolve_project_path(project_root: Path, relative: str) -> Path:
    path = Path(relative)
    if not path.is_absolute():
        path = project_root / relative
    return path


def normalize_category(raw: str, source: str = "") -> PropCategory:
    key = raw.strip().lower()
    category = CATEGORY_ALIASES.get(key)
    if category is None:
        raise OddsLoadError(
            f"Unknown odds category '{raw}'",
            hint=f"Valid categories: {', '.join(sorted(set(CATEGORY_ALIASES.values())))}",
        )
    return category


def parse_odds_row(row: dict[str, Any], source: Path | str) -> OddsLine:
    """Parse a CSV/JSON row into an OddsLine."""
    source_name = str(source)
    player = str(row.get("player_name", row.get("player", ""))).strip()
    if not player:
        raise OddsLoadError(f"Odds row missing player_name in {source_name}")

    raw_category = str(row.get("category", row.get("market", ""))).strip()
    category = normalize_category(raw_category, source_name)

    try:
        line = float(row["line"] if "line" in row else row.get("point", 0))
        over_odds = int(row.get("over_odds", row.get("over_odds_american", 0)))
        under_odds = int(row.get("under_odds", row.get("under_odds_american", 0)))
    except (KeyError, TypeError, ValueError) as exc:
        raise OddsLoadError(
            f"Invalid odds values for {player} ({category}) in {source_name}",
            hint="Required: line (float), over_odds (int), under_odds (int)",
        ) from exc

    if over_odds == 0 and under_odds == 0:
        raise OddsLoadError(f"Missing American odds for {player} ({category}) in {source_name}")

    return OddsLine(
        player_name=player,
        category=category,
        line=line,
        over_odds_american=over_odds,
        under_odds_american=under_odds,
        sportsbook=str(row.get("sportsbook", "")),
    )


def load_csv_file(path: Path) -> list[OddsLine]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise OddsLoadError(f"Odds CSV has no header: {path}")
            return [
                parse_odds_row(row, path)
                for row in reader
                if str(row.get("player_name", row.get("player", ""))).strip()
            ]
    except OddsLoadError:
        raise
    except OSError as exc:
        raise OddsLoadError(f"Cannot read odds file: {path}") from exc


def load_json_file(path: Path) -> list[OddsLine]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OddsLoadError(f"Cannot parse odds JSON: {path}") from exc

    rows = data if isinstance(data, list) else data.get("lines", [])
    if not isinstance(rows, list):
        raise OddsLoadError(f"Odds JSON must be a list or contain a 'lines' array: {path}")
    return [
        parse_odds_row(row, path)
        for row in rows
        if str(row.get("player_name", row.get("player", ""))).strip()
    ]


def merge_odds_lines(
    *line_groups: list[OddsLine],
    prefer_first: bool = True,
) -> list[OddsLine]:
    """
    Merge odds from multiple providers.

    Later groups overwrite earlier when prefer_first=False (API over file).
    """
    index: dict[tuple[str, PropCategory], OddsLine] = {}
    groups = line_groups if prefer_first else reversed(line_groups)
    for group in groups:
        for line in group:
            key = (line.player_name.lower(), line.category)
            index[key] = line
    return list(index.values())
