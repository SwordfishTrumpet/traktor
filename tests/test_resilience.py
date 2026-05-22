"""Tests for resilience patterns."""

import json
import time
from unittest.mock import patch

import pytest

from traktor.resilience import (
    CircuitBreaker,
    CircuitBreakerOpen,
    SyncProgress,
    retry_with_backoff,
)


class TestRetryWithBackoff:
    """Tests for retry_with_backoff decorator."""

    def test_success_no_retry(self, tmp_path):
        """Successful call should not retry."""
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.01, name="test")
        def succeed():
            nonlocal call_count
            call_count += 1
            return "success"

        result = succeed()
        assert result == "success"
        assert call_count == 1

    def test_retry_then_success(self, tmp_path):
        """Retry on failure, then succeed."""
        call_count = 0

        @retry_with_backoff(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(RuntimeError,),
            name="test",
        )
        def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("fail")
            return "success"

        result = fail_then_succeed()
        assert result == "success"
        assert call_count == 2

    def test_max_retries_exceeded(self, tmp_path):
        """Should raise after max retries exceeded."""
        call_count = 0

        @retry_with_backoff(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(RuntimeError,),
            name="test",
        )
        def always_fail():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError, match="fail"):
            always_fail()

        assert call_count == 3  # initial + 2 retries

    def test_non_retryable_exception(self, tmp_path):
        """Should not retry non-retryable exceptions."""
        call_count = 0

        @retry_with_backoff(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(RuntimeError,),
            name="test",
        )
        def raise_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            raise_value_error()

        assert call_count == 1

    def test_retry_on_status_code(self, tmp_path):
        """Should retry on matching status code in error message."""
        call_count = 0

        @retry_with_backoff(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(RuntimeError,),
            retryable_status_codes=(503,),
            name="test",
        )
        def fail_with_503():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("(503) service_unavailable")
            return "success"

        result = fail_with_503()
        assert result == "success"
        assert call_count == 2

    def test_no_retry_on_non_matching_status_code(self, tmp_path):
        """Should not retry if status code doesn't match."""
        call_count = 0

        @retry_with_backoff(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(RuntimeError,),
            retryable_status_codes=(500, 502),
            name="test",
        )
        def fail_with_400():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("(400) bad_request")

        with pytest.raises(RuntimeError, match="400"):
            fail_with_400()

        assert call_count == 1

    def test_retry_on_connection_error(self, tmp_path):
        """Should always retry on ConnectionError and TimeoutError."""
        call_count = 0

        @retry_with_backoff(
            max_retries=1,
            base_delay=0.01,
            retryable_exceptions=(ConnectionError,),
            name="test",
        )
        def fail_connection():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("connection refused")
            return "success"

        result = fail_connection()
        assert result == "success"
        assert call_count == 2

    def test_exponential_backoff_delay(self, tmp_path):
        """Delay should increase exponentially."""
        delays = []

        @retry_with_backoff(
            max_retries=3,
            base_delay=0.1,
            max_delay=1.0,
            retryable_exceptions=(RuntimeError,),
            name="test",
        )
        def always_fail():
            raise RuntimeError("fail")

        with patch("time.sleep", side_effect=delays.append):
            with pytest.raises(RuntimeError):
                always_fail()

        # Delays should be: 0.1, 0.2, 0.4 (exponential)
        assert len(delays) == 3
        assert delays[0] == 0.1
        assert delays[1] == 0.2
        assert delays[2] == 0.4

    def test_max_delay_cap(self, tmp_path):
        """Delay should not exceed max_delay."""
        delays = []

        @retry_with_backoff(
            max_retries=5,
            base_delay=1.0,
            max_delay=2.0,
            retryable_exceptions=(RuntimeError,),
            name="test",
        )
        def always_fail():
            raise RuntimeError("fail")

        with patch("time.sleep", side_effect=delays.append):
            with pytest.raises(RuntimeError):
                always_fail()

        # All delays should be capped at 2.0
        assert all(d <= 2.0 for d in delays)


