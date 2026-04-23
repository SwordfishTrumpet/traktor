"""Shared test fixtures for traktor tests."""

from unittest.mock import Mock

import pytest


@pytest.fixture
def mock_plex_server():
    """Create a mock Plex server."""
    server = Mock()

    # Mock library sections
    movie_section = Mock()
    movie_section.title = "Movies"
    movie_section.type = "movie"
    movie_section.all.return_value = []

    show_section = Mock()
    show_section.title = "TV Shows"
    show_section.type = "show"
    show_section.all.return_value = []

    server.library.sections.return_value = [movie_section, show_section]

    return server


@pytest.fixture
def mock_plex_item():
    """Create a mock Plex item."""
    item = Mock()
    item.title = "Test Movie"
    item.year = 2023
    item.TYPE = "movie"
    item.guid = "plex://movie/5d9..."
    item.ratingKey = "12345"
    item.isWatched = False
    item.lastViewedAt = None
    return item


@pytest.fixture
def mock_episode():
    """Create a mock Plex episode."""
    episode = Mock()
    episode.title = "Pilot"
    episode.seasonNumber = 1
    episode.episodeNumber = 1
    episode.TYPE = "episode"
    episode.guid = "plex://episode/5d9..."
    episode.ratingKey = "67890"
    episode.isWatched = False
    episode.lastViewedAt = None
    return episode


@pytest.fixture
def mock_show():
    """Create a mock Plex show."""
    show = Mock()
    show.title = "Test Show"
    show.year = 2022
    show.TYPE = "show"
    show.guid = "plex://show/5d9..."
    show.ratingKey = "54321"

    # Mock seasons
    season1 = Mock()
    season1.seasonNumber = 1
    season1.episodes.return_value = [mock_episode()]

    season2 = Mock()
    season2.seasonNumber = 2
    season2.episodes.return_value = []

    show.seasons.return_value = [season1, season2]
    return show


@pytest.fixture
def mock_trakt_auth():
    """Create a mock TraktAuth instance."""
    auth = Mock()
    auth.access_token = "test_access_token"
    auth.refresh_token = "test_refresh_token"
    return auth


@pytest.fixture
def mock_trakt_client():
    """Create a mock TraktClient instance."""
    client = Mock()
    client.get_liked_lists.return_value = [
        {
            "name": "Test List",
            "description": "A test list",
            "ids": {"slug": "test-list"},
            "user": {"ids": {"slug": "testuser"}},
            "item_count": 5,
        }
    ]
    client.get_list_items.return_value = [
        {
            "type": "movie",
            "movie": {
                "title": "Test Movie",
                "year": 2023,
                "ids": {"imdb": "tt1234567", "tmdb": 1234567},
            },
        }
    ]
    return client


@pytest.fixture
def mock_cache_manager():
    """Create a mock CacheManager instance."""
    cache = Mock()
    cache.memory_cache = {
        "movies_by_imdb": {"tt1234567": {"rating_key": "12345", "title": "Test Movie"}},
        "movies_by_tmdb": {"1234567": {"rating_key": "12345", "title": "Test Movie"}},
        "shows_by_imdb": {"tt7654321": {"rating_key": "54321", "title": "Test Show"}},
        "shows_by_tmdb": {"7654321": {"rating_key": "54321", "title": "Test Show"}},
    }
    cache.find_movie_by_imdb.side_effect = lambda imdb_id: cache.memory_cache["movies_by_imdb"].get(
        imdb_id
    )
    cache.find_movie_by_tmdb.side_effect = lambda tmdb_id: cache.memory_cache["movies_by_tmdb"].get(
        str(tmdb_id)
    )
    cache.find_show_by_imdb.side_effect = lambda imdb_id: cache.memory_cache["shows_by_imdb"].get(
        imdb_id
    )
    cache.find_show_by_tmdb.side_effect = lambda tmdb_id: cache.memory_cache["shows_by_tmdb"].get(
        str(tmdb_id)
    )
    return cache


@pytest.fixture
def mock_plex_client(mock_plex_server, mock_cache_manager):
    """Create a mock PlexClient instance."""
    client = Mock()
    client.server = mock_plex_server
    client.cache = mock_cache_manager
    client.find_item_by_cache.side_effect = lambda imdb_id=None, tmdb_id=None, media_type=None: (
        mock_cache_manager.find_movie_by_imdb(imdb_id)
        if media_type == "movie"
        else mock_cache_manager.find_show_by_imdb(imdb_id) if media_type == "show" else None
    )
    return client


@pytest.fixture
def mock_history_manager():
    """Create a mock WatchHistoryManager instance."""
    manager = Mock()
    manager.get_last_sync_timestamp.return_value = None
    manager.get_stats.return_value = {
        "total_items": 0,
        "watched_both": 0,
        "watched_plex_only": 0,
        "watched_trakt_only": 0,
        "unwatched_both": 0,
    }
    return manager


@pytest.fixture
def mock_conflict_resolver():
    """Create a mock ConflictResolver instance."""
    resolver = Mock()
    resolver.strategy = "newest_wins"
    resolver.resolve.return_value = "plex"  # Default to Plex wins
    return resolver
