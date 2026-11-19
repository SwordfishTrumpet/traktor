"""Conflict resolution strategies for watch status sync."""

from __future__ import annotations

from datetime import datetime

from .log import logger
from .utils import parse_timestamp

# Confidence scoring constants
MAX_PLAY_COUNT_BONUS = 0.3
PLAY_COUNT_BONUS_PER_DIFF = 0.1
COMPLETION_HIGH_THRESHOLD = 0.9
COMPLETION_LOW_THRESHOLD = 0.1
COMPLETION_HIGH_BONUS = 0.1
COMPLETION_LOW_PENALTY = -0.1
MANUAL_MARK_BONUS = 0.2

# Media type constants
MEDIA_TYPE_MOVIE = "movie"
MEDIA_TYPE_EPISODE = "episode"
MEDIA_TYPE_SHOW = "show"
MEDIA_TYPE_SEASON = "season"


class ConflictResolver:
    """Resolves conflicts between Plex and Trakt watch states.

    Supports multiple resolution strategies:
    - newest_wins: Use the most recent watch timestamp
    - plex_wins: Always prefer Plex state
    - trakt_wins: Always prefer Trakt state

    Advanced features:
    - Per-media-type rules: Different handling for movies vs episodes vs shows
    - Timezone-aware comparison: Converts all timestamps to UTC
    - Confidence scoring: Weights decisions by play count, completion, manual marks
    """

    VALID_STRATEGIES = ("newest_wins", "plex_wins", "trakt_wins")

    def __init__(self, strategy: str = "newest_wins") -> None:
        """Initialize conflict resolver.

        Args:
            strategy: Conflict resolution strategy name

        Raises:
            ValueError: If strategy is not valid
        """
        if strategy not in self.VALID_STRATEGIES:
            raise ValueError(
                f"Invalid strategy: {strategy}. Must be one of {self.VALID_STRATEGIES}",
            )

        self.strategy = strategy
        logger.info(f"Conflict resolver initialized with strategy: {strategy}")

    def resolve(
        self,
        plex_watched: bool,
        trakt_watched: bool,
        plex_last_watched: datetime | str | int | float | None = None,
        trakt_last_watched: datetime | str | int | float | None = None,
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
        # When both platforms agree on the watched state there is no state to
        # apply, and timestamp divergence is not a change either: Plex cannot
        # be handed a watch date, and re-adding a Trakt play to move its date
        # fabricates a play and overwrites the real one. Pushing either way is
        # also self-defeating - the push restamps the winner with "now", so the
        # difference never closes and every run pushes the same items again
        # (a permanently non-idempotent sync, issue #37).
        if plex_watched == trakt_watched:
            return "no_action"

        # Callers pass the platform's own serialized timestamps: an ISO-8601
        # string from the Plex cache and from the Trakt API response. Normalize
        # them here before the strategies compare them (issue #20).
        plex_ts = self._normalize_timestamp(plex_last_watched)
        trakt_ts = self._normalize_timestamp(trakt_last_watched)

        # Apply resolution strategy
        if self.strategy == "newest_wins":
            return self._resolve_newest_wins(
                plex_watched,
                trakt_watched,
                plex_ts,
                trakt_ts,
            )
        if self.strategy == "plex_wins":
            return self._resolve_plex_wins(plex_watched, trakt_watched)
        if self.strategy == "trakt_wins":
            return self._resolve_trakt_wins(plex_watched, trakt_watched)

        # Fallback (should never reach here)
        logger.error(f"Unknown strategy: {self.strategy}")
        return "no_action"

    def resolve_with_context(
        self,
        plex_watched: bool,
        trakt_watched: bool,
        plex_last_watched: datetime | None = None,
        trakt_last_watched: datetime | None = None,
        media_type: str | None = None,
        plex_play_count: int | None = None,
        trakt_play_count: int | None = None,
        plex_completion_pct: float | None = None,
        trakt_completion_pct: float | None = None,
        plex_manual: bool | None = None,
        trakt_manual: bool | None = None,
        plex_tz: str | None = None,
        trakt_tz: str | None = None,
    ) -> str:
        """Resolve conflict with full context including media type, confidence, and timezone.

        Args:
            plex_watched: Whether item is watched in Plex
            trakt_watched: Whether item is watched in Trakt
            plex_last_watched: When item was last watched in Plex (or None)
            trakt_last_watched: When item was last watched in Trakt (or None)
            media_type: Type of media ("movie", "episode", "show", "season")
            plex_play_count: Number of times watched in Plex
            trakt_play_count: Number of times watched in Trakt
            plex_completion_pct: Completion percentage in Plex (0.0-1.0)
            trakt_completion_pct: Completion percentage in Trakt (0.0-1.0)
            plex_manual: Whether Plex mark was manual
            trakt_manual: Whether Trakt mark was manual
            plex_tz: Timezone name for Plex timestamps
            trakt_tz: Timezone name for Trakt timestamps

        Returns:
            Action to take: 'push_to_trakt', 'push_to_plex', or 'no_action'
        """
        # Agreed states need no action - see resolve() for why timestamp
        # divergence between two watched records is not a change.
        if plex_watched == trakt_watched:
            return "no_action"

        # Apply per-media-type rules
        if media_type == MEDIA_TYPE_MOVIE:
            return self._resolve_movie(
                plex_watched,
                trakt_watched,
                plex_last_watched,
                trakt_last_watched,
                plex_play_count,
                trakt_play_count,
                plex_completion_pct,
                trakt_completion_pct,
                plex_manual,
                trakt_manual,
                plex_tz,
                trakt_tz,
            )
        if media_type == MEDIA_TYPE_EPISODE:
            return self._resolve_episode(
                plex_watched,
                trakt_watched,
                plex_last_watched,
                trakt_last_watched,
                plex_play_count,
                trakt_play_count,
                plex_completion_pct,
                trakt_completion_pct,
                plex_manual,
                trakt_manual,
                plex_tz,
                trakt_tz,
            )
        if media_type == MEDIA_TYPE_SHOW:
            return self._resolve_show(
                plex_watched,
                trakt_watched,
                plex_last_watched,
                trakt_last_watched,
                plex_play_count,
                trakt_play_count,
                plex_completion_pct,
                trakt_completion_pct,
                plex_manual,
                trakt_manual,
                plex_tz,
                trakt_tz,
            )
        if media_type == MEDIA_TYPE_SEASON:
            return self._resolve_season(
                plex_watched,
                trakt_watched,
                plex_last_watched,
                trakt_last_watched,
                plex_play_count,
                trakt_play_count,
                plex_completion_pct,
                trakt_completion_pct,
                plex_manual,
                trakt_manual,
                plex_tz,
                trakt_tz,
            )

        # Fallback to basic resolve for unknown media types
        return self.resolve(
            plex_watched,
            trakt_watched,
            plex_last_watched,
            trakt_last_watched,
        )

    def _resolve_newest_wins(
        self,
        plex_watched: bool,
        trakt_watched: bool,
        plex_last_watched: datetime | None,
        trakt_last_watched: datetime | None,
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
            if trakt_watched and not plex_watched:
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
                    f"Plex is newer ({plex_last_watched} > {trakt_last_watched}) "
                    "- pushing to Trakt",
                )
                return "push_to_trakt"
        else:
            # Trakt is newer or same time
            if trakt_watched != plex_watched:
                logger.debug(
                    f"Trakt is newer ({trakt_last_watched} > {plex_last_watched}) "
                    "- pushing to Plex",
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

    def _resolve_movie(
        self,
        plex_watched: bool,
        trakt_watched: bool,
        plex_last_watched: datetime | None,
        trakt_last_watched: datetime | None,
        plex_play_count: int | None,
        trakt_play_count: int | None,
        plex_completion_pct: float | None,
        trakt_completion_pct: float | None,
        plex_manual: bool | None,
        trakt_manual: bool | None,
        plex_tz: str | None,
        trakt_tz: str | None,
    ) -> str:
        """Resolve movie conflicts with strict timestamp comparison and confidence scoring."""
        plex_ts = self._normalize_timestamp(plex_last_watched, plex_tz)
        trakt_ts = self._normalize_timestamp(trakt_last_watched, trakt_tz)

        # If timestamps are available, use confidence scoring
        if plex_ts is not None or trakt_ts is not None:
            plex_score = self._calculate_confidence_score(
                plex_play_count,
                plex_completion_pct,
                plex_manual,
            )
            trakt_score = self._calculate_confidence_score(
                trakt_play_count,
                trakt_completion_pct,
                trakt_manual,
            )

            # Add timestamp component to score
            if plex_ts is not None and trakt_ts is not None:
                if plex_ts > trakt_ts:
                    plex_score += 0.5
                elif trakt_ts > plex_ts:
                    trakt_score += 0.5
                else:
                    plex_score += 0.25
                    trakt_score += 0.25
            elif plex_ts is not None:
                plex_score += 0.5
            else:
                trakt_score += 0.5

            logger.debug(
                f"Movie confidence scores - Plex: {plex_score:.2f}, Trakt: {trakt_score:.2f}",
            )

            if plex_score > trakt_score:
                if plex_watched != trakt_watched:
                    return "push_to_trakt"
            elif trakt_score > plex_score:
                if trakt_watched != plex_watched:
                    return "push_to_plex"

        # Fallback to basic newest_wins
        return self._resolve_newest_wins(
            plex_watched,
            trakt_watched,
            plex_ts,
            trakt_ts,
        )

    def _resolve_episode(
        self,
        plex_watched: bool,
        trakt_watched: bool,
        plex_last_watched: datetime | None,
        trakt_last_watched: datetime | None,
        plex_play_count: int | None,
        trakt_play_count: int | None,
        plex_completion_pct: float | None,
        trakt_completion_pct: float | None,
        plex_manual: bool | None,
        trakt_manual: bool | None,
        plex_tz: str | None,
        trakt_tz: str | None,
    ) -> str:
        """Resolve episode conflicts with completion-weighted scoring."""
        plex_ts = self._normalize_timestamp(plex_last_watched, plex_tz)
        trakt_ts = self._normalize_timestamp(trakt_last_watched, trakt_tz)

        # For episodes, completion percentage is more important than timestamp
        plex_score = self._calculate_confidence_score(
            plex_play_count,
            plex_completion_pct,
            plex_manual,
        )
        trakt_score = self._calculate_confidence_score(
            trakt_play_count,
            trakt_completion_pct,
            trakt_manual,
        )

        # Weight completion more heavily for episodes
        if plex_completion_pct is not None:
            plex_score += plex_completion_pct * 0.3
        if trakt_completion_pct is not None:
            trakt_score += trakt_completion_pct * 0.3

        # Add timestamp component (less weight than for movies)
        if plex_ts is not None and trakt_ts is not None:
            if plex_ts > trakt_ts:
                plex_score += 0.3
            elif trakt_ts > plex_ts:
                trakt_score += 0.3
            else:
                plex_score += 0.15
                trakt_score += 0.15
        elif plex_ts is not None:
            plex_score += 0.3
        else:
            trakt_score += 0.3

        logger.debug(
            f"Episode confidence scores - Plex: {plex_score:.2f}, Trakt: {trakt_score:.2f}",
        )

        if plex_score > trakt_score:
            if plex_watched != trakt_watched:
                return "push_to_trakt"
        elif trakt_score > plex_score:
            if trakt_watched != plex_watched:
                return "push_to_plex"

        return "no_action"

    def _resolve_show(
        self,
        plex_watched: bool,
        trakt_watched: bool,
        plex_last_watched: datetime | None,
        trakt_last_watched: datetime | None,
        plex_play_count: int | None,
        trakt_play_count: int | None,
        plex_completion_pct: float | None,
        trakt_completion_pct: float | None,
        plex_manual: bool | None,
        trakt_manual: bool | None,
        plex_tz: str | None,
        trakt_tz: str | None,
    ) -> str:
        """Resolve show conflicts with aggregate completion logic."""
        plex_ts = self._normalize_timestamp(plex_last_watched, plex_tz)
        trakt_ts = self._normalize_timestamp(trakt_last_watched, trakt_tz)

        # For shows, use aggregate completion across all episodes
        plex_score = self._calculate_confidence_score(
            plex_play_count,
            plex_completion_pct,
            plex_manual,
        )
        trakt_score = self._calculate_confidence_score(
            trakt_play_count,
            trakt_completion_pct,
            trakt_manual,
        )

        # Weight completion percentage (aggregate across episodes)
        if plex_completion_pct is not None:
            plex_score += plex_completion_pct * 0.4
        if trakt_completion_pct is not None:
            trakt_score += trakt_completion_pct * 0.4

        # Add timestamp component
        if plex_ts is not None and trakt_ts is not None:
            if plex_ts > trakt_ts:
                plex_score += 0.3
            elif trakt_ts > plex_ts:
                trakt_score += 0.3
            else:
                plex_score += 0.15
                trakt_score += 0.15
        elif plex_ts is not None:
            plex_score += 0.3
        else:
            trakt_score += 0.3

        logger.debug(
            f"Show confidence scores - Plex: {plex_score:.2f}, Trakt: {trakt_score:.2f}",
        )

        if plex_score > trakt_score:
            if plex_watched != trakt_watched:
                return "push_to_trakt"
        elif trakt_score > plex_score:
            if trakt_watched != plex_watched:
                return "push_to_plex"

        return "no_action"

    def _resolve_season(
        self,
        plex_watched: bool,
        trakt_watched: bool,
        plex_last_watched: datetime | None,
        trakt_last_watched: datetime | None,
        plex_play_count: int | None,
        trakt_play_count: int | None,
        plex_completion_pct: float | None,
        trakt_completion_pct: float | None,
        plex_manual: bool | None,
        trakt_manual: bool | None,
        plex_tz: str | None,
        trakt_tz: str | None,
    ) -> str:
        """Resolve season conflicts with bulk operation handling.

        If most episodes in a season are watched, mark the entire season.
        """
        plex_ts = self._normalize_timestamp(plex_last_watched, plex_tz)
        trakt_ts = self._normalize_timestamp(trakt_last_watched, trakt_tz)

        # For seasons, bulk operations: if completion > 0.5, consider it watched
        plex_effective_watched = plex_watched
        trakt_effective_watched = trakt_watched

        if plex_completion_pct is not None and plex_completion_pct > 0.5:
            plex_effective_watched = True
        if trakt_completion_pct is not None and trakt_completion_pct > 0.5:
            trakt_effective_watched = True

        if plex_effective_watched == trakt_effective_watched:
            # Agreed states need no action - see resolve().
            return "no_action"

        # Use confidence scoring for the effective states
        plex_score = self._calculate_confidence_score(
            plex_play_count,
            plex_completion_pct,
            plex_manual,
        )
        trakt_score = self._calculate_confidence_score(
            trakt_play_count,
            trakt_completion_pct,
            trakt_manual,
        )

        # Add timestamp component
        if plex_ts is not None and trakt_ts is not None:
            if plex_ts > trakt_ts:
                plex_score += 0.3
            elif trakt_ts > plex_ts:
                trakt_score += 0.3
            else:
                plex_score += 0.15
                trakt_score += 0.15
        elif plex_ts is not None:
            plex_score += 0.3
        else:
            trakt_score += 0.3

        logger.debug(
            f"Season confidence scores - Plex: {plex_score:.2f}, Trakt: {trakt_score:.2f}",
        )

        if plex_score > trakt_score:
            if plex_effective_watched != trakt_effective_watched:
                return "push_to_trakt"
        elif trakt_score > plex_score:
            if trakt_effective_watched != plex_effective_watched:
                return "push_to_plex"

        return "no_action"

    def _calculate_confidence_score(
        self,
        play_count: int | None,
        completion_pct: float | None,
        manual: bool | None,
    ) -> float:
        """Calculate confidence score based on play count, completion, and manual mark.

        Args:
            play_count: Number of times watched
            completion_pct: Completion percentage (0.0-1.0)
            manual: Whether mark was manual

        Returns:
            Confidence score (higher = more confident)
        """
        score = 0.0

        # Play count bonus: +0.1 per play, max 0.3
        if play_count is not None and play_count > 0:
            score += min(play_count * PLAY_COUNT_BONUS_PER_DIFF, MAX_PLAY_COUNT_BONUS)

        # Completion bonus/penalty
        if completion_pct is not None:
            if completion_pct >= COMPLETION_HIGH_THRESHOLD:
                score += COMPLETION_HIGH_BONUS
            elif completion_pct <= COMPLETION_LOW_THRESHOLD:
                score += COMPLETION_LOW_PENALTY

        # Manual mark bonus
        if manual is True:
            score += MANUAL_MARK_BONUS

        return score

    def _normalize_timestamp(
        self,
        dt: datetime | str | int | float | None,
        tz_name: str | None = None,
    ) -> datetime | None:
        """Normalize timestamp to UTC for comparison.

        Thin wrapper over the canonical :func:`utils.parse_timestamp`. Aware
        datetimes are converted to UTC; naive datetimes are interpreted as
        system local time (matching plexapi's ``datetime.fromtimestamp()``
        behavior) or in the named timezone when ``tz_name`` is provided.

        Args:
            dt: Timestamp to normalize
            tz_name: Timezone name (e.g., "America/New_York")

        Returns:
            UTC-aware datetime or None
        """
        return parse_timestamp(dt, tz_name=tz_name)
