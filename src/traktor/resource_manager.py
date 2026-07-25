"""Resource management for traktor process.

Supports memory limits, CPU throttling, and network bandwidth control.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time

from .log import logger

# Optional dependency for memory tracking; find_spec guard avoids try/except
# import redefinition issues flagged by mypy.
psutil = __import__("psutil") if importlib.util.find_spec("psutil") else None

# Unix-only module for resource limits
resource = __import__("resource") if importlib.util.find_spec("resource") else None

# Constants
BYTES_PER_KB = 1024
KB_PER_MB = 1024
DEFAULT_MAX_MEMORY_MB = 512  # 512 MB default limit
CPU_THROTTLE_INTERVAL_MS = 100  # Sleep interval for CPU throttling in milliseconds


class ResourceManager:
    """Manages resource limits for traktor process.

    Supports:
    - Memory limits (soft RSS limit)
    - CPU throttling (via sleep intervals)
    - Network bandwidth control (placeholder)
    """

    def __init__(self, max_memory_mb: int | None = None) -> None:
        self.max_memory_mb = max_memory_mb or DEFAULT_MAX_MEMORY_MB
        self._cpu_throttle_enabled = False
        self._cpu_throttle_thread: threading.Thread | None = None
        self._stop_throttle = threading.Event()

    def set_memory_limit(self, max_memory_mb: int) -> bool:
        """Set soft memory limit for the process.

        Args:
            max_memory_mb: Maximum memory in MB

        Returns:
            True if successful, False if not supported
        """
        if sys.platform not in ("linux", "darwin") or resource is None:
            logger.warning(f"Memory limits not supported on {sys.platform}")
            return False

        try:
            max_bytes = max_memory_mb * KB_PER_MB * BYTES_PER_KB
            resource.setrlimit(resource.RLIMIT_RSS, (max_bytes, max_bytes * 2))
            logger.info(f"Memory limit set to {max_memory_mb} MB")
        except (ValueError, OSError, AttributeError) as e:
            logger.warning(f"Could not set memory limit: {e}")
            return False
        else:
            return True

    def start_cpu_throttle(self, target_cpu_percent: float = 50.0) -> None:
        """Start CPU throttling to limit CPU usage.

        Args:
            target_cpu_percent: Target CPU percentage (0-100)
        """
        self._stop_throttle.clear()
        self._cpu_throttle_enabled = True

        def throttle() -> None:
            while not self._stop_throttle.is_set():
                # Simple throttling: sleep periodically
                # More sophisticated throttling would monitor actual CPU usage
                sleep_time = (100 - target_cpu_percent) / 100 * CPU_THROTTLE_INTERVAL_MS / 1000
                if sleep_time > 0:
                    time.sleep(sleep_time)
                self._stop_throttle.wait(CPU_THROTTLE_INTERVAL_MS / 1000)

        self._cpu_throttle_thread = threading.Thread(target=throttle, daemon=True)
        self._cpu_throttle_thread.start()
        logger.info(f"CPU throttling started (target: {target_cpu_percent}%)")

    def stop_cpu_throttle(self) -> None:
        """Stop CPU throttling."""
        if self._cpu_throttle_enabled:
            self._stop_throttle.set()
            if self._cpu_throttle_thread:
                self._cpu_throttle_thread.join(timeout=1.0)
            self._cpu_throttle_enabled = False
            logger.info("CPU throttling stopped")

    def get_current_memory_usage_mb(self) -> float:
        """Get current memory usage in MB.

        Returns:
            Memory usage in MB
        """
        if psutil is not None:
            try:
                process = psutil.Process(os.getpid())
                return process.memory_info().rss / (KB_PER_MB * BYTES_PER_KB)
            except Exception:
                return 0.0

        # Fallback: use resource module (Unix only)
        if sys.platform in ("linux", "darwin") and resource is not None:
            try:
                usage = resource.getrusage(resource.RUSAGE_SELF)
                return usage.ru_maxrss / KB_PER_MB  # KB to MB
            except (AttributeError, OSError):
                return 0.0

        return 0.0

    def check_memory_usage(self) -> bool:
        """Check if current memory usage is within limits.

        Returns:
            True if within limits, False if exceeded
        """
        current = self.get_current_memory_usage_mb()
        if current > self.max_memory_mb:
            logger.warning(
                f"Memory usage ({current:.1f} MB) exceeds limit ({self.max_memory_mb} MB)",
            )
            return False
        return True

    def set_bandwidth_limit(self, max_kbps: int) -> bool:
        """Set network bandwidth limit.

        Note: This is a placeholder. Actual bandwidth limiting requires OS-level tools
        like tc (Linux) or netsh (Windows).

        Args:
            max_kbps: Maximum bandwidth in KB/s

        Returns:
            True if conceptually applied, False if not supported
        """
        logger.warning(
            f"Network bandwidth limiting ({max_kbps} KB/s) is not implemented. "
            "Use OS-level tools (tc, netsh) for bandwidth control.",
        )
        return False


# Module-level instance for convenience
resource_manager = ResourceManager()
