"""Shared utility functions for traktor."""

import importlib.util
from datetime import datetime, timezone
from typing import Optional

# zoneinfo is available in Python 3.9+; find_spec guard provides a fallback
# without try/except import redefinition issues flagged by mypy.
ZoneInfo = __import__("zoneinfo").ZoneInfo if importlib.util.find_spec("zoneinfo") else None


def normalize_tmdb_id(tmdb_id):
    """Normalize TMDb ID to string or None.

    TMDb IDs must be stored as strings for consistent lookup across the codebase.
    This function handles conversion from various input types, treating falsy
    values (0, empty string) as None.

    Args:
        tmdb_id: TMDb ID as string, int, or None

    Returns:
        String representation of TMDb ID, or None if input is None/falsy
    """
    if tmdb_id is None:
        return None
    return str(tmdb_id) if tmdb_id else None


def _get_zoneinfo(tz_name: str):
    """Look up a named timezone via zoneinfo, returning None if unavailable."""
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return None


def parse_timestamp(value, tz_name: Optional[str] = None) -> Optional[datetime]:
    """Parse a timestamp into a timezone-aware UTC datetime.

    This is the canonical timestamp parser for the codebase, used by the
    cache manager, watch sync engine, and conflict resolver.

    Semantics:
    - ``datetime``: aware datetimes are converted to UTC; naive datetimes are
      interpreted as system local time (matching plexapi's
      ``datetime.fromtimestamp()`` behavior), unless ``tz_name`` is given.
    - ``int``/``float``: treated as a Unix epoch timestamp (UTC).
    - ``str``: parsed as ISO-8601 (``Z`` suffix handled); a numeric string is
      treated as a Unix epoch timestamp. Naive ISO strings are interpreted as
      system local time, unless ``tz_name`` is given.
    - ``tz_name``: when provided, naive datetimes are interpreted in that named
      timezone (e.g. ``"America/New_York"``) instead of system local time.
      Falls back to system local time if the timezone is unavailable.
    - Any other input, or an unparseable value, returns ``None``.

    Args:
        value: Unix timestamp (int/float), ISO string, or datetime
        tz_name: Optional timezone name for interpreting naive datetimes

    Returns:
        Aware UTC datetime or None if parsing fails
    """
    if value is None:
        return None

    try:
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value.astimezone(timezone.utc)
            tz = _get_zoneinfo(tz_name) if tz_name else None
            if tz is not None:
                return value.replace(tzinfo=tz).astimezone(timezone.utc)
            # Naive datetime: plexapi returns naive local datetimes, so
            # astimezone() on a naive datetime interprets it as local time.
            return value.astimezone(timezone.utc)

        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)

        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                # Try parsing as a Unix timestamp string
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc)
            tz = _get_zoneinfo(tz_name) if tz_name else None
            if tz is not None:
                return dt.replace(tzinfo=tz).astimezone(timezone.utc)
            return dt.astimezone(timezone.utc)

        return None
    except (ValueError, TypeError, OverflowError):
        return None
