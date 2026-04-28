"""Tests for clients module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from traktor import clients


class TestCacheManager:
    """Tests for CacheManager class."""

    @pytest.fixture
    def mock_plex_server(self):
        """Create a mock Plex server."""
        server = MagicMock()
        server.library.sections.return_value = []
        return server

    @pytest.fixture
    def cache_manager(self, mock_plex_server, tmp_path, monkeypatch):
        """Create a CacheManager with temp cache directory."""
        monkeypatch.setattr(clients, "CACHE_DIR", tmp_path / ".traktor_cache")
        return clients.CacheManager(mock_plex_server)

    def test_init(self, mock_plex_server):
        """Test CacheManager initialization."""
        cm = clients.CacheManager(mock_plex_server)
        assert cm.plex_server == mock_plex_server
        assert cm.memory_cache == {}

    def test_extract_external_ids_with_imdb_guid(self):
        """Test extracting IMDb ID from item.guid."""
        item = MagicMock()
        item.guid = "imdb://tt1234567"
        item.guids = []

        ids = clients.CacheManager._extract_external_ids(item)

        assert "tt1234567" in ids["imdb"]
        assert len(ids["tmdb"]) == 0

    def test_extract_external_ids_with_tmdb_guid(self):
        """Test extracting TMDb ID from item.guid."""
        item = MagicMock()
        item.guid = "tmdb://987654"
        item.guids = []

        ids = clients.CacheManager._extract_external_ids(item)

        assert "987654" in ids["tmdb"]
        assert len(ids["imdb"]) == 0

    def test_extract_external_ids_from_guids_list(self):
        """Test extracting IDs from item.guids list."""
        item = MagicMock()
        item.guid = None

        guid1 = MagicMock()
        guid1.__str__ = MagicMock(return_value="imdb://tt1234567")
        guid2 = MagicMock()
        guid2.__str__ = MagicMock(return_value="tmdb://987654")
        item.guids = [guid1, guid2]

        ids = clients.CacheManager._extract_external_ids(item)

        assert "tt1234567" in ids["imdb"]
        assert "987654" in ids["tmdb"]

    def test_extract_external_ids_no_ids(self):
        """Test extracting IDs when none are present."""
        item = MagicMock()
        item.guid = None
        item.guids = []

        ids = clients.CacheManager._extract_external_ids(item)

        assert len(ids["imdb"]) == 0
        assert len(ids["tmdb"]) == 0

    def test_extract_external_ids_with_local_guid(self):
        """Test extracting IDs with local:// guid (should be ignored)."""
        item = MagicMock()
        item.guid = "local://12345"
        item.guids = []

        ids = clients.CacheManager._extract_external_ids(item)

        assert len(ids["imdb"]) == 0
        assert len(ids["tmdb"]) == 0

    def test_parse_guid_for_ids_imdb(self):
        """Test _parse_guid_for_ids with IMDb guid."""
        ids = {"imdb": set(), "tmdb": set()}
        clients.CacheManager._parse_guid_for_ids("imdb://tt1234567?some=param", ids)

        assert "tt1234567" in ids["imdb"]
        assert len(ids["tmdb"]) == 0

    def test_parse_guid_for_ids_tmdb(self):
        """Test _parse_guid_for_ids with TMDb guid."""
        ids = {"imdb": set(), "tmdb": set()}
        clients.CacheManager._parse_guid_for_ids("tmdb://987654", ids)

        assert "987654" in ids["tmdb"]
        assert len(ids["imdb"]) == 0

    def test_parse_guid_for_ids_invalid(self):
        """Test _parse_guid_for_ids with invalid guid."""
        ids = {"imdb": set(), "tmdb": set()}
        clients.CacheManager._parse_guid_for_ids("local://12345", ids)

        assert len(ids["imdb"]) == 0
        assert len(ids["tmdb"]) == 0


