"""Tests for settings module."""

import os
from unittest.mock import patch

from traktor import settings


class TestSettings:
    """Tests for settings configuration."""

    def test_docker_mode_parsing_true(self):
        """Test DOCKER_MODE=true parsing."""
        with patch.dict(os.environ, {"DOCKER_MODE": "true"}, clear=False):
            # Reload settings to pick up new env var
            import importlib

            importlib.reload(settings)
            assert settings.DOCKER_MODE is True

    def test_docker_mode_parsing_false(self):
        """Test DOCKER_MODE=false parsing."""
        with patch.dict(os.environ, {"DOCKER_MODE": "false"}, clear=False):
            import importlib

            importlib.reload(settings)
            assert settings.DOCKER_MODE is False

    def test_docker_mode_parsing_case_insensitive(self):
        """Test DOCKER_MODE case insensitivity."""
        with patch.dict(os.environ, {"DOCKER_MODE": "TRUE"}, clear=False):
            import importlib

            importlib.reload(settings)
            assert settings.DOCKER_MODE is True

    def test_traktor_workers_parsing(self):
        """Test TRAKTOR_WORKERS parsing."""
        with patch.dict(os.environ, {"TRAKTOR_WORKERS": "16"}, clear=False):
            import importlib

            importlib.reload(settings)
            assert settings.MAX_WORKERS == 16

    def test_traktor_workers_default(self):
        """Test TRAKTOR_WORKERS default value."""
        # Ensure env var is not set
        env_without_workers = {k: v for k, v in os.environ.items() if k != "TRAKTOR_WORKERS"}
        with patch.dict(os.environ, env_without_workers, clear=True):
            import importlib

            importlib.reload(settings)
            assert settings.MAX_WORKERS == 8  # Default value

    def test_path_resolution_local_mode(self):
        """Test path resolution in local mode."""
        with patch.dict(os.environ, {"DOCKER_MODE": "false"}, clear=False):
            import importlib

            importlib.reload(settings)
            # In local mode, paths should be in home directory
            assert "~/.traktor" in str(settings.CACHE_DIR) or ".traktor" in str(settings.CACHE_DIR)

    def test_path_resolution_docker_mode(self):
        """Test path resolution in Docker mode."""
        with patch.dict(os.environ, {"DOCKER_MODE": "true"}, clear=False):
            import importlib

            importlib.reload(settings)
            # In Docker mode, paths should be under /data
            assert "/data" in str(settings.CACHE_DIR)
            assert "/data" in str(settings.LOG_FILE)

    def test_cache_ttl_settings(self):
        """Test that cache TTL settings are defined."""
        # Check constants exist without reloading settings
        assert hasattr(settings, "CACHE_MAX_AGE_HOURS")
        assert settings.CACHE_MAX_AGE_HOURS == 24

    def test_ensure_dirs_function(self, tmp_path):
        """Test ensure_dirs creates directories."""
        # Mock settings to use temp directory
        test_cache_dir = tmp_path / "test_cache"
        test_config_file = tmp_path / "test_config.json"

        with patch.object(settings, "CACHE_DIR", test_cache_dir):
            with patch.object(settings, "CONFIG_FILE", test_config_file):
                with patch.object(settings, "LOG_FILE", tmp_path / "test.log"):
                    settings.ensure_dirs()
                    assert test_cache_dir.exists()
                    assert test_cache_dir.is_dir()

    def test_traktor_max_memory_mb_default(self):
        """Test TRAKTOR_MAX_MEMORY_MB default value."""
        env_without = {k: v for k, v in os.environ.items() if k != "TRAKTOR_MAX_MEMORY_MB"}
        with patch.dict(os.environ, env_without, clear=True):
            import importlib

            importlib.reload(settings)
            assert settings.TRAKTOR_MAX_MEMORY_MB == 512

    def test_traktor_max_memory_mb_custom(self):
        """Test TRAKTOR_MAX_MEMORY_MB custom value."""
        with patch.dict(os.environ, {"TRAKTOR_MAX_MEMORY_MB": "1024"}, clear=False):
            import importlib

            importlib.reload(settings)
            assert settings.TRAKTOR_MAX_MEMORY_MB == 1024

    def test_traktor_cpu_throttle_default(self):
        """Test TRAKTOR_CPU_THROTTLE default value."""
        env_without = {k: v for k, v in os.environ.items() if k != "TRAKTOR_CPU_THROTTLE"}
        with patch.dict(os.environ, env_without, clear=True):
            import importlib

            importlib.reload(settings)
            assert settings.TRAKTOR_CPU_THROTTLE is False

    def test_traktor_cpu_throttle_enabled(self):
        """Test TRAKTOR_CPU_THROTTLE enabled."""
        with patch.dict(os.environ, {"TRAKTOR_CPU_THROTTLE": "true"}, clear=False):
            import importlib

            importlib.reload(settings)
            assert settings.TRAKTOR_CPU_THROTTLE is True

    def test_traktor_bandwidth_limit_kbps_default(self):
        """Test TRAKTOR_BANDWIDTH_LIMIT_KBPS default value."""
        env_without = {k: v for k, v in os.environ.items() if k != "TRAKTOR_BANDWIDTH_LIMIT_KBPS"}
        with patch.dict(os.environ, env_without, clear=True):
            import importlib

            importlib.reload(settings)
            assert settings.TRAKTOR_BANDWIDTH_LIMIT_KBPS == 0

    def test_traktor_bandwidth_limit_kbps_custom(self):
        """Test TRAKTOR_BANDWIDTH_LIMIT_KBPS custom value."""
        with patch.dict(os.environ, {"TRAKTOR_BANDWIDTH_LIMIT_KBPS": "1000"}, clear=False):
            import importlib

            importlib.reload(settings)
            assert settings.TRAKTOR_BANDWIDTH_LIMIT_KBPS == 1000
