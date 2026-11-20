"""Auto-update module for traktor.

Provides safe update checking with rollback capability. Download and apply
operations are intentionally stubbed — users should update via git/uv.
"""

from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path
from typing import Any

import requests

from .log import logger
from .utils import sanitize_terminal_text

# GitHub API URL for latest release
UPDATE_CHECK_URL = "https://api.github.com/repos/SwordfishTrumpet/traktor/releases/latest"
# Releases are distributed as the wheel attached to the GitHub Release, not
# through a package index, so this is where an update is fetched from.
RELEASE_DOWNLOAD_URL = "https://github.com/SwordfishTrumpet/traktor/releases/latest/download"
WHEEL_NAME_TEMPLATE = "traktor-{version}-py3-none-any.whl"

# Constants
UPDATE_TIMEOUT_SECONDS = 10
MAX_VERSION_PARTS = 3

tomllib: types.ModuleType | None
if importlib.util.find_spec("tomllib"):
    import tomllib
elif importlib.util.find_spec("tomli"):
    tomllib = __import__("tomli")
else:
    tomllib = None


def release_wheel_url(version: str) -> str:
    """Return the download URL of the wheel attached to a release.

    A release here is a tag plus a GitHub Release whose assets are the built
    wheel and sdist, so the wheel's URL is the update path a user can paste.

    Args:
        version: Release version, e.g. ``1.1.1`` or ``v1.1.1``

    Returns:
        Absolute URL of that release's wheel
    """
    bare = version[1:] if version.startswith("v") else version
    return f"{RELEASE_DOWNLOAD_URL}/{WHEEL_NAME_TEMPLATE.format(version=bare)}"


class AutoUpdater:
    """Check for and report available updates."""

    def __init__(self) -> None:
        self.current_version: str = self._get_current_version()
        self.latest_version: str = ""
        self.release_info: dict[str, Any] = {}

    def _get_current_version(self) -> str:
        """Get current version from pyproject.toml or package."""
        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        if pyproject.exists() and tomllib is not None:
            try:
                with pyproject.open("rb") as f:
                    data = tomllib.load(f)
                    return data.get("project", {}).get("version", "unknown")
            except (OSError, ValueError):
                pass
        return "unknown"

    def check_for_update(self) -> dict[str, Any]:
        """Check if a new version is available.

        Returns dict with: current_version, latest_version, update_available,
        release_url, release_notes.
        """
        try:
            response = requests.get(
                UPDATE_CHECK_URL,
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "traktor-updater",
                },
                timeout=UPDATE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            self.latest_version = data.get("tag_name", "unknown")
            self.release_info = data

            update_available = self._version_compare(self.latest_version, self.current_version) > 0

            return {
                "current_version": self.current_version,
                "latest_version": self.latest_version,
                "update_available": update_available,
                "release_url": data.get("html_url", ""),
                "release_notes": data.get("body", ""),
            }
        except (requests.RequestException, json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to check for updates: {e}")
            return {
                "current_version": self.current_version,
                "latest_version": "unknown",
                "update_available": False,
                "error": str(e),
            }

    def _version_compare(self, v1: str, v2: str) -> int:
        """Compare two version strings.

        Returns >0 if v1 > v2, <0 if v1 < v2, 0 if equal.
        """
        v1 = v1.lstrip("v")
        v2 = v2.lstrip("v")

        def parse(v: str) -> list[int]:
            parts = v.split(".")
            return [int(p) if p.isdigit() else 0 for p in parts[:MAX_VERSION_PARTS]]

        p1 = parse(v1)
        p2 = parse(v2)
        for i in range(max(len(p1), len(p2))):
            a = p1[i] if i < len(p1) else 0
            b = p2[i] if i < len(p2) else 0
            if a != b:
                return a - b
        return 0

    def download_update(self, target_dir: Path) -> bool:
        """Download latest release tarball/zip to target_dir."""
        logger.info(f"Download update to {target_dir} (not implemented)")
        return False

    def apply_update(self) -> bool:
        """Apply downloaded update with rollback capability.

        Steps:
        1. Create backup of current installation
        2. Download new version
        3. Replace files
        4. If failed, restore backup
        """
        logger.info("Applying update (not implemented)")
        return False

    def rollback(self) -> bool:
        """Rollback to previous version."""
        logger.info("Rolling back update (not implemented)")
        return False


def check_and_print_update() -> int:
    """Check for updates and print results.

    Every field comes from a GitHub response, so each one is rendered through
    :func:`sanitize_terminal_text` before it reaches stdout: a release body
    carrying control bytes could otherwise rewrite the user's screen or set the
    clipboard (issue #43).
    """
    updater = AutoUpdater()
    result = updater.check_for_update()

    if "error" in result:
        print(f"❌ Failed to check for updates: {sanitize_terminal_text(result['error'])}")
        return 1

    print(f"Current version: {sanitize_terminal_text(result['current_version'])}")
    print(f"Latest version:  {sanitize_terminal_text(result['latest_version'])}")

    if result["update_available"]:
        print("\n🎉 A new version is available!")
        print(f"Release URL: {sanitize_terminal_text(result['release_url'])}")
        if result["release_notes"]:
            print("\nRelease notes:")
            print(sanitize_terminal_text(result["release_notes"]))
        print_update_commands(result["latest_version"])
        return 0

    print("\n✅ You are running the latest version.")
    return 0


def print_update_commands(version: str) -> None:
    """Print how to update to ``version``: pull a checkout, or install its wheel.

    Releases are not published to a package index: the release workflow attaches
    the wheel to the GitHub Release, so that artifact is what an update installs and
    `uv pip install -U traktor` would fetch nothing.

    Args:
        version: Version to update to
    """
    safe_version = sanitize_terminal_text(version)
    print("\nTo update a checkout of this repository: git pull && uv sync")
    print("To install the release wheel instead:")
    print(f'  uv pip install "{sanitize_terminal_text(release_wheel_url(safe_version))}"')


def apply_update_and_print() -> int:
    """Apply update (stub)."""
    updater = AutoUpdater()
    result = updater.check_for_update()

    if "error" in result:
        print(f"❌ Failed to check for updates: {sanitize_terminal_text(result['error'])}")
        return 1

    if not result["update_available"]:
        print("✅ No update available.")
        return 0

    print(
        f"Applying update from {sanitize_terminal_text(result['current_version'])} "
        f"to {sanitize_terminal_text(result['latest_version'])}..."
    )
    success = updater.apply_update()
    if not success:
        print("❌ Auto-update is not implemented. Please update manually:")
        print_update_commands(result["latest_version"])
        return 1
    return 0
