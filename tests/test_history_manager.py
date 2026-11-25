"""Tests for history_manager module."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from traktor import history_manager


@pytest.fixture
def temp_state_file(tmp_path, monkeypatch):
    """Create a temporary state file location."""
    state_file = tmp_path / ".traktor_watch_sync.json"
    monkeypatch.setattr(history_manager, "WATCH_SYNC_FILE", state_file)
    return state_file


@pytest.fixture
def mock_history_manager(temp_state_file):
    """Create a WatchHistoryManager with temp state file."""
    return history_manager.WatchHistoryManager(plex_server_id="test-server-123")


class TestWatchHistoryManager:
    """Tests for WatchHistoryManager class."""

    def test_init_creates_empty_state(self, temp_state_file):
        """Test that init creates empty state when no file exists."""
        manager = history_manager.WatchHistoryManager(plex_server_id="test-server")

        assert manager.state["version"] == 1
        assert manager.state["plex_server_id"] == "test-server"
        assert manager.state["synced_items"] == []
        assert manager.state["last_sync_timestamp"] is None

    def test_load_existing_state(self, temp_state_file):
        """Test loading existing state from file."""
        existing_state = {
            "version": 1,
            "plex_server_id": "test-server",
            "last_sync_timestamp": "2026-04-10T12:00:00+00:00",
            "synced_items": [
                {
                    "imdb_id": "tt1234567",
                    "media_type": "movie",
                    "watched_plex": True,
                    "watched_trakt": True,
                }
            ],
        }

        temp_state_file.write_text(json.dumps(existing_state))

        manager = history_manager.WatchHistoryManager(plex_server_id="test-server")

        assert len(manager.state["synced_items"]) == 1
        assert manager.state["synced_items"][0]["imdb_id"] == "tt1234567"

    def test_server_id_mismatch_creates_new_state(self, temp_state_file):
        """Test that mismatched server ID creates new state."""
        existing_state = {
            "version": 1,
            "plex_server_id": "different-server",
            "synced_items": [{"imdb_id": "tt1234567", "media_type": "movie"}],
        }

        temp_state_file.write_text(json.dumps(existing_state))

        manager = history_manager.WatchHistoryManager(plex_server_id="new-server")

        assert manager.state["synced_items"] == []
        assert manager.state["plex_server_id"] == "new-server"

    def test_add_new_synced_item(self, mock_history_manager):
        """Test adding a new synced item."""
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt1234567",
            tmdb_id=987654,
            plex_rating_key="12345",
            trakt_id="trakt-123",
            watched_plex=True,
            watched_trakt=False,
            last_watched_at_plex="2026-04-10T12:00:00Z",
        )

        assert len(mock_history_manager.state["synced_items"]) == 1
        item = mock_history_manager.state["synced_items"][0]
        assert item["imdb_id"] == "tt1234567"
        assert item["tmdb_id"] == "987654"
        assert item["watched_plex"] is True
        assert item["watched_trakt"] is False

    def test_update_existing_synced_item(self, mock_history_manager):
        """Test updating an existing synced item."""
        # Add initial item
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt1234567",
            watched_plex=False,
            watched_trakt=False,
        )

        # Update it
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt1234567",
            watched_plex=True,
            watched_trakt=True,
        )

        assert len(mock_history_manager.state["synced_items"]) == 1
        item = mock_history_manager.state["synced_items"][0]
        assert item["watched_plex"] is True
        assert item["watched_trakt"] is True

    def test_get_synced_item_by_imdb(self, mock_history_manager):
        """Test getting synced item by IMDb ID."""
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt1234567",
            tmdb_id=987654,
        )

        item = mock_history_manager.get_synced_item(imdb_id="tt1234567")

        assert item is not None
        assert item["imdb_id"] == "tt1234567"

    def test_get_synced_item_by_tmdb(self, mock_history_manager):
        """Test getting synced item by TMDb ID."""
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt1234567",
            tmdb_id=987654,
        )

        item = mock_history_manager.get_synced_item(tmdb_id=987654)

        assert item is not None
        assert item["tmdb_id"] == "987654"

    def test_get_synced_item_not_found(self, mock_history_manager):
        """Test getting non-existent synced item."""
        item = mock_history_manager.get_synced_item(imdb_id="tt9999999")
        assert item is None

    def test_remove_synced_item(self, mock_history_manager):
        """Test removing a synced item."""
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt1234567",
        )

        result = mock_history_manager.remove_synced_item(imdb_id="tt1234567")

        assert result is True
        assert len(mock_history_manager.state["synced_items"]) == 0

    def test_remove_nonexistent_item(self, mock_history_manager):
        """Test removing item that doesn't exist."""
        result = mock_history_manager.remove_synced_item(imdb_id="tt9999999")
        assert result is False

    def test_update_last_sync_timestamp(self, mock_history_manager):
        """Test updating last sync timestamp."""
        mock_history_manager.update_last_sync_timestamp()

        timestamp = mock_history_manager.get_last_sync_timestamp()
        assert timestamp is not None
        assert isinstance(timestamp, datetime)

    def test_get_stats(self, mock_history_manager):
        """Test getting sync statistics."""
        # Add various items
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt1111111",
            watched_plex=True,
            watched_trakt=True,
        )
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt2222222",
            watched_plex=True,
            watched_trakt=False,
        )
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt3333333",
            watched_plex=False,
            watched_trakt=True,
        )
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt4444444",
            watched_plex=False,
            watched_trakt=False,
        )

        stats = mock_history_manager.get_stats()

        assert stats["total_items"] == 4
        assert stats["watched_both"] == 1
        assert stats["watched_plex_only"] == 1
        assert stats["watched_trakt_only"] == 1
        assert stats["unwatched_both"] == 1

    def test_clear_all(self, mock_history_manager):
        """Test clearing all synced items."""
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt1234567",
        )

        mock_history_manager.clear_all()

        assert len(mock_history_manager.state["synced_items"]) == 0
        assert mock_history_manager.state["last_sync_timestamp"] is None

    def test_backup_and_restore(self, mock_history_manager, tmp_path):
        """Test backup and restore functionality."""
        # Add item
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt1234567",
        )

        # Create backup
        backup_path = tmp_path / "backup.json"
        result = mock_history_manager.backup_state(backup_path)

        assert result == backup_path
        assert backup_path.exists()

        # Clear and restore
        mock_history_manager.clear_all()
        assert len(mock_history_manager.state["synced_items"]) == 0

        restore_result = mock_history_manager.restore_from_backup(backup_path)
        assert restore_result is True
        assert len(mock_history_manager.state["synced_items"]) == 1

    def test_restore_from_nonexistent_backup(self, mock_history_manager):
        """Test restoring from backup that doesn't exist."""
        result = mock_history_manager.restore_from_backup(Path("/nonexistent/backup.json"))
        assert result is False

    def test_tmdb_id_string_conversion(self, mock_history_manager):
        """Test that TMDb IDs are converted to strings."""
        mock_history_manager.add_or_update_synced_item(
            media_type="movie",
            imdb_id="tt1234567",
            tmdb_id=987654,  # Integer
        )

        # Should be able to find with string
        item = mock_history_manager.get_synced_item(tmdb_id="987654")
        assert item is not None

        # And with integer
        item = mock_history_manager.get_synced_item(tmdb_id=987654)
        assert item is not None


