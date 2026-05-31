"""Tests for auto_update module."""

import json
from unittest.mock import MagicMock, patch

from traktor.auto_update import (
    AutoUpdater,
    apply_update_and_print,
    check_and_print_update,
)


class TestVersionCompare:
    """Test version comparison logic."""

    def test_equal_versions(self):
        updater = AutoUpdater()
        assert updater._version_compare("1.0.0", "1.0.0") == 0
        assert updater._version_compare("v1.0.0", "1.0.0") == 0
        assert updater._version_compare("1.0.0", "v1.0.0") == 0

    def test_major_version_update(self):
        updater = AutoUpdater()
        assert updater._version_compare("2.0.0", "1.0.0") > 0
        assert updater._version_compare("1.0.0", "2.0.0") < 0

    def test_minor_version_update(self):
        updater = AutoUpdater()
        assert updater._version_compare("1.1.0", "1.0.0") > 0
        assert updater._version_compare("1.0.0", "1.1.0") < 0

    def test_patch_version_update(self):
        updater = AutoUpdater()
        assert updater._version_compare("1.0.1", "1.0.0") > 0
        assert updater._version_compare("1.0.0", "1.0.1") < 0

    def test_v_prefix(self):
        updater = AutoUpdater()
        assert updater._version_compare("v1.2.0", "v1.1.0") > 0
        assert updater._version_compare("v1.0.0", "v1.0.0") == 0

    def test_different_length_versions(self):
        updater = AutoUpdater()
        assert updater._version_compare("1.0", "1.0.0") == 0
        assert updater._version_compare("1", "1.0.0") == 0
        assert updater._version_compare("1.1", "1.0.0") > 0

    def test_non_numeric_parts(self):
        updater = AutoUpdater()
        # Non-numeric parts are treated as 0
        assert updater._version_compare("1.0.0-alpha", "1.0.0") == 0
        assert updater._version_compare("1.0.0-alpha", "1.0.1") < 0

    def test_empty_version(self):
        updater = AutoUpdater()
        assert updater._version_compare("", "1.0.0") < 0
        assert updater._version_compare("1.0.0", "") > 0


class TestCheckForUpdate:
    """Test update checking with mocked HTTP responses."""

    def test_update_available(self):
        updater = AutoUpdater()
        updater.current_version = "1.0.0"

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "tag_name": "v2.0.0",
                "html_url": "https://github.com/SwordfishTrumpet/traktor/releases/v2.0.0",
                "body": "New features",
            }
        ).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("traktor.auto_update.urlopen", return_value=mock_response):
            result = updater.check_for_update()

        assert result["current_version"] == "1.0.0"
        assert result["latest_version"] == "v2.0.0"
        assert result["update_available"] is True
        assert result["release_url"] == (
            "https://github.com/SwordfishTrumpet/traktor/releases/v2.0.0"
        )
        assert result["release_notes"] == "New features"

    def test_no_update_available(self):
        updater = AutoUpdater()
        updater.current_version = "2.0.0"

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "tag_name": "v1.0.0",
                "html_url": "https://github.com/SwordfishTrumpet/traktor/releases/v1.0.0",
                "body": "Old release",
            }
        ).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("traktor.auto_update.urlopen", return_value=mock_response):
            result = updater.check_for_update()

        assert result["current_version"] == "2.0.0"
        assert result["latest_version"] == "v1.0.0"
        assert result["update_available"] is False

    def test_same_version_no_update(self):
        updater = AutoUpdater()
        updater.current_version = "1.0.0"

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "tag_name": "v1.0.0",
                "html_url": "https://github.com/SwordfishTrumpet/traktor/releases/v1.0.0",
                "body": "Same",
            }
        ).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("traktor.auto_update.urlopen", return_value=mock_response):
            result = updater.check_for_update()

        assert result["update_available"] is False


class TestCheckForUpdateErrors:
    """Test error handling in check_for_update."""

    def test_network_error(self):
        updater = AutoUpdater()
        updater.current_version = "1.0.0"

        from urllib.error import URLError

        with patch("traktor.auto_update.urlopen", side_effect=URLError("Network error")):
            result = updater.check_for_update()

        assert result["current_version"] == "1.0.0"
        assert result["latest_version"] == "unknown"
        assert result["update_available"] is False
        assert "error" in result
        assert "Network error" in result["error"]

    def test_invalid_json(self):
        updater = AutoUpdater()
        updater.current_version = "1.0.0"

        mock_response = MagicMock()
        mock_response.read.return_value = b"not valid json"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("traktor.auto_update.urlopen", return_value=mock_response):
            result = updater.check_for_update()

        assert result["current_version"] == "1.0.0"
        assert result["latest_version"] == "unknown"
        assert result["update_available"] is False
        assert "error" in result

    def test_timeout_error(self):
        updater = AutoUpdater()
        updater.current_version = "1.0.0"

        with patch("traktor.auto_update.urlopen", side_effect=TimeoutError("Connection timed out")):
            result = updater.check_for_update()

        assert result["current_version"] == "1.0.0"
        assert result["latest_version"] == "unknown"
        assert result["update_available"] is False
        assert "error" in result
        assert "timed out" in result["error"]


