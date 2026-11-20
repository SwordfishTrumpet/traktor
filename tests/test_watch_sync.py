"""Tests for watch_sync module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests
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

    def test_process_episode_batch_reads_state_directly(self, sync_engine):
        """Regression A1: episode watch state comes from the season.episodes()
        response, never from per-episode fetchItem calls via plex.is_watched().
        """
        watched_ep = MagicMock()
        watched_ep.ratingKey = 101
        watched_ep.episodeNumber = 1
        watched_ep.isWatched = True
        watched_ep.lastViewedAt = datetime(2024, 6, 15, 12, 0, 0)

        unwatched_ep = MagicMock()
        unwatched_ep.ratingKey = 102
        unwatched_ep.episodeNumber = 2
        unwatched_ep.isWatched = False
        unwatched_ep.lastViewedAt = None

        plex_state = {}
        sync_engine._process_episode_batch(
            [watched_ep, unwatched_ep], {"title": "Test Show"}, "tt123", 1, plex_state
        )

        sync_engine.plex.is_watched.assert_not_called()
        assert plex_state[("episode", "tt123", 1, 1)]["watched"] is True
        assert plex_state[("episode", "tt123", 1, 1)]["rating_key"] == 101
        assert plex_state[("episode", "tt123", 1, 2)]["watched"] is False

    def test_pull_from_plex_shows_no_episode_api_storm(self, sync_engine):
        """Regression A1: _pull_from_plex must never call plex.is_watched for
        episodes (previously 1 fetchItem per episode on large libraries).
        """
        watched_ep = MagicMock()
        watched_ep.ratingKey = 201
        watched_ep.episodeNumber = 3
        watched_ep.isWatched = True
        watched_ep.lastViewedAt = datetime(2024, 6, 15, 12, 0, 0)

        season = MagicMock()
        season.seasonNumber = 2
        season.episodes.return_value = [watched_ep]

        show_item = MagicMock()
        show_item.seasons.return_value = [season]
        sync_engine.plex._get_plex_item.return_value = show_item

        sync_engine.plex.cache.memory_cache = {
            "movies_by_imdb": {},
            "movies_by_tmdb": {},
            "shows_by_imdb": {"tt999": {"ratingKey": 500}},
            "movies_list": [],
            "shows_list": [{"ratingKey": 500, "title": "Test Show"}],
        }
        sync_engine.plex.cache.movie_key_to_ids = {}
        sync_engine.plex.cache.show_key_to_imdb = {500: "tt999"}

        plex_state = sync_engine._pull_from_plex(shows_only=True)

        sync_engine.plex.is_watched.assert_not_called()
        assert plex_state[("episode", "tt999", 2, 3)]["watched"] is True
        assert plex_state[("episode", "tt999", 2, 3)]["rating_key"] == 201


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


class TestDeltaSyncRegression:
    """Regression tests for delta sync correctness (movies watched on other
    services must be picked up regardless of when they were watched, and naive
    Plex timestamps must not crash the Plex pull)."""

    def test_parse_plex_timestamp_naive_iso_string(self):
        """Naive ISO strings (plexapi cache format) must become aware UTC."""
        dt = watch_sync.WatchSyncEngine._parse_plex_timestamp("2023-02-06T21:30:39")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)

    def test_parse_plex_timestamp_naive_datetime(self):
        """Naive datetimes (plexapi attribute access) must become aware UTC."""
        result = watch_sync.WatchSyncEngine._parse_plex_timestamp(datetime(2023, 2, 6, 21, 30, 39))
        assert result is not None
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)

    def test_parse_plex_timestamp_aware_and_epoch(self):
        """Aware datetimes and epoch values must still parse."""
        aware = datetime(2023, 2, 6, 21, 30, 39, tzinfo=timezone.utc)
        assert watch_sync.WatchSyncEngine._parse_plex_timestamp(aware).utcoffset() == timedelta(0)
        epoch = 1675719039  # 2023-02-06T21:30:39Z
        assert watch_sync.WatchSyncEngine._parse_plex_timestamp(epoch).utcoffset() == timedelta(0)

    def test_pull_from_trakt_movies_ignores_delta_window(self, sync_engine):
        """Movies watched before the delta window must still be pulled.

        Regression: _pull_from_trakt used get_watched_history(start_at=...)
        which dropped any movie watched before the last sync, so movies watched
        on other services weeks/months ago were never marked watched in Plex.
        """
        since = datetime.now(timezone.utc) - timedelta(days=7)
        sync_engine.trakt.get_watched_movies.return_value = [
            {
                "last_watched_at": "2023-02-06T21:30:39.000Z",
                "movie": {"title": "Old Movie", "ids": {"imdb": "tt1234567", "tmdb": 12345}},
            }
        ]
        # get_watched_history must never be used for the movie state
        sync_engine.trakt.get_watched_history = MagicMock(return_value=[])

        state = sync_engine._pull_from_trakt(since=since, movies_only=True)

        assert ("movie", "tt1234567", "12345") in state
        assert state[("movie", "tt1234567", "12345")]["watched"] is True
        sync_engine.trakt.get_watched_movies.assert_called_once()
        sync_engine.trakt.get_watched_history.assert_not_called()

    def test_pull_from_plex_handles_naive_last_viewed(self, sync_engine):
        """Naive ISO lastViewedAt values must not crash the Plex pull.

        Regression: plexapi serializes lastViewedAt as a naive local datetime,
        which the cache stores as a naive ISO string. Comparing it against the
        aware `since` datetime raised TypeError, the outer except swallowed it,
        and _pull_from_plex returned {} - so nothing was ever synced to Plex.
        """
        now = datetime.now(timezone.utc)
        cache = {
            "movies_by_imdb": {
                "tt1111111": {"ratingKey": 111},
                "tt2222222": {"ratingKey": 222},
                "tt3333333": {"ratingKey": 333},
            },
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
            "movies_list": [
                # Watched long ago (outside delta window) - should be skipped
                {
                    "ratingKey": 111,
                    "title": "Old Watched",
                    "isWatched": True,
                    "lastViewedAt": "2023-02-06T21:30:39",
                },
                # Unwatched - must always be included (this is the user's case)
                {
                    "ratingKey": 222,
                    "title": "Unwatched Movie",
                    "isWatched": False,
                    "lastViewedAt": None,
                },
                # Watched recently (inside delta window) - should be included
                {
                    "ratingKey": 333,
                    "title": "Recent Watched",
                    "isWatched": True,
                    "lastViewedAt": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
                },
            ],
            "shows_list": [],
        }
        sync_engine.plex.cache.memory_cache = cache
        sync_engine.plex.cache.movie_key_to_ids = {
            111: ("tt1111111", None),
            222: ("tt2222222", None),
            333: ("tt3333333", None),
        }
        sync_engine.plex.cache.show_key_to_imdb = {}
        sync_engine.stats["items_skipped_due_to_delta"] = 0

        # Trakt already holds the old watch, with the same timestamp, so no
        # change is possible for it and the delta filter may drop it.
        known_state = {
            ("movie", "tt1111111", None): {
                "watched": True,
                "last_watched_at": "2023-02-06T21:30:39",
                "title": "Old Watched",
            }
        }

        plex_state = sync_engine._pull_from_plex(
            movies_only=False,
            shows_only=False,
            since=now - timedelta(days=7),
            known_state=known_state,
        )

        # No crash, and the unwatched movie is present with its rating key
        assert (
            "movie",
            "tt1111111",
            None,
        ) not in plex_state  # already watched in Trakt: delta-skipped
        assert ("movie", "tt2222222", None) in plex_state  # unwatched: present
        assert ("movie", "tt3333333", None) in plex_state  # recent watched: present
        assert sync_engine.stats["items_skipped_due_to_delta"] >= 1
        unwatched = plex_state[("movie", "tt2222222", None)]
        assert unwatched["watched"] is False
        assert unwatched["rating_key"] == 222

    def _movie_cache(self, last_viewed: str, rating_key: int = 111):
        return {
            "movies_by_imdb": {"tt1111111": {"ratingKey": rating_key}},
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
            "movies_list": [
                {
                    "ratingKey": rating_key,
                    "title": "Phone Booth",
                    "isWatched": True,
                    "lastViewedAt": last_viewed,
                }
            ],
            "shows_list": [],
        }

    def test_watch_recorded_during_a_run_is_pulled(self, sync_engine):
        """A watch recorded after the cache snapshot must not be delta-skipped.

        Regression (#19): the sync window starts when the previous run ended,
        but the Plex state it filters came from the cache snapshot taken when
        that run started. A watch recorded inside the run therefore sits behind
        the window, and skipping it lost the change on every later run too.
        """
        watched_at = datetime(2026, 10, 26, 18, 30, 34, tzinfo=timezone.utc)
        run_end = watched_at + timedelta(minutes=20)  # the run finished later

        sync_engine.plex.cache.memory_cache = self._movie_cache(
            watched_at.strftime("%Y-%m-%dT%H:%M:%S")
        )
        sync_engine.plex.cache.movie_key_to_ids = {111: ("tt1111111", None)}
        sync_engine.plex.cache.show_key_to_imdb = {}
        sync_engine.stats["items_skipped_due_to_delta"] = 0

        # Trakt has no record of the movie, so a change is needed and the
        # delta filter must not drop it. Before the fix this returned {}.
        plex_state = sync_engine._pull_from_plex(
            movies_only=True, shows_only=False, since=run_end, known_state={}
        )

        key = ("movie", "tt1111111", None)
        assert key in plex_state
        assert plex_state[key]["watched"] is True
        assert sync_engine.stats["items_skipped_due_to_delta"] == 0

        changes = sync_engine._calculate_changes(plex_state, {}, "both")
        assert [item["title"] for item in changes["trakt"]["mark_watched"]] == ["Phone Booth"]

    def test_episode_watch_recorded_during_a_run_is_pulled(self, sync_engine):
        """Episodes share the movie path's delta defect (#19 class sweep)."""
        watched_at = datetime(2026, 10, 26, 18, 30, 34, tzinfo=timezone.utc)
        run_end = watched_at + timedelta(minutes=20)
        episode = MagicMock()
        episode.ratingKey = 900
        episode.isWatched = True
        episode.lastViewedAt = watched_at
        episode.episodeNumber = 3
        plex_state = {}

        sync_engine._process_episode_batch(
            [episode],
            {"title": "Some Show"},
            "tt5555555",
            1,
            plex_state,
            since=run_end,
            known_state={},
        )

        key = ("episode", "tt5555555", 1, 3)
        assert key in plex_state
        assert plex_state[key]["watched"] is True

        # The delta filter still applies when Trakt already agrees.
        plex_state_skipped = {}
        sync_engine._process_episode_batch(
            [episode],
            {"title": "Some Show"},
            "tt5555555",
            1,
            plex_state_skipped,
            since=run_end,
            known_state={
                key: {"watched": True, "last_watched_at": watched_at.isoformat()},
            },
        )
        assert plex_state_skipped == {}


class TestSyncWatchedStatusOrchestration:
    """Coverage for sync_watched_status branches (TODO audit D4)."""

    def test_trakt_is_pulled_before_plex(self, sync_engine):
        """The Plex pull needs the Trakt state to filter safely (#19)."""
        trakt_state = {("movie", "tt1111111", None): {"watched": True}}
        order = []
        sync_engine._pull_from_trakt = MagicMock(
            side_effect=lambda **kwargs: order.append("trakt") or trakt_state
        )
        sync_engine._pull_from_plex = MagicMock(
            side_effect=lambda **kwargs: order.append("plex") or {}
        )
        sync_engine._calculate_changes = MagicMock(
            return_value={
                "plex": {"mark_watched": [], "mark_unwatched": []},
                "trakt": {"mark_watched": [], "mark_unwatched": []},
            }
        )
        sync_engine._apply_changes = MagicMock()

        sync_engine.sync_watched_status()

        assert order == ["trakt", "plex"]
        assert sync_engine._pull_from_plex.call_args.kwargs["known_state"] == trakt_state

    def test_delta_mode(self, sync_engine):
        """History timestamp present -> delta mode is enabled."""
        sync_engine.history.update_last_sync_timestamp()
        sync_engine._pull_from_plex = MagicMock(return_value={})
        sync_engine._pull_from_trakt = MagicMock(return_value={})
        sync_engine._calculate_changes = MagicMock(
            return_value={
                "plex": {"mark_watched": [], "mark_unwatched": []},
                "trakt": {"mark_watched": [], "mark_unwatched": []},
            }
        )
        sync_engine._apply_changes = MagicMock()

        sync_engine.sync_watched_status()

        assert sync_engine.stats["delta_mode"] is True
        sync_engine._apply_changes.assert_called_once()

    def test_backfill_mode(self, sync_engine):
        """backfill_history=True forces a full sync (no delta)."""
        sync_engine.history.update_last_sync_timestamp()
        sync_engine._pull_from_plex = MagicMock(return_value={})
        sync_engine._pull_from_trakt = MagicMock(return_value={})
        sync_engine._calculate_changes = MagicMock(
            return_value={
                "plex": {"mark_watched": [], "mark_unwatched": []},
                "trakt": {"mark_watched": [], "mark_unwatched": []},
            }
        )
        sync_engine._apply_changes = MagicMock()

        sync_engine.sync_watched_status(backfill_history=True)

        assert sync_engine.stats["delta_mode"] is False

    def test_first_run_uses_window(self, sync_engine):
        """No history and no backfill -> first-run window is used."""
        sync_engine._pull_from_plex = MagicMock(return_value={})
        sync_engine._pull_from_trakt = MagicMock(return_value={})
        sync_engine._calculate_changes = MagicMock(
            return_value={
                "plex": {"mark_watched": [], "mark_unwatched": []},
                "trakt": {"mark_watched": [], "mark_unwatched": []},
            }
        )
        sync_engine._apply_changes = MagicMock()

        sync_engine.sync_watched_status()

        # First run: last_sync is the 7-day window
        last_sync = sync_engine.history.get_last_sync_timestamp()
        assert last_sync is not None
        # Called with a since window on the Plex pull
        args, kwargs = sync_engine._pull_from_plex.call_args
        assert kwargs.get("since") is not None

    def test_both_filters_default_movies(self, sync_engine):
        """movies_only + shows_only both set -> movies only wins."""
        sync_engine._pull_from_plex = MagicMock(return_value={})
        sync_engine._pull_from_trakt = MagicMock(return_value={})
        sync_engine._calculate_changes = MagicMock(
            return_value={
                "plex": {"mark_watched": [], "mark_unwatched": []},
                "trakt": {"mark_watched": [], "mark_unwatched": []},
            }
        )
        sync_engine._apply_changes = MagicMock()

        sync_engine.sync_watched_status(movies_only=True, shows_only=True)

        args, kwargs = sync_engine._pull_from_plex.call_args
        assert kwargs.get("movies_only") is True
        assert kwargs.get("shows_only") is False


class TestSyncPlaybackProgress:
    """Coverage for sync_playback_progress (TODO audit D4)."""

    def test_no_progress(self, sync_engine):
        """Empty Trakt progress -> early return."""
        sync_engine.trakt.get_playback_progress.return_value = {}

        stats = sync_engine.sync_playback_progress()

        assert stats["plex_progress_updated"] == 0
        sync_engine.plex.batch_set_playback_progress.assert_not_called()

    def test_movie_progress_updated(self, sync_engine):
        """Movie progress is pushed to Plex."""
        sync_engine.trakt.get_playback_progress.return_value = {
            ("movie", "tt123"): {"title": "Movie", "progress_percent": 50.0}
        }
        sync_engine.plex.cache.memory_cache = {
            "movies_by_imdb": {"tt123": {"ratingKey": 1, "duration": 600000}},
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
        }
        sync_engine.plex.cache.movie_imdb_to_rating_key = {"tt123": 1}
        sync_engine.plex.get_playback_progress.return_value = (0, 600000)
        sync_engine.plex.batch_set_playback_progress.return_value = {
            "success": 1,
            "failed": 0,
            "errors": [],
        }

        stats = sync_engine.sync_playback_progress()

        assert stats["progress_conflicts"] == 1
        assert stats["plex_progress_updated"] == 1
        updates = sync_engine.plex.batch_set_playback_progress.call_args[0][0]
        assert updates[0]["view_offset_ms"] == 300000

    def test_movie_progress_already_in_sync(self, sync_engine):
        """Small progress delta -> skipped."""
        sync_engine.trakt.get_playback_progress.return_value = {
            ("movie", "tt123"): {"title": "Movie", "progress_percent": 50.0}
        }
        sync_engine.plex.cache.memory_cache = {
            "movies_by_imdb": {"tt123": {"ratingKey": 1, "duration": 600000}},
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
        }
        sync_engine.plex.cache.movie_imdb_to_rating_key = {"tt123": 1}
        # Plex already at ~50% (within 30s threshold)
        sync_engine.plex.get_playback_progress.return_value = (300100, 600000)

        stats = sync_engine.sync_playback_progress()

        assert stats["skipped_already_in_sync"] == 1
        sync_engine.plex.batch_set_playback_progress.assert_not_called()

    def test_movie_progress_dry_run(self, sync_engine, caplog):
        """Dry run logs the would-be update without applying."""
        import logging

        sync_engine.trakt.get_playback_progress.return_value = {
            ("movie", "tt123"): {"title": "Movie", "progress_percent": 50.0}
        }
        sync_engine.plex.cache.memory_cache = {
            "movies_by_imdb": {"tt123": {"ratingKey": 1, "duration": 600000}},
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
        }
        sync_engine.plex.cache.movie_imdb_to_rating_key = {"tt123": 1}
        sync_engine.plex.get_playback_progress.return_value = (0, 600000)

        with caplog.at_level(logging.INFO):
            stats = sync_engine.sync_playback_progress(dry_run=True)

        assert stats["progress_conflicts"] == 1
        sync_engine.plex.batch_set_playback_progress.assert_not_called()
        assert "[DRY RUN]" in caplog.text

    def test_movie_not_in_plex_skipped(self, sync_engine):
        """Movie not found in Plex -> skipped."""
        sync_engine.trakt.get_playback_progress.return_value = {
            ("movie", "tt999"): {"title": "Missing", "progress_percent": 50.0}
        }
        sync_engine.plex.cache.memory_cache = {
            "movies_by_imdb": {},
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
        }
        sync_engine.plex.cache.movie_imdb_to_rating_key = {}

        stats = sync_engine.sync_playback_progress()

        assert stats["skipped_no_progress"] == 1
        sync_engine.plex.batch_set_playback_progress.assert_not_called()

    def test_episode_progress_skipped(self, sync_engine):
        """Episode progress is not yet implemented -> skipped."""
        sync_engine.trakt.get_playback_progress.return_value = {
            ("episode", "tt123", 1, 1): {"title": "Ep", "progress_percent": 50.0}
        }

        stats = sync_engine.sync_playback_progress()

        assert stats["skipped_no_progress"] == 1
        sync_engine.plex.batch_set_playback_progress.assert_not_called()

    def test_no_duration_skipped(self, sync_engine):
        """Movie without duration info -> skipped."""
        sync_engine.trakt.get_playback_progress.return_value = {
            ("movie", "tt123"): {"title": "Movie", "progress_percent": 50.0}
        }
        sync_engine.plex.cache.memory_cache = {
            "movies_by_imdb": {"tt123": {"ratingKey": 1, "duration": None}},
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
        }
        sync_engine.plex.cache.movie_imdb_to_rating_key = {"tt123": 1}
        sync_engine.plex.get_playback_progress.return_value = (None, None)

        stats = sync_engine.sync_playback_progress()

        assert stats["skipped_no_progress"] == 1


class TestApplyChangesFailurePaths:
    """Coverage for _apply_changes error paths (TODO audit D4)."""

    def test_trakt_add_history_failure(self, sync_engine):
        """Trakt add_to_history exception is counted as errors."""
        changes = {
            "plex": {"mark_watched": [], "mark_unwatched": []},
            "trakt": {
                "mark_watched": [
                    {
                        "key": ("movie", "tt123", None),
                        "media_type": "movie",
                        "imdb_id": "tt123",
                        "tmdb_id": None,
                        "title": "Movie",
                        "rating_key": 1,
                    }
                ],
                "mark_unwatched": [],
            },
        }
        sync_engine.trakt.add_to_history.side_effect = Exception("boom")

        sync_engine._apply_changes(changes)

        assert sync_engine.stats["errors"] == 1

    def test_trakt_remove_history_failure(self, sync_engine):
        """Trakt remove_from_history exception is counted as errors."""
        changes = {
            "plex": {"mark_watched": [], "mark_unwatched": []},
            "trakt": {
                "mark_watched": [],
                "mark_unwatched": [
                    {
                        "key": ("movie", "tt123", None),
                        "media_type": "movie",
                        "imdb_id": "tt123",
                        "tmdb_id": None,
                        "title": "Movie",
                    }
                ],
            },
        }
        sync_engine.trakt.remove_from_history.side_effect = Exception("boom")

        sync_engine._apply_changes(changes)

        assert sync_engine.stats["errors"] == 1


class TestPullFailurePropagation:
    """Regression tests for GitHub issue #1.

    Pull helpers must never silently degrade to an empty state dict: a
    transient API failure used to produce trakt_state={}, which the resolver
    interpreted as "everything unwatched on Trakt" and pushed a full-library
    history rewrite. Failures must now propagate as WatchStatePullError so
    sync_watched_status() aborts before applying changes or advancing
    last_sync_timestamp.
    """

    def _plex_state_with_watched_movie(self):
        """A Plex state that would trigger push_to_trakt if Trakt pull failed."""
        return {
            ("movie", "tt0111161", "278"): {
                "watched": True,
                "last_watched_at": datetime.now(timezone.utc),
                "rating_key": 100,
                "title": "The Shawshank Redemption",
            }
        }

    def test_trakt_request_failure_aborts_sync(self, sync_engine):
        """A raised exception from get_watched_movies() aborts before apply."""
        sync_engine._pull_from_plex = MagicMock(return_value=self._plex_state_with_watched_movie())
        sync_engine.trakt.get_watched_movies = MagicMock(
            side_effect=requests.exceptions.ConnectionError("transient outage")
        )
        sync_engine._apply_changes = MagicMock()

        with pytest.raises(watch_sync.WatchStatePullError):
            sync_engine.sync_watched_status()

        # No changes applied to either platform
        sync_engine._apply_changes.assert_not_called()
        # Failure is visible in stats
        assert sync_engine.stats["errors"] > 0
        # Delta timestamp not advanced by the failed run
        assert sync_engine.history.get_last_sync_timestamp() is None

    def test_trakt_shows_request_failure_aborts_sync(self, sync_engine):
        """A raised exception from get_watched_shows() aborts before apply."""
        sync_engine._pull_from_plex = MagicMock(return_value={})
        sync_engine.trakt.get_watched_shows = MagicMock(
            side_effect=requests.exceptions.Timeout("timeout")
        )
        sync_engine._apply_changes = MagicMock()

        with pytest.raises(watch_sync.WatchStatePullError):
            sync_engine.sync_watched_status()

        sync_engine._apply_changes.assert_not_called()
        assert sync_engine.stats["errors"] > 0
        assert sync_engine.history.get_last_sync_timestamp() is None

    def test_plex_pull_failure_aborts_sync(self, sync_engine):
        """An unexpected Plex failure propagates instead of degrading to {}."""
        sync_engine._pull_from_trakt = MagicMock(return_value={})
        sync_engine._apply_changes = MagicMock()

        # Break cache access - the first operation inside _pull_from_plex's try block
        sync_engine.plex.cache.memory_cache.get.side_effect = RuntimeError("plex unavailable")
        with pytest.raises(watch_sync.WatchStatePullError):
            sync_engine.sync_watched_status()

        # Pull order is Trakt then Plex (#19), so the Trakt pull has already
        # run; the Plex failure still aborts before any change is applied.
        sync_engine._pull_from_trakt.assert_called_once()
        sync_engine._apply_changes.assert_not_called()
        assert sync_engine.stats["errors"] > 0
        assert sync_engine.history.get_last_sync_timestamp() is None

    def test_failed_run_does_not_advance_existing_timestamp(self, sync_engine):
        """A previously stored timestamp survives a failed sync unchanged."""
        original_ts = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
        sync_engine.history.state["last_sync_timestamp"] = original_ts.isoformat()
        sync_engine.history.save_state()

        sync_engine._pull_from_plex = MagicMock(return_value=self._plex_state_with_watched_movie())
        sync_engine.trakt.get_watched_movies = MagicMock(
            side_effect=requests.exceptions.ConnectionError("outage")
        )

        with pytest.raises(watch_sync.WatchStatePullError):
            sync_engine.sync_watched_status()

        assert sync_engine.history.get_last_sync_timestamp() == original_ts

    def test_pull_from_trakt_wraps_request_exception(self, sync_engine):
        """_pull_from_trakt raises WatchStatePullError on RequestException."""
        sync_engine.trakt.get_watched_movies = MagicMock(
            side_effect=requests.exceptions.RequestException("boom")
        )
        with pytest.raises(watch_sync.WatchStatePullError):
            sync_engine._pull_from_trakt(movies_only=True)

    def test_pull_from_trakt_wraps_parsing_error(self, sync_engine):
        """Malformed Trakt payloads also raise WatchStatePullError."""
        # Force a KeyError inside the loop by returning a non-dict movie entry
        sync_engine.trakt.get_watched_movies = MagicMock(return_value=[{"movie": ["not-a-dict"]}])
        with pytest.raises(watch_sync.WatchStatePullError):
            sync_engine._pull_from_trakt(movies_only=True)

    def test_pull_from_plex_wraps_unexpected_error(self, sync_engine):
        """_pull_from_plex raises WatchStatePullError on unexpected failure."""
        sync_engine.plex.cache.memory_cache.get.side_effect = RuntimeError("cache exploded")
        with pytest.raises(watch_sync.WatchStatePullError):
            sync_engine._pull_from_plex()

    def test_successful_pull_still_updates_timestamp(self, sync_engine):
        """Happy path is unaffected: successful pulls still advance the stamp."""
        sync_engine._pull_from_plex = MagicMock(return_value={})
        sync_engine._pull_from_trakt = MagicMock(return_value={})
        sync_engine._calculate_changes = MagicMock(
            return_value={
                "plex": {"mark_watched": [], "mark_unwatched": []},
                "trakt": {"mark_watched": [], "mark_unwatched": []},
            }
        )
        sync_engine._apply_changes = MagicMock()

        sync_engine.sync_watched_status()

        assert sync_engine.history.get_last_sync_timestamp() is not None


class TestHistoryBatchPersistence:
    """Regression tests for issue #7: the apply phase must persist history
    state once (not once per item) and rating-key lookups must use an index
    instead of scanning the synced_items list."""

    @staticmethod
    def _make_changes(n_movies):
        """Build a changes dict with n_movies Plex mark_watched entries."""
        items = [
            {
                "key": ("movie", f"tt{i}", None),
                "media_type": "movie",
                "imdb_id": f"tt{i}",
                "tmdb_id": None,
                "rating_key": 1000 + i,
                "title": f"Movie {i}",
            }
            for i in range(n_movies)
        ]
        return {
            "plex": {"mark_watched": items, "mark_unwatched": []},
            "trakt": {"mark_watched": [], "mark_unwatched": []},
        }

    def test_apply_changes_persists_history_once(self, sync_engine):
        """N history updates produce exactly one save_state() write."""
        n = 25
        changes = self._make_changes(n)

        save_calls = []
        original_save = sync_engine.history.save_state

        def counting_save():
            save_calls.append(1)
            original_save()

        sync_engine.history.save_state = counting_save

        sync_engine._apply_changes(changes)

        assert len(sync_engine.history.state["synced_items"]) == n
        assert (
            len(save_calls) == 1
        ), f"Expected one save_state() per apply phase, got {len(save_calls)}"

    def test_apply_changes_persists_once_even_on_error(self, sync_engine):
        """A mid-batch failure still persists accumulated state exactly once."""
        changes = self._make_changes(5)

        calls = {"save": 0}
        original_save = sync_engine.history.save_state

        def failing_update(**kwargs):
            raise RuntimeError("boom mid-batch")

        original_add = sync_engine.history.add_or_update_synced_item

        def add_then_fail(**kwargs):
            if kwargs.get("plex_rating_key") == 1003:
                raise RuntimeError("boom")
            return original_add(**kwargs)

        def counting_save():
            calls["save"] += 1
            original_save()

        sync_engine.history.add_or_update_synced_item = add_then_fail
        sync_engine.history.save_state = counting_save

        with pytest.raises(RuntimeError, match="boom"):
            sync_engine._apply_changes(changes)

        # Items processed before the failure are preserved by the single write
        assert len(sync_engine.history.state["synced_items"]) == 3
        assert calls["save"] == 1

    def test_rating_key_lookup_uses_index_not_scan(self, mock_history_manager):
        """get_synced_item(plex_rating_key=...) resolves via the index without
        iterating synced_items."""
        manager = mock_history_manager
        for i in range(50):
            manager.add_or_update_synced_item(
                media_type="episode",
                imdb_id="tt_show",
                plex_rating_key=i,
            )

        class CountingList(list):
            iterations = 0

            def __iter__(self):
                CountingList.iterations += 1
                return super().__iter__()

        manager.state["synced_items"] = CountingList(manager.state["synced_items"])

        CountingList.iterations = 0
        item = manager.get_synced_item(plex_rating_key=42)

        assert item is not None
        assert item["plex_rating_key"] == 42
        assert CountingList.iterations == 0, "Index hit must not iterate synced_items"

    def test_index_maintained_across_remove_and_reload(self, tmp_path, monkeypatch):
        """Index stays consistent after remove and after a reload from disk."""
        from traktor import history_manager

        state_file = tmp_path / ".traktor_watch_sync.json"
        monkeypatch.setattr(history_manager, "WATCH_SYNC_FILE", state_file)

        manager = history_manager.WatchHistoryManager(plex_server_id="srv")
        manager.add_or_update_synced_item(media_type="movie", imdb_id="tt1", plex_rating_key="rk-1")
        manager.add_or_update_synced_item(media_type="movie", imdb_id="tt2", plex_rating_key="rk-2")

        # No explicit save happened in add; explicit batch-style persist here
        manager.save_state()

        assert manager.remove_synced_item(plex_rating_key="rk-1") is True
        assert manager._item_index.get("rk-1") is None

        reloaded = history_manager.WatchHistoryManager(plex_server_id="srv")
        assert reloaded.get_synced_item(plex_rating_key="rk-2") is not None
        assert reloaded.get_synced_item(plex_rating_key="rk-1") is None


class TestBatchFailureSurfacing:
    """Regression tests for issue #9: per-batch failures must be counted as
    errors and failed items must not be recorded as synced."""

    def test_trakt_add_batch_failures_count_as_errors(self, sync_engine):
        """Partial Trakt add failures surface in stats['errors']."""
        sync_engine.trakt.add_to_history = MagicMock(
            return_value={
                "added": {"movies": 2, "episodes": 0},
                "failed": {"movies": 3, "episodes": 0},
            }
        )
        changes = {
            "plex": {"mark_watched": [], "mark_unwatched": []},
            "trakt": {
                "mark_watched": [
                    {
                        "key": ("movie", f"tt{i}", None),
                        "media_type": "movie",
                        "imdb_id": f"tt{i}",
                        "tmdb_id": None,
                        "title": f"M{i}",
                    }
                    for i in range(5)
                ],
                "mark_unwatched": [],
            },
        }

        sync_engine._apply_changes(changes)

        assert sync_engine.stats["errors"] == 3
        assert sync_engine.stats["trakt_watched"] == 2

    def test_trakt_remove_batch_failures_count_as_errors(self, sync_engine):
        """Partial Trakt remove failures surface in stats['errors']."""
        sync_engine.trakt.remove_from_history = MagicMock(
            return_value={
                "deleted": {"movies": 0, "episodes": 1},
                "failed": {"movies": 0, "episodes": 2},
            }
        )
        changes = {
            "plex": {"mark_watched": [], "mark_unwatched": []},
            "trakt": {
                "mark_watched": [],
                "mark_unwatched": [
                    {
                        "key": ("episode", "tt_show", 1, i),
                        "media_type": "episode",
                        "imdb_id": "tt_show",
                        "tmdb_id": None,
                        "title": f"E{i}",
                    }
                    for i in range(1, 4)
                ],
            },
        }

        sync_engine._apply_changes(changes)

        assert sync_engine.stats["errors"] == 2
        assert sync_engine.stats["trakt_unwatched"] == 1

    def test_failed_plex_items_not_recorded_synced(self, sync_engine):
        """Items whose Plex mark operation failed are not written to history state."""
        changes = {
            "plex": {
                "mark_watched": [
                    {
                        "key": ("movie", "tt_ok", None),
                        "media_type": "movie",
                        "imdb_id": "tt_ok",
                        "tmdb_id": None,
                        "rating_key": 1,
                        "title": "OK",
                    },
                    {
                        "key": ("movie", "tt_bad", None),
                        "media_type": "movie",
                        "imdb_id": "tt_bad",
                        "tmdb_id": None,
                        "rating_key": 2,
                        "title": "BAD",
                    },
                ],
                "mark_unwatched": [],
            },
            "trakt": {"mark_watched": [], "mark_unwatched": []},
        }
        sync_engine.plex.batch_mark_as_watched = MagicMock(
            return_value={
                "success": 1,
                "failed": 1,
                "errors": ["Error marking 2 as watched: boom"],
                "failed_rating_keys": [2],
            }
        )

        sync_engine._apply_changes(changes)

        recorded_keys = [
            item.get("plex_rating_key") for item in sync_engine.history.state["synced_items"]
        ]
        assert recorded_keys == [
            1
        ], "Failed item must not be remembered as synced; only the successful item is"

    def test_all_plex_items_succeed_all_recorded(self, sync_engine):
        """Happy path unchanged: every item lands in history state (issue #7 behavior)."""
        changes = {
            "plex": {
                "mark_watched": [
                    {
                        "key": ("movie", f"tt{i}", None),
                        "media_type": "movie",
                        "imdb_id": f"tt{i}",
                        "tmdb_id": None,
                        "rating_key": 100 + i,
                        "title": f"M{i}",
                    }
                    for i in range(3)
                ],
                "mark_unwatched": [],
            },
            "trakt": {"mark_watched": [], "mark_unwatched": []},
        }
        sync_engine.plex.batch_mark_as_watched = MagicMock(
            return_value={"success": 3, "failed": 0, "errors": [], "failed_rating_keys": []}
        )

        sync_engine._apply_changes(changes)

        assert len(sync_engine.history.state["synced_items"]) == 3
        assert sync_engine.stats["errors"] == 0


class TestWatchDatePropagation:
    """Plex->Trakt pushes must carry the Plex watch date (issue #37).

    Trakt stamps a play with the moment of the request when ``watched_at`` is
    omitted. Dropping the date turned each sync into a fabricated watch date
    plus a duplicate play, and because the fabricated date is newer than Plex's
    the resolver kept re-pushing the same items on every run.
    """

    def test_movie_push_carries_watched_at(self, sync_engine):
        """The movie payload keeps the Plex lastViewedAt value."""
        changes = {
            "plex": {"mark_watched": [], "mark_unwatched": []},
            "trakt": {
                "mark_watched": [
                    {
                        "key": ("movie", "tt1234567", "12345"),
                        "imdb_id": "tt1234567",
                        "tmdb_id": "12345",
                        "media_type": "movie",
                        "title": "Test Movie",
                        "watched_at": "2023-02-06T21:30:39.000Z",
                    }
                ],
                "mark_unwatched": [],
            },
        }

        sync_engine._apply_changes(changes)

        payload = sync_engine.trakt.add_to_history.call_args.kwargs["movies"][0]
        assert payload["watched_at"] == "2023-02-06T21:30:39.000Z"
        assert payload["ids"] == {"imdb": "tt1234567", "tmdb": "12345"}

    def test_movie_push_converts_datetime_watched_at(self, sync_engine):
        """A datetime watch date is rendered as Trakt's ISO-8601 UTC form."""
        watched_at = datetime(2026, 10, 26, 18, 30, 34, tzinfo=timezone.utc)
        changes = {
            "plex": {"mark_watched": [], "mark_unwatched": []},
            "trakt": {
                "mark_watched": [
                    {
                        "key": ("movie", "tt1234567", None),
                        "imdb_id": "tt1234567",
                        "tmdb_id": None,
                        "media_type": "movie",
                        "title": "Test Movie",
                        "watched_at": watched_at,
                    }
                ],
                "mark_unwatched": [],
            },
        }

        sync_engine._apply_changes(changes)

        payload = sync_engine.trakt.add_to_history.call_args.kwargs["movies"][0]
        assert payload["watched_at"] == "2026-10-26T18:30:34.000Z"

    def test_episode_push_carries_watched_at(self, sync_engine):
        """The episode payload keeps the Plex lastViewedAt value too."""
        changes = {
            "plex": {"mark_watched": [], "mark_unwatched": []},
            "trakt": {
                "mark_watched": [
                    {
                        "key": ("episode", "tt1234567", 2, 5),
                        "imdb_id": "tt1234567",
                        "tmdb_id": None,
                        "media_type": "episode",
                        "title": "Test Show S02E05",
                        "watched_at": "2024-01-02T20:00:00.000Z",
                    }
                ],
                "mark_unwatched": [],
            },
        }

        sync_engine._apply_changes(changes)

        payload = sync_engine.trakt.add_to_history.call_args.kwargs["episodes"][0]
        assert payload == {
            "ids": {"imdb": "tt1234567"},
            "season": 2,
            "number": 5,
            "watched_at": "2024-01-02T20:00:00.000Z",
        }

    def test_push_without_watch_date_omits_field(self, sync_engine):
        """No watch date means no watched_at key (Trakt falls back to now)."""
        changes = {
            "plex": {"mark_watched": [], "mark_unwatched": []},
            "trakt": {
                "mark_watched": [
                    {
                        "key": ("movie", "tt1234567", None),
                        "imdb_id": "tt1234567",
                        "tmdb_id": None,
                        "media_type": "movie",
                        "title": "Test Movie",
                        "watched_at": None,
                    }
                ],
                "mark_unwatched": [],
            },
        }

        sync_engine._apply_changes(changes)

        payload = sync_engine.trakt.add_to_history.call_args.kwargs["movies"][0]
        assert "watched_at" not in payload


class TestWatchSyncIdempotency:
    """A sync must produce no changes once both platforms agree (issue #37)."""

    def test_both_watched_with_divergent_timestamps_yields_no_changes(self, sync_engine):
        """Watched on both sides is not a change, whatever the dates say."""
        plex_state = {
            ("movie", "tt0071562", "240"): {
                "watched": True,
                "last_watched_at": "2024-04-20T20:54:27",
                "rating_key": "12345",
                "title": "The Godfather Part II",
            }
        }
        trakt_state = {
            ("movie", "tt0071562", "240"): {
                "watched": True,
                "last_watched_at": "2026-11-19T06:48:00.000Z",
                "title": "The Godfather Part II",
                "plays": 46,
            }
        }

        changes = sync_engine._calculate_changes(plex_state, trakt_state, "both")

        assert changes["plex"]["mark_watched"] == []
        assert changes["trakt"]["mark_watched"] == []

    def test_repeated_calculation_produces_no_new_changes(self, sync_engine):
        """Resolving the same agreed state twice stays quiet (no ping-pong)."""
        plex_state = {
            ("episode", "tt1234567", 1, 1): {
                "watched": True,
                "last_watched_at": "2025-01-01T10:00:00",
                "rating_key": "999",
                "title": "Test Show S01E01",
            }
        }
        trakt_state = {
            ("episode", "tt1234567", 1, 1): {
                "watched": True,
                "last_watched_at": "2026-11-19T06:48:00.000Z",
                "title": "Test Show S01E01",
            }
        }

        for _ in range(3):
            changes = sync_engine._calculate_changes(plex_state, trakt_state, "both")
            assert changes["plex"]["mark_watched"] == []
            assert changes["trakt"]["mark_watched"] == []


class _FakeEpisodeStore:
    """Stand-in for CacheManager's episode snapshot API, request-free."""

    def __init__(self, snapshots=None, memory_cache=None):
        self.snapshots = dict(snapshots or {})
        self.memory_cache = memory_cache if memory_cache is not None else {}
        self.flushes = 0
        self.forgotten = []

    def get_show_episodes(self, rating_key):
        return self.snapshots.get(str(rating_key))

    def record_show_episodes(self, rating_key, counters, episodes):
        self.snapshots[str(rating_key)] = {
            "viewedLeafCount": counters.get("viewedLeafCount"),
            "leafCount": counters.get("leafCount"),
            "lastViewedAt": counters.get("lastViewedAt"),
            "episodes": episodes,
        }

    def forget_show_episodes(self, rating_key):
        self.forgotten.append(str(rating_key))
        self.snapshots.pop(str(rating_key), None)

    def flush_show_episodes(self):
        self.flushes += 1


def _make_show(rating_key, watched_count, leaf_count, last_viewed=None, episodes=()):
    """Stub Plex show that counts how often its seasons are read."""
    show = MagicMock()
    show.ratingKey = rating_key
    show.viewedLeafCount = watched_count
    show.leafCount = leaf_count
    show.lastViewedAt = last_viewed
    seasons = []
    for season_num, season_episodes in episodes:
        season = MagicMock()
        season.seasonNumber = season_num
        season.episodes.return_value = list(season_episodes)
        seasons.append(season)
    show.seasons.return_value = seasons
    return show


def _make_episode(rating_key, number, watched, last_viewed=None, view_count=None):
    episode = MagicMock()
    episode.ratingKey = rating_key
    episode.episodeNumber = number
    episode.isWatched = watched
    episode.lastViewedAt = last_viewed
    episode.viewCount = (1 if watched else 0) if view_count is None else view_count
    return episode


def _wiki_show(rating_key=700, watched=2, leaf=3, last_viewed="2024-05-01T20:00:00"):
    """A show with three episodes: two watched, one not."""
    episodes = {
        1: [
            _make_episode(rating_key * 10 + 1, 1, True, last_viewed),
            _make_episode(rating_key * 10 + 2, 2, True, last_viewed),
            _make_episode(rating_key * 10 + 3, 3, False, None),
        ]
    }
    return _make_show(rating_key, watched, leaf, last_viewed, episodes.items())


class TestDeltaEpisodeReuse:
    """A delta pull reuses cached episode state instead of re-reading Plex (#21).

    The slowest part of a run was one season request per show and one episode
    request per season, for the whole library, on every delta run. A show's own
    watch counters decide whether its episode rows are still accurate.
    """

    @pytest.fixture(autouse=True)
    def _real_show_counters(self, sync_engine):
        """Use the real counter reader, not an auto-created MagicMock."""
        sync_engine.plex.show_counters.side_effect = PlexClient.show_counters

    def _cache(self, shows, imdb_by_key):
        return (
            {
                "movies_by_imdb": {},
                "movies_by_tmdb": {},
                "shows_by_imdb": {},
                "movies_list": [],
                "shows_list": [{"ratingKey": rk, "title": f"Show {rk}"} for rk in shows],
                "show_episodes": {},
            },
            {},
            imdb_by_key,
        )

    def test_unchanged_shows_issue_no_season_or_episode_requests(self, sync_engine):
        """The DoD: 50 unchanged shows, zero season requests, same state."""
        shows = {1000 + i: _wiki_show(1000 + i) for i in range(50)}
        imdb_by_key = {rk: f"tt{rk}" for rk in shows}
        cache, mov_ids, show_ids = self._cache(shows, imdb_by_key)

        # Full pull first: populates snapshots and gives the reference state
        store = _FakeEpisodeStore(memory_cache=cache)
        sync_engine.plex.cache = store
        sync_engine.plex.cache.movie_key_to_ids = mov_ids
        sync_engine.plex.cache.show_key_to_imdb = show_ids
        sync_engine.plex._get_plex_item.side_effect = lambda rk: shows[rk]
        sync_engine.plex.get_show_counters.side_effect = lambda: {
            rk: PlexClient.show_counters(s) for rk, s in shows.items()
        }

        full_state = sync_engine._pull_from_plex(shows_only=True)
        assert len(full_state) == 150  # every episode is represented
        assert len(store.snapshots) == 50  # and every show is snapshotted

        # Now a delta pull: no show may be read from Plex at all
        store.flushes = 0
        season_reads = {rk: s.seasons.call_count for rk, s in shows.items()}
        sync_engine.plex._get_plex_item.side_effect = AssertionError(
            "delta pull must not fetch shows whose counters are unchanged"
        )
        sync_engine.stats["items_skipped_due_to_delta"] = 0

        delta_state = sync_engine._pull_from_plex(shows_only=True, since=datetime.now(timezone.utc))

        assert delta_state == full_state
        assert {rk: s.seasons.call_count for rk, s in shows.items()} == season_reads
        assert sync_engine.stats["shows_reused_from_snapshot"] == 50
        assert store.flushes == 1

    def test_changed_show_is_read_again(self, sync_engine):
        """A show whose counters moved is re-read; the others are not."""
        unchanged = _wiki_show(2000)
        changed = _wiki_show(2001, watched=3, leaf=3)
        shows = {2000: unchanged, 2001: changed}
        cache, mov_ids, show_ids = self._cache(shows, {2000: "tt2000", 2001: "tt2001"})
        store = _FakeEpisodeStore(
            memory_cache=cache,
            snapshots={
                # 2000: counters still describe the cached rows
                "2000": {
                    "viewedLeafCount": 2,
                    "leafCount": 3,
                    "lastViewedAt": "2024-05-01T20:00:00+00:00",
                    "episodes": [[1, 1, 20001, 1, "2024-05-01T20:00:00+00:00"]],
                },
                # 2001: snapshot says 2 watched, Plex now says 3 -> changed
                "2001": {
                    "viewedLeafCount": 2,
                    "leafCount": 3,
                    "lastViewedAt": "2024-05-01T20:00:00+00:00",
                    "episodes": [[1, 1, 20011, 1, "2024-05-01T20:00:00+00:00"]],
                },
            },
        )
        sync_engine.plex.cache = store
        sync_engine.plex.cache.movie_key_to_ids = mov_ids
        sync_engine.plex.cache.show_key_to_imdb = show_ids
        sync_engine.plex._get_plex_item.side_effect = lambda rk: shows[rk]
        sync_engine.plex.get_show_counters.side_effect = lambda: {
            2000: {
                "viewedLeafCount": 2,
                "leafCount": 3,
                "lastViewedAt": "2024-05-01T20:00:00+00:00",
            },
            2001: PlexClient.show_counters(changed),
        }

        state = sync_engine._pull_from_plex(shows_only=True, since=datetime.now(timezone.utc))

        assert unchanged.seasons.call_count == 0
        assert changed.seasons.call_count == 1
        # The changed show's fresh rows replace its stale snapshot
        assert len(store.snapshots["2001"]["episodes"]) == 3
        assert state[("episode", "tt2001", 1, 3)]["watched"] is False

    def test_show_without_snapshot_is_read(self, sync_engine):
        """No snapshot means no evidence, so the show is read."""
        show = _wiki_show(3000)
        cache, mov_ids, show_ids = self._cache({3000: show}, {3000: "tt3000"})
        store = _FakeEpisodeStore(memory_cache=cache)
        sync_engine.plex.cache = store
        sync_engine.plex.cache.movie_key_to_ids = mov_ids
        sync_engine.plex.cache.show_key_to_imdb = show_ids
        sync_engine.plex._get_plex_item.side_effect = lambda rk: show
        sync_engine.plex.get_show_counters.side_effect = lambda: {
            3000: PlexClient.show_counters(show)
        }

        sync_engine._pull_from_plex(shows_only=True, since=datetime.now(timezone.utc))

        assert show.seasons.call_count == 1

    def test_unreadable_counters_force_a_read(self, sync_engine):
        """A show whose counters Plex did not report is read, never reused."""
        show = _wiki_show(4000)
        cache, mov_ids, show_ids = self._cache({4000: show}, {4000: "tt4000"})
        store = _FakeEpisodeStore(
            memory_cache=cache,
            snapshots={
                "4000": {
                    "viewedLeafCount": 2,
                    "leafCount": 3,
                    "lastViewedAt": "2024-05-01T20:00:00+00:00",
                    "episodes": [[1, 1, 40001, 1, "2024-05-01T20:00:00+00:00"]],
                }
            },
        )
        sync_engine.plex.cache = store
        sync_engine.plex.cache.movie_key_to_ids = mov_ids
        sync_engine.plex.cache.show_key_to_imdb = show_ids
        sync_engine.plex._get_plex_item.side_effect = lambda rk: show
        sync_engine.plex.get_show_counters.side_effect = lambda: {}

        sync_engine._pull_from_plex(shows_only=True, since=datetime.now(timezone.utc))

        assert show.seasons.call_count == 1

    def test_backfill_reads_every_show(self, sync_engine):
        """A full sync (since=None, as --backfill-history) ignores snapshots."""
        show = _wiki_show(5000)
        cache, mov_ids, show_ids = self._cache({5000: show}, {5000: "tt5000"})
        store = _FakeEpisodeStore(
            memory_cache=cache,
            snapshots={
                "5000": {
                    "viewedLeafCount": 2,
                    "leafCount": 3,
                    "lastViewedAt": "2024-05-01T20:00:00+00:00",
                    "episodes": [[1, 1, 50001, 1, "2024-05-01T20:00:00+00:00"]],
                }
            },
        )
        sync_engine.plex.cache = store
        sync_engine.plex.cache.movie_key_to_ids = mov_ids
        sync_engine.plex.cache.show_key_to_imdb = show_ids
        sync_engine.plex._get_plex_item.side_effect = lambda rk: show
        sync_engine.plex.get_show_counters.side_effect = lambda: {}

        state = sync_engine._pull_from_plex(shows_only=True)

        assert show.seasons.call_count == 1
        assert len(state) == 3

    def test_failed_show_read_drops_its_snapshot(self, sync_engine):
        """A failed read must not leave a snapshot that hides future changes."""
        show = _wiki_show(6000)
        cache, mov_ids, show_ids = self._cache({6000: show}, {6000: "tt6000"})
        store = _FakeEpisodeStore(
            memory_cache=cache,
            snapshots={
                "6000": {
                    "viewedLeafCount": 1,
                    "leafCount": 3,
                    "lastViewedAt": "2024-05-01T20:00:00+00:00",
                    "episodes": [[1, 1, 60001, 1, "2024-05-01T20:00:00+00:00"]],
                }
            },
        )
        sync_engine.plex.cache = store
        sync_engine.plex.cache.movie_key_to_ids = mov_ids
        sync_engine.plex.cache.show_key_to_imdb = show_ids
        sync_engine.plex._get_plex_item.side_effect = requests.exceptions.ReadTimeout("slow")
        sync_engine.plex.get_show_counters.side_effect = lambda: {
            6000: PlexClient.show_counters(show)
        }

        with pytest.raises(watch_sync.WatchStatePullError) as excinfo:
            sync_engine._pull_from_plex(shows_only=True, since=datetime.now(timezone.utc))

        assert "6000" in store.forgotten
        assert "6000" not in store.snapshots
        assert store.flushes == 1
        assert "Show 6000" in str(excinfo.value)

    def test_replay_matches_a_live_read_for_inconsistent_counters(self, sync_engine):
        """A replay must reproduce the live state even if Plex reports both.

        The row stores the watched flag Plex reported, not the play count, so a
        server that reports ``viewCount == 0`` next to ``isWatched == True``
        cannot make a replayed run disagree with a live one.
        """
        episode = _make_episode(90001, 1, True, "2024-05-01T20:00:00", view_count=0)
        show = _make_show(9000, 1, 1, "2024-05-01T20:00:00", [(1, [episode])])
        cache, mov_ids, show_ids = self._cache({9000: show}, {9000: "tt9000"})
        store = _FakeEpisodeStore(memory_cache=cache)
        sync_engine.plex.cache = store
        sync_engine.plex.cache.movie_key_to_ids = mov_ids
        sync_engine.plex.cache.show_key_to_imdb = show_ids
        sync_engine.plex._get_plex_item.side_effect = lambda rk: show
        sync_engine.plex.get_show_counters.side_effect = lambda: {
            9000: PlexClient.show_counters(show)
        }

        live = sync_engine._pull_from_plex(shows_only=True)

        sync_engine.plex._get_plex_item.side_effect = AssertionError("must be replayed")
        replayed = sync_engine._pull_from_plex(shows_only=True, since=datetime.now(timezone.utc))

        assert replayed == live
        assert replayed[("episode", "tt9000", 1, 1)]["watched"] is True


class TestUnwatchedShowsAreNotRead:
    """A show with no watched episode on either side is not read (#39).

    A full pull used to read the seasons and episodes of every show in the
    library, so a first run, a cache reset and a backfill spent most of their
    time discovering that a show has nothing to report: 4,369 of the owner's
    4,596 shows. The show's own counters and the Trakt state pulled moments
    earlier settle it without a request.
    """

    @pytest.fixture(autouse=True)
    def _real_show_counters(self, sync_engine):
        """Use the real counter reader, not an auto-created MagicMock."""
        sync_engine.plex.show_counters.side_effect = PlexClient.show_counters

    def _library(self, sync_engine, shows):
        cache = {
            "movies_by_imdb": {},
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
            "movies_list": [],
            "shows_list": [{"ratingKey": rk, "title": f"Show {rk}"} for rk in shows],
            "show_episodes": {},
        }
        store = _FakeEpisodeStore(memory_cache=cache)
        sync_engine.plex.cache = store
        sync_engine.plex.cache.movie_key_to_ids = {}
        sync_engine.plex.cache.show_key_to_imdb = {rk: f"tt{rk}" for rk in shows}
        sync_engine.plex._get_plex_item.side_effect = lambda rk: shows[rk]
        sync_engine.plex.get_show_counters.side_effect = lambda: {
            rk: PlexClient.show_counters(show) for rk, show in shows.items()
        }
        return store

    @staticmethod
    def _unwatched_show(rating_key):
        """A show Plex reports as entirely unwatched, with three episodes."""
        episodes = [_make_episode(rating_key * 10 + n, n, False) for n in (1, 2, 3)]
        return _make_show(rating_key, 0, 3, None, [(1, episodes)])

    def test_full_pull_reads_only_the_shows_with_watch_state(self, sync_engine):
        """The DoD: 50 shows, 2 watched, and none of the other 48 read."""
        watched = {1000: _wiki_show(1000), 1001: _wiki_show(1001)}
        unwatched = {2000 + i: self._unwatched_show(2000 + i) for i in range(48)}
        self._library(sync_engine, {**watched, **unwatched})

        state = sync_engine._pull_from_plex(shows_only=True, since=None, known_state={})

        assert {rk: show.seasons.call_count for rk, show in watched.items()} == {
            1000: 1,
            1001: 1,
        }
        assert {show.seasons.call_count for show in unwatched.values()} == {0}
        # Only the watched shows contribute state: three episodes each.
        assert len(state) == 6
        assert sync_engine.stats["shows_skipped_no_watch_state"] == 48

    def test_trakt_watched_episode_still_reads_the_show(self, sync_engine):
        """A show watched only in Trakt is read, so its rating keys can be pushed."""
        show = self._unwatched_show(3000)
        self._library(sync_engine, {3000: show})
        known_state = {
            ("episode", "tt3000", 1, 1): {
                "watched": True,
                "last_watched_at": "2024-05-01T20:00:00.000Z",
            }
        }

        state = sync_engine._pull_from_plex(shows_only=True, since=None, known_state=known_state)

        assert show.seasons.call_count == 1
        assert sync_engine.stats["shows_skipped_no_watch_state"] == 0
        # The push to Plex needs the episode's Plex rating key, which only a
        # read can supply.
        assert state[("episode", "tt3000", 1, 1)]["rating_key"] == 30001

    def test_missing_counters_read_the_show(self, sync_engine):
        """Counters Plex did not report are no evidence, so the show is read."""
        show = self._unwatched_show(4000)
        self._library(sync_engine, {4000: show})
        sync_engine.plex.get_show_counters.side_effect = lambda: {}

        sync_engine._pull_from_plex(shows_only=True, since=None, known_state={})

        assert show.seasons.call_count == 1
        assert sync_engine.stats["shows_skipped_no_watch_state"] == 0

    def test_unknown_trakt_state_reads_the_show(self, sync_engine):
        """Without the Trakt pull the skip cannot be proven, so it is not made."""
        show = self._unwatched_show(4100)
        self._library(sync_engine, {4100: show})

        sync_engine._pull_from_plex(shows_only=True)

        assert show.seasons.call_count == 1
        assert sync_engine.stats["shows_skipped_no_watch_state"] == 0

    def test_delta_pull_after_cleared_snapshots_skips_unwatched_shows(self, sync_engine):
        """A delta run with no snapshots still skips the shows with nothing to say."""
        watched = _wiki_show(5000)
        unwatched = self._unwatched_show(5001)
        self._library(sync_engine, {5000: watched, 5001: unwatched})

        state = sync_engine._pull_from_plex(
            shows_only=True, since=datetime.now(timezone.utc), known_state={}
        )

        assert watched.seasons.call_count == 1
        assert unwatched.seasons.call_count == 0
        assert len(state) == 3
        assert sync_engine.stats["shows_skipped_no_watch_state"] == 1

    def test_skip_survives_a_sync_run(self, sync_engine):
        """The counter is reset by sync_watched_status, so it must exist there."""
        show = self._unwatched_show(6000)
        self._library(sync_engine, {6000: show})
        sync_engine.plex.get_show_counters.side_effect = lambda: {
            6000: PlexClient.show_counters(show)
        }
        sync_engine.plex.cache.memory_cache["movies_list"] = []
        sync_engine._apply_changes = MagicMock()

        stats = sync_engine.sync_watched_status(shows_only=True)

        assert stats["shows_skipped_no_watch_state"] == 1
        assert show.seasons.call_count == 0


class TestShowReadFailuresAreSurfaced:
    """A show that cannot be read must never look like an unwatched show (#22).

    The pull used to log a warning per failing show and carry on, so eleven
    shows could drop out of a run while the run recorded success.
    """

    @pytest.fixture(autouse=True)
    def _no_retry_sleep(self, monkeypatch):
        monkeypatch.setattr(watch_sync, "SHOW_READ_RETRY_DELAY_SECONDS", 0)

    def _engine_for(self, sync_engine, shows, imdb_by_key=None):
        cache = {
            "movies_by_imdb": {},
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
            "movies_list": [],
            "shows_list": [{"ratingKey": rk, "title": f"Show {rk}"} for rk in shows],
            "show_episodes": {},
        }
        store = _FakeEpisodeStore(memory_cache=cache)
        sync_engine.plex.cache = store
        sync_engine.plex.cache.movie_key_to_ids = {}
        sync_engine.plex.cache.show_key_to_imdb = imdb_by_key or {}
        sync_engine.plex.show_counters.side_effect = PlexClient.show_counters
        sync_engine.plex.get_show_counters.side_effect = lambda: {}
        return store

    def test_transient_failure_is_retried_and_succeeds(self, sync_engine):
        """One read timeout is retried, and the show's state still arrives."""
        show = _wiki_show(8000)
        self._engine_for(sync_engine, {8000: show}, {8000: "tt8000"})
        sync_engine.plex._get_plex_item.side_effect = [
            requests.exceptions.ReadTimeout("slow"),
            show,
        ]

        state = sync_engine._pull_from_plex(shows_only=True, since=datetime.now(timezone.utc))

        assert state[("episode", "tt8000", 1, 1)]["watched"] is True
        assert state[("episode", "tt8000", 1, 3)]["watched"] is False
        assert sync_engine.stats["shows_unreadable"] == 0

    def test_timeout_after_the_retry_fails_the_pull(self, sync_engine):
        """A second timeout raises, naming the show, instead of skipping it."""
        show = _wiki_show(8100)
        store = self._engine_for(sync_engine, {8100: show}, {8100: "tt8100"})
        sync_engine.plex._get_plex_item.side_effect = requests.exceptions.ReadTimeout("still slow")

        with pytest.raises(watch_sync.WatchStatePullError) as excinfo:
            sync_engine._pull_from_plex(shows_only=True, since=datetime.now(timezone.utc))

        assert "Show 8100" in str(excinfo.value)
        assert "still slow" in str(excinfo.value)
        assert "8100" in store.forgotten
        # It was tried twice, not once
        assert sync_engine.plex._get_plex_item.call_count == 2

    def test_unexpected_error_fails_the_pull(self, sync_engine):
        """An unexplained failure surfaces too, rather than dropping the show."""
        self._engine_for(sync_engine, {8200: _wiki_show(8200)}, {8200: "tt8200"})
        sync_engine.plex._get_plex_item.side_effect = ValueError("bad payload")

        with pytest.raises(watch_sync.WatchStatePullError) as excinfo:
            sync_engine._pull_from_plex(shows_only=True, since=datetime.now(timezone.utc))

        assert "Show 8200" in str(excinfo.value)
        assert "bad payload" in str(excinfo.value)

    def test_show_gone_from_plex_is_counted_not_fatal(self, sync_engine):
        """A stale cache entry must not fail every run, but must be visible."""
        healthy = _wiki_show(8301)
        self._engine_for(sync_engine, {8300: None, 8301: healthy}, {8300: "tt8300", 8301: "tt8301"})
        sync_engine.plex._get_plex_item.side_effect = lambda rk: (None if rk == 8300 else healthy)

        state = sync_engine._pull_from_plex(shows_only=True, since=datetime.now(timezone.utc))

        assert sync_engine.stats["shows_unreadable"] == 1
        assert ("episode", "tt8301", 1, 1) in state
        assert not any(key[1] == "tt8300" for key in state)

    def test_sync_reports_the_failure_instead_of_success(self, sync_engine):
        """The failure reaches the caller, so the run is not recorded as a pass."""
        self._engine_for(sync_engine, {8400: _wiki_show(8400)}, {8400: "tt8400"})
        sync_engine.plex._get_plex_item.side_effect = requests.exceptions.ReadTimeout("slow")
        sync_engine._apply_changes = MagicMock()

        with pytest.raises(watch_sync.WatchStatePullError):
            sync_engine.sync_watched_status()

        sync_engine._apply_changes.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #69: a settled library must still expire old history entries
# ---------------------------------------------------------------------------


class TestPruningOnAQuietRun:
    """`_apply_changes()` used to prune only when Plex items needed updating."""

    @pytest.fixture
    def quiet_engine(self, sync_engine):
        sync_engine._pull_from_plex = MagicMock(return_value={})
        sync_engine._pull_from_trakt = MagicMock(return_value={})
        return sync_engine

    def test_a_no_change_sync_drops_an_expired_entry(self, quiet_engine, mock_history_manager):
        """Both sides agree, nothing is applied, and the expired row still goes (#69)."""
        mock_history_manager.state["synced_items"].append(
            {
                "media_type": "movie",
                "imdb_id": "tt-expired-entry",
                "plex_rating_key": 4242,
                "updated_at": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
            }
        )
        mock_history_manager.save_state()

        stats = quiet_engine.sync_watched_status()

        assert stats["plex_watched"] == 0
        assert stats["trakt_watched"] == 0
        reloaded = history_manager.WatchHistoryManager(plex_server_id="test-server")
        assert "tt-expired-entry" not in [
            item.get("imdb_id") for item in reloaded.state["synced_items"]
        ]

    def test_a_dry_run_keeps_the_state_file_untouched(self, quiet_engine, mock_history_manager):
        """Retention runs with the run's persist, and a dry run persists nothing."""
        mock_history_manager.state["synced_items"].append(
            {
                "media_type": "movie",
                "imdb_id": "tt-expired-entry",
                "plex_rating_key": 4242,
                "updated_at": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
            }
        )
        mock_history_manager.save_state()

        quiet_engine.sync_watched_status(dry_run=True)

        reloaded = history_manager.WatchHistoryManager(plex_server_id="test-server")
        assert [item.get("imdb_id") for item in reloaded.state["synced_items"]] == [
            "tt-expired-entry"
        ]