class TestWatchHistoryManagerEdgeCases:
    """Edge-case coverage for state corruption and error paths (TODO audit D7)."""

    def test_load_state_corrupt_json(self, temp_state_file):
        """Corrupt JSON state file falls back to empty state."""
        temp_state_file.write_text("{not valid json")
        manager = history_manager.WatchHistoryManager(plex_server_id="test-server-123")

        assert manager.state["synced_items"] == []
        assert manager.state["plex_server_id"] == "test-server-123"

    def test_load_state_invalid_format(self, temp_state_file):
        """State file missing required keys falls back to empty state."""
        temp_state_file.write_text(json.dumps({"foo": "bar"}))
        manager = history_manager.WatchHistoryManager(plex_server_id="test-server-123")

        assert manager.state["synced_items"] == []

    def test_save_state_permission_error(self, mock_history_manager, monkeypatch):
        """Save failure is logged, not raised."""
        from unittest.mock import patch

        with patch("traktor.utils.os.replace", side_effect=PermissionError("denied")):
            # Should not raise
            mock_history_manager.save_state()


def test_get_last_sync_timestamp_invalid_format(mock_history_manager, caplog):
    """Invalid stored timestamp returns None with a warning."""
    mock_history_manager.state["last_sync_timestamp"] = "not-a-timestamp"
    assert mock_history_manager.get_last_sync_timestamp() is None