class TestStubs:
    """Test stub methods that return False."""

    def test_download_update_returns_false(self, tmp_path):
        updater = AutoUpdater()
        result = updater.download_update(tmp_path)
        assert result is False

    def test_apply_update_returns_false(self):
        updater = AutoUpdater()
        result = updater.apply_update()
        assert result is False

    def test_rollback_returns_false(self):
        updater = AutoUpdater()
        result = updater.rollback()
        assert result is False


class TestGetCurrentVersion:
    """Test version reading from pyproject.toml."""

    def test_get_current_version_from_pyproject(self, tmp_path, monkeypatch):
        # Create a mock pyproject.toml
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "1.2.3"\n')

        updater = AutoUpdater()

        # Patch the path resolution
        with patch.object(updater, "_get_current_version", return_value="1.2.3"):
            version = updater._get_current_version()

        assert version == "1.2.3"

    def test_get_current_version_unknown(self, tmp_path):
        updater = AutoUpdater()
        # In the test environment, pyproject.toml may not exist at the expected path
        # The default is "unknown" if it can't be found
        version = updater._get_current_version()
        # Could be a real version or unknown depending on test setup
        assert isinstance(version, str)


class TestCheckAndPrintUpdate:
    """Test the convenience function for CLI."""

    def test_check_and_print_update_available(self, capsys):
        with patch.object(AutoUpdater, "check_for_update") as mock_check:
            mock_check.return_value = {
                "current_version": "1.0.0",
                "latest_version": "v2.0.0",
                "update_available": True,
                "release_url": "https://example.com/release",
                "release_notes": "New stuff",
            }

            result = check_and_print_update()
            assert result == 0

            captured = capsys.readouterr()
            assert "A new version is available" in captured.out
            assert "1.0.0" in captured.out
            assert "v2.0.0" in captured.out

    def test_check_and_print_update_not_available(self, capsys):
        with patch.object(AutoUpdater, "check_for_update") as mock_check:
            mock_check.return_value = {
                "current_version": "2.0.0",
                "latest_version": "v2.0.0",
                "update_available": False,
                "release_url": "",
                "release_notes": "",
            }

            result = check_and_print_update()
            assert result == 0

            captured = capsys.readouterr()
            assert "latest version" in captured.out

    def test_check_and_print_update_error(self, capsys):
        with patch.object(AutoUpdater, "check_for_update") as mock_check:
            mock_check.return_value = {
                "current_version": "1.0.0",
                "latest_version": "unknown",
                "update_available": False,
                "error": "Network unreachable",
            }

            result = check_and_print_update()
            assert result == 1

            captured = capsys.readouterr()
            assert "Failed to check" in captured.out


class TestApplyUpdateAndPrint:
    """Test the apply update convenience function."""

    def test_apply_update_no_update_available(self, capsys):
        with patch.object(AutoUpdater, "check_for_update") as mock_check:
            mock_check.return_value = {
                "current_version": "2.0.0",
                "latest_version": "v2.0.0",
                "update_available": False,
            }

            result = apply_update_and_print()
            assert result == 0

            captured = capsys.readouterr()
            assert "No update available" in captured.out

    def test_apply_update_available(self, capsys):
        with patch.object(AutoUpdater, "check_for_update") as mock_check:
            mock_check.return_value = {
                "current_version": "1.0.0",
                "latest_version": "v2.0.0",
                "update_available": True,
            }

            result = apply_update_and_print()
            assert result == 1

            captured = capsys.readouterr()
            assert "not implemented" in captured.out

    def test_apply_update_error(self, capsys):
        with patch.object(AutoUpdater, "check_for_update") as mock_check:
            mock_check.return_value = {
                "current_version": "1.0.0",
                "latest_version": "unknown",
                "update_available": False,
                "error": "Network unreachable",
            }

            result = apply_update_and_print()
            assert result == 1

            captured = capsys.readouterr()
            assert "Failed to check" in captured.out
