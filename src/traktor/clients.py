"""External service clients and Plex cache management."""

import gzip
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlencode

import requests
from plexapi.exceptions import BadRequest, NotFound

from .config import save_config
from .log import get_correlation_id, logger
from .performance import performance_monitor
from .resilience import CircuitBreakerOpen, retry_with_backoff, trakt_circuit_breaker
from .settings import (
    CACHE_DIR,
    CACHE_MAX_AGE_HOURS,
    CACHE_VERSION,
    TRAKT_ACCESS_TOKEN,
    TRAKT_API_URL,
    TRAKT_CLIENT_ID,
    TRAKT_CLIENT_SECRET,
    TRAKT_REDIRECT_URI,
    TRAKT_REFRESH_TOKEN,
)
from .utils import normalize_tmdb_id, parse_timestamp

# Constants for API pagination and safety limits
MAX_HISTORY_PAGES = 1000  # Safety limit to prevent infinite loops in pagination

# Rate limiting constants
TRAKT_RATE_LIMIT_REQUESTS = 1000  # requests per
TRAKT_RATE_LIMIT_WINDOW = 300  # 5 minutes in seconds
MIN_REQUEST_INTERVAL = TRAKT_RATE_LIMIT_WINDOW / TRAKT_RATE_LIMIT_REQUESTS  # 0.3 seconds
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # exponential backoff base

# Connection pooling constants
CONNECTION_POOL_SIZE = 10  # Maximum connections to keep in pool
CONNECTION_POOL_MAXSIZE = 10  # Maximum connections to keep in pool for reuse

# Playlist management constants
MAX_PLAYLIST_SIZE_FOR_INCREMENTAL = 1000  # Playlists larger than this are deleted and recreated
DEFAULT_PLAYLIST_BATCH_SIZE = 500  # Number of items to add per batch when updating playlists
LIBRARY_CACHE_LOG_INTERVAL = 5000  # Log progress every N items during cache building
CONTAINER_SIZE = 500  # Items per page when fetching all library items

# API pagination and data fetching constants
DEFAULT_API_PAGE_SIZE = 100  # Default items per page for API requests
DEFAULT_RECENTLY_ADDED_MAXRESULTS = 1000  # Max results for recentlyAdded API

# HTTP status codes
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_RATE_LIMIT = 429


