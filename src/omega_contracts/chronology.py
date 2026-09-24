"""Canonical date/time parsing and the unconditional May 2026 seal."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from .errors import ContractError

_CANONICAL_DATE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
_RFC3339_TIMESTAMP = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z"
)
_MAY_TOKEN_PATTERNS = (
    re.compile(r"(?<!\d)2026(?:05|[-_/.\\\s]+0?5)(?!\d{3})", re.IGNORECASE),
    re.compile(r"(?<![a-z])may(?:[-_/.\\\s]+)2026(?!\d)", re.IGNORECASE),
    re.compile(r"(?<!\d)2026(?:[-_/.\\\s]+)may(?![a-z])", re.IGNORECASE),
)


def parse_date(value: date | str, *, label: str = "date") -> date:
    """Accept a date object or canonical ISO date text; reject datetime subclasses."""
    if isinstance(value, datetime):
        raise ContractError(f"{label} must be a date, not a datetime")
    if isinstance(value, date):
        parsed = value
    elif isinstance(value, str) and _CANONICAL_DATE.fullmatch(value):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ContractError(f"{label} is not a valid calendar date") from exc
    else:
        raise ContractError(f"{label} must be canonical YYYY-MM-DD text or a date")
    assert_not_may_2026(parsed, label=label)
    return parsed


def parse_aware_utc(value: datetime | str, *, label: str = "timestamp") -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and _RFC3339_TIMESTAMP.fullmatch(value):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ContractError(f"{label} must be an RFC 3339 timestamp") from exc
    else:
        raise ContractError(f"{label} must be an aware datetime or canonical RFC 3339 text")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{label} must be timezone-aware")
    normalized = parsed.astimezone(timezone.utc)
    assert_not_may_2026(normalized.date(), label=f"{label} date")
    return normalized


def assert_not_may_2026(value: date, *, label: str = "date") -> None:
    if value.year == 2026 and value.month == 5:
        raise ContractError(f"{label} is sealed May 2026")


def require_before(observed: datetime, horizon: datetime, *, label: str) -> None:
    observed_utc = parse_aware_utc(observed, label=label)
    horizon_utc = parse_aware_utc(horizon, label="decision horizon")
    if observed_utc >= horizon_utc:
        raise ContractError(f"{label} must be strictly before the decision horizon")


def require_ordered_dates(*, fit_end: date | str, selection_start: date | str) -> None:
    left = parse_date(fit_end, label="fit_end")
    right = parse_date(selection_start, label="selection_start")
    if left >= right:
        raise ContractError("fit_end must be strictly before selection_start")


def assert_may_safe_path(path: Path, *, allowed_root: Path | None = None) -> Path:
    """Reject lexical and resolved May tokens before callers open a path."""
    raw = unicodedata.normalize("NFKC", str(path))
    decoded = raw
    for _ in range(3):
        newer = unquote(decoded)
        if newer == decoded:
            break
        decoded = newer
    if any(pattern.search(decoded) for pattern in _MAY_TOKEN_PATTERNS):
        raise ContractError("path refers to sealed May 2026")
    resolved = path.resolve(strict=False)
    resolved_text = unicodedata.normalize("NFKC", str(resolved))
    if any(
        pattern.search(resolved_text)
        for pattern in _MAY_TOKEN_PATTERNS
    ):
        raise ContractError("resolved path refers to sealed May 2026")
    if allowed_root is not None:
        root = allowed_root.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ContractError("path escapes its configured root") from exc
    return resolved
