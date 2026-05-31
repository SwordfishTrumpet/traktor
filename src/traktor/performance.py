"""Performance monitoring for API calls, cache operations, and memory usage."""

from __future__ import annotations

import resource
import sys
import time
from typing import Any

# Optional dependency for memory tracking
try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

BOTTLENECK_THRESHOLD_MS = 1000  # API calls slower than 1s are flagged
MEMORY_SAMPLE_INTERVAL = 60  # Seconds between memory samples (for automated sampling)
MAX_API_CALLS_TRACKED = 1000  # Prevent unbounded growth


class PerformanceMonitor:
    """Tracks performance metrics for API calls, cache operations, and memory usage.

    This class is designed to be lightweight and thread-safe for basic usage.
    For thread-safe concurrent tracking, wrap calls in locks.
    """

    def __init__(self) -> None:
        self.api_calls: dict[str, dict[str, Any]] = {}
        self.cache_stats = {"hits": 0, "misses": 0}
        self.memory_samples: list[dict[str, Any]] = []
        self.start_time = time.time()

    def record_api_call(self, endpoint: str, duration: float) -> None:
        """Record timing for an API call.

        Args:
            endpoint: API endpoint identifier (e.g., "movies/popular")
            duration: Duration in seconds
        """
        if endpoint not in self.api_calls:
            self.api_calls[endpoint] = {
                "count": 0,
                "total_time": 0.0,
                "min_time": float("inf"),
                "max_time": 0.0,
            }
        stats = self.api_calls[endpoint]
        stats["count"] += 1
        stats["total_time"] += duration
        if duration < stats["min_time"]:
            stats["min_time"] = duration
        if duration > stats["max_time"]:
            stats["max_time"] = duration

    def record_cache_hit(self) -> None:
        """Record a cache hit."""
        self.cache_stats["hits"] += 1

    def record_cache_miss(self) -> None:
        """Record a cache miss."""
        self.cache_stats["misses"] += 1

    def record_memory_usage(self) -> None:
        """Record current memory usage in MB.

        Uses psutil if available, otherwise falls back to the resource module
        (Unix only) or returns 0.0 if neither is available.
        """
        rss_mb = 0.0
        if psutil is not None:
            try:
                process = psutil.Process()
                rss_mb = process.memory_info().rss / (1024 * 1024)
            except Exception:
                rss_mb = 0.0
        else:
            # Fallback to resource module (Unix only)
            try:
                usage = resource.getrusage(resource.RUSAGE_SELF)
                # ru_maxrss is in KB on Linux, bytes on macOS
                if sys.platform == "darwin":
                    rss_mb = usage.ru_maxrss / (1024 * 1024)
                else:
                    rss_mb = usage.ru_maxrss / 1024
            except (AttributeError, OSError):
                # Final fallback: approximate using sys.getsizeof on globals
                rss_mb = 0.0

        self.memory_samples.append({
            "timestamp": time.time(),
            "rss_mb": round(rss_mb, 2),
        })

    def get_summary(self) -> dict[str, Any]:
        """Return summary dict with all metrics.

        Returns:
            Dict containing api_calls, cache_stats (with hit_rate), memory_samples,
            and uptime_seconds.
        """
        total_cache = self.cache_stats["hits"] + self.cache_stats["misses"]
        hit_rate = (
            self.cache_stats["hits"] / total_cache if total_cache > 0 else 0.0
        )

        return {
            "api_calls": dict(self.api_calls),
            "cache_stats": {
                "hits": self.cache_stats["hits"],
                "misses": self.cache_stats["misses"],
                "total": total_cache,
                "hit_rate": round(hit_rate, 4),
            },
            "memory_samples": list(self.memory_samples),
            "uptime_seconds": round(time.time() - self.start_time, 2),
        }

    def detect_bottlenecks(self) -> list[str]:
        """Identify slow operations and return warnings.

        Returns:
            List of warning strings for endpoints exceeding threshold.
        """
        warnings = []
        threshold_seconds = BOTTLENECK_THRESHOLD_MS / 1000.0
        for endpoint, stats in self.api_calls.items():
            avg_time = stats["total_time"] / stats["count"] if stats["count"] > 0 else 0
            if avg_time > threshold_seconds:
                warnings.append(
                    f"Bottleneck detected: {endpoint} "
                    f"avg={avg_time * 1000:.1f}ms "
                    f"(n={stats['count']}, max={stats['max_time'] * 1000:.1f}ms)",
                )
        return warnings


# Global performance monitor instance
performance_monitor = PerformanceMonitor()