def test_get_synced_item_by_trakt_id(mock_history_manager):
    """Find synced item by Trakt ID."""
    mock_history_manager.add_or_update_synced_item(
        media_type="movie", imdb_id="tt123", trakt_id=500
    )
    item = mock_history_manager.get_synced_item(trakt_id=500)
    assert item is not None
    assert item["imdb_id"] == "tt123"


def test_get_synced_item_by_plex_rating_key(mock_history_manager):
    """Find synced item by Plex rating key."""
    mock_history_manager.add_or_update_synced_item(
        media_type="movie", imdb_id="tt123", plex_rating_key=999
    )
    item = mock_history_manager.get_synced_item(plex_rating_key=999)
    assert item is not None
    assert item["imdb_id"] == "tt123"


def test_remove_synced_item_by_trakt_id(mock_history_manager):
    """Remove synced item by Trakt ID."""
    mock_history_manager.add_or_update_synced_item(media_type="movie", imdb_id="tt123", trakt_id=42)
    assert mock_history_manager.remove_synced_item(trakt_id=42) is True
    assert len(mock_history_manager.state["synced_items"]) == 0


def test_remove_synced_item_by_plex_rating_key(mock_history_manager):
    """Remove synced item by Plex rating key."""
    mock_history_manager.add_or_update_synced_item(
        media_type="episode", imdb_id="tt456", plex_rating_key=77
    )
    assert mock_history_manager.remove_synced_item(plex_rating_key=77) is True
    assert len(mock_history_manager.state["synced_items"]) == 0


def test_backup_state_permission_error(mock_history_manager, tmp_path, monkeypatch):
    """Backup failure returns None without raising."""
    from unittest.mock import patch

    with patch("traktor.utils.os.replace", side_effect=PermissionError("denied")):
        result = mock_history_manager.backup_state(tmp_path / "backup.json")
    assert result is None


def test_restore_from_default_backup_path(mock_history_manager):
    """Restore with no explicit path uses the default backup file."""
    # No backup file exists yet -> False
    assert mock_history_manager.restore_from_backup() is False


def test_restore_invalid_state_format(mock_history_manager, tmp_path):
    """Restore rejects a backup file with invalid state format."""
    backup_path = tmp_path / "bad.json"
    backup_path.write_text(json.dumps({"wrong": "shape"}))
    assert mock_history_manager.restore_from_backup(backup_path) is False


def test_restore_corrupt_json(mock_history_manager, tmp_path):
    """Restore handles a corrupt backup file gracefully."""
    backup_path = tmp_path / "corrupt.json"
    backup_path.write_text("{not valid")
    assert mock_history_manager.restore_from_backup(backup_path) is False