class TestCacheManagerFinders:
    """Tests for CacheManager find methods."""

    @pytest.fixture
    def populated_cache_manager(self, tmp_path, monkeypatch):
        """Create a CacheManager with pre-populated cache."""
        mock_server = MagicMock()
        monkeypatch.setattr(clients, "CACHE_DIR", tmp_path / ".traktor_cache")
        cm = clients.CacheManager(mock_server)

        # Populate cache
        cm.memory_cache = {
            "movies_by_imdb": {"tt1234567": {"ratingKey": "1", "title": "Test Movie"}},
            "movies_by_tmdb": {"987654": {"ratingKey": "1", "title": "Test Movie"}},
            "shows_by_imdb": {"tt7654321": {"ratingKey": "2", "title": "Test Show"}},
            "shows_by_tmdb": {"456789": {"ratingKey": "2", "title": "Test Show"}},
        }
        return cm

    def test_find_movie_by_imdb(self, populated_cache_manager):
        """Test finding movie by IMDb ID."""
        result = populated_cache_manager.find_movie_by_imdb("tt1234567")

        assert result is not None
        assert result["title"] == "Test Movie"

    def test_find_movie_by_imdb_not_found(self, populated_cache_manager):
        """Test finding movie by IMDb ID when not in cache."""
        result = populated_cache_manager.find_movie_by_imdb("tt9999999")

        assert result is None

    def test_find_movie_by_tmdb(self, populated_cache_manager):
        """Test finding movie by TMDb ID."""
        result = populated_cache_manager.find_movie_by_tmdb("987654")

        assert result is not None
        assert result["title"] == "Test Movie"

    def test_find_movie_by_tmdb_integer(self, populated_cache_manager):
        """Test finding movie by TMDb ID as integer (should convert to string)."""
        result = populated_cache_manager.find_movie_by_tmdb(987654)

        assert result is not None
        assert result["title"] == "Test Movie"

    def test_find_show_by_imdb(self, populated_cache_manager):
        """Test finding show by IMDb ID."""
        result = populated_cache_manager.find_show_by_imdb("tt7654321")

        assert result is not None
        assert result["title"] == "Test Show"

    def test_find_show_by_tmdb(self, populated_cache_manager):
        """Test finding show by TMDb ID."""
        result = populated_cache_manager.find_show_by_tmdb("456789")

        assert result is not None
        assert result["title"] == "Test Show"


class TestTraktAuth:
    """Tests for TraktAuth class."""

    def test_init_no_tokens(self, monkeypatch):
        """Test initialization with no tokens."""
        # Mock settings to have no tokens
        monkeypatch.setattr(clients, "TRAKT_ACCESS_TOKEN", None)
        monkeypatch.setattr(clients, "TRAKT_REFRESH_TOKEN", None)

        auth = clients.TraktAuth()

        assert auth.access_token is None
        assert auth.refresh_token is None

    def test_init_with_env_tokens(self, monkeypatch):
        """Test initialization loads tokens from environment."""
        # Mock settings to have tokens
        monkeypatch.setattr(clients, "TRAKT_ACCESS_TOKEN", "test-access-token")
        monkeypatch.setattr(clients, "TRAKT_REFRESH_TOKEN", "test-refresh-token")

        auth = clients.TraktAuth()

        assert auth.access_token == "test-access-token"
        assert auth.refresh_token == "test-refresh-token"

    def test_save_tokens_logs_message(self, caplog):
        """Test that save_tokens logs message for in-memory tokens."""
        auth = clients.TraktAuth()
        auth.access_token = "new-access-token"
        auth.refresh_token = "new-refresh-token"

        import logging

        with caplog.at_level(logging.INFO):
            result = auth.save_tokens()

        # Should return True (success)
        assert result is True
        # Should log message about tokens being in memory
        assert "tokens obtained" in caplog.text.lower() or "memory" in caplog.text.lower()

    def test_get_auth_url(self):
        """Test generating auth URL."""
        auth = clients.TraktAuth()

        url = auth.get_auth_url()

        assert "trakt.tv" in url
        assert "oauth" in url
        assert "client_id" in url


