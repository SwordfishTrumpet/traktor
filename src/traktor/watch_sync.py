"""Watch status synchronization engine."""

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import requests
from plexapi.exceptions import NotFound

from .log import logger
from .progress import SyncProgress
from .utils import format_trakt_timestamp, normalize_tmdb_id, parse_timestamp

# Constants for progress sync
DEFAULT_PROGRESS_THRESHOLD_MS = (
    30000  # 30 seconds - only sync if progress differs by more than this
)

# Default time window for first sync (days) - limits initial sync to recent items
DEFAULT_FIRST_SYNC_WINDOW_DAYS = 7

# Show-level Plex counters that move whenever any episode's watch state changes,
# and therefore decide whether a cached episode snapshot is still accurate (#21)
_SHOW_COUNTER_FIELDS = ("viewedLeafCount", "leafCount", "lastViewedAt")

# A snapshot episode row is [season, number, rating_key, watched, lastViewedAt]
_EPISODE_ROW_FIELDS = 5

# A show whose read fails transiently is retried once before the pull gives up
# (issue #22). One retry covers the read timeouts Plex actually returns; more
# would just delay the report the owner needs.
SHOW_READ_ATTEMPTS = 2
SHOW_READ_RETRY_DELAY_SECONDS = 2.0

# Transport errors that a retry can plausibly fix. requests covers plexapi's
# ReadTimeout/ConnectionError, TimeoutError covers socket timeouts.
_TRANSIENT_READ_ERRORS = (requests.exceptions.RequestException, TimeoutError)


class WatchStatePullError(Exception):
    """Raised when pulling watch state from Plex or Trakt fails.

    Pull helpers must NEVER silently degrade to an empty state dict: an empty
    state is indistinguishable from "everything unwatched", which would cause
    the resolver to push a full-library history rewrite against the other
    platform (see GitHub issue #1). Raising aborts sync_watched_status() before
    any change is applied and before last_sync_timestamp advances.
    """


