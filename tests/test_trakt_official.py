"""Tests for Trakt official lists client."""

import time
from unittest.mock import Mock, patch

import pytest
import requests

from traktor.trakt_official import (
    DEFAULT_CACHE_TTLS,
    DEFAULT_LIMITS,
    ENDPOINT_SCORES,
    ENDPOINTS,
    MIN_REQUEST_INTERVAL,
    VALID_PERIODS,
    TraktOfficialClient,
)


class TestTraktOfficialClient:
    """Tests for TraktOfficialClient."""

    @pytest.fixture
    def client(self):
        """Create a TraktOfficialClient with mocked client ID."""
        with patch("traktor.trakt_official.TRAKT_CLIENT_ID", "test_client_id"):
            client = TraktOfficialClient()
            return client

    @pytest.fixture
    def mock_response(self):
        """Create a mock response for requests."""
        response = Mock()
        response.status_code = 200
        response.json.return_value = []
        response.content = b"[]"
        return response

    def test_init(self):
        """Test client initialization."""
        with patch("traktor.trakt_official.TRAKT_CLIENT_ID", "test_id"):
            client = TraktOfficialClient()
            assert client.client_id == "test_id"
            assert client.base_url == "https://api.trakt.tv"
            assert hasattr(client, "_rate_limiter")
            assert client._rate_limiter.min_interval == MIN_REQUEST_INTERVAL

    def test_init_with_custom_id(self):
        """Test client initialization with custom client ID."""
        client = TraktOfficialClient(client_id="custom_id")
        assert client.client_id == "custom_id"

    def test_get_headers(self, client):
        """Test header generation."""
        headers = client._get_headers()
        assert headers["Content-Type"] == "application/json"
        assert headers["trakt-api-version"] == "2"
        assert headers["trakt-api-key"] == "test_client_id"
        assert "Authorization" not in headers  # No auth needed for public endpoints

    def test_rate_limit(self, client):
        """Test rate limiting."""
        # First call should not sleep (no recent request)
        start_time = time.time()
        client._rate_limiter.wait()
        elapsed = time.time() - start_time
        assert elapsed < 0.1

        # Second immediate call should sleep for ~1 second (MIN_REQUEST_INTERVAL)
        # But we'll mock to avoid actual sleep
        with patch("time.sleep") as mock_sleep:
            client._rate_limiter.wait()
            mock_sleep.assert_called()
            # Verify that sleep was called with approximately the expected interval
            sleep_arg = mock_sleep.call_args[0][0]
            assert sleep_arg > 0  # Should sleep some amount
            assert sleep_arg <= MIN_REQUEST_INTERVAL  # But not more than the max

    def test_get_endpoint_path_no_period(self, client):
        """Test endpoint path generation without period."""
        path = client._get_endpoint_path("movies.trending")
        assert path == "/movies/trending"

    def test_get_endpoint_path_with_period(self, client):
        """Test endpoint path generation with period."""
        path = client._get_endpoint_path("movies.played", period="monthly")
        assert path == "/movies/played/monthly"

    def test_get_endpoint_path_invalid_period(self, client):
        """Test endpoint path with invalid period falls back to weekly."""
        path = client._get_endpoint_path("movies.played", period="invalid")
        assert path == "/movies/played/weekly"

    def test_parse_items_movies_trending(self, client):
        """Test parsing trending movie items."""
        data = [
            {
                "watchers": 100,
                "movie": {
                    "title": "Test Movie",
                    "year": 2024,
                    "ids": {"imdb": "tt1234567", "tmdb": 12345},
                },
            }
        ]
        items = client._parse_items(data, "movies.trending")
        assert len(items) == 1
        assert items[0]["type"] == "movie"
        assert items[0]["movie"]["title"] == "Test Movie"
        assert items[0]["score"] == ENDPOINT_SCORES["movies.trending"]
        assert items[0]["watchers"] == 100

    def test_parse_items_shows_popular(self, client):
        """Test parsing popular show items."""
        data = [
            {
                "watchers": 50,
                "show": {
                    "title": "Test Show",
                    "year": 2024,
                    "ids": {"imdb": "tt7654321", "tmdb": 54321},
                },
            }
        ]
        items = client._parse_items(data, "shows.popular")
        assert len(items) == 1
        assert items[0]["type"] == "show"
        assert items[0]["show"]["title"] == "Test Show"
        assert items[0]["score"] == ENDPOINT_SCORES["shows.popular"]

    def test_parse_items_boxoffice(self, client):
        """Test parsing box office items."""
        data = [
            {
                "revenue": 1000000,
                "movie": {
                    "title": "Box Office Hit",
                    "year": 2024,
                    "ids": {"imdb": "tt9999999", "tmdb": 99999},
                },
            }
        ]
        items = client._parse_items(data, "movies.boxoffice")
        assert len(items) == 1
        assert items[0]["type"] == "movie"
        # Revenue is not currently preserved in parsed items, just verify item is parsed
        assert items[0]["movie"]["title"] == "Box Office Hit"

    def test_parse_items_watched_format(self, client):
        """Test parsing watched/played format (direct movie/show object)."""
        data = [
            {
                "title": "Direct Movie",
                "year": 2024,
                "ids": {"imdb": "tt1111111", "tmdb": 11111},
                "watchers": 75,
            }
        ]
        items = client._parse_items(data, "movies.watched")
        assert len(items) == 1
        assert items[0]["type"] == "movie"
        assert items[0]["movie"]["title"] == "Direct Movie"

    def test_parse_items_empty(self, client):
        """Test parsing empty data."""
        items = client._parse_items([], "movies.trending")
        assert len(items) == 0

    def test_parse_items_invalid(self, client):
        """Test parsing invalid data."""
        data = [{"invalid": "data"}]
        items = client._parse_items(data, "movies.trending")
        assert len(items) == 0

    @patch("traktor.trakt_official.requests.get")
    def test_request_success(self, mock_get, client, mock_response):
        """Test successful API request."""
        mock_get.return_value = mock_response

        result = client._request("movies/trending")
        assert result == []
        mock_get.assert_called_once()

    @patch("traktor.trakt_official.requests.get")
    def test_request_with_params(self, mock_get, client, mock_response):
        """Test API request with parameters."""
        mock_response.json.return_value = [{"watchers": 10, "movie": {"title": "Test"}}]
        mock_get.return_value = mock_response

        result = client._request("movies/trending", params={"limit": 50})
        assert len(result) == 1
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args[1]["params"] == {"limit": 50}

    @patch("traktor.trakt_official.requests.get")
    def test_request_failure(self, mock_get, client):
        """Test API request failure."""
        mock_get.side_effect = requests.exceptions.RequestException("Network error")

        with pytest.raises(requests.exceptions.RequestException):
            client._request("movies/trending")

    @patch("traktor.trakt_official.requests.get")
    def test_get_movies_trending(self, mock_get, client, mock_response):
        """Test get_movies_trending method."""
        mock_response.json.return_value = [
            {
                "watchers": 100,
                "movie": {
                    "title": "Trending Movie",
                    "year": 2024,
                    "ids": {"imdb": "tt1234567"},
                },
            }
        ]
        mock_get.return_value = mock_response

        result = client.get_movies_trending(limit=10)
        assert len(result) == 1
        assert result[0]["type"] == "movie"
        mock_get.assert_called_once()

    @patch("traktor.trakt_official.requests.get")
    def test_get_shows_popular(self, mock_get, client, mock_response):
        """Test get_shows_popular method."""
        mock_response.json.return_value = [
            {
                "watchers": 50,
                "show": {
                    "title": "Popular Show",
                    "year": 2024,
                    "ids": {"imdb": "tt7654321"},
                },
            }
        ]
        mock_get.return_value = mock_response

        result = client.get_shows_popular(limit=20)
        assert len(result) == 1
        assert result[0]["type"] == "show"

    @patch("traktor.trakt_official.requests.get")
    def test_get_movies_played_with_period(self, mock_get, client, mock_response):
        """Test get_movies_played with period parameter."""
        mock_response.json.return_value = [
            {"title": "Played Movie", "year": 2024, "ids": {"imdb": "tt1111111"}}
        ]
        mock_get.return_value = mock_response

        result = client.get_movies_played(period="monthly", limit=30)
        assert len(result) == 1

    @patch("traktor.trakt_official.requests.get")
    def test_get_endpoint_routing(self, mock_get, client, mock_response):
        """Test generic get_endpoint method routing."""
        mock_response.json.return_value = [
            {
                "watchers": 10,
                "movie": {"title": "Test", "year": 2024, "ids": {"imdb": "tt0000000"}},
            }
        ]
        mock_get.return_value = mock_response

        # Test movies.trending routing
        result = client.get_endpoint("movies.trending")
        assert len(result) == 1

        # Test unknown endpoint
        result = client.get_endpoint("invalid.endpoint")
        assert result == []

    def test_get_available_endpoints(self):
        """Test getting available endpoints list."""
        endpoints = TraktOfficialClient.get_available_endpoints()
        assert "movies.trending" in endpoints
        assert "shows.popular" in endpoints
        assert len(endpoints) == len(ENDPOINTS)

    def test_validate_endpoint(self):
        """Test endpoint validation."""
        assert TraktOfficialClient.validate_endpoint("movies.trending") is True
        assert TraktOfficialClient.validate_endpoint("shows.popular") is True
        assert TraktOfficialClient.validate_endpoint("invalid.endpoint") is False

    def test_get_endpoint_score(self):
        """Test getting endpoint scores."""
        assert TraktOfficialClient.get_endpoint_score("movies.trending") == 10
        assert TraktOfficialClient.get_endpoint_score("movies.popular") == 8
        assert TraktOfficialClient.get_endpoint_score("movies.collected") == 4
        assert TraktOfficialClient.get_endpoint_score("invalid") == 5  # Default

    def test_get_cache_ttl(self):
        """Test getting cache TTLs."""
        assert TraktOfficialClient.get_cache_ttl("movies.trending") == 1
        assert TraktOfficialClient.get_cache_ttl("movies.popular") == 6
        assert TraktOfficialClient.get_cache_ttl("movies.anticipated") == 24


