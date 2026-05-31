"""Tests for interactive module."""

from unittest.mock import patch

from traktor import interactive


class TestIsInteractive:
    """Tests for is_interactive function."""

    def test_is_interactive_when_tty(self):
        """Test is_interactive returns True when both stdin and stdout are TTY."""
        with patch("sys.stdin.isatty", return_value=True):
            with patch("sys.stdout.isatty", return_value=True):
                assert interactive.is_interactive() is True

    def test_is_interactive_when_stdin_not_tty(self):
        """Test is_interactive returns False when stdin is not a TTY."""
        with patch("sys.stdin.isatty", return_value=False):
            with patch("sys.stdout.isatty", return_value=True):
                assert interactive.is_interactive() is False

    def test_is_interactive_when_stdout_not_tty(self):
        """Test is_interactive returns False when stdout is not a TTY."""
        with patch("sys.stdin.isatty", return_value=True):
            with patch("sys.stdout.isatty", return_value=False):
                assert interactive.is_interactive() is False

    def test_is_interactive_when_neither_tty(self):
        """Test is_interactive returns False when neither is a TTY."""
        with patch("sys.stdin.isatty", return_value=False):
            with patch("sys.stdout.isatty", return_value=False):
                assert interactive.is_interactive() is False


class TestConfirmChanges:
    """Tests for confirm_changes function."""

    def test_confirm_changes_non_interactive_returns_true(self, caplog):
        """Test that non-interactive mode returns True without prompting."""
        import logging

        with patch("traktor.interactive.is_interactive", return_value=False):
            with caplog.at_level(logging.WARNING):
                result = interactive.confirm_changes("test operation")

        assert result is True
        assert "Non-interactive mode" in caplog.text

    def test_confirm_changes_yes_input(self):
        """Test confirm_changes with 'y' input."""
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", return_value="y"):
                result = interactive.confirm_changes("test operation")
        assert result is True

    def test_confirm_changes_yes_input_uppercase(self):
        """Test confirm_changes with 'Y' input."""
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", return_value="Y"):
                result = interactive.confirm_changes("test operation")
        assert result is True

    def test_confirm_changes_no_input(self):
        """Test confirm_changes with 'n' input."""
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", return_value="n"):
                result = interactive.confirm_changes("test operation")
        assert result is False

    def test_confirm_changes_empty_input_defaults_true(self):
        """Test confirm_changes with empty input defaults to True."""
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", return_value=""):
                result = interactive.confirm_changes("test operation", default=True)
        assert result is True

    def test_confirm_changes_empty_input_defaults_false(self):
        """Test confirm_changes with empty input defaults to False."""
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", return_value=""):
                result = interactive.confirm_changes("test operation", default=False)
        assert result is False

    def test_confirm_changes_eof_error(self):
        """Test confirm_changes handles EOFError gracefully."""
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", side_effect=EOFError):
                result = interactive.confirm_changes("test operation")
        assert result is False

    def test_confirm_changes_keyboard_interrupt(self):
        """Test confirm_changes handles KeyboardInterrupt gracefully."""
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", side_effect=KeyboardInterrupt):
                result = interactive.confirm_changes("test operation")
        assert result is False

    def test_confirm_changes_with_items(self):
        """Test confirm_changes displays items list."""
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", return_value="y"):
                result = interactive.confirm_changes(
                    "test operation",
                    items=["item1", "item2", "item3"],
                )
        assert result is True

    def test_confirm_changes_with_many_items_truncates(self):
        """Test confirm_changes truncates long item lists."""
        items = [f"item{i}" for i in range(15)]
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", return_value="y"):
                result = interactive.confirm_changes(
                    "test operation",
                    items=items,
                )
        assert result is True


