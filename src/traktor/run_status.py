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

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .log import logger
from .utils import file_lock, read_json_object, write_json_atomic

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
    return read_json_object(path)


def _merge_run(path: Path, update: Dict[str, Any]) -> None:
    """Merge ``update`` into the run record, keeping the keys beside it.

    The read, the merge and the write happen under the same lock the resume
    bookkeeping takes, so neither half of the file deletes the other when two
    processes write at once (issues #13, #64). A key the update omits - such as
    ``last_success_at`` when a run fails - keeps its previous value.

    Args:
        path: Sync progress file to update
        update: Run-record keys to set
    """
    with file_lock(path):
        data = _read(path)
        previous = data.get(RUN_KEY)
        previous = previous if isinstance(previous, dict) else {}
        data[RUN_KEY] = {**previous, **update}
        if not write_json_atomic(path, data, indent=None, mode=0o600):
            # Recording the outcome is bookkeeping: never fail a sync over it.
            logger.warning(f"Could not record sync run status in {path}")


def _previous_run(path: Path) -> Dict[str, Any]:
    """Return the run record already on disk, or an empty mapping."""
    run = _read(path).get(RUN_KEY)
    return run if isinstance(run, dict) else {}


def record_run_started(path: Path) -> None:
    """Record that a run has started, keeping the previous success timestamp.

    Args:
        path: Sync progress file to record in
    """
    _merge_run(
        path,
        {
            "status": RUN_STATUS_RUNNING,
            "started_at": _now(),
            "finished_at": None,
            "error": None,
        },
    )


def record_run_succeeded(path: Path) -> None:
    """Record a successful run and advance the last-success timestamp.

    Args:
        path: Sync progress file to record in
    """
    finished_at = _now()
    _merge_run(
        path,
        {
            "status": RUN_STATUS_SUCCESS,
            "finished_at": finished_at,
            "last_success_at": finished_at,
            "error": None,
        },
    )


def record_run_failed(path: Path, error: str) -> None:
    """Record a failed run, leaving the last success timestamp where it was.

    Args:
        path: Sync progress file to record in
        error: Short reason shown by the status endpoint
    """
    _merge_run(
        path,
        {
            "status": RUN_STATUS_FAILED,
            "finished_at": _now(),
            "error": error[:MAX_ERROR_CHARS],
        },
    )


def read_run_status(path: Path) -> Dict[str, Any]:
    """Return the last recorded run, or an empty mapping when none exists.

    Args:
        path: Sync progress file to read

    Returns:
        Record with status/started_at/finished_at/last_success_at/error keys
    """
    return _previous_run(path)
