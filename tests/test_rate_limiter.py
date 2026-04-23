"""Tests for the RateLimiter class."""

import time
from unittest.mock import patch

from traktor.clients import RateLimiter


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_init(self):
        """Test RateLimiter initialization."""
        limiter = RateLimiter(min_interval=1.5)
        assert limiter.min_interval == 1.5
        assert limiter._last_request_time == 0.0

    def test_first_wait_no_sleep(self):
        """Test that first wait() call doesn't sleep."""
        limiter = RateLimiter(min_interval=1.0)

        start = time.time()
        limiter.wait()
        elapsed = time.time() - start

        # Should complete almost immediately (no prior request)
        assert elapsed < 0.1

    def test_wait_enforces_interval(self):
        """Test that wait() enforces the minimum interval."""
        limiter = RateLimiter(min_interval=0.5)

        # First call - no sleep
        limiter.wait()

        # Second call immediately - should sleep ~0.5s
        with patch("time.sleep") as mock_sleep:
            limiter.wait()
            mock_sleep.assert_called_once()
            sleep_time = mock_sleep.call_args[0][0]
            # Should sleep for close to the full interval (minus tiny elapsed time)
            assert sleep_time > 0.4  # At least most of the interval
            assert sleep_time <= 0.5  # But not more than the interval

    def test_wait_after_delay(self):
        """Test wait() after sufficient time has passed."""
        limiter = RateLimiter(min_interval=1.0)

        # First call
        limiter.wait()

        # Simulate time passing
        limiter._last_request_time = time.time() - 2.0  # 2 seconds ago

        # This call should not sleep since enough time has passed
        with patch("time.sleep") as mock_sleep:
            limiter.wait()
            mock_sleep.assert_not_called()

    def test_thread_safety(self):
        """Test that RateLimiter is thread-safe."""
        import threading

        limiter = RateLimiter(min_interval=0.1)
        call_times = []
        lock = threading.Lock()

        def make_request():
            limiter.wait()
            with lock:
                call_times.append(time.time())

        # Create multiple threads
        threads = [threading.Thread(target=make_request) for _ in range(5)]

        # Start all threads simultaneously
        for t in threads:
            t.start()

        # Wait for all to complete
        for t in threads:
            t.join()

        # Verify that calls were spaced out (at least min_interval apart)
        sorted_times = sorted(call_times)
        for i in range(1, len(sorted_times)):
            gap = sorted_times[i] - sorted_times[i - 1]
            # Each gap should be at least close to min_interval
            # (allowing some tolerance for thread scheduling)
            assert gap >= 0.08, f"Gap {i} was only {gap}s, expected at least 0.08s"

    def test_logging_on_sleep(self):
        """Test that sleep duration is logged."""
        from unittest.mock import MagicMock

        limiter = RateLimiter(min_interval=0.5)

        # First call to set last_request_time
        limiter.wait()

        # Patch logger to capture debug output
        with patch("traktor.clients.logger") as mock_logger:
            mock_logger.debug = MagicMock()
            limiter.wait()
            # Should log the sleep duration
            mock_logger.debug.assert_called()
            log_msg = mock_logger.debug.call_args[0][0]
            assert "Rate limiting: sleeping" in log_msg
            assert "s" in log_msg

    def test_zero_interval(self):
        """Test RateLimiter with zero interval."""
        limiter = RateLimiter(min_interval=0.0)

        # Multiple calls should not sleep
        with patch("time.sleep") as mock_sleep:
            limiter.wait()
            limiter.wait()
            limiter.wait()
            mock_sleep.assert_not_called()

    def test_very_small_interval(self):
        """Test with a very small interval."""
        limiter = RateLimiter(min_interval=0.001)  # 1ms

        limiter.wait()

        # Immediate second call might sleep for tiny amount
        start = time.time()
        limiter.wait()
        elapsed = time.time() - start

        # Should complete very quickly (much less than 0.1s even with sleep)
        assert elapsed < 0.05
