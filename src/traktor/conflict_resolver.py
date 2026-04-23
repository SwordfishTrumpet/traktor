"""Conflict resolution strategies for watch status sync."""

from datetime import datetime
from typing import Optional

from .log import logger

# Default threshold in seconds for considering timestamps significantly different
DEFAULT_TIMESTAMP_THRESHOLD_SECONDS = 60


class ConflictResolver:
    """Resolves conflicts between Plex and Trakt watch states.

    Supports multiple resolution strategies:
    - newest_wins: Use the most recent watch timestamp
    - plex_wins: Always prefer Plex state
    - trakt_wins: Always prefer Trakt state
    """

    VALID_STRATEGIES = ["newest_wins", "plex_wins", "trakt_wins"]

    def __init__(self, strategy="newest_wins"):
        """Initialize conflict resolver.

        Args:
            strategy: Conflict resolution strategy name

        Raises:
            ValueError: If strategy is not valid
        """
        if strategy not in self.VALID_STRATEGIES:
            raise ValueError(
                f"Invalid strategy: {strategy}. Must be one of {self.VALID_STRATEGIES}"
            )

        self.strategy = strategy
        logger.info(f"Conflict resolver initialized with strategy: {strategy}")

    def resolve(
        self,
        plex_watched: bool,
        trakt_watched: bool,
        plex_last_watched: Optional[datetime] = None,
        trakt_last_watched: Optional[datetime] = None,
    ) -> str:
        """Resolve conflict between Plex and Trakt watch states.

        Args:
            plex_watched: Whether item is watched in Plex
            trakt_watched: Whether item is watched in Trakt
            plex_last_watched: When item was last watched in Plex (or None)
            trakt_last_watched: When item was last watched in Trakt (or None)

        Returns:
            Action to take: 'push_to_trakt', 'push_to_plex', or 'no_action'
        """
        # If states match, no action needed
        if plex_watched == trakt_watched:
            # But we might want to update timestamps
            if plex_watched and trakt_watched:
                # Both watched - check if timestamps differ significantly
                if self._should_update_timestamp(plex_last_watched, trakt_last_watched):
                    # Update the older one to match the newer timestamp
                    if self._is_newer(plex_last_watched, trakt_last_watched):
                        return "push_to_plex"  # Update Plex timestamp
                    else:
                        return "push_to_trakt"  # Update Trakt timestamp
            return "no_action"

        # Apply resolution strategy
        if self.strategy == "newest_wins":
            return self._resolve_newest_wins(
                plex_watched, trakt_watched, plex_last_watched, trakt_last_watched
            )
        elif self.strategy == "plex_wins":
            return self._resolve_plex_wins(plex_watched, trakt_watched)
        elif self.strategy == "trakt_wins":
            return self._resolve_trakt_wins(plex_watched, trakt_watched)

        # Fallback (should never reach here)
        logger.error(f"Unknown strategy: {self.strategy}")
        return "no_action"

    def _resolve_newest_wins(
        self,
        plex_watched: bool,
        trakt_watched: bool,
        plex_last_watched: Optional[datetime],
        trakt_last_watched: Optional[datetime],
    ) -> str:
        """Resolve using newest timestamp strategy.

        If Plex was watched more recently than Trakt, push Plex state to Trakt.
        If Trakt was watched more recently than Plex, push Trakt state to Plex.
        If no timestamps available, default to unwatched -> watched transitions.
        """
        # Handle missing timestamps
        if plex_last_watched is None and trakt_last_watched is None:
            # No timestamps - prefer unwatched -> watched transitions
            if plex_watched and not trakt_watched:
                return "push_to_trakt"
            elif trakt_watched and not plex_watched:
                return "push_to_plex"
            return "no_action"

        if plex_last_watched is None:
            # Only Trakt has timestamp - trust Trakt
            if trakt_watched != plex_watched:
                return "push_to_plex"
            return "no_action"

        if trakt_last_watched is None:
            # Only Plex has timestamp - trust Plex
            if plex_watched != trakt_watched:
                return "push_to_trakt"
            return "no_action"

        # Compare timestamps
        if plex_last_watched > trakt_last_watched:
            # Plex is newer
            if plex_watched != trakt_watched:
                logger.debug(
                    f"Plex is newer ({plex_last_watched} > {trakt_last_watched}) - pushing to Trakt"
                )
                return "push_to_trakt"
        else:
            # Trakt is newer or same time
            if trakt_watched != plex_watched:
                logger.debug(
                    f"Trakt is newer ({trakt_last_watched} > {plex_last_watched}) - pushing to Plex"
                )
                return "push_to_plex"

        return "no_action"

    def _resolve_plex_wins(self, plex_watched: bool, trakt_watched: bool) -> str:
        """Resolve using Plex always wins strategy."""
        if plex_watched != trakt_watched:
            return "push_to_trakt"
        return "no_action"

    def _resolve_trakt_wins(self, plex_watched: bool, trakt_watched: bool) -> str:
        """Resolve using Trakt always wins strategy."""
        if trakt_watched != plex_watched:
            return "push_to_plex"
        return "no_action"

    def _is_newer(self, dt1: Optional[datetime], dt2: Optional[datetime]) -> bool:
        """Check if dt1 is newer than dt2.

        Returns True if dt1 is newer, False if dt2 is newer or equal.
        Handles None values by treating None as "old".
        """
        if dt1 is None:
            return False
        if dt2 is None:
            return True
        return dt1 > dt2

    def _should_update_timestamp(
        self,
        plex_last_watched: Optional[datetime],
        trakt_last_watched: Optional[datetime],
        threshold_seconds: int = DEFAULT_TIMESTAMP_THRESHOLD_SECONDS,
    ) -> bool:
        """Determine if timestamps differ enough to warrant an update.

        Args:
            plex_last_watched: Plex timestamp
            trakt_last_watched: Trakt timestamp
            threshold_seconds: Minimum difference in seconds to trigger update

        Returns:
            True if timestamps differ by more than threshold
        """
        if plex_last_watched is None or trakt_last_watched is None:
            return False

        diff = abs((plex_last_watched - trakt_last_watched).total_seconds())
        return diff > threshold_seconds

    def set_strategy(self, strategy: str):
        """Change the resolution strategy.

        Args:
            strategy: New strategy name

        Raises:
            ValueError: If strategy is not valid
        """
        if strategy not in self.VALID_STRATEGIES:
            raise ValueError(
                f"Invalid strategy: {strategy}. Must be one of {self.VALID_STRATEGIES}"
            )

        self.strategy = strategy
        logger.info(f"Conflict resolver strategy changed to: {strategy}")

    def get_strategy(self) -> str:
        """Get current resolution strategy.

        Returns:
            Current strategy name
        """
        return self.strategy

    @staticmethod
    def get_valid_strategies() -> list:
        """Get list of valid strategy names.

        Returns:
            List of valid strategy names
        """
        return ConflictResolver.VALID_STRATEGIES.copy()
