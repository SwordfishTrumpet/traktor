"""Tests for performance monitoring module."""

import time

from traktor.performance import (
    BOTTLENECK_THRESHOLD_MS,
    MAX_API_CALLS_TRACKED,
    PerformanceMonitor,
)


class TestPerformanceMonitor:
    def test_record_api_call(self):
        monitor = PerformanceMonitor()
        monitor.record_api_call("movies/popular", 0.5)
        stats = monitor.api_calls["movies/popular"]
        assert stats["count"] == 1
        assert stats["total_time"] == 0.5
        assert stats["min_time"] == 0.5
        assert stats["max_time"] == 0.5

    def test_record_api_call_multiple(self):
        monitor = PerformanceMonitor()
        monitor.record_api_call("movies/popular", 0.5)
        monitor.record_api_call("movies/popular", 1.5)
        stats = monitor.api_calls["movies/popular"]
        assert stats["count"] == 2
        assert stats["total_time"] == 2.0
        assert stats["min_time"] == 0.5
        assert stats["max_time"] == 1.5

    def test_record_api_call_different_endpoints(self):
        monitor = PerformanceMonitor()
        monitor.record_api_call("movies/popular", 0.5)
        monitor.record_api_call("shows/trending", 0.3)
        assert len(monitor.api_calls) == 2

    def test_api_calls_capped_lru(self):
        """Test that the endpoint dict is bounded by MAX_API_CALLS_TRACKED.

        Regression for A2: the cap constant was defined but never enforced,
        so long-running processes could grow the dict without bound.
        """
        monitor = PerformanceMonitor()
        for i in range(MAX_API_CALLS_TRACKED + 50):
            monitor.record_api_call(f"endpoint_{i}", 0.01)

        assert len(monitor.api_calls) == MAX_API_CALLS_TRACKED
        # Least-recently-recorded endpoints are evicted first
        assert "endpoint_0" not in monitor.api_calls
        assert f"endpoint_{MAX_API_CALLS_TRACKED + 49}" in monitor.api_calls

    def test_api_calls_lru_updated_endpoint_not_evicted(self):
        """Test that re-recorded endpoints are treated as most-recent."""
        monitor = PerformanceMonitor()
        monitor.record_api_call("kept", 0.01)
        for i in range(MAX_API_CALLS_TRACKED):
            monitor.record_api_call(f"filler_{i}", 0.01)
        # Re-record "kept" so it becomes the most-recent endpoint
        monitor.record_api_call("kept", 0.02)

        assert len(monitor.api_calls) == MAX_API_CALLS_TRACKED
        assert "kept" in monitor.api_calls
        assert "filler_0" not in monitor.api_calls

    def test_record_cache_hit(self):
        monitor = PerformanceMonitor()
        monitor.record_cache_hit()
        assert monitor.cache_stats["hits"] == 1
        assert monitor.cache_stats["misses"] == 0

    def test_record_cache_miss(self):
        monitor = PerformanceMonitor()
        monitor.record_cache_miss()
        assert monitor.cache_stats["hits"] == 0
        assert monitor.cache_stats["misses"] == 1

    def test_get_summary(self):
        monitor = PerformanceMonitor()
        monitor.record_api_call("movies/popular", 0.5)
        monitor.record_api_call("movies/popular", 1.5)
        monitor.record_cache_hit()
        monitor.record_cache_miss()
        summary = monitor.get_summary()
        assert "api_calls" in summary
        assert "cache_stats" in summary
        assert "memory_samples" in summary
        assert "uptime_seconds" in summary
        assert summary["cache_stats"]["hits"] == 1
        assert summary["cache_stats"]["misses"] == 1
        assert summary["api_calls"]["movies/popular"]["count"] == 2

    def test_detect_bottlenecks(self):
        monitor = PerformanceMonitor()
        monitor.record_api_call("slow/endpoint", 2.0)
        monitor.record_api_call("fast/endpoint", 0.1)
        bottlenecks = monitor.detect_bottlenecks()
        assert len(bottlenecks) == 1
        assert "slow/endpoint" in bottlenecks[0]

    def test_detect_bottlenecks_no_bottlenecks(self):
        monitor = PerformanceMonitor()
        monitor.record_api_call("fast/endpoint", 0.1)
        bottlenecks = monitor.detect_bottlenecks()
        assert len(bottlenecks) == 0

    def test_memory_usage_tracking(self):
        monitor = PerformanceMonitor()
        monitor.record_memory_usage()
        assert len(monitor.memory_samples) >= 1
        sample = monitor.memory_samples[0]
        assert "timestamp" in sample
        assert "rss_mb" in sample

    def test_get_summary_cache_hit_rate(self):
        monitor = PerformanceMonitor()
        monitor.record_cache_hit()
        monitor.record_cache_hit()
        monitor.record_cache_miss()
        summary = monitor.get_summary()
        assert abs(summary["cache_stats"]["hit_rate"] - 2 / 3) < 0.0001

    def test_get_summary_with_no_cache_ops(self):
        monitor = PerformanceMonitor()
        summary = monitor.get_summary()
        assert summary["cache_stats"]["hit_rate"] == 0.0


