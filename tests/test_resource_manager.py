"""Tests for resource_manager module."""

import logging
import sys
import time
from unittest.mock import MagicMock, patch

from traktor.resource_manager import (
    BYTES_PER_KB,
    CPU_THROTTLE_INTERVAL_MS,
    DEFAULT_MAX_MEMORY_MB,
    KB_PER_MB,
    ResourceManager,
    resource_manager,
)


class TestResourceManagerInit:
    """Tests for ResourceManager initialization."""

    def test_default_memory_limit(self):
        """Test default memory limit is used when not specified."""
        manager = ResourceManager()
        assert manager.max_memory_mb == DEFAULT_MAX_MEMORY_MB

    def test_custom_memory_limit(self):
        """Test custom memory limit is used when specified."""
        manager = ResourceManager(max_memory_mb=1024)
        assert manager.max_memory_mb == 1024

    def test_cpu_throttle_disabled_by_default(self):
        """Test CPU throttling is disabled by default."""
        manager = ResourceManager()
        assert manager._cpu_throttle_enabled is False
        assert manager._cpu_throttle_thread is None


class TestSetMemoryThreshold:
    """Tests for set_memory_threshold method (issue #16)."""

    def test_threshold_is_recorded_and_logged_honestly(self, caplog):
        """Setting the threshold must not claim a limit the kernel applies."""
        manager = ResourceManager()

        with caplog.at_level(logging.INFO):
            result = manager.set_memory_threshold(256)

        assert result is True
        assert manager.max_memory_mb == 256
        assert "warning threshold set to 256 MB" in caplog.text
        assert "not enforced by the kernel" in caplog.text
        # The old log line promised a ceiling that RLIMIT_RSS never applied.
        assert "Memory limit set to 256 MB" not in caplog.text

    def test_threshold_sets_no_rlimit(self):
        """RLIMIT_RSS has no effect on Linux, so it is never claimed as one."""
        manager = ResourceManager()
        with patch("resource.setrlimit") as mock_setrlimit:
            assert manager.set_memory_threshold(256) is True
        mock_setrlimit.assert_not_called()

    def test_threshold_applies_on_every_platform(self):
        """The warning works wherever usage can be measured, Windows included."""
        manager = ResourceManager()
        with patch.object(sys, "platform", "win32"):
            assert manager.set_memory_threshold(128) is True
        assert manager.max_memory_mb == 128


class TestGetCurrentMemoryUsage:
    """Tests for get_current_memory_usage_mb method."""

    def test_get_memory_usage_with_psutil(self):
        """Test memory usage with psutil available."""
        manager = ResourceManager()
        mock_process = MagicMock()
        mock_process.memory_info.return_value = MagicMock(rss=512 * KB_PER_MB * BYTES_PER_KB)

        mock_psutil = MagicMock()
        mock_psutil.Process = MagicMock(return_value=mock_process)

        with patch("traktor.resource_manager.psutil", mock_psutil):
            result = manager.get_current_memory_usage_mb()
            assert result == 512.0

    def test_get_memory_usage_psutil_not_installed_linux(self):
        """Test memory usage fallback on Linux without psutil."""
        manager = ResourceManager()
        with patch.object(sys, "platform", "linux"):
            with patch("traktor.resource_manager.psutil", None):
                with patch("traktor.resource_manager.resource") as mock_resource:
                    mock_usage = MagicMock()
                    mock_usage.ru_maxrss = 1024 * KB_PER_MB  # KB
                    mock_resource.getrusage.return_value = mock_usage
                    result = manager.get_current_memory_usage_mb()
                    assert result == 1024.0

    def test_get_memory_usage_psutil_not_installed_macos(self):
        """Test memory usage fallback on macOS without psutil."""
        manager = ResourceManager()
        with patch.object(sys, "platform", "darwin"):
            with patch("traktor.resource_manager.psutil", None):
                with patch("traktor.resource_manager.resource") as mock_resource:
                    mock_usage = MagicMock()
                    mock_usage.ru_maxrss = 512 * KB_PER_MB  # KB
                    mock_resource.getrusage.return_value = mock_usage
                    result = manager.get_current_memory_usage_mb()
                    assert result == 512.0

    def test_get_memory_usage_psutil_not_installed_windows(self):
        """Test memory usage returns 0 on Windows without psutil."""
        manager = ResourceManager()
        with patch.object(sys, "platform", "win32"):
            with patch("traktor.resource_manager.psutil", None):
                with patch("traktor.resource_manager.resource", None):
                    result = manager.get_current_memory_usage_mb()
                    assert result == 0.0


