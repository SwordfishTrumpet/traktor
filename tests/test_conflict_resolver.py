"""Tests for conflict_resolver module."""

from datetime import datetime, timedelta, timezone

import pytest

from traktor import conflict_resolver
from traktor.conflict_resolver import (
    COMPLETION_HIGH_BONUS,
    COMPLETION_LOW_PENALTY,
    MANUAL_MARK_BONUS,
)


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


class TestResolveWithContext:
    """Tests for resolve_with_context with media type, confidence, and timezone."""

    @pytest.fixture
    def resolver(self):
        return conflict_resolver.ConflictResolver("newest_wins")

    def test_resolve_with_context_movie(self, resolver):
        """Test movie resolution with timestamps."""
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        action = resolver.resolve_with_context(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=now,
            trakt_last_watched=now - timedelta(hours=1),
            media_type="movie",
        )
        assert action == "push_to_trakt"

    def test_resolve_with_context_episode_completion_weighted(self, resolver):
        """Test episode resolution weights completion heavily."""
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        # Plex has newer timestamp but Trakt has higher completion
        action = resolver.resolve_with_context(
            plex_watched=False,
            trakt_watched=True,
            plex_last_watched=now,
            trakt_last_watched=now - timedelta(hours=1),
            media_type="episode",
            trakt_completion_pct=0.95,
            plex_completion_pct=0.05,
        )
        # Trakt should win due to high completion
        assert action == "push_to_plex"

    def test_resolve_with_context_show_aggregate(self, resolver):
        """Test show resolution with aggregate completion."""
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        action = resolver.resolve_with_context(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=now,
            trakt_last_watched=now - timedelta(hours=1),
            media_type="show",
            plex_completion_pct=0.8,
            trakt_completion_pct=0.2,
        )
        assert action == "push_to_trakt"

    def test_resolve_with_context_season_bulk(self, resolver):
        """Test season resolution with bulk completion > 0.5."""
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        # Plex has 60% completion but marked unwatched - effective state is watched
        action = resolver.resolve_with_context(
            plex_watched=False,
            trakt_watched=True,
            plex_last_watched=now,
            trakt_last_watched=now - timedelta(hours=1),
            media_type="season",
            plex_completion_pct=0.6,
            trakt_completion_pct=0.3,
        )
        # Both effective watched=True, Plex is newer -> timestamp update pushes to Plex
        assert action == "push_to_plex"

    def test_resolve_with_context_confidence_play_count(self, resolver):
        """Test confidence scoring with play count."""
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        action = resolver.resolve_with_context(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=now,
            trakt_last_watched=now,
            media_type="movie",
            plex_play_count=5,
            trakt_play_count=0,
        )
        # Plex has higher play count, should push to Trakt
        assert action == "push_to_trakt"

    def test_resolve_with_context_confidence_manual(self, resolver):
        """Test confidence scoring with manual mark."""
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        action = resolver.resolve_with_context(
            plex_watched=False,
            trakt_watched=True,
            plex_last_watched=now,
            trakt_last_watched=now,
            media_type="movie",
            trakt_manual=True,
            plex_manual=False,
        )
        # Trakt has manual mark, should push to Plex
        assert action == "push_to_plex"

    def test_resolve_with_context_no_action_same_state(self, resolver):
        """Test no action when states match."""
        action = resolver.resolve_with_context(
            plex_watched=True,
            trakt_watched=True,
            media_type="movie",
        )
        assert action == "no_action"

    def test_resolve_with_context_unknown_media_type(self, resolver):
        """Test fallback to basic resolve for unknown media type."""
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        action = resolver.resolve_with_context(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=now,
            trakt_last_watched=now - timedelta(hours=1),
            media_type="unknown",
        )
        assert action == "push_to_trakt"


