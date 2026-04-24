"""Sync orchestration and list processing helpers."""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests
from plexapi.exceptions import NotFound
from plexapi.server import PlexServer

from .clients import CacheManager, PlexClient, TraktAuth, TraktClient
from .config import get_plex_credentials, load_config
from .conflict_resolver import ConflictResolver
from .history_manager import WatchHistoryManager
from .log import logger
from .official_lists import OfficialListsService
from .resilience import backup_manager, integrity_checker
from .settings import (
    LOG_FILE,
    TRAKT_CLIENT_ID,
    TRAKT_CLIENT_SECRET,
    TRAKTOR_LIST_SOURCE,
    TRAKTOR_OFFICIAL_ENDPOINTS,
    TRAKTOR_OFFICIAL_LISTS_ENABLED,
    TRAKTOR_OFFICIAL_PERIOD,
    TRAKTOR_OFFICIAL_PERIODS,
    WATCH_SYNC_CONFLICT_RESOLUTION,
    WATCH_SYNC_DIRECTION,
)
from .watch_sync import WatchSyncEngine

# Constants for batch processing
DEFAULT_CHUNK_SIZE = 500  # Number of items to process per chunk for memory efficiency


def process_item_parallel(idx: int, item: Dict[str, Any], plex: Any) -> Dict[str, Any]:
    """Process a single item in parallel."""

    def build_result(success, title, year="", media_type="Unknown", imdb_id=None, plex_item=None):
        result = {"success": success, "title": title, "year": year, "idx": idx}
        if success:
            result["item"] = plex_item
        else:
            result["type"] = media_type
            result["imdb_id"] = imdb_id
        return result

    def find_media_item(media, media_type):
        # Get original imdb_id from media dict for preservation in results
        original_imdb_id = media.get("ids", {}).get("imdb", None)

        if original_imdb_id:
            plex_item = plex.find_item_by_cache(imdb_id=original_imdb_id, media_type=media_type)
            if plex_item:
                return plex_item, original_imdb_id

        tmdb_id = media.get("ids", {}).get("tmdb", None)
        if tmdb_id:
            plex_item = plex.find_item_by_cache(tmdb_id=tmdb_id, media_type=media_type)
            if plex_item:
                return plex_item, original_imdb_id

        return None, original_imdb_id

    try:
        media_type = item.get("type", None)

        if media_type == "movie":
            movie = item.get("movie", {})
            title = movie.get("title", "Unknown")
            year = movie.get("year", "")
            plex_item, imdb_id = find_media_item(movie, "movie")

            if plex_item:
                return build_result(True, title, year, plex_item=plex_item)

            return build_result(False, title, year, media_type="Movie", imdb_id=imdb_id)

        if media_type == "show":
            show = item.get("show", {})
            title = show.get("title", "Unknown")
            year = show.get("year", "")
            show_item, imdb_id = find_media_item(show, "show")

            if show_item:
                try:
                    seasons = show_item.seasons()
                    season1 = None
                    for season in seasons:
                        if season.seasonNumber == 1:
                            season1 = season
                            break

                    if season1:
                        episodes = season1.episodes()
                        if episodes:
                            first_episode = episodes[0]
                            logger.info(f"  Using S01E01 for '{title}': {first_episode.title}")
                            return build_result(
                                True,
                                f"{title} - S01E01",
                                year,
                                plex_item=first_episode,
                            )
                        logger.warning(f"  No episodes found in season 1 for '{title}' - skipping")
                    else:
                        logger.warning(f"  Season 1 not found for '{title}' - skipping")

                    return build_result(False, title, year, media_type="Show", imdb_id=imdb_id)

                except NotFound:
                    logger.warning(f"  S01E01 not found for '{title}' - skipping")
                    return build_result(False, title, year, media_type="Show", imdb_id=imdb_id)
                except (AttributeError, IndexError) as e:
                    logger.error(
                        f"  Error accessing season/episode data for '{title}': {e} - skipping"
                    )
                    return build_result(False, title, year, media_type="Show", imdb_id=imdb_id)

            return build_result(False, title, year, media_type="Show", imdb_id=imdb_id)

        return build_result(False, "Unknown")
    except (AttributeError, IndexError, KeyError, TypeError) as e:
        logger.error(f"Data error processing item {idx}: {e}")
        result = build_result(False, "Error")
        result["error"] = str(e)
        return result
    except Exception as e:
        logger.error(f"Error processing item {idx}: {e}")
        result = build_result(False, "Error")
        result["error"] = str(e)
        return result


def filter_description(description: Optional[str]) -> str:
    """Filter description to keep only useful text."""
    if not description:
        return description

    advertisement_keywords = [
        "powered by",
        "created by",
        "source:",
        "trakt.tv",
        "generated by",
        "maintained by",
        "create your own",
        "http://",
        "https://",
    ]
    result = []

    for line in description.split("\n"):
        if line.strip() == "":
            if result and result[-1].strip() != "":
                result.append(line)
            continue

        # Filter out traktor update timestamps and old "Updated at" timestamps
        if line.strip().startswith(("Updated by Traktor at", "Updated at")):
            continue

        if any(keyword in line.lower() for keyword in advertisement_keywords):
            continue

        result.append(line)

    return "\n".join(result)