class TestHistoryPruning:
    """Tests for synced-item retention (issue #7 follow-up)."""

    def _add_item(self, manager, rating_key, updated_at):
        """Add an entry, then backdate its updated_at stamp."""
        manager.add_or_update_synced_item(
            media_type="movie", imdb_id=f"tt{rating_key}", plex_rating_key=rating_key
        )
        manager.state["synced_items"][-1]["updated_at"] = updated_at

    def test_old_entries_pruned_recent_kept(self, mock_history_manager):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        self._add_item(mock_history_manager, 1, (now - timedelta(days=200)).isoformat())
        self._add_item(mock_history_manager, 2, (now - timedelta(days=10)).isoformat())

        removed = mock_history_manager.prune_synced_items(max_age_days=180)

        assert removed == 1
        remaining = [i["plex_rating_key"] for i in mock_history_manager.state["synced_items"]]
        assert remaining == [2]
        # Index stays consistent with the pruned list
        assert mock_history_manager._item_index.get(1) is None
        assert mock_history_manager._item_index.get(2) is not None

    def test_entries_without_any_timestamp_are_dropped(self, mock_history_manager):
        """A legacy entry with no date at all cannot stay for ever (issue #68).

        The pre-upgrade state file carries no updated_at, and treating that as
        "keep" meant the documented retention window never expired it.
        """
        mock_history_manager.add_or_update_synced_item(
            media_type="movie", imdb_id="tt1", plex_rating_key=1
        )
        del mock_history_manager.state["synced_items"][-1]["updated_at"]

        assert mock_history_manager.prune_synced_items(max_age_days=180) == 1
        assert mock_history_manager.state["synced_items"] == []

    def test_zero_disables_pruning(self, mock_history_manager):
        from datetime import datetime, timedelta, timezone

        old = (datetime.now(timezone.utc) - timedelta(days=999)).isoformat()
        self._add_item(mock_history_manager, 1, old)

        assert mock_history_manager.prune_synced_items(max_age_days=0) == 0
        assert len(mock_history_manager.state["synced_items"]) == 1

    def test_default_uses_settings_retention(self, mock_history_manager, monkeypatch):
        """None max_age_days reads HISTORY_RETENTION_DAYS from settings."""
        from datetime import datetime, timedelta, timezone

        import traktor.history_manager as hm_module

        now = datetime.now(timezone.utc)
        self._add_item(mock_history_manager, 1, (now - timedelta(days=30)).isoformat())
        monkeypatch.setattr(hm_module, "HISTORY_RETENTION_DAYS", 7)

        assert mock_history_manager.prune_synced_items() == 1

    def test_invalid_timestamp_falls_back_to_the_watch_date(self, mock_history_manager):
        """An unparseable updated_at still has the watch date to age it (#68)."""
        from datetime import datetime, timedelta, timezone

        recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        mock_history_manager.add_or_update_synced_item(
            media_type="movie", imdb_id="tt1", plex_rating_key=1, last_watched_at_plex=recent
        )
        mock_history_manager.state["synced_items"][-1]["updated_at"] = "garbage"

        assert mock_history_manager.prune_synced_items(max_age_days=180) == 0
        assert len(mock_history_manager.state["synced_items"]) == 1

    def test_invalid_timestamp_without_any_date_is_dropped(self, mock_history_manager):
        """Nothing can date the entry, so retention drops it (issue #68)."""
        mock_history_manager.add_or_update_synced_item(
            media_type="movie", imdb_id="tt1", plex_rating_key=1
        )
        mock_history_manager.state["synced_items"][-1]["updated_at"] = "garbage"

        assert mock_history_manager.prune_synced_items(max_age_days=1) == 1
        assert mock_history_manager.state["synced_items"] == []


# ---------------------------------------------------------------------------
# Issue #23: an interrupted state save keeps the previous file
# ---------------------------------------------------------------------------


