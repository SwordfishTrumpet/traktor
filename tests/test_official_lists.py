"""Tests for official lists service."""

import json
import time
from unittest.mock import patch

import pytest

from traktor.official_lists import OfficialListsService


class TestOfficialListsService:
    """Tests for OfficialListsService."""

    @pytest.fixture
    def service(self, tmp_path):
        """Create an OfficialListsService with temporary cache directory."""
        with patch("traktor.trakt_official.TRAKT_CLIENT_ID", "test_client_id"):
            service = OfficialListsService(cache_dir=tmp_path)
            return service

    @pytest.fixture
    def sample_movie_items(self):
        """Sample movie items for testing."""
        return [
            {
                "type": "movie",
                "movie": {
                    "title": "Movie 1",
                    "year": 2024,
                    "ids": {"imdb": "tt1111111", "tmdb": 11111},
                },
                "score": 10,
                "watchers": 100,
            },
            {
                "type": "movie",
                "movie": {
                    "title": "Movie 2",
                    "year": 2024,
                    "ids": {"imdb": "tt2222222", "tmdb": 22222},
                },
                "score": 8,
                "watchers": 50,
            },
        ]

    @pytest.fixture
    def sample_show_items(self):
        """Sample show items for testing."""
        return [
            {
                "type": "show",
                "show": {
                    "title": "Show 1",
                    "year": 2024,
                    "ids": {"imdb": "tt3333333", "tmdb": 33333},
                },
                "score": 10,
                "watchers": 75,
            },
            {
                "type": "show",
                "show": {
                    "title": "Show 2",
                    "year": 2024,
                    "ids": {"imdb": "tt4444444", "tmdb": 44444},
                },
                "score": 6,
                "watchers": 25,
            },
        ]

    def test_init(self, tmp_path):
        """Test service initialization."""
        with patch("traktor.trakt_official.TRAKT_CLIENT_ID", "test_id"):
            service = OfficialListsService(cache_dir=tmp_path)
            assert service.cache_dir == tmp_path
            assert service.client is not None

    def test_init_creates_cache_dir(self, tmp_path):
        """Test that initialization creates cache directory."""
        cache_dir = tmp_path / "nested" / "cache"
        assert not cache_dir.exists()

        with patch("traktor.trakt_official.TRAKT_CLIENT_ID", "test_id"):
            _ = OfficialListsService(cache_dir=cache_dir)
            assert cache_dir.exists()

    def test_get_cache_file(self, service):
        """Test cache file path generation."""
        path = service._get_cache_file("movies.trending")
        assert path.name == "movies_trending.json"
        assert path.parent == service.cache_dir

    def test_is_cache_valid_no_file(self, service):
        """Test cache validation when file doesn't exist."""
        assert service._is_cache_valid("movies.trending") is False

    def test_is_cache_valid_fresh(self, service, sample_movie_items):
        """Test cache validation with fresh cache."""
        # Save fresh cache
        service._save_cache("movies.trending", sample_movie_items)
        assert service._is_cache_valid("movies.trending") is True

    def test_is_cache_valid_expired(self, service, sample_movie_items):
        """Test cache validation with expired cache."""
        # Save cache
        service._save_cache("movies.trending", sample_movie_items)

        # Modify file modification time to be in the past
        cache_file = service._get_cache_file("movies.trending")
        old_time = time.time() - 7200  # 2 hours ago
        import os

        os.utime(cache_file, (old_time, old_time))

        # Cache should be expired (trending TTL is 1 hour)
        assert service._is_cache_valid("movies.trending") is False

    def test_load_cache_valid(self, service, sample_movie_items):
        """Test loading valid cache."""
        service._save_cache("movies.trending", sample_movie_items)
        loaded = service._load_cache("movies.trending")
        assert loaded is not None
        assert len(loaded) == 2

    def test_load_cache_invalid(self, service):
        """Test loading invalid cache returns None."""
        loaded = service._load_cache("movies.trending")
        assert loaded is None

    def test_save_cache(self, service, sample_movie_items):
        """Test saving cache."""
        service._save_cache("movies.trending", sample_movie_items)

        cache_file = service._get_cache_file("movies.trending")
        assert cache_file.exists()

        with open(cache_file, "r") as f:
            data = json.load(f)
        assert len(data) == 2
        assert data[0]["movie"]["title"] == "Movie 1"

    def test_deduplicate_items(self, service, sample_movie_items, sample_show_items):
        """Test item deduplication."""
        # Add duplicate movie with different score
        duplicate_movie = {
            "type": "movie",
            "movie": {
                "title": "Movie 1",
                "year": 2024,
                "ids": {"imdb": "tt1111111", "tmdb": 11111},
            },
            "score": 6,
            "watchers": 30,
        }

        all_items = [
            ("endpoint1", sample_movie_items[0]),  # Movie 1, score 10
            ("endpoint2", duplicate_movie),  # Movie 1, score 6 (duplicate)
            ("endpoint1", sample_movie_items[1]),  # Movie 2, score 8
            ("endpoint1", sample_show_items[0]),  # Show 1
            ("endpoint2", sample_show_items[1]),  # Show 2
        ]

        result = service._deduplicate_items(all_items)

        assert len(result["movies"]) == 2
        assert len(result["shows"]) == 2

        # Movie 1 should have combined score of 16 (10 + 6)
        movie1 = next(m for m in result["movies"] if m["movie"]["title"] == "Movie 1")
        assert movie1["combined_score"] == 16
        assert "endpoint1" in movie1["sources"]
        assert "endpoint2" in movie1["sources"]

        # Movies should be sorted by score (highest first)
        assert result["movies"][0]["combined_score"] >= result["movies"][1]["combined_score"]

    def test_deduplicate_items_no_ids(self, service):
        """Test deduplication of items without IDs."""
        items_without_ids = [
            (
                "endpoint1",
                {
                    "type": "movie",
                    "movie": {"title": "No ID Movie", "year": 2024, "ids": {}},
                    "score": 10,
                },
            )
        ]

        result = service._deduplicate_items(items_without_ids)
        assert len(result["movies"]) == 0  # Should skip items without IDs

    def test_deduplicate_items_empty_input(self, service):
        """Test deduplication with empty input."""
        result = service._deduplicate_items([])
        assert len(result["movies"]) == 0
        assert len(result["shows"]) == 0

    def test_deduplicate_items_single_item(self, service, sample_movie_items):
        """Test deduplication with a single item."""
        all_items = [("endpoint1", sample_movie_items[0])]
        result = service._deduplicate_items(all_items)
        assert len(result["movies"]) == 1
        assert len(result["shows"]) == 0
        assert result["movies"][0]["combined_score"] == 10

    @patch("traktor.official_lists.TraktOfficialClient.get_endpoint")
    def test_fetch_endpoint_success(self, mock_get_endpoint, service, sample_movie_items):
        """Test fetching endpoint with success."""
        mock_get_endpoint.return_value = sample_movie_items

        result = service.fetch_endpoint("movies.trending", use_cache=False)
        assert len(result) == 2
        mock_get_endpoint.assert_called_once()

    @patch("traktor.official_lists.TraktOfficialClient.get_endpoint")
    def test_fetch_endpoint_uses_cache(self, mock_get_endpoint, service, sample_movie_items):
        """Test that fetch uses cache when available."""
        # Pre-populate cache
        service._save_cache("movies.trending", sample_movie_items)

        result = service.fetch_endpoint("movies.trending", use_cache=True)
        assert len(result) == 2
        mock_get_endpoint.assert_not_called()  # Should not call API

    @patch("traktor.official_lists.TraktOfficialClient.get_endpoint")
    def test_fetch_endpoint_fallback_to_stale_cache(
        self, mock_get_endpoint, service, sample_movie_items
    ):
        """Test fallback to stale cache on API failure."""
        # Pre-populate cache
        service._save_cache("movies.trending", sample_movie_items)

        # Make cache expired
        cache_file = service._get_cache_file("movies.trending")
        old_time = time.time() - 7200
        import os

        os.utime(cache_file, (old_time, old_time))

        # API fails
        mock_get_endpoint.side_effect = Exception("API error")

        result = service.fetch_endpoint("movies.trending", use_cache=True)
        assert len(result) == 2  # Should use stale cache

    @patch("traktor.official_lists.TraktOfficialClient.get_endpoint")
    def test_fetch_multiple_endpoints(self, mock_get_endpoint, service, sample_movie_items):
        """Test fetching multiple endpoints."""
        mock_get_endpoint.return_value = sample_movie_items

        endpoints = ["movies.trending", "movies.popular"]
        result = service.fetch_multiple_endpoints(endpoints, use_cache=False)

        assert len(result) == 2
        assert "movies.trending" in result
        assert "movies.popular" in result
        assert mock_get_endpoint.call_count == 2

    def test_aggregate_items(self, service, sample_movie_items, sample_show_items):
        """Test aggregating items from multiple endpoints."""
        endpoint_results = {
            "movies.trending": sample_movie_items,
            "shows.trending": sample_show_items,
        }

        result = service.aggregate_items(endpoint_results)
        assert len(result["movies"]) == 2
        assert len(result["shows"]) == 2

    def test_get_playlists_from_endpoints_separate(self, service, sample_movie_items):
        """Test getting separate playlists per endpoint."""
        with patch.object(service, "fetch_endpoint") as mock_fetch:
            mock_fetch.return_value = sample_movie_items

            playlists = service.get_playlists_from_endpoints(
                ["movies.trending"], separate_playlists=True
            )

            assert len(playlists) == 1
            assert playlists[0]["name"] == "Trakt Movies - Trending"
            assert "Updated automatically" in playlists[0]["description"]
            assert playlists[0]["source"] == "movies.trending"

    def test_get_playlists_from_endpoints_stats_with_period(self, service, sample_movie_items):
        """Test getting playlists for stats endpoints with period."""
        with patch.object(service, "fetch_endpoint") as mock_fetch:
            mock_fetch.return_value = sample_movie_items

            playlists = service.get_playlists_from_endpoints(
                ["movies.played"], period="monthly", separate_playlists=True
            )

            assert len(playlists) == 1
            assert "Monthly" in playlists[0]["name"]
            assert "monthly" in playlists[0]["description"]

    def test_get_playlists_from_endpoints_merged(
        self, service, sample_movie_items, sample_show_items
    ):
        """Test getting merged playlists by type."""
        with patch.object(service, "fetch_multiple_endpoints") as mock_fetch:
            mock_fetch.return_value = {
                "movies.trending": sample_movie_items,
                "shows.trending": sample_show_items,
            }

            playlists = service.get_playlists_from_endpoints(
                ["movies.trending", "shows.trending"], separate_playlists=False
            )

            # Should create two playlists: one for movies, one for shows
            assert len(playlists) == 2
            movie_playlist = next(p for p in playlists if "Movies" in p["name"])
            show_playlist = next(p for p in playlists if "Shows" in p["name"])
            assert "aggregated" in movie_playlist["source"]
            assert "aggregated" in show_playlist["source"]

    def test_clear_cache_specific(self, service, sample_movie_items):
        """Test clearing cache for specific endpoint."""
        service._save_cache("movies.trending", sample_movie_items)
        cache_file = service._get_cache_file("movies.trending")
        assert cache_file.exists()

        service.clear_cache("movies.trending")
        assert not cache_file.exists()

    def test_clear_cache_all(self, service, sample_movie_items):
        """Test clearing all cache."""
        service._save_cache("movies.trending", sample_movie_items)
        service._save_cache("movies.popular", sample_movie_items)

        service.clear_cache()

        assert len(list(service.cache_dir.glob("*.json"))) == 0

    def test_get_default_endpoints(self):
        """Test getting default endpoints."""
        defaults = OfficialListsService.get_default_endpoints()
        assert "movies.trending" in defaults
        assert "movies.popular" in defaults
        assert "shows.trending" in defaults
        assert "shows.popular" in defaults

    def test_parse_endpoint_list_valid(self):
        """Test parsing valid endpoint list."""
        endpoints = OfficialListsService.parse_endpoint_list(
            "movies.trending,shows.popular,movies.anticipated"
        )
        assert len(endpoints) == 3
        assert "movies.trending" in endpoints
        assert "shows.popular" in endpoints

    def test_parse_endpoint_list_invalid(self):
        """Test parsing endpoint list with invalid entries."""
        endpoints = OfficialListsService.parse_endpoint_list(
            "movies.trending,invalid.endpoint,shows.popular"
        )
        assert len(endpoints) == 2
        assert "invalid.endpoint" not in endpoints

    def test_parse_endpoint_list_empty(self):
        """Test parsing empty endpoint list."""
        endpoints = OfficialListsService.parse_endpoint_list("")
        assert len(endpoints) == 0


