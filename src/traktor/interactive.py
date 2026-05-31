"""Interactive mode helpers for user confirmation and undo operations."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .log import logger
from .settings import DATA_DIR

# Constants
MAX_UNDO_SNAPSHOTS = 5
UNDO_SNAPSHOT_DIR_NAME = "undo"
DEFAULT_SNAPSHOT_VERSION = 1


def is_interactive() -> bool:
    """Check if running in an interactive terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def confirm_changes(
    description: str,
    items: list[str] | None = None,
    default: bool = True,
) -> bool:
    """Prompt user for confirmation before making changes.

    Args:
        description: Short description of the operation.
        items: Optional list of items to display.
        default: Default choice if user just presses Enter.

    Returns:
        True if user confirms, False otherwise.
    """
    if not is_interactive():
        logger.warning(f"Non-interactive mode: skipping confirmation for '{description}'")
        return True

    print(f"\n{'=' * 60}")
    print(f"Confirmation Required: {description}")
    print(f"{'=' * 60}")

    if items:
        print(f"\nThe following {len(items)} items will be affected:")
        for item in items[:10]:
            print(f"  - {item}")
        if len(items) > 10:
            print(f"  ... and {len(items) - 10} more")

    prompt = "Proceed? [Y/n]: " if default else "Proceed? [y/N]: "
    try:
        response = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return False

    if not response:
        return default

    return response in ("y", "yes")


def preview_changes(
    changes: dict[str, Any],
    change_type: str = "watch_sync",
) -> bool:
    """Preview changes and ask for user confirmation.

    Args:
        changes: Dictionary of changes to preview.
        change_type: Type of changes being previewed.

    Returns:
        True if user confirms, False otherwise.
    """
    if not is_interactive():
        logger.warning(f"Non-interactive mode: skipping preview for '{change_type}'")
        return True

    print(f"\n{'=' * 60}")
    print(f"Preview: {change_type.replace('_', ' ').title()}")
    print(f"{'=' * 60}")

    total_changes = 0

    if change_type == "watch_sync":
        plex_watched = len(changes.get("plex", {}).get("mark_watched", []))
        plex_unwatched = len(changes.get("plex", {}).get("mark_unwatched", []))
        trakt_watched = len(changes.get("trakt", {}).get("mark_watched", []))
        trakt_unwatched = len(changes.get("trakt", {}).get("mark_unwatched", []))

        total_changes = plex_watched + plex_unwatched + trakt_watched + trakt_unwatched

        if total_changes == 0:
            print("No changes to apply.")
            return True

        print(f"\nSummary: Will apply {total_changes} changes")
        if plex_watched:
            print(f"  Plex: Mark {plex_watched} item(s) as watched")
        if plex_unwatched:
            print(f"  Plex: Mark {plex_unwatched} item(s) as unwatched")
        if trakt_watched:
            print(f"  Trakt: Mark {trakt_watched} item(s) as watched")
        if trakt_unwatched:
            print(f"  Trakt: Mark {trakt_unwatched} item(s) as unwatched")

        print("\nDetails:")
        for platform in ("plex", "trakt"):
            for action in ("mark_watched", "mark_unwatched"):
                items = changes.get(platform, {}).get(action, [])
                for item in items[:5]:
                    title = item.get("title", "Unknown")
                    action_label = "watched" if action == "mark_watched" else "unwatched"
                    print(f"  [{platform.upper()}] {title} -> {action_label}")
                if len(items) > 5:
                    print(f"  ... and {len(items) - 5} more on {platform}")

    elif change_type == "playlist_cleanup":
        playlists = changes.get("playlists", [])
        total_changes = len(playlists)

        if total_changes == 0:
            print("No orphaned playlists to delete.")
            return True

        print(f"\nWill delete {total_changes} orphaned playlist(s):")
        for name in playlists[:10]:
            print(f"  - {name}")
        if len(playlists) > 10:
            print(f"  ... and {len(playlists) - 10} more")

    else:
        total_changes = changes.get("total", 0)
        print(f"\nSummary: Will apply {total_changes} changes")
        items = changes.get("items", [])
        for item in items[:10]:
            print(f"  - {item}")
        if len(items) > 10:
            print(f"  ... and {len(items) - 10} more")

    print()
    return confirm_changes(
        f"Apply these {total_changes} changes?",
        default=True,
    )


def _get_undo_dir() -> Path:
    """Get the undo snapshot directory."""
    undo_dir = DATA_DIR / ".traktor" / UNDO_SNAPSHOT_DIR_NAME
    undo_dir.mkdir(parents=True, exist_ok=True)
    return undo_dir


def _generate_snapshot_filename() -> str:
    """Generate a unique snapshot filename."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return f"snapshot_{timestamp}.json"


def save_undo_snapshot(
    operation_type: str,
    data: dict[str, Any],
) -> Path | None:
    """Save an undo snapshot.

    Args:
        operation_type: Type of operation (e.g., "playlist_sync", "watch_sync").
        data: Snapshot data to save.

    Returns:
        Path to the saved snapshot file, or None if save failed.
    """
    snapshot = {
        "version": DEFAULT_SNAPSHOT_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation_type": operation_type,
        "data": data,
    }

    try:
        undo_dir = _get_undo_dir()
        snapshot_file = undo_dir / _generate_snapshot_filename()
        with snapshot_file.open("w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        logger.info(f"Saved undo snapshot: {snapshot_file}")
    except (OSError, TypeError) as e:
        logger.error(f"Failed to save undo snapshot: {e}")
        return None
    else:
        cleanup_old_undo_snapshots()
        return snapshot_file


def restore_undo_snapshot(
    snapshot_path: Path | None = None,
) -> dict[str, Any] | None:
    """Restore an undo snapshot.

    Args:
        snapshot_path: Path to specific snapshot, or None for most recent.

    Returns:
        Snapshot dict if restored, None if no snapshot found.
    """
    if snapshot_path is None:
        snapshots = list_undo_snapshots()
        if not snapshots:
            logger.info("No undo snapshots found")
            return None
        snapshot_path = snapshots[0]

    snapshot_path = Path(snapshot_path)
    if not snapshot_path.exists():
        logger.error(f"Undo snapshot not found: {snapshot_path}")
        return None

    try:
        with snapshot_path.open("r", encoding="utf-8") as f:
            snapshot = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Failed to restore undo snapshot: {e}")
        return None
    else:
        logger.info(f"Restored undo snapshot: {snapshot_path}")
        return snapshot


def list_undo_snapshots() -> list[Path]:
    """List available undo snapshots, sorted by modification time (newest first).

    Returns:
        List of snapshot file paths.
    """
    undo_dir = _get_undo_dir()
    if not undo_dir.exists():
        return []

    snapshots = sorted(
        undo_dir.glob("snapshot_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return snapshots


def cleanup_old_undo_snapshots(max_snapshots: int = MAX_UNDO_SNAPSHOTS) -> None:
    """Remove old undo snapshots, keeping only the most recent ones.

    Args:
        max_snapshots: Maximum number of snapshots to keep.
    """
    snapshots = list_undo_snapshots()
    if len(snapshots) <= max_snapshots:
        return

    to_remove = snapshots[max_snapshots:]
    for snapshot in to_remove:
        try:
            snapshot.unlink()
            logger.debug(f"Removed old undo snapshot: {snapshot}")
        except OSError as e:
            logger.warning(f"Failed to remove old undo snapshot {snapshot}: {e}")

    logger.info(f"Cleaned up {len(to_remove)} old undo snapshot(s)")
