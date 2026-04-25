"""Watch status synchronization engine."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import requests
from plexapi.exceptions import NotFound

from .log import logger
from .progress import SyncProgress
from .utils import normalize_tmdb_id

# Constants for progress sync
DEFAULT_PROGRESS_THRESHOLD_MS = (
    30000  # 30 seconds - only sync if progress differs by more than this
)

# Default time window for first sync (days) - limits initial sync to recent items
DEFAULT_FIRST_SYNC_WINDOW_DAYS = 7


class WatchSyncEngine:
    """Engine for bidirectional watch status synchronization.

    Handles pulling watch state from Plex and Trakt, resolving conflicts,
    and pushing changes to both platforms.
    """

    @staticmethod
    def _parse_plex_timestamp(timestamp):
        """Parse a Plex timestamp to datetime.

        Plex returns timestamps as Unix epoch integers, but sometimes
        they may be ISO format strings. This method handles both.

        Args:
            timestamp: Unix timestamp (int/float) or ISO string

        Returns:
            datetime or None if parsing fails
        """
        if timestamp is None:
            return None

        try:
            # Handle Unix timestamp (int or float)
            if isinstance(timestamp, (int, float)):
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)

            # Handle string timestamps (ISO format)
            if isinstance(timestamp, str):
                # Try ISO format first
                try:
                    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except ValueError:
                    # Try parsing as Unix timestamp string
                    try:
                        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
                    except ValueError:
                        return None

            return None
        except (ValueError, TypeError, OverflowError):
            return None

    def __init__(
        self,
        plex_client: Any,
        trakt_client: Any,
        history_manager: Any,
        conflict_resolver: Any,
    ) -> None:
        """Initialize the watch sync engine.

        Args:
            plex_client: PlexClient instance
            trakt_client: TraktClient instance
            history_manager: WatchHistoryManager instance
            conflict_resolver: ConflictResolver instance
        """
        self.plex = plex_client
        self.trakt = trakt_client
        self.history = history_manager
        self.resolver = conflict_resolver
        self.dry_run = False
        self.stats = {
            "plex_watched": 0,
            "plex_unwatched": 0,
            "trakt_watched": 0,
            "trakt_unwatched": 0,
            "conflicts_resolved": 0,
            "errors": 0,
        }

    def sync_watched_status(
        self,
        direction: str = "both",
        dry_run: bool = False,
        movies_only: bool = False,
        shows_only: bool = False,
        backfill_history: bool = False,
    ) -> Dict[str, Any]:
        """Main entry point for watch status synchronization.

        Args:
            direction: Sync direction - 'plex-to-trakt', 'trakt-to-plex', or 'both'
            dry_run: If True, only simulate changes without applying them
            movies_only: If True, only sync movies
            shows_only: If True, only sync shows/episodes
            backfill_history: If True, perform initial backfill of watch history

        Returns:
            Dict with sync statistics
        """
        self.dry_run = dry_run
        self.stats = {
            "plex_watched": 0,
            "plex_unwatched": 0,
            "trakt_watched": 0,
            "trakt_unwatched": 0,
            "conflicts_resolved": 0,
            "errors": 0,
            "processed_movies": 0,
            "processed_episodes": 0,
            "delta_mode": False,
            "items_skipped_due_to_delta": 0,
            "plex_progress_updated": 0,
            "trakt_progress_updated": 0,
            "progress_conflicts": 0,
        }

        # Validate filter options
        if movies_only and shows_only:
            logger.warning(
                "Both --sync-movies-only and --sync-shows-only specified, defaulting to movies only"
            )
            shows_only = False

        content_type = "movies" if movies_only else "shows" if shows_only else "all"
        logger.info("=" * 80)
        logger.info(
            f"Starting watch sync (direction={direction}, dry_run={dry_run}, content={content_type}, backfill={backfill_history})"
        )
        logger.info("=" * 80)

        # Initialize progress tracker
        progress = SyncProgress()

        try:
            # Get last sync timestamp for delta-based sync
            last_sync = self.history.get_last_sync_timestamp()
            if last_sync and not backfill_history:
                logger.info(f"Delta sync mode: Only processing items changed since {last_sync}")
                self.stats["delta_mode"] = True
            elif backfill_history:
                logger.info("Backfill mode: Processing all items (full sync)")
                last_sync = None
            else:
                # First run - use default time window for faster initial sync
                last_sync = datetime.now(timezone.utc) - timedelta(days=DEFAULT_FIRST_SYNC_WINDOW_DAYS)
                logger.info(f"First sync: Processing items from last {DEFAULT_FIRST_SYNC_WINDOW_DAYS} days only (for speed)")
                logger.info("Use --backfill-history for full history sync if needed")

            # Stage 1: Pull from Plex
            progress.start_stage(
                name="pull_plex",
                total=0,  # We don't know total items until we process them
                desc="Pulling watch state from Plex",
                unit="items",
            )
            plex_state = self._pull_from_plex(
                movies_only=movies_only, shows_only=shows_only, since=last_sync
            )
            progress.complete_stage()

            # Stage 2: Pull from Trakt
            progress.start_stage(
                name="pull_trakt",
                total=0,  # We don't know total items until we process them
                desc="Pulling watch state from Trakt",
                unit="items",
            )
            trakt_state = self._pull_from_trakt(
                movies_only=movies_only, shows_only=shows_only, since=last_sync
            )
            progress.complete_stage()

            # Stage 3: Calculate changes
            progress.start_stage(
                name="calculate_changes",
                total=len(set(plex_state.keys()) | set(trakt_state.keys())),
                desc="Calculating sync changes",
                unit="items",
            )
            changes = self._calculate_changes(plex_state, trakt_state, direction)
            progress.complete_stage()

            # Stage 4: Apply changes
            if not dry_run:
                total_changes = (
                    len(changes["plex"]["mark_watched"])
                    + len(changes["plex"]["mark_unwatched"])
                    + len(changes["trakt"]["mark_watched"])
                    + len(changes["trakt"]["mark_unwatched"])
                )
                progress.start_stage(
                    name="apply_changes",
                    total=total_changes,
                    desc="Applying sync changes",
                    unit="changes",
                )
                self._apply_changes(changes)
                progress.complete_stage()
            else:
                progress.start_stage(
                    name="dry_run",
                    total=(
                        len(changes["plex"]["mark_watched"])
                        + len(changes["plex"]["mark_unwatched"])
                        + len(changes["trakt"]["mark_watched"])
                        + len(changes["trakt"]["mark_unwatched"])
                    ),
                    desc="Dry run - simulating changes",
                    unit="changes",
                )
                self._log_dry_run_changes(changes)
                progress.complete_stage()

            # Update sync timestamp
            if not dry_run:
                self.history.update_last_sync_timestamp()

            # Log final summary
            summary = progress.get_summary()
            logger.info("=" * 80)
            logger.info("Watch sync completed")
            logger.info("=" * 80)
            logger.info(f"Total time: {timedelta(seconds=int(summary['total_time']))}")
            for stage_name, stage_info in summary["stages"].items():
                if stage_info["completed"]:
                    logger.info(
                        f"  {stage_info['description']}: "
                        f"{stage_info['processed']}/{stage_info['total']} items, "
                        f"duration: {timedelta(seconds=int(stage_info['duration']))}"
                    )
            logger.info(f"Stats: {self.stats}")

            return self.stats

        except Exception as e:
            logger.error(f"Watch sync failed: {e}", exc_info=True)
            self.stats["errors"] += 1
            raise

    def _pull_from_plex(self, movies_only=False, shows_only=False, since=None) -> Dict:
        """Pull watch state from Plex.

        Args:
            movies_only: If True, only process movies
            shows_only: If True, only process shows/episodes
            since: Optional datetime to only fetch items changed since this time

        Returns:
            Dict mapping (media_type, imdb_id, tmdb_id) -> watch_info
        """
        if since:
            logger.info(f"Pulling watch state from Plex (since {since})...")
        else:
            logger.info("Pulling watch state from Plex (full sync)...")

        plex_state = {}

        try:
            # Get items from cache manager and check their watch status
            cache = self.plex.cache.memory_cache

            # Build reverse lookup: rating_key -> external IDs for efficient lookup
            # This avoids O(n*m) complexity when searching for external IDs
            movie_key_to_ids = {}
            for imdb_id, data in cache.get("movies_by_imdb", {}).items():
                rating_key = data.get("ratingKey")
                if rating_key:
                    movie_key_to_ids[rating_key] = (imdb_id, None)
            for tmdb_id, data in cache.get("movies_by_tmdb", {}).items():
                rating_key = data.get("ratingKey")
                if rating_key:
                    existing = movie_key_to_ids.get(rating_key)
                    if existing:
                        movie_key_to_ids[rating_key] = (existing[0], tmdb_id)
                    else:
                        movie_key_to_ids[rating_key] = (None, tmdb_id)

            # Build show lookup: rating_key -> show_imdb
            show_key_to_imdb = {}
            for imdb_id, data in cache.get("shows_by_imdb", {}).items():
                rating_key = data.get("ratingKey")
                if rating_key:
                    show_key_to_imdb[rating_key] = imdb_id

            # Process movies from cache (unless shows_only is True)
            if not shows_only:
                for movie_data in cache.get("movies_list", []):
                    rating_key = movie_data.get("ratingKey")
                    if not rating_key:
                        continue

                    # Use cached watch status (no API call)
                    is_watched = movie_data.get("isWatched", False)
                    last_viewed = movie_data.get("lastViewedAt")

                    # Skip if we're in delta mode and item hasn't changed since last sync
                    if since and last_viewed:
                        last_viewed_dt = self._parse_plex_timestamp(last_viewed)
                        if last_viewed_dt and last_viewed_dt < since:
                            self.stats["items_skipped_due_to_delta"] += 1
                            continue

                    # Get external IDs from pre-built lookup
                    imdb_id, tmdb_id = movie_key_to_ids.get(rating_key, (None, None))

                    if imdb_id or tmdb_id:
                        key = ("movie", imdb_id, tmdb_id)
                        plex_state[key] = {
                            "watched": is_watched,
                            "last_watched_at": last_viewed,
                            "rating_key": rating_key,
                            "title": movie_data.get("title"),
                        }

            # Process shows from cache (unless movies_only is True)
            if not movies_only:
                shows_processed = 0
                episodes_processed = 0

                for show_data in cache.get("shows_list", []):
                    rating_key = show_data.get("ratingKey")
                    if not rating_key:
                        continue

                    # Get show's external ID from pre-built lookup
                    show_imdb = show_key_to_imdb.get(rating_key)

                    # Use cached watch status for show (no API call needed for show level)
                    # Episodes still need to be fetched from Plex as they're not cached
                    try:
                        show_item = self.plex._get_plex_item(rating_key)
                        if show_item and hasattr(show_item, "seasons"):
                            shows_processed += 1
                            for season in show_item.seasons():
                                season_num = getattr(season, "seasonNumber", None)
                                if not season_num:
                                    continue

                                # Process episodes in batches to limit memory
                                batch_episodes = []
                                batch_size = 50

                                if hasattr(season, "episodes"):
                                    for episode in season.episodes():
                                        batch_episodes.append(episode)

                                        # Process batch when it reaches batch_size
                                        if len(batch_episodes) >= batch_size:
                                            self._process_episode_batch(
                                                batch_episodes,
                                                show_data,
                                                show_imdb,
                                                season_num,
                                                plex_state,
                                                since,
                                            )
                                            episodes_processed += len(batch_episodes)
                                            batch_episodes = []

                                    # Process remaining episodes in batch
                                    if batch_episodes:
                                        self._process_episode_batch(
                                            batch_episodes,
                                            show_data,
                                            show_imdb,
                                            season_num,
                                            plex_state,
                                            since,
                                        )
                                        episodes_processed += len(batch_episodes)

                    except (AttributeError, NotFound) as e:
                        # Expected errors: show structure issues or missing items
                        logger.debug(
                            f"Could not process show episodes for {show_data.get('title')}: {e}"
                        )
                        continue
                    except Exception as e:
                        # Unexpected errors should be logged as warnings
                        logger.warning(
                            f"Unexpected error processing show {show_data.get('title')}: {e}"
                        )
                        continue

                logger.debug(
                    f"Processed {shows_processed} shows with {episodes_processed} episodes "
                    f"({self.stats['items_skipped_due_to_delta']} skipped due to delta)"
                )

            logger.info(f"Pulled {len(plex_state)} item(s) from Plex")
            return plex_state

        except Exception as e:
            logger.error(f"Failed to pull from Plex: {e}")
            return {}

    def _process_episode_batch(
        self,
        episodes,
        show_data,
        show_imdb,
        season_num,
        plex_state,
        since=None,
    ):
        """Process a batch of episodes and add to plex_state.

        This helper method reduces memory usage by processing episodes in batches
        rather than all at once.
        """
        for episode in episodes:
            ep_rating_key = episode.ratingKey
            is_watched, last_viewed = self.plex.is_watched(ep_rating_key)

            # Skip if we're in delta mode and item hasn't changed since last sync
            if since and last_viewed:
                last_viewed_dt = self._parse_plex_timestamp(last_viewed)
                if last_viewed_dt and last_viewed_dt < since:
                    self.stats["items_skipped_due_to_delta"] += 1
                    continue

            # Get episode number
            episode_num = getattr(episode, "episodeNumber", None)
            if episode_num is None:
                continue

            # Build unique key using show, season, and episode numbers
            key = ("episode", show_imdb, season_num, episode_num)
            plex_state[key] = {
                "watched": is_watched,
                "last_watched_at": last_viewed,
                "rating_key": ep_rating_key,
                "title": f"{show_data.get('title')} S{season_num}E{episode_num}",
            }

    def _pull_from_trakt(self, movies_only=False, shows_only=False, since=None) -> Dict:
        """Pull watch state from Trakt.

        Args:
            movies_only: If True, only process movies
            shows_only: If True, only process shows/episodes
            since: Optional datetime to only fetch items changed since this time

        Returns:
            Dict mapping (media_type, imdb_id, tmdb_id) -> watch_info
        """
        if since:
            logger.info(f"Pulling watch state from Trakt (since {since})...")
        else:
            logger.info("Pulling watch state from Trakt (full sync)...")

        trakt_state = {}

        try:
            # Convert since datetime to ISO string for Trakt API
            start_at = since.isoformat() if since else None

            # Get watched history with delta filtering
            if not shows_only:
                # Get movie history
                movie_history = self.trakt.get_all_watched_history(media_type="movies")
                if since:
                    # Also get recent history for delta sync
                    recent_movie_history = self.trakt.get_watched_history(
                        media_type="movies", start_at=start_at
                    )
                    # Combine and deduplicate
                    all_movies = {}
                    for movie in movie_history + recent_movie_history:
                        ids = movie.get("movie", {}).get("ids", {})
                        key = (
                            "movie",
                            ids.get("imdb"),
                            normalize_tmdb_id(ids.get("tmdb")),
                        )
                        # Keep the most recent entry
                        if key not in all_movies or movie.get("last_watched_at", "") > all_movies[
                            key
                        ].get("last_watched_at", ""):
                            all_movies[key] = movie

                    watched_movies = list(all_movies.values())
                else:
                    watched_movies = movie_history

                for movie in watched_movies:
                    ids = movie.get("movie", {}).get("ids", {})
                    key = (
                        "movie",
                        ids.get("imdb"),
                        normalize_tmdb_id(ids.get("tmdb")),
                    )

                    trakt_state[key] = {
                        "watched": True,
                        "last_watched_at": movie.get("watched_at") or movie.get("last_watched_at"),
                        "trakt_id": ids.get("trakt"),
                        "title": movie.get("movie", {}).get("title"),
                        "plays": movie.get("plays", 1),
                    }

            # Get watched shows/episodes (unless movies_only is True)
            if not movies_only:
                watched_shows = self.trakt.get_watched_shows()
                for show in watched_shows:
                    show_ids = show.get("show", {}).get("ids", {})
                    show_title = show.get("show", {}).get("title", "Unknown")

                    for season in show.get("seasons", []):
                        season_num = season.get("number")
                        for episode in season.get("episodes", []):
                            # Handle episode being either a dict or just an episode number
                            if isinstance(episode, dict):
                                episode_num = episode.get("number")
                                ep_ids = episode.get("ids", {})
                                last_watched_at = episode.get("last_watched_at")
                                trakt_id = ep_ids.get("trakt")
                            else:
                                # Episode is just the episode number (int)
                                episode_num = episode
                                ep_ids = {}
                                last_watched_at = None
                                trakt_id = None

                            # Build unique key using show, season, and episode numbers
                            key = (
                                "episode",
                                show_ids.get("imdb"),
                                season_num,
                                episode_num,
                            )

                            trakt_state[key] = {
                                "watched": True,
                                "last_watched_at": last_watched_at,
                                "trakt_id": trakt_id,
                                "title": f"{show_title} S{season_num}E{episode_num}",
                                "show_title": show_title,
                                "season": season_num,
                                "episode": episode_num,
                            }

            logger.info(f"Pulled {len(trakt_state)} item(s) from Trakt")
            return trakt_state

        except requests.exceptions.RequestException as e:
            logger.error(f"Trakt API request failed: {e}")
            return {}
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Trakt data parsing error: {e}")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error pulling from Trakt: {e}")
            return {}

    def _calculate_changes(self, plex_state, trakt_state, direction):
        """Calculate what changes need to be made.

        Args:
            plex_state: Dict of Plex watch states
            trakt_state: Dict of Trakt watch states
            direction: Sync direction

        Returns:
            Dict with changes to apply to each platform
        """
        changes = {
            "plex": {"mark_watched": [], "mark_unwatched": []},
            "trakt": {"mark_watched": [], "mark_unwatched": []},
        }

        # Get all unique keys
        all_keys = set(plex_state.keys()) | set(trakt_state.keys())

        for key in all_keys:
            plex_info = plex_state.get(key)
            trakt_info = trakt_state.get(key)

            # Handle variable-length keys: movies are 3-tuple, episodes are 4-tuple
            # Format: ("movie", imdb_id, tmdb_id) or ("episode", show_imdb, season_num, episode_num)
            media_type = key[0]
            imdb_id = key[1]  # For movies: imdb_id, for episodes: show_imdb
            # tmdb_id only exists for movies (3-tuple), not for episodes (4-tuple)
            tmdb_id = key[2] if media_type == "movie" else None

            # Skip if we can't sync this direction
            if direction == "plex-to-trakt" and not plex_info:
                continue
            if direction == "trakt-to-plex" and not trakt_info:
                continue

            # Determine action based on conflict resolution
            action = self.resolver.resolve(
                plex_watched=plex_info.get("watched") if plex_info else False,
                trakt_watched=trakt_info.get("watched") if trakt_info else False,
                plex_last_watched=plex_info.get("last_watched_at") if plex_info else None,
                trakt_last_watched=trakt_info.get("last_watched_at") if trakt_info else None,
            )

            if action == "push_to_trakt":
                if plex_info and plex_info.get("watched"):
                    changes["trakt"]["mark_watched"].append(
                        {
                            "key": key,
                            "imdb_id": imdb_id,
                            "tmdb_id": tmdb_id,
                            "media_type": media_type,
                            "watched_at": plex_info.get("last_watched_at"),
                            "title": plex_info.get("title"),
                            "rating_key": plex_info.get("rating_key"),
                        }
                    )
                elif plex_info:
                    changes["trakt"]["mark_unwatched"].append(
                        {
                            "key": key,
                            "imdb_id": imdb_id,
                            "tmdb_id": tmdb_id,
                            "media_type": media_type,
                            "title": plex_info.get("title"),
                        }
                    )

            elif action == "push_to_plex":
                if trakt_info and trakt_info.get("watched"):
                    # Only push to Plex if the item exists in Plex
                    rating_key = plex_info.get("rating_key") if plex_info else None
                    if rating_key:
                        changes["plex"]["mark_watched"].append(
                            {
                                "key": key,
                                "imdb_id": imdb_id,
                                "tmdb_id": tmdb_id,
                                "media_type": media_type,
                                "rating_key": rating_key,
                                "title": trakt_info.get("title"),
                                "trakt_id": trakt_info.get("trakt_id"),
                            }
                        )
                    else:
                        logger.debug(
                            f"Skipping push to Plex for {trakt_info.get('title')}: not found in Plex library"
                        )
                elif trakt_info:
                    # Only push unwatched to Plex if the item exists in Plex
                    rating_key = plex_info.get("rating_key") if plex_info else None
                    if rating_key:
                        changes["plex"]["mark_unwatched"].append(
                            {
                                "key": key,
                                "imdb_id": imdb_id,
                                "tmdb_id": tmdb_id,
                                "media_type": media_type,
                                "rating_key": rating_key,
                                "title": trakt_info.get("title"),
                                "trakt_id": trakt_info.get("trakt_id"),
                            }
                        )
                    else:
                        logger.debug(
                            f"Skipping mark unwatched in Plex for {trakt_info.get('title')}: not found in Plex library"
                        )

            if action != "no_action":
                self.stats["conflicts_resolved"] += 1

        logger.info(
            f"Calculated changes: {len(changes['plex']['mark_watched'])} to mark watched in Plex, "
            f"{len(changes['plex']['mark_unwatched'])} to mark unwatched in Plex, "
            f"{len(changes['trakt']['mark_watched'])} to mark watched in Trakt, "
            f"{len(changes['trakt']['mark_unwatched'])} to mark unwatched in Trakt"
        )

        return changes

    def _apply_changes(self, changes):
        """Apply calculated changes to both platforms.

        Args:
            changes: Dict with changes for each platform
        """
        # Apply Plex changes in batches
        plex_watched_keys = []
        plex_unwatched_keys = []
        plex_items_to_update = []

        for item in changes["plex"]["mark_watched"]:
            if item.get("rating_key"):
                plex_watched_keys.append(item["rating_key"])
                plex_items_to_update.append({"item": item, "watched": True})

        for item in changes["plex"]["mark_unwatched"]:
            if item.get("rating_key"):
                plex_unwatched_keys.append(item["rating_key"])
                plex_items_to_update.append({"item": item, "watched": False})

        # Batch mark as watched
        if plex_watched_keys:
            logger.info(f"Batch marking {len(plex_watched_keys)} items as watched in Plex...")
            result = self.plex.batch_mark_as_watched(plex_watched_keys)
            self.stats["plex_watched"] += result["success"]
            self.stats["errors"] += result["failed"]

        # Batch mark as unwatched
        if plex_unwatched_keys:
            logger.info(f"Batch marking {len(plex_unwatched_keys)} items as unwatched in Plex...")
            result = self.plex.batch_mark_as_unwatched(plex_unwatched_keys)
            self.stats["plex_unwatched"] += result["success"]
            self.stats["errors"] += result["failed"]

        # Update history for successful items
        for update in plex_items_to_update:
            item = update["item"]
            # Note: In a real implementation, we'd track which items succeeded/failed
            # For now, we'll update history for all items
            self.history.add_or_update_synced_item(
                media_type=item["media_type"],
                imdb_id=item["imdb_id"],
                tmdb_id=item.get("tmdb_id"),
                plex_rating_key=item["rating_key"],
                trakt_id=item.get("trakt_id"),
                watched_plex=update["watched"],
            )

        # Apply Trakt changes in batches
        movies_to_add = []
        episodes_to_add = []

        for item in changes["trakt"]["mark_watched"]:
            if item["media_type"] == "movie":
                movies_to_add.append(
                    {
                        "ids": (
                            {"imdb": item["imdb_id"], "tmdb": item["tmdb_id"]}
                            if item["imdb_id"] or item["tmdb_id"]
                            else {}
                        ),
                    }
                )
            else:
                # Episode keys are ("episode", show_imdb, season, episode_num)
                key = item["key"]
                season = key[2] if len(key) > 2 else None
                episode_num = key[3] if len(key) > 3 else None
                episodes_to_add.append(
                    {
                        "ids": {"imdb": item["imdb_id"]} if item["imdb_id"] else {},
                        "season": season,
                        "number": episode_num,
                    }
                )

        if movies_to_add or episodes_to_add:
            try:
                result = self.trakt.add_to_history(
                    movies=movies_to_add if movies_to_add else None,
                    episodes=episodes_to_add if episodes_to_add else None,
                )
                if result:
                    added_movies = result.get("added", {}).get("movies", 0)
                    added_episodes = result.get("added", {}).get("episodes", 0)
                    self.stats["trakt_watched"] += added_movies + added_episodes
            except Exception as e:
                logger.error(f"Failed to add items to Trakt history: {e}")
                self.stats["errors"] += len(movies_to_add) + len(episodes_to_add)

        # Handle Trakt unwatched (remove from history)
        movies_to_remove = []
        episodes_to_remove = []

        for item in changes["trakt"]["mark_unwatched"]:
            if item["media_type"] == "movie":
                movies_to_remove.append(
                    {
                        "ids": (
                            {"imdb": item["imdb_id"], "tmdb": item["tmdb_id"]}
                            if item["imdb_id"] or item["tmdb_id"]
                            else {}
                        ),
                    }
                )
            else:
                # Episode keys are ("episode", show_imdb, season, episode_num)
                key = item["key"]
                season = key[2] if len(key) > 2 else None
                episode_num = key[3] if len(key) > 3 else None
                episodes_to_remove.append(
                    {
                        "ids": {"imdb": item["imdb_id"]} if item["imdb_id"] else {},
                        "season": season,
                        "number": episode_num,
                    }
                )

        if movies_to_remove or episodes_to_remove:
            try:
                result = self.trakt.remove_from_history(
                    movies=movies_to_remove if movies_to_remove else None,
                    episodes=episodes_to_remove if episodes_to_remove else None,
                )
                if result:
                    deleted_movies = result.get("deleted", {}).get("movies", 0)
                    deleted_episodes = result.get("deleted", {}).get("episodes", 0)
                    self.stats["trakt_unwatched"] += deleted_movies + deleted_episodes
            except Exception as e:
                logger.error(f"Failed to remove items from Trakt history: {e}")
                self.stats["errors"] += len(movies_to_remove) + len(episodes_to_remove)

    def _log_dry_run_changes(self, changes):
        """Log what changes would be made in dry-run mode.

        Args:
            changes: Dict with changes for each platform
        """
        logger.info("DRY RUN - Changes that would be made:")

        logger.info(f"  Plex: Mark {len(changes['plex']['mark_watched'])} items as watched")
        for item in changes["plex"]["mark_watched"][:5]:
            logger.info(f"    - {item.get('title', 'Unknown')}")
        if len(changes["plex"]["mark_watched"]) > 5:
            logger.info(f"    ... and {len(changes['plex']['mark_watched']) - 5} more")

        logger.info(f"  Plex: Mark {len(changes['plex']['mark_unwatched'])} items as unwatched")
        for item in changes["plex"]["mark_unwatched"][:5]:
            logger.info(f"    - {item.get('title', 'Unknown')}")
        if len(changes["plex"]["mark_unwatched"]) > 5:
            logger.info(f"    ... and {len(changes['plex']['mark_unwatched']) - 5} more")

        logger.info(f"  Trakt: Mark {len(changes['trakt']['mark_watched'])} items as watched")
        for item in changes["trakt"]["mark_watched"][:5]:
            logger.info(f"    - {item.get('title', 'Unknown')}")
        if len(changes["trakt"]["mark_watched"]) > 5:
            logger.info(f"    ... and {len(changes['trakt']['mark_watched']) - 5} more")

        logger.info(f"  Trakt: Mark {len(changes['trakt']['mark_unwatched'])} items as unwatched")
        for item in changes["trakt"]["mark_unwatched"][:5]:
            logger.info(f"    - {item.get('title', 'Unknown')}")
        if len(changes["trakt"]["mark_unwatched"]) > 5:
            logger.info(f"    ... and {len(changes['trakt']['mark_unwatched']) - 5} more")

        # Update stats for reporting
        self.stats["plex_watched"] = len(changes["plex"]["mark_watched"])
        self.stats["plex_unwatched"] = len(changes["plex"]["mark_unwatched"])
        self.stats["trakt_watched"] = len(changes["trakt"]["mark_watched"])
        self.stats["trakt_unwatched"] = len(changes["trakt"]["mark_unwatched"])

    def sync_playback_progress(
        self, dry_run: bool = False, movies_only: bool = False, shows_only: bool = False
    ) -> Dict[str, Any]:
        """Sync playback progress (resume points) from Trakt to Plex.

        Note: Currently only supports Trakt -> Plex direction due to Trakt API
        limitations for setting progress. Plex progress will be updated to
        match Trakt's paused positions.

        Args:
            dry_run: If True, only simulate changes without applying them
            movies_only: If True, only sync movie progress
            shows_only: If True, only sync episode progress

        Returns:
            Dict with progress sync statistics
        """
        if movies_only and shows_only:
            logger.warning(
                "Both --sync-movies-only and --sync-shows-only specified, defaulting to movies only"
            )
            shows_only = False

        content_type = "movies" if movies_only else "episodes" if shows_only else "all"
        logger.info("=" * 80)
        logger.info(f"Starting playback progress sync (dry_run={dry_run}, content={content_type})")
        logger.info("=" * 80)

        progress_stats = {
            "plex_progress_updated": 0,
            "trakt_progress_updated": 0,
            "progress_conflicts": 0,
            "errors": 0,
            "skipped_no_progress": 0,
            "skipped_already_in_sync": 0,
        }

        try:
            # Determine media type filter for Trakt API
            trakt_media_type = None
            if movies_only:
                trakt_media_type = "movies"
            elif shows_only:
                trakt_media_type = "episodes"

            # Get progress from Trakt
            logger.info("Fetching playback progress from Trakt...")
            trakt_progress = self.trakt.get_playback_progress(media_type=trakt_media_type)

            if not trakt_progress:
                logger.info("No playback progress found in Trakt")
                return progress_stats

            logger.info(f"Found {len(trakt_progress)} item(s) with progress in Trakt")

            # Get Plex cache for lookups
            cache = self.plex.cache.memory_cache

            # Build lookup: imdb_id -> rating_key for movies
            movie_imdb_to_rating_key = {}
            for imdb_id, data in cache.get("movies_by_imdb", {}).items():
                rating_key = data.get("ratingKey")
                if rating_key and imdb_id:
                    movie_imdb_to_rating_key[imdb_id] = rating_key

            # Build lookup: (show_imdb, season, episode) -> rating_key for episodes
            # Note: Episode progress sync is not yet implemented.
            # This would require scanning all episodes which is expensive.
            # Currently only movie progress is synced - show progress skipped.

            # Process each Trakt progress item
            progress_updates = []

            for key, progress_info in trakt_progress.items():
                media_type = key[0] if isinstance(key, tuple) and len(key) > 0 else None

                if media_type == "movie":
                    imdb_id = key[1] if len(key) > 1 else None

                    if not imdb_id:
                        progress_stats["skipped_no_progress"] += 1
                        continue

                    # Find movie in Plex
                    rating_key = movie_imdb_to_rating_key.get(imdb_id)
                    if not rating_key:
                        logger.debug(
                            f"Movie with IMDb {imdb_id} not found in Plex, skipping progress sync"
                        )
                        progress_stats["skipped_no_progress"] += 1
                        continue

                    # Get current Plex progress
                    plex_offset_ms, plex_duration = self.plex.get_playback_progress(rating_key)

                    # Calculate Trakt progress in milliseconds
                    progress_percent = progress_info.get("progress_percent", 0)

                    # Get duration from cache
                    movie_data = cache.get("movies_by_imdb", {}).get(imdb_id, {})
                    # Plex stores duration in milliseconds
                    duration_ms = (
                        movie_data.get("duration", plex_duration)
                        if isinstance(movie_data, dict)
                        else plex_duration
                    )

                    if not duration_ms:
                        logger.debug(f"No duration available for movie {imdb_id}, skipping")
                        progress_stats["skipped_no_progress"] += 1
                        continue

                    trakt_offset_ms = int(duration_ms * progress_percent / 100)

                    # Check if update is needed (only if significantly different)
                    if (
                        plex_offset_ms is not None
                        and abs(plex_offset_ms - trakt_offset_ms) < DEFAULT_PROGRESS_THRESHOLD_MS
                    ):
                        logger.debug(
                            f"Progress already in sync for {progress_info.get('title', 'Unknown')}"
                        )
                        progress_stats["skipped_already_in_sync"] += 1
                        continue

                    progress_stats["progress_conflicts"] += 1

                    if dry_run:
                        logger.info(
                            f"[DRY RUN] Would update progress for {progress_info.get('title', 'Unknown')}: {progress_percent:.1f}%"
                        )
                    else:
                        progress_updates.append(
                            {
                                "rating_key": rating_key,
                                "view_offset_ms": trakt_offset_ms,
                                "title": progress_info.get("title", "Unknown"),
                                "progress_percent": progress_percent,
                            }
                        )

                elif media_type == "episode":
                    # Episode progress sync is more complex - skip for now
                    # Would need to build episode lookup cache
                    logger.debug("Episode progress sync not yet implemented")
                    progress_stats["skipped_no_progress"] += 1

            # Apply progress updates in batch
            if progress_updates and not dry_run:
                logger.info(
                    f"Updating playback progress for {len(progress_updates)} item(s) in Plex..."
                )
                result = self.plex.batch_set_playback_progress(progress_updates)
                progress_stats["plex_progress_updated"] = result["success"]
                progress_stats["errors"] += result["failed"]

                # Log failures
                for error in result.get("errors", [])[:5]:
                    logger.warning(f"Failed to update progress: {error}")
                if len(result.get("errors", [])) > 5:
                    logger.warning(f"... and {len(result['errors']) - 5} more failures")

            logger.info("=" * 80)
            logger.info("Playback progress sync completed")
            logger.info("=" * 80)
            logger.info(f"Plex progress updated: {progress_stats['plex_progress_updated']}")
            logger.info(f"Skipped (no progress): {progress_stats['skipped_no_progress']}")
            logger.info(f"Skipped (already in sync): {progress_stats['skipped_already_in_sync']}")
            logger.info(f"Errors: {progress_stats['errors']}")

            # Update main stats
            self.stats["plex_progress_updated"] = progress_stats["plex_progress_updated"]
            self.stats["progress_conflicts"] = progress_stats["progress_conflicts"]

            return progress_stats

        except requests.exceptions.RequestException as e:
            logger.error(f"Trakt API request failed during progress sync: {e}")
            progress_stats["errors"] += 1
            return progress_stats
        except Exception as e:
            logger.error(f"Unexpected error during progress sync: {e}", exc_info=True)
            progress_stats["errors"] += 1
            return progress_stats

    def get_sync_summary(self) -> Dict[str, Any]:
        """Get a summary of the last sync operation.

        Returns:
            Dict with sync summary information
        """
        last_sync = self.history.get_last_sync_timestamp()
        stats = self.history.get_stats()

        return {
            "last_sync": last_sync.isoformat() if last_sync else None,
            "total_tracked_items": stats["total_items"],
            "watched_both": stats["watched_both"],
            "watched_plex_only": stats["watched_plex_only"],
            "watched_trakt_only": stats["watched_trakt_only"],
            "unwatched_both": stats["unwatched_both"],
            "last_operation_stats": self.stats,
        }