class TestPerformanceMonitorMemory:
    def test_record_memory_usage_structure(self):
        monitor = PerformanceMonitor()
        monitor.record_memory_usage()
        assert len(monitor.memory_samples) == 1
        sample = monitor.memory_samples[0]
        assert isinstance(sample["timestamp"], float)
        assert isinstance(sample["rss_mb"], (int, float))
        assert sample["rss_mb"] >= 0

    def test_multiple_memory_samples(self):
        monitor = PerformanceMonitor()
        monitor.record_memory_usage()
        time.sleep(0.01)
        monitor.record_memory_usage()
        assert len(monitor.memory_samples) == 2


class TestPerformanceMonitorIntegration:
    def test_api_timing_decorator_pattern(self):
        """Test that timing pattern works correctly for API calls."""
        monitor = PerformanceMonitor()
        start = time.time()
        time.sleep(0.01)
        elapsed = time.time() - start
        monitor.record_api_call("test/endpoint", elapsed)
        stats = monitor.api_calls["test/endpoint"]
        assert stats["count"] == 1
        assert stats["total_time"] >= 0.01

    def test_cache_hit_and_miss_combined(self):
        monitor = PerformanceMonitor()
        for _ in range(10):
            monitor.record_cache_hit()
        for _ in range(5):
            monitor.record_cache_miss()
        summary = monitor.get_summary()
        assert summary["cache_stats"]["hits"] == 10
        assert summary["cache_stats"]["misses"] == 5
        assert summary["cache_stats"]["total"] == 15
        assert abs(summary["cache_stats"]["hit_rate"] - 10 / 15) < 0.0001

    def test_bottleneck_at_exact_threshold(self):
        monitor = PerformanceMonitor()
        threshold_seconds = BOTTLENECK_THRESHOLD_MS / 1000.0
        monitor.record_api_call("threshold/endpoint", threshold_seconds)
        bottlenecks = monitor.detect_bottlenecks()
        # Average exactly at threshold should NOT be flagged
        assert len(bottlenecks) == 0

    def test_bottleneck_above_threshold(self):
        monitor = PerformanceMonitor()
        threshold_seconds = BOTTLENECK_THRESHOLD_MS / 1000.0
        monitor.record_api_call("above/endpoint", threshold_seconds + 0.1)
        bottlenecks = monitor.detect_bottlenecks()
        assert len(bottlenecks) == 1

    def test_summary_uptime_increases(self):
        monitor = PerformanceMonitor()
        summary1 = monitor.get_summary()
        time.sleep(0.01)
        summary2 = monitor.get_summary()
        assert summary2["uptime_seconds"] > summary1["uptime_seconds"]


class TestPerformanceMonitorMemoryPaths:
    """Coverage for memory sampling fallback paths (TODO audit D3)."""

    def test_memory_usage_without_psutil_resource_fallback(self, monkeypatch):
        """Non-darwin resource-module fallback."""
        import traktor.performance as perf

        monkeypatch.setattr(perf, "psutil", None)
        monkeypatch.setattr(perf, "sys", __import__("sys"))
        monkeypatch.setattr(perf.sys, "platform", "linux")

        monitor = perf.PerformanceMonitor()
        monitor.record_memory_usage()

        assert len(monitor.memory_samples) == 1
        assert monitor.memory_samples[0]["rss_mb"] >= 0

    def test_memory_usage_resource_error_fallback(self, monkeypatch):
        """resource module failure falls back to 0.0."""
        import traktor.performance as perf

        monkeypatch.setattr(perf, "psutil", None)

        def boom(*args, **kwargs):
            raise OSError("no rusage")

        monkeypatch.setattr(perf.resource, "getrusage", boom)

        monitor = perf.PerformanceMonitor()
        monitor.record_memory_usage()

        assert monitor.memory_samples[0]["rss_mb"] == 0.0

    def test_memory_usage_psutil_failure(self, monkeypatch):
        """psutil failure falls back to 0.0."""
        import traktor.performance as perf

        class FakeProcess:
            def memory_info(self):
                raise RuntimeError("broken")

        fake_psutil = type("FakePsutil", (), {"Process": staticmethod(lambda: FakeProcess())})
        monkeypatch.setattr(perf, "psutil", fake_psutil)

        monitor = perf.PerformanceMonitor()
        monitor.record_memory_usage()

        assert monitor.memory_samples[0]["rss_mb"] == 0.0