class TestTraktClient:
    """Tests for TraktClient class."""

    @pytest.fixture
    def mock_auth(self):
        """Create a mock TraktAuth."""
        auth = MagicMock()
        auth.get_headers.return_value = {
            "Authorization": "Bearer test-token",
            "trakt-api-key": "test-client-id",
        }
        return auth

    @pytest.fixture
    def trakt_client(self, mock_auth):
        """Create a TraktClient with mock auth."""
        return clients.TraktClient(mock_auth)

    def test_init(self, mock_auth):
        """Test TraktClient initialization."""
        client = clients.TraktClient(mock_auth)
        assert client.auth == mock_auth

    def test_get_liked_lists_success(self, trakt_client, mock_auth):
        """Test fetching liked lists successfully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"list": {"name": "Test List", "ids": {"trakt": 123}}}]

        # Patch the session.get method on the trakt_client instance
        with patch.object(trakt_client._session, "get", return_value=mock_response):
            result = trakt_client.get_liked_lists()

        assert len(result) == 1
        assert result[0]["list"]["name"] == "Test List"

    def test_request_token_refresh_on_401(self, trakt_client, mock_auth):
        """Test that 401 triggers token refresh."""
        # First call returns 401, second returns 200
        mock_response_401 = MagicMock()
        mock_response_401.status_code = 401

        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.content = b"[]"

        mock_auth.refresh_access_token.return_value = True

        # Patch the session.get method on the trakt_client instance
        with patch.object(
            trakt_client._session, "get", side_effect=[mock_response_401, mock_response_200]
        ):
            # Should retry after token refresh
            trakt_client._request("users/likes/lists")

        # Token refresh should have been called
        mock_auth.refresh_access_token.assert_called_once()

    def test_get_watched_movies(self, trakt_client, mock_auth):
        """Test get_watched_movies method exists and can be mocked."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "movie": {"title": "Test Movie", "ids": {"imdb": "tt1234567"}},
                "last_watched_at": "2026-04-10T12:00:00.000Z",
            }
        ]

        with patch.object(trakt_client._session, "get", return_value=mock_response):
            result = trakt_client.get_watched_movies()

        assert len(result) == 1
        assert result[0]["movie"]["title"] == "Test Movie"

    def test_get_watched_shows(self, trakt_client, mock_auth):
        """Test get_watched_shows method exists and can be mocked."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "show": {"title": "Test Show", "ids": {"imdb": "tt7654321"}},
                "seasons": [
                    {
                        "number": 1,
                        "episodes": [{"number": 1, "last_watched_at": "2026-04-10T12:00:00.000Z"}],
                    }
                ],
            }
        ]

        with patch.object(trakt_client._session, "get", return_value=mock_response):
            result = trakt_client.get_watched_shows()

        assert len(result) == 1
        assert result[0]["show"]["title"] == "Test Show"

    def test_add_to_history(self, trakt_client, mock_auth):
        """Test adding items to Trakt history."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"added": {"movies": 1, "episodes": 0}}

        with patch.object(trakt_client._session, "post", return_value=mock_response):
            result = trakt_client.add_to_history(
                movies=[{"title": "Test", "year": 2020, "ids": {"imdb": "tt1234567"}}]
            )

        assert result["added"]["movies"] == 1

    def test_remove_from_history(self, trakt_client, mock_auth):
        """Test removing items from Trakt history."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"deleted": {"movies": 1, "episodes": 0}}

        with patch.object(trakt_client._session, "post", return_value=mock_response):
            result = trakt_client.remove_from_history(movies=[{"ids": {"imdb": "tt1234567"}}])

        assert result["deleted"]["movies"] == 1


class TestPlexClient:
    """Tests for PlexClient class."""

    @pytest.fixture
    def mock_plex_server(self):
        """Create a mock Plex server."""
        server = MagicMock()
        server.friendlyName = "Test Server"
        server.version = "1.0.0"
        return server

    @pytest.fixture
    def mock_cache_manager(self):
        """Create a mock CacheManager."""
        cache = MagicMock()
        cache.memory_cache = {}
        return cache

    @pytest.fixture
    def plex_client(self, mock_plex_server, mock_cache_manager):
        """Create a PlexClient with mock dependencies."""
        return clients.PlexClient(mock_plex_server, mock_cache_manager)

    def test_init(self, mock_plex_server, mock_cache_manager):
        """Test PlexClient initialization."""
        pc = clients.PlexClient(mock_plex_server, mock_cache_manager)
        assert pc.server == mock_plex_server
        assert pc.cache == mock_cache_manager

    def test_find_item_by_cache_movie(self, plex_client, mock_cache_manager):
        """Test finding movie by cache lookup."""
        # Setup cache
        mock_cache_manager.find_movie_by_imdb.return_value = {
            "ratingKey": "12345",
            "title": "Test Movie",
        }

        # Setup server fetch
        mock_item = MagicMock()
        plex_client.server.fetchItem.return_value = mock_item

        result = plex_client.find_item_by_cache(imdb_id="tt1234567", media_type="movie")

        assert result == mock_item
        mock_cache_manager.find_movie_by_imdb.assert_called_once_with("tt1234567")

    def test_find_item_by_cache_show(self, plex_client, mock_cache_manager):
        """Test finding show by cache lookup."""
        # Setup cache
        mock_cache_manager.find_show_by_imdb.return_value = {
            "ratingKey": "67890",
            "title": "Test Show",
        }

        # Setup server fetch
        mock_item = MagicMock()
        plex_client.server.fetchItem.return_value = mock_item

        result = plex_client.find_item_by_cache(imdb_id="tt7654321", media_type="show")

        assert result == mock_item
        mock_cache_manager.find_show_by_imdb.assert_called_once_with("tt7654321")

    def test_find_item_by_cache_not_found(self, plex_client, mock_cache_manager):
        """Test cache lookup when item not found."""
        mock_cache_manager.find_movie_by_imdb.return_value = None
        mock_cache_manager.find_movie_by_tmdb.return_value = None

        result = plex_client.find_item_by_cache(imdb_id="tt9999999", media_type="movie")

        assert result is None

    def test_find_item_by_cache_prefers_imdb(self, plex_client, mock_cache_manager):
        """Test that IMDb lookup is preferred over TMDb."""
        mock_cache_manager.find_movie_by_imdb.return_value = {
            "ratingKey": "111",
            "title": "By IMDb",
        }

        mock_item = MagicMock()
        plex_client.server.fetchItem.return_value = mock_item

        _ = plex_client.find_item_by_cache(imdb_id="tt1234567", tmdb_id=987654, media_type="movie")

        # Should only call IMDb lookup, not TMDb
        mock_cache_manager.find_movie_by_imdb.assert_called_once_with("tt1234567")
        mock_cache_manager.find_movie_by_tmdb.assert_not_called()

    def test_mark_as_watched(self, plex_client):
        """Test marking item as watched."""
        mock_item = MagicMock()
        mock_item.title = "Test Movie"
        plex_client.server.fetchItem.return_value = mock_item

        result = plex_client.mark_as_watched("12345")

        assert result is True
        mock_item.markWatched.assert_called_once()

    def test_mark_as_watched_not_found(self, plex_client):
        """Test marking non-existent item as watched."""
        from plexapi.exceptions import NotFound

        plex_client.server.fetchItem.side_effect = NotFound("Item not found")

        result = plex_client.mark_as_watched("99999")

        assert result is False

    def test_mark_as_unwatched(self, plex_client):
        """Test marking item as unwatched."""
        mock_item = MagicMock()
        mock_item.title = "Test Movie"
        plex_client.server.fetchItem.return_value = mock_item

        result = plex_client.mark_as_unwatched("12345")

        assert result is True
        mock_item.markUnwatched.assert_called_once()

    def test_is_watched(self, plex_client):
        """Test checking if item is watched."""
        mock_item = MagicMock()
        mock_item.isWatched = True
        mock_item.lastViewedAt = 1234567890
        plex_client.server.fetchItem.return_value = mock_item

        is_watched, last_viewed = plex_client.is_watched("12345")

        assert is_watched is True
        assert last_viewed == 1234567890

    def test_is_watched_not_found(self, plex_client):
        """Test checking watched status of non-existent item."""
        from plexapi.exceptions import NotFound

        plex_client.server.fetchItem.side_effect = NotFound("Item not found")

        is_watched, last_viewed = plex_client.is_watched("99999")

        assert is_watched is False
        assert last_viewed is None

    def test_get_play_history(self, plex_client):
        """Test getting play history."""
        mock_section = MagicMock()
        mock_section.type = "movie"
        mock_section.history.return_value = [MagicMock(), MagicMock()]
        plex_client.server.library.sections.return_value = [mock_section]

        result = plex_client.get_play_history()

        assert len(result) == 2

    def test_create_or_update_playlist_sorts_items(self, plex_client):
        """Test that create_or_update_playlist sorts items with movies first."""
        # Create mock items with mixed types
        mock_movie = MagicMock()
        mock_movie.TYPE = "movie"
        mock_movie.title = "Test Movie"

        mock_episode = MagicMock()
        mock_episode.TYPE = "episode"
        mock_episode.title = "Test Episode"

        mock_episode2 = MagicMock()
        mock_episode2.TYPE = "episode"
        mock_episode2.title = "Test Episode 2"

        # Items in mixed order
        items = [mock_episode, mock_movie, mock_episode2]

        # Mock playlist creation
        mock_playlist = MagicMock()
        mock_playlist.items.return_value = []
        plex_client.server.playlist.side_effect = clients.NotFound
        plex_client.server.createPlaylist.return_value = mock_playlist

        # Call the method
        plex_client.create_or_update_playlist("Test Playlist", items)

        # Check that createPlaylist was called with sorted items (movies first)
        plex_client.server.createPlaylist.assert_called_once()
        call_args = plex_client.server.createPlaylist.call_args

        # The items should be sorted with movie first
        sorted_items = call_args[1]["items"]
        assert len(sorted_items) == 3
        assert sorted_items[0].TYPE == "movie"  # Movie should be first
        assert sorted_items[1].TYPE == "episode"  # Then episodes
        assert sorted_items[2].TYPE == "episode"

    def test_create_or_update_playlist_empty_items(self, plex_client):
        """Test create_or_update_playlist with empty items list."""
        # Mock playlist creation
        mock_playlist = MagicMock()
        mock_playlist.items.return_value = []
        plex_client.server.playlist.side_effect = clients.NotFound
        plex_client.server.createPlaylist.return_value = mock_playlist

        # Call with empty list
        plex_client.create_or_update_playlist("Test Playlist", [])

        # Should still work
        plex_client.server.createPlaylist.assert_called_once()

    def test_create_or_update_playlist_existing_playlist(self, plex_client):
        """Test updating an existing playlist."""
        # Create mock items
        mock_movie = MagicMock()
        mock_movie.TYPE = "movie"

        mock_episode = MagicMock()
        mock_episode.TYPE = "episode"

        items = [mock_episode, mock_movie]  # Mixed order

        # Mock existing playlist
        mock_playlist = MagicMock()
        mock_playlist.items.return_value = []
        mock_playlist.removeItems.return_value = None
        mock_playlist.addItems.return_value = None

        plex_client.server.playlist.return_value = mock_playlist

        # Call the method
        plex_client.create_or_update_playlist("Test Playlist", items)

        # Check that addItems was called with sorted items
        mock_playlist.addItems.assert_called_once()
        sorted_items = mock_playlist.addItems.call_args[0][0]

        # Should be sorted with movie first
        assert sorted_items[0].TYPE == "movie"
        assert sorted_items[1].TYPE == "episode"

    def test_create_or_update_playlist_recreates_large_playlist(self, plex_client):
        """Test that large playlists (>1000 items) are deleted and recreated."""
        mock_movie = MagicMock()
        mock_movie.TYPE = "movie"
        mock_movie.title = "Test Movie"
        items = [mock_movie]

        # Mock existing large playlist
        mock_playlist = MagicMock()
        # Return 1500 items (more than MAX_PLAYLIST_SIZE_FOR_INCREMENTAL)
        mock_playlist.items.return_value = [MagicMock() for _ in range(1500)]
        mock_playlist.delete.return_value = None
        plex_client.server.playlist.return_value = mock_playlist
        plex_client.server.createPlaylist.return_value = mock_playlist

        plex_client.create_or_update_playlist("Large Playlist", items)

        # Should have deleted old playlist and created new one
        mock_playlist.delete.assert_called_once()
        plex_client.server.createPlaylist.assert_called_once()

    def test_create_or_update_playlist_batch_adding(self, plex_client):
        """Test that items are added in batches when exceeding batch size."""
        items = []
        for i in range(600):  # More than DEFAULT_PLAYLIST_BATCH_SIZE (500)
            mock_movie = MagicMock()
            mock_movie.TYPE = "movie"
            mock_movie.title = f"Movie {i}"
            items.append(mock_movie)

        # Mock existing small playlist
        mock_playlist = MagicMock()
        mock_playlist.items.return_value = []  # Empty playlist
        mock_playlist.addItems.return_value = None
        plex_client.server.playlist.return_value = mock_playlist

        plex_client.create_or_update_playlist("Batch Playlist", items)

        # Should have called addItems in batches
        assert mock_playlist.addItems.call_count >= 2

    def test_create_or_update_playlist_all_movies_no_sort_change(self, plex_client):
        """Test that all-movie items don't change order beyond staying movies."""
        mock_movie1 = MagicMock()
        mock_movie1.TYPE = "movie"
        mock_movie1.title = "Movie A"

        mock_movie2 = MagicMock()
        mock_movie2.TYPE = "movie"
        mock_movie2.title = "Movie B"

        items = [mock_movie1, mock_movie2]

        plex_client.server.playlist.side_effect = clients.NotFound
        mock_playlist = MagicMock()
        mock_playlist.items.return_value = []
        plex_client.server.createPlaylist.return_value = mock_playlist

        plex_client.create_or_update_playlist("Movies Only", items)

        call_args = plex_client.server.createPlaylist.call_args
        sorted_items = call_args[1]["items"]
        assert len(sorted_items) == 2
        # All movies should remain in original order within the movie group
        assert sorted_items[0].TYPE == "movie"
        assert sorted_items[1].TYPE == "movie"

    def test_create_or_update_playlist_all_episodes_no_sort_change(self, plex_client):
        """Test that all-episode items stay as episodes."""
        mock_ep1 = MagicMock()
        mock_ep1.TYPE = "episode"
        mock_ep1.title = "Episode 1"

        mock_ep2 = MagicMock()
        mock_ep2.TYPE = "episode"
        mock_ep2.title = "Episode 2"

        items = [mock_ep1, mock_ep2]

        plex_client.server.playlist.side_effect = clients.NotFound
        mock_playlist = MagicMock()
        mock_playlist.items.return_value = []
        plex_client.server.createPlaylist.return_value = mock_playlist

        plex_client.create_or_update_playlist("Episodes Only", items)

        call_args = plex_client.server.createPlaylist.call_args
        sorted_items = call_args[1]["items"]
        assert len(sorted_items) == 2
        assert sorted_items[0].TYPE == "episode"
        assert sorted_items[1].TYPE == "episode"

    def test_create_or_update_playlist_add_to_existing(self, plex_client):
        """Test adding items to an existing non-empty playlist."""
        mock_movie = MagicMock()
        mock_movie.TYPE = "movie"
        mock_movie.title = "New Movie"
        items = [mock_movie]

        # Mock existing playlist with 5 items
        mock_playlist = MagicMock()
        mock_playlist.items.return_value = [MagicMock() for _ in range(5)]
        mock_playlist.removeItems.return_value = None
        mock_playlist.addItems.return_value = None
        plex_client.server.playlist.return_value = mock_playlist

        plex_client.create_or_update_playlist("Existing Playlist", items)

        # Should remove old items and add new
        mock_playlist.removeItems.assert_called_once()
        mock_playlist.addItems.assert_called_once()

    def test_create_or_update_playlist_error_handling(self, plex_client):
        """Test that errors during playlist creation are raised."""
        mock_movie = MagicMock()
        mock_movie.TYPE = "movie"
        mock_movie.title = "Test Movie"
        items = [mock_movie]

        # Simulate server error during playlist access
        plex_client.server.playlist.side_effect = Exception("Server unavailable")

        with pytest.raises(Exception, match="Server unavailable"):
            plex_client.create_or_update_playlist("Error Playlist", items)