class TestCheckMemoryUsage:
    """Tests for check_memory_usage method."""

    def test_check_memory_usage_within_limit(self):
        """Test check_memory_usage returns True when within limit."""
        manager = ResourceManager(max_memory_mb=1024)
        with patch.object(manager, "get_current_memory_usage_mb", return_value=512.0):
            result = manager.check_memory_usage()
            assert result is True

    def test_check_memory_usage_exceeds_limit(self, caplog):
        """Usage above the threshold warns with the threshold, not a limit."""
        manager = ResourceManager(max_memory_mb=256)
        with patch.object(manager, "get_current_memory_usage_mb", return_value=512.0):
            with caplog.at_level(logging.WARNING):
                result = manager.check_memory_usage()
        assert result is False
        assert "above the warning threshold (256 MB)" in caplog.text


class TestCpuThrottle:
    """Tests for CPU throttling methods."""

    def test_start_cpu_throttle(self):
        """Test start_cpu_throttle starts a thread."""
        manager = ResourceManager()
        manager.start_cpu_throttle(target_cpu_percent=50.0)
        assert manager._cpu_throttle_enabled is True
        assert manager._cpu_throttle_thread is not None
        assert manager._cpu_throttle_thread.is_alive()
        manager.stop_cpu_throttle()

    def test_stop_cpu_throttle(self):
        """Test stop_cpu_throttle stops the thread."""
        manager = ResourceManager()
        manager.start_cpu_throttle(target_cpu_percent=50.0)
        # Give thread a moment to start
        time.sleep(0.01)
        manager.stop_cpu_throttle()
        assert manager._cpu_throttle_enabled is False

    def test_stop_cpu_throttle_when_not_started(self):
        """Test stop_cpu_throttle is safe when throttling was never started."""
        manager = ResourceManager()
        manager.stop_cpu_throttle()
        assert manager._cpu_throttle_enabled is False

    def test_cpu_throttle_does_sleep(self):
        """Test CPU throttle thread calls sleep."""
        manager = ResourceManager()
        with patch("time.sleep") as mock_sleep:
            with patch.object(manager._stop_throttle, "is_set", side_effect=[False, False, True]):
                with patch.object(manager._stop_throttle, "wait", return_value=False):
                    manager.start_cpu_throttle(target_cpu_percent=50.0)
                    # Wait briefly for thread to start
                    time.sleep(0.05)
                    manager.stop_cpu_throttle()
                    # Sleep should have been called at least once
                    assert mock_sleep.called


class TestSetBandwidthLimit:
    """Tests for set_bandwidth_limit method."""

    def test_set_bandwidth_limit_returns_false(self):
        """Test set_bandwidth_limit returns False as not implemented."""
        manager = ResourceManager()
        result = manager.set_bandwidth_limit(1000)
        assert result is False

    def test_set_bandwidth_limit_logs_warning(self, caplog):
        """Test set_bandwidth_limit logs a warning."""
        manager = ResourceManager()
        with patch("traktor.resource_manager.logger") as mock_logger:
            manager.set_bandwidth_limit(1000)
            mock_logger.warning.assert_called_once()
            assert "not implemented" in mock_logger.warning.call_args[0][0].lower()


class TestModuleLevelInstance:
    """Tests for module-level resource_manager instance."""

    def test_module_level_instance_exists(self):
        """Test module-level resource_manager instance exists."""
        assert isinstance(resource_manager, ResourceManager)

    def test_module_level_instance_default_memory(self):
        """Test module-level instance has default memory limit."""
        assert resource_manager.max_memory_mb == DEFAULT_MAX_MEMORY_MB


class TestConstants:
    """Tests for module constants."""

    def test_bytes_per_kb(self):
        """Test BYTES_PER_KB constant."""
        assert BYTES_PER_KB == 1024

    def test_kb_per_mb(self):
        """Test KB_PER_MB constant."""
        assert KB_PER_MB == 1024

    def test_default_max_memory_mb(self):
        """Test DEFAULT_MAX_MEMORY_MB constant."""
        assert DEFAULT_MAX_MEMORY_MB == 512

    def test_cpu_throttle_interval_ms(self):
        """Test CPU_THROTTLE_INTERVAL_MS constant."""
        assert CPU_THROTTLE_INTERVAL_MS == 100
