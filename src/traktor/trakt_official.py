"""Trakt official curated lists API client.

This module provides access to Trakt's official algorithmic curated lists
including trending, popular, played, watched, collected, anticipated, and box office.
"""

from typing import Dict, List, Optional

import requests

from .clients import RateLimiter
from .log import logger
from .settings import TRAKT_API_URL, TRAKT_CLIENT_ID

# Rate limiting: 60 requests per minute for public endpoints
MAX_REQUESTS_PER_MINUTE = 60
MIN_REQUEST_INTERVAL = 60.0 / MAX_REQUESTS_PER_MINUTE  # ~1 second between requests

# API pagination constants
DEFAULT_API_LIMIT = 100  # Default items per API request

# Default limits per endpoint type
DEFAULT_LIMITS = {
    "trending": 100,
    "popular": 100,
    "played": 100,
    "watched": 100,
    "collected": 100,
    "anticipated": 100,
    "boxoffice": 10,
}

# Valid periods for stats endpoints
VALID_PERIODS = {"daily", "weekly", "monthly", "yearly"}

# Endpoint scoring for deduplication (higher = more important)
ENDPOINT_SCORES = {
    "movies.trending": 10,
    "shows.trending": 10,
    "movies.popular": 8,
    "shows.popular": 8,
    "movies.boxoffice": 7,
    "movies.played": 6,
    "movies.watched": 6,
    "shows.played": 6,
    "shows.watched": 6,
    "movies.anticipated": 5,
    "shows.anticipated": 5,
    "movies.collected": 4,
    "shows.collected": 4,
}

# API endpoint paths
ENDPOINTS = {
    # Movies (7 endpoints)
    "movies.trending": "/movies/trending",
    "movies.popular": "/movies/popular",
    "movies.played": "/movies/played/{period}",
    "movies.watched": "/movies/watched/{period}",
    "movies.collected": "/movies/collected/{period}",
    "movies.anticipated": "/movies/anticipated",
    "movies.boxoffice": "/movies/boxoffice",
    # Shows (6 endpoints)
    "shows.trending": "/shows/trending",
    "shows.popular": "/shows/popular",
    "shows.played": "/shows/played/{period}",
    "shows.watched": "/shows/watched/{period}",
    "shows.collected": "/shows/collected/{period}",
    "shows.anticipated": "/shows/anticipated",
}

# Cache TTLs per endpoint type (in hours)
DEFAULT_CACHE_TTLS = {
    "trending": 1,
    "popular": 6,
    "played": 6,
    "watched": 6,
    "collected": 24,
    "anticipated": 24,
    "boxoffice": 12,
}


