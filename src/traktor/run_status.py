"""Record the outcome of the most recent sync run.

The health endpoint answers ``GET /status`` from this record (issue #13): it used
to return the fixed pair ``idle``/``null``, so a monitor could not tell a healthy
run from one that failed every day. The record lives in the sync progress file,
beside the resume bookkeeping that file already holds; it is merged into the
existing JSON rather than written over it, so neither half loses the other.

A record is written at the start of every run and again when it ends, so a
failure that stops the run early (an exception, or the ``sys.exit()`` calls on a
missing credential) is recorded as failed instead of leaving the endpoint
looking like a run still in progress.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .log import logger

RUN_KEY = "run"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_UNKNOWN = "unknown"
# An error string is a status line, not a traceback: keep it short enough to read
# in a one-line monitor and free of the detail that belongs in the log.
MAX_ERROR_CHARS = 200


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> Dict[str, Any]:
    """Read the progress file, returning an empty mapping when it is unusable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: Dict[str, Any]) -> None:
    """Write the progress file atomically so a reader never sees half a record."""
    tmp_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".sync_progress.", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_name, path)
        tmp_name = None
    except (PermissionError, FileNotFoundError, OSError) as e:
        # Recording the outcome is bookkeeping: never fail a sync over it.
        logger.warning(f"Could not record sync run status: {e}")
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError as e:
                # A leftover temp file is harmless, but leaving no trace is how a
                # failure hides: say what it was and where.
                logger.debug(f"Could not remove temporary status file {tmp_name}: {e}")


def _previous_run(path: Path) -> Dict[str, Any]:
    """Return the run record already on disk, or an empty mapping."""
    run = _read(path).get(RUN_KEY)
    return run if isinstance(run, dict) else {}


def record_run_started(path: Path) -> None:
    """Record that a run has started, keeping the previous success timestamp.

    Args:
        path: Sync progress file to record in
    """
    data = _read(path)
    data[RUN_KEY] = {
        "status": RUN_STATUS_RUNNING,
        "started_at": _now(),
        "finished_at": None,
        "last_success_at": _previous_run(path).get("last_success_at"),
        "error": None,
    }
    _write(path, data)


def record_run_succeeded(path: Path) -> None:
    """Record a successful run and advance the last-success timestamp.

    Args:
        path: Sync progress file to record in
    """
    data = _read(path)
    finished_at = _now()
    data[RUN_KEY] = {
        **(_previous_run(path)),
        "status": RUN_STATUS_SUCCESS,
        "finished_at": finished_at,
        "last_success_at": finished_at,
        "error": None,
    }
    _write(path, data)


def record_run_failed(path: Path, error: str) -> None:
    """Record a failed run, leaving the last success timestamp where it was.

    Args:
        path: Sync progress file to record in
        error: Short reason shown by the status endpoint
    """
    data = _read(path)
    data[RUN_KEY] = {
        **(_previous_run(path)),
        "status": RUN_STATUS_FAILED,
        "finished_at": _now(),
        "error": error[:MAX_ERROR_CHARS],
    }
    _write(path, data)


def read_run_status(path: Path) -> Dict[str, Any]:
    """Return the last recorded run, or an empty mapping when none exists.

    Args:
        path: Sync progress file to read

    Returns:
        Record with status/started_at/finished_at/last_success_at/error keys
    """
    return _previous_run(path)