def write_missing_report(
    missing_items: List[Dict[str, Any]], file_path: Optional[Union[str, Path]] = None
) -> None:
    """Write missing items for the current run to disk."""
    if file_path is None:
        file_path = LOG_FILE.parent / "missing.txt"
    path = Path(file_path)

    if not missing_items:
        if path.exists():
            path.unlink()
        logger.info("No missing items found for this run")
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write("List | Type | Title | Year | IMDb ID\n")
        f.write("-" * 100 + "\n")
        for item in missing_items:
            f.write(
                f"{item['list_name']} | {item['type']} | {item['title']} | {item['year']} | {item['imdb_id']}\n"
            )

    logger.info(f"Wrote {len(missing_items)} missing items to {file_path}")


class MissingItemTracker:
    """Tracks missing items during sync operations for reporting."""

    def __init__(self):
        self.missing_items = []
        self.not_found = []

    def build_item(self, list_name, media_type, title, year="", imdb_id=""):
        """Build a missing item dictionary."""
        return {
            "list_name": list_name,
            "type": media_type,
            "title": title,
            "year": year,
            "imdb_id": imdb_id,
        }

    def extract_details(self, item):
        """Extract missing item details from a Trakt list item."""
        media_type = item.get("type", None)
        media = item.get("movie", {}) if media_type == "movie" else item.get("show", {})
        label = "Movie" if media_type == "movie" else "Show" if media_type == "show" else "Unknown"
        return self.build_item(
            list_name="",
            media_type=label,
            title=media.get("title", "Unknown"),
            year=media.get("year", ""),
            imdb_id=media.get("ids", {}).get("imdb", ""),
        )

    def record_result(self, list_name, result, stats):
        """Record a missing item from a processing result."""
        self.not_found.append(f"{result['title']} ({result['year']})")
        stats["items_not_found"] += 1
        self.missing_items.append(
            self.build_item(
                list_name,
                result.get("type", "Unknown"),
                result.get("title", "Unknown"),
                result.get("year", ""),
                result.get("imdb_id", ""),
            )
        )

    def record_exception(self, list_name, item, error, stats):
        """Record a missing item from a worker thread exception."""
        logger.error(f"Worker thread exception: {error}")
        details = self.extract_details(item)
        details["list_name"] = list_name
        self.not_found.append(f"{details['title']} (error)")
        stats["items_not_found"] += 1
        self.missing_items.append(details)

    def get_items(self):
        """Return all tracked missing items."""
        return self.missing_items

    def get_not_found_list(self):
        """Return the not found summary list."""
        return self.not_found


# Module-level tracker instance for backward-compatible wrapper functions
_default_tracker = MissingItemTracker()


def _build_missing_item(list_name, media_type, title, year="", imdb_id=""):
    """Backward-compatible wrapper - build a missing item dictionary."""
    return _default_tracker.build_item(list_name, media_type, title, year, imdb_id)


def _extract_missing_item_details(item):
    """Backward-compatible wrapper - extract missing item details."""
    return _default_tracker.extract_details(item)


def _record_missing_result(list_name, result, not_found, stats, missing_items):
    """Backward-compatible wrapper - record missing result with external lists."""
    _default_tracker.not_found = not_found
    _default_tracker.missing_items = missing_items
    _default_tracker.record_result(list_name, result, stats)


def _record_worker_exception(list_name, item, error, not_found, stats, missing_items):
    """Backward-compatible wrapper - record exception with external lists."""
    _default_tracker.not_found = not_found
    _default_tracker.missing_items = missing_items
    _default_tracker.record_exception(list_name, item, error, stats)


def _build_playlist_description(description):
    filtered_description = filter_description(description)
    sync_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if filtered_description:
        return f"{filtered_description}\n\nUpdated by Traktor at {sync_timestamp}"
    return f"Updated by Traktor at {sync_timestamp}"


def _collect_plex_items(
    items, plex, workers, list_name, stats, missing_items, chunk_size=DEFAULT_CHUNK_SIZE
):
    """Collect Plex items from Trakt items using parallel processing with chunked execution.

    Processes items in chunks to limit memory usage for very large lists.
    """
    plex_items = []
    not_found = []

    # Process in chunks to limit memory usage for very large lists
    total_items = len(items)
    for chunk_start in range(0, total_items, chunk_size):
        chunk_end = min(chunk_start + chunk_size, total_items)
        chunk = items[chunk_start:chunk_end]
        chunk_offset = chunk_start

        logger.debug(
            f"Processing chunk {chunk_start // chunk_size + 1}/{(total_items - 1) // chunk_size + 1} "
            f"({len(chunk)} items, offset {chunk_start})"
        )

        chunk_plex_items = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_item = {
                executor.submit(process_item_parallel, chunk_offset + idx, item, plex): item
                for idx, item in enumerate(chunk)
            }

            for future in as_completed(future_to_item):
                try:
                    result = future.result()
                    if result["success"]:
                        chunk_plex_items.append((result["idx"], result["item"]))
                        stats["items_matched"] += 1
                    else:
                        _record_missing_result(list_name, result, not_found, stats, missing_items)
                except Exception as e:
                    _record_worker_exception(
                        list_name, future_to_item[future], e, not_found, stats, missing_items
                    )

        plex_items.extend(chunk_plex_items)

    plex_items.sort(key=lambda entry: entry[0])
    result_list = [item for _, item in plex_items]
    return result_list