class TestTraktClientRateLimiting:
    """Tests for TraktClient rate limiting and retry logic."""

    @pytest.fixture
    def mock_auth(self):
        """Create a mock TraktAuth."""
        auth = MagicMock()
        auth.get_headers.return_value = {"Authorization": "Bearer test_token"}
        auth.refresh_access_token.return_value = True
        return auth

    @pytest.fixture
    def trakt_client(self, mock_auth):
        """Create a TraktClient with mock auth."""
        return clients.TraktClient(mock_auth)

    def test_rate_limit_enforced(self, trakt_client, mock_auth):
        """Test that rate limiting enforces minimum interval between requests."""
        import time

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"test": "data"}'
        mock_response.json.return_value = {"test": "data"}
        mock_response.raise_for_status.return_value = None

        with patch.object(trakt_client._session, "get", return_value=mock_response):
            # Make first request
            start_time = time.time()
            trakt_client._request("test/endpoint")

            # Make second request immediately
            trakt_client._request("test/endpoint")
            elapsed = time.time() - start_time

            # Should have waited at least MIN_REQUEST_INTERVAL (0.3 seconds)
            assert elapsed >= 0.3

    def test_retry_on_rate_limit_429(self, trakt_client, mock_auth):
        """Test retry logic on 429 rate limit response."""
        # First call returns 429, second succeeds
        mock_response_429 = MagicMock(status_code=429, headers={"Retry-After": "1"})
        mock_response_200 = MagicMock(
            status_code=200,
            content=b"{}",
            json=lambda: {},
            raise_for_status=lambda: None,
        )

        with patch.object(
            trakt_client._session, "get", side_effect=[mock_response_429, mock_response_200]
        ):
            trakt_client._request("test/endpoint")

            # Should have made 2 requests
            assert trakt_client._session.get.call_count == 2

    def test_retry_on_server_error_500(self, trakt_client, mock_auth):
        """Test retry logic on 5xx server errors."""
        # First call returns 500, second succeeds
        mock_response_500 = MagicMock(status_code=500)
        mock_response_200 = MagicMock(
            status_code=200,
            content=b"{}",
            json=lambda: {},
            raise_for_status=lambda: None,
        )

        with patch.object(
            trakt_client._session, "get", side_effect=[mock_response_500, mock_response_200]
        ):
            trakt_client._request("test/endpoint")

            # Should have made 2 requests
            assert trakt_client._session.get.call_count == 2

    def test_retry_on_timeout(self, trakt_client, mock_auth):
        """Test retry logic on request timeout."""
        from requests.exceptions import Timeout

        # First call times out, second succeeds
        mock_response_200 = MagicMock(
            status_code=200,
            content=b"{}",
            json=lambda: {},
            raise_for_status=lambda: None,
        )

        with patch.object(
            trakt_client._session,
            "get",
            side_effect=[Timeout("Request timed out"), mock_response_200],
        ):
            trakt_client._request("test/endpoint")

            # Should have made 2 requests
            assert trakt_client._session.get.call_count == 2