class TestSyncProgress:
    """Tests for SyncProgress tracker."""

    def test_load_nonexistent_file(self, tmp_path):
        """Should start empty if file doesn't exist."""
        progress_file = tmp_path / "progress.json"
        progress = SyncProgress(progress_file)
        assert progress.get_stats()["completed_count"] == 0

    def test_mark_and_check_completed(self, tmp_path):
        """Should track completed items."""
        progress_file = tmp_path / "progress.json"
        progress = SyncProgress(progress_file)

        progress.mark_completed("list1")
        assert progress.is_completed("list1")
        assert not progress.is_completed("list2")

    def test_persistence(self, tmp_path):
        """Should persist across instances."""
        progress_file = tmp_path / "progress.json"

        progress1 = SyncProgress(progress_file)
        progress1.mark_completed("list1")
        progress1.mark_completed("list2")

        progress2 = SyncProgress(progress_file)
        assert progress2.is_completed("list1")
        assert progress2.is_completed("list2")
        assert not progress2.is_completed("list3")

    def test_reset(self, tmp_path):
        """Should clear all progress on reset."""
        progress_file = tmp_path / "progress.json"
        progress = SyncProgress(progress_file)

        progress.mark_completed("list1")
        progress.reset()

        assert not progress.is_completed("list1")
        assert progress.get_stats()["completed_count"] == 0

    def test_get_stats(self, tmp_path):
        """Should return correct statistics."""
        progress_file = tmp_path / "progress.json"
        progress = SyncProgress(progress_file)

        progress.mark_completed("c")
        progress.mark_completed("a")
        progress.mark_completed("b")

        stats = progress.get_stats()
        assert stats["completed_count"] == 3
        assert stats["completed"] == ["a", "b", "c"]

    def test_corrupt_file_handling(self, tmp_path):
        """Should handle corrupt progress file gracefully."""
        progress_file = tmp_path / "progress.json"
        progress_file.write_text("not json")

        progress = SyncProgress(progress_file)
        assert progress.get_stats()["completed_count"] == 0

    def test_save_includes_timestamp(self, tmp_path):
        """Saved file should include timestamp."""
        progress_file = tmp_path / "progress.json"
        progress = SyncProgress(progress_file)
        progress.mark_completed("list1")

        with open(progress_file) as f:
            data = json.load(f)

        assert "completed" in data
        assert "timestamp" in data
        assert data["completed"] == ["list1"]


class TestCircuitBreaker:
    """Tests for CircuitBreaker."""

    def test_initially_closed(self):
        """Circuit should start closed."""
        breaker = CircuitBreaker()
        assert breaker.state.value == "closed"

    def test_successful_call(self):
        """Successful call should return result."""
        breaker = CircuitBreaker()
        result = breaker.call(lambda: "success")
        assert result == "success"

    def test_opens_after_failures(self):
        """Circuit should open after threshold failures."""
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=1.0)

        def fail():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            breaker.call(fail)
        with pytest.raises(RuntimeError):
            breaker.call(fail)

        with pytest.raises(CircuitBreakerOpen):
            breaker.call(fail)

        assert breaker.state.value == "open"

    def test_half_open_after_cooldown(self):
        """Circuit should enter half-open after cooldown."""
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.01, success_threshold=1)

        def fail():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            breaker.call(fail)
        assert breaker.state.value == "open"

        time.sleep(0.02)

        result = breaker.call(lambda: "success")
        assert result == "success"
        assert breaker.state.value == "closed"

    def test_fallback_when_open(self):
        """Should use fallback when circuit is open."""
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=1.0)

        def fail():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            breaker.call(fail)

        result = breaker.call(fail, fallback=lambda: "fallback")
        assert result == "fallback"

    def test_get_stats(self):
        """Should return circuit statistics."""
        breaker = CircuitBreaker(name="test_breaker")
        stats = breaker.get_stats()

        assert stats["name"] == "test_breaker"
        assert stats["state"] == "closed"
        assert stats["failure_count"] == 0
        assert stats["failure_threshold"] == 5

    def test_success_resets_failure_count(self):
        """Success should reset failure count in closed state."""
        breaker = CircuitBreaker(failure_threshold=5)

        def fail():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            breaker.call(fail)
        assert breaker.get_stats()["failure_count"] == 1

        breaker.call(lambda: "success")
        assert breaker.get_stats()["failure_count"] == 0
