"""Tests for performance monitoring module."""

import time

from traktor.performance import (
    BOTTLENECK_THRESHOLD_MS,
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
