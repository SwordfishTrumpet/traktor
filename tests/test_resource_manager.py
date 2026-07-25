"""Tests for resource_manager module."""

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


class TestSetMemoryLimit:
    """Tests for set_memory_limit method."""

    def test_set_memory_limit_linux_success(self):
        """Test memory limit set on Linux."""
        manager = ResourceManager()
        with patch.object(sys, "platform", "linux"):
            with patch("resource.setrlimit") as mock_setrlimit:
                with patch("resource.RLIMIT_RSS", 5):
                    result = manager.set_memory_limit(256)
                    assert result is True
                    mock_setrlimit.assert_called_once()
                    args = mock_setrlimit.call_args[0]
                    assert args[0] == 5  # resource.RLIMIT_RSS
                    assert args[1][0] == 256 * KB_PER_MB * BYTES_PER_KB
                    assert args[1][1] == 256 * KB_PER_MB * BYTES_PER_KB * 2

    def test_set_memory_limit_macos_success(self):
        """Test memory limit set on macOS."""
        manager = ResourceManager()
        with patch.object(sys, "platform", "darwin"):
            with patch("resource.setrlimit") as mock_setrlimit:
                result = manager.set_memory_limit(256)
                assert result is True
                mock_setrlimit.assert_called_once()

    def test_set_memory_limit_windows_not_supported(self):
        """Test memory limit returns False on Windows."""
        manager = ResourceManager()
        with patch.object(sys, "platform", "win32"):
            result = manager.set_memory_limit(256)
            assert result is False

    def test_set_memory_limit_value_error(self):
        """Test memory limit handles ValueError."""
        manager = ResourceManager()
        with patch.object(sys, "platform", "linux"):
            with patch("resource.setrlimit", side_effect=ValueError("Invalid limit")):
                result = manager.set_memory_limit(256)
                assert result is False

    def test_set_memory_limit_os_error(self):
        """Test memory limit handles OSError."""
        manager = ResourceManager()
        with patch.object(sys, "platform", "linux"):
            with patch("resource.setrlimit", side_effect=OSError("Permission denied")):
                result = manager.set_memory_limit(256)
                assert result is False

    def test_set_memory_limit_attribute_error(self):
        """Test memory limit handles AttributeError (no resource module)."""
        manager = ResourceManager()
        with patch.object(sys, "platform", "linux"):
            with patch("resource.setrlimit", side_effect=AttributeError("No such attribute")):
                result = manager.set_memory_limit(256)
                assert result is False


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

    def test_check_memory_usage_exceeds_limit(self):
        """Test check_memory_usage returns False when exceeded."""
        manager = ResourceManager(max_memory_mb=256)
        with patch.object(manager, "get_current_memory_usage_mb", return_value=512.0):
            result = manager.check_memory_usage()
            assert result is False


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
