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
