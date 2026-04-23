"""Tests for watch_sync module."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from plexapi.exceptions import NotFound

from traktor import conflict_resolver, history_manager, watch_sync
from traktor.clients import PlexClient, TraktClient
from traktor.utils import normalize_tmdb_id


@pytest.fixture
def mock_plex_client():
    """Create a mock Plex client."""
    client = MagicMock()
    client.is_watched = MagicMock(return_value=(False, None))
    client.mark_as_watched = MagicMock(return_value=True)
    client.mark_as_unwatched = MagicMock(return_value=True)
    return client


@pytest.fixture
def mock_trakt_client():
    """Create a mock Trakt client."""
    client = MagicMock()
    client.get_watched_movies = MagicMock(return_value=[])
    client.get_watched_shows = MagicMock(return_value=[])
    client.add_to_history = MagicMock(return_value={"added": {"movies": 1, "episodes": 0}})
    client.remove_from_history = MagicMock(return_value={"deleted": {"movies": 1, "episodes": 0}})
    return client


@pytest.fixture
def mock_history_manager(tmp_path, monkeypatch):
    """Create a mock history manager."""
    state_file = tmp_path / ".traktor_watch_sync.json"
    monkeypatch.setattr(history_manager, "WATCH_SYNC_FILE", state_file)
    return history_manager.WatchHistoryManager(plex_server_id="test-server")


@pytest.fixture
def mock_conflict_resolver():
    """Create a mock conflict resolver."""
    return conflict_resolver.ConflictResolver("newest_wins")


@pytest.fixture
def sync_engine(mock_plex_client, mock_trakt_client, mock_history_manager, mock_conflict_resolver):
    """Create a WatchSyncEngine with mock dependencies."""
    return watch_sync.WatchSyncEngine(
        plex_client=mock_plex_client,
        trakt_client=mock_trakt_client,
        history_manager=mock_history_manager,
        conflict_resolver=mock_conflict_resolver,
    )


class TestWatchSyncEngine:
    """Tests for WatchSyncEngine class."""

    def test_normalize_tmdb_id_with_int(self, sync_engine):
        """Test TMDb ID normalization with integer."""
        assert normalize_tmdb_id(12345) == "12345"

    def test_normalize_tmdb_id_with_string(self, sync_engine):
        """Test TMDb ID normalization with string."""
        assert normalize_tmdb_id("12345") == "12345"

    def test_normalize_tmdb_id_with_none(self, sync_engine):
        """Test TMDb ID normalization with None."""
        assert normalize_tmdb_id(None) is None

    def test_normalize_tmdb_id_with_zero(self, sync_engine):
        """Test TMDb ID normalization with zero (falsy)."""
        assert normalize_tmdb_id(0) is None
        assert normalize_tmdb_id("0") == "0"  # String "0" is truthy

    def test_normalize_tmdb_id_with_empty_string(self, sync_engine):
        """Test TMDb ID normalization with empty string."""
        assert normalize_tmdb_id("") is None

    def test_init(self, sync_engine):
        """Test engine initialization."""
        assert sync_engine.dry_run is False
        assert sync_engine.stats["plex_watched"] == 0
        assert sync_engine.stats["trakt_watched"] == 0

    def test_sync_watched_status_stats_reset(self, sync_engine):
        """Test that stats are reset at start of sync."""
        # Modify stats
        sync_engine.stats["plex_watched"] = 100

        # Mock pull methods to return empty
        sync_engine._pull_from_plex = MagicMock(return_value={})
        sync_engine._pull_from_trakt = MagicMock(return_value={})
        sync_engine._calculate_changes = MagicMock(
            return_value={
                "plex": {"mark_watched": [], "mark_unwatched": []},
                "trakt": {"mark_watched": [], "mark_unwatched": []},
            }
        )
        sync_engine._apply_changes = MagicMock()

        # Run sync
        sync_engine.sync_watched_status()

        # Stats should be reset
        assert sync_engine.stats["plex_watched"] == 0

    def test_sync_watched_status_dry_run(self, sync_engine):
        """Test dry run mode."""
        sync_engine._pull_from_plex = MagicMock(return_value={})
        sync_engine._pull_from_trakt = MagicMock(return_value={})
        sync_engine._calculate_changes = MagicMock(
            return_value={
                "plex": {"mark_watched": [], "mark_unwatched": []},
                "trakt": {"mark_watched": [], "mark_unwatched": []},
            }
        )
        sync_engine._log_dry_run_changes = MagicMock()

        # Run in dry-run mode
        sync_engine.sync_watched_status(dry_run=True)

        # Should call dry-run logger, not apply changes
        sync_engine._log_dry_run_changes.assert_called_once()

    def test_calculate_changes_no_differences(self, sync_engine):
        """Test calculating changes when states match."""
        plex_state = {
            ("movie", "tt1234567", None): {
                "watched": True,
                "last_watched_at": datetime.now(),
            }
        }
        trakt_state = {
            ("movie", "tt1234567", None): {
                "watched": True,
                "last_watched_at": datetime.now(),
            }
        }

        changes = sync_engine._calculate_changes(plex_state, trakt_state, "both")

        # No changes needed when states match
        assert len(changes["plex"]["mark_watched"]) == 0
        assert len(changes["trakt"]["mark_watched"]) == 0

    def test_calculate_changes_push_to_trakt(self, sync_engine):
        """Test calculating changes to push to Trakt."""
        now = datetime.now()
        plex_state = {
            ("movie", "tt1234567", None): {
                "watched": True,
                "last_watched_at": now,
                "rating_key": "12345",
                "title": "Test Movie",
            }
        }
        trakt_state = {
            ("movie", "tt1234567", None): {
                "watched": False,
                "last_watched_at": None,
            }
        }

        changes = sync_engine._calculate_changes(plex_state, trakt_state, "both")

        assert len(changes["trakt"]["mark_watched"]) == 1
        assert changes["trakt"]["mark_watched"][0]["imdb_id"] == "tt1234567"

    def test_calculate_changes_push_to_plex(self, sync_engine):
        """Test calculating changes to push to Plex."""
        now = datetime.now()
        plex_state = {
            ("movie", "tt1234567", None): {
                "watched": False,
                "last_watched_at": None,
                "rating_key": "12345",
            }
        }
        trakt_state = {
            ("movie", "tt1234567", None): {
                "watched": True,
                "last_watched_at": now,
                "trakt_id": "trakt-123",
                "title": "Test Movie",
            }
        }

        changes = sync_engine._calculate_changes(plex_state, trakt_state, "both")

        assert len(changes["plex"]["mark_watched"]) == 1
        assert changes["plex"]["mark_watched"][0]["rating_key"] == "12345"

    def test_calculate_changes_direction_filter(self, sync_engine):
        """Test that direction filters changes."""
        plex_state = {
            ("movie", "tt1234567", None): {"watched": True, "last_watched_at": datetime.now()},
        }
        trakt_state = {
            ("movie", "tt1234567", None): {"watched": False, "last_watched_at": None},
        }

        # plex-to-trakt direction
        changes = sync_engine._calculate_changes(plex_state, trakt_state, "plex-to-trakt")
        assert len(changes["trakt"]["mark_watched"]) == 1

        # trakt-to-plex direction - should not push since Plex is already watched
        changes = sync_engine._calculate_changes(plex_state, trakt_state, "trakt-to-plex")
        assert len(changes["plex"]["mark_watched"]) == 0

    def test_apply_changes_mark_watched_plex(self, sync_engine):
        """Test applying mark watched changes to Plex."""
        # Mock batch method
        sync_engine.plex.batch_mark_as_watched = MagicMock(
            return_value={"success": 1, "failed": 0, "errors": []}
        )

        changes = {
            "plex": {
                "mark_watched": [
                    {
                        "key": ("movie", "tt1234567", None),
                        "imdb_id": "tt1234567",
                        "media_type": "movie",
                        "rating_key": "12345",
                        "title": "Test Movie",
                    }
                ],
                "mark_unwatched": [],
            },
            "trakt": {"mark_watched": [], "mark_unwatched": []},
        }

        sync_engine._apply_changes(changes)

        sync_engine.plex.batch_mark_as_watched.assert_called_once_with(["12345"])
        assert sync_engine.stats["plex_watched"] == 1

    def test_apply_changes_mark_unwatched_plex(self, sync_engine):
        """Test applying mark unwatched changes to Plex."""
        # Mock batch method
        sync_engine.plex.batch_mark_as_unwatched = MagicMock(
            return_value={"success": 1, "failed": 0, "errors": []}
        )

        changes = {
            "plex": {
                "mark_watched": [],
                "mark_unwatched": [
                    {
                        "key": ("movie", "tt1234567", None),
                        "imdb_id": "tt1234567",
                        "media_type": "movie",
                        "rating_key": "12345",
                        "title": "Test Movie",
                    }
                ],
            },
            "trakt": {"mark_watched": [], "mark_unwatched": []},
        }

        sync_engine._apply_changes(changes)

        sync_engine.plex.batch_mark_as_unwatched.assert_called_once_with(["12345"])
        assert sync_engine.stats["plex_unwatched"] == 1

    def test_apply_changes_mark_watched_trakt(self, sync_engine):
        """Test applying mark watched changes to Trakt."""
        changes = {
            "plex": {"mark_watched": [], "mark_unwatched": []},
            "trakt": {
                "mark_watched": [
                    {
                        "key": ("movie", "tt1234567", 12345),
                        "imdb_id": "tt1234567",
                        "tmdb_id": 12345,
                        "media_type": "movie",
                        "title": "Test Movie",
                    }
                ],
                "mark_unwatched": [],
            },
        }

        sync_engine._apply_changes(changes)

        sync_engine.trakt.add_to_history.assert_called_once()
        call_args = sync_engine.trakt.add_to_history.call_args
        assert call_args.kwargs["movies"] is not None
        assert len(call_args.kwargs["movies"]) == 1

    def test_apply_changes_error_handling(self, sync_engine):
        """Test error handling during change application."""
        # Mock batch method to return failure
        sync_engine.plex.batch_mark_as_watched = MagicMock(
            return_value={"success": 0, "failed": 1, "errors": ["Failed"]}
        )

        changes = {
            "plex": {
                "mark_watched": [
                    {
                        "key": ("movie", "tt1234567", None),
                        "imdb_id": "tt1234567",
                        "media_type": "movie",
                        "rating_key": "12345",
                    }
                ],
                "mark_unwatched": [],
            },
            "trakt": {"mark_watched": [], "mark_unwatched": []},
        }

        sync_engine._apply_changes(changes)

        # Should track error
        assert sync_engine.stats["errors"] == 1
        assert sync_engine.stats["plex_watched"] == 0

    def test_log_dry_run_changes(self, sync_engine, caplog):
        """Test dry run logging."""
        import logging

        with caplog.at_level(logging.INFO):
            changes = {
                "plex": {
                    "mark_watched": [{"title": "Movie 1"}, {"title": "Movie 2"}],
                    "mark_unwatched": [{"title": "Movie 3"}],
                },
                "trakt": {
                    "mark_watched": [{"title": "Movie 4"}],
                    "mark_unwatched": [{"title": "Movie 5"}, {"title": "Movie 6"}],
                },
            }

            sync_engine._log_dry_run_changes(changes)

        assert "DRY RUN" in caplog.text
        assert "Movie 1" in caplog.text
        assert "Movie 4" in caplog.text

        # Stats should be updated for reporting
        assert sync_engine.stats["plex_watched"] == 2
        assert sync_engine.stats["trakt_unwatched"] == 2

    def test_get_sync_summary(self, sync_engine):
        """Test getting sync summary."""
        summary = sync_engine.get_sync_summary()

        assert "last_sync" in summary
        assert "total_tracked_items" in summary
        assert "watched_both" in summary
        assert "last_operation_stats" in summary


class TestEpisodeKeyUniqueness:
    """Tests for episode key uniqueness in watch sync."""

    def test_episode_keys_are_unique_per_season_episode(self, sync_engine):
        """Test that multiple episodes from the same show have unique keys."""
        # Mock Trakt response with multiple episodes from same show
        sync_engine.trakt.get_watched_shows.return_value = [
            {
                "show": {
                    "title": "Test Show",
                    "ids": {"imdb": "tt1234567", "tmdb": 12345},
                },
                "seasons": [
                    {
                        "number": 1,
                        "episodes": [
                            {"number": 1, "last_watched_at": "2026-04-10T10:00:00Z"},
                            {"number": 2, "last_watched_at": "2026-04-10T11:00:00Z"},
                            {"number": 3, "last_watched_at": "2026-04-10T12:00:00Z"},
                        ],
                    },
                    {
                        "number": 2,
                        "episodes": [
                            {"number": 1, "last_watched_at": "2026-04-11T10:00:00Z"},
                        ],
                    },
                ],
            }
        ]

        trakt_state = sync_engine._pull_from_trakt(movies_only=True, shows_only=False)
        # Should have 0 items since shows_only=False means skip shows
        assert len(trakt_state) == 0

        trakt_state = sync_engine._pull_from_trakt(movies_only=False, shows_only=True)
        # Should have 4 episodes
        assert len(trakt_state) == 4

        # Verify all keys are unique
        keys = list(trakt_state.keys())
        assert len(keys) == len(set(keys)), "Episode keys should be unique"

        # Verify key format is ("episode", show_imdb, season_num, episode_num)
        for key in keys:
            assert key[0] == "episode"
            assert key[1] == "tt1234567"  # Show IMDb
            assert isinstance(key[2], int)  # Season number
            assert isinstance(key[3], int)  # Episode number

        # Verify we have the expected episodes
        expected_keys = {
            ("episode", "tt1234567", 1, 1),
            ("episode", "tt1234567", 1, 2),
            ("episode", "tt1234567", 1, 3),
            ("episode", "tt1234567", 2, 1),
        }
        assert set(keys) == expected_keys

    def test_episode_type_handling_dict_vs_int(self, sync_engine):
        """Test that episodes work whether they are dicts or just episode numbers."""
        # Mock with mixed episode formats (dicts and ints)
        sync_engine.trakt.get_watched_shows.return_value = [
            {
                "show": {
                    "title": "Test Show",
                    "ids": {"imdb": "tt1234567"},
                },
                "seasons": [
                    {
                        "number": 1,
                        "episodes": [
                            {"number": 1, "last_watched_at": "2026-04-10T10:00:00Z"},
                            2,  # Just the episode number (int)
                            {"number": 3},  # Dict without timestamp
                        ],
                    },
                ],
            }
        ]

        trakt_state = sync_engine._pull_from_trakt(movies_only=False, shows_only=True)

        # All 3 episodes should be present
        assert len(trakt_state) == 3

        # Verify all episodes have proper keys
        assert ("episode", "tt1234567", 1, 1) in trakt_state
        assert ("episode", "tt1234567", 1, 2) in trakt_state
        assert ("episode", "tt1234567", 1, 3) in trakt_state

        # Verify episode with timestamp has it preserved
        assert (
            trakt_state[("episode", "tt1234567", 1, 1)]["last_watched_at"] == "2026-04-10T10:00:00Z"
        )

        # Verify episode without timestamp has None
        assert trakt_state[("episode", "tt1234567", 1, 2)]["last_watched_at"] is None
        assert trakt_state[("episode", "tt1234567", 1, 3)]["last_watched_at"] is None

    def test_episode_key_includes_show_imdb_season_and_episode(self, sync_engine):
        """Test that episode keys properly include all identifying information."""
        sync_engine.trakt.get_watched_shows.return_value = [
            {
                "show": {
                    "title": "Test Show",
                    "ids": {"imdb": "tt1234567"},
                },
                "seasons": [
                    {
                        "number": 1,
                        "episodes": [
                            {"number": 1},
                        ],
                    },
                ],
            }
        ]

        trakt_state = sync_engine._pull_from_trakt(movies_only=False, shows_only=True)

        # Key should be a 4-tuple with all identifying info
        key = ("episode", "tt1234567", 1, 1)
        assert key in trakt_state

        item = trakt_state[key]
        assert item["show_title"] == "Test Show"
        assert item["season"] == 1
        assert item["episode"] == 1


class TestCalculateChangesEpisodeKeys:
    """Tests for episode key handling in _calculate_changes."""

    def test_calculate_changes_handles_episode_keys(self, sync_engine):
        """Test that _calculate_changes correctly handles episode keys (4-tuples)."""
        now = datetime.now()

        # Episode keys are 4-tuples: ("episode", show_imdb, season_num, episode_num)
        plex_state = {
            ("episode", "tt1234567", 1, 1): {
                "watched": True,
                "last_watched_at": now,
                "rating_key": "12345",
                "title": "Test Show S01E01",
            }
        }
        trakt_state = {
            ("episode", "tt1234567", 1, 1): {
                "watched": False,
                "last_watched_at": None,
            }
        }

        # Should not raise ValueError for unpacking 4-tuple as 3-tuple
        changes = sync_engine._calculate_changes(plex_state, trakt_state, "both")

        # Plex watched, Trakt not -> push to Trakt
        assert len(changes["trakt"]["mark_watched"]) == 1
        assert changes["trakt"]["mark_watched"][0]["media_type"] == "episode"
        assert changes["trakt"]["mark_watched"][0]["imdb_id"] == "tt1234567"

    def test_calculate_changes_handles_mixed_movie_and_episode_keys(self, sync_engine):
        """Test that _calculate_changes handles both movie (3-tuple) and episode (4-tuple) keys."""
        now = datetime.now()

        plex_state = {
            # Movie key: 3-tuple
            ("movie", "tt1111111", None): {
                "watched": True,
                "last_watched_at": now,
                "rating_key": "111",
                "title": "Test Movie",
            },
            # Episode key: 4-tuple
            ("episode", "tt2222222", 1, 2): {
                "watched": True,
                "last_watched_at": now,
                "rating_key": "222",
                "title": "Test Show S01E02",
            },
        }
        trakt_state = {
            ("movie", "tt1111111", None): {"watched": False},
            ("episode", "tt2222222", 1, 2): {"watched": False},
        }

        changes = sync_engine._calculate_changes(plex_state, trakt_state, "both")

        # Both should be pushed to Trakt
        assert len(changes["trakt"]["mark_watched"]) == 2
        media_types = {item["media_type"] for item in changes["trakt"]["mark_watched"]}
        assert media_types == {"movie", "episode"}

    def test_calculate_changes_skips_plex_push_when_no_rating_key(self, sync_engine):
        """Test that pushing to Plex is skipped when item doesn't exist in Plex."""
        now = datetime.now()

        plex_state = {
            # Episode exists in Trakt but not in Plex (no plex_info entry or no rating_key)
            # Key present but with None rating_key simulates "not in Plex"
        }
        trakt_state = {
            ("episode", "tt1234567", 1, 1): {
                "watched": True,
                "last_watched_at": now,
                "trakt_id": "trakt-123",
                "title": "Test Episode",
            }
        }

        changes = sync_engine._calculate_changes(plex_state, trakt_state, "both")

        # Should try to push to Plex, but no rating_key available
        # This should not crash, but should log a warning and skip
        # Since plex_info is None, we can't push to Plex
        assert len(changes["plex"]["mark_watched"]) == 0  # No push because no rating_key


