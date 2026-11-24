"""Tests for clients module."""

import gzip
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests
from plexapi.video import Show

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

    def test_movie_key_to_ids_builds_both_indexes(self, populated_cache_manager):
        """Test reverse map combines IMDb and TMDb indexes per rating key."""
        result = populated_cache_manager.movie_key_to_ids

        assert result == {"1": ("tt1234567", "987654")}
        # Property is cached - same object on second access
        assert populated_cache_manager.movie_key_to_ids is result

    def test_show_key_to_imdb(self, populated_cache_manager):
        """Test reverse map from show rating key to IMDb ID."""
        assert populated_cache_manager.show_key_to_imdb == {"2": "tt7654321"}

    def test_movie_imdb_to_rating_key(self, populated_cache_manager):
        """Test reverse map from movie IMDb ID to rating key."""
        assert populated_cache_manager.movie_imdb_to_rating_key == {"tt1234567": "1"}

    def test_reverse_indexes_invalidated_on_add(self, tmp_path, monkeypatch):
        """Test that adding an item invalidates the lazily-built reverse maps."""
        mock_server = MagicMock()
        monkeypatch.setattr(clients, "CACHE_DIR", tmp_path / ".traktor_cache")
        cm = clients.CacheManager(mock_server)
        cm.memory_cache = {
            "movies_by_imdb": {},
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
            "shows_by_tmdb": {},
            "movies_list": [],
            "shows_list": [],
            "by_rating_key": {},
        }
        assert cm.movie_key_to_ids == {}

        item = MagicMock()
        item.ratingKey = 42
        item.title = "New Movie"
        item.year = 2024
        item.guid = "com.plexapp.agents.imdb://tt999?lang=en"
        item.guids = []
        item.lastViewedAt = None
        item.isWatched = False

        cm._add_item_to_cache(item, "movie")

        assert cm.movie_key_to_ids == {42: ("tt999", None)}


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

    def test_save_tokens_logs_message(self, caplog, tmp_path, monkeypatch):
        """Test that save_tokens logs message for in-memory tokens."""
        # Point the client at a fake .env: the token store is the file settings
        # loaded, not the working directory (issue #103).
        fake_env = tmp_path / ".env"
        fake_env.write_text("TRAKT_ACCESS_TOKEN=old\nTRAKT_REFRESH_TOKEN=old\n")
        monkeypatch.setattr(clients, "ENV_FILE", fake_env)

        auth = clients.TraktAuth()
        auth.access_token = "a" * 32  # current Trakt token length
        auth.refresh_token = "b" * 32  # current Trakt token length

        import logging

        with caplog.at_level(logging.INFO):
            result = auth.save_tokens()

        # Should return True (success)
        assert result is True
        # Should log message about tokens being in memory
        assert "tokens obtained" in caplog.text.lower() or "memory" in caplog.text.lower()
        # The token store must not be readable by other accounts (issue #12)
        assert (fake_env.stat().st_mode & 0o777) == 0o600

    def test_save_tokens_restricts_env_permissions(self, tmp_path, monkeypatch):
        """A wide-open .env is tightened to owner-only when tokens are saved
        (issue #12)."""
        fake_env = tmp_path / ".env"
        fake_env.write_text("TRAKT_ACCESS_TOKEN=old\nTRAKT_REFRESH_TOKEN=old\n")
        fake_env.chmod(0o644)
        monkeypatch.setattr(clients, "ENV_FILE", fake_env)

        auth = clients.TraktAuth()
        auth.access_token = "a" * 32
        auth.refresh_token = "b" * 32

        assert auth.save_tokens() is True
        assert (fake_env.stat().st_mode & 0o777) == 0o600

    def test_save_tokens_refuses_placeholders(self, caplog, tmp_path, monkeypatch):
        """Test that save_tokens refuses to write placeholder/short tokens."""
        monkeypatch.chdir(tmp_path)
        fake_env = tmp_path / ".env"
        fake_env.write_text("TRAKT_ACCESS_TOKEN=old\nTRAKT_REFRESH_TOKEN=old\n")

        auth = clients.TraktAuth()
        auth.access_token = "new-access-token"  # literal placeholder text
        auth.refresh_token = "new-refresh-token"

        import logging

        with caplog.at_level(logging.ERROR):
            result = auth.save_tokens()

        assert result is False
        assert "refusing to save" in caplog.text.lower()
        # The .env file must not have been overwritten with placeholders
        assert fake_env.read_text() == "TRAKT_ACCESS_TOKEN=old\nTRAKT_REFRESH_TOKEN=old\n"

    def test_has_valid_tokens_rejects_placeholders(self, monkeypatch):
        """Placeholder tokens must not be treated as valid auth."""
        monkeypatch.setattr(clients, "TRAKT_ACCESS_TOKEN", "new-access-token")
        monkeypatch.setattr(clients, "TRAKT_REFRESH_TOKEN", "new-refresh-token")

        auth = clients.TraktAuth()
        assert auth.has_valid_tokens() is False

    def test_has_valid_tokens_rejects_short_tokens(self, monkeypatch):
        """Truncated tokens must not be treated as valid auth."""
        monkeypatch.setattr(clients, "TRAKT_ACCESS_TOKEN", "abc123")
        monkeypatch.setattr(clients, "TRAKT_REFRESH_TOKEN", "xyz789")

        auth = clients.TraktAuth()
        assert auth.has_valid_tokens() is False

    def test_has_valid_tokens_accepts_real_tokens(self, monkeypatch):
        """Real-length tokens must pass validation."""
        monkeypatch.setattr(clients, "TRAKT_ACCESS_TOKEN", "a" * 32)
        monkeypatch.setattr(clients, "TRAKT_REFRESH_TOKEN", "b" * 32)

        auth = clients.TraktAuth()
        assert auth.has_valid_tokens() is True

    def test_get_auth_url(self):
        """Test generating auth URL."""
        auth = clients.TraktAuth()

        url = auth.get_auth_url()

        assert "trakt.tv" in url
        assert "oauth" in url
        assert "client_id" in url
        # A fresh anti-CSRF state rides along (issue #10)
        assert "state=" in url
        assert auth.oauth_state
        assert auth.oauth_state in url

    def test_get_auth_url_uses_a_new_state_per_login(self):
        """Every login attempt gets its own state value (issue #10)."""
        auth = clients.TraktAuth()

        first_url = auth.get_auth_url()
        first_state = auth.oauth_state
        second_url = auth.get_auth_url()

        assert first_state
        assert auth.oauth_state != first_state
        assert first_state in first_url
        assert auth.oauth_state in second_url


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

    def test_get_own_lists_wraps_each_list_like_a_liked_list(self, trakt_client):
        """Owned lists come back in the same shape the processing path expects (#26)."""
        owned = [
            {"name": "My List", "ids": {"trakt": 1}, "user": {"username": "me"}},
            {"name": "Other List", "ids": {"trakt": 2}, "user": {"username": "me"}},
        ]
        with patch.object(trakt_client, "_paginated_get", return_value=owned) as paginated:
            result = trakt_client.get_own_lists()

        paginated.assert_called_once_with("users/me/lists")
        assert result == [{"list": owned[0]}, {"list": owned[1]}]

    def test_get_own_lists_propagates_request_failure(self, trakt_client):
        """A failing fetch raises so the caller can report the source as skipped."""
        with patch.object(
            trakt_client, "_paginated_get", side_effect=requests.exceptions.ConnectionError("x")
        ):
            with pytest.raises(requests.exceptions.RequestException):
                trakt_client.get_own_lists()

    def test_get_list_items_reads_a_private_list_through_the_owner_route(self, trakt_client):
        """A private list answers 403 by username, so it is read as the owner (#26)."""
        forbidden = requests.exceptions.HTTPError(response=MagicMock(status_code=403))
        movie = {"type": "movie", "movie": {"title": "Private Pick"}}
        with patch.object(
            trakt_client, "_paginated_get", side_effect=[forbidden, [movie]]
        ) as paginated:
            items = trakt_client.get_list_items("swordfishtrumpet", "36982406")

        assert items == [movie]
        assert [call.args[0] for call in paginated.call_args_list] == [
            "users/swordfishtrumpet/lists/36982406/items",
            "users/me/lists/36982406/items",
        ]

    def test_get_list_items_does_not_hide_other_failures(self, trakt_client):
        """Only a permission answer falls back; a 500 still raises (#26)."""
        server_error = requests.exceptions.HTTPError(response=MagicMock(status_code=500))
        with patch.object(trakt_client, "_paginated_get", side_effect=server_error):
            with pytest.raises(requests.exceptions.HTTPError):
                trakt_client.get_list_items("swordfishtrumpet", "42")

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

    def test_post_token_refresh_on_401(self, trakt_client, mock_auth):
        """Test that POST requests also refresh the token on 401.

        Regression: the POST path previously caught HTTPError which
        ``_request_with_retry`` never raises for 401s, so POST history
        operations never triggered a token refresh. The shared
        ``_execute_with_token_refresh`` helper now handles both methods.
        """
        mock_response_401 = MagicMock()
        mock_response_401.status_code = 401

        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.json.return_value = {"added": {"movies": 0, "episodes": 0}}
        mock_response_200.content = b"{}"

        mock_auth.refresh_access_token.return_value = True

        with patch.object(
            trakt_client._session, "post", side_effect=[mock_response_401, mock_response_200]
        ):
            response = trakt_client._post_with_token_refresh(
                "https://api.trakt.tv/sync/history", {"movies": []}
            )

        mock_auth.refresh_access_token.assert_called_once()
        assert response.status_code == 200

    def test_post_token_refresh_failure_raises(self, trakt_client, mock_auth):
        """Test that POST raises a clear error when token refresh fails."""
        mock_response_401 = MagicMock()
        mock_response_401.status_code = 401

        mock_auth.refresh_access_token.return_value = False

        with patch.object(trakt_client._session, "post", return_value=mock_response_401):
            with pytest.raises(requests.exceptions.RequestException) as exc_info:
                trakt_client._post_with_token_refresh(
                    "https://api.trakt.tv/sync/history", {"movies": []}
                )

        assert "--force-auth" in str(exc_info.value)

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
        plex_client.server.playlists.return_value = []
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
        """An empty list with no playlist yet creates nothing instead of crashing (#26)."""
        plex_client.server.playlists.return_value = []

        playlist = plex_client.create_or_update_playlist("Test Playlist", [])

        assert playlist is None
        plex_client.server.createPlaylist.assert_not_called()

    def test_recreating_an_oversized_playlist_with_nothing_to_add_skips_the_create(
        self, plex_client
    ):
        """Deleting an oversized playlist with no new items must not raise (#26)."""
        oversized = MagicMock()
        oversized.items.return_value = [MagicMock() for _ in range(1500)]
        plex_client.server.playlists.side_effect = [[oversized], []]

        playlist = plex_client.create_or_update_playlist("Large Playlist", [])

        assert playlist is None
        oversized.delete.assert_called_once()
        plex_client.server.createPlaylist.assert_not_called()

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

        plex_client.server.playlists.return_value = [mock_playlist]

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
        # First lookup finds the oversized playlist; once deleted Plex no longer
        # lists it, so the recreate lookup must come back empty.
        plex_client.server.playlists.side_effect = [[mock_playlist], []]
        plex_client.server.createPlaylist.return_value = mock_playlist

        plex_client.create_or_update_playlist("Large Playlist", items)

        # Should have deleted old playlist and created new one
        mock_playlist.delete.assert_called_once()
        plex_client.server.createPlaylist.assert_called_once()

    def test_a_retried_create_reuses_the_playlist_the_failed_attempt_made(
        self, plex_client, monkeypatch
    ):
        """A 500 that already created the playlist must not produce a twin (#24)."""
        monkeypatch.setattr(clients.time, "sleep", lambda _seconds: None)

        movie = MagicMock()
        movie.TYPE = "movie"
        movie.ratingKey = "1"

        created = MagicMock()
        created.items.return_value = []

        # Lookup 1: absent before the create. Lookup 2: still absent when the
        # create is attempted. The attempt then raises 500 although Plex stored
        # the playlist, so the retry's lookup (3) finds it.
        plex_client.server.playlists.side_effect = [[], [], [created]]
        plex_client.server.createPlaylist.side_effect = clients.BadRequest(
            "(500) internal_server_error; http://plex/playlists?title=Test"
        )

        playlist = plex_client.create_or_update_playlist("Test Playlist", [movie])

        assert playlist is created
        assert plex_client.server.createPlaylist.call_count == 1

    def test_a_title_with_two_playlists_is_collapsed_to_one(self, plex_client):
        """A title that already resolves twice is cleaned up on the next sync (#24)."""
        kept = MagicMock()
        kept.ratingKey = "100"
        kept.items.return_value = []
        stale = MagicMock()
        stale.ratingKey = "101"
        stale.items.return_value = []

        plex_client.server.playlists.return_value = [kept, stale]

        playlist = plex_client.create_or_update_playlist("Trakt Shows - Popular", [])

        assert playlist is kept
        kept.delete.assert_not_called()
        stale.delete.assert_called_once()

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
        plex_client.server.playlists.return_value = [mock_playlist]

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

        plex_client.server.playlists.return_value = []
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

        plex_client.server.playlists.return_value = []
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
        plex_client.server.playlists.return_value = [mock_playlist]

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
        plex_client.server.playlists.side_effect = Exception("Server unavailable")

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
        # The meta stamp must be timezone-aware, or a machine east of Greenwich reads it
        # as hours in the future and finds nothing (issue #104).
        self._write_recent_meta(cache_manager)

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

    @staticmethod
    def _write_recent_meta(cache_manager):
        """Write a cache meta file with a 1-hour-old timestamp.

        The stamp is timezone-aware: a naive one is read as UTC by design, so on a machine
        east of Greenwich a naive "now" looked hours into the future and the update found
        nothing (issue #104).
        """
        from datetime import datetime, timedelta, timezone

        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        cache_manager.cache_meta_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_manager.cache_meta_file, "w") as f:
            json.dump(
                {
                    "version": clients.CACHE_VERSION,
                    "created": one_hour_ago,
                    "last_update": one_hour_ago,
                },
                f,
            )

    def test_incremental_update_handles_datetime_added_at(self, cache_manager, mock_plex_server):
        """Regression: plexapi >= 4.17 returns addedAt as datetime, not epoch int.

        Production log proof (2026-07-22):
            Could not get recentlyAdded for section 'Films':
            'datetime.datetime' object cannot be interpreted as an integer
        """
        from datetime import datetime

        movie_section = mock_plex_server.library.sections.return_value[0]
        movie = movie_section.recentlyAdded.return_value[0]
        # plexapi's utils.toDatetime returns a naive local datetime
        movie.addedAt = datetime.now()

        self._write_recent_meta(cache_manager)
        result = cache_manager._incremental_cache_update()

        assert result is True
        assert len(cache_manager.memory_cache["movies_list"]) == 1
        assert "tt1234567" in cache_manager.memory_cache["movies_by_imdb"]

    def test_incremental_update_handles_aware_datetime_added_at(
        self, cache_manager, mock_plex_server
    ):
        """Timezone-aware datetime addedAt must also be accepted."""
        from datetime import datetime, timezone

        movie_section = mock_plex_server.library.sections.return_value[0]
        movie = movie_section.recentlyAdded.return_value[0]
        movie.addedAt = datetime.now(timezone.utc)

        self._write_recent_meta(cache_manager)
        result = cache_manager._incremental_cache_update()

        assert result is True
        assert len(cache_manager.memory_cache["movies_list"]) == 1

    def test_incremental_update_skips_old_datetime_added_at(self, cache_manager, mock_plex_server):
        """Items with datetime addedAt older than the cutoff must be skipped."""
        from datetime import datetime, timedelta

        movie_section = mock_plex_server.library.sections.return_value[0]
        movie = movie_section.recentlyAdded.return_value[0]
        movie.addedAt = datetime.now() - timedelta(hours=5)

        self._write_recent_meta(cache_manager)
        result = cache_manager._incremental_cache_update()

        assert result is True
        assert len(cache_manager.memory_cache["movies_list"]) == 0


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_rate_limiter_enforces_min_interval(self):
        """Test that rate limiter enforces minimum interval between requests."""
        import time

        limiter = clients.RateLimiter(min_interval=0.1)

        start = time.time()
        limiter.wait()
        limiter.wait()
        elapsed = time.time() - start

        # Should have waited at least 0.1 seconds between the two calls
        assert elapsed >= 0.1

    def test_rate_limiter_allows_first_request_immediately(self):
        """Test that first request is not delayed."""
        import time

        limiter = clients.RateLimiter(min_interval=10.0)

        start = time.time()
        limiter.wait()
        elapsed = time.time() - start

        # First request should be immediate (no delay)
        assert elapsed < 0.1

    def test_rate_limiter_thread_safety(self):
        """Test that rate limiter is thread-safe."""
        import threading
        import time

        limiter = clients.RateLimiter(min_interval=0.05)
        call_times = []
        lock = threading.Lock()

        def worker():
            limiter.wait()
            with lock:
                call_times.append(time.time())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        start = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total_time = time.time() - start
        # 4 threads with 0.05s interval should take at least 0.15s
        # (first is immediate, then 3 more at 0.05s each)
        assert total_time >= 0.15

        # Verify intervals between calls
        with lock:
            sorted_times = sorted(call_times)
            for i in range(1, len(sorted_times)):
                gap = sorted_times[i] - sorted_times[i - 1]
                # Allow some tolerance for threading overhead
                assert gap >= 0.03


