"""Watch history tracking and sync state management."""

import json
from datetime import datetime, timezone

from .log import logger
from .settings import DATA_DIR

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
        """Save current sync state to disk."""
        try:
            self.sync_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.sync_file, "w") as f:
                json.dump(self.state, f, indent=2)
            logger.debug(f"Saved watch sync state to {self.sync_file}")
        except (PermissionError, FileNotFoundError, OSError) as e:
            logger.error(f"Failed to save watch sync state: {e}")

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

        Args:
            imdb_id: IMDb ID to search for
            tmdb_id: TMDb ID to search for
            trakt_id: Trakt ID to search for
            plex_rating_key: Plex rating key to search for

        Returns:
            Synced item dict or None if not found
        """
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
        # Find existing item
        existing = self.get_synced_item(
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            trakt_id=trakt_id,
            plex_rating_key=plex_rating_key,
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

        logger.debug(f"Updated synced item: {item.get('imdb_id') or item.get('tmdb_id')}")
        self.save_state()

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
                logger.debug(f"Removed synced item: {item.get('imdb_id') or item.get('tmdb_id')}")
                self.save_state()
                return True

        return False

    def get_all_synced_items(self):
        """Get all synced items.

        Returns:
            List of synced item dicts
        """
        return self.state["synced_items"].copy()

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

        try:
            with open(backup_path, "w") as f:
                json.dump(self.state, f, indent=2)
            logger.info(f"Created watch sync state backup: {backup_path}")
            return backup_path
        except (PermissionError, FileNotFoundError, OSError) as e:
            logger.error(f"Failed to create backup: {e}")
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
            self.save_state()
            logger.info(f"Restored watch sync state from backup: {backup_path}")
            return True

        except (json.JSONDecodeError, FileNotFoundError, PermissionError, OSError) as e:
            logger.error(f"Failed to restore from backup: {e}")
            return False
