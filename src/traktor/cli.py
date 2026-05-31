"""CLI entrypoint."""

import argparse
import json
import sys
import time
from pathlib import Path

from .auto_update import apply_update_and_print, check_and_print_update
from .diagnose import run_diagnosis
from .health_server import HealthServer
from .interactive import (
    confirm_changes,
    is_interactive,
    list_undo_snapshots,
    restore_undo_snapshot,
)
from .log import logger, setup_logging
from .resilience import (
    backup_manager,
    health_checker,
    integrity_checker,
    plex_circuit_breaker,
    trakt_circuit_breaker,
)
from .resource_manager import ResourceManager
from .settings import HEALTH_SERVER_PORT, MAX_WORKERS, ensure_dirs
from .sync import sync_lists


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Sync Trakt liked lists to Plex playlists")
    parser.add_argument(
        "--plex-url",
        "-u",
        help="Plex server URL (can also use PLEX_URL env var)",
        default=None,
    )
    parser.add_argument(
        "--plex-token",
        "-t",
        help="Plex token (can also use PLEX_TOKEN env var)",
        default=None,
    )
    parser.add_argument(
        "--force-auth", action="store_true", help="Force re-authentication with Trakt"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    parser.add_argument(
        "--refresh-cache", action="store_true", help="Force refresh of Plex library cache"
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=MAX_WORKERS,
        help=f"Number of parallel workers (default: {MAX_WORKERS})",
    )

    # Watch sync arguments
    parser.add_argument(
        "--sync-watched",
        action="store_true",
        help="Enable two-way watch status synchronization between Plex and Trakt",
    )
    parser.add_argument(
        "--sync-watched-only",
        action="store_true",
        help="Sync only watch status (skip all lists/collections) - useful for cron jobs",
    )
    parser.add_argument(
        "--watch-direction",
        choices=["both", "plex-to-trakt", "trakt-to-plex"],
        default="both",
        help="Direction for watch sync (default: both)",
    )
    parser.add_argument(
        "--watch-conflict",
        choices=["newest", "plex", "trakt"],
        default="newest",
        help="Conflict resolution strategy for watch sync (default: newest)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview watch sync changes without applying them",
    )
    parser.add_argument(
        "--sync-movies-only",
        action="store_true",
        help="Only sync watch status for movies (skip shows/episodes)",
    )
    parser.add_argument(
        "--sync-shows-only",
        action="store_true",
        help="Only sync watch status for shows/episodes (skip movies)",
    )
    parser.add_argument(
        "--backfill-history",
        action="store_true",
        help="Initial backfill of watch history from both platforms",
    )
    parser.add_argument(
        "--sync-progress",
        action="store_true",
        help="Sync playback progress (resume points) from Trakt to Plex",
    )

    # Collection and Watchlist sync arguments
    parser.add_argument(
        "--sync-collection",
        action="store_true",
        help="Sync Trakt collection to Plex playlist",
    )
    parser.add_argument(
        "--sync-watchlist",
        action="store_true",
        help="Sync Trakt watchlist to Plex playlist",
    )

    # Official lists arguments
    parser.add_argument(
        "--official-lists",
        action="store_true",
        help="Enable Trakt official curated lists sync",
    )

    # Health server command
    parser.add_argument(
        "--serve-health",
        action="store_true",
        help="Start HTTP health check server on port 8080",
    )

    # Diagnose command
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Run self-diagnosis to check configuration and connectivity",
    )

    # Mission-critical commands
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Run health checks and report system status",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Create a backup of all traktor state (config, tokens, cache)",
    )
    parser.add_argument(
        "--backup-restore",
        metavar="BACKUP_PATH",
        help="Restore from a backup directory",
    )
    parser.add_argument(
        "--backup-list",
        action="store_true",
        help="List available backups",
    )
    parser.add_argument(
        "--integrity-check",
        action="store_true",
        help="Verify integrity of config, tokens, and cache files",
    )
    parser.add_argument(
        "--circuit-status",
        action="store_true",
        help="Show circuit breaker status for API clients",
    )

    parser.add_argument(
        "--no-official-lists",
        action="store_true",
        help="Disable official lists even if enabled in config",
    )
    parser.add_argument(
        "--official-endpoints",
        type=str,
        default=None,
        help="Comma-separated list of official endpoints (e.g., 'movies.trending,shows.popular')",
    )
    parser.add_argument(
        "--official-period",
        choices=["daily", "weekly", "monthly", "yearly"],
        default="weekly",
        help="Time period for stats endpoints (default: weekly)",
    )
    parser.add_argument(
        "--official-periods",
        type=str,
        default=None,
        help="Comma-separated periods for multiple playlist generation (e.g., 'weekly,monthly')",
    )
    parser.add_argument(
        "--official-playlist-mode",
        choices=["separate", "merged"],
        default="separate",
        help="Playlist mode: separate per endpoint or merged by type (default: separate)",
    )
    parser.add_argument(
        "--list-source",
        choices=["liked", "official", "both"],
        default=None,  # Will use TRAKTOR_LIST_SOURCE env var or fall back to "official"
        help="Which list sources to sync (default: from TRAKTOR_LIST_SOURCE env var, or 'official')",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for confirmation before making significant changes",
    )
    parser.add_argument(
        "--undo",
        action="store_true",
        help="Undo the most recent operation",
    )

    # Auto-update arguments
    parser.add_argument(
        "--check-update",
        action="store_true",
        help="Check if a new version of traktor is available",
    )
    parser.add_argument(
        "--apply-update",
        action="store_true",
        help="Apply the latest update (with rollback capability)",
    )

    # Logging and diagnostics
    parser.add_argument(
        "--structured-logging",
        action="store_true",
        help="Enable structured JSON logging to file (console remains plain text)",
    )
    parser.add_argument(
        "--performance-report",
        action="store_true",
        help="Print detailed performance report at end of sync",
    )
    parser.add_argument(
        "--max-memory-mb",
        type=int,
        default=None,
        help="Maximum memory usage in MB (default: 512)",
    )
    parser.add_argument(
        "--cpu-throttle",
        type=float,
        default=None,
        help="Target CPU usage percentage (0-100) for throttling",
    )

    return parser.parse_args()


