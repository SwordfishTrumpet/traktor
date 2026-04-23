"""Official Trakt lists service for managing curated content.

This module provides the OfficialListsService which coordinates fetching,
caching, and deduplicating items from Trakt's official algorithmic lists.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from .log import logger
from .settings import CACHE_DIR
from .trakt_official import TraktOfficialClient


class OfficialListsService:
    """Service for managing Trakt official curated lists.

    Handles:
    - Fetching from multiple endpoints with parallel processing
    - Smart caching per endpoint type (different TTLs for trending vs anticipated)
    - Deduplication using endpoint scoring (items in multiple lists rank higher)
    - Aggregation into unified movie/show lists
    """

    def __init__(self, client_id: Optional[str] = None, cache_dir: Optional[Path] = None):
        """Initialize the official lists service.

        Args:
            client_id: Trakt API client ID (defaults to TRAKT_CLIENT_ID)
            cache_dir: Directory for caching (defaults to CACHE_DIR/official_lists)
        """
        self.client = TraktOfficialClient(client_id)
        self.cache_dir = cache_dir or CACHE_DIR / "official_lists"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("OfficialListsService initialized")

    def _get_cache_file(self, endpoint_name: str) -> Path:
        """Get the cache file path for an endpoint."""
        return self.cache_dir / f"{endpoint_name.replace('.', '_')}.json"

    def _get_cache_metadata_file(self) -> Path:
        """Get the cache metadata file path."""
        return self.cache_dir / "cache_metadata.json"

    def _is_cache_valid(self, endpoint_name: str) -> bool:
        """Check if cache for an endpoint is still valid.

        Args:
            endpoint_name: Name of the endpoint

        Returns:
            True if cache is valid, False otherwise
        """
        cache_file = self._get_cache_file(endpoint_name)
        if not cache_file.exists():
            return False

        try:
            # Get TTL for this endpoint type
            ttl_hours = self.client.get_cache_ttl(endpoint_name)
            cache_mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)

            if datetime.now() - cache_mtime > timedelta(hours=ttl_hours):
                logger.debug(f"Cache expired for {endpoint_name} (TTL: {ttl_hours}h)")
                return False

            return True
        except (OSError, ValueError) as e:
            logger.error(f"Error checking cache validity for {endpoint_name}: {e}")
            return False

    def _load_cache(self, endpoint_name: str) -> Optional[List[Dict]]:
        """Load cached data for an endpoint.

        Args:
            endpoint_name: Name of the endpoint

        Returns:
            List of cached items or None if not valid
        """
        if not self._is_cache_valid(endpoint_name):
            return None

        cache_file = self._get_cache_file(endpoint_name)
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.debug(f"Loaded {len(data)} items from cache for {endpoint_name}")
            return data
        except (json.JSONDecodeError, FileNotFoundError, PermissionError, OSError) as e:
            logger.error(f"Failed to load cache for {endpoint_name}: {e}")
            return None

    def _save_cache(self, endpoint_name: str, items: List[Dict]):
        """Save data to cache for an endpoint.

        Args:
            endpoint_name: Name of the endpoint
            items: List of items to cache
        """
        cache_file = self._get_cache_file(endpoint_name)
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(items, f)
            logger.debug(f"Saved {len(items)} items to cache for {endpoint_name}")
        except (PermissionError, FileNotFoundError, OSError) as e:
            logger.error(f"Failed to save cache for {endpoint_name}: {e}")

    def _load_stale_cache(self, endpoint_name: str) -> Optional[List[Dict]]:
        """Load stale cache as fallback when API request fails.

        Args:
            endpoint_name: Name of the endpoint

        Returns:
            List of cached items or None if not available/readable
        """
        cache_file = self._get_cache_file(endpoint_name)
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    logger.warning(f"Using stale cache for {endpoint_name}")
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError, PermissionError):
                pass
        return None

    def _deduplicate_items(self, all_items: List[Tuple[str, Dict]]) -> Dict[str, List[Dict]]:
        """Deduplicate items from multiple endpoints using scoring.

        Items appearing in multiple lists get their scores summed, and the
        highest-scoring items appear first in the result.

        Args:
            all_items: List of (endpoint_name, item) tuples

        Returns:
            Dict with 'movies' and 'shows' keys containing deduplicated items
        """
        # Group items by unique ID
        movies_by_id: Dict[str, Dict] = {}
        shows_by_id: Dict[str, Dict] = {}

        for endpoint_name, item in all_items:
            media_type = item.get("type")
            media_data = item.get("movie") if media_type == "movie" else item.get("show")

            if not media_data:
                continue

            ids = media_data.get("ids", {})
            imdb_id = ids.get("imdb")
            tmdb_id = ids.get("tmdb")

            # Use IMDb ID as primary key, fallback to TMDb
            unique_id = imdb_id if imdb_id else f"tmdb:{tmdb_id}" if tmdb_id else None
            if not unique_id:
                continue

            score = item.get("score", 5)

            if media_type == "movie":
                if unique_id in movies_by_id:
                    # Item already exists, sum the scores
                    movies_by_id[unique_id]["score"] += score
                    movies_by_id[unique_id]["sources"].append(endpoint_name)
                else:
                    # New item
                    movies_by_id[unique_id] = {
                        "item": item,
                        "score": score,
                        "sources": [endpoint_name],
                    }
            elif media_type == "show":
                if unique_id in shows_by_id:
                    shows_by_id[unique_id]["score"] += score
                    shows_by_id[unique_id]["sources"].append(endpoint_name)
                else:
                    shows_by_id[unique_id] = {
                        "item": item,
                        "score": score,
                        "sources": [endpoint_name],
                    }

        # Convert back to lists and sort by score (descending)
        movies = [
            {
                **entry["item"],
                "combined_score": entry["score"],
                "sources": entry["sources"],
            }
            for entry in movies_by_id.values()
        ]
        shows = [
            {
                **entry["item"],
                "combined_score": entry["score"],
                "sources": entry["sources"],
            }
            for entry in shows_by_id.values()
        ]

        # Sort by combined score (descending), then by watchers (if available)
        movies.sort(key=lambda x: (-x["combined_score"], -x.get("watchers", 0)))
        shows.sort(key=lambda x: (-x["combined_score"], -x.get("watchers", 0)))

        logger.info(
            f"Deduplication: {len(all_items)} total items -> "
            f"{len(movies)} unique movies, {len(shows)} unique shows"
        )

        return {"movies": movies, "shows": shows}

    def fetch_endpoint(
        self, endpoint_name: str, period: str = "weekly", use_cache: bool = True
    ) -> List[Dict]:
        """Fetch items from a single endpoint.

        Args:
            endpoint_name: Name of the endpoint (e.g., "movies.trending")
            period: Time period for stats endpoints
            use_cache: Whether to use caching

        Returns:
            List of items from the endpoint
        """
        # Try cache first
        if use_cache:
            cached = self._load_cache(endpoint_name)
            if cached is not None:
                logger.info(f"Using cached data for {endpoint_name}")
                return cached

        # Fetch from API
        try:
            items = self.client.get_endpoint(endpoint_name, period=period)

            # Save to cache
            if use_cache:
                self._save_cache(endpoint_name, items)

            return items
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error fetching {endpoint_name}: {e}")
            if use_cache:
                stale = self._load_stale_cache(endpoint_name)
                if stale is not None:
                    return stale
            return []
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Data error fetching {endpoint_name}: {e}")
            if use_cache:
                stale = self._load_stale_cache(endpoint_name)
                if stale is not None:
                    return stale
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching {endpoint_name}: {e}")
            if use_cache:
                stale = self._load_stale_cache(endpoint_name)
                if stale is not None:
                    return stale
            return []

    def fetch_multiple_endpoints(
        self,
        endpoint_names: List[str],
        period: str = "weekly",
        use_cache: bool = True,
        max_workers: int = 3,
    ) -> Dict[str, List[Dict]]:
        """Fetch from multiple endpoints in parallel.

        Args:
            endpoint_names: List of endpoint names to fetch
            period: Time period for stats endpoints
            use_cache: Whether to use caching
            max_workers: Maximum parallel workers

        Returns:
            Dict mapping endpoint name to list of items
        """
        logger.info(f"Fetching {len(endpoint_names)} endpoint(s) with {max_workers} workers...")
        results = {}

        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_endpoint = {
                executor.submit(
                    self.fetch_endpoint, endpoint_name, period, use_cache
                ): endpoint_name
                for endpoint_name in endpoint_names
            }

            for future in as_completed(future_to_endpoint):
                endpoint_name = future_to_endpoint[future]
                try:
                    items = future.result()
                    results[endpoint_name] = items
                    logger.debug(f"Fetched {len(items)} items from {endpoint_name}")
                except (requests.exceptions.RequestException, ValueError, KeyError, TypeError) as e:
                    logger.error(f"Failed to fetch {endpoint_name}: {e}")
                    results[endpoint_name] = []

        return results

    def aggregate_items(self, endpoint_results: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
        """Aggregate and deduplicate items from multiple endpoints.

        Args:
            endpoint_results: Dict mapping endpoint name to items

        Returns:
            Dict with 'movies' and 'shows' keys containing deduplicated items
        """
        all_items = []
        for endpoint_name, items in endpoint_results.items():
            for item in items:
                all_items.append((endpoint_name, item))

        return self._deduplicate_items(all_items)

    def get_playlists_from_endpoints(
        self,
        endpoint_names: List[str],
        period: str = "weekly",
        use_cache: bool = True,
        separate_playlists: bool = True,
    ) -> List[Dict]:
        """Generate playlist configurations from endpoints.

        Args:
            endpoint_names: List of endpoint names to fetch
            period: Time period for stats endpoints
            use_cache: Whether to use caching
            separate_playlists: If True, create separate playlist per endpoint

        Returns:
            List of playlist dicts with 'name', 'items', 'description'
        """
        playlists = []

        if separate_playlists:
            # Create separate playlist for each endpoint
            for endpoint_name in endpoint_names:
                items = self.fetch_endpoint(endpoint_name, period, use_cache)

                if not items:
                    logger.warning(f"No items for {endpoint_name}, skipping playlist")
                    continue

                # Generate playlist name
                parts = endpoint_name.split(".")
                media_type = parts[0].capitalize()  # Movies or Shows
                category = parts[1].capitalize() if len(parts) > 1 else "Unknown"

                # Add period for stats endpoints
                if category.lower() in ["played", "watched", "collected"]:
                    playlist_name = f"Trakt {media_type} - {category} ({period.capitalize()})"
                    description = f"{category} {media_type.lower()} on Trakt ({period}). Updated automatically."
                elif category.lower() == "boxoffice":
                    playlist_name = f"Trakt {media_type} - Box Office"
                    description = f"Weekend box office {media_type.lower()}. Updated automatically."
                else:
                    playlist_name = f"Trakt {media_type} - {category}"
                    description = (
                        f"{category} {media_type.lower()} on Trakt. Updated automatically."
                    )

                # Sort items by year (descending) within each list
                # This ensures newer movies/shows appear first
                items.sort(
                    key=lambda x: (
                        x.get("movie", {}).get("year", 0)
                        if x.get("type") == "movie"
                        else x.get("show", {}).get("year", 0)
                    ),
                    reverse=True,
                )

                playlists.append(
                    {
                        "name": playlist_name,
                        "items": items,
                        "description": description,
                        "source": endpoint_name,
                    }
                )

                logger.info(f"Created playlist '{playlist_name}' with {len(items)} items")
        else:
            # Create aggregated playlists (one for movies, one for shows)
            results = self.fetch_multiple_endpoints(endpoint_names, period, use_cache)
            aggregated = self.aggregate_items(results)

            if aggregated["movies"]:
                # Sort movies by year descending (newest first)
                aggregated["movies"].sort(
                    key=lambda x: x.get("movie", {}).get("year", 0), reverse=True
                )
                playlists.append(
                    {
                        "name": "Trakt Official - Movies",
                        "items": aggregated["movies"],
                        "description": f"Curated from Trakt official lists ({len(endpoint_names)} sources). Updated automatically.",
                        "source": "aggregated.movies",
                    }
                )
                logger.info(
                    f"Created aggregated movie playlist with {len(aggregated['movies'])} items"
                )

            if aggregated["shows"]:
                # Sort shows by year descending (newest first)
                aggregated["shows"].sort(
                    key=lambda x: x.get("show", {}).get("year", 0), reverse=True
                )
                playlists.append(
                    {
                        "name": "Trakt Official - Shows",
                        "items": aggregated["shows"],
                        "description": f"Curated from Trakt official lists ({len(endpoint_names)} sources). Updated automatically.",
                        "source": "aggregated.shows",
                    }
                )
                logger.info(
                    f"Created aggregated show playlist with {len(aggregated['shows'])} items"
                )

        return playlists

    def clear_cache(self, endpoint_name: Optional[str] = None):
        """Clear cache for an endpoint or all endpoints.

        Args:
            endpoint_name: Specific endpoint to clear, or None for all
        """
        if endpoint_name:
            cache_file = self._get_cache_file(endpoint_name)
            if cache_file.exists():
                cache_file.unlink()
                logger.info(f"Cleared cache for {endpoint_name}")
        else:
            # Clear all cache files
            for cache_file in self.cache_dir.glob("*.json"):
                cache_file.unlink()
            logger.info("Cleared all official lists cache")

    @staticmethod
    def get_default_endpoints() -> List[str]:
        """Get the default recommended endpoints.

        Returns:
            List of endpoint names for a good default experience
        """
        return [
            "movies.trending",
            "movies.popular",
            "shows.trending",
            "shows.popular",
        ]

    @staticmethod
    def parse_endpoint_list(endpoint_string: str) -> List[str]:
        """Parse a comma-separated endpoint list.

        Args:
            endpoint_string: Comma-separated endpoint names

        Returns:
            List of valid endpoint names
        """
        if not endpoint_string:
            return []

        endpoints = [e.strip() for e in endpoint_string.split(",")]
        valid_endpoints = []

        for endpoint in endpoints:
            if TraktOfficialClient.validate_endpoint(endpoint):
                valid_endpoints.append(endpoint)
            else:
                logger.warning(f"Invalid endpoint: {endpoint}")

        return valid_endpoints
