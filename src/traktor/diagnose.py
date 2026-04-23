"""Self-diagnosis module for troubleshooting traktor issues.

Provides comprehensive system checks for:
- Environment and dependencies
- Configuration validation
- API connectivity (Trakt, Plex)
- Common issues and suggestions
"""

import os
import platform
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

from .config import load_config
from .log import logger
from .settings import (
    CACHE_DIR,
    CONFIG_FILE,
    DOCKER_MODE,
    LOG_FILE,
    TRAKT_CLIENT_ID,
    TRAKT_CLIENT_SECRET,
)


@dataclass
class CheckResult:
    """Result of a single diagnostic check."""

    name: str
    status: str  # "pass", "fail", "warn"
    message: str
    details: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)


class DiagnoseCommand:
    """Self-diagnosis command for troubleshooting."""

    def __init__(self):
        self.results: List[CheckResult] = []
        self.has_failures = False
        self.has_warnings = False

    def run_all_checks(self) -> Dict:
        """Run all diagnostic checks and return summary.

        Returns:
            Dict with check results and summary
        """
        logger.info("=" * 80)
        logger.info("Starting traktor self-diagnosis")
        logger.info("=" * 80)

        # Run all check categories
        self._check_environment()
        self._check_configuration()
        self._check_connectivity()
        self._check_common_issues()

        # Generate summary
        summary = self._generate_summary()

        logger.info("=" * 80)
        logger.info("Diagnosis complete")
        logger.info("=" * 80)

        return summary

    def _check_environment(self):
        """Check system environment and dependencies."""
        print("\n🔍 Checking environment...")

        # Python version
        python_version = sys.version_info
        version_str = f"{python_version.major}.{python_version.minor}.{python_version.micro}"

        if python_version.major == 3 and python_version.minor >= 8:
            self._add_result(
                "Python Version",
                "pass",
                f"Python {version_str} (supported)",
            )
        else:
            self._add_result(
                "Python Version",
                "fail",
                f"Python {version_str} (requires 3.8+)",
                suggestions=["Upgrade to Python 3.8 or higher"],
            )

        # Check required dependencies
        required_deps = [
            ("plexapi", "PlexAPI library"),
            ("requests", "HTTP requests library"),
            ("dotenv", "Environment file loading"),
        ]

        for module_name, description in required_deps:
            try:
                __import__(module_name)
                self._add_result(
                    f"Dependency: {description}",
                    "pass",
                    f"{module_name} installed",
                )
            except ImportError:
                self._add_result(
                    f"Dependency: {description}",
                    "fail",
                    f"{module_name} not found",
                    suggestions=["Install with: uv sync"],
                )

        # Check optional dependencies
        optional_deps = [
            ("pytest", "Testing framework (dev)"),
        ]

        for module_name, description in optional_deps:
            try:
                __import__(module_name)
                self._add_result(
                    f"Optional: {description}",
                    "pass",
                    f"{module_name} installed",
                )
            except ImportError:
                self._add_result(
                    f"Optional: {description}",
                    "warn",
                    f"{module_name} not installed",
                    suggestions=["Install dev dependencies: uv sync --extra dev"],
                )

        # Platform info
        self._add_result(
            "Platform",
            "pass",
            f"{platform.system()} {platform.release()} ({platform.machine()})",
        )

        # Docker mode
        if DOCKER_MODE:
            self._add_result(
                "Docker Mode",
                "pass",
                "Running in Docker container",
            )

    def _check_configuration(self):
        """Check configuration files and credentials."""
        print("\n⚙️  Checking configuration...")

        # Check Trakt credentials
        if TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET:
            self._add_result(
                "Trakt Credentials",
                "pass",
                "TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET are set",
            )
        elif TRAKT_CLIENT_ID or TRAKT_CLIENT_SECRET:
            self._add_result(
                "Trakt Credentials",
                "fail",
                "Only one Trakt credential set (need both ID and SECRET)",
                suggestions=[
                    "Set both TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET",
                    "Get credentials from: https://trakt.tv/oauth/applications",
                ],
            )
        else:
            self._add_result(
                "Trakt Credentials",
                "fail",
                "TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET not set",
                suggestions=[
                    "Copy .env.example to .env and fill in your credentials",
                    "Get credentials from: https://trakt.tv/oauth/applications",
                ],
            )

        # Check Plex credentials from environment
        plex_url = os.getenv("PLEX_URL")
        plex_token = os.getenv("PLEX_TOKEN")

        if plex_url and plex_token:
            self._add_result(
                "Plex Credentials (Environment)",
                "pass",
                "PLEX_URL and PLEX_TOKEN are set",
            )
        elif plex_url or plex_token:
            self._add_result(
                "Plex Credentials (Environment)",
                "fail",
                "Only one Plex credential set in environment",
                suggestions=["Set both PLEX_URL and PLEX_TOKEN, or neither (to use saved config)"],
            )
        else:
            self._add_result(
                "Plex Credentials (Environment)",
                "warn",
                "Plex credentials not in environment (will use saved config or prompt)",
                suggestions=[
                    "Set PLEX_URL and PLEX_TOKEN in .env file for convenience",
                    "Or run interactively to save credentials",
                ],
            )

        # Check config file
        config = load_config()
        if CONFIG_FILE.exists():
            self._add_result(
                "Config File",
                "pass",
                f"Config file exists ({CONFIG_FILE})",
            )

            # Check for managed playlists
            managed = config.get("managed_playlists", [])
            if managed:
                self._add_result(
                    "Managed Playlists",
                    "pass",
                    f"{len(managed)} playlist(s) being tracked",
                )
        else:
            self._add_result(
                "Config File",
                "warn",
                "No config file found (will be created on first run)",
            )

        # Check directories
        for name, path in [
            ("Cache Directory", CACHE_DIR),
            ("Log Directory", LOG_FILE.parent),
        ]:
            if path.exists():
                self._add_result(
                    name,
                    "pass",
                    f"{path} exists",
                )
            else:
                self._add_result(
                    name,
                    "warn",
                    f"{path} does not exist (will be created)",
                )

    def _check_connectivity(self):
        """Check API connectivity."""
        print("\n🌐 Checking connectivity...")

        # Check Trakt API connectivity
        if TRAKT_CLIENT_ID:
            try:
                response = requests.get(
                    "https://api.trakt.tv/movies/trending",
                    headers={
                        "Content-Type": "application/json",
                        "trakt-api-version": "2",
                        "trakt-api-key": TRAKT_CLIENT_ID,
                    },
                    timeout=10,
                )

                if response.status_code == 200:
                    self._add_result(
                        "Trakt API Connectivity",
                        "pass",
                        "Successfully connected to Trakt API",
                    )
                elif response.status_code == 401:
                    self._add_result(
                        "Trakt API Connectivity",
                        "warn",
                        "Trakt API returned 401 (authentication may be needed)",
                        suggestions=["Run with --force-auth to re-authenticate"],
                    )
                else:
                    self._add_result(
                        "Trakt API Connectivity",
                        "warn",
                        f"Trakt API returned status {response.status_code}",
                    )
            except requests.exceptions.ConnectionError:
                self._add_result(
                    "Trakt API Connectivity",
                    "fail",
                    "Cannot connect to Trakt API (connection error)",
                    suggestions=["Check your internet connection", "Verify Trakt is not down"],
                )
            except requests.exceptions.Timeout:
                self._add_result(
                    "Trakt API Connectivity",
                    "fail",
                    "Trakt API connection timed out",
                    suggestions=["Check your internet connection", "Try again later"],
                )
            except Exception as e:
                self._add_result(
                    "Trakt API Connectivity",
                    "fail",
                    f"Error checking Trakt API: {e}",
                )
        else:
            self._add_result(
                "Trakt API Connectivity",
                "warn",
                "Skipped (no TRAKT_CLIENT_ID set)",
            )

        # Check Plex connectivity if credentials available
        plex_url = os.getenv("PLEX_URL")
        plex_token = os.getenv("PLEX_TOKEN")

        if plex_url:
            try:
                response = requests.get(
                    f"{plex_url}/",
                    headers={"X-Plex-Token": plex_token} if plex_token else {},
                    timeout=10,
                )

                if response.status_code == 200:
                    self._add_result(
                        "Plex Server Connectivity",
                        "pass",
                        f"Successfully connected to Plex at {plex_url}",
                    )

                    # Check user permissions if we can connect
                    self._check_plex_user_permissions(plex_url, plex_token)

                elif response.status_code == 401:
                    self._add_result(
                        "Plex Server Connectivity",
                        "fail",
                        "Plex returned 401 (invalid token)",
                        suggestions=[
                            "Check your PLEX_TOKEN",
                            "Get token from: https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/",
                        ],
                    )
                else:
                    self._add_result(
                        "Plex Server Connectivity",
                        "warn",
                        f"Plex returned status {response.status_code}",
                    )
            except requests.exceptions.ConnectionError:
                self._add_result(
                    "Plex Server Connectivity",
                    "fail",
                    f"Cannot connect to Plex at {plex_url}",
                    suggestions=[
                        "Verify PLEX_URL is correct",
                        "Check that Plex server is running",
                        "Check firewall/network settings",
                    ],
                )
            except Exception as e:
                self._add_result(
                    "Plex Server Connectivity",
                    "fail",
                    f"Error checking Plex: {e}",
                )
        else:
            self._add_result(
                "Plex Server Connectivity",
                "warn",
                "Skipped (no PLEX_URL set)",
            )

    def _check_plex_user_permissions(self, plex_url: str, plex_token: str):
        """Check if the token belongs to the server owner or a managed user.

        Playlists created by the owner are visible to all users.
        Playlists created by managed users are private to them.
        """
        try:
            # Import PlexAPI here to avoid heavy import during basic checks
            from plexapi.server import PlexServer

            server = PlexServer(plex_url, plex_token)

            # Try to get account information
            try:
                my_plex = server.myPlexAccount()
                username = getattr(my_plex, "username", "unknown")

                # Check resources to see if we own this server
                is_owner = False
                for resource in my_plex.resources():
                    if (
                        hasattr(resource, "clientIdentifier")
                        and resource.clientIdentifier == server.machineIdentifier
                    ):
                        is_owner = getattr(resource, "owned", False)
                        break

                if is_owner:
                    self._add_result(
                        "Plex User Permissions",
                        "pass",
                        f"Connected as server OWNER ({username})",
                        details="Playlists will be visible to ALL users on the server",
                    )
                else:
                    self._add_result(
                        "Plex User Permissions",
                        "warn",
                        f"Connected as MANAGED USER ({username})",
                        details="Playlists will be PRIVATE to you only - other users won't see them",
                        suggestions=[
                            "Use the SERVER OWNER's token to create playlists visible to all users",
                            "Owner token can be found in Plex Web > Settings > General",
                        ],
                    )
            except Exception as e:
                # Could not determine account info
                logger.debug(f"Could not check Plex account info: {e}")

        except Exception as e:
            logger.debug(f"Could not check Plex user permissions: {e}")

    def _check_common_issues(self):
        """Check for common configuration issues."""
        print("\n🔎 Checking for common issues...")

        # Check if running in non-interactive mode without saved credentials
        plex_url = os.getenv("PLEX_URL")
        plex_token = os.getenv("PLEX_TOKEN")

        if not sys.stdin.isatty():
            issues = []

            # Note: Trakt OAuth requires interactive mode for initial auth
            # After authentication, tokens are kept in memory only

            if not plex_url or not plex_token:
                config = load_config()
                if "plex_url" not in config:
                    issues.append("No saved Plex credentials (required for non-interactive mode)")

            if issues:
                self._add_result(
                    "Non-Interactive Mode",
                    "warn",
                    "Running in non-interactive mode with missing credentials",
                    details="; ".join(issues),
                    suggestions=[
                        "Run interactively first to save credentials",
                        "Or set all required environment variables",
                    ],
                )
            else:
                self._add_result(
                    "Non-Interactive Mode",
                    "pass",
                    "Running in non-interactive mode with saved credentials",
                )

        # Check cache size
        if CACHE_DIR.exists():
            try:
                total_size = 0
                for dirpath, dirnames, filenames in os.walk(CACHE_DIR):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        total_size += os.path.getsize(fp)

                size_mb = total_size / (1024 * 1024)

                if size_mb > 100:
                    self._add_result(
                        "Cache Size",
                        "warn",
                        f"Cache is {size_mb:.1f} MB (consider clearing with --refresh-cache)",
                    )
                else:
                    self._add_result(
                        "Cache Size",
                        "pass",
                        f"Cache is {size_mb:.1f} MB",
                    )
            except Exception as e:
                logger.debug(f"Could not calculate cache size: {e}")

        # Check log file size
        if LOG_FILE.exists():
            size_mb = LOG_FILE.stat().st_size / (1024 * 1024)

            if size_mb > 50:
                self._add_result(
                    "Log File Size",
                    "warn",
                    f"Log file is {size_mb:.1f} MB (rotates at 5MB, but may have many backups)",
                    suggestions=["Clear old log files from ~/.traktor/ if needed"],
                )
            else:
                self._add_result(
                    "Log File Size",
                    "pass",
                    f"Log file is {size_mb:.1f} MB",
                )

    def _add_result(
        self,
        name: str,
        status: str,
        message: str,
        details: Optional[str] = None,
        suggestions: Optional[List[str]] = None,
    ):
        """Add a check result."""
        result = CheckResult(
            name=name,
            status=status,
            message=message,
            details=details,
            suggestions=suggestions or [],
        )
        self.results.append(result)

        if status == "fail":
            self.has_failures = True
            icon = "❌"
        elif status == "warn":
            self.has_warnings = True
            icon = "⚠️"
        else:
            icon = "✅"

        print(f"  {icon} {name}: {message}")

        if details:
            print(f"     Details: {details}")

        for suggestion in result.suggestions:
            print(f"     💡 {suggestion}")

    def _generate_summary(self) -> Dict:
        """Generate summary of all checks."""
        passed = sum(1 for r in self.results if r.status == "pass")
        warnings = sum(1 for r in self.results if r.status == "warn")
        failures = sum(1 for r in self.results if r.status == "fail")

        summary = {
            "total": len(self.results),
            "passed": passed,
            "warnings": warnings,
            "failures": failures,
            "healthy": not self.has_failures,
            "results": [
                {
                    "name": r.name,
                    "status": r.status,
                    "message": r.message,
                    "details": r.details,
                    "suggestions": r.suggestions,
                }
                for r in self.results
            ],
        }

        return summary

    def print_summary(self):
        """Print a formatted summary of the diagnosis."""
        print("\n" + "=" * 80)
        print("DIAGNOSIS SUMMARY")
        print("=" * 80)

        passed = sum(1 for r in self.results if r.status == "pass")
        warnings = sum(1 for r in self.results if r.status == "warn")
        failures = sum(1 for r in self.results if r.status == "fail")

        print(f"\n  ✅ Passed: {passed}")
        print(f"  ⚠️  Warnings: {warnings}")
        print(f"  ❌ Failures: {failures}")

        if failures > 0:
            print(f"\n  Status: ❌ Not healthy - {failures} critical issue(s) found")
            print("\n  To fix:")
            for result in self.results:
                if result.status == "fail":
                    print(f"    - {result.name}: {result.message}")
                    for suggestion in result.suggestions[:2]:  # Show top 2 suggestions
                        print(f"      💡 {suggestion}")
        elif warnings > 0:
            print(f"\n  Status: ⚠️  Healthy with {warnings} warning(s)")
        else:
            print("\n  Status: ✅ All checks passed - ready to sync!")

        print("\n" + "=" * 80)


def run_diagnosis():
    """Run the diagnosis command and return exit code."""
    diagnose = DiagnoseCommand()
    summary = diagnose.run_all_checks()
    diagnose.print_summary()

    # Return exit code based on health
    if summary["failures"] > 0:
        return 1
    return 0
