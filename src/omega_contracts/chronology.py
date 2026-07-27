"""Single chronology authority for every Omega scaffold eligibility decision."""

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
_CANONICAL_UTC_Z = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_MAY_TOKEN_PATTERNS = (
    re.compile(r"(?<!\d)2026(?:05|[-_/.\\\s]+0?5)(?!\d{3})", re.IGNORECASE),
    re.compile(
        r"(?<!\d)0?5[-_/.\\\s]+(?:0?[1-9]|[12]\d|3[01])[-_/.\\\s]+2026(?!\d)",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![a-z])may(?:[-_/.\\\s]+)2026(?!\d)", re.IGNORECASE),
    re.compile(r"(?<!\d)2026(?:[-_/.\\\s]+)may(?![a-z])", re.IGNORECASE),
    re.compile(
        r"(?<![a-z])may[-_/.\\\s]+(?:0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?[-_,/.\\\s]+2026(?!\d)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?[-_/.\\\s]+may[-_,/.\\\s]+2026(?!\d)",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![a-z])may[-_/.\\\s]*2026(?!\d)", re.IGNORECASE),
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
        raise ContractError(
            f"{label} must be an aware datetime or canonical RFC 3339 text"
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{label} must be timezone-aware")
    normalized = parsed.astimezone(timezone.utc)
    assert_not_may_2026(normalized.date(), label=f"{label} date")
    return normalized


def parse_canonical_utc(value: datetime | str, *, label: str = "timestamp") -> datetime:
    """Require an aware UTC datetime or canonical RFC 3339 text ending in ``Z``.

    External scaffold contracts use this narrower function.  Offset-bearing
    timestamps remain supported only for already-typed internal chronology
    inputs through :func:`parse_aware_utc`.
    """

    if isinstance(value, str):
        if _CANONICAL_UTC_Z.fullmatch(value) is None:
            raise ContractError(f"{label} must be canonical UTC text ending in Z")
        parsed = parse_aware_utc(value, label=label)
    elif isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ContractError(f"{label} must be timezone-aware UTC")
        parsed = parse_aware_utc(value, label=label)
    else:
        raise ContractError(
            f"{label} must be an aware UTC datetime or canonical UTC text"
        )
    return parsed


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


def require_event_eligibility(
    *,
    observed_at: datetime | str,
    decision_horizon: datetime | str,
    captured_at: datetime | str,
    event_start: datetime | str,
) -> tuple[datetime, datetime, datetime, datetime]:
    """Enforce ``observed < horizon <= capture < event`` in canonical UTC."""

    observed = parse_canonical_utc(observed_at, label="source observed_at")
    horizon = parse_canonical_utc(decision_horizon, label="decision horizon")
    captured = parse_canonical_utc(captured_at, label="snapshot captured_at")
    event = parse_canonical_utc(event_start, label="event start")
    if observed >= horizon:
        raise ContractError(
            "source observation must be strictly before decision horizon"
        )
    if horizon > captured:
        raise ContractError("decision horizon must not be after snapshot capture")
    if captured >= event:
        raise ContractError("snapshot capture must be strictly before event start")
    return observed, horizon, captured, event


def require_load_freshness(
    *,
    captured_at: datetime | str,
    now_utc: datetime | str,
    maximum_age_seconds: int,
) -> int:
    """Return whole elapsed seconds after enforcing a required operational age bound.

    Equality at the maximum age is accepted.  A future capture is rejected.
    ``bool`` is rejected even though it is an ``int`` subclass.
    """

    if (
        isinstance(maximum_age_seconds, bool)
        or not isinstance(maximum_age_seconds, int)
        or maximum_age_seconds < 0
    ):
        raise ContractError(
            "maximum snapshot age must be an explicit non-negative integer"
        )
    captured = parse_canonical_utc(captured_at, label="snapshot captured_at")
    now = parse_canonical_utc(now_utc, label="load clock")
    delta = now - captured
    if delta.total_seconds() < 0:
        raise ContractError("snapshot capture time is in the future")
    if delta.total_seconds() > maximum_age_seconds:
        raise ContractError("snapshot is stale at load time")
    return int(delta.total_seconds())


def assert_may_safe_path(path: Path, *, allowed_root: Path | None = None) -> Path:
    """Reject lexical and resolved May tokens before callers open a path."""
    raw = unicodedata.normalize("NFKC", str(path))
    decoded = raw
    for _ in range(5):
        newer = unquote(decoded)
        if newer == decoded:
            break
        decoded = newer
    if any(pattern.search(decoded) for pattern in _MAY_TOKEN_PATTERNS):
        raise ContractError("path refers to sealed May 2026")
    resolved = path.resolve(strict=False)
    resolved_text = unicodedata.normalize("NFKC", str(resolved))
    if any(pattern.search(resolved_text) for pattern in _MAY_TOKEN_PATTERNS):
        raise ContractError("resolved path refers to sealed May 2026")
    if allowed_root is not None:
        root = allowed_root.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ContractError("path escapes its configured root") from exc
    return resolved