class TestTraktOfficialClientConstants:
    """Tests for constants and configuration."""

    def test_valid_periods(self):
        """Test valid periods constant."""
        assert "daily" in VALID_PERIODS
        assert "weekly" in VALID_PERIODS
        assert "monthly" in VALID_PERIODS
        assert "yearly" in VALID_PERIODS
        assert len(VALID_PERIODS) == 4

    def test_default_limits(self):
        """Test default limits configuration."""
        assert DEFAULT_LIMITS["trending"] == 100
        assert DEFAULT_LIMITS["popular"] == 100
        assert DEFAULT_LIMITS["boxoffice"] == 10

    def test_endpoint_scores(self):
        """Test endpoint scores configuration."""
        assert ENDPOINT_SCORES["movies.trending"] == 10
        assert ENDPOINT_SCORES["shows.trending"] == 10
        assert ENDPOINT_SCORES["movies.collected"] == 4

    def test_cache_ttls(self):
        """Test default cache TTLs."""
        assert DEFAULT_CACHE_TTLS["trending"] == 1
        assert DEFAULT_CACHE_TTLS["popular"] == 6
        assert DEFAULT_CACHE_TTLS["anticipated"] == 24

    def test_endpoints_dict(self):
        """Test endpoints dictionary."""
        assert "movies.trending" in ENDPOINTS
        assert "shows.popular" in ENDPOINTS
        assert "{period}" in ENDPOINTS["movies.played"]

    def test_request_interval(self):
        """Test rate limiting interval."""
        # 60 requests per minute = 1 second between requests
        assert MIN_REQUEST_INTERVAL == 1.0