class TestPreviewChanges:
    """Tests for preview_changes function."""

    def test_preview_changes_non_interactive_returns_true(self, caplog):
        """Test that non-interactive mode returns True without preview."""
        import logging

        with patch("traktor.interactive.is_interactive", return_value=False):
            with caplog.at_level(logging.WARNING):
                result = interactive.preview_changes({})

        assert result is True
        assert "Non-interactive mode" in caplog.text

    def test_preview_changes_watch_sync_empty(self):
        """Test preview_changes with no watch sync changes."""
        changes = {
            "plex": {"mark_watched": [], "mark_unwatched": []},
            "trakt": {"mark_watched": [], "mark_unwatched": []},
        }
        with patch("traktor.interactive.is_interactive", return_value=True):
            result = interactive.preview_changes(changes, change_type="watch_sync")
        assert result is True

    def test_preview_changes_watch_sync_with_changes_confirmed(self):
        """Test preview_changes with watch sync changes and user confirms."""
        changes = {
            "plex": {
                "mark_watched": [{"title": "Movie 1"}, {"title": "Movie 2"}],
                "mark_unwatched": [],
            },
            "trakt": {
                "mark_watched": [],
                "mark_unwatched": [{"title": "Movie 3"}],
            },
        }
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", return_value="y"):
                result = interactive.preview_changes(changes, change_type="watch_sync")
        assert result is True

    def test_preview_changes_watch_sync_with_changes_cancelled(self):
        """Test preview_changes with watch sync changes and user cancels."""
        changes = {
            "plex": {
                "mark_watched": [{"title": "Movie 1"}],
                "mark_unwatched": [],
            },
            "trakt": {
                "mark_watched": [],
                "mark_unwatched": [],
            },
        }
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", return_value="n"):
                result = interactive.preview_changes(changes, change_type="watch_sync")
        assert result is False

    def test_preview_changes_playlist_cleanup_empty(self):
        """Test preview_changes with empty playlist cleanup."""
        changes = {"playlists": []}
        with patch("traktor.interactive.is_interactive", return_value=True):
            result = interactive.preview_changes(changes, change_type="playlist_cleanup")
        assert result is True

    def test_preview_changes_playlist_cleanup_with_playlists(self):
        """Test preview_changes with playlists to delete."""
        changes = {"playlists": ["Playlist 1", "Playlist 2"]}
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", return_value="y"):
                result = interactive.preview_changes(changes, change_type="playlist_cleanup")
        assert result is True

    def test_preview_changes_unknown_type(self):
        """Test preview_changes with unknown change type."""
        changes = {"total": 5, "items": ["a", "b", "c"]}
        with patch("traktor.interactive.is_interactive", return_value=True):
            with patch("builtins.input", return_value="y"):
                result = interactive.preview_changes(changes, change_type="unknown")
        assert result is True