def test_interrupted_state_save_keeps_the_previous_file(temp_state_file, monkeypatch):
    """A failed save must not empty the watch state (issue #23)."""
    from unittest.mock import patch

    manager = history_manager.WatchHistoryManager()
    manager.state["synced_items"].append(
        {
            "media_type": "movie",
            "imdb_id": "tt0113277",
            "plex_rating_key": 42,
            "trakt_id": 7,
            "updated_at": datetime.now().isoformat(),
        }
    )
    manager.save_state()
    before = Path(temp_state_file).read_text(encoding="utf-8")
    assert "tt0113277" in before

    manager.state["synced_items"].append(
        {"media_type": "movie", "imdb_id": "tt0000002", "plex_rating_key": 43}
    )
    with patch("traktor.utils.os.fsync", side_effect=OSError(28, "No space left on device")):
        manager.save_state()

    assert Path(temp_state_file).read_text(encoding="utf-8") == before
    reloaded = history_manager.WatchHistoryManager()
    assert [item["imdb_id"] for item in reloaded.state["synced_items"]] == ["tt0113277"]


# ---------------------------------------------------------------------------
# Issue #65: an overlapping run must not erase the other run's entries
# ---------------------------------------------------------------------------


class TestOverlappingRuns:
    """Two runs load the state before either saves, so the save must merge."""

    def _manager(self):
        return history_manager.WatchHistoryManager(plex_server_id="shared-server")

    def test_a_second_run_keeps_the_first_runs_entries(self, temp_state_file):
        first = self._manager()
        second = self._manager()

        first.add_or_update_synced_item(
            media_type="movie", imdb_id="tt0000001", plex_rating_key=1, watched_plex=True
        )
        first.save_state()
        second.add_or_update_synced_item(
            media_type="movie", imdb_id="tt0000002", plex_rating_key=2, watched_plex=True
        )
        second.save_state()

        reloaded = self._manager()
        assert sorted(item["imdb_id"] for item in reloaded.state["synced_items"]) == [
            "tt0000001",
            "tt0000002",
        ]

    def test_the_newer_entry_wins_a_conflict(self, temp_state_file):
        first = self._manager()
        first.add_or_update_synced_item(
            media_type="movie", imdb_id="tt0000001", plex_rating_key=1, watched_plex=False
        )
        first.save_state()

        second = self._manager()
        second.add_or_update_synced_item(
            media_type="movie", imdb_id="tt0000001", plex_rating_key=1, watched_plex=True
        )
        second.save_state()

        reloaded = self._manager()
        assert len(reloaded.state["synced_items"]) == 1
        assert reloaded.state["synced_items"][0]["watched_plex"] is True

    def test_a_pruned_entry_is_not_resurrected_by_the_next_save(self, temp_state_file):
        """A removal this run recorded survives its own later saves (issue #65)."""
        manager = self._manager()
        manager.add_or_update_synced_item(
            media_type="movie", imdb_id="tt0000009", plex_rating_key=9
        )
        manager.state["synced_items"][-1]["updated_at"] = "2000-01-01T00:00:00+00:00"
        manager.save_state()

        assert manager.prune_synced_items(max_age_days=180) == 1
        manager.add_or_update_synced_item(
            media_type="movie", imdb_id="tt0000010", plex_rating_key=10
        )
        manager.save_state()

        reloaded = self._manager()
        assert [item["imdb_id"] for item in reloaded.state["synced_items"]] == ["tt0000010"]

    def test_a_file_from_another_server_is_replaced_not_merged(self, temp_state_file):
        """Merging another server's rows would mix two libraries (issue #65)."""
        other = self._manager()
        other.add_or_update_synced_item(media_type="movie", imdb_id="tt0000003", plex_rating_key=3)
        other.save_state()

        manager = history_manager.WatchHistoryManager(plex_server_id="this-server")
        manager.add_or_update_synced_item(
            media_type="movie", imdb_id="tt0000004", plex_rating_key=4
        )
        manager.save_state()

        reloaded = history_manager.WatchHistoryManager(plex_server_id="this-server")
        assert [item["imdb_id"] for item in reloaded.state["synced_items"]] == ["tt0000004"]


