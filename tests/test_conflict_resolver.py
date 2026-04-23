"""Tests for conflict_resolver module."""

from datetime import datetime, timedelta

import pytest

from traktor import conflict_resolver


class TestConflictResolver:
    """Tests for ConflictResolver class."""

    def test_init_valid_strategy(self):
        """Test initialization with valid strategies."""
        for strategy in ["newest_wins", "plex_wins", "trakt_wins"]:
            resolver = conflict_resolver.ConflictResolver(strategy)
            assert resolver.get_strategy() == strategy

    def test_init_invalid_strategy(self):
        """Test initialization with invalid strategy raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            conflict_resolver.ConflictResolver("invalid_strategy")
        assert "Invalid strategy" in str(exc_info.value)

    def test_set_strategy_valid(self):
        """Test setting a valid strategy."""
        resolver = conflict_resolver.ConflictResolver("newest_wins")
        resolver.set_strategy("plex_wins")
        assert resolver.get_strategy() == "plex_wins"

    def test_set_strategy_invalid(self):
        """Test setting invalid strategy raises ValueError."""
        resolver = conflict_resolver.ConflictResolver("newest_wins")
        with pytest.raises(ValueError):
            resolver.set_strategy("invalid")

    def test_get_valid_strategies(self):
        """Test getting list of valid strategies."""
        strategies = conflict_resolver.ConflictResolver.get_valid_strategies()
        assert "newest_wins" in strategies
        assert "plex_wins" in strategies
        assert "trakt_wins" in strategies


class TestNewestWinsStrategy:
    """Tests for newest_wins conflict resolution strategy."""

    @pytest.fixture
    def resolver(self):
        return conflict_resolver.ConflictResolver("newest_wins")

    def test_both_watched_same_state_no_action(self, resolver):
        """Test no action when both have same watched state."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=True,
        )
        assert action == "no_action"

    def test_both_unwatched_no_action(self, resolver):
        """Test no action when both unwatched."""
        action = resolver.resolve(
            plex_watched=False,
            trakt_watched=False,
        )
        assert action == "no_action"

    def test_plex_watched_plex_newer_push_to_trakt(self, resolver):
        """Test pushing to Trakt when Plex watched and newer."""
        now = datetime.now()
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=now,
            trakt_last_watched=now - timedelta(hours=1),
        )
        assert action == "push_to_trakt"

    def test_trakt_watched_trakt_newer_push_to_plex(self, resolver):
        """Test pushing to Plex when Trakt watched and newer."""
        now = datetime.now()
        action = resolver.resolve(
            plex_watched=False,
            trakt_watched=True,
            plex_last_watched=now - timedelta(hours=1),
            trakt_last_watched=now,
        )
        assert action == "push_to_plex"

    def test_plex_watched_no_trakt_timestamp_push_to_trakt(self, resolver):
        """Test pushing to Trakt when Plex watched and no Trakt timestamp."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=datetime.now(),
            trakt_last_watched=None,
        )
        assert action == "push_to_trakt"

    def test_trakt_watched_no_plex_timestamp_push_to_plex(self, resolver):
        """Test pushing to Plex when Trakt watched and no Plex timestamp."""
        action = resolver.resolve(
            plex_watched=False,
            trakt_watched=True,
            plex_last_watched=None,
            trakt_last_watched=datetime.now(),
        )
        assert action == "push_to_plex"

    def test_no_timestamps_plex_watched_push_to_trakt(self, resolver):
        """Test pushing to Trakt when Plex watched but no timestamps."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=None,
            trakt_last_watched=None,
        )
        assert action == "push_to_trakt"

    def test_no_timestamps_trakt_watched_push_to_plex(self, resolver):
        """Test pushing to Plex when Trakt watched but no timestamps."""
        action = resolver.resolve(
            plex_watched=False,
            trakt_watched=True,
            plex_last_watched=None,
            trakt_last_watched=None,
        )
        assert action == "push_to_plex"

    def test_same_timestamps_no_action(self, resolver):
        """Test no action when timestamps are identical."""
        now = datetime.now()
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=True,
            plex_last_watched=now,
            trakt_last_watched=now,
        )
        assert action == "no_action"


class TestPlexWinsStrategy:
    """Tests for plex_wins conflict resolution strategy."""

    @pytest.fixture
    def resolver(self):
        return conflict_resolver.ConflictResolver("plex_wins")

    def test_plex_watched_trakt_not_push_to_trakt(self, resolver):
        """Test pushing to Trakt when Plex watched."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
        )
        assert action == "push_to_trakt"

    def test_plex_unwatched_trakt_watched_push_to_trakt(self, resolver):
        """Test pushing to Trakt when Plex unwatched."""
        action = resolver.resolve(
            plex_watched=False,
            trakt_watched=True,
        )
        assert action == "push_to_trakt"

    def test_same_state_no_action(self, resolver):
        """Test no action when states match."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=True,
        )
        assert action == "no_action"


class TestTraktWinsStrategy:
    """Tests for trakt_wins conflict resolution strategy."""

    @pytest.fixture
    def resolver(self):
        return conflict_resolver.ConflictResolver("trakt_wins")

    def test_trakt_watched_plex_not_push_to_plex(self, resolver):
        """Test pushing to Plex when Trakt watched."""
        action = resolver.resolve(
            plex_watched=False,
            trakt_watched=True,
        )
        assert action == "push_to_plex"

    def test_trakt_unwatched_plex_watched_push_to_plex(self, resolver):
        """Test pushing to Plex when Trakt unwatched."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
        )
        assert action == "push_to_plex"

    def test_same_state_no_action(self, resolver):
        """Test no action when states match."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=True,
        )
        assert action == "no_action"