class RateLimiter:
    """Thread-safe rate limiter for API requests.

    Enforces a minimum interval between requests to comply with API rate limits.
    Uses threading.Lock() for thread safety in multi-threaded environments.

    Example:
        limiter = RateLimiter(min_interval=0.3)  # 0.3 seconds between requests
        limiter.wait()  # Blocks until it's safe to make a request
    """

    def __init__(self, min_interval: float):
        """Initialize rate limiter.

        Args:
            min_interval: Minimum seconds between requests
        """
        self.min_interval = min_interval
        self._last_request_time = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Wait until it's safe to make a request.

        Blocks if necessary to maintain the minimum interval between requests.
        Thread-safe - can be called from multiple threads concurrently.
        """
        with self._lock:
            current_time = time.time()
            time_since_last = current_time - self._last_request_time
            if time_since_last < self.min_interval:
                sleep_time = self.min_interval - time_since_last
                logger.debug(f"Rate limiting: sleeping {sleep_time:.3f}s")
                time.sleep(sleep_time)
            self._last_request_time = time.time()


class CacheManager:
    """Manages caching of Plex library data for fast lookups."""

    def __init__(self, plex_server: Any) -> None:
        self.plex_server = plex_server
        self.cache_file = CACHE_DIR / "plex_library_cache.json.gz"
        self.cache_meta_file = CACHE_DIR / "cache_metadata.json"
        self.memory_cache: Dict[str, Any] = {}
        self._plex_objects: Dict[str, Any] = {}
        # Lazily-built reverse indexes (see properties below). Invalidated
        # whenever the memory cache is replaced or extended.
        self._movie_key_to_ids: Optional[Dict[int, Tuple[Optional[str], Optional[str]]]] = None
        self._show_key_to_imdb: Optional[Dict[int, str]] = None
        self._movie_imdb_to_rating_key: Optional[Dict[str, int]] = None

    def _invalidate_reverse_indexes(self) -> None:
        """Drop lazily-built reverse indexes; rebuilt on next property access."""
        self._movie_key_to_ids = None
        self._show_key_to_imdb = None
        self._movie_imdb_to_rating_key = None

    @property
    def movie_key_to_ids(self) -> Dict[int, Tuple[Optional[str], Optional[str]]]:
        """Map Plex movie rating key -> (imdb_id, tmdb_id), built once.

        Pure function of the cache: avoids rebuilding this reverse map on every
        watch-sync pull (see TODO audit A3/B3).
        """
        if self._movie_key_to_ids is None:
            ids_map: Dict[int, Tuple[Optional[str], Optional[str]]] = {}
            for imdb_id, data in self.memory_cache.get("movies_by_imdb", {}).items():
                rating_key = data.get("ratingKey")
                if rating_key:
                    ids_map.setdefault(rating_key, (imdb_id, None))
            for tmdb_id, data in self.memory_cache.get("movies_by_tmdb", {}).items():
                rating_key = data.get("ratingKey")
                if rating_key:
                    existing = ids_map.get(rating_key)
                    if existing:
                        ids_map[rating_key] = (existing[0], tmdb_id)
                    else:
                        ids_map[rating_key] = (None, tmdb_id)
            self._movie_key_to_ids = ids_map
        return self._movie_key_to_ids

    @property
    def show_key_to_imdb(self) -> Dict[int, str]:
        """Map Plex show rating key -> show imdb_id, built once."""
        if self._show_key_to_imdb is None:
            self._show_key_to_imdb = {
                data["ratingKey"]: imdb_id
                for imdb_id, data in self.memory_cache.get("shows_by_imdb", {}).items()
                if data.get("ratingKey")
            }
        return self._show_key_to_imdb

    @property
    def movie_imdb_to_rating_key(self) -> Dict[str, int]:
        """Map movie imdb_id -> Plex rating key, built once."""
        if self._movie_imdb_to_rating_key is None:
            self._movie_imdb_to_rating_key = {
                imdb_id: data["ratingKey"]
                for imdb_id, data in self.memory_cache.get("movies_by_imdb", {}).items()
                if data.get("ratingKey") and imdb_id
            }
        return self._movie_imdb_to_rating_key

    def _get_library_hash(self) -> Optional[str]:
        """Get a hash of current library state for cache invalidation."""
        try:
            sections = self.plex_server.library.sections()
            hash_input = ""
            for section in sections:
                total_size = getattr(section, "totalSize", 0)
                if not total_size:
                    try:
                        total_size = len(section.all())
                    except Exception:
                        total_size = 0
                hash_input += f"{section.title}:{section.type}:{total_size}:"
            return hashlib.sha256(hash_input.encode()).hexdigest()
        except (AttributeError, TypeError, ValueError) as e:
            logger.error(f"Failed to get library hash due to data error: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get library hash: {e}")
            return None

    def _is_cache_valid(self):
        """Check if cache is still valid (not expired and library hasn't changed)."""
        if not self.cache_file.exists() or not self.cache_meta_file.exists():
            # Check for legacy uncompressed cache
            legacy_cache = CACHE_DIR / "plex_library_cache.json"
            if legacy_cache.exists():
                logger.debug("Found legacy uncompressed cache, will rebuild")
            return False

        try:
            with open(self.cache_meta_file, "r") as f:
                meta = json.load(f)

            if meta.get("version") != CACHE_VERSION:
                logger.debug("Cache version mismatch")
                return False

            cached_time = datetime.fromisoformat(meta.get("created", "2000-01-01"))
            if cached_time.tzinfo is None:
                cached_time = cached_time.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - cached_time > timedelta(hours=CACHE_MAX_AGE_HOURS):
                logger.debug("Cache expired")
                return False

            current_hash = self._get_library_hash()
            if current_hash != meta.get("library_hash"):
                logger.debug("Library changed, cache invalid")
                return False

            return True

        except (json.JSONDecodeError, FileNotFoundError, PermissionError) as e:
            logger.error(f"Cache validation error (file issue): {e}")
            return False
        except Exception as e:
            logger.error(f"Cache validation error: {e}")
            return False

    def _validate_cache_structure(self) -> bool:
        """Validate that loaded cache has required structure.

        Returns:
            True if cache structure is valid, False if corrupt/incomplete
        """
        if not isinstance(self.memory_cache, dict):
            logger.warning("Cache is not a dict, rebuilding...")
            return False

        required_keys = [
            "movies_by_imdb",
            "movies_by_tmdb",
            "shows_by_imdb",
            "shows_by_tmdb",
            "movies_list",
            "shows_list",
            "by_rating_key",
        ]

        for key in required_keys:
            if key not in self.memory_cache:
                logger.warning(f"Cache missing required key '{key}', rebuilding...")
                return False
            if not isinstance(self.memory_cache[key], (dict, list)):
                logger.warning(f"Cache key '{key}' has wrong type, rebuilding...")
                return False

        return True

    def load_cache(self, force_refresh: bool = False, incremental: bool = True) -> bool:
        """Load cache from disk or build new cache.

        Args:
            force_refresh: If True, rebuild cache from scratch
            incremental: If True (and force_refresh is False), try incremental update

        Returns:
            True if loaded from disk/updated incrementally, False if rebuilt
        """
        self._invalidate_reverse_indexes()
        if not force_refresh and self._is_cache_valid():
            logger.info("Loading library cache from disk...")
            try:
                with gzip.open(self.cache_file, "rt", encoding="utf-8") as f:
                    self.memory_cache = json.load(f)
                logger.info(f"Loaded cache with {len(self.memory_cache)} entries")

                # Validate structure - if corrupt, rebuild
                if not self._validate_cache_structure():
                    logger.warning("Cache structure validation failed, rebuilding...")
                    self.memory_cache = {}
                    self._plex_objects.clear()
                    self._build_cache()
                    return False

                # Try incremental update if enabled
                if incremental:
                    if self._incremental_cache_update():
                        return True
                    # If incremental fails, continue with loaded cache
                    # (it will be valid until expiry)

                return True
            except gzip.BadGzipFile:
                logger.warning("Cache file is corrupted, will rebuild")
            except Exception as e:
                logger.error(f"Failed to load cache: {e}")
                # Fall through to build fresh cache

        # Check for legacy uncompressed cache
        legacy_cache = CACHE_DIR / "plex_library_cache.json"
        if legacy_cache.exists() and not force_refresh:
            logger.info("Found legacy uncompressed cache, migrating to compressed format...")
            try:
                with open(legacy_cache, "r", encoding="utf-8") as f:
                    self.memory_cache = json.load(f)
                logger.info(f"Loaded legacy cache with {len(self.memory_cache)} entries")
                # Save in new compressed format
                self._save_cache()
                # Remove legacy cache
                legacy_cache.unlink()
                logger.info("Migrated legacy cache to compressed format")
                return True
            except Exception as e:
                logger.error(f"Failed to migrate legacy cache: {e}")
                # Fall through to build fresh cache

        logger.info("Building fresh library cache...")
        self._build_cache()
        return False

    @staticmethod
    def _parse_guid_for_ids(guid_str, ids):
        """Parse a single guid string and extract external IDs."""
        if "imdb://" in guid_str:
            ids["imdb"].add(guid_str.split("imdb://")[-1].split("?")[0].rstrip(">"))
        elif "tmdb://" in guid_str:
            ids["tmdb"].add(guid_str.split("tmdb://")[-1].split("?")[0].rstrip(">"))

    @staticmethod
    def _extract_external_ids(item):
        """Extract supported external IDs from Plex guid data."""
        ids = {"imdb": set(), "tmdb": set()}

        if item.guid:
            guid_str = str(item.guid)
            CacheManager._parse_guid_for_ids(guid_str, ids)

        if hasattr(item, "guids"):
            for guid in item.guids:
                guid_str = str(guid)
                CacheManager._parse_guid_for_ids(guid_str, ids)

        return ids

    def _safe_get_attr(self, item, attr, default=None):
        """Safely access a PlexAPI attribute without triggering a server reload.

        PlexAPI's __getattribute__ triggers _reload() when an attribute value
        is None (treating it as "not loaded"), which bypasses getattr's default.
        This uses object.__getattribute__ to bypass PlexAPI's override.
        """
        try:
            return object.__getattribute__(item, attr)
        except AttributeError:
            return default

    def _add_item_to_cache(self, item, media_type):
        """Add a Plex item to the in-memory cache and ID indexes."""
        # Store Plex object reference to avoid re-fetching from API later
        self._plex_objects[str(item.ratingKey)] = item
        last_viewed = self._safe_get_attr(item, "lastViewedAt", None)
        if last_viewed is not None:
            last_viewed = last_viewed.isoformat()

        item_data = {
            "title": item.title,
            "year": item.year,
            "ratingKey": item.ratingKey,
            "guid": str(item.guid) if item.guid else None,
            "isWatched": self._safe_get_attr(item, "isWatched", False),
            "lastViewedAt": last_viewed,
        }
        self.memory_cache[f"{media_type}s_list"].append(item_data)

        # Add to rating_key index for O(1) lookup
        self.memory_cache["by_rating_key"][item.ratingKey] = item_data

        external_ids = self._extract_external_ids(item)
        for imdb_id in external_ids["imdb"]:
            self.memory_cache[f"{media_type}s_by_imdb"][imdb_id] = item_data
        for tmdb_id in external_ids["tmdb"]:
            # Ensure TMDb IDs are stored as strings for consistent lookup
            self.memory_cache[f"{media_type}s_by_tmdb"][str(tmdb_id)] = item_data

        self._invalidate_reverse_indexes()

    def _build_cache(self):
        """Build cache by scanning all library items."""
        self._plex_objects.clear()
        self._invalidate_reverse_indexes()
        self.memory_cache = {
            "movies_by_imdb": {},
            "movies_by_tmdb": {},
            "shows_by_imdb": {},
            "shows_by_tmdb": {},
            "movies_list": [],
            "shows_list": [],
            "by_rating_key": {},  # O(1) lookup for watch status
        }

        start_time = time.time()

        for section in self.plex_server.library.sections():
            if section.type in ("movie", "show"):
                logger.info(f"Caching {section.type}s from '{section.title}'...")
                items = section.all(container_size=CONTAINER_SIZE)
            else:
                continue

            total = len(items)
            for idx, item in enumerate(items):
                if idx % LIBRARY_CACHE_LOG_INTERVAL == 0:
                    logger.debug(f"  Cached {idx}/{total} {section.type}s...")
                try:
                    self._add_item_to_cache(item, section.type)
                except Exception as e:
                    logger.warning(
                        f"Failed to cache item '{getattr(item, 'title', 'unknown')}': {e}"
                    )
                    continue

        elapsed = time.time() - start_time
        logger.info(f"Cache built in {elapsed:.2f} seconds")
        logger.info(
            f"  Movies: {len(self.memory_cache['movies_list'])} ({len(self.memory_cache['movies_by_imdb'])} indexed by IMDB)"
        )
        logger.info(
            f"  Shows: {len(self.memory_cache['shows_list'])} ({len(self.memory_cache['shows_by_imdb'])} indexed by IMDB)"
        )

        self._save_cache()

    def _save_cache(self):
        """Save cache to disk with gzip compression."""
        try:
            # Serialize once, reuse for both compression and stats
            cache_json = json.dumps(self.memory_cache)
            uncompressed_bytes = cache_json.encode("utf-8")
            uncompressed_size = len(uncompressed_bytes)

            with gzip.open(self.cache_file, "wt", encoding="utf-8", compresslevel=6) as f:
                f.write(cache_json)

            compressed_size = self.cache_file.stat().st_size
            compression_ratio = (
                (1 - compressed_size / uncompressed_size) * 100 if uncompressed_size > 0 else 0
            )

            meta = {
                "version": CACHE_VERSION,
                "created": datetime.now(timezone.utc).isoformat(),
                "library_hash": self._get_library_hash(),
                "last_update": datetime.now(timezone.utc).isoformat(),
                "compression": {
                    "uncompressed_bytes": uncompressed_size,
                    "compressed_bytes": compressed_size,
                    "ratio_percent": round(compression_ratio, 1),
                },
            }
            with open(self.cache_meta_file, "w") as f:
                json.dump(meta, f)

            logger.info(
                f"Cache saved to {self.cache_file} "
                f"({uncompressed_size:,} bytes -> {compressed_size:,} bytes, "
                f"{compression_ratio:.1f}% compression)"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")
            return False

    def _get_last_update_timestamp(self) -> Optional[datetime]:
        """Get the timestamp of the last cache update.

        Returns:
            datetime of last update or None if not available
        """
        if not self.cache_meta_file.exists():
            return None

        try:
            with open(self.cache_meta_file, "r") as f:
                meta = json.load(f)
            last_update_str = meta.get("last_update") or meta.get("created")
            if last_update_str:
                dt = datetime.fromisoformat(last_update_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            return None
        except Exception as e:
            logger.debug(f"Could not get last update timestamp: {e}")
            return None

    @staticmethod
    def _normalize_added_at(added_at) -> Optional[datetime]:
        """Normalize a Plex ``addedAt`` value to a timezone-aware UTC datetime.

        plexapi >= 4.17 converts ``addedAt`` to a naive local ``datetime`` via
        ``utils.toDatetime()``; older versions exposed a raw epoch integer.
        This is a thin wrapper over the canonical :func:`utils.parse_timestamp`.

        Returns:
            Timezone-aware datetime, or None if the value is missing/unparseable.
        """
        return parse_timestamp(added_at)

    def _incremental_cache_update(self) -> bool:
        """Update cache incrementally using recentlyAdded API.

        Fetches only items added since the last cache update and merges
        them with the existing cache. This is much faster than a full rebuild
        for libraries with few new items.

        Returns:
            True if successful, False if fallback to full rebuild needed
        """
        last_update = self._get_last_update_timestamp()
        if not last_update:
            logger.debug("No last update timestamp found, falling back to full rebuild")
            return False

        # Only do incremental update if last update was within last 24 hours
        # and cache hasn't expired
        time_since_update = datetime.now(timezone.utc) - last_update
        if time_since_update > timedelta(hours=CACHE_MAX_AGE_HOURS):
            logger.info(
                f"Cache is {time_since_update.total_seconds() / 3600:.1f} hours old, doing full rebuild"
            )
            return False

        new_items_count = 0
        start_time = time.time()

        try:
            for section in self.plex_server.library.sections():
                if section.type not in ("movie", "show"):
                    continue

                # Calculate minutes since last update for recentlyAdded
                minutes_since = int(time_since_update.total_seconds() / 60) + 1  # +1 buffer

                logger.info(
                    f"Checking for new {section.type}s in '{section.title}' (since {minutes_since} minutes ago)..."
                )

                try:
                    # Get recently added items (fetch more to filter by date)
                    recent_items = section.recentlyAdded(
                        maxresults=DEFAULT_RECENTLY_ADDED_MAXRESULTS
                    )

                    # Filter items by added date
                    cutoff_time = datetime.now(timezone.utc) - time_since_update
                    new_items = []
                    for item in recent_items:
                        added_at = self._normalize_added_at(
                            self._safe_get_attr(item, "addedAt", None)
                        )
                        if added_at is not None and added_at > cutoff_time:
                            new_items.append(item)

                    if new_items:
                        logger.info(f"  Found {len(new_items)} new {section.type}(s)")
                        for item in new_items:
                            self._add_item_to_cache(item, section.type)
                            new_items_count += 1
                    else:
                        logger.debug(f"  No new {section.type}s found")

                except Exception as e:
                    logger.warning(
                        f"  Could not get recentlyAdded for section '{section.title}': {e}"
                    )
                    # If recentlyAdded fails, fall back to full rebuild
                    return False

            if new_items_count > 0:
                elapsed = time.time() - start_time
                logger.info(
                    f"Incremental update added {new_items_count} items in {elapsed:.2f} seconds"
                )
                self._save_cache()
            else:
                logger.info("No new items found, cache is up to date")
                # Update timestamp even if no new items
                self._save_cache()

            return True

        except Exception as e:
            logger.error(f"Incremental cache update failed: {e}")
            return False

    def find_movie_by_imdb(self, imdb_id: str) -> Optional[Dict[str, Any]]:
        return self.memory_cache["movies_by_imdb"].get(imdb_id)

    def find_movie_by_tmdb(self, tmdb_id: Union[str, int]) -> Optional[Dict[str, Any]]:
        return self.memory_cache["movies_by_tmdb"].get(str(tmdb_id))

    def find_show_by_imdb(self, imdb_id: str) -> Optional[Dict[str, Any]]:
        return self.memory_cache["shows_by_imdb"].get(imdb_id)

    def find_show_by_tmdb(self, tmdb_id: Union[str, int]) -> Optional[Dict[str, Any]]:
        return self.memory_cache["shows_by_tmdb"].get(str(tmdb_id))


class TraktAuth:
    """Handle Trakt OAuth authentication."""

    # Trakt token sanity limits. Trakt currently issues 32-character tokens for
    # both access and refresh (verified live 2026-08-16); historically they were
    # longer JWTs. We only guard against obvious garbage here: placeholder text
    # like "new-access-token" (16 chars) or truncated values far below any
    # plausible Trakt format. Keep these floors LOW so a legitimate format
    # change can never lock users out again.
    MIN_ACCESS_TOKEN_LENGTH = 20
    MIN_REFRESH_TOKEN_LENGTH = 20
    PLACEHOLDER_TOKENS = frozenset(
        {
            "new-access-token",
            "new-refresh-token",
            "your-access-token",
            "your-refresh-token",
            "access-token",
            "refresh-token",
            "changeme",
            "change-me",
            "<access-token>",
            "<refresh-token>",
        }
    )

    def __init__(self):
        # Load tokens from environment if available
        self.access_token = TRAKT_ACCESS_TOKEN
        self.refresh_token = TRAKT_REFRESH_TOKEN

    def has_valid_tokens(self) -> bool:
        """Check whether loaded tokens look like real Trakt tokens.

        Returns True only if both tokens are present, are long enough to be
        genuine Trakt tokens, and are not placeholder text. This prevents
        broken/placeholder .env values from silently passing the auth check
        and failing later with 401s.

        Returns:
            True if tokens appear valid, False otherwise
        """
        if not self.access_token or not self.refresh_token:
            return False

        access = self.access_token.strip()
        refresh = self.refresh_token.strip()

        if access.lower() in self.PLACEHOLDER_TOKENS or refresh.lower() in self.PLACEHOLDER_TOKENS:
            logger.warning(
                "Trakt tokens in .env look like placeholder text "
                f"(access={len(access)} chars, refresh={len(refresh)} chars). "
                "Run with --force-auth to authenticate with real tokens."
            )
            return False

        if (
            len(access) < self.MIN_ACCESS_TOKEN_LENGTH
            or len(refresh) < self.MIN_REFRESH_TOKEN_LENGTH
        ):
            logger.warning(
                "Trakt tokens in .env are too short to be valid "
                f"(access={len(access)} chars, refresh={len(refresh)} chars; "
                f"expected >= {self.MIN_ACCESS_TOKEN_LENGTH} and >= {self.MIN_REFRESH_TOKEN_LENGTH}). "
                "Run with --force-auth to re-authenticate."
            )
            return False

        return True

    def save_tokens(self):
        """Save tokens to .env file for persistence.

        When tokens are obtained or refreshed, they are saved to the .env file
        so the user doesn't need to re-authenticate on each run.
        """
        logger.debug("Saving tokens to .env file")
        if not self.has_valid_tokens():
            logger.error(
                "Refusing to save tokens: access/refresh token values are "
                "missing, too short, or look like placeholders "
                f"(access={len(self.access_token or '')} chars, "
                f"refresh={len(self.refresh_token or '')} chars)"
            )
            return False
        if self.access_token and self.refresh_token:
            logger.info("Authentication tokens obtained successfully")
            # Log masked tokens for debugging (security: don't log full tokens)
            masked_access = (
                f"{self.access_token[:4]}...{self.access_token[-4:]}"
                if len(self.access_token) > 8
                else "****"
            )
            masked_refresh = (
                f"{self.refresh_token[:4]}...{self.refresh_token[-4:]}"
                if len(self.refresh_token) > 8
                else "****"
            )
            logger.debug(f"Access token: {masked_access}")
            logger.debug(f"Refresh token: {masked_refresh}")

            # Save tokens to .env file
            try:
                env_path = Path("/app/.env") if Path("/app/.env").exists() else Path(".env")
                if env_path.exists():
                    with open(env_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()

                    # Update or add token lines
                    token_vars = {
                        "TRAKT_ACCESS_TOKEN": self.access_token,
                        "TRAKT_REFRESH_TOKEN": self.refresh_token,
                    }

                    for var_name, var_value in token_vars.items():
                        found = False
                        for i, line in enumerate(lines):
                            # Match lines that start with the var name (allowing for whitespace/comments)
                            stripped = line.strip()
                            if stripped.startswith(f"{var_name}=") or stripped == var_name:
                                lines[i] = f"{var_name}={var_value}\n"
                                found = True
                                break
                        if not found:
                            lines.append(f"{var_name}={var_value}\n")

                    with open(env_path, "w", encoding="utf-8") as f:
                        f.writelines(lines)

                    logger.info(f"Tokens saved to {env_path}")
                    logger.info("You can now run traktor without --force-auth")
                else:
                    logger.warning(f".env file not found at {env_path}, tokens not saved")
            except Exception as e:
                logger.warning(f"Failed to save tokens to .env: {e}")
                logger.info("Tokens are in memory for this session only")
        return True

    def get_auth_url(self) -> str:
        params = {
            "response_type": "code",
            "client_id": TRAKT_CLIENT_ID,
            "redirect_uri": TRAKT_REDIRECT_URI,
        }
        url = f"{TRAKT_API_URL}/oauth/authorize?{urlencode(params)}"
        logger.debug("Generated Trakt auth URL")
        return url

    def authenticate(self, auth_code: str) -> bool:
        logger.info("Authenticating with Trakt using authorization code...")
        logger.debug(f"Auth code length: {len(auth_code)}")

        payload = {
            "code": auth_code,
            "client_id": TRAKT_CLIENT_ID,
            "client_secret": TRAKT_CLIENT_SECRET,
            "redirect_uri": TRAKT_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
        logger.debug(f"Request payload keys: {list(payload.keys())}")

        # Create a temporary session for authentication (no connection pool needed for single request)
        session = requests.Session()
        try:
            response = session.post(f"{TRAKT_API_URL}/oauth/token", json=payload, timeout=30)
            logger.debug(f"Auth response status: {response.status_code}")

            response.raise_for_status()
            data = response.json()

            self.access_token = data["access_token"]
            self.refresh_token = data["refresh_token"]

            logger.info("Authentication successful")
            logger.debug(f"Access token received (length: {len(self.access_token)})")
            logger.debug(f"Refresh token received (length: {len(self.refresh_token)})")
            logger.debug(f"Expires in: {data.get('expires_in', 'unknown')} seconds")
            logger.debug(f"Created at: {data.get('created_at', 'unknown')}")

            self.save_tokens()
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Authentication request failed: {e}", exc_info=True)
            raise
        except KeyError as e:
            logger.error(f"Missing key in auth response: {e}", exc_info=True)
            raise
        finally:
            session.close()

    def refresh_access_token(self) -> bool:
        logger.info("Refreshing Trakt access token...")

        if not self.refresh_token:
            logger.warning("No refresh token available")
            return False

        logger.debug(f"Using refresh token (length: {len(self.refresh_token)})")

        # Create a temporary session for token refresh
        session = requests.Session()
        try:
            response = session.post(
                f"{TRAKT_API_URL}/oauth/token",
                json={
                    "refresh_token": self.refresh_token,
                    "client_id": TRAKT_CLIENT_ID,
                    "client_secret": TRAKT_CLIENT_SECRET,
                    "redirect_uri": TRAKT_REDIRECT_URI,
                    "grant_type": "refresh_token",
                },
                timeout=30,
            )

            logger.debug(f"Token refresh response status: {response.status_code}")

            if response.status_code == HTTP_OK:
                data = response.json()
                self.access_token = data["access_token"]
                self.refresh_token = data["refresh_token"]
                logger.info("Token refresh successful")
                logger.debug(f"New access token (length: {len(self.access_token)})")
                self.save_tokens()
                return True

            # 400 Bad Request means the refresh token is invalid/expired
            if response.status_code == 400:
                logger.error("Token refresh failed with 400 - refresh token is invalid or expired")
                logger.error(
                    "Trakt authentication expired. Run with --force-auth to re-authenticate."
                )
                # Clear stored tokens to force re-authentication on next run
                self.access_token = None
                self.refresh_token = None
                return False

            logger.error(f"Token refresh failed with status {response.status_code}")
            logger.debug(f"Response status: {response.status_code}")
            return False

        except requests.exceptions.RequestException as e:
            logger.error(f"Token refresh request failed: {e}", exc_info=True)
            return False
        except (KeyError, ValueError) as e:
            logger.error(f"Token refresh response error: {e}", exc_info=True)
            return False
        finally:
            session.close()

    def get_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": TRAKT_CLIENT_ID if TRAKT_CLIENT_ID is not None else "",
            "Authorization": f"Bearer {self.access_token}" if self.access_token is not None else "",
        }
        logger.debug("Request headers prepared")
        return headers


class TraktClient:
    """Client for interacting with Trakt API with connection pooling."""

    def __init__(self, auth):
        self.auth = auth
        # Use shared rate limiter (1000 requests per 5 minutes = 0.3s interval)
        self._rate_limiter = RateLimiter(MIN_REQUEST_INTERVAL)
        # Create a session with connection pooling for better performance
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=CONNECTION_POOL_SIZE,
            pool_maxsize=CONNECTION_POOL_MAXSIZE,
            max_retries=0,  # We handle retries manually with exponential backoff
        )
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        logger.debug("TraktClient initialized with connection pooling")

    def _request_with_retry(
        self, method, url, headers=None, params=None, json_data=None, timeout=30
    ):
        """Make a request with exponential backoff retry logic using connection pool.

        Args:
            method: HTTP method ('GET', 'POST', etc.)
            url: Request URL
            headers: Request headers
            params: Query parameters
            json_data: JSON payload for POST requests
            timeout: Request timeout

        Returns:
            Response object

        Raises:
            requests.exceptions.RequestException: If all retries fail
        """
        for attempt in range(MAX_RETRIES):
            try:
                self._rate_limiter.wait()

                if method.upper() == "GET":
                    response = self._session.get(
                        url, headers=headers, params=params, timeout=timeout
                    )
                elif method.upper() == "POST":
                    response = self._session.post(
                        url, headers=headers, json=json_data, timeout=timeout
                    )
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                # Handle rate limit (429) with retry
                if response.status_code == HTTP_RATE_LIMIT:
                    retry_after = int(
                        response.headers.get("Retry-After", RETRY_BACKOFF_BASE**attempt)
                    )
                    logger.warning(
                        f"Rate limited (429). Retry after {retry_after}s. Attempt {attempt + 1}/{MAX_RETRIES}"
                    )
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(retry_after)
                        continue

                # Handle server errors (5xx) with exponential backoff
                if 500 <= response.status_code < 600:
                    wait_time = RETRY_BACKOFF_BASE**attempt
                    logger.warning(
                        f"Server error ({response.status_code}). Retrying in {wait_time}s. Attempt {attempt + 1}/{MAX_RETRIES}"
                    )
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(wait_time)
                        continue

                return response

            except requests.exceptions.Timeout:
                wait_time = RETRY_BACKOFF_BASE**attempt
                logger.warning(
                    f"Request timeout. Retrying in {wait_time}s. Attempt {attempt + 1}/{MAX_RETRIES}"
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(wait_time)
                else:
                    raise

            except requests.exceptions.ConnectionError:
                wait_time = RETRY_BACKOFF_BASE**attempt
                logger.warning(
                    f"Connection error. Retrying in {wait_time}s. Attempt {attempt + 1}/{MAX_RETRIES}"
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(wait_time)
                else:
                    raise

        # If we get here, all retries failed
        raise requests.exceptions.RequestException(f"Max retries ({MAX_RETRIES}) exceeded")

    def _execute_request(self, method, url, headers=None, params=None, json_data=None):
        """Execute request with circuit breaker protection."""

        def make_request():
            return self._request_with_retry(
                method, url, headers=headers, params=params, json_data=json_data
            )

        try:
            return trakt_circuit_breaker.call(make_request)
        except CircuitBreakerOpen:
            logger.error("Trakt API circuit breaker is OPEN - service unavailable")
            # Return a mock response that indicates failure
            raise requests.exceptions.RequestException(
                "Trakt API unavailable (circuit breaker open)"
            )

    def _execute_with_token_refresh(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ):
        """Execute a Trakt request, refreshing the access token once on 401.

        Shared by the GET (``_request``) and POST (``_post_with_token_refresh``)
        paths so the 401 -> refresh -> retry-once logic lives in a single place.
        On refresh failure the request is aborted with a clear error prompting
        the user to re-authenticate (``--force-auth``).

        Args:
            method: HTTP method ("GET" or "POST")
            url: Full request URL
            headers: Request headers
            params: Query parameters (GET)
            json_data: JSON payload (POST)

        Returns:
            Response object from requests

        Raises:
            requests.exceptions.RequestException: If refresh fails or the
                circuit breaker is open
        """
        response = self._execute_request(
            method, url, headers=headers, params=params, json_data=json_data
        )

        if response.status_code == HTTP_UNAUTHORIZED:
            logger.warning("Received 401 - Token expired, attempting refresh...")
            if self.auth.refresh_access_token():
                logger.info("Token refreshed successfully, retrying request...")
                headers = self.auth.get_headers()
                response = self._execute_request(
                    method, url, headers=headers, params=params, json_data=json_data
                )
                logger.debug(f"Retry response status: {response.status_code}")
            else:
                logger.error("Failed to refresh token - authentication is invalid")
                print("Trakt token refresh failed. Run with --force-auth to re-authenticate.")
                raise requests.exceptions.RequestException(
                    "Trakt authentication expired. Run with --force-auth to re-authenticate."
                )

        return response

    def _request(self, endpoint, params=None):
        url = f"{TRAKT_API_URL}/{endpoint}"
        headers = self.auth.get_headers()

        logger.debug(f"Making request to: {url}")
        logger.debug(f"Params: {params}")

        cid = get_correlation_id()
        if cid:
            logger.debug(f"Request correlation_id: {cid}")

        start_time = time.time()
        try:
            response = self._execute_with_token_refresh("GET", url, headers=headers, params=params)

            elapsed = time.time() - start_time
            performance_monitor.record_api_call(endpoint, elapsed)
            logger.debug(f"Trakt API: {endpoint} -> {response.status_code} in {elapsed:.3f}s")
            logger.debug(
                f"Request completed: {endpoint}",
                extra={
                    "extra": {
                        "endpoint": endpoint,
                        "status_code": response.status_code,
                        "duration_ms": round(elapsed * 1000, 1),
                        "correlation_id": cid,
                    }
                },
            )

            response.raise_for_status()

            logger.debug(f"Response content length: {len(response.content)} bytes")
            return response

        except requests.exceptions.RequestException as e:
            elapsed = time.time() - start_time
            performance_monitor.record_api_call(endpoint, elapsed)
            logger.error(f"API request failed: {e}", exc_info=True)
            logger.debug(f"Request URL: {url}")
            logger.debug(f"Request params: {params}")
            raise

    def get_liked_lists(self) -> List[Dict[str, Any]]:
        logger.info("Fetching liked lists from Trakt...")

        try:
            response = self._request("users/likes/lists")
            data = response.json()

            logger.info(f"Successfully fetched {len(data)} liked list(s)")
            for i, liked in enumerate(data, 1):
                list_info = liked.get("list", {})
                name = list_info.get("name", "Unknown")
                username = list_info.get("user", {}).get("username", "Unknown")
                item_count = list_info.get("item_count", 0)
                logger.debug(f"List {i}: '{name}' by {username} ({item_count} items)")

            return data

        except requests.exceptions.RequestException as e:
            # Traceback already logged by _request(); log context only here.
            logger.error(f"Failed to fetch liked lists: API request failed - {e}")
            raise
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to fetch liked lists: Data parsing error - {e}", exc_info=True)
            raise

    def get_list_items(self, username: str, list_id: str) -> List[Dict[str, Any]]:
        logger.info(f"Fetching items from list: {list_id} (user: {username})")

        try:
            all_items = self._paginated_get(f"users/{username}/lists/{list_id}/items")

            logger.info(f"Retrieved {len(all_items)} item(s) from list")

            for i, item in enumerate(all_items[:5], 1):
                item_type = item.get("type", "unknown")
                if item_type == "movie":
                    media = item.get("movie", {})
                    logger.debug(
                        f"  Item {i}: Movie - {media.get('title', 'Unknown')} ({media.get('year', 'N/A')})"
                    )
                elif item_type == "show":
                    media = item.get("show", {})
                    logger.debug(
                        f"  Item {i}: Show - {media.get('title', 'Unknown')} ({media.get('year', 'N/A')})"
                    )

            if len(all_items) > 5:
                logger.debug(f"  ... and {len(all_items) - 5} more items")

            return all_items

        except Exception as e:
            logger.error(f"Failed to get list items: {e}", exc_info=True)
            raise

    def get_collection(self, media_type: str = "movies") -> List[Dict[str, Any]]:
        """Fetch user's collection from Trakt.

        Collection represents items the user has collected (owns) in their library.

        Args:
            media_type: 'movies' or 'shows'

        Returns:
            List of collection items with media details
        """
        if media_type not in ("movies", "shows"):
            raise ValueError("media_type must be 'movies' or 'shows'")

        logger.info(f"Fetching {media_type} collection from Trakt...")

        try:
            response = self._request(f"sync/collection/{media_type}")
            data = response.json()

            # Transform collection format to match list items format
            items = []
            for item in data:
                collected_at = item.get("collected_at")
                # Trakt collection responses use the singular media key
                # ("movie" / "show") regardless of the endpoint plural.
                media_data = item.get("movie" if media_type == "movies" else "show", {})

                if media_data:
                    formatted_item = {
                        "type": "movie" if media_type == "movies" else "show",
                        "collected_at": collected_at,
                    }
                    if media_type == "movies":
                        formatted_item["movie"] = media_data
                    else:
                        formatted_item["show"] = media_data
                    items.append(formatted_item)

            logger.info(f"Successfully fetched {len(items)} {media_type} from collection")
            return items

        except Exception as e:
            logger.error(f"Failed to fetch collection: {e}", exc_info=True)
            raise

    def get_watchlist(self, media_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch user's watchlist from Trakt.

        Watchlist represents items the user wants to watch.

        Args:
            media_type: Filter by 'movies', 'shows', 'episodes', or None for all

        Returns:
            List of watchlist items with media details
        """
        logger.info("Fetching watchlist from Trakt...")

        try:
            params = {"type": media_type} if media_type else None
            response = self._request("sync/watchlist", params=params)
            data = response.json()

            # Transform watchlist format to match list items format
            items = []
            for item in data:
                listed_at = item.get("listed_at")
                item_type = item.get("type")
                media_data = item.get(item_type, {}) if item_type else {}

                if media_data and item_type in ("movie", "show"):
                    formatted_item = {
                        "type": item_type,
                        "listed_at": listed_at,
                        item_type: media_data,
                    }
                    items.append(formatted_item)

            logger.info(f"Successfully fetched {len(items)} items from watchlist")
            return items

        except Exception as e:
            logger.error(f"Failed to fetch watchlist: {e}", exc_info=True)
            raise

    def get_watched_history(
        self, media_type=None, start_at=None, end_at=None, page=1, limit=DEFAULT_API_PAGE_SIZE
    ):
        """Fetch watched history from Trakt.

        Args:
            media_type: Filter by type ('movies', 'episodes', 'shows') or None for all
            start_at: ISO datetime string for filtering (inclusive)
            end_at: ISO datetime string for filtering (inclusive)
            page: Page number for pagination
            limit: Items per page (max 100)

        Returns:
            List of watched history items
        """
        logger.info(f"Fetching watched history (type={media_type}, page={page})...")

        params = {"page": page, "limit": limit}
        if start_at:
            params["start_at"] = start_at
        if end_at:
            params["end_at"] = end_at

        try:
            endpoint = f"sync/history/{media_type}" if media_type else "sync/history"
            response = self._request(endpoint, params=params)
            data = response.json()

            logger.info(f"Retrieved {len(data)} history item(s)")
            return data

        except Exception as e:
            logger.error(f"Failed to fetch watched history: {e}", exc_info=True)
            raise

    def _paginated_get(
        self,
        endpoint: str,
        params_base: Optional[Dict[str, Any]] = None,
        page_size: int = DEFAULT_API_PAGE_SIZE,
    ) -> List[Dict[str, Any]]:
        """Fetch all pages of a Trakt endpoint.

        Shared pagination helper: requests pages until the Trakt page-count
        header is reached, a short page is returned, or the safety limit
        ``MAX_HISTORY_PAGES`` is hit.

        Args:
            endpoint: Trakt API endpoint path
            params_base: Extra query params applied to every page
            page_size: Items per page (max 100)

        Returns:
            Concatenated list of items from all pages
        """
        all_items: List[Dict[str, Any]] = []
        page = 1
        params_base = params_base or {}

        while True:
            params = {**params_base, "page": page, "limit": page_size}
            response = self._request(endpoint, params=params)
            data = response.json()
            if not data:
                break

            all_items.extend(data)

            # Respect Trakt pagination headers if present; otherwise treat
            # a short page as the end of the list.
            try:
                page_count = int(response.headers.get("X-Pagination-Page-Count", page))
            except (TypeError, ValueError):
                page_count = page

            if page >= page_count or len(data) < page_size:
                break

            page += 1

            # Safety limit to prevent infinite loops
            if page > MAX_HISTORY_PAGES:
                logger.warning(f"Reached page limit ({MAX_HISTORY_PAGES}), stopping pagination")
                break

        return all_items

    def get_watched_movies(self) -> List[Dict[str, Any]]:
        """Get all watched movies from Trakt (paginated).

        Returns:
            List of watched movies with 'last_watched_at' timestamps
        """
        logger.info("Fetching watched movies from Trakt...")

        try:
            all_items = self._paginated_get("sync/watched/movies")
            logger.info(f"Retrieved {len(all_items)} watched movie(s)")
            return all_items

        except Exception as e:
            logger.error(f"Failed to fetch watched movies: {e}", exc_info=True)
            raise

    def get_watched_shows(self) -> List[Dict[str, Any]]:
        """Get all watched shows with episodes from Trakt.

        Returns:
            List of shows with seasons and episodes marked as watched
        """
        logger.info("Fetching watched shows from Trakt...")

        try:
            response = self._request("sync/watched/shows")
            data = response.json()

            # Count total episodes
            episode_count = 0
            for show in data:
                for season in show.get("seasons", []):
                    episode_count += len(season.get("episodes", []))

            logger.info(f"Retrieved {len(data)} watched show(s) with {episode_count} episode(s)")
            return data

        except Exception as e:
            logger.error(f"Failed to fetch watched shows: {e}", exc_info=True)
            raise

    def _post_with_token_refresh(self, url, payload, action_description="API request"):
        """Make a POST request with automatic token refresh on 401 and retry logic.

        Args:
            url: The URL to POST to
            payload: JSON payload to send
            action_description: Description of the action for logging

        Returns:
            Response object from requests.post

        Raises:
            requests.exceptions.RequestException: If the request fails
        """
        headers = self.auth.get_headers()
        endpoint = url.replace(f"{TRAKT_API_URL}/", "")
        start_time = time.time()

        response = self._execute_with_token_refresh("POST", url, headers=headers, json_data=payload)

        elapsed = time.time() - start_time
        performance_monitor.record_api_call(endpoint, elapsed)
        logger.debug(f"{action_description} response status: {response.status_code}")
        logger.debug(f"POST to {endpoint} completed in {elapsed:.3f}s")
        response.raise_for_status()
        return response

    def _batch_history_operation(
        self,
        movies,
        episodes,
        batch_size,
        operation_url,
        operation_name,
        success_key="added",
    ):
        """Generic batch operation for history add/remove.

        Args:
            movies: List of movie dicts
            episodes: List of episode dicts
            batch_size: Maximum items per batch
            operation_url: API endpoint URL
            operation_name: Human-readable operation name for logging
            success_key: Key to extract from response ("added" or "deleted")

        Returns:
            Combined result dict or None if no operations
        """
        total_movies = len(movies or [])
        total_episodes = len(episodes or [])
        logger.info(
            f"{operation_name}: {total_movies} movies, {total_episodes} episodes (batch size: {batch_size})"
        )

        all_responses = []
        failed_movie_payloads = []
        failed_episode_payloads = []

        # Process movies in batches
        if movies:
            for i in range(0, len(movies), batch_size):
                batch = movies[i : i + batch_size]
                logger.debug(
                    f"Processing movie batch {i // batch_size + 1}/{(len(movies) - 1) // batch_size + 1} ({len(batch)} items)"
                )

                payload = {"movies": batch}

                try:
                    response = self._post_with_token_refresh(operation_url, payload, operation_name)

                    data = response.json()
                    logger.info(
                        f"Successfully processed movie batch: {data.get(success_key, {}).get('movies', 0)} movies"
                    )
                    all_responses.append(data)

                except requests.exceptions.RequestException as e:
                    logger.error(f"Failed to process movie batch ({len(batch)} items): {e}")
                    failed_movie_payloads.extend(batch)
                    continue

        # Process episodes in batches
        if episodes:
            for i in range(0, len(episodes), batch_size):
                batch = episodes[i : i + batch_size]
                logger.debug(
                    f"Processing episode batch {i // batch_size + 1}/{(len(episodes) - 1) // batch_size + 1} ({len(batch)} items)"
                )

                payload = {"episodes": batch}

                try:
                    response = self._post_with_token_refresh(operation_url, payload, operation_name)

                    data = response.json()
                    logger.info(
                        f"Successfully processed episode batch: {data.get(success_key, {}).get('episodes', 0)} episodes"
                    )
                    all_responses.append(data)

                except requests.exceptions.RequestException as e:
                    logger.error(f"Failed to process episode batch ({len(batch)} items): {e}")
                    failed_episode_payloads.extend(batch)
                    continue

        if not all_responses:
            if not failed_movie_payloads and not failed_episode_payloads:
                logger.warning(f"No items were successfully processed for {operation_name}")
            else:
                logger.error(
                    f"All batches failed for {operation_name}: "
                    f"{len(failed_movie_payloads)} movies, {len(failed_episode_payloads)} episodes"
                )
            return None

        # Combine results
        combined_result = {
            success_key: {
                "movies": sum(r.get(success_key, {}).get("movies", 0) for r in all_responses),
                "episodes": sum(r.get(success_key, {}).get("episodes", 0) for r in all_responses),
            },
            "not_found": {
                "movies": sum(r.get("not_found", {}).get("movies", 0) for r in all_responses),
                "episodes": sum(r.get("not_found", {}).get("episodes", 0) for r in all_responses),
            },
            # Per-batch failure information (issue #9): callers must surface these,
            # otherwise partial failures are invisible and drift goes unreconciled.
            "failed": {
                "movies": len(failed_movie_payloads),
                "episodes": len(failed_episode_payloads),
            },
        }
        if failed_movie_payloads or failed_episode_payloads:
            combined_result["failed_payloads"] = {
                "movies": failed_movie_payloads,
                "episodes": failed_episode_payloads,
            }

        logger.info(
            f"Batch {operation_name} completed: {combined_result[success_key]['movies']} movies, "
            f"{combined_result[success_key]['episodes']} episodes processed"
        )
        return combined_result

    def add_to_history(self, movies=None, episodes=None, watched_at=None, batch_size=100):
        """Mark items as watched in Trakt history.

        Args:
            movies: List of movie dicts with 'title', 'year', 'ids'
            episodes: List of episode dicts with 'season', 'number', 'ids'
            watched_at: ISO datetime string for when items were watched
            batch_size: Maximum items per API call

        Returns:
            Combined API response dict or None
        """
        # Add watched_at to each item if provided
        if watched_at:
            if movies:
                movies = [{**m, "watched_at": watched_at} for m in movies]
            if episodes:
                episodes = [{**e, "watched_at": watched_at} for e in episodes]

        return self._batch_history_operation(
            movies=movies,
            episodes=episodes,
            batch_size=batch_size,
            operation_url=f"{TRAKT_API_URL}/sync/history",
            operation_name="Add to history",
            success_key="added",
        )

    def remove_from_history(self, movies=None, episodes=None, batch_size=100):
        """Remove items from Trakt history (mark as unwatched).

        Args:
            movies: List of movie dicts with 'ids'
            episodes: List of episode dicts with 'ids'
            batch_size: Maximum items per API call

        Returns:
            Combined API response dict or None
        """
        return self._batch_history_operation(
            movies=movies,
            episodes=episodes,
            batch_size=batch_size,
            operation_url=f"{TRAKT_API_URL}/sync/history/remove",
            operation_name="Remove from history",
            success_key="deleted",
        )

    def get_playback_progress(self, media_type=None):
        """Get playback progress from Trakt.

        Args:
            media_type: Filter by type ('movies', 'episodes') or None for all

        Returns:
            Dict mapping item keys to progress info
        """
        logger.info(f"Fetching playback progress from Trakt (type={media_type})...")

        try:
            endpoint = f"sync/playback/{media_type}" if media_type else "sync/playback"
            response = self._request(endpoint)
            data = response.json()

            progress = {}
            for item in data:
                progress_type = item.get("type")
                if progress_type == "movie":
                    movie = item.get("movie", {})
                    ids = movie.get("ids", {})
                    key = ("movie", ids.get("imdb"), normalize_tmdb_id(ids.get("tmdb")))
                    progress[key] = {
                        "progress_percent": item.get("progress", 0),
                        "paused_at": item.get("paused_at"),
                        "id": item.get("id"),  # Trakt playback ID for updates
                        "title": movie.get("title"),
                    }
                elif progress_type == "episode":
                    episode = item.get("episode", {})
                    show = item.get("show", {})
                    show_ids = show.get("ids", {})
                    key = (
                        "episode",
                        show_ids.get("imdb"),
                        episode.get("season"),
                        episode.get("number"),
                    )
                    progress[key] = {
                        "progress_percent": item.get("progress", 0),
                        "paused_at": item.get("paused_at"),
                        "id": item.get("id"),
                        "title": f"{show.get('title')} S{episode.get('season')}E{episode.get('number')}",
                    }

            logger.info(f"Retrieved {len(progress)} playback progress entries")
            return progress

        except Exception as e:
            logger.error(f"Failed to fetch playback progress: {e}", exc_info=True)
            return {}


class PlexClient:
    """Client for interacting with Plex."""

    def __init__(self, server, cache_manager):
        logger.info("Initializing Plex client")

        self.server = server
        self.cache = cache_manager
        logger.info(f"Server: {self.server.friendlyName} (v{self.server.version})")

        # Check if the current user is the server owner
        self._check_user_permissions()

    def _check_user_permissions(self):
        """Check if the current token belongs to the server owner or a managed user.

        Playlists created by the owner are visible to all users.
        Playlists created by managed users are private to them.
        """
        try:
            # Get the current user's info from the Plex server
            my_plex = self.server.myPlexAccount()
            if my_plex:
                # Get the server from the account perspective
                for resource in my_plex.resources():
                    if (
                        hasattr(resource, "clientIdentifier")
                        and resource.clientIdentifier == self.server.machineIdentifier
                    ):
                        # This is our server, check if we're the owner
                        is_owner = getattr(resource, "owned", False)
                        if is_owner:
                            logger.info(
                                "Connected as server OWNER - playlists will be visible to ALL users"
                            )
                        else:
                            user_name = getattr(my_plex, "username", "unknown")
                            logger.warning(
                                f"Connected as MANAGED USER '{user_name}' - playlists will be PRIVATE to you only"
                            )
                            logger.warning(
                                "To create playlists visible to ALL users, use the SERVER OWNER's token"
                            )
                        return

            # Fallback: try to determine by checking server preferences
            # Server owners have full access to settings, managed users don't
            try:
                # Try to access server settings (only owners can do this)
                _ = self.server.settings()
                logger.info(
                    "Connected with owner-level privileges - playlists will be visible to ALL users"
                )
            except Exception:
                logger.warning(
                    "Connected with limited privileges - playlists may be PRIVATE to current user only"
                )
                logger.warning(
                    "To create playlists visible to ALL users, use the SERVER OWNER's token"
                )

        except Exception as e:
            logger.debug(f"Could not determine user permissions: {e}")
            # Don't fail the sync, just continue without the warning

    def find_item_by_cache(
        self,
        imdb_id: Optional[str] = None,
        tmdb_id: Optional[Union[str, int]] = None,
        media_type: str = "movie",
    ) -> Optional[Any]:
        # Only log at debug level on cache miss to reduce log volume
        # Cache hits are common and logging every one creates excessive noise

        finders = {
            "movie": (self.cache.find_movie_by_imdb, self.cache.find_movie_by_tmdb),
            "show": (self.cache.find_show_by_imdb, self.cache.find_show_by_tmdb),
        }
        imdb_finder, tmdb_finder = finders[media_type]

        for source_name, external_id, finder in (
            ("IMDB", imdb_id, imdb_finder),
            ("TMDB", tmdb_id, tmdb_finder),
        ):
            if not external_id:
                continue

            result = finder(external_id)
            if result:
                logger.debug(
                    f"Cache hit: {media_type} by {source_name}={external_id}"
                    f" -> {result.get('title')}"
                )
                performance_monitor.record_cache_hit()
                return self._get_plex_item(result["ratingKey"])

        # Only log cache misses at debug level
        logger.debug(f"Cache miss: {media_type} IMDB={imdb_id} TMDB={tmdb_id}")
        performance_monitor.record_cache_miss()
        return None

    def _get_plex_item(self, rating_key):
        rating_key_str = str(rating_key)
        if rating_key_str in self.cache._plex_objects:
            return self.cache._plex_objects[rating_key_str]
        try:
            return self.server.fetchItem(rating_key)
        except Exception as e:
            logger.warning(
                f"Cached ratingKey {rating_key} could not be fetched (stale cache entry?): {e}"
            )
            return None

    @retry_with_backoff(
        max_retries=3,
        base_delay=2.0,
        retryable_exceptions=(BadRequest, ConnectionError, TimeoutError),
        retryable_status_codes=(500, 502, 503, 504),
        name="plex_create_playlist",
    )
    def _create_playlist(self, name, items):
        if items:
            logger.debug(f"Creating playlist with {len(items)} items...")
            playlist = self.server.createPlaylist(name, items=items)
            logger.info(f"Created playlist with {len(items)} items")
            return playlist

        logger.debug("Creating empty playlist...")
        playlist = self.server.createPlaylist(name)
        logger.info("Created empty playlist")
        return playlist

    def _update_playlist_description(self, playlist, description, action="set"):
        if not description:
            return

        try:
            playlist.edit(summary=description)
            # Log a single-line preview of the description (replace newlines with spaces)
            preview = description.replace("\n", " ").replace("\r", "")[:50]
            logger.info(f"{action.capitalize()} playlist description: {preview}...")
        except Exception as e:
            logger.warning(f"Could not {action} playlist description: {e}")

    @retry_with_backoff(
        max_retries=3,
        base_delay=2.0,
        retryable_exceptions=(BadRequest, ConnectionError, TimeoutError),
        retryable_status_codes=(500, 502, 503, 504),
        name="plex_update_playlist",
    )
    def create_or_update_playlist(
        self,
        name: str,
        items: List[Any],
        description: Optional[str] = None,
        batch_size: int = DEFAULT_PLAYLIST_BATCH_SIZE,
    ) -> Any:
        """Create or update a playlist with optimized batch processing.

        Args:
            name: Playlist name
            items: List of Plex items to add
            description: Playlist description
            batch_size: Number of items to add per batch (for large playlists)
        """
        logger.info(f"Creating/updating playlist: {name}")
        logger.debug(f"Items to add: {len(items)}")
        start_time = time.time()

        # Sort items to ensure movies come before episodes
        # This helps Plex correctly categorize the playlist type
        if items:
            # Sort with movies ('movie') first, then episodes ('episode')
            items = sorted(items, key=lambda x: 0 if getattr(x, "TYPE", "") == "movie" else 1)
            logger.debug(
                f"Sorted items by type: {[getattr(i, 'TYPE', 'unknown') for i in items[:5]]}..."
            )

        try:
            try:
                playlist = self.server.playlist(name)
                logger.info(f"Found existing playlist: {name}")

                current_items = playlist.items()
                current_count = len(current_items)
                logger.debug(f"Current playlist has {current_count} items")

                if current_count > MAX_PLAYLIST_SIZE_FOR_INCREMENTAL:
                    logger.info(
                        f"Playlist has {current_count} items - deleting and recreating for speed..."
                    )
                    playlist.delete()
                    logger.info("Deleted old playlist")

                    playlist = self._create_playlist(name, items)
                    self._update_playlist_description(playlist, description, action="set")
                else:
                    # Compute delta: only remove items that are no longer in the list
                    # and add items that are new. This preserves playlist metadata and
                    # is much faster than remove-all-add-all for small changes.
                    current_keys = {getattr(item, "ratingKey", None) for item in current_items}
                    new_keys = {getattr(item, "ratingKey", None) for item in items}
                    keys_to_remove = current_keys - new_keys
                    keys_to_add = new_keys - current_keys

                    if keys_to_remove and None not in keys_to_remove:
                        items_to_remove = [
                            item
                            for item in current_items
                            if getattr(item, "ratingKey", None) in keys_to_remove
                        ]
                        logger.debug(f"Removing {len(items_to_remove)} old items...")
                        playlist.removeItems(items_to_remove)
                        logger.info(f"Removed {len(items_to_remove)} old items")

                    if keys_to_add and None not in keys_to_add:
                        items_to_add = [
                            item
                            for item in items
                            if getattr(item, "ratingKey", None) in keys_to_add
                        ]
                        # Add items in batches for better performance with large playlists
                        if len(items_to_add) > batch_size:
                            logger.debug(
                                f"Adding {len(items_to_add)} items in batches of {batch_size}..."
                            )
                            for i in range(0, len(items_to_add), batch_size):
                                batch = items_to_add[i : i + batch_size]
                                playlist.addItems(batch)
                                logger.debug(f"  Added batch {i // batch_size + 1}")
                        else:
                            playlist.addItems(items_to_add)
                        logger.info(f"Added {len(items_to_add)} new items to playlist")

                    self._update_playlist_description(playlist, description, action="update")

            except NotFound:
                logger.info(f"Creating new playlist: {name}")
                playlist = self._create_playlist(name, items)
                self._update_playlist_description(playlist, description, action="set")

            return playlist

        except Exception as e:
            logger.error(f"Failed to create/update playlist '{name}': {e}", exc_info=True)
            raise
        finally:
            elapsed = time.time() - start_time
            performance_monitor.record_api_call("plex:create_or_update_playlist", elapsed)
            logger.debug(f"Playlist operation completed in {elapsed:.3f}s")

    def cleanup_orphaned_playlists(
        self,
        active_playlist_names: List[str],
        config: Dict[str, Any],
        dry_run: bool = False,
    ) -> List[str]:
        deleted = []
        to_delete = []

        managed_playlists = config.get("managed_playlists", [])
        blacklisted_lists = config.get("blacklisted_lists", [])
        if blacklisted_lists:
            logger.info(f"Also removing {len(blacklisted_lists)} blacklisted playlist(s)")
            for playlist_name in blacklisted_lists:
                if playlist_name in managed_playlists:
                    try:
                        playlist = self.server.playlist(playlist_name)
                        if dry_run:
                            to_delete.append(playlist_name)
                            logger.info(f"[DRY RUN] Would delete blacklisted: {playlist_name}")
                        else:
                            playlist.delete()
                            logger.info(f"Deleted blacklisted playlist: {playlist_name}")
                            deleted.append(playlist_name)
                    except NotFound:
                        logger.debug(f"Blacklisted playlist not found in Plex: {playlist_name}")
                    except Exception as e:
                        logger.error(
                            f"Failed to delete blacklisted playlist '{playlist_name}': {e}"
                        )

        try:
            all_playlists = self.server.playlists()
            logger.info(f"Found {len(all_playlists)} total playlists in Plex")

            for playlist in all_playlists:
                if (
                    playlist.title in managed_playlists
                    and playlist.title not in active_playlist_names
                ):
                    if dry_run:
                        to_delete.append(playlist.title)
                        logger.info(f"[DRY RUN] Would delete orphaned: {playlist.title}")
                    else:
                        try:
                            playlist.delete()
                            logger.info(f"Deleted orphaned playlist: {playlist.title}")
                            deleted.append(playlist.title)
                        except Exception as e:
                            logger.error(f"Failed to delete playlist '{playlist.title}': {e}")

            if dry_run and to_delete:
                logger.info(f"[DRY RUN] Would clean up {len(to_delete)} playlist(s)")
            elif deleted:
                logger.info(f"Cleaned up {len(deleted)} orphaned playlist(s)")
            else:
                logger.info("No orphaned playlists found")

        except Exception as e:
            logger.error(f"Error during playlist cleanup: {e}", exc_info=True)

        if not dry_run:
            config["managed_playlists"] = list(active_playlist_names)
            logger.info(
                f"About to save config with {len(config['managed_playlists'])} managed playlists"
            )
            save_config(config)

        return to_delete if dry_run else deleted

    def mark_as_watched(self, rating_key: Union[str, int]) -> bool:
        """Mark an item as watched in Plex.

        Args:
            rating_key: Plex rating key of the item

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Marking item as watched in Plex: {rating_key}")

        try:
            item = self.server.fetchItem(rating_key)

            if hasattr(item, "markWatched"):
                item.markWatched()
                logger.info(f"Successfully marked as watched: {item.title}")
                return True
            else:
                logger.warning(f"Item does not support markWatched: {item.title}")
                return False

        except NotFound:
            logger.error(f"Item not found in Plex: {rating_key}")
            return False
        except (ConnectionError, TimeoutError) as e:
            logger.error(f"Network error marking as watched {rating_key}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error marking as watched {rating_key}: {e}")
            return False

    def mark_as_unwatched(self, rating_key: Union[str, int]) -> bool:
        """Mark an item as unwatched in Plex.

        Args:
            rating_key: Plex rating key of the item

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Marking item as unwatched in Plex: {rating_key}")

        try:
            item = self.server.fetchItem(rating_key)

            if hasattr(item, "markUnwatched"):
                item.markUnwatched()
                logger.info(f"Successfully marked as unwatched: {item.title}")
                return True
            else:
                logger.warning(f"Item does not support markUnwatched: {item.title}")
                return False

        except NotFound:
            logger.error(f"Item not found in Plex: {rating_key}")
            return False
        except (ConnectionError, TimeoutError) as e:
            logger.error(f"Network error marking as unwatched {rating_key}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error marking as unwatched {rating_key}: {e}")
            return False

    @retry_with_backoff(
        max_retries=2,
        base_delay=1.0,
        retryable_exceptions=(BadRequest, ConnectionError, TimeoutError),
        retryable_status_codes=(500, 502, 503, 504),
        name="plex_batch_watched",
    )
    def batch_mark_as_watched(self, rating_keys: List[Union[str, int]]) -> Dict[str, Any]:
        """Mark multiple items as watched in Plex.

        Args:
            rating_keys: List of Plex rating keys

        Returns:
            Dict with success count and failures
        """
        logger.info(f"Batch marking {len(rating_keys)} items as watched in Plex...")

        results: Dict[str, Any] = {
            "success": 0,
            "failed": 0,
            "errors": [],
            "failed_rating_keys": [],
        }

        for rating_key in rating_keys:
            try:
                if self.mark_as_watched(rating_key):
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    results["failed_rating_keys"].append(rating_key)
                    results["errors"].append(f"Failed to mark {rating_key} as watched")
            except Exception as e:
                results["failed"] += 1
                results["failed_rating_keys"].append(rating_key)
                results["errors"].append(f"Error marking {rating_key} as watched: {e}")

        logger.info(
            f"Batch mark as watched completed: {results['success']} succeeded, {results['failed']} failed"
        )
        return results

    @retry_with_backoff(
        max_retries=2,
        base_delay=1.0,
        retryable_exceptions=(BadRequest, ConnectionError, TimeoutError),
        retryable_status_codes=(500, 502, 503, 504),
        name="plex_batch_unwatched",
    )
    def batch_mark_as_unwatched(self, rating_keys: List[Union[str, int]]) -> Dict[str, Any]:
        """Mark multiple items as unwatched in Plex.

        Args:
            rating_keys: List of Plex rating keys

        Returns:
            Dict with success count and failures
        """
        logger.info(f"Batch marking {len(rating_keys)} items as unwatched in Plex...")

        results: Dict[str, Any] = {
            "success": 0,
            "failed": 0,
            "errors": [],
            "failed_rating_keys": [],
        }

        for rating_key in rating_keys:
            try:
                if self.mark_as_unwatched(rating_key):
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    results["failed_rating_keys"].append(rating_key)
                    results["errors"].append(f"Failed to mark {rating_key} as unwatched")
            except Exception as e:
                results["failed"] += 1
                results["failed_rating_keys"].append(rating_key)
                results["errors"].append(f"Error marking {rating_key} as unwatched: {e}")

        logger.info(
            f"Batch mark as unwatched completed: {results['success']} succeeded, {results['failed']} failed"
        )
        return results

    def is_watched(self, rating_key: Union[str, int]) -> Tuple[bool, Optional[Any]]:
        """Check if an item is watched using cache (no API call).

        Args:
            rating_key: Plex rating key of the item

        Returns:
            Tuple of (is_watched_bool, last_viewed_at_timestamp or None)
        """
        # Normalize rating_key to int for consistent lookup
        try:
            rating_key_int = int(rating_key)
        except (ValueError, TypeError):
            rating_key_int = None

        # Try cache first using O(1) rating_key index (fast path - no API call)
        if rating_key_int is not None:
            cached = self.cache.memory_cache.get("by_rating_key", {}).get(rating_key_int)
            if cached:
                return (
                    cached.get("isWatched", False),
                    cached.get("lastViewedAt"),
                )

        # Fallback to API call if not in cache (episodes, or cache miss)
        logger.debug(f"Cache miss for watched check: {rating_key}, falling back to API")
        try:
            item = self.server.fetchItem(rating_key)
            is_watched = getattr(item, "isWatched", False)
            last_viewed = getattr(item, "lastViewedAt", None)
            return is_watched, last_viewed
        except NotFound:
            logger.warning(f"Item not found for watched check: {rating_key}")
            return False, None
        except Exception as e:
            logger.error(f"Error checking watched status: {e}")
            return False, None

    def get_playback_progress(self, rating_key):
        """Get playback progress for an item from Plex.

        Args:
            rating_key: Plex rating key of the item

        Returns:
            Tuple of (view_offset_ms, total_duration_ms) or (None, None) if not available
        """
        try:
            item = self.server.fetchItem(rating_key)
            view_offset = getattr(item, "viewOffset", None)
            duration = getattr(item, "duration", None)

            if view_offset is not None:
                logger.debug(f"Plex progress for {item.title}: {view_offset}ms / {duration}ms")
                return view_offset, duration

            return None, None

        except NotFound:
            logger.warning(f"Item not found for progress check: {rating_key}")
            return None, None
        except Exception as e:
            logger.error(f"Error checking playback progress: {e}")
            return None, None

    def set_playback_progress(self, rating_key, view_offset_ms):
        """Set playback progress for an item in Plex.

        Args:
            rating_key: Plex rating key of the item
            view_offset_ms: View offset in milliseconds

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Setting playback progress in Plex: {rating_key} -> {view_offset_ms}ms")

        try:
            item = self.server.fetchItem(rating_key)

            if hasattr(item, "updateProgress"):
                item.updateProgress(view_offset_ms)
                logger.info(f"Successfully set progress for: {item.title}")
                return True
            else:
                logger.warning(f"Item does not support updateProgress: {item.title}")
                return False

        except NotFound:
            logger.error(f"Item not found in Plex: {rating_key}")
            return False
        except Exception as e:
            logger.error(f"Failed to set playback progress: {e}")
            return False

    def batch_set_playback_progress(self, progress_updates):
        """Set playback progress for multiple items in Plex.

        Args:
            progress_updates: List of dicts with 'rating_key' and 'view_offset_ms'

        Returns:
            Dict with success count and failures
        """
        logger.info(f"Batch setting playback progress for {len(progress_updates)} items in Plex...")

        results = {"success": 0, "failed": 0, "errors": []}

        for update in progress_updates:
            try:
                if self.set_playback_progress(update["rating_key"], update["view_offset_ms"]):
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append(f"Failed to set progress for {update['rating_key']}")
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"Error setting progress for {update['rating_key']}: {e}")

        logger.info(
            f"Batch set progress completed: {results['success']} succeeded, {results['failed']} failed"
        )
        return results
