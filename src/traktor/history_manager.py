"""Watch history tracking and sync state management."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .log import logger
from .settings import DATA_DIR, HISTORY_RETENTION_DAYS
from .utils import file_lock, parse_timestamp, read_json_object, write_json_atomic

# Sync state file location
WATCH_SYNC_FILE = DATA_DIR / ".traktor_watch_sync.json"


class WatchHistoryManager:
    """Manages sync state for watched status between Plex and Trakt.

    Tracks mappings between Plex rating keys and Trakt IDs, along with
    watch state timestamps for conflict resolution.
    """

    def __init__(self, plex_server_id=None):
        """Initialize watch history manager.

        Args:
            plex_server_id: Unique identifier for the Plex server
        """
        self.plex_server_id = plex_server_id
        self.sync_file = WATCH_SYNC_FILE
        self.state = self._load_state()
        self._build_index()
        # Entries this run dropped. save_state() removes them from the file after
        # merging, so a run that loaded an older snapshot cannot bring them back
        # (issues #65, #68).
        self._removed_keys: set = set()
        # clear_all() and restore_from_backup() replace the file rather than merge.
        self._replace_on_save = False

    def _build_index(self):
        """Build the plex_rating_key -> item index for O(1) lookups.

        Rebuilt on load/reset; maintained incrementally on add/remove
        (issue #7). Items without a plex_rating_key are not indexed.
        """
        self._item_index = {
            item.get("plex_rating_key"): item
            for item in self.state["synced_items"]
            if item.get("plex_rating_key") is not None
        }

    def _load_state(self):
        """Load sync state from disk."""
        if not self.sync_file.exists():
            logger.info("No existing watch sync state found")
            return self._create_empty_state()

        try:
            with open(self.sync_file, "r") as f:
                state = json.load(f)

            # Validate state format
            if not self._validate_state(state):
                logger.warning("Invalid watch sync state format, creating new state")
                return self._create_empty_state()

            # Check server ID matches
            if state.get("plex_server_id") != self.plex_server_id:
                logger.warning("Plex server ID mismatch, creating new state")
                return self._create_empty_state()

            logger.info(f"Loaded watch sync state with {len(state.get('synced_items', []))} items")
            return state

        except (json.JSONDecodeError, FileNotFoundError, PermissionError, OSError) as e:
            logger.error(f"Failed to load watch sync state: {e}")
            return self._create_empty_state()

    def _create_empty_state(self):
        """Create empty sync state structure."""
        return {
            "version": 1,
            "last_sync_timestamp": None,
            "plex_server_id": self.plex_server_id,
            "synced_items": [],
        }

    def _validate_state(self, state):
        """Validate sync state structure."""
        required_keys = ["version", "synced_items"]
        return all(key in state for key in required_keys)

    def save_state(self):
        """Merge this run's state into the file on disk and write it once (issue #65).

        Every run used to load the whole state and write it back in full, so a
        run that started earlier silently erased the rows a run that started
        after it had committed. The read-modify-write now runs under one lock
        and merges: the item with the newer ``updated_at`` wins, entries this
        run removed stay removed, and ``last_sync_timestamp`` only moves
        forward. The file is still replaced atomically, so an interrupted save
        leaves the previous file intact (issue #23).
        """
        with file_lock(self.sync_file):
            merged = self._merge_with_disk()
            written = write_json_atomic(self.sync_file, merged)
        if not written:
            logger.error(f"Failed to save watch sync state: {self.sync_file}")
            return
        self.state = merged
        self._build_index()
        self._removed_keys.clear()
        self._replace_on_save = False
        logger.debug(f"Saved watch sync state to {self.sync_file}")

    def _merge_with_disk(self) -> Dict[str, Any]:
        """Return this run's state merged with what is on disk right now."""
        on_disk = read_json_object(self.sync_file)
        if self._replace_on_save or not self._mergeable(on_disk):
            # Nothing to merge with, the file is unreadable, or it belongs to
            # another Plex server: this run's state is the whole truth.
            return dict(self.state)

        merged_items: list = []
        positions: Dict[Any, int] = {}
        unkeyed: list = []
        for item in on_disk.get("synced_items") or []:
            if not isinstance(item, dict):
                continue
            key = self._item_key(item)
            if key is None:
                unkeyed.append(item)
            else:
                positions[key] = len(merged_items)
                merged_items.append(item)

        for item in self.state["synced_items"]:
            key = self._item_key(item)
            if key is None:
                unkeyed.append(item)
                continue
            position = positions.get(key)
            if position is None:
                positions[key] = len(merged_items)
                merged_items.append(item)
            elif self._is_newer(item, merged_items[position]):
                merged_items[position] = item

        merged_items = [i for i in merged_items if self._item_key(i) not in self._removed_keys]
        merged_items.extend(unkeyed)

        merged = dict(on_disk)
        merged["version"] = self.state.get("version", 1)
        merged["plex_server_id"] = self.plex_server_id
        merged["synced_items"] = merged_items
        merged["last_sync_timestamp"] = self._later_timestamp(
            on_disk.get("last_sync_timestamp"), self.state.get("last_sync_timestamp")
        )
        return merged

    def _mergeable(self, on_disk: Dict[str, Any]) -> bool:
        """Return True when ``on_disk`` can be merged with this run's state."""
        if not on_disk or not self._validate_state(on_disk):
            return False
        return on_disk.get("plex_server_id") == self.plex_server_id

    @staticmethod
    def _item_key(item: Dict[str, Any]) -> Optional[Any]:
        """Return the identity two runs must agree on for one entry.

        The Plex rating key is exact. Without one the Trakt, TMDb or IMDb id is
        used, in that order of specificity; episodes of one show share an IMDb
        id, so an entry keyed only by it may take the place of a sibling.
        """
        rating_key = item.get("plex_rating_key")
        if rating_key is not None:
            return ("plex_rating_key", rating_key)
        for field in ("trakt_id", "tmdb_id", "imdb_id"):
            value = item.get(field)
            if value:
                return (field, str(value))
        return None

    @staticmethod
    def _is_newer(candidate: Dict[str, Any], current: Dict[str, Any]) -> bool:
        """Return True when ``candidate`` is the more recent version of an entry."""
        candidate_at = WatchHistoryManager._parse_entry_timestamp(candidate.get("updated_at"))
        current_at = WatchHistoryManager._parse_entry_timestamp(current.get("updated_at"))
        if candidate_at is None:
            return False
        if current_at is None:
            return True
        return candidate_at > current_at

    @staticmethod
    def _later_timestamp(first: Any, second: Any) -> Optional[str]:
        """Return the later of two ``last_sync_timestamp`` values."""
        first_at = parse_timestamp(first)
        second_at = parse_timestamp(second)
        if first_at is None:
            return second if second else first
        if second_at is None:
            return first
        return second if second_at >= first_at else first

    def update_last_sync_timestamp(self):
        """Update the last sync timestamp to now."""
        self.state["last_sync_timestamp"] = datetime.now(timezone.utc).isoformat()
        self.save_state()

    def get_last_sync_timestamp(self):
        """Get the last sync timestamp as datetime or None."""
        timestamp_str = self.state.get("last_sync_timestamp")
        if timestamp_str:
            try:
                return datetime.fromisoformat(timestamp_str)
            except ValueError:
                logger.warning(f"Invalid timestamp format: {timestamp_str}")
        return None

    def get_synced_item(self, imdb_id=None, tmdb_id=None, trakt_id=None, plex_rating_key=None):
        """Get a synced item by any of its identifiers.

        When ``plex_rating_key`` is provided, an O(1) index lookup is used
        first (issue #7); the linear scan remains as fallback for legacy
        entries stored before indexing or type-mismatched keys.

        Args:
            imdb_id: IMDb ID to search for
            tmdb_id: TMDb ID to search for
            trakt_id: Trakt ID to search for
            plex_rating_key: Plex rating key to search for

        Returns:
            Synced item dict or None if not found
        """
        if plex_rating_key is not None:
            indexed = self._item_index.get(plex_rating_key)
            if indexed is not None:
                return indexed

        for item in self.state["synced_items"]:
            if imdb_id and item.get("imdb_id") == imdb_id:
                return item
            if tmdb_id and str(item.get("tmdb_id")) == str(tmdb_id):
                return item
            if trakt_id and item.get("trakt_id") == trakt_id:
                return item
            if plex_rating_key and item.get("plex_rating_key") == plex_rating_key:
                return item
        return None

    def add_or_update_synced_item(
        self,
        media_type,
        imdb_id=None,
        tmdb_id=None,
        tvdb_id=None,
        trakt_id=None,
        plex_rating_key=None,
        watched_plex=None,
        watched_trakt=None,
        last_watched_at_plex=None,
        last_watched_at_trakt=None,
    ):
        """Add or update a synced item in the state.

        Args:
            media_type: Type of media (movie, episode, season, show)
            imdb_id: IMDb ID
            tmdb_id: TMDb ID
            tvdb_id: TVDb ID
            trakt_id: Trakt media ID
            plex_rating_key: Plex rating key
            watched_plex: Boolean for Plex watch status
            watched_trakt: Boolean for Trakt watch status
            last_watched_at_plex: ISO timestamp string for Plex watch
            last_watched_at_trakt: ISO timestamp string for Trakt watch
        """
        # Find existing item: exact O(1) lookup when we have a rating key.
        # Deliberately no multi-identifier fallback in that case: episodes of
        # one show share an imdb_id, so the legacy scan would cross-match them
        # (issue #7). Without a rating key, use the legacy scan.
        if plex_rating_key is not None:
            existing = self._item_index.get(plex_rating_key)
        else:
            existing = self.get_synced_item(
                imdb_id=imdb_id,
                tmdb_id=tmdb_id,
                trakt_id=trakt_id,
            )

        if existing:
            # Update existing item
            item = existing
        else:
            # Create new item
            item = {
                "imdb_id": imdb_id,
                "tmdb_id": str(tmdb_id) if tmdb_id else None,
                "tvdb_id": tvdb_id,
                "media_type": media_type,
                "plex_rating_key": plex_rating_key,
                "trakt_id": trakt_id,
                "sync_version": 1,
            }
            self.state["synced_items"].append(item)

        # Update fields if provided
        if watched_plex is not None:
            item["watched_plex"] = watched_plex
        if watched_trakt is not None:
            item["watched_trakt"] = watched_trakt
        if last_watched_at_plex is not None:
            item["last_watched_at_plex"] = last_watched_at_plex
        if last_watched_at_trakt is not None:
            item["last_watched_at_trakt"] = last_watched_at_trakt
        item["updated_at"] = datetime.now(timezone.utc).isoformat()

        logger.debug(f"Updated synced item: {item.get('imdb_id') or item.get('tmdb_id')}")
        if plex_rating_key is not None:
            self._item_index[plex_rating_key] = item
        # NOTE (issue #7): no save_state() here - callers mutate state for all
        # items and persist once at the end of the batch to avoid O(N) writes.

    def prune_synced_items(self, max_age_days=None):
        """Remove synced-item entries older than the retention window.

        History state is bookkeeping only - reconciliation resolves from live
        platform data - so stale entries can be dropped safely. This bounds
        the state file's growth (issue #7 follow-up).

        Args:
            max_age_days: Retention window in days; None uses
                settings.HISTORY_RETENTION_DAYS. 0 or negative disables pruning.

        Returns:
            Number of entries removed.
        """
        if max_age_days is None:
            max_age_days = HISTORY_RETENTION_DAYS
        if max_age_days <= 0:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        kept = []
        removed = 0
        for item in self.state["synced_items"]:
            age = self._entry_age_timestamp(item)
            if age is None or age < cutoff:
                removed += 1
                self._forget(item)
            else:
                kept.append(item)

        if removed:
            self.state["synced_items"] = kept
            logger.info(
                f"Pruned {removed} watch-history entr(y/ies) older than {max_age_days} days"
            )
        return removed

    @staticmethod
    def _entry_age_timestamp(item: Dict[str, Any]) -> Optional[datetime]:
        """Return the newest timestamp that can date an entry (issue #68).

        ``updated_at`` arrives with the release that introduced it. State files
        written before that carry none, and treating a missing stamp as "keep
        forever" meant the documented retention window never expired pre-upgrade
        history. The watch timestamps are the next evidence of age; an entry with
        no usable timestamp at all is bookkeeping that cannot be dated, so
        pruning drops it rather than keeping it for ever.
        """
        for field in ("updated_at", "last_watched_at_plex", "last_watched_at_trakt"):
            parsed = WatchHistoryManager._parse_entry_timestamp(item.get(field))
            if parsed is not None:
                return parsed
        return None

    def _forget(self, item: Dict[str, Any]) -> None:
        """Drop an entry from the index and record its removal for the save."""
        if item.get("plex_rating_key") is not None:
            self._item_index.pop(item.get("plex_rating_key"), None)
        key = self._item_key(item)
        if key is not None:
            self._removed_keys.add(key)

    @staticmethod
    def _parse_entry_timestamp(value):
        """Parse an entry timestamp through the shared parser; None if unusable."""
        return parse_timestamp(value)

    def remove_synced_item(self, imdb_id=None, tmdb_id=None, trakt_id=None, plex_rating_key=None):
        """Remove a synced item from the state.

        Args:
            imdb_id: IMDb ID to search for
            tmdb_id: TMDb ID to search for
            trakt_id: Trakt ID to search for
            plex_rating_key: Plex rating key to search for

        Returns:
            True if item was removed, False if not found
        """
        for i, item in enumerate(self.state["synced_items"]):
            match = False
            if imdb_id and item.get("imdb_id") == imdb_id:
                match = True
            elif tmdb_id and str(item.get("tmdb_id")) == str(tmdb_id):
                match = True
            elif trakt_id and item.get("trakt_id") == trakt_id:
                match = True
            elif plex_rating_key and item.get("plex_rating_key") == plex_rating_key:
                match = True

            if match:
                self.state["synced_items"].pop(i)
                self._forget(item)
                logger.debug(f"Removed synced item: {item.get('imdb_id') or item.get('tmdb_id')}")
                self.save_state()
                return True

        return False

    def get_stats(self):
        """Get statistics about synced items.

        Returns:
            Dict with sync statistics
        """
        items = self.state["synced_items"]
        return {
            "total_items": len(items),
            "watched_both": sum(
                1 for i in items if i.get("watched_plex") and i.get("watched_trakt")
            ),
            "watched_plex_only": sum(
                1 for i in items if i.get("watched_plex") and not i.get("watched_trakt")
            ),
            "watched_trakt_only": sum(
                1 for i in items if not i.get("watched_plex") and i.get("watched_trakt")
            ),
            "unwatched_both": sum(
                1 for i in items if not i.get("watched_plex") and not i.get("watched_trakt")
            ),
        }

    def clear_all(self):
        """Clear all synced items and reset state."""
        self.state = self._create_empty_state()
        self._build_index()
        self._removed_keys.clear()
        # An explicit clear replaces the file: merging would resurrect every
        # entry it just dropped.
        self._replace_on_save = True
        self.save_state()
        logger.info("Cleared all watch sync state")

    def backup_state(self, backup_path=None):
        """Create a backup of current state.

        Args:
            backup_path: Path for backup file (default: .traktor_watch_sync.json.backup)

        Returns:
            Path to backup file
        """
        if backup_path is None:
            backup_path = self.sync_file.with_suffix(".json.backup")

        if write_json_atomic(backup_path, self.state):
            logger.info(f"Created watch sync state backup: {backup_path}")
            return backup_path
        logger.error(f"Failed to create backup: {backup_path}")
        return None

    def restore_from_backup(self, backup_path=None):
        """Restore state from a backup file.

        Args:
            backup_path: Path to backup file (default: .traktor_watch_sync.json.backup)

        Returns:
            True if restore successful, False otherwise
        """
        if backup_path is None:
            backup_path = self.sync_file.with_suffix(".json.backup")

        if not backup_path.exists():
            logger.warning(f"Backup file not found: {backup_path}")
            return False

        try:
            with open(backup_path, "r") as f:
                state = json.load(f)

            if not self._validate_state(state):
                logger.error("Invalid backup state format")
                return False

            self.state = state
            self._build_index()
            self._removed_keys.clear()
            self._replace_on_save = True
            self.save_state()
            logger.info(f"Restored watch sync state from backup: {backup_path}")
            return True

        except (json.JSONDecodeError, FileNotFoundError, PermissionError, OSError) as e:
            logger.error(f"Failed to restore from backup: {e}")
            return False