def _run_health_check():
    """Run health checks and print results."""
    print("Running health checks...\n")

    # Register health checks
    def check_cache_exists():
        from .settings import CACHE_DIR

        return CACHE_DIR.exists()

    def check_config_valid():
        from .settings import CONFIG_FILE

        if not CONFIG_FILE.exists():
            return True  # Optional
        try:
            with open(CONFIG_FILE) as f:
                json.load(f)
            return True
        except (json.JSONDecodeError, FileNotFoundError, PermissionError, OSError):
            return False

    health_checker.register("cache", check_cache_exists)
    health_checker.register("config", check_config_valid)

    results = health_checker.check_all()

    status_icon = (
        "✅"
        if results["status"] == "healthy"
        else "⚠️" if results["status"] == "degraded" else "❌"
    )
    print(f"Overall Status: {status_icon} {results['status'].upper()}")
    print(f"Timestamp: {results['timestamp']}\n")

    print("Component Status:")
    for component, status in results["components"].items():
        icon = "✅" if status == "healthy" else "⚠️" if status == "degraded" else "❌"
        print(f"  {icon} {component}: {status}")

    return 0 if results["status"] == "healthy" else 1


def _run_backup(args):
    """Create a backup."""
    print("Creating backup...")
    backup_path = backup_manager.create_backup(reason="manual")
    print(f"✅ Backup created: {backup_path}")
    return 0


def _list_backups():
    """List available backups."""
    backups = backup_manager.list_backups()
    if not backups:
        print("No backups found.")
        return 0

    print(f"Available backups ({len(backups)}):\n")
    for backup in backups:
        print(f"  📦 {backup['name']}")
        print(f"     Created: {backup['created']}")
        print(f"     Reason: {backup['reason']}")
        print(f"     Path: {backup['path']}")
        print()
    return 0


def _restore_backup(backup_path):
    """Restore from a backup."""
    path = Path(backup_path)
    if not path.exists():
        print(f"❌ Backup not found: {backup_path}")
        return 1

    print(f"Restoring from: {backup_path}")
    if backup_manager.restore_backup(path):
        print("✅ Restore completed successfully")
        return 0
    else:
        print("❌ Restore failed")
        return 1


def _run_integrity_check():
    """Run integrity checks."""
    print("Running integrity checks...\n")
    results = integrity_checker.run_all_checks()

    if results["overall_healthy"]:
        print("✅ All integrity checks passed")
    else:
        print("❌ Some integrity checks failed")

    print(f"\nTimestamp: {results['timestamp']}\n")

    for check_name, check_result in results["checks"].items():
        status = "✅" if check_result["healthy"] else "❌"
        print(f"{status} {check_name}")
        details = check_result.get("details", {})
        for key, value in details.items():
            print(f"   {key}: {value}")
        print()

    return 0 if results["overall_healthy"] else 1


def _show_circuit_status():
    """Show circuit breaker status."""
    print("Circuit Breaker Status\n")

    trakt_stats = trakt_circuit_breaker.get_stats()
    plex_stats = plex_circuit_breaker.get_stats()

    for stats in [trakt_stats, plex_stats]:
        state_icon = {
            "closed": "🟢",
            "open": "🔴",
            "half_open": "🟡",
        }.get(stats["state"], "⚪")

        print(f"{state_icon} {stats['name']}")
        print(f"   State: {stats['state']}")
        print(f"   Failures: {stats['failure_count']}/{stats['failure_threshold']}")
        print(f"   Cooldown: {stats['cooldown_seconds']}s")
        if stats["last_failure"]:
            print(f"   Last failure: {stats['last_failure']}")
        print()

    return 0


