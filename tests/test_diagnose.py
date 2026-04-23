"""Tests for the diagnose module."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from traktor import diagnose


class TestCheckResult:
    """Tests for CheckResult dataclass."""

    def test_check_result_creation(self):
        """Test creating a CheckResult."""
        result = diagnose.CheckResult(
            name="Test Check",
            status="pass",
            message="Everything is fine",
            details="Additional details",
            suggestions=["Do this", "Do that"],
        )

        assert result.name == "Test Check"
        assert result.status == "pass"
        assert result.message == "Everything is fine"
        assert result.details == "Additional details"
        assert result.suggestions == ["Do this", "Do that"]


class TestDiagnoseCommand:
    """Tests for DiagnoseCommand."""

    @pytest.fixture
    def diagnose_cmd(self):
        """Create a DiagnoseCommand instance."""
        return diagnose.DiagnoseCommand()

    def test_init(self, diagnose_cmd):
        """Test DiagnoseCommand initialization."""
        assert diagnose_cmd.results == []
        assert diagnose_cmd.has_failures is False
        assert diagnose_cmd.has_warnings is False

    def test_add_result_pass(self, diagnose_cmd):
        """Test adding a passing result."""
        diagnose_cmd._add_result("Test", "pass", "Good")

        assert len(diagnose_cmd.results) == 1
        assert diagnose_cmd.results[0].name == "Test"
        assert diagnose_cmd.results[0].status == "pass"
        assert diagnose_cmd.has_failures is False
        assert diagnose_cmd.has_warnings is False

    def test_add_result_fail(self, diagnose_cmd):
        """Test adding a failing result."""
        diagnose_cmd._add_result("Test", "fail", "Bad", suggestions=["Fix it"])

        assert len(diagnose_cmd.results) == 1
        assert diagnose_cmd.results[0].status == "fail"
        assert diagnose_cmd.has_failures is True
        assert diagnose_cmd.has_warnings is False

    def test_add_result_warn(self, diagnose_cmd):
        """Test adding a warning result."""
        diagnose_cmd._add_result("Test", "warn", "Caution")

        assert len(diagnose_cmd.results) == 1
        assert diagnose_cmd.results[0].status == "warn"
        assert diagnose_cmd.has_failures is False
        assert diagnose_cmd.has_warnings is True

    def test_generate_summary(self, diagnose_cmd):
        """Test summary generation."""
        diagnose_cmd._add_result("Pass 1", "pass", "OK")
        diagnose_cmd._add_result("Pass 2", "pass", "OK")
        diagnose_cmd._add_result("Warn 1", "warn", "Caution")
        diagnose_cmd._add_result("Fail 1", "fail", "Error")

        summary = diagnose_cmd._generate_summary()

        assert summary["total"] == 4
        assert summary["passed"] == 2
        assert summary["warnings"] == 1
        assert summary["failures"] == 1
        assert summary["healthy"] is False
        assert len(summary["results"]) == 4

    @patch("traktor.diagnose.sys.version_info")
    def test_check_environment_python_version_pass(self, mock_version, diagnose_cmd):
        """Test Python version check (passing)."""
        mock_version.major = 3
        mock_version.minor = 10
        mock_version.micro = 0

        diagnose_cmd._check_environment()

        python_checks = [r for r in diagnose_cmd.results if "Python Version" in r.name]
        assert len(python_checks) == 1
        assert python_checks[0].status == "pass"

    @patch("traktor.diagnose.sys.version_info")
    def test_check_environment_python_version_fail(self, mock_version, diagnose_cmd):
        """Test Python version check (failing)."""
        mock_version.major = 3
        mock_version.minor = 7
        mock_version.micro = 0

        diagnose_cmd._check_environment()

        python_checks = [r for r in diagnose_cmd.results if "Python Version" in r.name]
        assert len(python_checks) == 1
        assert python_checks[0].status == "fail"
        assert "3.8" in python_checks[0].suggestions[0]

    def test_check_environment_dependencies(self, diagnose_cmd):
        """Test dependency checks."""
        diagnose_cmd._check_environment()

        dep_checks = [r for r in diagnose_cmd.results if "Dependency" in r.name]
        # Should check for plexapi, requests, dotenv
        assert len(dep_checks) >= 3

    @patch("traktor.diagnose.TRAKT_CLIENT_ID", "test_id")
    @patch("traktor.diagnose.TRAKT_CLIENT_SECRET", "test_secret")
    def test_check_configuration_trakt_pass(self, diagnose_cmd):
        """Test Trakt credentials check (passing)."""
        diagnose_cmd._check_configuration()

        trakt_checks = [r for r in diagnose_cmd.results if "Trakt Credentials" in r.name]
        assert len(trakt_checks) == 1
        assert trakt_checks[0].status == "pass"

    @patch("traktor.diagnose.TRAKT_CLIENT_ID", None)
    @patch("traktor.diagnose.TRAKT_CLIENT_SECRET", None)
    def test_check_configuration_trakt_fail(self, diagnose_cmd):
        """Test Trakt credentials check (failing)."""
        diagnose_cmd._check_configuration()

        trakt_checks = [r for r in diagnose_cmd.results if "Trakt Credentials" in r.name]
        assert len(trakt_checks) == 1
        assert trakt_checks[0].status == "fail"
        assert len(trakt_checks[0].suggestions) > 0

    @patch("traktor.diagnose.requests.get")
    @patch("traktor.diagnose.TRAKT_CLIENT_ID", "test_id")
    def test_check_connectivity_trakt_pass(self, mock_get, diagnose_cmd):
        """Test Trakt API connectivity check (passing)."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        diagnose_cmd._check_connectivity()

        trakt_checks = [r for r in diagnose_cmd.results if "Trakt API" in r.name]
        assert len(trakt_checks) == 1
        assert trakt_checks[0].status == "pass"

    @patch("traktor.diagnose.requests.get")
    @patch("traktor.diagnose.TRAKT_CLIENT_ID", "test_id")
    def test_check_connectivity_trakt_401(self, mock_get, diagnose_cmd):
        """Test Trakt API connectivity check (401 response)."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        diagnose_cmd._check_connectivity()

        trakt_checks = [r for r in diagnose_cmd.results if "Trakt API" in r.name]
        assert len(trakt_checks) == 1
        assert trakt_checks[0].status == "warn"

    @patch("traktor.diagnose.requests.get")
    @patch("traktor.diagnose.TRAKT_CLIENT_ID", "test_id")
    def test_check_connectivity_trakt_connection_error(self, mock_get, diagnose_cmd):
        """Test Trakt API connectivity check (connection error)."""
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError("No connection")

        diagnose_cmd._check_connectivity()

        trakt_checks = [r for r in diagnose_cmd.results if "Trakt API" in r.name]
        assert len(trakt_checks) == 1
        assert trakt_checks[0].status == "fail"
        assert len(trakt_checks[0].suggestions) > 0

    def test_print_summary_with_failures(self, capsys, diagnose_cmd):
        """Test summary printing with failures."""
        diagnose_cmd._add_result("Fail", "fail", "Error", suggestions=["Fix it"])
        diagnose_cmd._add_result("Pass", "pass", "OK")

        diagnose_cmd.print_summary()

        captured = capsys.readouterr()
        assert "DIAGNOSIS SUMMARY" in captured.out
        assert "Failures: 1" in captured.out
        assert "Not healthy" in captured.out

    def test_print_summary_all_pass(self, capsys, diagnose_cmd):
        """Test summary printing when all checks pass."""
        diagnose_cmd._add_result("Pass 1", "pass", "OK")
        diagnose_cmd._add_result("Pass 2", "pass", "OK")

        diagnose_cmd.print_summary()

        captured = capsys.readouterr()
        assert "All checks passed" in captured.out


class TestRunDiagnosis:
    """Tests for the run_diagnosis function."""

    @patch("traktor.diagnose.DiagnoseCommand")
    def test_run_diagnosis_success(self, mock_cmd_class):
        """Test run_diagnosis when healthy."""
        mock_cmd = MagicMock()
        mock_cmd_class.return_value = mock_cmd
        mock_cmd.run_all_checks.return_value = {
            "failures": 0,
            "warnings": 0,
            "passed": 10,
        }

        result = diagnose.run_diagnosis()

        assert result == 0
        mock_cmd.run_all_checks.assert_called_once()
        mock_cmd.print_summary.assert_called_once()

    @patch("traktor.diagnose.DiagnoseCommand")
    def test_run_diagnosis_with_failures(self, mock_cmd_class):
        """Test run_diagnosis with failures."""
        mock_cmd = MagicMock()
        mock_cmd_class.return_value = mock_cmd
        mock_cmd.run_all_checks.return_value = {
            "failures": 2,
            "warnings": 1,
            "passed": 5,
        }

        result = diagnose.run_diagnosis()

        assert result == 1