# ---------------------------------------------------------------------------
# Issue #95: an overlapping run's removal must not beat a newer run's row
# ---------------------------------------------------------------------------


class TestOverlappingRemovals:
    """Removals are merged by version, not applied blindly."""

    def _manager(self):
        return history_manager.WatchHistoryManager(plex_server_id="shared-server")

    def test_a_stale_prune_keeps_a_row_a_newer_run_committed(self, temp_state_file):
        seed = self._manager()
        seed.add_or_update_synced_item(
            media_type="movie", imdb_id="tt0000001", plex_rating_key=1, watched_plex=False
        )
        seed.state["synced_items"][0]["updated_at"] = "2000-01-01T00:00:00+00:00"
        seed.save_state()

        older_run = self._manager()
        newer_run = self._manager()
        newer_run.add_or_update_synced_item(
            media_type="movie", imdb_id="tt0000001", plex_rating_key=1, watched_plex=True
        )
        newer_run.save_state()

        assert older_run.prune_synced_items(max_age_days=180) == 1
        older_run.save_state()

        after = self._manager().state["synced_items"]
        assert len(after) == 1
        assert after[0]["watched_plex"] is True

    def test_an_older_save_does_not_resurrect_a_removed_row(self, temp_state_file):
        seed = self._manager()
        seed.add_or_update_synced_item(
            media_type="movie", imdb_id="tt0000002", plex_rating_key=2, watched_plex=True
        )
        seed.save_state()

        older_run = self._manager()
        newer_run = self._manager()
        newer_run.remove_synced_item(plex_rating_key=2)
        older_run.save_state()

        assert self._manager().state["synced_items"] == []

    def test_a_tombstone_does_not_outlive_its_row_for_ever(self, temp_state_file):
        """Tombstones are dropped after twice the retention window."""
        manager = self._manager()
        manager.add_or_update_synced_item(
            media_type="movie", imdb_id="tt0000003", plex_rating_key=3
        )
        manager.remove_synced_item(plex_rating_key=3)

        saved = json.loads(temp_state_file.read_text())
        assert saved["removed_items"] == {}

        manager._removed_keys = {"plex_rating_key:3": "2000-01-01T00:00:00+00:00"}
        manager.save_state()

        assert json.loads(temp_state_file.read_text())["removed_items"] == {}


# ---------------------------------------------------------------------------
# Issue #96: a row with no identifier must not duplicate or resist retention
# ---------------------------------------------------------------------------


class TestUnkeyedRows:
    """An entry with no Plex/Trakt/TMDb/IMDb id still needs a stable identity."""

    def test_repeated_saves_do_not_duplicate_an_unkeyed_row(self, temp_state_file):
        manager = history_manager.WatchHistoryManager(plex_server_id="shared-server")
        manager.add_or_update_synced_item(media_type="movie")

        counts = []
        for _ in range(4):
            manager.save_state()
            counts.append(len(json.loads(temp_state_file.read_text())["synced_items"]))

        assert counts == [1, 1, 1, 1]

    def test_a_pruned_unkeyed_row_stays_pruned(self, temp_state_file):
        manager = history_manager.WatchHistoryManager(plex_server_id="shared-server")
        manager.add_or_update_synced_item(media_type="movie")
        manager.state["synced_items"][0]["updated_at"] = "2000-01-01T00:00:00+00:00"
        manager.save_state()

        assert manager.prune_synced_items(max_age_days=180) == 1
        manager.save_state()

        reloaded = history_manager.WatchHistoryManager(plex_server_id="shared-server")
        assert reloaded.state["synced_items"] == []

    def test_two_different_unkeyed_rows_stay_two(self, temp_state_file):
        manager = history_manager.WatchHistoryManager(plex_server_id="shared-server")
        manager.add_or_update_synced_item(media_type="movie")
        manager.add_or_update_synced_item(media_type="episode")
        manager.save_state()

        reloaded = history_manager.WatchHistoryManager(plex_server_id="shared-server")
        assert sorted(item["media_type"] for item in reloaded.state["synced_items"]) == [
            "episode",
            "movie",
        ]