class WatchSyncEngine:
    """Engine for bidirectional watch status synchronization.

    Handles pulling watch state from Plex and Trakt, resolving conflicts,
    and pushing changes to both platforms.
    """

    @staticmethod
    def _parse_plex_timestamp(timestamp):
        """Parse a Plex timestamp to an aware UTC datetime.

        Plex (via plexapi) returns timestamps as naive *local* datetimes
        (``datetime.fromtimestamp``), which are serialized to naive ISO strings
        in the cache. This thin wrapper over the canonical
        :func:`utils.parse_timestamp` handles Unix epoch ints/floats, ISO
        strings, and datetime objects, and always returns a timezone-aware UTC
        datetime so the result can be compared against ``since`` (an aware UTC
        datetime) without raising TypeError.

        Naive datetimes are interpreted as system local time, matching
        plexapi's ``datetime.fromtimestamp()`` behavior.

        Args:
            timestamp: Unix timestamp (int/float), ISO string, or datetime

        Returns:
            Aware UTC datetime or None if parsing fails
        """
        return parse_timestamp(timestamp)

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
        self.stats: Dict[str, Any] = {
            "plex_watched": 0,
            "plex_unwatched": 0,
            "trakt_watched": 0,
            "trakt_unwatched": 0,
            "conflicts_resolved": 0,
            "errors": 0,
            "items_skipped_due_to_delta": 0,
            "shows_reused_from_snapshot": 0,
            "shows_skipped_no_watch_state": 0,
            "shows_unreadable": 0,
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
            "shows_reused_from_snapshot": 0,
            "shows_skipped_no_watch_state": 0,
            "shows_unreadable": 0,
            "plex_progress_updated": 0,
            "trakt_progress_updated": 0,
            "progress_conflicts": 0,
            "changes": [],
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
                last_sync = datetime.now(timezone.utc) - timedelta(
                    days=DEFAULT_FIRST_SYNC_WINDOW_DAYS
                )
                logger.info(
                    f"First sync: Processing items from last {DEFAULT_FIRST_SYNC_WINDOW_DAYS} days only (for speed)"
                )
                logger.info("Use --backfill-history for full history sync if needed")

            # Stage 1: Pull from Trakt. This runs first because the Plex pull
            # needs the Trakt state to decide which items the delta filter may
            # skip - an item is skipped only when both sides already agree, so a
            # watch the previous run never observed cannot be dropped (#19).
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

            # Stage 2: Pull from Plex
            progress.start_stage(
                name="pull_plex",
                total=0,  # We don't know total items until we process them
                desc="Pulling watch state from Plex",
                unit="items",
            )
            plex_state = self._pull_from_plex(
                movies_only=movies_only,
                shows_only=shows_only,
                since=last_sync,
                known_state=trakt_state,
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
            self.stats["changes"] = changes
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
                # Prune before this save too: _apply_changes() may not have run
                # (a dry run) and the run still persists state (issue #69).
                self.history.prune_synced_items()
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

    def _delta_skip_allowed(
        self,
        key,
        is_watched,
        last_viewed_dt,
        known_state,
        since,
    ):
        """Decide whether the delta filter may drop an item before comparison.

        The delta filter is an optimisation, so it may only drop an item whose
        outcome the resolver would ignore anyway. Dropping an item just because
        it fell outside the window loses every change the previous run failed to
        observe: the Plex state comes from the library cache snapshot taken when
        that run started, while the window advanced to when it ended, so a watch
        recorded inside the run falls behind the window and is skipped on every
        later run too (issue #19).

        Skipping is therefore allowed only when the other platform already holds
        the same state, which is exactly the case where no change is possible.

        Args:
            key: Media key (3-tuple for movies, 4-tuple for episodes)
            is_watched: Watch state read from Plex
            last_viewed_dt: Plex last-viewed timestamp, aware UTC or None
            known_state: Watch state already pulled from the other platform
            since: Delta window start, or None for a full sync

        Returns:
            True when the item can be skipped without losing a change
        """
        since_dt = self._parse_plex_timestamp(since)
        if since_dt is None or last_viewed_dt is None:
            return False
        if last_viewed_dt >= since_dt:
            return False

        known = (known_state or {}).get(key) or {}
        action = self.resolver.resolve(
            plex_watched=is_watched,
            trakt_watched=bool(known.get("watched")),
            plex_last_watched=last_viewed_dt,
            trakt_last_watched=known.get("last_watched_at"),
        )
        return action == "no_action"

    def _pull_from_plex(
        self, movies_only=False, shows_only=False, since=None, known_state=None
    ) -> Dict:
        """Pull watch state from Plex.

        Args:
            movies_only: If True, only process movies
            shows_only: If True, only process shows/episodes
            since: Optional datetime to only fetch items changed since this time
            known_state: Watch state already pulled from Trakt, used to decide
                whether the delta filter may skip an item (issue #19) and
                whether a show has nothing to transfer at all (issue #39)

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

            # Reverse lookups are pre-built by CacheManager (pure functions of
            # the cache, built once when loaded - see TODO audit A3/B3).
            movie_key_to_ids = self.plex.cache.movie_key_to_ids
            show_key_to_imdb = self.plex.cache.show_key_to_imdb

            # Process movies from cache (unless shows_only is True)
            if not shows_only:
                for movie_data in cache.get("movies_list", []):
                    rating_key = movie_data.get("ratingKey")
                    if not rating_key:
                        continue

                    # Use cached watch status (no API call)
                    is_watched = movie_data.get("isWatched", False)
                    last_viewed = movie_data.get("lastViewedAt")
                    last_viewed_dt = self._parse_plex_timestamp(last_viewed)

                    # Get external IDs from pre-built lookup
                    imdb_id, tmdb_id = movie_key_to_ids.get(rating_key, (None, None))
                    key = ("movie", imdb_id, tmdb_id)

                    # Skip only when the other side already agrees (see
                    # _delta_skip_allowed)
                    if self._delta_skip_allowed(
                        key, is_watched, last_viewed_dt, known_state, since
                    ):
                        self.stats["items_skipped_due_to_delta"] += 1
                        continue

                    if imdb_id or tmdb_id:
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
                shows_reused = 0
                shows_skipped = 0

                # One cheap read of the show-level watch counters, on every kind
                # of pull. A show's cached episodes may only be reused while
                # these still match the counters they were read with, and the
                # same counters say whether the show holds a watched episode at
                # all; anything unread stays out of the mapping and is treated
                # as changed (issues #21, #39). This costs one paged request per
                # show section, against the one season request per show it saves.
                live_counters = self.plex.get_show_counters()

                # A show with a watched episode in Trakt needs its Plex episode
                # rating keys to be pushable, so it is never skipped (#39).
                trakt_watched_shows = self._trakt_shows_with_watched_episodes(known_state)

                try:
                    for show_data in cache.get("shows_list", []):
                        rating_key = show_data.get("ratingKey")
                        if not rating_key:
                            continue

                        # Get show's external ID from pre-built lookup
                        show_imdb = show_key_to_imdb.get(rating_key)

                        # A show that neither platform reports a watched episode
                        # for has nothing to transfer, so its seasons and
                        # episodes are not read at all (issue #39).
                        if self._show_has_no_watch_state(
                            live_counters.get(rating_key), show_imdb, trakt_watched_shows
                        ):
                            shows_skipped += 1
                            continue

                        # Delta runs reuse a show's cached episodes when Plex's
                        # counters say nothing in it changed, which is where the
                        # 100k+ season and episode requests used to go (#21).
                        snapshot = (
                            self.plex.cache.get_show_episodes(rating_key)
                            if since and hasattr(self.plex.cache, "get_show_episodes")
                            else None
                        )
                        if self._show_episodes_unchanged(snapshot, live_counters.get(rating_key)):
                            shows_processed += 1
                            shows_reused += 1
                            episodes_processed += self._replay_show_episodes(
                                snapshot, show_data, show_imdb, plex_state, since, known_state
                            )
                            continue

                        # Read this show from Plex (full pull, changed show, or
                        # no snapshot yet) and record what was read for later
                        # delta runs.
                        try:
                            read = self._read_show_with_retry(
                                rating_key, show_data, show_imdb, plex_state, since, known_state
                            )
                        except (AttributeError, NotFound) as e:
                            # Plex says this show is gone or its structure is
                            # unreadable. Not watch state we can transfer, but it
                            # must be visible instead of looking unwatched (#22).
                            self.plex.cache.forget_show_episodes(rating_key)
                            self._record_unreadable_show(show_data, e)
                            continue
                        except WatchStatePullError:
                            # Never let a failed read pass for an unwatched show.
                            self.plex.cache.forget_show_episodes(rating_key)
                            raise
                        except Exception as e:
                            self.plex.cache.forget_show_episodes(rating_key)
                            self._record_unreadable_show(show_data, e)
                            raise WatchStatePullError(
                                f"Could not read watch state for show "
                                f"'{show_data.get('title')}': {e}"
                            ) from e

                        if read is None:
                            # Plex no longer has this rating key (stale cache entry).
                            self.plex.cache.forget_show_episodes(rating_key)
                            self._record_unreadable_show(
                                show_data, "not present in the Plex library"
                            )
                            continue

                        show_rows, episodes_seen, item_counters = read
                        shows_processed += 1
                        episodes_processed += episodes_seen

                        counters = item_counters
                        if not self._counters_known(counters):
                            # No usable counters from either read: keep no
                            # snapshot rather than an unusable one.
                            counters = live_counters.get(rating_key) or {}
                        if self._counters_known(counters):
                            self.plex.cache.record_show_episodes(rating_key, counters, show_rows)
                        else:
                            self.plex.cache.forget_show_episodes(rating_key)

                finally:
                    # Persist once per pull, and only when something changed.
                    self.plex.cache.flush_show_episodes()

                self.stats["shows_reused_from_snapshot"] = shows_reused
                self.stats["shows_skipped_no_watch_state"] = shows_skipped
                logger.debug(
                    f"Processed {shows_processed} shows ({shows_reused} reused from cached "
                    f"episode state, {shows_skipped} skipped with no watch state) with "
                    f"{episodes_processed} episodes "
                    f"({self.stats['items_skipped_due_to_delta']} skipped due to delta, "
                    f"{self.stats['shows_unreadable']} unreadable)"
                )
                if shows_reused:
                    logger.info(
                        f"Reused cached episode state for {shows_reused} of "
                        f"{shows_processed} show(s); no season or episode requests for them"
                    )
                if shows_skipped:
                    logger.info(
                        f"Skipped {shows_skipped} show(s) with no watched episode on "
                        f"either platform; no season or episode requests for them"
                    )

            logger.info(f"Pulled {len(plex_state)} item(s) from Plex")
            return plex_state

        except WatchStatePullError:
            # Already carries the detail (and the show name) - see #22.
            raise
        except Exception as e:
            # Never degrade to empty state - see WatchStatePullError docstring.
            logger.error(f"Failed to pull watch state from Plex: {e}", exc_info=True)
            raise WatchStatePullError(f"Failed to pull watch state from Plex: {e}") from e

    def _record_unreadable_show(self, show_data, error) -> None:
        """Count and name a show whose episode state could not be read (#22).

        The show is left out of the state, which is indistinguishable from "no
        episodes watched" unless it is reported, so it is counted in the run
        statistics and logged by name at WARNING.
        """
        self.stats["shows_unreadable"] += 1
        logger.warning(f"Show '{show_data.get('title')}' left out of the watch pull: {error}")

    @staticmethod
    def _trakt_shows_with_watched_episodes(known_state) -> Optional[Set[Any]]:
        """Collect the IMDb IDs of the shows Trakt reports a watched episode for.

        Args:
            known_state: Watch state already pulled from Trakt

        Returns:
            Set of show IMDb IDs, or None when the Trakt state was not supplied -
            an unknown Trakt side can never justify skipping a show (issue #39)
        """
        if not isinstance(known_state, dict):
            return None
        return {
            key[1]
            for key in known_state
            if isinstance(key, tuple) and key and key[0] == "episode" and key[1]
        }

    @classmethod
    def _show_has_no_watch_state(cls, counters, show_imdb, trakt_watched_shows) -> bool:
        """True when neither platform holds a watched episode for this show.

        A full pull used to read the seasons and episodes of every show in the
        library, so a first run, a cache reset and a backfill spent most of their
        time discovering that a show has nothing to report (4,369 of 4,596 shows
        on the owner's server, issue #39). Two cheap signals settle it: the
        show's own counters, and the Trakt state pulled moments earlier.

        The skip keeps the issue #19 rule - only an item the resolver would send
        to ``no_action`` may be dropped - because both platforms report the show
        as unwatched, so every episode of it resolves to ``no_action``. A show
        Trakt has a watched episode for is never skipped: pushing it to Plex
        needs the Plex rating keys a read would have collected.

        Args:
            counters: Counters read from Plex now, or None when unread
            show_imdb: Show IMDb ID, or None
            trakt_watched_shows: Show IMDb IDs with a watched episode in Trakt,
                or None when the Trakt state is unknown

        Returns:
            True only when Plex's counters are complete enough to rule out a
            watched episode and Trakt reports none either
        """
        if trakt_watched_shows is None:
            return False
        if not cls._counters_known(counters):
            return False
        if counters.get("viewedLeafCount"):
            return False
        return show_imdb not in trakt_watched_shows

    def _read_show_with_retry(
        self, rating_key, show_data, show_imdb, plex_state, since=None, known_state=None
    ):
        """Read one show's episodes, retrying a transient failure once.

        A read timeout used to leave the show out of the pull while the run
        reported success, so watch state for it never transferred (issue #22).
        One retry covers a slow server; if the failure persists the pull raises
        and the run is recorded failed with the show named.

        Args:
            rating_key: Plex rating key of the show
            show_data: Cache entry of the show
            show_imdb: Show IMDb ID, used to build episode keys
            plex_state: State dict being built
            since: Delta window start
            known_state: State already pulled from Trakt

        Returns:
            ``(rows, episodes_seen, counters)``, or None when Plex no longer has
            the show

        Raises:
            WatchStatePullError: the read kept failing after the last attempt
        """
        last_error = None
        for attempt in range(1, SHOW_READ_ATTEMPTS + 1):
            try:
                return self._read_show(
                    rating_key, show_data, show_imdb, plex_state, since, known_state
                )
            except _TRANSIENT_READ_ERRORS as e:
                last_error = e
                if attempt < SHOW_READ_ATTEMPTS:
                    logger.warning(
                        f"Retrying show '{show_data.get('title')}' after a transient "
                        f"Plex error (attempt {attempt}/{SHOW_READ_ATTEMPTS}): {e}"
                    )
                    time.sleep(SHOW_READ_RETRY_DELAY_SECONDS)

        raise WatchStatePullError(
            f"Could not read watch state for show '{show_data.get('title')}' after "
            f"{SHOW_READ_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    def _read_show(
        self, rating_key, show_data, show_imdb, plex_state, since=None, known_state=None
    ):
        """Read one show's seasons and episodes from Plex.

        Returns:
            ``(rows, episodes_seen, counters)`` for the episode snapshot, or None
            when the show is not in the Plex library any more
        """
        show_item = self.plex._get_plex_item(rating_key)
        if show_item is None or not hasattr(show_item, "seasons"):
            return None

        rows: List[List[Any]] = []
        episodes_seen = 0
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

                    if len(batch_episodes) >= batch_size:
                        rows.extend(
                            self._process_episode_batch(
                                batch_episodes,
                                show_data,
                                show_imdb,
                                season_num,
                                plex_state,
                                since,
                                known_state,
                            )
                        )
                        episodes_seen += len(batch_episodes)
                        batch_episodes = []

                if batch_episodes:
                    rows.extend(
                        self._process_episode_batch(
                            batch_episodes,
                            show_data,
                            show_imdb,
                            season_num,
                            plex_state,
                            since,
                            known_state,
                        )
                    )
                    episodes_seen += len(batch_episodes)

        return rows, episodes_seen, self.plex.show_counters(show_item)

    @staticmethod
    def _counters_known(counters) -> bool:
        """True when all three show counters carry a usable value.

        A missing counter cannot prove that a show's episodes are unchanged, so
        such a show is re-read rather than trusted (issue #21).
        """
        if not isinstance(counters, dict):
            return False
        if not isinstance(counters.get("viewedLeafCount"), int):
            return False
        if not isinstance(counters.get("leafCount"), int):
            return False
        return counters.get("lastViewedAt") is None or isinstance(counters.get("lastViewedAt"), str)

    @classmethod
    def _show_episodes_unchanged(cls, snapshot, current) -> bool:
        """True when a show's cached episodes still describe its live state.

        Plex's own show-level counters decide this: ``viewedLeafCount``,
        ``leafCount`` and ``lastViewedAt`` move whenever an episode is watched,
        un-watched, added or removed, so equal counters mean the cached rows are
        still accurate and no season or episode request is needed (issue #21).

        Args:
            snapshot: Cached snapshot from the previous pull, or None
            current: Counters read from Plex now, or None

        Returns:
            True only for a complete snapshot whose counters are unchanged
        """
        if not isinstance(snapshot, dict) or not cls._counters_known(current):
            return False
        if not isinstance(snapshot.get("episodes"), list):
            return False
        return all(snapshot.get(field) == current.get(field) for field in _SHOW_COUNTER_FIELDS)

    def _replay_show_episodes(
        self, snapshot, show_data, show_imdb, plex_state, since=None, known_state=None
    ) -> int:
        """Emit a show's episodes from its cached snapshot, without asking Plex.

        The rows reuse the live pull's per-episode path, so the resulting state
        is identical to a full read - only the requests are gone.

        Args:
            snapshot: Cached snapshot for the show
            show_data: Cache entry of the show
            show_imdb: Show IMDb ID, used to build episode keys
            plex_state: State dict being built
            since: Delta window start
            known_state: State already pulled from Trakt

        Returns:
            Number of episode rows replayed
        """
        emitted = 0
        for row in snapshot.get("episodes") or []:
            if not isinstance(row, (list, tuple)) or len(row) < _EPISODE_ROW_FIELDS:
                continue
            season_num, episode_num, episode_key, watched, last_viewed = row[:5]
            emitted += 1
            self._record_episode(
                show_data.get("title"),
                show_imdb,
                season_num,
                episode_num,
                bool(watched),
                last_viewed,
                episode_key,
                plex_state,
                since,
                known_state,
            )
        return emitted

    def _record_episode(
        self,
        show_title,
        show_imdb,
        season_num,
        episode_num,
        is_watched,
        last_viewed,
        episode_rating_key,
        plex_state,
        since=None,
        known_state=None,
    ) -> None:
        """Add one episode to plex_state, applying the #19 delta guard.

        Shared by the live Plex read and the cached-snapshot replay so both
        produce the same state - the live read normalizes its timestamp to the
        same ISO-8601 form the snapshot stores, so a replayed run is
        indistinguishable from a fetched one (issues #19, #21).
        """
        key = ("episode", show_imdb, season_num, episode_num)
        last_viewed_dt = self._parse_plex_timestamp(last_viewed)

        # Skip only when the other side already agrees (issue #19).
        if self._delta_skip_allowed(key, is_watched, last_viewed_dt, known_state, since):
            self.stats["items_skipped_due_to_delta"] += 1
            return

        plex_state[key] = {
            "watched": is_watched,
            "last_watched_at": last_viewed,
            "rating_key": episode_rating_key,
            "title": f"{show_title} S{season_num}E{episode_num}",
        }

    def _process_episode_batch(
        self,
        episodes,
        show_data,
        show_imdb,
        season_num,
        plex_state,
        since=None,
        known_state=None,
    ) -> List[List[Any]]:
        """Process a batch of episodes and add to plex_state.

        This helper method reduces memory usage by processing episodes in batches
        rather than all at once. It returns the compact snapshot rows for the
        cached-episode store (issue #21).

        Watch status is read directly from the episode objects returned by
        ``season.episodes()`` (``isWatched``/``lastViewedAt`` attributes are
        populated from that response). Previously every episode triggered a
        ``fetchItem`` API call via ``PlexClient.is_watched()`` because episodes
        were never added to the rating-key cache - an API storm of ~100k calls
        for large libraries (see TODO audit A1).
        """
        rows: List[List[Any]] = []
        for episode in episodes:
            ep_rating_key = episode.ratingKey

            # No per-episode API call: the season.episodes() response already
            # carries viewCount/lastViewedAt for every episode.
            is_watched = getattr(episode, "isWatched", False)
            last_viewed = getattr(episode, "lastViewedAt", None)

            # Get episode number
            episode_num = getattr(episode, "episodeNumber", None)
            if episode_num is None:
                continue

            parsed_last_viewed = self._parse_plex_timestamp(last_viewed)
            last_viewed_iso = parsed_last_viewed.isoformat() if parsed_last_viewed else None
            # Store the watched flag Plex reported, not the raw play count: the
            # replay must reproduce exactly what the live path emitted.
            rows.append(
                [season_num, episode_num, ep_rating_key, 1 if is_watched else 0, last_viewed_iso]
            )

            self._record_episode(
                show_data.get("title"),
                show_imdb,
                season_num,
                episode_num,
                is_watched,
                last_viewed_iso,
                ep_rating_key,
                plex_state,
                since,
                known_state,
            )

        return rows

    def _pull_from_trakt(self, movies_only=False, shows_only=False, since=None) -> Dict:
        """Pull watch state from Trakt.

        Args:
            movies_only: If True, only process movies
            shows_only: If True, only process shows/episodes
            since: Optional datetime to only fetch items changed since this time.
                NOTE: Only used for delta logging; movies always pull the full
                watched list so items watched before the last sync are not lost.

        Returns:
            Dict mapping (media_type, imdb_id, tmdb_id) -> watch_info
        """
        if since:
            logger.info(f"Pulling watch state from Trakt (since {since})...")
        else:
            logger.info("Pulling watch state from Trakt (full sync)...")

        trakt_state = {}

        try:
            # Movies: always fetch the full watched list (authoritative state).
            # We deliberately do NOT apply delta filtering here (history with
            # start_at) because that would silently drop movies watched before
            # the last sync - e.g. movies watched on other services weeks or
            # months ago would never be marked as watched in Plex. This matches
            # how shows are handled below (full get_watched_shows() list).
            # Delta filtering is still applied on the Plex side, where unwatched
            # items (no lastViewedAt) are never skipped, so the sync stays both
            # correct and cheap.
            if not shows_only:
                watched_movies = self.trakt.get_watched_movies()

                for movie in watched_movies:
                    ids = movie.get("movie", {}).get("ids", {})
                    key: tuple = (
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
            # Never degrade to empty state - see WatchStatePullError docstring.
            logger.error(f"Trakt API request failed: {e}", exc_info=True)
            raise WatchStatePullError(
                f"Trakt API request failed while pulling watch state: {e}"
            ) from e
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Trakt data parsing error: {e}", exc_info=True)
            raise WatchStatePullError(f"Trakt watch state parsing error: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error pulling from Trakt: {e}", exc_info=True)
            raise WatchStatePullError(
                f"Unexpected error pulling watch state from Trakt: {e}"
            ) from e

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

        # Batch mark as watched / unwatched, collecting per-item failures
        # (issue #9): failed items must not be recorded as synced in history.
        failed_rating_keys = set()

        if plex_watched_keys:
            logger.info(f"Batch marking {len(plex_watched_keys)} items as watched in Plex...")
            result = self.plex.batch_mark_as_watched(plex_watched_keys)
            self.stats["plex_watched"] += result["success"]
            self.stats["errors"] += result["failed"]
            failed_rating_keys.update(result.get("failed_rating_keys", []))

        if plex_unwatched_keys:
            logger.info(f"Batch marking {len(plex_unwatched_keys)} items as unwatched in Plex...")
            result = self.plex.batch_mark_as_unwatched(plex_unwatched_keys)
            self.stats["plex_unwatched"] += result["success"]
            self.stats["errors"] += result["failed"]
            failed_rating_keys.update(result.get("failed_rating_keys", []))

        # Update history for successful items, then persist once (issue #7):
        # add_or_update_synced_item() only mutates in-memory state; a single
        # save_state() at the end replaces one full-file write per item.
        try:
            for update in plex_items_to_update:
                item = update["item"]
                if item["rating_key"] in failed_rating_keys:
                    # Item's Plex operation failed - recording it as synced would
                    # remember a state that was never applied (issue #9).
                    logger.warning(
                        f"Skipping history record for failed item: {item.get('title', 'unknown')}"
                    )
                    continue
                self.history.add_or_update_synced_item(
                    media_type=item["media_type"],
                    imdb_id=item["imdb_id"],
                    tmdb_id=item.get("tmdb_id"),
                    plex_rating_key=item["rating_key"],
                    trakt_id=item.get("trakt_id"),
                    watched_plex=update["watched"],
                )
        finally:
            # Prune stale entries before every persist, not only when Plex items
            # changed (issue #69): a settled library - the state a converged sync
            # reaches - is exactly when expired entries used to survive for ever.
            self.history.prune_synced_items()
            self.history.save_state()

        # Apply Trakt changes in batches
        movies_to_add = []
        episodes_to_add = []

        for item in changes["trakt"]["mark_watched"]:
            # Carry the watch date across: Trakt stamps a play with "now" when
            # watched_at is omitted, so dropping it turns a sync into a
            # fabricated date (and a fresh duplicate play) on every push.
            watched_at = format_trakt_timestamp(item.get("watched_at"))

            if item["media_type"] == "movie":
                movie: Dict[str, Any] = {
                    "ids": (
                        {"imdb": item["imdb_id"], "tmdb": item["tmdb_id"]}
                        if item["imdb_id"] or item["tmdb_id"]
                        else {}
                    ),
                }
                if watched_at:
                    movie["watched_at"] = watched_at
                movies_to_add.append(movie)
            else:
                # Episode keys are ("episode", show_imdb, season, episode_num)
                key = item["key"]
                season = key[2] if len(key) > 2 else None
                episode_num = key[3] if len(key) > 3 else None
                episode: Dict[str, Any] = {
                    "ids": {"imdb": item["imdb_id"]} if item["imdb_id"] else {},
                    "season": season,
                    "number": episode_num,
                }
                if watched_at:
                    episode["watched_at"] = watched_at
                episodes_to_add.append(episode)

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
                    # Per-batch failures are surfaced by the client (issue #9);
                    # count the affected items as errors so partial outages are visible.
                    failed = result.get("failed", {})
                    self.stats["errors"] += failed.get("movies", 0) + failed.get("episodes", 0)
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
                    # Per-batch failures are surfaced by the client (issue #9)
                    failed = result.get("failed", {})
                    self.stats["errors"] += failed.get("movies", 0) + failed.get("episodes", 0)
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

            # Build lookup: imdb_id -> rating_key for movies (pre-built by
            # CacheManager, see TODO audit B3 - avoids duplicate reverse-map
            # construction between _pull_from_plex and this method).
            movie_imdb_to_rating_key = self.plex.cache.movie_imdb_to_rating_key

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