def _handle_undo() -> int:
    """Handle the --undo command."""
    print("Undo Operations")
    print("=" * 60)

    snapshots = list_undo_snapshots()
    if not snapshots:
        print("No undo snapshots available.")
        print("Snapshots are created automatically when using --interactive mode.")
        return 1

    # Show most recent snapshot
    snapshot = restore_undo_snapshot()
    if not snapshot:
        print("Failed to load undo snapshot.")
        return 1

    operation_type = snapshot.get("operation_type", "unknown")
    timestamp = snapshot.get("timestamp", "unknown")

    print(f"Most recent snapshot: {operation_type} ({timestamp})")
    print(
        "\nUndo requires restoring from a previous state. "
        "Please run the sync again with the appropriate settings."
    )
    print("\nNote: For playlist changes, the previous playlist items are stored in the snapshot.")
    print("For watch sync changes, the previous watch state is preserved.")

    if not is_interactive():
        print("\nNon-interactive mode: showing snapshot info only.")
        return 0

    confirmed = confirm_changes(
        "Restore from this snapshot on next sync?",
        default=False,
    )
    if confirmed:
        print("\nSnapshot will be used for restoration on next sync run.")
        return 0
    else:
        print("\nUndo cancelled.")
        return 1


def main():
    """Main entry point."""
    args = parse_args()
    ensure_dirs()
    setup_logging(verbose=args.verbose, structured=args.structured_logging)

    # Handle health server command first (before any other processing)
    if args.serve_health:
        server = HealthServer(port=HEALTH_SERVER_PORT)
        server.start()
        print(f"Health server running on port {HEALTH_SERVER_PORT}")
        print("Endpoints: /health, /metrics, /status")
        print("Press Ctrl+C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down health server...")
            server.stop()
            sys.exit(0)

    # Handle diagnose command first (before any other processing)
    if args.diagnose:
        exit_code = run_diagnosis()
        sys.exit(exit_code)

    # Handle mission-critical commands
    if args.health_check:
        sys.exit(_run_health_check())

    if args.backup:
        sys.exit(_run_backup(args))

    if args.backup_list:
        sys.exit(_list_backups())

    if args.backup_restore:
        sys.exit(_restore_backup(args.backup_restore))

    if args.integrity_check:
        sys.exit(_run_integrity_check())

    if args.circuit_status:
        sys.exit(_show_circuit_status())

    # Handle undo command
    if args.undo:
        sys.exit(_handle_undo())

    # Handle auto-update commands
    if args.check_update:
        sys.exit(check_and_print_update())

    if args.apply_update:
        sys.exit(apply_update_and_print())

    logger.info("Command line arguments parsed")

    # Mask sensitive arguments for logging
    def mask_value(k, v):
        if not v:
            return v
        if k == "plex_token":
            return "***"
        if k == "plex_url" and isinstance(v, str):
            # Mask credentials in URL if present (http://user:pass@host -> http://***@host)
            if "://" in v and "@" in v:
                try:
                    from urllib.parse import urlparse, urlunparse

                    parsed = urlparse(v)
                    if parsed.username or parsed.password:
                        # Rebuild URL without credentials
                        masked_netloc = parsed.hostname
                        if parsed.port:
                            masked_netloc += f":{parsed.port}"
                        return urlunparse(
                            (
                                parsed.scheme,
                                masked_netloc,
                                parsed.path,
                                parsed.params,
                                parsed.query,
                                parsed.fragment,
                            )
                        )
                except Exception:
                    logger.debug(f"URL parsing failed for {v}, using original")
                    # If parsing fails, return original
        return v

    safe_args = {k: mask_value(k, v) for k, v in vars(args).items()}
    logger.debug(f"Arguments: {safe_args}")

    # Initialize resource manager if memory or CPU limits are set
    resource_manager = None
    if args and (args.max_memory_mb is not None or args.cpu_throttle is not None):
        resource_manager = ResourceManager(max_memory_mb=args.max_memory_mb)
        if args.max_memory_mb is not None:
            resource_manager.set_memory_limit(args.max_memory_mb)
        if args.cpu_throttle is not None:
            resource_manager.start_cpu_throttle(target_cpu_percent=args.cpu_throttle)

    try:
        exit_code = sync_lists(args, resource_manager=resource_manager)
        if exit_code:
            sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.warning("Operation cancelled by user (KeyboardInterrupt)")
        print("\n\nOperation cancelled by user.")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Unhandled exception: {e}", exc_info=True)
        print(f"\nError: {e}")
        sys.exit(1)