class TestTraktClientRetryLogic:
    """Tests for TraktClient retry and rate limiting logic."""

    @pytest.fixture
    def trakt_client(self, monkeypatch):
        """Create a TraktClient with mocked auth."""
        monkeypatch.setattr(clients, "TRAKT_CLIENT_ID", "test-client-id")
        monkeypatch.setattr(clients, "TRAKT_API_URL", "https://api.trakt.tv")
        auth = MagicMock()
        auth.get_headers.return_value = {"Authorization": "Bearer test-token"}
        return clients.TraktClient(auth)

    def test_request_with_retry_handles_rate_limit_429(self, trakt_client):
        """Test that 429 responses trigger retry with backoff."""
        mock_response_429 = MagicMock()
        mock_response_429.status_code = 429
        mock_response_429.headers = {"Retry-After": "1"}
        mock_response_429.raise_for_status = MagicMock()

        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.raise_for_status = MagicMock()

        with patch.object(
            trakt_client._session,
            "get",
            side_effect=[mock_response_429, mock_response_200],
        ):
            response = trakt_client._request_with_retry(
                "GET", "https://api.trakt.tv/test", headers={}
            )

            assert response.status_code == 200
            assert trakt_client._session.get.call_count == 2

    def test_request_with_retry_handles_server_error_500(self, trakt_client):
        """Test that 5xx responses trigger exponential backoff retry."""
        mock_response_500 = MagicMock()
        mock_response_500.status_code = 500
        mock_response_500.raise_for_status = MagicMock()

        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.raise_for_status = MagicMock()

        with patch.object(
            trakt_client._session,
            "get",
            side_effect=[mock_response_500, mock_response_200],
        ):
            response = trakt_client._request_with_retry(
                "GET", "https://api.trakt.tv/test", headers={}
            )

            assert response.status_code == 200
            assert trakt_client._session.get.call_count == 2

    def test_request_with_retry_handles_timeout(self, trakt_client):
        """Test that timeout exceptions trigger retry."""
        from requests.exceptions import Timeout

        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.raise_for_status = MagicMock()

        with patch.object(
            trakt_client._session,
            "get",
            side_effect=[Timeout("Request timed out"), mock_response_200],
        ):
            response = trakt_client._request_with_retry(
                "GET", "https://api.trakt.tv/test", headers={}
            )

            assert response.status_code == 200
            assert trakt_client._session.get.call_count == 2

    def test_request_with_retry_exhausts_retries(self, trakt_client):
        """Test that max retries are exhausted before raising."""
        from requests.exceptions import ConnectionError

        with patch.object(
            trakt_client._session,
            "get",
            side_effect=ConnectionError("Persistent error"),
        ):
            with pytest.raises(ConnectionError):
                trakt_client._request_with_retry("GET", "https://api.trakt.tv/test", headers={})

            # Should have attempted MAX_RETRIES times
            assert trakt_client._session.get.call_count == clients.MAX_RETRIES

    def test_rate_limiter_applied_to_requests(self, trakt_client):
        """Test that rate limiter is applied before each request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch.object(trakt_client._session, "get", return_value=mock_response):
            with patch.object(trakt_client._rate_limiter, "wait") as mock_wait:
                trakt_client._request_with_retry("GET", "https://api.trakt.tv/test", headers={})

                mock_wait.assert_called_once()


class TestTraktAuthRefresh:
    """Tests for TraktAuth token refresh behavior."""

    def test_refresh_access_token_clears_tokens_on_400(self, monkeypatch):
        """Test that refresh failure with 400 clears stored tokens."""
        monkeypatch.setattr(clients, "TRAKT_CLIENT_ID", "test-client-id")
        monkeypatch.setattr(clients, "TRAKT_CLIENT_SECRET", "test-client-secret")
        monkeypatch.setattr(clients, "TRAKT_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob")
        monkeypatch.setattr(clients, "TRAKT_API_URL", "https://api.trakt.tv")

        auth = clients.TraktAuth()
        auth.access_token = "old-access"
        auth.refresh_token = "old-refresh"

        # Mock a 400 response
        mock_response = MagicMock()
        mock_response.status_code = 400

        with patch("requests.Session.post", return_value=mock_response):
            result = auth.refresh_access_token()

        assert result is False
        assert auth.access_token is None
        assert auth.refresh_token is None


class TestTraktClientPagination:
    """Coverage for the shared _paginated_get helper (TODO audit D3/B2)."""

    @pytest.fixture
    def mock_auth(self):
        auth = MagicMock()
        auth.get_headers.return_value = {"Authorization": "Bearer test"}
        return auth

    def _response(self, data, page_count=None):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = data
        headers = {}
        if page_count is not None:
            headers["X-Pagination-Page-Count"] = str(page_count)
        r.headers = headers
        r.content = b"[]"
        return r

    def test_single_page(self, mock_auth):
        """Short page -> stop after one request."""
        client = clients.TraktClient(mock_auth)
        data = [{"movie": {"title": "A"}}]
        with patch.object(client._session, "get", return_value=self._response(data)):
            result = client.get_watched_movies()
        assert len(result) == 1

    def test_multiple_pages(self, mock_auth):
        """Page-count header drives pagination across pages."""
        client = clients.TraktClient(mock_auth)
        page1 = [{"movie": {"title": f"M{i}"}} for i in range(100)]
        page2 = [{"movie": {"title": "M100"}}]
        with patch.object(
            client._session,
            "get",
            side_effect=[self._response(page1, page_count=2), self._response(page2, page_count=2)],
        ):
            result = client.get_watched_movies()
        assert len(result) == 101

    def test_page_count_header_respected(self, mock_auth):
        """Even with full pages, the page-count header stops pagination."""
        client = clients.TraktClient(mock_auth)
        page1 = [{"movie": {"title": f"M{i}"}} for i in range(100)]
        with patch.object(client._session, "get", return_value=self._response(page1, page_count=1)):
            result = client.get_watched_movies()
        assert len(result) == 100

    def test_bad_page_count_header_falls_back(self, mock_auth):
        """Non-integer page-count header falls back to short-page detection."""
        client = clients.TraktClient(mock_auth)
        data = [{"movie": {"title": "A"}}]
        r = self._response(data)
        r.headers = {"X-Pagination-Page-Count": "not-a-number"}
        with patch.object(client._session, "get", return_value=r):
            result = client.get_watched_movies()
        assert len(result) == 1

    def test_list_items_uses_helper(self, mock_auth):
        """get_list_items paginates through the shared helper."""
        client = clients.TraktClient(mock_auth)
        data = [{"type": "movie", "movie": {"title": "A", "ids": {}}}]
        with patch.object(client._session, "get", return_value=self._response(data)):
            result = client.get_list_items("user", "list-id")
        assert len(result) == 1


class TestPlexClientBatchOps:
    """Coverage for PlexClient batch operations (TODO audit D3)."""

    @pytest.fixture
    def plex_client(self):
        server = MagicMock()
        cache = MagicMock()
        cache.memory_cache = {}
        return clients.PlexClient(server, cache)

    def test_batch_mark_watched_partial_failure(self, plex_client):
        """Failures in mark_as_watched are aggregated."""
        plex_client.mark_as_watched = MagicMock(side_effect=[True, False, Exception("boom")])

        results = plex_client.batch_mark_as_watched([1, 2, 3])

        assert results["success"] == 1
        assert results["failed"] == 2
        assert len(results["errors"]) == 2

    def test_batch_mark_unwatched_partial_failure(self, plex_client):
        """Failures in mark_as_unwatched are aggregated."""
        plex_client.mark_as_unwatched = MagicMock(side_effect=[True, False])

        results = plex_client.batch_mark_as_unwatched([1, 2])

        assert results["success"] == 1
        assert results["failed"] == 1

    def test_batch_set_playback_progress_partial(self, plex_client):
        """Partial failures in batch_set_playback_progress are aggregated."""
        plex_client.set_playback_progress = MagicMock(side_effect=[True, False, Exception("x")])

        results = plex_client.batch_set_playback_progress(
            [
                {"rating_key": 1, "view_offset_ms": 100},
                {"rating_key": 2, "view_offset_ms": 200},
                {"rating_key": 3, "view_offset_ms": 300},
            ]
        )

        assert results["success"] == 1
        assert results["failed"] == 2
        assert len(results["errors"]) == 2


class TestTraktAuthRefreshPaths:
    """Coverage for refresh_access_token paths (TODO audit D3)."""

    def test_no_refresh_token_returns_false(self, monkeypatch):
        """Missing refresh token -> False."""
        monkeypatch.setattr(clients, "TRAKT_ACCESS_TOKEN", None)
        monkeypatch.setattr(clients, "TRAKT_REFRESH_TOKEN", None)
        auth = clients.TraktAuth()
        auth.refresh_token = None

        assert auth.refresh_access_token() is False

    def test_refresh_success(self, monkeypatch, tmp_path):
        """Successful refresh updates tokens and returns True."""
        monkeypatch.setattr(clients, "TRAKT_ACCESS_TOKEN", None)
        monkeypatch.setattr(clients, "TRAKT_REFRESH_TOKEN", None)
        monkeypatch.setattr(clients, "TRAKT_CLIENT_ID", "cid")
        monkeypatch.setattr(clients, "TRAKT_CLIENT_SECRET", "csec")
        monkeypatch.setattr(clients, "TRAKT_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob")
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("TRAKT_ACCESS_TOKEN=old\nTRAKT_REFRESH_TOKEN=old\n")

        auth = clients.TraktAuth()
        auth.refresh_token = "valid-refresh"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "a" * 32,
            "refresh_token": "b" * 32,
        }

        with patch.object(clients.requests.Session, "post", return_value=mock_response):
            result = auth.refresh_access_token()

        assert result is True
        assert auth.access_token == "a" * 32

    def test_refresh_400_clears_tokens(self, monkeypatch):
        """HTTP 400 clears stored tokens to force re-auth."""
        monkeypatch.setattr(clients, "TRAKT_ACCESS_TOKEN", None)
        monkeypatch.setattr(clients, "TRAKT_REFRESH_TOKEN", None)
        monkeypatch.setattr(clients, "TRAKT_CLIENT_ID", "cid")
        monkeypatch.setattr(clients, "TRAKT_CLIENT_SECRET", "csec")
        monkeypatch.setattr(clients, "TRAKT_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob")

        auth = clients.TraktAuth()
        auth.access_token = "old-access"
        auth.refresh_token = "expired-refresh"

        mock_response = MagicMock()
        mock_response.status_code = 400

        with patch.object(clients.requests.Session, "post", return_value=mock_response):
            result = auth.refresh_access_token()

        assert result is False
        assert auth.access_token is None
        assert auth.refresh_token is None

    def test_refresh_other_status_returns_false(self, monkeypatch):
        """Other non-200/400 status returns False without clearing tokens."""
        monkeypatch.setattr(clients, "TRAKT_ACCESS_TOKEN", None)
        monkeypatch.setattr(clients, "TRAKT_REFRESH_TOKEN", None)
        monkeypatch.setattr(clients, "TRAKT_CLIENT_ID", "cid")
        monkeypatch.setattr(clients, "TRAKT_CLIENT_SECRET", "csec")
        monkeypatch.setattr(clients, "TRAKT_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob")

        auth = clients.TraktAuth()
        auth.access_token = "old-access"
        auth.refresh_token = "refresh"

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch.object(clients.requests.Session, "post", return_value=mock_response):
            result = auth.refresh_access_token()

        assert result is False
        assert auth.access_token == "old-access"

    def test_refresh_request_exception(self, monkeypatch):
        """Network error during refresh returns False."""
        monkeypatch.setattr(clients, "TRAKT_ACCESS_TOKEN", None)
        monkeypatch.setattr(clients, "TRAKT_REFRESH_TOKEN", None)
        monkeypatch.setattr(clients, "TRAKT_CLIENT_ID", "cid")
        monkeypatch.setattr(clients, "TRAKT_CLIENT_SECRET", "csec")
        monkeypatch.setattr(clients, "TRAKT_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob")

        auth = clients.TraktAuth()
        auth.refresh_token = "refresh"

        with patch.object(
            clients.requests.Session,
            "post",
            side_effect=clients.requests.exceptions.ConnectionError("down"),
        ):
            result = auth.refresh_access_token()

        assert result is False


class TestTraktClientCollectionAndWatchlist:
    """Coverage for collection/watchlist fetching (TODO audit D3)."""

    @pytest.fixture
    def mock_auth(self):
        auth = MagicMock()
        auth.get_headers.return_value = {"Authorization": "Bearer test"}
        return auth

    def test_get_collection_movies(self, mock_auth):
        client = clients.TraktClient(mock_auth)
        data = [
            {
                "movie": {
                    "title": "Heat",
                    "year": 1995,
                    "ids": {"imdb": "tt0113277", "tmdb": 949},
                }
            }
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = data

        with patch.object(client._session, "get", return_value=mock_response):
            result = client.get_collection("movies")

        assert len(result) == 1
        assert result[0]["type"] == "movie"

    def test_get_collection_invalid_type(self, mock_auth):
        client = clients.TraktClient(mock_auth)
        with pytest.raises(ValueError):
            client.get_collection("podcasts")

    def test_get_collection_shows(self, mock_auth):
        client = clients.TraktClient(mock_auth)
        data = [
            {
                "show": {
                    "title": "Breaking Bad",
                    "year": 2008,
                    "ids": {"imdb": "tt0903747"},
                }
            }
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = data

        with patch.object(client._session, "get", return_value=mock_response):
            result = client.get_collection("shows")

        assert len(result) == 1
        assert result[0]["type"] == "show"

    def test_get_watchlist(self, mock_auth):
        client = clients.TraktClient(mock_auth)
        data = [
            {
                "type": "movie",
                "listed_at": "2024-01-01T00:00:00Z",
                "movie": {"title": "Heat", "year": 1995, "ids": {"imdb": "tt0113277"}},
            }
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = data

        with patch.object(client._session, "get", return_value=mock_response):
            result = client.get_watchlist()

        assert len(result) == 1
        assert result[0]["type"] == "movie"
        assert result[0]["listed_at"] == "2024-01-01T00:00:00Z"


class TestTraktClientBatchHistory:
    """Coverage for batch history operations (TODO audit D3)."""

    @pytest.fixture
    def mock_auth(self):
        auth = MagicMock()
        auth.get_headers.return_value = {"Authorization": "Bearer test"}
        return auth

    def test_add_to_history_batches(self, mock_auth):
        """Movies are split into batches of the given size."""
        client = clients.TraktClient(mock_auth)
        movies = [{"ids": {"imdb": f"tt{i}"}} for i in range(3)]

        def make_response(*args, **kwargs):
            r = MagicMock()
            r.status_code = 200
            batch = kwargs.get("json", {}).get("movies", [])
            r.json.return_value = {"added": {"movies": len(batch), "episodes": 0}}
            return r

        with patch.object(client._session, "post", side_effect=make_response):
            result = client.add_to_history(movies=movies, batch_size=2)

        assert result is not None
        assert result["added"]["movies"] == 3

    def test_remove_from_history_episodes(self, mock_auth):
        """Episodes are removed in batches."""
        client = clients.TraktClient(mock_auth)
        episodes = [{"ids": {"imdb": "tt1"}, "season": 1, "number": i} for i in range(1, 4)]

        def make_response(*args, **kwargs):
            r = MagicMock()
            r.status_code = 200
            batch = kwargs.get("json", {}).get("episodes", [])
            r.json.return_value = {"deleted": {"movies": 0, "episodes": len(batch)}}
            return r

        with patch.object(client._session, "post", side_effect=make_response):
            result = client.remove_from_history(episodes=episodes, batch_size=2)

        assert result is not None
        assert result["deleted"]["episodes"] == 3

    def test_batch_operation_request_failure(self, mock_auth):
        """A failing batch is skipped and counted as an error."""
        client = clients.TraktClient(mock_auth)
        movies = [{"ids": {"imdb": "tt1"}}]

        with patch.object(
            client._session,
            "post",
            side_effect=clients.requests.exceptions.ConnectionError("down"),
        ):
            result = client.add_to_history(movies=movies, batch_size=2)

        # All batches failed -> None
        assert result is None

    def test_batch_operation_no_items(self, mock_auth):
        """No movies/episodes -> None without any API call."""
        client = clients.TraktClient(mock_auth)

        with patch.object(client._session, "post") as mock_post:
            result = client.add_to_history()

        assert result is None
        mock_post.assert_not_called()


class TestPlexClientOrphanedCleanup:
    """Coverage for cleanup_orphaned_playlists (TODO audit D3)."""

    @pytest.fixture
    def plex_client(self, tmp_path, monkeypatch):
        server = MagicMock()
        cache = MagicMock()
        cache.memory_cache = {}
        return clients.PlexClient(server, cache)

    def _make_playlist(self, title):
        p = MagicMock()
        p.title = title
        return p

    def test_no_orphans(self, plex_client):
        plex_client.server.playlists.return_value = [self._make_playlist("Active")]
        config = {"managed_playlists": ["Active"]}

        deleted = plex_client.cleanup_orphaned_playlists(["Active"], config)

        assert deleted == []
        assert config["managed_playlists"] == ["Active"]

    def test_deletes_orphaned(self, plex_client):
        orphan = self._make_playlist("Old")
        plex_client.server.playlists.return_value = [orphan, self._make_playlist("Active")]
        config = {"managed_playlists": ["Active", "Old"]}

        deleted = plex_client.cleanup_orphaned_playlists(["Active"], config)

        assert deleted == ["Old"]
        orphan.delete.assert_called_once()
        assert config["managed_playlists"] == ["Active"]

    def test_dry_run_returns_to_delete(self, plex_client):
        orphan = self._make_playlist("Old")
        plex_client.server.playlists.return_value = [orphan]
        config = {"managed_playlists": ["Old"]}

        to_delete = plex_client.cleanup_orphaned_playlists(["Active"], config, dry_run=True)

        assert to_delete == ["Old"]
        orphan.delete.assert_not_called()
        # Config must not be modified in dry-run mode
        assert config["managed_playlists"] == ["Old"]

    def test_deletes_blacklisted(self, plex_client):
        from plexapi.exceptions import NotFound

        plex_client.server.playlist.side_effect = NotFound("nope")
        plex_client.server.playlists.return_value = [self._make_playlist("Active")]
        config = {
            "managed_playlists": ["Active", "Blacklisted"],
            "blacklisted_lists": ["Blacklisted"],
        }

        deleted = plex_client.cleanup_orphaned_playlists(["Active"], config)

        # Blacklisted playlist not found -> skipped gracefully
        assert deleted == []

    def test_delete_error_handled(self, plex_client):
        orphan = self._make_playlist("Old")
        orphan.delete.side_effect = Exception("boom")
        plex_client.server.playlists.return_value = [orphan]
        config = {"managed_playlists": ["Old"]}

        deleted = plex_client.cleanup_orphaned_playlists([], config)

        assert deleted == []


class TestBatchHistoryFailureReporting:
    """Regression tests for issue #9: per-batch failures must be visible in
    the combined result instead of silently swallowed."""

    @pytest.fixture
    def mock_auth(self):
        auth = MagicMock()
        auth.get_headers.return_value = {"Authorization": "Bearer test"}
        return auth

    def test_partial_batch_failure_reported(self, mock_auth):
        """One failing movie batch yields failure counts + payloads in the result."""
        client = clients.TraktClient(mock_auth)
        movies = [{"ids": {"imdb": f"tt{i}"}} for i in range(4)]

        def make_response(*args, **kwargs):
            batch = kwargs.get("json", {}).get("movies", [])
            # Second batch fails persistently (survives the client's retry layer)
            if any(m["ids"]["imdb"] in ("tt2", "tt3") for m in batch):
                raise clients.requests.exceptions.ConnectionError("transient outage")
            r = MagicMock()
            r.status_code = 201
            r.json.return_value = {"added": {"movies": len(batch), "episodes": 0}}
            return r

        with patch.object(client._session, "post", side_effect=make_response):
            result = client.add_to_history(movies=movies, batch_size=2)

        # First batch succeeded (2 movies), second failed (2 movies)
        assert result is not None
        assert result["added"]["movies"] == 2
        assert result["failed"]["movies"] == 2
        assert result["failed"]["episodes"] == 0
        assert result["failed_payloads"]["movies"] == movies[2:]
        assert result["failed_payloads"]["episodes"] == []

    def test_episode_batch_failure_reported(self, mock_auth):
        """Failing episode batches are counted separately."""
        client = clients.TraktClient(mock_auth)
        episodes = [{"ids": {}, "season": 1, "number": i} for i in range(2)]

        with patch.object(
            client._session,
            "post",
            side_effect=clients.requests.exceptions.ConnectionError("down"),
        ):
            result = client.remove_from_history(episodes=episodes, batch_size=1)

        assert result is None  # all batches failed -> no successful response

    def test_all_success_reports_zero_failed(self, mock_auth):
        """A fully successful run reports failed = 0 for both media types."""
        client = clients.TraktClient(mock_auth)
        movies = [{"ids": {"imdb": "tt1"}}]

        def make_response(*args, **kwargs):
            r = MagicMock()
            r.status_code = 201
            r.json.return_value = {"added": {"movies": 1, "episodes": 0}}
            return r

        with patch.object(client._session, "post", side_effect=make_response):
            result = client.add_to_history(movies=movies)

        assert result["failed"] == {"movies": 0, "episodes": 0}
        assert "failed_payloads" not in result


class TestPlexBatchFailedKeys:
    """Plex batch operations must identify exactly which items failed."""

    @pytest.fixture
    def plex_client(self):
        client = MagicMock(spec=clients.PlexClient)
        return client

    def test_failed_rating_keys_collected_watched(self):
        """mark_as_watched failures are reported per rating key."""
        client = clients.PlexClient.__new__(clients.PlexClient)
        with patch.object(
            client,
            "mark_as_watched",
            side_effect=lambda rk: rk != 2,  # key 2 fails
        ):
            results = client.batch_mark_as_watched([1, 2, 3])

        assert results["success"] == 2
        assert results["failed"] == 1
        assert results["failed_rating_keys"] == [2]

    def test_failed_rating_keys_collected_unwatched(self):
        """mark_as_unwatched exceptions are reported per rating key."""
        client = clients.PlexClient.__new__(clients.PlexClient)

        def mark(rk):
            if rk == "rk-bad":
                raise RuntimeError("plex hiccup")
            return True

        with patch.object(client, "mark_as_unwatched", side_effect=mark):
            results = client.batch_mark_as_unwatched(["rk-ok", "rk-bad"])

        assert results["success"] == 1
        assert results["failed_rating_keys"] == ["rk-bad"]


class TestTraktResponseCounting:
    """Trakt answers with counts for writes and payload lists for misses (#38)."""

    @pytest.fixture
    def mock_auth(self):
        auth = MagicMock()
        auth.get_headers.return_value = {"Authorization": "Bearer test"}
        return auth

    def test_count_trakt_items_handles_both_shapes(self):
        """Counts, payload lists and missing values all count correctly."""
        assert clients.count_trakt_items(3) == 3
        assert clients.count_trakt_items(0) == 0
        assert clients.count_trakt_items([{"ids": {}}, {"ids": {}}]) == 2
        assert clients.count_trakt_items([]) == 0
        assert clients.count_trakt_items(None) == 0
        assert clients.count_trakt_items("unexpected") == 0

    def test_not_found_payload_lists_are_counted(self, mock_auth):
        """A not_found response with payload lists must not raise on sum()."""
        client = clients.TraktClient(mock_auth)
        movies = [{"ids": {"imdb": "tt1"}}, {"ids": {"imdb": "tt2"}}]

        def make_response(*args, **kwargs):
            r = MagicMock()
            r.status_code = 201
            r.json.return_value = {
                "added": {"movies": 1, "episodes": 0},
                "not_found": {"movies": [{"ids": {"imdb": "tt2"}}], "episodes": []},
            }
            return r

        with patch.object(client._session, "post", side_effect=make_response):
            result = client.add_to_history(movies=movies)

        assert result is not None
        assert result["added"]["movies"] == 1
        assert result["not_found"] == {"movies": 1, "episodes": 0}

    def test_per_item_watched_at_reaches_the_request(self, mock_auth):
        """Per-item watched_at survives batching into the POST payload."""
        client = clients.TraktClient(mock_auth)
        movies = [
            {"ids": {"imdb": "tt1"}, "watched_at": "2024-06-15T12:00:00.000Z"},
        ]
        captured = {}

        def make_response(*args, **kwargs):
            captured.update(kwargs.get("json") or {})
            r = MagicMock()
            r.status_code = 201
            r.json.return_value = {"added": {"movies": 1, "episodes": 0}}
            return r

        with patch.object(client._session, "post", side_effect=make_response):
            client.add_to_history(movies=movies)

        assert captured["movies"][0]["watched_at"] == "2024-06-15T12:00:00.000Z"

    def test_section_arriving_as_a_list_does_not_raise(self, mock_auth):
        """A bare list in place of a section block counts 0, never crashes."""
        client = clients.TraktClient(mock_auth)
        movies = [{"ids": {"imdb": "tt1"}}]

        def make_response(*args, **kwargs):
            r = MagicMock()
            r.status_code = 201
            r.json.return_value = {
                "added": [{"ids": {"imdb": "tt1"}}],
                "not_found": {"movies": [{"ids": {"imdb": "tt2"}}], "episodes": []},
            }
            return r

        with patch.object(client._session, "post", side_effect=make_response):
            result = client.add_to_history(movies=movies)

        # A bare list has no per-media split, so it is not counted twice
        assert result["added"] == {"movies": 0, "episodes": 0}
        assert result["not_found"] == {"movies": 1, "episodes": 0}


class TestShowCountersAndEpisodeSnapshots:
    """Issue #21: the cheap show-level signal and the snapshot store it feeds."""

    @pytest.fixture
    def cache_manager(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / ".traktor_cache"
        cache_dir.mkdir()
        monkeypatch.setattr(clients, "CACHE_DIR", cache_dir)
        server = MagicMock()
        server.library.sections.return_value = []
        return clients.CacheManager(server)

    @staticmethod
    def _show(rating_key, watched=2, leaf=3, last_viewed=None):
        show = MagicMock()
        show.ratingKey = rating_key
        show.viewedLeafCount = watched
        show.leafCount = leaf
        show.lastViewedAt = last_viewed
        return show

    def test_show_counters_normalizes_last_viewed(self):
        """An aware datetime is stored as the same instant in UTC ISO (issue #104)."""
        counters = clients.PlexClient.show_counters(
            self._show(1, 2, 3, datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
        )

        assert counters["viewedLeafCount"] == 2
        assert counters["leafCount"] == 3
        assert counters["lastViewedAt"].endswith("+00:00")
        assert counters["lastViewedAt"].startswith("2024-06-15T12:00:00")

    def test_show_counters_without_timestamp(self):
        """A never-watched show reports None rather than a fake date."""
        counters = clients.PlexClient.show_counters(self._show(1, 0, 3, None))

        assert counters["lastViewedAt"] is None

    def test_get_show_counters_reads_only_show_sections(self, cache_manager):
        """One paged read per show section; movie sections are not touched."""
        show_section = MagicMock()
        show_section.type = "show"
        show_section.title = "TV"
        show_section.all.return_value = [self._show(700), self._show(701)]
        movie_section = MagicMock()
        movie_section.type = "movie"
        movie_section.title = "Films"

        server = MagicMock()
        server.library.sections.return_value = [movie_section, show_section]
        plex_client = clients.PlexClient(server, cache_manager)

        counters = plex_client.get_show_counters()

        assert set(counters) == {700, 701}
        show_section.all.assert_called_once_with(container_size=clients.CONTAINER_SIZE)
        movie_section.all.assert_not_called()

    def test_get_show_counters_skips_an_unreadable_section(self, cache_manager):
        """A failing section is left out, so its shows are re-read, not trusted."""
        broken = MagicMock()
        broken.type = "show"
        broken.title = "Broken"
        broken.all.side_effect = requests.exceptions.ReadTimeout("slow")
        healthy = MagicMock()
        healthy.type = "show"
        healthy.title = "TV"
        healthy.all.return_value = [self._show(700)]

        server = MagicMock()
        server.library.sections.return_value = [broken, healthy]
        plex_client = clients.PlexClient(server, cache_manager)

        counters = plex_client.get_show_counters()

        assert set(counters) == {700}

    def test_snapshot_roundtrip_and_flush(self, cache_manager):
        """Rows and counters are readable back, and flush persists them."""
        rows = [[1, 1, 7001, 1, "2024-05-01T20:00:00+00:00"]]
        counters = {
            "viewedLeafCount": 2,
            "leafCount": 3,
            "lastViewedAt": "2024-05-01T20:00:00+00:00",
        }

        cache_manager.record_show_episodes(700, counters, rows)

        # Rating key works as int and as the JSON string
        assert cache_manager.get_show_episodes(700)["episodes"] == rows
        assert cache_manager.get_show_episodes("700")["viewedLeafCount"] == 2

        cache_manager.flush_show_episodes()

        with gzip.open(cache_manager.cache_file, "rt", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["show_episodes"]["700"]["episodes"] == rows

    def test_flush_writes_nothing_when_no_snapshot_changed(self, cache_manager):
        """A pull that only reuses snapshots costs no cache write."""
        counters = {"viewedLeafCount": 1, "leafCount": 1, "lastViewedAt": None}
        cache_manager.record_show_episodes(700, counters, [[1, 1, 7001, 1, None]])
        cache_manager.flush_show_episodes()
        cache_manager.cache_file.unlink()

        cache_manager.flush_show_episodes()

        assert not cache_manager.cache_file.exists()

    def test_forget_drops_the_snapshot_and_marks_it_dirty(self, cache_manager):
        """Forgetting a show persists the removal, not just the memory change."""
        counters = {"viewedLeafCount": 1, "leafCount": 1, "lastViewedAt": None}
        cache_manager.record_show_episodes(700, counters, [])
        cache_manager.flush_show_episodes()

        cache_manager.forget_show_episodes(700)

        assert cache_manager.get_show_episodes(700) is None
        cache_manager.flush_show_episodes()
        with gzip.open(cache_manager.cache_file, "rt", encoding="utf-8") as f:
            assert "700" not in json.load(f)["show_episodes"]

    def test_snapshots_survive_a_cache_rebuild(self, cache_manager):
        """A library rescan must not throw away every show's episode state."""
        counters = {"viewedLeafCount": 1, "leafCount": 1, "lastViewedAt": None}
        cache_manager.record_show_episodes(700, counters, [[1, 1, 7001, 1, None]])

        cache_manager._build_cache()

        assert cache_manager.get_show_episodes(700)["episodes"] == [[1, 1, 7001, 1, None]]


# ---------------------------------------------------------------------------
# Issue #41: a cached rating key is used to build a Plex request URL
# ---------------------------------------------------------------------------


def _empty_cache(cache) -> None:
    cache.memory_cache = {
        "movies_by_imdb": {},
        "movies_by_tmdb": {},
        "shows_by_imdb": {},
        "shows_by_tmdb": {},
        "movies_list": [],
        "shows_list": [],
        "by_rating_key": {},
        "show_episodes": {},
    }


class TestCachedRatingKeyValidation:
    """A rating key from disk is untrusted input, not a URL fragment (#41)."""

    @pytest.fixture
    def server(self):
        server = MagicMock()
        server.library.sections.return_value = []
        return server

    def test_legacy_cache_with_a_url_rating_key_is_dropped(self, server, tmp_path, monkeypatch):
        """A doctored legacy cache cannot keep an entry that redirects a lookup."""
        monkeypatch.setattr(clients, "CACHE_DIR", tmp_path)
        poisoned = {
            "movies_by_imdb": {
                "tt0000001": {
                    "title": "Injected",
                    "year": 2026,
                    "ratingKey": "@127.0.0.1:9/steal",
                }
            },
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
            "shows_by_tmdb": {},
            "movies_list": [{"title": "Injected", "year": 2026, "ratingKey": "@127.0.0.1:9/steal"}],
            "shows_list": [],
            "by_rating_key": {},
        }
        (tmp_path / "plex_library_cache.json").write_text(json.dumps(poisoned), encoding="utf-8")

        cache = clients.CacheManager(server)

        assert cache.load_cache() is True
        assert cache.find_movie_by_imdb("tt0000001") is None
        assert cache.memory_cache["movies_list"] == []

    def test_compressed_cache_with_a_url_rating_key_is_dropped(self, server, tmp_path, monkeypatch):
        """The compressed path sanitizes its entries too."""
        monkeypatch.setattr(clients, "CACHE_DIR", tmp_path)
        cache = clients.CacheManager(server)
        _empty_cache(cache)
        cache.memory_cache["shows_by_imdb"] = {
            "tt0000002": {"title": "Injected", "year": 2026, "ratingKey": "../steal"}
        }

        assert cache._validate_cache_structure() is True
        assert cache.memory_cache["shows_by_imdb"] == {}

    def test_integer_rating_keys_are_kept(self, server):
        cache = clients.CacheManager(server)
        _empty_cache(cache)
        cache.memory_cache["movies_by_imdb"] = {
            "tt1": {"title": "Kept", "year": 2000, "ratingKey": 42}
        }

        assert cache._validate_cache_structure() is True
        assert cache.memory_cache["movies_by_imdb"]["tt1"]["ratingKey"] == 42

    def test_fetch_refuses_a_non_integer_rating_key(self, server):
        """The sink refuses it too, so a value injected at runtime cannot leak."""
        cache = clients.CacheManager(server)
        _empty_cache(cache)
        plex = clients.PlexClient(server, cache)

        assert plex._get_plex_item("@evil.example/steal") is None
        server.fetchItem.assert_not_called()

    def test_watched_check_refuses_a_non_integer_rating_key(self, server):
        """The watched-status fallback reaches the same fetchItem sink (#41)."""
        cache = clients.CacheManager(server)
        _empty_cache(cache)
        plex = clients.PlexClient(server, cache)

        assert plex.is_watched("@evil.example/steal") == (False, None)
        server.fetchItem.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #55: a warm cache fetched the same item again for every occurrence
# ---------------------------------------------------------------------------


def test_fetched_item_is_reused_within_the_run():
    server = MagicMock()
    server.library.sections.return_value = []
    item = object()
    server.fetchItem.return_value = item
    cache = clients.CacheManager(server)
    _empty_cache(cache)
    plex = clients.PlexClient(server, cache)

    assert plex._get_plex_item(7) is item
    assert plex._get_plex_item(7) is item
    assert server.fetchItem.call_count == 1


# ---------------------------------------------------------------------------
# Issue #54: a show repeated across playlists was resolved every time
# ---------------------------------------------------------------------------


def test_first_episode_is_resolved_once_per_run():
    server = MagicMock()
    server.library.sections.return_value = []
    cache = clients.CacheManager(server)
    _empty_cache(cache)
    plex = clients.PlexClient(server, cache)

    episode = MagicMock()
    season = MagicMock()
    season.seasonNumber = 1
    season.episodes.return_value = [episode]
    show = MagicMock()
    show.ratingKey = 42
    show.title = "Show"
    show.seasons.return_value = [season]

    assert plex.resolve_first_episode(show) == (episode, "S01E01")
    assert plex.resolve_first_episode(show) == (episode, "S01E01")
    assert show.seasons.call_count == 1


# ---------------------------------------------------------------------------
# Issue #46: a full first page is not the last page
# ---------------------------------------------------------------------------


def _page(items, headers=None):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = items
    response.headers = headers or {}
    response.content = b"[]"
    return response


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Pagination-Page-Count": "not-a-number"}, {"X-Pagination-Page-Count": ""}],
)
def test_pagination_continues_when_page_count_is_unknown(headers):
    """A missing or unreadable page count must not cut the list at 100 (#46)."""
    auth = MagicMock()
    auth.get_headers.return_value = {}
    client = clients.TraktClient(auth)
    first = [{"movie": {"title": f"M{i}"}} for i in range(100)]
    second = [{"movie": {"title": f"M{i}"}} for i in range(100, 150)]

    with patch.object(client._rate_limiter, "wait", return_value=None):
        with patch.object(
            client._session,
            "get",
            side_effect=[_page(first, headers), _page(second, headers)],
        ):
            result = client.get_watched_movies()

    assert len(result) == 150


# ---------------------------------------------------------------------------
# Issue #51: Retry-After is not always a bare integer
# ---------------------------------------------------------------------------


def test_parse_retry_after_handles_every_documented_form():
    assert clients.parse_retry_after("7", 2) == 7.0
    assert clients.parse_retry_after(None, 2) == 2
    assert clients.parse_retry_after("", 2) == 2
    assert clients.parse_retry_after("1.5", 2) == 1.5
    # An HTTP-date in the past means "now", never a negative sleep.
    assert clients.parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", 2) == 0.0
    assert clients.parse_retry_after("nonsense", 2) == 2
    assert clients.parse_retry_after("99999", 2) == clients.MAX_RETRY_AFTER_SECONDS


@pytest.mark.parametrize("header", ["Wed, 21 Oct 2015 07:28:00 GMT", "1.5", "", "nonsense"])
def test_unusual_retry_after_values_still_retry(header):
    auth = MagicMock()
    auth.get_headers.return_value = {}
    client = clients.TraktClient(auth)
    limited = MagicMock()
    limited.status_code = 429
    limited.headers = {"Retry-After": header}
    ok = MagicMock()
    ok.status_code = 200
    ok.headers = {}

    with patch.object(client._rate_limiter, "wait", return_value=None):
        with patch.object(client._session, "get", side_effect=[limited, ok]):
            with patch("traktor.clients.time.sleep", return_value=None):
                result = client._request_with_retry("GET", "https://example.invalid/x")

    assert result.status_code == 200


# ---------------------------------------------------------------------------
# Issue #50 and #44: the .env token store
# ---------------------------------------------------------------------------


class TestTokenFile:
    """Token persistence must survive duplicates and prove a revocation (#50, #44)."""

    @pytest.fixture
    def env_file(self, tmp_path, monkeypatch):
        target = tmp_path / ".env"
        # Never resolve to the real /app/.env of a Docker host.
        monkeypatch.setattr(clients.TraktAuth, "env_file_path", staticmethod(lambda: target))
        monkeypatch.chdir(tmp_path)
        return target

    def test_duplicate_token_keys_do_not_shadow_the_saved_value(self, env_file):
        env_file.write_text(
            "TRAKT_ACCESS_TOKEN=old-access-0001\n"
            "TRAKT_ACCESS_TOKEN=old-access-0002\n"
            "TRAKT_REFRESH_TOKEN=old-refresh-0001\n"
            "TRAKT_REFRESH_TOKEN=old-refresh-0002\n",
            encoding="utf-8",
        )
        auth = clients.TraktAuth()
        auth.access_token = "new-access-token-0001"
        auth.refresh_token = "new-refresh-token-0001"

        assert auth.save_tokens() is True

        from dotenv import dotenv_values

        effective = dotenv_values(env_file)
        assert effective["TRAKT_ACCESS_TOKEN"] == "new-access-token-0001"
        assert effective["TRAKT_REFRESH_TOKEN"] == "new-refresh-token-0001"
        assert not any("old-access-0002" in line for line in env_file.read_text().splitlines())

    @pytest.mark.parametrize("status", [400, 401, 403])
    def test_rejected_refresh_removes_the_credential(self, status, env_file):
        env_file.write_text(
            "TRAKT_ACCESS_TOKEN=revoked-access-0001\n" "TRAKT_REFRESH_TOKEN=revoked-refresh-0001\n",
            encoding="utf-8",
        )
        auth = clients.TraktAuth()
        auth.access_token = "revoked-access-0001"
        auth.refresh_token = "revoked-refresh-0001"
        response = MagicMock()
        response.status_code = status

        with patch.object(clients.requests.Session, "post", return_value=response):
            assert auth.refresh_access_token() is False

        assert auth.access_token is None
        assert auth.refresh_token is None
        assert auth.has_valid_tokens() is False
        remaining = env_file.read_text(encoding="utf-8")
        assert "revoked-access-0001" not in remaining
        assert "revoked-refresh-0001" not in remaining

    def test_other_refresh_failures_keep_the_credential(self, env_file):
        """A 500 says nothing about the credential, so it stays."""
        env_file.write_text(
            "TRAKT_ACCESS_TOKEN=still-access-0001\nTRAKT_REFRESH_TOKEN=still-refresh-0001\n",
            encoding="utf-8",
        )
        auth = clients.TraktAuth()
        auth.access_token = "still-access-0001"
        auth.refresh_token = "still-refresh-0001"
        response = MagicMock()
        response.status_code = 500

        with patch.object(clients.requests.Session, "post", return_value=response):
            assert auth.refresh_access_token() is False

        assert auth.refresh_token == "still-refresh-0001"
        assert "still-refresh-0001" in env_file.read_text(encoding="utf-8")

    def test_interrupted_token_save_keeps_the_previous_file(self, env_file):
        env_file.write_text("TRAKT_ACCESS_TOKEN=kept-access-0001\n", encoding="utf-8")
        before = env_file.read_text(encoding="utf-8")
        auth = clients.TraktAuth()
        auth.access_token = "fresh-access-0001"
        auth.refresh_token = "fresh-refresh-0001"

        with patch("traktor.utils.os.fsync", side_effect=OSError(28, "No space left on device")):
            auth.save_tokens()

        assert env_file.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Issue #80: cached episode rows skipped the rating-key check #41 added
# ---------------------------------------------------------------------------


def _cache_with_episode_rows(rows):
    """Build a cache holding one show's episode snapshot."""
    server = MagicMock()
    server.library.sections.return_value = []
    cache = clients.CacheManager(server)
    _empty_cache(cache)
    cache.record_show_episodes(
        700, {"viewedLeafCount": 1, "leafCount": 1, "lastViewedAt": None}, rows
    )
    return cache


def test_cached_episode_row_with_a_url_rating_key_drops_the_snapshot():
    """A doctored episode row cannot keep a URL where the rating key belongs."""
    cache = _cache_with_episode_rows([[1, 1, "@127.0.0.1:9/steal", 1, None]])

    assert cache._sanitize_cached_items() >= 1
    assert cache.get_show_episodes(700) is None


def test_clean_episode_snapshot_survives_sanitizing():
    """The check drops nothing when every row is a plain rating key."""
    rows = [[1, 1, 7001, 1, None], [1, 2, 7002, 0, None]]
    cache = _cache_with_episode_rows(rows)

    assert cache._sanitize_cached_items() == 0
    assert cache.get_show_episodes(700)["episodes"] == rows


def test_malformed_episode_snapshot_is_dropped():
    """A snapshot with a wrong shape or a short row is dropped whole."""
    server = MagicMock()
    server.library.sections.return_value = []
    cache = clients.CacheManager(server)
    _empty_cache(cache)
    cache.memory_cache["show_episodes"] = {
        "700": {"episodes": "not a list"},
        "701": {"episodes": [[1, 1, 7001, 1]]},
    }

    assert cache._sanitize_cached_items() == 2
    assert cache.memory_cache["show_episodes"] == {}


# ---------------------------------------------------------------------------
# Issue #92: personal list reads returned only the first page of ten
# ---------------------------------------------------------------------------


def _trakt_client_with_auth():
    auth = MagicMock()
    auth.get_headers.return_value = {"Authorization": "Bearer test-token"}
    return clients.TraktClient(auth)


def _serve_pages(client, pages):
    """Make ``client`` answer an endpoint with ``pages`` and record the requests."""
    calls = []

    def fake_request(path, params=None, **kwargs):
        page = (params or {}).get("page", 1)
        calls.append(dict(params or {}))
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = pages[page - 1]
        response.headers = {"X-Pagination-Page-Count": str(len(pages))}
        return response

    client._request = fake_request
    return calls


def _pages(item_factory, total_pages=2, per_page=10):
    return [
        [item_factory(page * per_page + index) for index in range(per_page)]
        for page in range(total_pages)
    ]


def test_liked_lists_are_read_from_every_page():
    client = _trakt_client_with_auth()
    calls = _serve_pages(
        client,
        _pages(
            lambda n: {
                "list": {"name": f"List {n}", "user": {"username": "someone"}, "item_count": 1}
            }
        ),
    )

    assert len(client.get_liked_lists()) == 20
    assert [call.get("page") for call in calls] == [1, 2]


def test_collection_is_read_from_every_page():
    client = _trakt_client_with_auth()
    _serve_pages(
        client,
        _pages(lambda n: {"collected_at": "2026-01-01T00:00:00Z", "movie": {"title": f"M{n}"}}),
    )

    assert len(client.get_collection("movies")) == 20


def test_watchlist_is_read_from_every_page():
    client = _trakt_client_with_auth()
    _serve_pages(
        client,
        _pages(
            lambda n: {
                "type": "movie",
                "listed_at": "2026-01-01T00:00:00Z",
                "movie": {"title": f"M{n}"},
            }
        ),
    )

    assert len(client.get_watchlist()) == 20


def test_playback_progress_is_read_from_every_page():
    client = _trakt_client_with_auth()
    _serve_pages(
        client,
        _pages(
            lambda n: {
                "type": "movie",
                "progress": 50,
                "id": n,
                "movie": {"title": f"M{n}", "ids": {"imdb": f"tt{n:07d}"}},
            }
        ),
    )

    assert len(client.get_playback_progress()) == 20


# ---------------------------------------------------------------------------
# Issue #102: the library cache was reused across Plex accounts
# ---------------------------------------------------------------------------


def test_a_cache_built_for_another_plex_account_is_not_reused(tmp_path, monkeypatch):
    """The cached watched flags are per user, so a token switch rebuilds."""
    monkeypatch.setattr(clients, "CACHE_DIR", tmp_path)
    server = MagicMock()
    server.library.sections.return_value = []
    owner = clients.CacheManager(
        server, account_fingerprint=clients.plex_account_fingerprint("http://plex", "owner-token")
    )
    _empty_cache(owner)
    assert owner._save_cache() is True

    other = clients.CacheManager(
        server,
        account_fingerprint=clients.plex_account_fingerprint("http://plex", "managed-user-token"),
    )
    assert other._is_cache_valid() is False

    same = clients.CacheManager(
        server,
        account_fingerprint=clients.plex_account_fingerprint("http://plex", "owner-token"),
    )
    assert same._is_cache_valid() is True


def test_a_cache_without_a_fingerprint_is_not_served_to_a_known_account(tmp_path, monkeypatch):
    """A cache written before this change belongs to no account we can name."""
    monkeypatch.setattr(clients, "CACHE_DIR", tmp_path)
    server = MagicMock()
    server.library.sections.return_value = []
    legacy = clients.CacheManager(server)
    _empty_cache(legacy)
    assert legacy._save_cache() is True

    known = clients.CacheManager(server, account_fingerprint="a1b2c3d4e5f60718")
    assert known._is_cache_valid() is False


def test_the_account_fingerprint_is_not_the_credential():
    fingerprint = clients.plex_account_fingerprint("http://plex", "secret-token-value")

    assert fingerprint is not None
    assert len(fingerprint) == 16
    assert "secret-token-value" not in fingerprint
    assert clients.plex_account_fingerprint(None, "token") is None


# ---------------------------------------------------------------------------
# Issue #81: the cache file is untrusted and must be read within a size bound
# ---------------------------------------------------------------------------


def _write_cache_meta(cache, age_minutes=30):
    """Write a metadata stamp that makes the cache look fresh and valid.

    The stamp is ``age_minutes`` old, which is the window an incremental update
    looks back over.
    """
    from datetime import timedelta

    stamp = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    cache.cache_meta_file.parent.mkdir(parents=True, exist_ok=True)
    cache.cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache.cache_meta_file.write_text(
        json.dumps(
            {
                "version": clients.CACHE_VERSION,
                "created": stamp.isoformat(),
                "library_hash": cache._get_library_hash(),
                "last_update": stamp.isoformat(),
            }
        )
    )


def test_a_cache_that_expands_past_the_bound_is_rebuilt(tmp_path, monkeypatch, caplog):
    """A small doctored cache file must not be expanded into memory (issue #81)."""
    import logging

    import traktor.utils as utils

    # raising=False: on the pre-fix tree the constant does not exist, and the
    # test must then fail on behaviour, not on the missing name.
    monkeypatch.setattr(utils, "MAX_DECOMPRESSED_BYTES", 64 * 1024, raising=False)
    monkeypatch.setattr(clients, "CACHE_DIR", tmp_path / "cache")
    server = MagicMock()
    server.library.sections.return_value = []
    cache = clients.CacheManager(server)

    # A structurally valid cache: without the bound it loads as a normal cache.
    payload = {
        "movies_by_imdb": {},
        "movies_by_tmdb": {},
        "shows_by_imdb": {},
        "shows_by_tmdb": {},
        "movies_list": [],
        "shows_list": [],
        "by_rating_key": {},
        "show_episodes": {},
        "blob": "0" * (256 * 1024),
    }
    cache.cache_file.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(cache.cache_file, "wb", compresslevel=9) as f:
        f.write(json.dumps(payload).encode("utf-8"))
    _write_cache_meta(cache, age_minutes=0)

    with caplog.at_level(logging.WARNING):
        assert cache.load_cache() is False  # rebuilt, not loaded

    assert any("Refusing this cache file" in record.getMessage() for record in caplog.records)
    assert "blob" not in cache.memory_cache


# ---------------------------------------------------------------------------
# Issue #91: reading a show's counters must not reload the show from Plex
# ---------------------------------------------------------------------------


class CountingShow(Show):
    """A real PlexAPI Show that counts the reloads its attributes trigger."""

    reloads = 0

    def _reload(self, **kwargs):
        type(self).reloads += 1
        return self


def _show_from_xml(rating_key, watched):
    from xml.etree.ElementTree import fromstring

    attrs = (
        f'ratingKey="{rating_key}" key="/library/metadata/{rating_key}" '
        f'title="Show {rating_key}" type="show" year="2020" '
        f'leafCount="10" viewedLeafCount="{1 if watched else 0}"'
    )
    if watched:
        attrs += ' lastViewedAt="1700000000"'
    return CountingShow(
        None, fromstring(f"<Directory {attrs} />"), initpath=f"/library/metadata/{rating_key}"
    )


def test_reading_show_counters_does_not_reload_each_show():
    """An unwatched show has no lastViewedAt, and that used to cost a request (issue #91)."""
    CountingShow.reloads = 0
    shows = [_show_from_xml(index, index == 0) for index in range(50)]

    counters = [clients.PlexClient.show_counters(show) for show in shows]

    assert CountingShow.reloads == 0
    assert counters[0]["viewedLeafCount"] == 1
    assert counters[1]["lastViewedAt"] is None


# ---------------------------------------------------------------------------
# Issue #93: a capped recentlyAdded read must not be recorded as complete
# ---------------------------------------------------------------------------


class _FakeItem:
    def __init__(self, index, added_at):
        self.ratingKey = index
        self.title = f"Movie {index}"
        self.year = 2020
        self.guid = f"plex://movie/{index}"
        self.guids = []
        self.lastViewedAt = None
        self.isWatched = False
        self.addedAt = added_at


class _PagedSection:
    """A movie section whose recentlyAdded honours maxresults."""

    type = "movie"
    title = "Films"

    def __init__(self, items):
        self.items = items

    def recentlyAdded(self, maxresults=50, **kwargs):
        return self.items[:maxresults]


class _CappedSection(_PagedSection):
    """A section that keeps returning a full page, so completeness is unprovable."""

    def recentlyAdded(self, maxresults=50, **kwargs):
        return self.items[:maxresults][:maxresults]


def _incremental_cache(tmp_path, monkeypatch, section):
    monkeypatch.setattr(clients, "CACHE_DIR", tmp_path / "cache")
    server = MagicMock()
    server.library.sections.return_value = [section]
    cache = clients.CacheManager(server)
    _empty_cache(cache)
    return cache


def test_incremental_update_reads_past_a_thousand_new_items(tmp_path, monkeypatch):
    """A bulk import of more than 1,000 titles must all reach the cache (issue #93)."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    items = [_FakeItem(i, now - timedelta(seconds=i)) for i in range(1500)]
    cache = _incremental_cache(tmp_path, monkeypatch, _PagedSection(items))
    _write_cache_meta(cache)

    assert cache._incremental_cache_update() is True

    cached = {row["ratingKey"] for row in cache.memory_cache["movies_list"]}
    assert cached == set(range(1500))


def test_a_capped_incremental_read_does_not_advance_the_stamp(tmp_path, monkeypatch):
    """When completeness cannot be shown, the next run must try again (issue #93)."""
    from datetime import timedelta

    monkeypatch.setattr(clients, "DEFAULT_RECENTLY_ADDED_MAXRESULTS", 2)
    monkeypatch.setattr(clients, "MAX_RECENTLY_ADDED_MAXRESULTS", 4)
    now = datetime.now(timezone.utc)
    items = [_FakeItem(i, now - timedelta(seconds=i)) for i in range(10)]
    cache = _incremental_cache(tmp_path, monkeypatch, _CappedSection(items))
    _write_cache_meta(cache)
    stamp_before = cache._get_last_update_timestamp()

    assert cache._incremental_cache_update() is False
    assert cache._get_last_update_timestamp() == stamp_before
    assert cache.memory_cache["movies_list"] == []


# ---------------------------------------------------------------------------
# Issue #94: playback progress must read each item once per run
# ---------------------------------------------------------------------------


class _ProgressItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key
        self.viewOffset = 1000
        self.duration = 100000
        self.title = f"Item {rating_key}"
        self.updates = 0

    def updateProgress(self, value):
        self.updates += 1
        self.viewOffset = value


class _CountingServer:
    def __init__(self):
        self.items = {}
        self.fetch_calls = 0

    def fetchItem(self, rating_key):
        self.fetch_calls += 1
        key = int(rating_key)
        return self.items.setdefault(key, _ProgressItem(key))


def test_playback_progress_reads_each_item_once():
    """The setter reuses the object the getter fetched (issue #94)."""
    from types import SimpleNamespace

    server = _CountingServer()
    plex = clients.PlexClient.__new__(clients.PlexClient)
    plex.server = server
    plex.cache = SimpleNamespace(_plex_objects={})

    for key in range(20):
        plex.get_playback_progress(key)
    plex.batch_set_playback_progress(
        [{"rating_key": key, "view_offset_ms": 2000} for key in range(20)]
    )

    assert server.fetch_calls == 20
    assert sum(item.updates for item in server.items.values()) == 20


# ---------------------------------------------------------------------------
# Issue #106: watched shows came from the first page only
# ---------------------------------------------------------------------------


def test_watched_shows_are_read_from_every_page():
    client = _trakt_client_with_auth()
    calls = _serve_pages(
        client,
        _pages(
            lambda n: {
                "show": {"title": f"Show {n}", "ids": {"imdb": f"tt{n:07d}"}},
                "seasons": [{"number": 1, "episodes": [{"number": 1}]}],
            }
        ),
    )

    assert len(client.get_watched_shows()) == 20
    assert [call.get("page") for call in calls] == [1, 2]


# ---------------------------------------------------------------------------
# Issue #109: a finishing sync wrote its start-of-run config over a concurrent
# credential save
# ---------------------------------------------------------------------------


def test_orphan_cleanup_keeps_a_config_saved_during_the_run(tmp_path, monkeypatch):
    config_file = tmp_path / ".traktor_config.json"
    config_file.write_text('{"blacklisted_lists": []}', encoding="utf-8")
    monkeypatch.setattr(clients, "CONFIG_FILE", config_file)

    stale_snapshot = {"blacklisted_lists": [], "managed_playlists": []}

    # Another session saves credentials while this run is working.
    config_file.write_text(
        '{"blacklisted_lists": [], "plex_url": "http://plex.example:32400", '
        '"plex_token": "placeholder-value-for-the-test"}',
        encoding="utf-8",
    )

    server = MagicMock()
    server.playlists.return_value = []
    plex = clients.PlexClient(server, MagicMock())

    plex.cleanup_orphaned_playlists(["My Trakt List"], stale_snapshot)

    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["plex_url"] == "http://plex.example:32400"
    assert saved["managed_playlists"] == ["My Trakt List"]


# ---------------------------------------------------------------------------
# Issue #113: the structure check accepted either container for every key
# ---------------------------------------------------------------------------


class TestCacheContainerTypes:
    @pytest.mark.parametrize(
        "key, wrong_value",
        [
            ("movies_by_imdb", []),
            ("movies_by_tmdb", []),
            ("shows_by_imdb", []),
            ("shows_by_tmdb", []),
            ("by_rating_key", []),
            ("movies_list", {}),
            ("shows_list", {}),
        ],
    )
    def test_a_wrong_container_type_is_not_a_valid_cache(self, server, key, wrong_value):
        cache = clients.CacheManager(server)
        _empty_cache(cache)
        cache.memory_cache[key] = wrong_value

        assert cache._validate_cache_structure() is False

    def test_load_cache_rebuilds_instead_of_loading_a_wrong_container(
        self, server, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(clients, "CACHE_DIR", tmp_path)
        wrong = {
            "movies_by_imdb": [],
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
            "shows_by_tmdb": {},
            "movies_list": [],
            "shows_list": [],
            "by_rating_key": {},
        }
        (tmp_path / "plex_library_cache.json").write_text(json.dumps(wrong), encoding="utf-8")

        cache = clients.CacheManager(server)

        assert cache.load_cache() is False
        assert isinstance(cache.memory_cache.get("movies_by_imdb", {}), dict)
