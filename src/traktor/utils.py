"""Shared utility functions for traktor."""

import gzip
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from .log import logger

# zoneinfo is available in Python 3.9+; find_spec guard provides a fallback
# without try/except import redefinition issues flagged by mypy.
ZoneInfo = __import__("zoneinfo").ZoneInfo if importlib.util.find_spec("zoneinfo") else None

# fcntl is Unix-only; the same find_spec guard keeps this module importable on
# Windows, where file_lock() degrades to no locking.
fcntl = __import__("fcntl") if importlib.util.find_spec("fcntl") else None

KB_PER_MB = 1024
BYTES_PER_MB = 1024 * 1024
# How long a read-modify-write waits for another process before it gives up and
# writes anyway. Bookkeeping must never block a sync.
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.05


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


def read_json_object(path) -> Dict[str, Any]:
    """Read a JSON object from ``path``, returning an empty dict when unusable.

    A state file is written by one process while another may be reading it, so
    a missing, unreadable, malformed or non-object file is normal rather than
    exceptional: the caller merges its own keys into the result.

    Args:
        path: File to read

    Returns:
        The parsed object, or an empty dict
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


@contextmanager
def file_lock(path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Hold an exclusive advisory lock around a file's read-modify-write (issues #64, #65).

    The sync progress file and the watch-state file are each written by more
    than one process: a frequent cron run and a one-off run, or two runs the
    scheduler overlapped. Replacing the file atomically keeps a reader from
    seeing half a file, but it does not stop two writers from each saving a
    state that lacks the other's rows. Serializing the read, the merge and the
    write under one lock does.

    The lock is advisory (``flock``) and therefore honoured only by this
    program, which is the only writer of these files. A platform without
    ``fcntl``, or a lock that cannot be taken within ``timeout``, proceeds
    unlocked: bookkeeping must never break a sync.

    Args:
        path: File whose read-modify-write is being serialized; the lock file
            is ``<path>.lock`` beside it
        timeout: Seconds to wait for the lock before proceeding without it
    """
    if fcntl is None:
        yield
        return

    lock_path = Path(f"{path}.lock")
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+", encoding="utf-8")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    logger.warning(
                        f"Could not lock {lock_path} within {timeout:.0f}s; "
                        "merging without the lock"
                    )
                    break
                time.sleep(LOCK_POLL_SECONDS)
    except OSError as e:
        logger.warning(f"Could not lock {lock_path}: {e}")
        if handle is not None:
            handle.close()
            handle = None

    try:
        yield
    finally:
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError as e:
                logger.debug(f"Could not release {lock_path}: {e}")
            handle.close()


def rusage_maxrss_to_mb(maxrss: float) -> float:
    """Convert ``resource.getrusage().ru_maxrss`` to megabytes (issue #63).

    ``ru_maxrss`` is in kilobytes on Linux and in *bytes* on macOS, so a single
    divisor reports a 300 MB process as 307200 MB on a Mac - and the memory
    warning then fires on every run, hiding a real problem. The resource
    manager and the performance monitor both read this figure, so they share
    this helper instead of keeping a copy of the platform split each.

    Args:
        maxrss: ``ru_maxrss`` value from ``getrusage``

    Returns:
        Resident set size in MB, rounded to two decimals
    """
    divisor = BYTES_PER_MB if sys.platform == "darwin" else KB_PER_MB
    return round(float(maxrss) / divisor, 2)


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


# A gzip stream can expand a small file past what the host has (issue #81), so
# every reader here states how much decompressed data it will accept before it
# parses or copies anything. The ceiling is generous on purpose: the library
# cache of a large server is tens of megabytes, and a backup carries that cache.
MAX_DECOMPRESSED_BYTES = 256 * BYTES_PER_MB
# A small, highly repetitive file is normal, so an expansion ratio is only a
# bomb signal once the payload is already large.
RATIO_GUARDED_MIN_BYTES = 32 * BYTES_PER_MB
MAX_DECOMPRESSION_RATIO = 200
DECOMPRESS_CHUNK_BYTES = 1024 * 1024


class DecompressionLimitError(ValueError):
    """A gzip payload expands past the configured limit (issue #81)."""


def _refuse_oversized_decompression(
    compressed_bytes: int, decompressed_bytes: int, source: Any, max_bytes: int
) -> None:
    """Raise when a decompressed payload is past the size or ratio limit."""
    if decompressed_bytes > max_bytes:
        raise DecompressionLimitError(
            f"{source} expands past {max_bytes} bytes "
            f"({decompressed_bytes} from {compressed_bytes} compressed)"
        )
    if compressed_bytes > 0 and decompressed_bytes >= RATIO_GUARDED_MIN_BYTES:
        ratio = decompressed_bytes / compressed_bytes
        if ratio > MAX_DECOMPRESSION_RATIO:
            raise DecompressionLimitError(
                f"{source} expands {ratio:.0f}x ({decompressed_bytes} bytes from "
                f"{compressed_bytes} compressed), past the {MAX_DECOMPRESSION_RATIO}x limit"
            )


def iter_gzip_bounded(path, max_bytes: Optional[int] = None) -> Iterator[bytes]:
    """Yield a gzip file's contents in chunks, refusing an oversized payload.

    Args:
        path: Path to the gzip file
        max_bytes: Decompressed size ceiling for this read; defaults to
            :data:`MAX_DECOMPRESSED_BYTES`

    Yields:
        Decompressed chunks

    Raises:
        DecompressionLimitError: The payload is past the size or ratio limit
    """
    if max_bytes is None:
        max_bytes = MAX_DECOMPRESSED_BYTES
    compressed_bytes = Path(path).stat().st_size
    total = 0
    with gzip.open(path, "rb") as stream:
        while True:
            chunk = stream.read(DECOMPRESS_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            _refuse_oversized_decompression(compressed_bytes, total, path, max_bytes)
            yield chunk


def read_gzip_bytes(path, max_bytes: Optional[int] = None) -> bytes:
    """Read a whole gzip file, bounded by :data:`MAX_DECOMPRESSED_BYTES`."""
    return b"".join(iter_gzip_bounded(path, max_bytes))


def read_gzip_text(path, max_bytes: Optional[int] = None, encoding: str = "utf-8") -> str:
    """Read a whole gzip file as text, bounded by :data:`MAX_DECOMPRESSED_BYTES`."""
    return read_gzip_bytes(path, max_bytes).decode(encoding)
