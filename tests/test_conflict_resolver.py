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


class TestNewestWinsEdgeCases:
    """Edge case tests for newest_wins strategy."""

    @pytest.fixture
    def resolver(self):
        return conflict_resolver.ConflictResolver("newest_wins")

    def test_timezone_aware_comparison(self, resolver):
        """Test that datetime comparison handles naive timestamps consistently."""
        from datetime import timedelta

        now = datetime.now()
        one_hour_ago = now - timedelta(hours=1)

        # Plex has the newer timestamp
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=now,
            trakt_last_watched=one_hour_ago,
        )
        assert action == "push_to_trakt"

    def test_none_timestamps_with_different_states(self, resolver):
        """Test resolution when both timestamps are None and states differ."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=None,
            trakt_last_watched=None,
        )
        assert action == "push_to_trakt"

        action = resolver.resolve(
            plex_watched=False,
            trakt_watched=True,
            plex_last_watched=None,
            trakt_last_watched=None,
        )
        assert action == "push_to_plex"

    def test_none_watched_status_plex(self, resolver):
        """Test when Plex watched status is None (malformed data)."""
        action = resolver.resolve(
            plex_watched=None,
            trakt_watched=False,
            plex_last_watched=None,
            trakt_last_watched=None,
        )
        assert action == "no_action"

    def test_none_watched_status_trakt(self, resolver):
        """Test when Trakt watched status is None (malformed data)."""
        # When trakt watched is None and plex is True, newest_wins sees
        # None != True, and with both timestamps None, prefers
        # unwatched->watched transition (plex_watched and not trakt_watched)
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=None,
            plex_last_watched=None,
            trakt_last_watched=None,
        )
        assert action == "push_to_trakt"

    def test_both_watched_none_timestamps_no_action(self, resolver):
        """Test that both watched with None timestamps returns no_action."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=True,
            plex_last_watched=None,
            trakt_last_watched=None,
        )
        assert action == "no_action"

    def test_far_future_timestamps(self, resolver):
        """Test handling of unreasonably far future timestamps."""
        future = datetime(2099, 1, 1)
        past = datetime(2020, 1, 1)

        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=future,
            trakt_last_watched=past,
        )
        assert action == "push_to_trakt"

    def test_very_old_timestamps(self, resolver):
        """Test handling of very old timestamps."""
        ancient = datetime(1970, 1, 1)
        now = datetime.now()

        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=ancient,
            trakt_last_watched=now,
        )
        assert action == "push_to_plex"

    def test_identical_timestamps_different_states(self, resolver):
        """Test when timestamps are identical but states differ."""
        now = datetime.now()

        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=now,
            trakt_last_watched=now,
        )
        assert action in ("push_to_trakt", "push_to_plex", "no_action")


class TestPlexWinsEdgeCases:
    """Edge case tests for plex_wins strategy."""

    @pytest.fixture
    def resolver(self):
        return conflict_resolver.ConflictResolver("plex_wins")

    def test_both_unwatched_no_action(self, resolver):
        """Test no action when both unwatched (plex_wins)."""
        action = resolver.resolve(
            plex_watched=False,
            trakt_watched=False,
        )
        assert action == "no_action"

    def test_both_watched_no_action(self, resolver):
        """Test no action when both watched (plex_wins)."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=True,
        )
        assert action == "no_action"

    def test_plex_wins_ignores_timestamps(self, resolver):
        """Test that plex_wins ignores timestamps."""
        now = datetime.now()
        ancient = datetime(1970, 1, 1)

        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=ancient,
            trakt_last_watched=now,
        )
        assert action == "push_to_trakt"


class TestTraktWinsEdgeCases:
    """Edge case tests for trakt_wins strategy."""

    @pytest.fixture
    def resolver(self):
        return conflict_resolver.ConflictResolver("trakt_wins")

    def test_both_unwatched_no_action(self, resolver):
        """Test no action when both unwatched (trakt_wins)."""
        action = resolver.resolve(
            plex_watched=False,
            trakt_watched=False,
        )
        assert action == "no_action"

    def test_both_watched_no_action(self, resolver):
        """Test no action when both watched (trakt_wins)."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=True,
        )
        assert action == "no_action"

    def test_trakt_wins_ignores_timestamps(self, resolver):
        """Test that trakt_wins ignores timestamps."""
        now = datetime.now()
        ancient = datetime(1970, 1, 1)

        action = resolver.resolve(
            plex_watched=False,
            trakt_watched=True,
            plex_last_watched=now,
            trakt_last_watched=ancient,
        )
        assert action == "push_to_plex"


class TestTimestampHelperMethods:
    """Tests for timestamp helper methods."""

    @pytest.fixture
    def resolver(self):
        return conflict_resolver.ConflictResolver("newest_wins")

    def test_is_newer_dt1_newer(self, resolver):
        """Test _is_newer when dt1 is newer."""
        dt1 = datetime(2024, 6, 15, 12, 0, 0)
        dt2 = datetime(2024, 6, 15, 11, 0, 0)
        assert resolver._is_newer(dt1, dt2) is True

    def test_is_newer_dt2_newer(self, resolver):
        """Test _is_newer when dt2 is newer."""
        dt1 = datetime(2024, 6, 15, 11, 0, 0)
        dt2 = datetime(2024, 6, 15, 12, 0, 0)
        assert resolver._is_newer(dt1, dt2) is False

    def test_is_newer_equal_timestamps(self, resolver):
        """Test _is_newer when timestamps are equal."""
        dt = datetime(2024, 6, 15, 12, 0, 0)
        assert resolver._is_newer(dt, dt) is False

    def test_is_newer_dt1_none(self, resolver):
        """Test _is_newer when dt1 is None."""
        dt2 = datetime(2024, 6, 15, 12, 0, 0)
        assert resolver._is_newer(None, dt2) is False

    def test_is_newer_dt2_none(self, resolver):
        """Test _is_newer when dt2 is None."""
        dt1 = datetime(2024, 6, 15, 12, 0, 0)
        assert resolver._is_newer(dt1, None) is True

    def test_is_newer_both_none(self, resolver):
        """Test _is_newer when both are None."""
        assert resolver._is_newer(None, None) is False

    def test_should_update_timestamp_significant_diff(self, resolver):
        """Test _should_update_timestamp with significant difference."""
        dt1 = datetime(2024, 6, 15, 12, 0, 0)
        dt2 = datetime(2024, 6, 15, 12, 5, 0)
        assert resolver._should_update_timestamp(dt1, dt2) is True

    def test_should_update_timestamp_small_diff(self, resolver):
        """Test _should_update_timestamp with small difference."""
        dt1 = datetime(2024, 6, 15, 12, 0, 0)
        dt2 = datetime(2024, 6, 15, 12, 0, 30)
        assert resolver._should_update_timestamp(dt1, dt2) is False

    def test_should_update_timestamp_custom_threshold(self, resolver):
        """Test _should_update_timestamp with custom threshold."""
        dt1 = datetime(2024, 6, 15, 12, 0, 0)
        dt2 = datetime(2024, 6, 15, 12, 0, 30)
        assert resolver._should_update_timestamp(dt1, dt2, threshold_seconds=10) is True

    def test_should_update_timestamp_none_values(self, resolver):
        """Test _should_update_timestamp with None values."""
        dt = datetime(2024, 6, 15, 12, 0, 0)
        assert resolver._should_update_timestamp(None, dt) is False
        assert resolver._should_update_timestamp(dt, None) is False
        assert resolver._should_update_timestamp(None, None) is False