class TestCacheManagerIncrementalUpdate:
    """Tests for CacheManager incremental cache updates."""

    @pytest.fixture
    def mock_plex_server(self):
        """Create a mock Plex server with sections."""
        server = MagicMock()

        # Create a mock movie section
        movie_section = MagicMock()
        movie_section.type = "movie"
        movie_section.title = "Movies"

        # Create mock movies
        import time

        movie1 = MagicMock()
        movie1.title = "Test Movie"
        movie1.year = 2024
        movie1.ratingKey = "12345"
        movie1.guid = "imdb://tt1234567"
        movie1.guids = []
        movie1.addedAt = time.time()  # Recently added

        movie_section.recentlyAdded.return_value = [movie1]

        server.library.sections.return_value = [movie_section]
        return server

    @pytest.fixture
    def cache_manager(self, mock_plex_server, tmp_path, monkeypatch):
        """Create a CacheManager with temp cache directory."""
        monkeypatch.setattr(clients, "CACHE_DIR", tmp_path / ".traktor_cache")
        cm = clients.CacheManager(mock_plex_server)

        # Pre-populate with existing cache
        cm.memory_cache = {
            "movies_by_imdb": {},
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
            "shows_by_tmdb": {},
            "movies_list": [],
            "shows_list": [],
            "by_rating_key": {},
        }
        return cm

    def test_incremental_update_adds_new_items(self, cache_manager, mock_plex_server, tmp_path):
        """Test that incremental update adds new items to cache."""
        # Mock the cache meta file to have a recent timestamp
        from datetime import datetime, timedelta

        cache_manager.cache_meta_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_manager.cache_meta_file, "w") as f:
            json.dump(
                {
                    "version": clients.CACHE_VERSION,
                    "created": (datetime.now() - timedelta(hours=1)).isoformat(),
                    "last_update": (datetime.now() - timedelta(hours=1)).isoformat(),
                },
                f,
            )

        # Run incremental update
        result = cache_manager._incremental_cache_update()

        # Should succeed
        assert result is True

        # Should have added the new movie
        assert len(cache_manager.memory_cache["movies_list"]) == 1
        assert "tt1234567" in cache_manager.memory_cache["movies_by_imdb"]

    def test_incremental_update_fallback_when_no_timestamp(self, cache_manager):
        """Test that incremental update falls back to full rebuild without timestamp."""
        result = cache_manager._incremental_cache_update()

        # Should return False (indicating fallback needed)
        assert result is False

    def test_incremental_update_skips_when_cache_expired(self, cache_manager, tmp_path):
        """Test that incremental update skips when cache is too old."""
        from datetime import datetime, timedelta

        # Create a very old cache meta file
        cache_manager.cache_meta_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_manager.cache_meta_file, "w") as f:
            json.dump(
                {
                    "version": clients.CACHE_VERSION,
                    "created": (datetime.now() - timedelta(hours=25)).isoformat(),
                    "last_update": (datetime.now() - timedelta(hours=25)).isoformat(),
                },
                f,
            )

        result = cache_manager._incremental_cache_update()

        # Should return False (indicating fallback needed)
        assert result is False