class TestPlaybackProgressSync:
    """Tests for playback progress/resume point synchronization."""

    def test_plex_client_get_playback_progress(self):
        """Test getting playback progress from Plex client."""
        # Create a real PlexClient with mocked dependencies
        mock_server = MagicMock()
        mock_cache = MagicMock()

        plex_client = PlexClient(mock_server, mock_cache)

        # Mock the fetchItem to return a mock item with viewOffset
        mock_item = MagicMock()
        mock_item.viewOffset = 300000  # 5 minutes in ms
        mock_item.duration = 3600000  # 1 hour in ms
        mock_server.fetchItem.return_value = mock_item

        view_offset, duration = plex_client.get_playback_progress("12345")

        assert view_offset == 300000
        assert duration == 3600000

    def test_plex_client_get_playback_progress_not_found(self):
        """Test getting playback progress when item not found."""
        mock_server = MagicMock()
        mock_cache = MagicMock()

        plex_client = PlexClient(mock_server, mock_cache)

        # Mock fetchItem to raise NotFound
        mock_server.fetchItem.side_effect = NotFound("Item not found")

        view_offset, duration = plex_client.get_playback_progress("12345")

        assert view_offset is None
        assert duration is None

    def test_trakt_client_get_playback_progress(self):
        """Test getting playback progress from Trakt client."""
        mock_auth = MagicMock()
        mock_auth.get_headers.return_value = {"Authorization": "Bearer test"}

        trakt_client = TraktClient(mock_auth)

        # Mock the _request method
        progress_data = [
            {
                "type": "movie",
                "movie": {
                    "title": "Test Movie",
                    "ids": {"imdb": "tt1234567", "tmdb": 12345},
                },
                "progress": 45.5,
                "paused_at": "2024-01-15T10:30:00Z",
                "id": 123,
            }
        ]

        with patch.object(trakt_client, "_request") as mock_request:
            mock_response = MagicMock()
            mock_response.json.return_value = progress_data
            mock_request.return_value = mock_response

            progress = trakt_client.get_playback_progress("movies")

            assert len(progress) == 1
            key = ("movie", "tt1234567", "12345")
            assert key in progress
            assert progress[key]["progress_percent"] == 45.5
            assert progress[key]["id"] == 123

    def test_plex_client_set_playback_progress(self):
        """Test setting playback progress in Plex client."""
        mock_server = MagicMock()
        mock_cache = MagicMock()

        plex_client = PlexClient(mock_server, mock_cache)

        # Mock the fetchItem to return a mock item with updateProgress
        mock_item = MagicMock()
        mock_item.title = "Test Movie"
        mock_item.updateProgress = MagicMock()
        mock_server.fetchItem.return_value = mock_item

        result = plex_client.set_playback_progress("12345", 300000)

        assert result is True
        mock_item.updateProgress.assert_called_once_with(300000)

    def test_plex_client_set_playback_progress_not_supported(self):
        """Test setting playback progress when item doesn't support it."""
        mock_server = MagicMock()
        mock_cache = MagicMock()

        plex_client = PlexClient(mock_server, mock_cache)

        # Mock item without updateProgress method
        mock_item = MagicMock()
        mock_item.title = "Test Movie"
        del mock_item.updateProgress  # Remove the attribute
        mock_server.fetchItem.return_value = mock_item

        result = plex_client.set_playback_progress("12345", 300000)

        assert result is False

    def test_plex_client_set_playback_progress_not_found(self):
        """Test setting playback progress when item not found."""
        mock_server = MagicMock()
        mock_cache = MagicMock()

        plex_client = PlexClient(mock_server, mock_cache)

        # Mock fetchItem to raise NotFound
        mock_server.fetchItem.side_effect = NotFound("Item not found")

        result = plex_client.set_playback_progress("12345", 300000)

        assert result is False

    def test_plex_client_batch_set_playback_progress(self):
        """Test batch setting playback progress."""
        mock_server = MagicMock()
        mock_cache = MagicMock()

        plex_client = PlexClient(mock_server, mock_cache)

        # Mock items
        mock_item1 = MagicMock()
        mock_item1.title = "Movie 1"
        mock_item1.updateProgress = MagicMock()

        mock_item2 = MagicMock()
        mock_item2.title = "Movie 2"
        mock_item2.updateProgress = MagicMock()

        mock_server.fetchItem.side_effect = [mock_item1, mock_item2]

        progress_updates = [
            {"rating_key": "12345", "view_offset_ms": 300000},
            {"rating_key": "67890", "view_offset_ms": 600000},
        ]

        result = plex_client.batch_set_playback_progress(progress_updates)

        assert result["success"] == 2
        assert result["failed"] == 0
        assert len(result["errors"]) == 0
        mock_item1.updateProgress.assert_called_once_with(300000)
        mock_item2.updateProgress.assert_called_once_with(600000)

    def test_plex_client_batch_set_playback_progress_partial_failure(self):
        """Test batch setting progress with some failures."""
        mock_server = MagicMock()
        mock_cache = MagicMock()

        plex_client = PlexClient(mock_server, mock_cache)

        # First item succeeds, second fails (NotFound)
        mock_item = MagicMock()
        mock_item.title = "Movie 1"
        mock_item.updateProgress = MagicMock()

        mock_server.fetchItem.side_effect = [mock_item, NotFound("Item not found")]

        progress_updates = [
            {"rating_key": "12345", "view_offset_ms": 300000},
            {"rating_key": "67890", "view_offset_ms": 600000},
        ]

        result = plex_client.batch_set_playback_progress(progress_updates)

        assert result["success"] == 1
        assert result["failed"] == 1
        assert len(result["errors"]) == 1
