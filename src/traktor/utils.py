"""Shared utility functions for traktor."""

import importlib.util
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .log import logger

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


# Control characters a terminal acts on: carriage return, escape, backspace and
# the C1 range. Only tab and newline are left alone, because a report
# legitimately carries them.
TERMINAL_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def sanitize_terminal_text(value: Any) -> str:
    """Render remote text so a terminal can only display it, never obey it.

    A release body, a URL or a Trakt list name is written by somebody else. An
    escape byte in it can move the cursor, clear the screen or set the
    clipboard, and a carriage return can overwrite the line the user is reading,
    so every control character is turned into a visible ``\\x1b``-style escape
    (issue #43).

    Args:
        value: Text to render; ``None`` becomes the empty string

    Returns:
        The text with every control character escaped
    """
    text = "" if value is None else str(value)
    return TERMINAL_CONTROL_CHARS.sub(lambda match: f"\\x{ord(match.group()):02x}", text)


def write_bytes_atomic(path, payload: bytes, mode: Optional[int] = None) -> bool:
    """Replace a file's content without ever exposing a partial file (issue #23).

    The bytes go to a temporary file in the target's own directory, are flushed
    to disk, and are then renamed over the target. A crash, a full disk or a
    concurrent reader therefore sees either the old file or the new one: opening
    the destination for writing used to empty it first, so a failed dump left
    the state file, the cache, the config or the ``.env`` token store truncated.

    Args:
        path: Destination path; its directory is created when missing
        payload: Bytes to write
        mode: Optional permission bits for the new file (the temporary file is
            created 0600, which is kept when ``mode`` is None)

    Returns:
        True when the file was replaced, False when the write failed
    """
    target = Path(path)
    tmp_name: Optional[str] = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, target)
        tmp_name = None
        return True
    except OSError as e:
        logger.error(f"Failed to write {target}: {e}")
        return False
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError as e:
                # Leaving no trace is how a failure hides: say where it was.
                logger.debug(f"Could not remove temporary file {tmp_name}: {e}")


def write_text_atomic(path, text: str, encoding: str = "utf-8", mode: Optional[int] = None) -> bool:
    """Write text to a file through :func:`write_bytes_atomic`.

    Args:
        path: Destination path
        text: Text to write
        encoding: Encoding used to turn the text into bytes
        mode: Optional permission bits for the new file

    Returns:
        True when the file was replaced, False when the write failed
    """
    return write_bytes_atomic(path, text.encode(encoding), mode=mode)


def write_json_atomic(
    path, data: Any, indent: Optional[int] = 2, mode: Optional[int] = None
) -> bool:
    """Serialize ``data`` as JSON and write it through :func:`write_text_atomic`.

    Args:
        path: Destination path
        data: JSON-serializable value
        indent: Indentation passed to ``json.dumps``
        mode: Optional permission bits for the new file

    Returns:
        True when the file was replaced, False when the write failed
    """
    return write_text_atomic(path, json.dumps(data, indent=indent), mode=mode)


def restrict_to_owner(path) -> bool:
    """Set a file's permissions to owner read/write only (0600).

    Files that can hold a credential - the config file and the .env token
    store - must not be readable by other accounts on the machine (issue
    #12). Windows and some network file systems ignore the mode, so the
    result is verified rather than assumed.

    Args:
        path: File whose permissions should be restricted

    Returns:
        True when the file ends up mode 0600, False when the file system
        refused the change
    """
    try:
        os.chmod(path, 0o600)
        return stat.S_IMODE(os.stat(path).st_mode) == 0o600
    except OSError:
        return False


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


def format_trakt_timestamp(value) -> Optional[str]:
    """Render a timestamp in the ISO-8601 UTC form the Trakt API expects.

    Trakt accepts a per-item ``watched_at`` when history is written. Without
    one it stamps the play with the moment of the request, which turns a sync
    into fabricated watch dates (issue #37).

    Args:
        value: Timestamp as datetime, ISO string, or epoch int/float

    Returns:
        ``YYYY-MM-DDTHH:MM:SS.000Z`` in UTC, or None when unparseable
    """
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.000Z")