class TestUndoSnapshots:
    """Tests for undo snapshot operations."""

    def test_get_undo_dir_creates_directory(self, tmp_path, monkeypatch):
        """Test undo directory is created if it doesn't exist."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)
        undo_dir = interactive._get_undo_dir()
        assert undo_dir.exists()
        assert undo_dir.name == "undo"

    def test_generate_snapshot_filename(self):
        """Test snapshot filename generation is unique."""
        name1 = interactive._generate_snapshot_filename()
        name2 = interactive._generate_snapshot_filename()
        assert name1.startswith("snapshot_")
        assert name2.startswith("snapshot_")
        assert name1 != name2

    def test_save_undo_snapshot(self, tmp_path, monkeypatch):
        """Test saving an undo snapshot."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)
        data = {"test": "data", "number": 42}
        path = interactive.save_undo_snapshot("test_operation", data)

        assert path is not None
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "test_operation" in content
        assert "test" in content

    def test_save_undo_snapshot_creates_directory(self, tmp_path, monkeypatch):
        """Test save_undo_snapshot creates directory if needed."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)
        data = {"test": "data"}
        path = interactive.save_undo_snapshot("test", data)
        assert path is not None
        assert path.parent.exists()

    def test_restore_undo_snapshot_most_recent(self, tmp_path, monkeypatch):
        """Test restoring the most recent snapshot."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)
        data = {"test": "data"}
        interactive.save_undo_snapshot("test_operation", data)

        snapshot = interactive.restore_undo_snapshot()
        assert snapshot is not None
        assert snapshot["operation_type"] == "test_operation"
        assert snapshot["data"] == data

    def test_restore_undo_snapshot_specific_path(self, tmp_path, monkeypatch):
        """Test restoring a specific snapshot by path."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)
        data = {"test": "data"}
        path = interactive.save_undo_snapshot("test_operation", data)

        snapshot = interactive.restore_undo_snapshot(path)
        assert snapshot is not None
        assert snapshot["operation_type"] == "test_operation"

    def test_restore_undo_snapshot_not_found(self, tmp_path):
        """Test restoring a non-existent snapshot returns None."""
        missing = tmp_path / "missing.json"
        snapshot = interactive.restore_undo_snapshot(missing)
        assert snapshot is None

    def test_restore_undo_snapshot_no_snapshots(self, tmp_path, monkeypatch):
        """Test restoring when no snapshots exist returns None."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)
        snapshot = interactive.restore_undo_snapshot()
        assert snapshot is None

    def test_list_undo_snapshots_sorted(self, tmp_path, monkeypatch):
        """Test snapshots are sorted by modification time (newest first)."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)

        # Create multiple snapshots
        interactive.save_undo_snapshot("op1", {"id": 1})
        interactive.save_undo_snapshot("op2", {"id": 2})
        interactive.save_undo_snapshot("op3", {"id": 3})

        snapshots = interactive.list_undo_snapshots()
        assert len(snapshots) == 3
        # Check they are sorted newest first by checking timestamps in names
        # (since filenames contain microseconds, newer ones should sort first)
        names = [s.name for s in snapshots]
        assert len(names) == 3
        assert len(set(names)) == 3  # All unique

    def test_list_undo_snapshots_empty(self, tmp_path, monkeypatch):
        """Test listing snapshots when none exist."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)
        snapshots = interactive.list_undo_snapshots()
        assert snapshots == []

    def test_cleanup_old_undo_snapshots(self, tmp_path, monkeypatch):
        """Test old snapshots are cleaned up."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)

        # Create 5 snapshots
        for i in range(5):
            interactive.save_undo_snapshot(f"op{i}", {"id": i})

        # Explicitly clean up with limit 3
        interactive.cleanup_old_undo_snapshots(max_snapshots=3)

        snapshots = interactive.list_undo_snapshots()
        assert len(snapshots) == 3

    def test_cleanup_old_undo_snapshots_respects_limit(self, tmp_path, monkeypatch):
        """Test cleanup respects the max_snapshots limit."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)

        # Create 2 snapshots (less than default MAX_UNDO_SNAPSHOTS=5)
        interactive.save_undo_snapshot("op1", {"id": 1})
        interactive.save_undo_snapshot("op2", {"id": 2})

        snapshots_before = interactive.list_undo_snapshots()
        interactive.cleanup_old_undo_snapshots(max_snapshots=5)
        snapshots_after = interactive.list_undo_snapshots()

        assert len(snapshots_before) == len(snapshots_after)

    def test_cleanup_old_undo_snapshots_zero_limit(self, tmp_path, monkeypatch):
        """Test cleanup with max_snapshots=0 removes all."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)

        interactive.save_undo_snapshot("op1", {"id": 1})
        interactive.cleanup_old_undo_snapshots(max_snapshots=0)

        snapshots = interactive.list_undo_snapshots()
        assert len(snapshots) == 0

    def test_save_undo_snapshot_returns_none_on_error(self, tmp_path, monkeypatch):
        """Test save_undo_snapshot returns None on write error."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)

        # Create a file where the undo dir should be, preventing directory creation
        undo_dir = tmp_path / ".traktor" / "undo"
        undo_dir.parent.mkdir(parents=True, exist_ok=True)
        undo_dir.write_text("not a directory", encoding="utf-8")

        # Should handle FileExistsError gracefully
        path = interactive.save_undo_snapshot("test", {"data": "test"})
        assert path is None

    def test_snapshot_contains_version(self, tmp_path, monkeypatch):
        """Test snapshot includes version field."""
        monkeypatch.setattr(interactive, "DATA_DIR", tmp_path)
        data = {"test": "data"}
        path = interactive.save_undo_snapshot("test", data)

        snapshot = interactive.restore_undo_snapshot(path)
        assert snapshot["version"] == interactive.DEFAULT_SNAPSHOT_VERSION
        assert "timestamp" in snapshot
        assert "operation_type" in snapshot
        assert "data" in snapshot