class TestOfficialListsServiceIntegration:
    """Integration tests for OfficialListsService."""

    def test_full_workflow(self, tmp_path):
        """Test full workflow from fetch to playlist generation."""
        movie_items = [
            {
                "type": "movie",
                "movie": {
                    "title": "Test Movie",
                    "year": 2024,
                    "ids": {"imdb": "tt1234567", "tmdb": 12345},
                },
                "score": 10,
                "watchers": 100,
            }
        ]

        with patch("traktor.trakt_official.TRAKT_CLIENT_ID", "test_id"):
            service = OfficialListsService(cache_dir=tmp_path)

            with patch.object(service.client, "get_endpoint", return_value=movie_items):
                # Fetch and create playlists
                playlists = service.get_playlists_from_endpoints(
                    ["movies.trending"], separate_playlists=True
                )

                assert len(playlists) == 1
                assert playlists[0]["name"] == "Trakt Movies - Trending"
                assert len(playlists[0]["items"]) == 1

                # Verify cache was created
                cache_file = service._get_cache_file("movies.trending")
                assert cache_file.exists()


class TestOfficialListsServiceMultiplePeriods:
    """Tests for multiple periods support in official lists."""

    def test_get_playlists_from_multiple_periods(self, tmp_path):
        """Test generating playlists for multiple periods."""
        movie_items_weekly = [
            {
                "type": "movie",
                "movie": {
                    "title": "Weekly Movie",
                    "year": 2024,
                    "ids": {"imdb": "tt1111111", "tmdb": 11111},
                },
                "score": 10,
                "watchers": 100,
            }
        ]
        movie_items_monthly = [
            {
                "type": "movie",
                "movie": {
                    "title": "Monthly Movie",
                    "year": 2024,
                    "ids": {"imdb": "tt2222222", "tmdb": 22222},
                },
                "score": 8,
                "watchers": 50,
            }
        ]

        with patch("traktor.trakt_official.TRAKT_CLIENT_ID", "test_id"):
            service = OfficialListsService(cache_dir=tmp_path)

            # Mock fetch_endpoint to return different items for different periods
            def mock_fetch(endpoint, period, use_cache=True):
                if period == "weekly":
                    return movie_items_weekly
                elif period == "monthly":
                    return movie_items_monthly
                return []

            with patch.object(service, "fetch_endpoint", side_effect=mock_fetch):
                # Fetch weekly playlists
                weekly_playlists = service.get_playlists_from_endpoints(
                    ["movies.played"], period="weekly", separate_playlists=True
                )

                # Fetch monthly playlists
                monthly_playlists = service.get_playlists_from_endpoints(
                    ["movies.played"], period="monthly", separate_playlists=True
                )

                # Combine (simulating what sync.py does)
                all_playlists = weekly_playlists + monthly_playlists

                assert len(all_playlists) == 2

                # Check weekly playlist
                weekly = next(p for p in all_playlists if "Weekly" in p["name"])
                assert weekly["name"] == "Trakt Movies - Played (Weekly)"
                assert len(weekly["items"]) == 1
                assert weekly["items"][0]["movie"]["title"] == "Weekly Movie"

                # Check monthly playlist
                monthly = next(p for p in all_playlists if "Monthly" in p["name"])
                assert monthly["name"] == "Trakt Movies - Played (Monthly)"
                assert len(monthly["items"]) == 1
                assert monthly["items"][0]["movie"]["title"] == "Monthly Movie"