class TraktOfficialClient:
    """Client for Trakt's official curated list endpoints.

    These endpoints are public and do not require OAuth authentication.
    They provide algorithmically curated content based on aggregate Trakt user activity.
    """

    def __init__(self, client_id: Optional[str] = None):
        """Initialize the official lists client.

        Args:
            client_id: Trakt API client ID (defaults to TRAKT_CLIENT_ID from settings)
        """
        self.client_id = client_id or TRAKT_CLIENT_ID
        self.base_url = TRAKT_API_URL
        # Use shared rate limiter (60 requests per minute = 1.0s interval)
        self._rate_limiter = RateLimiter(MIN_REQUEST_INTERVAL)
        logger.debug("TraktOfficialClient initialized")

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers for API calls."""
        return {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
        }

    def _request(self, endpoint: str, params: Optional[Dict] = None) -> List[Dict]:
        """Make a rate-limited request to the Trakt API.

        Args:
            endpoint: API endpoint path (without base URL)
            params: Optional query parameters

        Returns:
            JSON response as list of dicts

        Raises:
            requests.exceptions.RequestException: If the request fails
        """
        self._rate_limiter.wait()
        url = f"{self.base_url}/{endpoint}"
        headers = self._get_headers()

        logger.debug(f"Making request to: {url}")
        logger.debug(f"Params: {params}")

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            logger.debug(f"Response: {len(data)} items")
            # Handle empty dict response (return as empty list)
            if isinstance(data, dict) and not data:
                return []
            return data if isinstance(data, list) else [data]
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            raise

    def _get_endpoint_path(self, endpoint_name: str, period: str = "weekly") -> str:
        """Get the full API path for an endpoint.

        Args:
            endpoint_name: Name of the endpoint (e.g., "movies.trending")
            period: Period for stats endpoints (daily/weekly/monthly/yearly)

        Returns:
            API endpoint path
        """
        path_template = ENDPOINTS.get(endpoint_name, "")
        if "{period}" in path_template:
            if period not in VALID_PERIODS:
                logger.warning(f"Invalid period '{period}', using 'weekly'")
                period = "weekly"
            return path_template.replace("{period}", period)
        return path_template

    def _parse_items(self, data: List[Dict], endpoint_name: str) -> List[Dict]:
        """Parse API response items into standardized format.

        Args:
            data: Raw API response data
            endpoint_name: Name of the endpoint (for determining media type)

        Returns:
            List of standardized item dicts with 'type', 'movie' or 'show', and 'score'
        """
        items = []
        is_movie = endpoint_name.startswith("movies.")
        media_type = "movie" if is_movie else "show"

        score = ENDPOINT_SCORES.get(endpoint_name, 5)

        for entry in data:
            if not isinstance(entry, dict):
                continue

            # Handle trending/popular format (has 'movie' or 'show' key with 'watchers' count)
            media_data = entry.get(media_type)
            if media_data and isinstance(media_data, dict):
                item = {
                    "type": media_type,
                    media_type: media_data,
                    "score": score,
                    "watchers": entry.get("watchers", 0),
                }
                items.append(item)
            # Handle watched/played/collected format (direct movie/show object with title and ids)
            elif entry.get("title") and entry.get("ids"):
                item = {
                    "type": media_type,
                    media_type: entry,
                    "score": score,
                    "watchers": entry.get("watchers", 0),
                }
                items.append(item)
            # Handle box office format (revenue data)
            elif entry.get("revenue") and isinstance(entry.get(media_type), dict):
                media_data = entry.get(media_type, {})
                item = {
                    "type": media_type,
                    media_type: media_data,
                    "score": score,
                    "revenue": entry.get("revenue", 0),
                }
                items.append(item)

        logger.debug(f"Parsed {len(items)} items from {endpoint_name}")
        return items

    def get_movies_trending(self, limit: int = DEFAULT_API_LIMIT) -> List[Dict]:
        """Get trending movies (currently being watched).

        Args:
            limit: Maximum number of items to return

        Returns:
            List of movie items with scores
        """
        logger.info(f"Fetching trending movies (limit={limit})...")
        data = self._request("movies/trending", params={"limit": limit})
        return self._parse_items(data, "movies.trending")

    def get_movies_popular(self, limit: int = DEFAULT_API_LIMIT) -> List[Dict]:
        """Get popular movies.

        Args:
            limit: Maximum number of items to return

        Returns:
            List of popular movies with scores
        """
        logger.info(f"Fetching popular movies (limit={limit})...")
        data = self._request("movies/popular", params={"limit": limit})
        return self._parse_items(data, "movies.popular")

    def get_movies_played(
        self, period: str = "weekly", limit: int = DEFAULT_API_LIMIT
    ) -> List[Dict]:
        """Get most played movies.

        Args:
            period: Time period (daily, weekly, monthly, yearly)
            limit: Maximum number of items to return

        Returns:
            List of movie items with scores
        """
        logger.info(f"Fetching played movies (period={period}, limit={limit})...")
        path = self._get_endpoint_path("movies.played", period)
        data = self._request(path, params={"limit": limit})
        return self._parse_items(data, "movies.played")

    def get_movies_watched(
        self, period: str = "weekly", limit: int = DEFAULT_API_LIMIT
    ) -> List[Dict]:
        """Get most watched movies.

        Args:
            period: Time period (daily, weekly, monthly, yearly)
            limit: Maximum number of items to return

        Returns:
            List of most watched movies with watcher counts
        """
        logger.info(f"Fetching watched movies (period={period}, limit={limit})...")
        path = self._get_endpoint_path("movies.watched", period)
        data = self._request(path, params={"limit": limit})
        return self._parse_items(data, "movies.watched")

    def get_movies_collected(
        self, period: str = "weekly", limit: int = DEFAULT_API_LIMIT
    ) -> List[Dict]:
        """Get most collected movies.

        Args:
            period: Time period (daily, weekly, monthly, yearly)
            limit: Maximum number of items to return

        Returns:
            List of most collected movies with collection counts
        """
        logger.info(f"Fetching collected movies (period={period}, limit={limit})...")
        path = self._get_endpoint_path("movies.collected", period)
        data = self._request(path, params={"limit": limit})
        return self._parse_items(data, "movies.collected")

    def get_movies_anticipated(self, limit: int = DEFAULT_API_LIMIT) -> List[Dict]:
        """Get most anticipated upcoming movies.

        Args:
            limit: Maximum number of items to return

        Returns:
            List of movie items with scores
        """
        logger.info(f"Fetching anticipated movies (limit={limit})...")
        data = self._request("movies/anticipated", params={"limit": limit})
        return self._parse_items(data, "movies.anticipated")

    def get_movies_boxoffice(self, limit: int = 10) -> List[Dict]:
        """Get weekend box office movies.

        Args:
            limit: Maximum number of items to return (default 10)

        Returns:
            List of movie items with scores
        """
        logger.info(f"Fetching box office movies (limit={limit})...")
        data = self._request("movies/boxoffice", params={"limit": limit})
        return self._parse_items(data, "movies.boxoffice")

    def get_shows_trending(self, limit: int = DEFAULT_API_LIMIT) -> List[Dict]:
        """Get trending shows.

        Args:
            limit: Maximum number of items to return

        Returns:
            List of show items with scores
        """
        logger.info(f"Fetching trending shows (limit={limit})...")
        data = self._request("shows/trending", params={"limit": limit})
        return self._parse_items(data, "shows.trending")

    def get_shows_popular(self, limit: int = DEFAULT_API_LIMIT) -> List[Dict]:
        """Get popular shows.

        Args:
            limit: Maximum number of items to return

        Returns:
            List of show items with scores
        """
        logger.info(f"Fetching popular shows (limit={limit})...")
        data = self._request("shows/popular", params={"limit": limit})
        return self._parse_items(data, "shows.popular")

    def get_shows_played(
        self, period: str = "weekly", limit: int = DEFAULT_API_LIMIT
    ) -> List[Dict]:
        """Get most played shows.

        Args:
            period: Time period (daily, weekly, monthly, yearly)
            limit: Maximum number of items to return

        Returns:
            List of show items with scores
        """
        logger.info(f"Fetching played shows (period={period}, limit={limit})...")
        path = self._get_endpoint_path("shows.played", period)
        data = self._request(path, params={"limit": limit})
        return self._parse_items(data, "shows.played")

    def get_shows_watched(
        self, period: str = "weekly", limit: int = DEFAULT_API_LIMIT
    ) -> List[Dict]:
        """Get most watched shows by unique users.

        Args:
            period: Time period (daily, weekly, monthly, yearly)
            limit: Maximum number of items to return

        Returns:
            List of show items with scores
        """
        logger.info(f"Fetching watched shows (period={period}, limit={limit})...")
        path = self._get_endpoint_path("shows.watched", period)
        data = self._request(path, params={"limit": limit})
        return self._parse_items(data, "shows.watched")

    def get_shows_collected(
        self, period: str = "weekly", limit: int = DEFAULT_API_LIMIT
    ) -> List[Dict]:
        """Get most collected shows.

        Args:
            period: Time period (daily, weekly, monthly, yearly)
            limit: Maximum number of items to return

        Returns:
            List of show items with scores
        """
        logger.info(f"Fetching collected shows (period={period}, limit={limit})...")
        path = self._get_endpoint_path("shows.collected", period)
        data = self._request(path, params={"limit": limit})
        return self._parse_items(data, "shows.collected")

    def get_shows_anticipated(self, limit: int = DEFAULT_API_LIMIT) -> List[Dict]:
        """Get most anticipated upcoming shows.

        Args:
            limit: Maximum number of items to return

        Returns:
            List of show items with scores
        """
        logger.info(f"Fetching anticipated shows (limit={limit})...")
        data = self._request("shows/anticipated", params={"limit": limit})
        return self._parse_items(data, "shows.anticipated")

    def get_endpoint(
        self, endpoint_name: str, period: str = "weekly", limit: Optional[int] = None
    ) -> List[Dict]:
        """Generic method to fetch from any endpoint by name.

        Args:
            endpoint_name: Endpoint name (e.g., "movies.trending")
            period: Time period for stats endpoints
            limit: Maximum items (defaults to DEFAULT_LIMITS)

        Returns:
            List of items from the endpoint
        """
        if endpoint_name not in ENDPOINTS:
            logger.error(f"Unknown endpoint: {endpoint_name}")
            return []

        # Determine default limit from endpoint type
        endpoint_type = endpoint_name.split(".")[-1]
        if limit is None:
            limit = DEFAULT_LIMITS.get(endpoint_type, 100)

        # Route to appropriate method
        method_map = {
            "movies.trending": self.get_movies_trending,
            "movies.popular": self.get_movies_popular,
            "movies.played": lambda **k: self.get_movies_played(period=period, **k),
            "movies.watched": lambda **k: self.get_movies_watched(period=period, **k),
            "movies.collected": lambda **k: self.get_movies_collected(period=period, **k),
            "movies.anticipated": self.get_movies_anticipated,
            "movies.boxoffice": self.get_movies_boxoffice,
            "shows.trending": self.get_shows_trending,
            "shows.popular": self.get_shows_popular,
            "shows.played": lambda **k: self.get_shows_played(period=period, **k),
            "shows.watched": lambda **k: self.get_shows_watched(period=period, **k),
            "shows.collected": lambda **k: self.get_shows_collected(period=period, **k),
            "shows.anticipated": self.get_shows_anticipated,
        }

        method = method_map.get(endpoint_name)
        if method:
            return method(limit=limit)

        return []

    @staticmethod
    def get_available_endpoints() -> List[str]:
        """Get list of all available endpoint names.

        Returns:
            List of endpoint name strings
        """
        return list(ENDPOINTS.keys())

    @staticmethod
    def validate_endpoint(endpoint_name: str) -> bool:
        """Check if an endpoint name is valid.

        Args:
            endpoint_name: Endpoint name to validate

        Returns:
            True if valid, False otherwise
        """
        return endpoint_name in ENDPOINTS

    @staticmethod
    def get_endpoint_score(endpoint_name: str) -> int:
        """Get the priority score for an endpoint.

        Args:
            endpoint_name: Name of the endpoint

        Returns:
            Priority score (higher = more important)
        """
        return ENDPOINT_SCORES.get(endpoint_name, 5)

    @staticmethod
    def get_cache_ttl(endpoint_name: str) -> int:
        """Get the recommended cache TTL for an endpoint.

        Args:
            endpoint_name: Name of the endpoint

        Returns:
            Cache TTL in hours
        """
        endpoint_type = endpoint_name.split(".")[-1]
        return DEFAULT_CACHE_TTLS.get(endpoint_type, 6)
