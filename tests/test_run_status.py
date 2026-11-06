"""Tests for the recorded sync run outcome served by GET /status (issue #13)."""

import json
from unittest.mock import MagicMock

import pytest

from traktor import sync
from traktor.run_status import (
    MAX_ERROR_CHARS,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCESS,
    read_run_status,
    record_run_failed,
    record_run_started,
    record_run_succeeded,
)


@pytest.fixture
def status_file(tmp_path):
    """A fresh sync progress file."""
    return tmp_path / "sync_progress.json"


class TestRecordRunOutcome:
    """The recorded outcome survives a round trip through the file."""

    def test_no_record_reads_empty(self, status_file):
        assert read_run_status(status_file) == {}

    def test_started_run_is_reported_as_running(self, status_file):
        record_run_started(status_file)

        run = read_run_status(status_file)
        assert run["status"] == RUN_STATUS_RUNNING
        assert run["started_at"] is not None
        assert run["finished_at"] is None
        assert run["error"] is None

    def test_success_sets_the_last_success_timestamp(self, status_file):
        record_run_started(status_file)
        record_run_succeeded(status_file)

        run = read_run_status(status_file)
        assert run["status"] == RUN_STATUS_SUCCESS
        assert run["last_success_at"] == run["finished_at"]
        assert run["last_success_at"] is not None

    def test_failure_keeps_the_last_success_timestamp(self, status_file):
        record_run_started(status_file)
        record_run_succeeded(status_file)
        first_success = read_run_status(status_file)["last_success_at"]

        record_run_started(status_file)
        record_run_failed(status_file, "run exited with code 1")

        run = read_run_status(status_file)
        assert run["status"] == RUN_STATUS_FAILED
        assert run["error"] == "run exited with code 1"
        assert run["last_success_at"] == first_success

    def test_error_is_trimmed_to_a_status_line(self, status_file):
        record_run_failed(status_file, "x" * (MAX_ERROR_CHARS + 50))

        assert len(read_run_status(status_file)["error"]) == MAX_ERROR_CHARS

    def test_record_merges_with_resume_bookkeeping(self, status_file):
        """Recording an outcome must not wipe the completed-list progress."""
        status_file.write_text(json.dumps({"completed": ["Liked movies"], "timestamp": "t"}))

        record_run_started(status_file)
        record_run_succeeded(status_file)

        data = json.loads(status_file.read_text())
        assert data["completed"] == ["Liked movies"]
        assert data["run"]["status"] == RUN_STATUS_SUCCESS

    def test_unreadable_file_is_not_an_error(self, status_file):
        status_file.write_text("{not json")

        assert read_run_status(status_file) == {}
        record_run_failed(status_file, "boom")
        assert read_run_status(status_file)["status"] == RUN_STATUS_FAILED


class TestSyncListsRecordsOutcome:
    """sync_lists leaves a record on every exit path."""

    @pytest.fixture(autouse=True)
    def use_temp_status_file(self, status_file, monkeypatch):
        monkeypatch.setattr(sync, "SYNC_PROGRESS_FILE", status_file)
        self.status_file = status_file

    def test_successful_run_is_recorded(self, monkeypatch):
        monkeypatch.setattr(sync, "_sync_lists", MagicMock(return_value=0))

        assert sync.sync_lists(None) == 0
        assert read_run_status(self.status_file)["status"] == RUN_STATUS_SUCCESS

    def test_failing_run_is_recorded_with_its_exit_code(self, monkeypatch):
        monkeypatch.setattr(sync, "_sync_lists", MagicMock(return_value=1))

        assert sync.sync_lists(None) == 1
        run = read_run_status(self.status_file)
        assert run["status"] == RUN_STATUS_FAILED
        assert "exit code 1" in run["error"]

    def test_early_sys_exit_is_recorded_as_a_failure(self, monkeypatch):
        """The auth and credential failures call sys.exit(), not return 1."""

        def die(*args, **kwargs):
            raise SystemExit(1)

        monkeypatch.setattr(sync, "_sync_lists", die)

        with pytest.raises(SystemExit):
            sync.sync_lists(None)

        run = read_run_status(self.status_file)
        assert run["status"] == RUN_STATUS_FAILED
        assert run["error"] == "run exited with code 1"

    def test_exception_is_recorded_as_a_failure(self, monkeypatch):
        def explode(*args, **kwargs):
            raise ConnectionError("Trakt is unreachable")

        monkeypatch.setattr(sync, "_sync_lists", explode)

        with pytest.raises(ConnectionError):
            sync.sync_lists(None)

        run = read_run_status(self.status_file)
        assert run["status"] == RUN_STATUS_FAILED
        assert run["error"] == "run stopped with ConnectionError"
        # The exception text stays in the log, not on a network-reachable endpoint.
        assert "unreachable" not in run["error"]

    def test_interrupted_run_is_recorded(self, monkeypatch):
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(sync, "_sync_lists", interrupt)

        with pytest.raises(KeyboardInterrupt):
            sync.sync_lists(None)

        assert read_run_status(self.status_file)["status"] == RUN_STATUS_FAILED