class TestOfficialListsServiceEdgeCases:
    """Edge case tests for OfficialListsService."""

    def test_deduplicate_items_with_show_only(self, tmp_path):
        """Test deduplication when only show items exist."""
        show_items = [
            (
                "endpoint1",
                {
                    "type": "show",
                    "show": {
                        "title": "Show 1",
                        "year": 2024,
                        "ids": {"imdb": "tt1111111", "tmdb": 11111},
                    },
                    "score": 10,
                    "watchers": 100,
                },
            )
        ]
        with patch("traktor.trakt_official.TRAKT_CLIENT_ID", "test_id"):
            service = OfficialListsService(cache_dir=tmp_path)
            result = service._deduplicate_items(show_items)
            assert len(result["movies"]) == 0
            assert len(result["shows"]) == 1

    def test_fetch_endpoint_stale_cache_fallback_with_request_exception(self, tmp_path):
        """Test stale cache fallback when RequestException occurs."""
        import requests

        movie_items = [
            {
                "type": "movie",
                "movie": {
                    "title": "Test Movie",
                    "year": 2024,
                    "ids": {"imdb": "tt1234567", "tmdb": 12345},
                },
                "score": 10,
                "watchers": 100,
            }
        ]

        with patch("traktor.trakt_official.TRAKT_CLIENT_ID", "test_id"):
            service = OfficialListsService(cache_dir=tmp_path)

            # Pre-populate cache
            service._save_cache("movies.trending", movie_items)

            # Make cache expired
            cache_file = service._get_cache_file("movies.trending")
            old_time = time.time() - 7200
            import os as os_mod

            os_mod.utime(cache_file, (old_time, old_time))

            # API fails with RequestException
            with patch.object(
                service.client,
                "get_endpoint",
                side_effect=requests.exceptions.RequestException("Network error"),
            ):
                result = service.fetch_endpoint("movies.trending", use_cache=True)
                assert len(result) == 1  # Should use stale cache

    def test_fetch_endpoint_no_cache_no_fallback(self, tmp_path):
        """Test that fetch returns empty when no cache and API fails."""
        import requests

        with patch("traktor.trakt_official.TRAKT_CLIENT_ID", "test_id"):
            service = OfficialListsService(cache_dir=tmp_path)

            with patch.object(
                service.client,
                "get_endpoint",
                side_effect=requests.exceptions.RequestException("Network error"),
            ):
                result = service.fetch_endpoint("movies.trending", use_cache=True)
                assert result == []

    def test_parse_endpoint_list_with_whitespace(self):
        """Test endpoint parsing handles whitespace."""
        endpoints = OfficialListsService.parse_endpoint_list(
            "movies.trending, shows.popular , movies.anticipated"
        )
        assert len(endpoints) == 3
        assert "movies.trending" in endpoints
        assert "shows.popular" in endpoints
        assert "movies.anticipated" in endpoints

    def test_parse_endpoint_list_with_all_valid(self):
        """Test parsing with all valid endpoints."""
        all_endpoints = ",".join(OfficialListsService.get_default_endpoints())
        endpoints = OfficialListsService.parse_endpoint_list(all_endpoints)
        assert len(endpoints) > 0
        for ep in endpoints:
            assert ep in OfficialListsService.get_default_endpoints()

    def test_get_playlists_empty_endpoints(self, tmp_path):
        """Test getting playlists with empty endpoint list."""
        with patch("traktor.trakt_official.TRAKT_CLIENT_ID", "test_id"):
            service = OfficialListsService(cache_dir=tmp_path)
            playlists = service.get_playlists_from_endpoints([], separate_playlists=True)
            assert len(playlists) == 0