def process_list_parallel(
    list_data: Dict[str, Any],
    plex: Any,
    trakt: Any,
    workers: int,
    stats: Dict[str, Any],
    updated_playlists: set,
    missing_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Process a single list in parallel."""
    list_info = list_data.get("list", {})
    list_name = list_info.get("name", "Unnamed List")
    username = list_info.get("user", {}).get("username", "")
    list_id = list_info.get("ids", {}).get("trakt", "")
    description = list_info.get("description", "")

    if not username or not list_id:
        logger.warning(f"Skipping list '{list_name}' - missing username or list_id")
        return {"list_name": list_name, "success": False, "error": "missing_info"}

    try:
        items = trakt.get_list_items(username, list_id)
        stats["items_total"] += len(items)

        # Build description early - needed for both empty and non-empty lists
        filtered_description = _build_playlist_description(description)

        if not items:
            # List is empty - clear the playlist to remove old items
            updated_playlists.add(list_name)
            plex.create_or_update_playlist(list_name, [], description=filtered_description)
            return {"list_name": list_name, "success": True, "matched": 0, "not_found": 0}

        plex_items = _collect_plex_items(items, plex, workers, list_name, stats, missing_items)

        # Always update the playlist, even if empty, to remove old items
        updated_playlists.add(list_name)
        plex.create_or_update_playlist(list_name, plex_items, description=filtered_description)

        # Calculate items not found for this list
        items_not_found = stats.get("items_not_found", 0)

        if plex_items:
            stats["playlists_updated"] += 1
            return {
                "list_name": list_name,
                "success": True,
                "matched": len(plex_items),
                "not_found": items_not_found,
            }

        return {
            "list_name": list_name,
            "success": True,
            "matched": 0,
            "not_found": items_not_found,
            "warning": "no_matches",
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed for list '{list_name}': {e}")
        stats["lists_failed"] += 1
        return {"list_name": list_name, "success": False, "error": f"API error: {e}"}
    except (ValueError, KeyError, TypeError) as e:
        logger.error(f"Data processing failed for list '{list_name}': {e}")
        stats["lists_failed"] += 1
        return {"list_name": list_name, "success": False, "error": f"Data error: {e}"}


def process_official_list_parallel(
    playlist_data: Dict[str, Any],
    plex: Any,
    workers: int,
    stats: Dict[str, Any],
    updated_playlists: set,
    missing_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Process an official list playlist in parallel."""
    list_name = playlist_data.get("name", "Unnamed Official List")
    items = playlist_data.get("items", [])
    description = playlist_data.get("description", "")

    logger.info(f"Processing official list: '{list_name}' ({len(items)} items)")

    # Build description with "Updated at" timestamp like liked lists
    filtered_description = _build_playlist_description(description)

    if not items:
        updated_playlists.add(list_name)
        plex.create_or_update_playlist(list_name, [], description=filtered_description)
        return {"list_name": list_name, "success": True, "matched": 0, "not_found": 0}

    stats["items_total"] += len(items)

    plex_items = _collect_plex_items(items, plex, workers, list_name, stats, missing_items)

    updated_playlists.add(list_name)
    plex.create_or_update_playlist(list_name, plex_items, description=filtered_description)

    # Calculate items not found for this list
    items_not_found = stats.get("items_not_found", 0)

    if plex_items:
        stats["playlists_updated"] += 1
        return {
            "list_name": list_name,
            "success": True,
            "matched": len(plex_items),
            "not_found": items_not_found,
        }

    return {
        "list_name": list_name,
        "success": True,
        "matched": 0,
        "not_found": items_not_found,
        "warning": "no_matches",
    }


def process_collection_sync(
    plex: Any,
    trakt: Any,
    workers: int,
    stats: Dict[str, Any],
    updated_playlists: set,
    missing_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Sync Trakt collection to Plex playlists (separate for movies and shows)."""
    results = []

    # Sync movie collection
    try:
        logger.info("Fetching movie collection from Trakt...")
        print("\nProcessing Trakt Movie Collection...")
        movie_items = trakt.get_collection("movies")

        if movie_items:
            list_name = "Trakt Collection - Movies"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            description = (
                f"Movies collected in your Trakt library\n\nUpdated by Traktor at {timestamp}"
            )

            items_not_found_before = stats.get("items_not_found", 0)
            plex_items = _collect_plex_items(
                movie_items, plex, workers, list_name, stats, missing_items
            )
            items_not_found = stats.get("items_not_found", 0) - items_not_found_before

            updated_playlists.add(list_name)
            plex.create_or_update_playlist(list_name, plex_items, description=description)

            if plex_items:
                stats["playlists_updated"] += 1
                print(f"   Playlist updated with {len(plex_items)} movie(s)")
            else:
                print("   No matching movies found in Plex")

            if items_not_found > 0:
                print(f"   {items_not_found} item(s) not found")

            results.append(
                {
                    "list_name": list_name,
                    "success": True,
                    "matched": len(plex_items),
                    "not_found": items_not_found,
                }
            )
        else:
            print("   No movies in collection")
            results.append(
                {"list_name": "Trakt Collection - Movies", "success": True, "matched": 0}
            )

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to sync movie collection: {e}")
        print(f"   Failed: {e}")
        results.append(
            {"list_name": "Trakt Collection - Movies", "success": False, "error": str(e)}
        )
    except (ValueError, KeyError, TypeError) as e:
        logger.error(f"Data error in movie collection sync: {e}")
        print(f"   Failed: {e}")
        results.append(
            {
                "list_name": "Trakt Collection - Movies",
                "success": False,
                "error": f"Data error: {e}",
            }
        )

    # Sync show collection
    try:
        logger.info("Fetching show collection from Trakt...")
        print("\nProcessing Trakt Show Collection...")
        show_items = trakt.get_collection("shows")

        if show_items:
            list_name = "Trakt Collection - Shows"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            description = (
                f"TV shows collected in your Trakt library\n\nUpdated by Traktor at {timestamp}"
            )

            stats["items_total"] += len(show_items)
            items_not_found_before = stats.get("items_not_found", 0)
            plex_items = _collect_plex_items(
                show_items, plex, workers, list_name, stats, missing_items
            )
            items_not_found = stats.get("items_not_found", 0) - items_not_found_before

            updated_playlists.add(list_name)
            plex.create_or_update_playlist(list_name, plex_items, description=description)

            if plex_items:
                stats["playlists_updated"] += 1
                print(f"   Playlist updated with {len(plex_items)} show(s)")
            else:
                print("   No matching shows found in Plex")

            if items_not_found > 0:
                print(f"   {items_not_found} item(s) not found")

            results.append(
                {
                    "list_name": list_name,
                    "success": True,
                    "matched": len(plex_items),
                    "not_found": items_not_found,
                }
            )
        else:
            print("   No shows in collection")
            results.append({"list_name": "Trakt Collection - Shows", "success": True, "matched": 0})

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to sync show collection: {e}")
        print(f"   Failed: {e}")
        results.append({"list_name": "Trakt Collection - Shows", "success": False, "error": str(e)})
    except (ValueError, KeyError, TypeError) as e:
        logger.error(f"Data error in show collection sync: {e}")
        print(f"   Failed: {e}")
        results.append(
            {"list_name": "Trakt Collection - Shows", "success": False, "error": f"Data error: {e}"}
        )

    return results


def process_watchlist_sync(
    plex: Any,
    trakt: Any,
    workers: int,
    stats: Dict[str, Any],
    updated_playlists: set,
    missing_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Sync Trakt watchlist to a single Plex playlist (movies + shows combined)."""
    results = []

    try:
        logger.info("Fetching watchlist from Trakt...")
        print("\nProcessing Trakt Watchlist...")
        watchlist_items = trakt.get_watchlist()

        if watchlist_items:
            list_name = "Trakt Watchlist"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            description = f"Items in your Trakt watchlist - movies and shows you want to watch\n\nUpdated by Traktor at {timestamp}"

            stats["items_total"] += len(watchlist_items)
            items_not_found_before = stats.get("items_not_found", 0)
            plex_items = _collect_plex_items(
                watchlist_items, plex, workers, list_name, stats, missing_items
            )
            items_not_found = stats.get("items_not_found", 0) - items_not_found_before

            updated_playlists.add(list_name)
            plex.create_or_update_playlist(list_name, plex_items, description=description)

            if plex_items:
                stats["playlists_updated"] += 1
                print(f"   Playlist updated with {len(plex_items)} item(s)")
            else:
                print("   No matching items found in Plex")

            if items_not_found > 0:
                print(f"   {items_not_found} item(s) not found")

            results.append(
                {
                    "list_name": list_name,
                    "success": True,
                    "matched": len(plex_items),
                    "not_found": items_not_found,
                }
            )
        else:
            print("   No items in watchlist")
            results.append({"list_name": "Trakt Watchlist", "success": True, "matched": 0})

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to sync watchlist: {e}")
        print(f"   Failed: {e}")
        results.append({"list_name": "Trakt Watchlist", "success": False, "error": str(e)})
    except (ValueError, KeyError, TypeError) as e:
        logger.error(f"Data error in watchlist sync: {e}")
        print(f"   Failed: {e}")
        results.append(
            {"list_name": "Trakt Watchlist", "success": False, "error": f"Data error: {e}"}
        )

    return results


def authenticate_trakt(auth, force=False):
    """Guide user through Trakt authentication."""
    logger.info("Checking Trakt authentication...")

    if auth.access_token and not force:
        logger.info("Trakt authentication found and valid")
        return True

    if force:
        logger.info("Forcing re-authentication as requested")
    else:
        logger.info("No valid Trakt authentication found")

    # Check if running in non-interactive mode (e.g., cron)
    if not sys.stdin.isatty():
        logger.error("Cannot authenticate in non-interactive mode")
        print("\nError: Trakt authentication required but running in non-interactive mode.")
        print("Please run interactively first to authenticate:")
        print("  uv run traktor")
        return False

    print("\nTrakt Authentication")
    print("===================")
    print("1. Visit this URL in your browser:")
    print(f"   {auth.get_auth_url()}")
    print("\n2. Log in to Trakt and authorize the application")
    print("3. Copy the authorization code and paste it below")
    print()

    auth_code = input("Authorization code: ").strip()
    logger.debug(f"Received auth code (length: {len(auth_code)})")

    try:
        auth.authenticate(auth_code)
        logger.info("Trakt authentication successful")
        print("Successfully authenticated with Trakt!")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Trakt authentication API request failed: {e}", exc_info=True)
        print(f"Authentication failed: Network or API error - {e}")
        return False
    except (KeyError, ValueError) as e:
        logger.error(f"Trakt authentication response error: {e}", exc_info=True)
        print(f"Authentication failed: Invalid response from Trakt - {e}")
        return False
    except EOFError:
        logger.error("Trakt authentication failed: EOF when reading input")
        print("Authentication failed: Input was interrupted. Please run interactively.")
        return False


def sync_lists(args: Optional[Any] = None) -> int:
    """Main sync function with parallel processing."""
    if not TRAKT_CLIENT_ID or not TRAKT_CLIENT_SECRET:
        logger.error("TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET must be set in environment/.env file")
        print("Error: Trakt API credentials not configured.")
        print("Please set TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET in your .env file.")
        sys.exit(1)

    # Mission-critical: Run integrity checks before sync
    logger.info("Running pre-sync integrity checks...")
    integrity_results = integrity_checker.run_all_checks()
    if not integrity_results["overall_healthy"]:
        logger.error("Pre-sync integrity check failed!")
        print("⚠️  Integrity check failed. Data may be corrupted.")
        print("Run 'traktor --integrity-check' for details.")
        print("Consider restoring from backup before proceeding.")
        response = input("Continue anyway? (y/N): ")
        if response.lower() != "y":
            logger.info("Sync aborted by user due to integrity check failure")
            sys.exit(1)
        logger.warning("User chose to continue despite integrity check failure")

    # Mission-critical: Create pre-sync backup if enabled
    backup_before_sync = os.getenv("TRAKTOR_BACKUP_BEFORE_SYNC", "false").lower() == "true"
    if backup_before_sync:
        try:
            logger.info("Creating pre-sync backup...")
            backup_path = backup_manager.create_backup(reason="pre_sync")
            logger.info(f"Pre-sync backup created: {backup_path}")
        except Exception as e:
            logger.error(f"Pre-sync backup failed: {e}")
            print("⚠️  Pre-sync backup failed!")
            response = input("Continue without backup? (y/N): ")
            if response.lower() != "y":
                logger.info("Sync aborted by user due to backup failure")
                sys.exit(1)
            logger.warning("User chose to continue without backup")

    logger.info("=" * 80)
    logger.info("Starting Traktor sync")
    logger.info("=" * 80)

    # Track overall sync timing
    sync_start_time = time.time()

    stats = {
        "lists_found": 0,
        "lists_processed": 0,
        "lists_failed": 0,
        "items_total": 0,
        "items_matched": 0,
        "items_not_found": 0,
        "playlists_updated": 0,
        "playlists_deleted": 0,
    }
    updated_playlists = set()
    missing_items = []
    config = load_config()

    missing_path = LOG_FILE.parent / "missing.txt"
    if missing_path.exists():
        missing_path.unlink()
        logger.info("Deleted existing missing.txt at start of sync")

    # Determine if OAuth authentication is required
    # Use CLI arg if provided, otherwise fall back to env var, then default to "official"
    if args and hasattr(args, 'list_source') and args.list_source:
        list_source = args.list_source
    else:
        list_source = TRAKTOR_LIST_SOURCE
    needs_auth = _needs_oauth_auth(args, list_source)

    logger.debug("Initializing TraktAuth...")
    auth = TraktAuth()

    if args and args.force_auth:
        logger.info("Force auth flag detected - requiring authentication")
        needs_auth = True

    # Authenticate only if needed
    if needs_auth:
        logger.info("Authentication required for liked lists/watch sync features")
        if not authenticate_trakt(auth, force=args.force_auth if args else False):
            logger.error("Trakt authentication failed, exiting")
            print("Failed to authenticate with Trakt. Exiting.")
            sys.exit(1)
        logger.info("Trakt authentication successful")
    else:
        logger.info("No authentication required - syncing official lists only (no OAuth needed)")

    logger.info("Getting Plex credentials...")
    try:
        plex_url, plex_token = get_plex_credentials(args)
    except ValueError as e:
        logger.error(f"Plex credentials error: {e}")
        print(f"Error: {e}")
        sys.exit(1)

    # Initialize Trakt client only if authenticated
    trakt = None
    if needs_auth:
        logger.info("Initializing Trakt client...")
        trakt = TraktClient(auth)

    logger.info("Initializing Plex client...")
    try:
        plex_server = PlexServer(plex_url, plex_token)
        logger.info("Connected to Plex server")

        cache_manager = CacheManager(plex_server)
        cache_manager.load_cache(force_refresh=args.refresh_cache if args else False)

        plex = PlexClient(plex_server, cache_manager)
        logger.info("Plex client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to connect to Plex: {e}", exc_info=True)
        print(f"Failed to connect to Plex: {e}")
        sys.exit(1)

    # Fetch liked lists only if authenticated
    liked_lists = []
    if needs_auth and trakt:
        logger.info("Fetching liked lists from Trakt...")
        try:
            liked_lists = trakt.get_liked_lists()
            stats["lists_found"] = len(liked_lists)
            logger.info(f"Found {len(liked_lists)} liked list(s)")
            print(f"Found {len(liked_lists)} liked list(s)")
        except Exception as e:
            logger.error(f"Failed to fetch liked lists: {e}", exc_info=True)
            print(f"Failed to fetch liked lists: {e}")
            # Don't exit - we can still sync official lists
            print("Continuing with official lists only...")

    if not liked_lists and list_source in ("liked", "both"):
        logger.warning("No liked lists found on Trakt")
        print("No liked lists found on Trakt.")

        # Only exit early if we're not syncing official lists
        sync_official = (
            _should_sync_official_lists(args) if args else TRAKTOR_OFFICIAL_LISTS_ENABLED
        )
        if not sync_official:
            return

    # Process liked lists (only if authenticated and list_source permits)
    if trakt and liked_lists and (not args or args.list_source in ("liked", "both")):
        # Skip liked lists when in watch-only mode
        if args and args.sync_watched_only:
            logger.info("Skipping liked lists (--sync-watched-only mode)")
        else:
            blacklisted_lists = config.get("blacklisted_lists", [])
            if blacklisted_lists:
                logger.info(f"Filtering out {len(blacklisted_lists)} blacklisted list(s)")
                for liked in liked_lists:
                    list_name = liked.get("list", {}).get("name", "")
                    if list_name in blacklisted_lists:
                        logger.info(f"Skipping blacklisted list: '{list_name}'")
                        print(f"   Skipping blacklisted list: {list_name}")
                liked_lists = [
                    liked
                    for liked in liked_lists
                    if liked.get("list", {}).get("name", "") not in blacklisted_lists
                ]
                stats["lists_found"] = len(liked_lists)
                logger.info(f"After filtering: {len(liked_lists)} list(s) to process")

            for liked in liked_lists:
                list_info = liked.get("list", {})
                list_name = list_info.get("name", "Unnamed List")
                username = list_info.get("user", {}).get("username", "")
                list_id = list_info.get("ids", {}).get("trakt", "")

                logger.info(f"Processing list: '{list_name}' (ID: {list_id}, user: {username})")
                print(f"\nProcessing list: {list_name}")
                print(f"   From user: {username}")

                result = process_list_parallel(
                    liked,
                    plex,
                    trakt,
                    args.workers if args else 8,
                    stats,
                    updated_playlists,
                    missing_items,
                )
                stats["lists_processed"] += 1

                if result.get("success", False):
                    if result.get("warning", "") == "no_matches":
                        print("   No matching items found in Plex library")
                    else:
                        print(f"   Playlist updated with {result.get('matched', 0)} item(s)")
                    if result.get("not_found", 0) > 0:
                        print(f"   {result.get('not_found', 0)} item(s) not found")
                else:
                    print(f"   Failed to process list: {result.get('error', 'Unknown error')}")

    # Official lists sync (always enabled by default - no auth required)
    if args and _should_sync_official_lists(args):
        print("\n" + "=" * 60)
        print("Official Lists Sync")
        print("=" * 60)

        try:
            official_service = OfficialListsService()

            # Determine which endpoints to fetch
            endpoints = _get_official_endpoints(args)
            periods = _get_official_periods(args)
            separate_playlists = args.official_playlist_mode == "separate"

            logger.info(f"Fetching official lists: {endpoints} (periods={periods})")
            print(f"Fetching {len(endpoints)} official endpoint(s) for {len(periods)} period(s)...")

            all_official_playlists = []

            # Fetch for each period
            for period in periods:
                # Get playlists from official service for this period
                period_playlists = official_service.get_playlists_from_endpoints(
                    endpoints, period=period, separate_playlists=separate_playlists
                )
                all_official_playlists.extend(period_playlists)

            if all_official_playlists:
                print(f"\nProcessing {len(all_official_playlists)} official playlist(s)...")
                for playlist in all_official_playlists:
                    result = process_official_list_parallel(
                        playlist,
                        plex,
                        args.workers if args else 8,
                        stats,
                        updated_playlists,
                        missing_items,
                    )

                    if result.get("success", False):
                        if result.get("warning", "") == "no_matches":
                            print(f"   {result['list_name']}: No matching items")
                        else:
                            print(f"   {result['list_name']}: {result.get('matched', 0)} items")
                    else:
                        print(
                            f"   {result['list_name']}: Failed - {result.get('error', 'Unknown')}"
                        )
            else:
                print("   No official playlists to process")

        except Exception as e:
            logger.error(f"Official lists sync failed: {e}", exc_info=True)
            print(f"\nOfficial lists sync failed: {e}")

    write_missing_report(missing_items)

    # Skip orphaned playlist cleanup when in watch-only mode (no playlists created)
    if args and args.sync_watched_only:
        logger.info("Skipping orphaned playlist cleanup (--sync-watched-only mode)")
    else:
        print("\nChecking for orphaned playlists...")
        orphaned = plex.cleanup_orphaned_playlists(updated_playlists, config)
        stats["playlists_deleted"] = len(orphaned)

        if orphaned:
            print(f"   Deleted {len(orphaned)} orphaned playlist(s):")
            for name in orphaned:
                print(f"     - {name}")
        else:
            print("   No orphaned playlists found")

    # Watch sync (if enabled and authenticated, or in watch-only mode)
    if args and (args.sync_watched or args.sync_watched_only):
        if trakt is None:
            print("\n⚠️  Watch sync skipped: Trakt authentication required")
            logger.warning("Watch sync requested but not authenticated")
        else:
            print("\n" + "=" * 60)
            print("Watch Status Sync")
            print("=" * 60)

            try:
                # Initialize watch sync components
                plex_server_id = getattr(plex_server, "machineIdentifier", None)
                history_manager = WatchHistoryManager(plex_server_id)

                # Map strategy from CLI or env setting to conflict resolver format
                strategy_map = {
                    "newest": "newest_wins",
                    "newest_wins": "newest_wins",
                    "plex": "plex_wins",
                    "plex_wins": "plex_wins",
                    "trakt": "trakt_wins",
                    "trakt_wins": "trakt_wins",
                }
                # Use CLI arg if provided, otherwise fall back to env setting
                conflict_strategy = strategy_map.get(
                    args.watch_conflict, WATCH_SYNC_CONFLICT_RESOLUTION
                )
                conflict_resolver = ConflictResolver(strategy=conflict_strategy)

                # Create sync engine
                sync_engine = WatchSyncEngine(plex, trakt, history_manager, conflict_resolver)

                # Determine direction: CLI arg takes precedence, then env setting, then default
                direction = args.watch_direction
                if direction == "both" and WATCH_SYNC_DIRECTION != "both":
                    direction = WATCH_SYNC_DIRECTION

                # Run sync
                watch_stats = sync_engine.sync_watched_status(
                    direction=direction,
                    dry_run=args.dry_run,
                    movies_only=args.sync_movies_only,
                    shows_only=args.sync_shows_only,
                    backfill_history=args.backfill_history,
                )

                # Print watch sync summary
                if args.dry_run:
                    print("\n[DRY RUN] Changes that would be made:")
                else:
                    print("\nWatch sync complete!")

                print(
                    f"  Plex: {watch_stats['plex_watched']} marked watched, {watch_stats['plex_unwatched']} marked unwatched"
                )
                print(
                    f"  Trakt: {watch_stats['trakt_watched']} marked watched, {watch_stats['trakt_unwatched']} marked unwatched"
                )

                if watch_stats["errors"] > 0:
                    print(f"  Errors: {watch_stats['errors']}")

            except Exception as e:
                logger.error(f"Watch sync failed: {e}", exc_info=True)
                print(f"\nWatch sync failed: {e}")

    # Progress sync (if enabled and authenticated)
    if args and args.sync_progress:
        if trakt is None:
            print("\n⚠️  Progress sync skipped: Trakt authentication required")
            logger.warning("Progress sync requested but not authenticated")
        else:
            print("\n" + "=" * 60)
            print("Playback Progress Sync")
            print("=" * 60)

            try:
                # Reuse the sync engine if it was created for watch sync
                # Otherwise create a new one
                if not (args and args.sync_watched):
                    plex_server_id = getattr(plex_server, "machineIdentifier", None)
                    history_manager = WatchHistoryManager(plex_server_id)
                    conflict_resolver = ConflictResolver(strategy="newest_wins")
                    sync_engine = WatchSyncEngine(plex, trakt, history_manager, conflict_resolver)

                # Run progress sync (Trakt -> Plex only)
                progress_stats = sync_engine.sync_playback_progress(
                    dry_run=args.dry_run,
                    movies_only=args.sync_movies_only,
                    shows_only=args.sync_shows_only,
                )

                if args.dry_run:
                    print("\n[DRY RUN] Progress changes that would be made:")
                else:
                    print("\nPlayback progress sync complete!")

                print(f"  Plex progress updated: {progress_stats['plex_progress_updated']}")
                print(f"  Skipped (no progress): {progress_stats['skipped_no_progress']}")
                print(f"  Skipped (already in sync): {progress_stats['skipped_already_in_sync']}")

                if progress_stats["errors"] > 0:
                    print(f"  Errors: {progress_stats['errors']}")

            except Exception as e:
                logger.error(f"Progress sync failed: {e}", exc_info=True)
                print(f"\nProgress sync failed: {e}")

    # Collection sync (if enabled and authenticated, and not in watch-only mode)
    if args and args.sync_collection and not args.sync_watched_only:
        if trakt is None:
            print("\n⚠️  Collection sync skipped: Trakt authentication required")
            logger.warning("Collection sync requested but not authenticated")
        else:
            print("\n" + "=" * 60)
            print("Trakt Collection Sync")
            print("=" * 60)

            collection_results = process_collection_sync(
                plex,
                trakt,
                args.workers if args else 8,
                stats,
                updated_playlists,
                missing_items,
            )

            for result in collection_results:
                if not result.get("success", False):
                    stats["lists_failed"] += 1

    # Watchlist sync (if enabled and authenticated, and not in watch-only mode)
    if args and args.sync_watchlist and not args.sync_watched_only:
        if trakt is None:
            print("\n⚠️  Watchlist sync skipped: Trakt authentication required")
            logger.warning("Watchlist sync requested but not authenticated")
        else:
            print("\n" + "=" * 60)
            print("Trakt Watchlist Sync")
            print("=" * 60)

            watchlist_results = process_watchlist_sync(
                plex,
                trakt,
                args.workers if args else 8,
                stats,
                updated_playlists,
                missing_items,
            )

            for result in watchlist_results:
                if not result.get("success", False):
                    stats["lists_failed"] += 1

    # Calculate total elapsed time for the sync operation
    elapsed = time.time() - sync_start_time
    _print_summary(stats, elapsed)


def _should_sync_official_lists(args):
    """Check if official lists should be synced based on args and settings."""
    # If watch-only mode, always skip lists
    if args and args.sync_watched_only:
        return False

    # If args is None, rely on environment variable setting only
    if args is None:
        return TRAKTOR_OFFICIAL_LISTS_ENABLED

    # If --no-official-lists is set, always skip
    if args.no_official_lists:
        return False

    # If --official-lists is set, always sync
    if args.official_lists:
        return True

    # If --list-source is 'official' or 'both', sync
    if args.list_source in ("official", "both"):
        return True

    # Check environment variable
    return TRAKTOR_OFFICIAL_LISTS_ENABLED


def _needs_oauth_auth(args, list_source):
    """Determine if OAuth authentication is required.

    OAuth is only needed for features that require user-specific Trakt data:
    - Liked lists (list_source is 'liked' or 'both')
    - Collection sync (--sync-collection)
    - Watchlist sync (--sync-watchlist)
    - Watch status sync (--sync-watched, --sync-watched-only)
    - Progress sync (--sync-progress)

    Official lists (trending, popular, etc.) do NOT require OAuth - only Client ID.

    Args:
        args: Parsed CLI arguments (may be None)
        list_source: The list source setting ('liked', 'official', or 'both')

    Returns:
        True if OAuth authentication is required, False otherwise
    """
    if args is None:
        # Default args case - check if liked lists would be synced
        return list_source in ("liked", "both")

    # Check watch-only mode first (takes precedence)
    if args.sync_watched_only:
        return True

    # Check list source
    if args.list_source in ("liked", "both"):
        return True

    # Check watch-related features
    if args.sync_watched:
        return True
    if args.sync_progress:
        return True

    # Check collection/watchlist features
    if args.sync_collection:
        return True
    if args.sync_watchlist:
        return True

    return False


def _get_official_endpoints(args):
    """Get the list of official endpoints to sync."""
    # CLI argument takes precedence
    if args.official_endpoints:
        from .official_lists import OfficialListsService

        return OfficialListsService.parse_endpoint_list(args.official_endpoints)

    # Check environment variable
    if TRAKTOR_OFFICIAL_ENDPOINTS:
        from .official_lists import OfficialListsService

        return OfficialListsService.parse_endpoint_list(TRAKTOR_OFFICIAL_ENDPOINTS)

    # Use defaults
    from .official_lists import OfficialListsService

    return OfficialListsService.get_default_endpoints()


def _get_official_period(args):
    """Get the period for stats endpoints."""
    # CLI argument takes precedence
    if args.official_period:
        return args.official_period

    # Check environment variable
    if TRAKTOR_OFFICIAL_PERIOD:
        return TRAKTOR_OFFICIAL_PERIOD

    return "weekly"


def _get_official_periods(args):
    """Get the list of periods for stats endpoints.

    Supports multiple periods via --official-periods or TRAKTOR_OFFICIAL_PERIODS.
    If not specified, falls back to single period from _get_official_period.

    Returns:
        List of period strings (e.g., ["weekly"], ["weekly", "monthly"])
    """

    def parse_periods(period_string):
        """Parse comma-separated period string into valid periods list."""
        if not period_string:
            return []
        periods = [p.strip() for p in period_string.split(",")]
        valid = [p for p in periods if p in ("daily", "weekly", "monthly", "yearly")]
        return valid

    # Check CLI argument first
    if hasattr(args, "official_periods") and args.official_periods:
        periods = parse_periods(args.official_periods)
        if periods:
            return periods

    # Check environment variable second
    periods = parse_periods(TRAKTOR_OFFICIAL_PERIODS)
    if periods:
        return periods

    # Fall back to single period from CLI/env/default
    return [_get_official_period(args)]


def _print_summary(stats, elapsed):
    """Log and print the sync summary."""
    logger.info("=" * 80)
    logger.info("SYNC SUMMARY")
    logger.info("=" * 80)
    lines = [
        f"Total time: {elapsed:.2f} seconds",
        f"Lists found: {stats['lists_found']}",
        f"Lists processed: {stats['lists_processed']}",
        f"Lists failed: {stats['lists_failed']}",
        f"Total items: {stats['items_total']}",
        f"Items matched: {stats['items_matched']}",
        f"Items not found: {stats['items_not_found']}",
        f"Playlists updated: {stats['playlists_updated']}",
        f"Playlists deleted: {stats['playlists_deleted']}",
    ]
    for line in lines:
        logger.info(line)
    logger.info("=" * 80)

    print("\n" + "=" * 60)
    print("Sync complete!")
    print(f"Total time: {elapsed:.2f} seconds")
    print("=" * 60)
    print("\nSummary:")
    for line in lines[1:]:
        print(f"  {line}")