class TestTimezoneAwareComparison:
    """Tests for timezone-aware timestamp comparison."""

    @pytest.fixture
    def resolver(self):
        return conflict_resolver.ConflictResolver("newest_wins")

    def test_normalize_timestamp_aware_to_utc(self, resolver):
        """Test that aware timestamps are converted to UTC."""
        est = timezone(timedelta(hours=-5))
        dt_est = datetime(2024, 6, 15, 12, 0, 0, tzinfo=est)
        dt_utc = resolver._normalize_timestamp(dt_est)
        assert dt_utc.tzinfo == timezone.utc
        assert dt_utc.hour == 17  # 12:00 EST -> 17:00 UTC

    def test_normalize_timestamp_naive_assumes_utc(self, resolver):
        """Test that naive timestamps are assumed UTC."""
        dt_naive = datetime(2024, 6, 15, 12, 0, 0)
        dt_utc = resolver._normalize_timestamp(dt_naive)
        assert dt_utc.tzinfo == timezone.utc
        assert dt_utc.hour == 12

    def test_normalize_timestamp_with_zoneinfo(self, resolver):
        """Test normalization with zoneinfo timezone name."""
        dt_naive = datetime(2024, 6, 15, 12, 0, 0)
        dt_utc = resolver._normalize_timestamp(dt_naive, tz_name="America/New_York")
        assert dt_utc.tzinfo == timezone.utc
        # 12:00 EDT -> 16:00 UTC (EDT is UTC-4 in June)
        assert dt_utc.hour == 16

    def test_normalize_timestamp_none(self, resolver):
        """Test normalization with None."""
        assert resolver._normalize_timestamp(None) is None

    def test_normalize_timestamp_invalid_tz_falls_back(self, resolver):
        """Test fallback to UTC when timezone name is invalid."""
        dt_naive = datetime(2024, 6, 15, 12, 0, 0)
        dt_utc = resolver._normalize_timestamp(dt_naive, tz_name="Invalid/Zone")
        assert dt_utc.tzinfo == timezone.utc

    def test_resolve_with_context_different_timezones(self, resolver):
        """Test resolution with timestamps from different timezones."""
        # Plex: 14:00 UTC, Trakt: 13:00 UTC (but reported as 15:00 Europe/Berlin = 13:00 UTC)
        plex_ts = datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        trakt_naive = datetime(2024, 6, 15, 15, 0, 0)
        action = resolver.resolve_with_context(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=plex_ts,
            trakt_last_watched=trakt_naive,
            media_type="movie",
            trakt_tz="Europe/Berlin",
        )
        # After normalization: trakt is 13:00 UTC, plex is 14:00 UTC -> plex is newer
        assert action == "push_to_trakt"


class TestConfidenceScoring:
    """Tests for confidence scoring system."""

    @pytest.fixture
    def resolver(self):
        return conflict_resolver.ConflictResolver("newest_wins")

    def test_calculate_confidence_score_play_count(self, resolver):
        """Test play count bonus."""
        score = resolver._calculate_confidence_score(play_count=3, completion_pct=None, manual=None)
        assert score == 0.3  # 3 * 0.1, capped at 0.3

    def test_calculate_confidence_score_play_count_max(self, resolver):
        """Test play count bonus is capped."""
        score = resolver._calculate_confidence_score(
            play_count=10, completion_pct=None, manual=None
        )
        assert score == 0.3  # capped at MAX_PLAY_COUNT_BONUS

    def test_calculate_confidence_score_completion_high(self, resolver):
        """Test high completion bonus."""
        score = resolver._calculate_confidence_score(play_count=0, completion_pct=0.95, manual=None)
        assert score == COMPLETION_HIGH_BONUS

    def test_calculate_confidence_score_completion_low(self, resolver):
        """Test low completion penalty."""
        score = resolver._calculate_confidence_score(play_count=0, completion_pct=0.05, manual=None)
        assert score == COMPLETION_LOW_PENALTY

    def test_calculate_confidence_score_manual(self, resolver):
        """Test manual mark bonus."""
        score = resolver._calculate_confidence_score(play_count=0, completion_pct=None, manual=True)
        assert score == MANUAL_MARK_BONUS

    def test_calculate_confidence_score_combined(self, resolver):
        """Test combined confidence scoring."""
        score = resolver._calculate_confidence_score(play_count=2, completion_pct=0.95, manual=True)
        expected = 0.2 + COMPLETION_HIGH_BONUS + MANUAL_MARK_BONUS
        assert score == expected

    def test_calculate_confidence_score_none_values(self, resolver):
        """Test confidence scoring with None values."""
        score = resolver._calculate_confidence_score(
            play_count=None, completion_pct=None, manual=None
        )
        assert score == 0.0

    def test_calculate_confidence_score_zero_play_count(self, resolver):
        """Test zero play count gives no bonus."""
        score = resolver._calculate_confidence_score(play_count=0, completion_pct=None, manual=None)
        assert score == 0.0

    def test_backward_compatibility_basic_resolve(self, resolver):
        """Test that basic resolve() still works without new parameters."""
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=False,
            plex_last_watched=now,
            trakt_last_watched=now - timedelta(hours=1),
        )
        assert action == "push_to_trakt"

    def test_backward_compatibility_same_state(self, resolver):
        """Test backward compatibility with same state."""
        action = resolver.resolve(
            plex_watched=True,
            trakt_watched=True,
        )
        assert action == "no_action"