# ---------------------------------------------------------------------------
# Issue #127: a failed write is reported, not reported as success
# ---------------------------------------------------------------------------


class TestFailedWritesAreReported:
    """A mutator must not claim a change the file does not hold (issue #127)."""

    def test_save_state_reports_success(self, mock_history_manager):
        assert mock_history_manager.save_state() is True

    def test_remove_reports_a_failed_write(self, mock_history_manager, monkeypatch):
        mock_history_manager.add_or_update_synced_item(
            media_type="movie", imdb_id="tt1", plex_rating_key=1
        )
        assert mock_history_manager.save_state() is True
        monkeypatch.setattr(history_manager, "write_json_atomic", lambda *a, **k: False)

        assert mock_history_manager.remove_synced_item(plex_rating_key=1) is False

    def test_timestamp_does_not_advance_after_a_failed_write(
        self, mock_history_manager, monkeypatch
    ):
        monkeypatch.setattr(history_manager, "write_json_atomic", lambda *a, **k: False)

        assert mock_history_manager.update_last_sync_timestamp() is False
        assert mock_history_manager.get_last_sync_timestamp() is None

    def test_restore_reports_a_failed_write(self, mock_history_manager, tmp_path, monkeypatch):
        mock_history_manager.add_or_update_synced_item(
            media_type="movie", imdb_id="tt2", plex_rating_key=2
        )
        assert mock_history_manager.save_state() is True
        backup = mock_history_manager.backup_state(tmp_path / "state.json.backup")
        assert backup is not None
        assert mock_history_manager.clear_all() is True

        monkeypatch.setattr(history_manager, "write_json_atomic", lambda *a, **k: False)

        assert mock_history_manager.restore_from_backup(backup) is False

    def test_clear_reports_a_failed_write(self, mock_history_manager, monkeypatch):
        monkeypatch.setattr(history_manager, "write_json_atomic", lambda *a, **k: False)

        assert mock_history_manager.clear_all() is False


# ---------------------------------------------------------------------------
# Issue #128: an unreadable state file is not replaced
# ---------------------------------------------------------------------------


class TestUnreadableStateFile:
    """Only a missing file may be replaced with this run's rows (issue #128)."""

    def test_an_unreadable_file_keeps_the_other_runs_rows(
        self, temp_state_file, mock_history_manager, monkeypatch
    ):
        mock_history_manager.add_or_update_synced_item(
            media_type="movie", imdb_id="ttA", plex_rating_key=1
        )
        assert mock_history_manager.save_state() is True
        before = temp_state_file.read_text(encoding="utf-8")

        mock_history_manager.add_or_update_synced_item(
            media_type="movie", imdb_id="ttA2", plex_rating_key=2
        )
        monkeypatch.setattr(history_manager, "try_read_json_object", lambda path: (False, {}))

        assert mock_history_manager.save_state() is False
        assert temp_state_file.read_text(encoding="utf-8") == before
        assert "ttA2" not in before

    def test_a_missing_file_is_written(self, temp_state_file, mock_history_manager):
        assert not temp_state_file.exists()
        mock_history_manager.add_or_update_synced_item(
            media_type="movie", imdb_id="ttB", plex_rating_key=3
        )

        assert mock_history_manager.save_state() is True
        assert "ttB" in temp_state_file.read_text(encoding="utf-8")


def test_try_read_json_object_separates_unreadable_from_missing(tmp_path):
    """The shared reader reports a file it could not parse (issue #128)."""
    from traktor.utils import try_read_json_object

    missing = tmp_path / "missing.json"
    assert try_read_json_object(missing) == (True, {})

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert try_read_json_object(broken) == (False, {})

    good = tmp_path / "good.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    assert try_read_json_object(good) == (True, {"a": 1})
