"""Mission-critical resilience patterns for traktor.

Provides circuit breakers, health monitoring, automated backups,
and integrity checks for production deployments.
"""

import gzip
import hashlib
import json
import os
import random
import shutil
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .log import logger
from .settings import (
    CACHE_DIR,
    CONFIG_FILE,
    ENV_FILE,
    TRAKTOR_BACKUP_DIR,
    TRAKTOR_MAX_BACKUPS,
    TRAKTOR_PLEX_COOLDOWN_SECONDS,
    TRAKTOR_PLEX_FAILURE_THRESHOLD,
    TRAKTOR_TRAKT_COOLDOWN_SECONDS,
    TRAKTOR_TRAKT_FAILURE_THRESHOLD,
)
from .utils import (
    DecompressionLimitError,
    file_lock,
    iter_gzip_bounded,
    read_gzip_text,
    read_json_object,
    restrict_to_owner,
    write_json_atomic,
    write_text_atomic,
)

# A backup directory name carries microseconds, so two backups in the same
# second land in two directories. The retry covers the pathological case where
# even that collides (issue #67).
BACKUP_PATH_ATTEMPTS = 5

# Backup directories this build creates. Retention deletes only these: it used
# to delete every directory under the backup location, including folders the
# user kept there (issue #84).
BACKUP_DIR_PREFIX = "traktor_backup_"


def _is_backup_dir(path: Path) -> bool:
    """Return True when ``path`` is a backup directory this build created.

    Args:
        path: Candidate directory under the backup location

    Returns:
        True only for a directory whose name this build produces
    """
    try:
        return path.is_dir() and path.name.startswith(BACKUP_DIR_PREFIX)
    except OSError:
        return False


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, rejecting requests
    HALF_OPEN = "half_open"  # Testing if recovered


class CircuitBreaker:
    """Circuit breaker pattern for resilient API calls.

    Prevents cascading failures by stopping requests to a failing service.
    After a cooldown period, it allows test requests to check recovery.

    Example:
        breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
        try:
            result = breaker.call(lambda: api.get_data())
        except CircuitBreakerOpen:
            # Service is down, use fallback
            pass
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        cooldown_seconds: float = 60.0,
        half_open_max_calls: int = 3,
        name: str = "default",
    ):
        """Initialize circuit breaker.

        Args:
            failure_threshold: Failures before opening circuit
            success_threshold: Successes in half-open to close circuit
            cooldown_seconds: Time before attempting recovery
            half_open_max_calls: Max test calls in half-open state
            name: Circuit breaker identifier for logging
        """
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        self.name = name

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            return self._state

    def call(self, func: Callable, fallback: Optional[Callable] = None) -> Any:
        """Execute function with circuit breaker protection.

        Args:
            func: Function to call if circuit allows
            fallback: Optional fallback function if circuit is open

        Returns:
            Result from func or fallback

        Raises:
            CircuitBreakerOpen: If circuit is open and no fallback provided
            Exception: Any exception from func (triggers failure count)
        """
        with self._lock:
            self._transition_state()

            if self._state == CircuitState.OPEN:
                logger.warning(f"[{self.name}] Circuit breaker OPEN - rejecting request")
                if fallback:
                    return fallback()
                raise CircuitBreakerOpen(f"Circuit {self.name} is open")

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    logger.warning(f"[{self.name}] Half-open limit reached - rejecting")
                    if fallback:
                        return fallback()
                    raise CircuitBreakerOpen(f"Circuit {self.name} half-open limit reached")
                self._half_open_calls += 1
                logger.info(
                    f"[{self.name}] Half-open test call {self._half_open_calls}/{self.half_open_max_calls}"
                )

        # Execute outside lock
        try:
            result = func()
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _transition_state(self) -> None:
        """Check and transition circuit state based on time."""
        if self._state == CircuitState.OPEN:
            if self._last_failure_time:
                elapsed = time.time() - self._last_failure_time
                if elapsed >= self.cooldown_seconds:
                    logger.info(f"[{self.name}] Cooldown expired - entering HALF_OPEN")
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    self._success_count = 0

    def _on_success(self) -> None:
        """Record successful call."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                logger.info(
                    f"[{self.name}] Half-open success {self._success_count}/{self.success_threshold}"
                )
                if self._success_count >= self.success_threshold:
                    logger.info(f"[{self.name}] Circuit CLOSED - service recovered")
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
            else:
                # Reset failure count on success in closed state
                if self._failure_count > 0:
                    logger.debug(f"[{self.name}] Resetting failure count after success")
                    self._failure_count = 0

    def _on_failure(self) -> None:
        """Record failed call."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                logger.warning(f"[{self.name}] Half-open failure - entering OPEN")
                self._state = CircuitState.OPEN
            elif self._failure_count >= self.failure_threshold:
                logger.error(
                    f"[{self.name}] Failure threshold reached ({self.failure_threshold}) - OPENING circuit"
                )
                self._state = CircuitState.OPEN

    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics."""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "half_open_calls": self._half_open_calls,
                "failure_threshold": self.failure_threshold,
                "cooldown_seconds": self.cooldown_seconds,
                "last_failure": (
                    datetime.fromtimestamp(self._last_failure_time).isoformat()
                    if self._last_failure_time
                    else None
                ),
            }


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open."""

    pass


class HealthStatus(Enum):
    """Health check status levels."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthCheck:
    """Health monitoring for traktor components.

    Tracks component health and provides aggregated status.
    Useful for monitoring dashboards and alerting.
    """

    # A component stays degraded for its first failures and is only called
    # unhealthy once the failures repeat. The first failure is never healthy: a
    # one-shot process (`traktor --health-check`) has exactly one sample, so the
    # old "healthy until the second failure" rule made it report every broken
    # component as healthy (issue #59).
    UNHEALTHY_AFTER_FAILURES = 3

    def __init__(self):
        """Initialize health checker."""
        self._checks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def register(self, name: str, check_func: Callable[[], bool]) -> None:
        """Register a health check.

        Args:
            name: Component name
            check_func: Function returning True if healthy
        """
        with self._lock:
            self._checks[name] = {
                "func": check_func,
                "last_result": None,
                "last_check": None,
                "consecutive_failures": 0,
                "status": HealthStatus.HEALTHY,
            }

    def _record_result(self, check: Dict[str, Any], healthy: bool) -> HealthStatus:
        """Store one check result and return the component's current status."""
        with self._lock:
            check["last_result"] = healthy
            check["last_check"] = datetime.now(timezone.utc).isoformat()
            if healthy:
                check["consecutive_failures"] = 0
                check["status"] = HealthStatus.HEALTHY
            else:
                check["consecutive_failures"] += 1
                if check["consecutive_failures"] >= self.UNHEALTHY_AFTER_FAILURES:
                    check["status"] = HealthStatus.UNHEALTHY
                else:
                    check["status"] = HealthStatus.DEGRADED
            return check["status"]

    def check(self, name: str) -> HealthStatus:
        """Run a single health check.

        Args:
            name: Component name

        Returns:
            Health status for the component, degraded or unhealthy as soon as
            the check fails
        """
        with self._lock:
            if name not in self._checks:
                return HealthStatus.UNHEALTHY

            check = self._checks[name]

        try:
            return self._record_result(check, bool(check["func"]()))
        except Exception as e:
            logger.error(f"Health check '{name}' failed: {e}")
            return self._record_result(check, False)

    def check_all(self) -> Dict[str, Any]:
        """Run all health checks.

        Returns:
            Dictionary with overall status and per-component results
        """
        results = {}
        worst_status = HealthStatus.HEALTHY

        for name in self._checks:
            status = self.check(name)
            results[name] = status.value
            # Track worst status
            if status == HealthStatus.UNHEALTHY:
                worst_status = HealthStatus.UNHEALTHY
            elif status == HealthStatus.DEGRADED and worst_status != HealthStatus.UNHEALTHY:
                worst_status = HealthStatus.DEGRADED

        return {
            "status": worst_status.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": results,
        }


class BackupManager:
    """Automated backup and restore for traktor state.

    Manages backups of configuration, tokens, and cache with:
    - Scheduled automatic backups
    - Retention policy (keep N backups)
    - Compression for space efficiency
    - Integrity verification
    """

    def __init__(
        self,
        backup_dir: Optional[Path] = None,
        max_backups: Optional[int] = None,
        compress: bool = True,
    ):
        """Initialize backup manager.

        Args:
            backup_dir: Directory for backups (default: TRAKTOR_BACKUP_DIR, else
                ~/.traktor_backups)
            max_backups: Maximum number of backups to retain (default:
                TRAKTOR_MAX_BACKUPS, else 10)
            compress: Whether to gzip compress backups
        """
        if backup_dir is None:
            backup_dir = TRAKTOR_BACKUP_DIR or Path.home() / ".traktor_backups"
        if max_backups is None:
            max_backups = TRAKTOR_MAX_BACKUPS
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.max_backups = max_backups
        self.compress = compress

        self._items_to_backup = [
            ("config", CONFIG_FILE),
            # .env is the live token store (issue #6); the legacy token JSON file
            # is no longer written by current code and is not backed up.
            ("env", ENV_FILE),
            ("cache", CACHE_DIR),
        ]

    def create_backup(self, reason: str = "manual") -> Path:
        """Create a backup of all traktor state.

        The directory is created exclusively (``exist_ok=False``) and its name
        carries microseconds, so a retry or two backups in the same second end
        up as two snapshots instead of the second overwriting the first
        (issue #67).

        Args:
            reason: Reason for backup (manual, scheduled, pre_sync, etc.)

        Returns:
            Path to created backup
        """
        final_path = self._create_backup_dir(reason)
        # Fill a hidden staging directory first, so another run's retention
        # sweep cannot count, or delete, a snapshot whose manifest is not
        # written yet. The directory is published under its real name when it
        # is complete (issue #107).
        working_path = final_path.with_name(f".{final_path.name}.partial")
        try:
            final_path.rename(working_path)
        except OSError as e:
            logger.debug(f"Could not stage backup in {working_path}: {e}")
            working_path = final_path
        backup_path = working_path

        logger.info(f"Creating backup: {final_path.name}")

        # Backup each item
        manifest: Dict[str, Any] = {
            "created": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "version": "1.0.0",
            "items": {},
        }

        for name, source_path in self._items_to_backup:
            if not source_path.exists():
                logger.warning(f"Skipping backup of {name}: not found at {source_path}")
                continue

            dest_path = backup_path / name
            checksum = self._backup_item(source_path, dest_path)
            manifest["items"][name] = {
                "source": str(source_path),
                "checksum": checksum,
                "compressed": self.compress,
            }
            # Every archive is owner-only: config holds the Plex token and the
            # cache holds what the user watched, so the copy must not be more
            # readable than the file it came from (issue #98).
            self._restrict_archive_permissions(dest_path)

        # Write manifest
        manifest_path = backup_path / "manifest.json"
        if not write_json_atomic(manifest_path, manifest, mode=0o600):
            logger.error(f"Failed to write backup manifest: {manifest_path}")
            return backup_path

        # Publish the finished snapshot under its retention-visible name. A
        # snapshot only becomes visible once it can be restored (issue #107).
        if working_path != final_path:
            try:
                working_path.rename(final_path)
            except OSError as e:
                logger.error(f"Could not publish backup {final_path.name}: {e}")
                return working_path
            backup_path = final_path

        # Cleanup old backups
        self._cleanup_old_backups()

        logger.info(f"Backup completed: {backup_path}")
        return backup_path

    def _create_backup_dir(self, reason: str) -> Path:
        """Create a backup directory nothing else can already occupy (issue #67).

        Args:
            reason: Reason for backup, part of the directory name

        Returns:
            The freshly created, uniquely named backup directory

        Raises:
            FileExistsError: When every attempt collided with an existing backup
        """
        for attempt in range(BACKUP_PATH_ATTEMPTS):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            suffix = f"_{attempt}" if attempt else ""
            candidate = self.backup_dir / f"{BACKUP_DIR_PREFIX}{timestamp}_{reason}{suffix}"
            try:
                candidate.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            # The snapshot holds credentials and viewing history, so the
            # directory is owner-only rather than umask-dependent (issue #98).
            try:
                os.chmod(candidate, 0o700)
            except OSError as e:
                logger.warning(f"Could not restrict permissions on {candidate}: {e}")
            return candidate
        raise FileExistsError(
            f"Could not create a unique backup directory under {self.backup_dir} "
            f"after {BACKUP_PATH_ATTEMPTS} attempts"
        )

    def _restrict_archive_permissions(self, archived: Path) -> None:
        """Make a backup archive readable only by its owner (issue #98).

        The archive is created by ``gzip.open``/``copy2`` under the process
        umask, so a 022 umask left the copy of the config and of the cache at
        0644 while the live files are 0600.

        Args:
            archived: Path of the archive just created (file or directory)
        """
        try:
            if archived.is_dir():
                os.chmod(archived, 0o700)
                for path in archived.rglob("*"):
                    if path.is_dir():
                        os.chmod(path, 0o700)
                    else:
                        restrict_to_owner(path)
                return
        except OSError as e:
            logger.warning(f"Could not restrict permissions on {archived}: {e}")
            return
        target = archived.with_suffix(".gz") if self.compress else archived
        if target.exists() and not restrict_to_owner(target):
            logger.warning(f"Could not restrict permissions on {target}")

    @staticmethod
    def _files_in_order(root: Path, strip_compression_suffix: bool = False) -> List[Path]:
        """Return the files under ``root`` sorted by their logical path (issue #42).

        A backup's checksum is computed over the files in this order, so the
        source tree and the backup made from it must be walked identically.
        Filesystem iteration order is not stable, and appending ``.gz`` changed
        it, which made every multi-file backup fail its own verification.

        Args:
            root: Directory to walk
            strip_compression_suffix: Drop the backup's trailing ``.gz`` before
                sorting, so a backup sorts like the tree it was made from

        Returns:
            File paths, sorted by logical relative path
        """

        def logical(path: Path) -> str:
            relative = path.relative_to(root).as_posix()
            if strip_compression_suffix and relative.endswith(".gz"):
                return relative[: -len(".gz")]
            return relative

        return sorted((path for path in root.rglob("*") if path.is_file()), key=logical)

    def _backup_item(self, source: Path, dest: Path) -> str:
        """Backup a single item (file or directory).

        Returns:
            SHA256 checksum of backed up data
        """
        sha256 = hashlib.sha256()

        if source.is_file():
            # Backup single file
            if self.compress:
                dest = dest.with_suffix(".gz")
                with open(source, "rb") as f_in:
                    with gzip.open(dest, "wb") as f_out:
                        data = f_in.read()
                        f_out.write(data)
                        sha256.update(data)
            else:
                shutil.copy2(source, dest)
                with open(source, "rb") as f:
                    sha256.update(f.read())
        else:
            # Backup directory
            dest.mkdir(parents=True, exist_ok=True)
            for item in self._files_in_order(source):
                rel_path = item.relative_to(source)
                item_dest = dest / rel_path
                item_dest.parent.mkdir(parents=True, exist_ok=True)

                if self.compress:
                    item_dest = item_dest.with_suffix(item_dest.suffix + ".gz")
                    with open(item, "rb") as f_in:
                        with gzip.open(item_dest, "wb") as f_out:
                            data = f_in.read()
                            f_out.write(data)
                            sha256.update(data)
                else:
                    shutil.copy2(item, item_dest)
                    with open(item, "rb") as f:
                        sha256.update(f.read())

        return sha256.hexdigest()

    def restore_backup(self, backup_path: Path, verify: bool = True) -> bool:
        """Restore from a backup.

        Only the paths this build knows are restored, and only for item names
        it issued (issue #11). A manifest that names another destination, or an
        item name this build did not write, is refused: a backup that arrived
        from elsewhere must never be able to overwrite or remove an arbitrary
        file.

        Args:
            backup_path: Path to backup directory
            verify: Whether to verify checksums before restoring

        Returns:
            True if restore successful
        """
        logger.info(f"Restoring from backup: {backup_path}")

        manifest_path = backup_path / "manifest.json"
        if not manifest_path.exists():
            logger.error(f"No manifest found in backup: {backup_path}")
            return False

        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read backup manifest: {e}")
            return False

        # The destination of every item comes from this build's own path list,
        # never from the manifest (issue #11). The manifest is attacker-
        # reachable data, so both the item name and the named source are
        # validated before anything is removed or written.
        items = manifest.get("items", {})
        if not isinstance(items, dict):
            logger.error(f"Backup manifest items are malformed: {backup_path}")
            return False

        expected_sources = dict(self._items_to_backup)
        for name, info in items.items():
            expected = expected_sources.get(name)
            if expected is None:
                logger.error(
                    f"Backup carries an unknown item '{name}'; refusing the restore "
                    f"(expected one of: {', '.join(sorted(expected_sources))})"
                )
                return False
            manifest_source = info.get("source") if isinstance(info, dict) else None
            if manifest_source is None or Path(manifest_source) != Path(expected):
                logger.error(
                    f"Backup item '{name}' names '{manifest_source}' but this build restores "
                    f"'{expected}'; refusing the restore"
                )
                return False

        # Verify backup integrity
        if verify:
            for name, info in items.items():
                compressed = bool(info.get("compressed", self.compress))
                if not self._verify_backup_item(
                    backup_path / name, info.get("checksum", ""), compressed
                ):
                    logger.error(f"Backup verification failed for {name}")
                    return False

        # Perform restore
        for name in items:
            source = backup_path / name
            dest = Path(expected_sources[name])

            # Remove existing
            if dest.exists():
                if dest.is_file():
                    dest.unlink()
                else:
                    shutil.rmtree(dest)

            # Restore
            if not self._restore_item(
                source, dest, compressed=bool(info.get("compressed", self.compress))
            ):
                logger.error(f"Restore of '{name}' failed; {dest} may be incomplete")
                return False

            if name == "env":
                # Restrictive permissions on restored credential material
                Path(dest).chmod(0o600)

        logger.info("Restore completed successfully")
        return True

    def _verify_backup_item(
        self, backup_item: Path, expected_checksum: str, compressed: bool = True
    ) -> bool:
        """Verify a backed up item matches its checksum.

        Args:
            backup_item: Path to the backed up file or directory
            expected_checksum: SHA256 recorded in the manifest
            compressed: Whether the backup appended ``.gz`` to every file, which
                the sort order has to undo to match the source walk (issue #42)

        Returns:
            False if the item cannot be read (e.g. corrupt gzip stream) or
            the checksum does not match.
        """
        sha256 = hashlib.sha256()

        try:
            if backup_item.is_file() or backup_item.with_suffix(".gz").exists():
                # Single file
                file_path = backup_item if backup_item.exists() else backup_item.with_suffix(".gz")
                if compressed:
                    # Bounded: a small archive must not expand past the limit
                    # while it is being hashed (issue #81).
                    for chunk in iter_gzip_bounded(file_path):
                        sha256.update(chunk)
                else:
                    with open(file_path, "rb") as f:
                        sha256.update(f.read())
            else:
                # Directory
                for file_path in self._files_in_order(
                    backup_item, strip_compression_suffix=compressed
                ):
                    # The manifest decides whether a file is compressed, not its
                    # name: an uncompressed backup of the library cache carries
                    # a plain "*.json.gz" file (issue #42).
                    if compressed and file_path.suffix == ".gz":
                        for chunk in iter_gzip_bounded(file_path):
                            sha256.update(chunk)
                    else:
                        with open(file_path, "rb") as f:
                            sha256.update(f.read())
        except (OSError, EOFError, DecompressionLimitError) as e:
            logger.error(f"Failed to verify backup item {backup_item}: {e}")
            return False

        return sha256.hexdigest() == expected_checksum

    def _restore_item(self, source: Path, dest: Path, compressed: bool = True) -> bool:
        """Restore a single item from backup.

        Args:
            source: Backed up file or directory
            dest: Destination path
            compressed: Whether the backup appended ``.gz`` to every file, as the
                manifest records; a plain file that happens to end in ``.gz``
                must not be decompressed (issue #42)

        Returns:
            True when the item was restored. A compressed item that expands past
            the size bound (issue #81) stops part way and its partial output is
            removed, so a doctored archive cannot fill the disk.
        """
        try:
            if source.is_file() or source.with_suffix(".gz").exists():
                file_path = source if source.exists() else source.with_suffix(".gz")
                dest.parent.mkdir(parents=True, exist_ok=True)

                if compressed:
                    self._write_gzip_bounded(file_path, dest)
                else:
                    shutil.copy2(file_path, dest)
            else:
                # Directory
                dest.mkdir(parents=True, exist_ok=True)
                for item in source.rglob("*"):
                    if item.is_file():
                        rel_path = item.relative_to(source)
                        item_dest = dest / rel_path
                        item_dest.parent.mkdir(parents=True, exist_ok=True)

                        if compressed:
                            # Remove .gz suffix
                            self._write_gzip_bounded(item, item_dest.with_suffix(""))
                        else:
                            shutil.copy2(item, item_dest)
        except (OSError, EOFError, DecompressionLimitError) as e:
            logger.error(f"Failed to restore {source}: {e}")
            return False
        return True

    @staticmethod
    def _write_gzip_bounded(source: Path, dest: Path) -> None:
        """Write one compressed backup file to ``dest``, bounded while copying.

        The payload is streamed rather than read whole, and a partial file is
        removed before the limit error propagates (issue #81).
        """
        try:
            with open(dest, "wb") as f_out:
                for chunk in iter_gzip_bounded(source):
                    f_out.write(chunk)
        except (OSError, EOFError, DecompressionLimitError):
            dest.unlink(missing_ok=True)
            raise

    def _cleanup_old_backups(self) -> None:
        """Remove old backups exceeding max_backups limit.

        Only directories this build created are candidates. The cleanup used to
        consider every directory under the backup location, so a folder the user
        kept there was deleted once the count passed the limit (issue #84).
        """
        # Two runs may sweep the same directory at once: a snapshot can vanish
        # between the listing and the removal, and a sweep must not die on a
        # path another run already removed (issue #107).
        candidates: List[tuple] = []
        for path in self.backup_dir.iterdir():
            if not _is_backup_dir(path):
                continue
            try:
                candidates.append((path.stat().st_mtime, path))
            except OSError:
                logger.debug(f"Backup vanished before cleanup: {path.name}")
        candidates.sort(key=lambda entry: entry[0], reverse=True)

        for _, old_backup in candidates[self.max_backups :]:
            logger.info(f"Removing old backup: {old_backup.name}")
            try:
                shutil.rmtree(old_backup)
            except FileNotFoundError:
                logger.debug(f"Backup already removed by another run: {old_backup.name}")

    def list_backups(self) -> List[Dict[str, Any]]:
        """List all available backups with metadata."""
        backups = []
        for backup_dir in sorted(
            self.backup_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            if not backup_dir.is_dir():
                continue

            manifest_path = backup_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
                # An older release wrote the manifest with a plain json.dump, so
                # an interrupted backup can hold a half-written one. Skipping it
                # keeps the readable backups listable (issue #83).
                logger.warning(
                    f"Skipping backup with an unreadable manifest {backup_dir.name}: {e}"
                )
                continue
            if not isinstance(manifest, dict):
                logger.warning(f"Skipping backup with a malformed manifest: {backup_dir.name}")
                continue
            backups.append(
                {
                    "name": backup_dir.name,
                    "path": str(backup_dir),
                    "created": manifest.get("created"),
                    "reason": manifest.get("reason"),
                    "version": manifest.get("version"),
                }
            )

        return backups


class IntegrityChecker:
    """Integrity verification for traktor state.

    Detects corruption in cache and config files before they cause failures.
    """

    def __init__(self):
        """Initialize integrity checker."""
        self.checks = [
            ("config", self._check_config),
            ("token", self._check_token),
            ("cache", self._check_cache),
        ]

    def run_all_checks(self) -> Dict[str, Any]:
        """Run all integrity checks.

        Returns:
            Dictionary with overall health and per-check results
        """
        results = {}
        all_healthy = True

        for name, check_func in self.checks:
            try:
                result = check_func()
                results[name] = {
                    "healthy": result["healthy"],
                    "details": result.get("details", {}),
                }
                if not result["healthy"]:
                    all_healthy = False
            except Exception as e:
                logger.error(f"Integrity check '{name}' threw exception: {e}")
                results[name] = {"healthy": False, "error": str(e)}
                all_healthy = False

        return {
            "overall_healthy": all_healthy,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": results,
        }

    def _check_config(self) -> Dict[str, Any]:
        """Check config file integrity."""
        if not CONFIG_FILE.exists():
            return {
                "healthy": True,
                "details": {"exists": False, "note": "No config file (optional)"},
            }

        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            return {
                "healthy": True,
                "details": {"exists": True, "valid_json": True, "keys": list(data.keys())},
            }
        except json.JSONDecodeError as e:
            return {
                "healthy": False,
                "details": {"exists": True, "valid_json": False, "error": str(e)},
            }
        except Exception as e:
            return {"healthy": False, "details": {"exists": True, "error": str(e)}}

    def _check_token(self) -> Dict[str, Any]:
        """Check the active credential store (.env, see issue #6).

        Only availability/readability is validated here: a missing or
        token-less .env is normal before first authentication, so it stays
        "healthy" rather than blocking syncs at the pre-sync gate. Token
        values are never read into memory by this check.
        """
        if not ENV_FILE.exists():
            return {
                "healthy": True,
                "details": {"exists": False, "note": "No .env yet (will auth on first run)"},
            }

        try:
            lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
            has_keys = any(line.startswith("TRAKT_ACCESS_TOKEN=") for line in lines) and any(
                line.startswith("TRAKT_REFRESH_TOKEN=") for line in lines
            )
            return {
                "healthy": True,
                "details": {
                    "exists": True,
                    "readable": True,
                    "has_required_keys": has_keys,
                },
            }
        except OSError as e:
            return {
                "healthy": False,
                "details": {"exists": True, "error": str(e)},
            }

    def _check_cache(self) -> Dict[str, Any]:
        """Check cache directory integrity."""
        if not CACHE_DIR.exists():
            return {
                "healthy": True,
                "details": {"exists": False, "note": "No cache (will build on first run)"},
            }

        # Scan both plain and gzip-compressed cache files. The primary Plex
        # library cache is gzip-compressed (plex_library_cache.json.gz) - see
        # issue #5; scanning only *.json silently skipped it.
        cache_files = sorted(list(CACHE_DIR.rglob("*.json")) + list(CACHE_DIR.rglob("*.json.gz")))
        corrupt_files = []

        for cache_file in cache_files:
            if not self._is_valid_cache_file(cache_file):
                corrupt_files.append(str(cache_file.relative_to(CACHE_DIR)))

        return {
            "healthy": len(corrupt_files) == 0,
            "details": {
                "exists": True,
                "corrupt_files": corrupt_files if corrupt_files else None,
            },
        }

    @staticmethod
    def _is_valid_cache_file(cache_file: Path) -> bool:
        """Return True if a cache file parses as JSON (plain or gzipped).

        Mirrors CacheManager.load_cache()'s corruption handling: unreadable or
        malformed files are treated as corrupt rather than crashing the check.
        """
        try:
            if cache_file.name.endswith(".gz"):
                # Same bound as CacheManager.load_cache: the integrity check
                # must not expand a doctored cache either (issue #81).
                json.loads(read_gzip_text(cache_file))
            else:
                with open(cache_file, encoding="utf-8") as f:
                    json.load(f)
        except json.JSONDecodeError:
            return False
        except (OSError, EOFError, DecompressionLimitError):
            # Covers truncated/invalid gzip streams (BadGzipFile, EOFError), read
            # errors and a payload past the decompression bound
            return False
        else:
            return True


# At most this share of a computed retry delay is added back at random, so
# clients that fail together do not retry together (issue #125).
RETRY_JITTER_FRACTION = 0.5


def add_jitter(delay: float) -> float:
    """Return ``delay`` plus a random share of it, never less than ``delay``.

    Parallel workers retry a shared dependency on the same schedule, so they
    come back in one synchronized wave and hit the dependency exactly as it
    recovers. Adding a random share - rather than subtracting - keeps a
    server-supplied ``Retry-After`` a lower bound while still spreading the
    clients apart (issue #125).

    Args:
        delay: Deterministic delay in seconds

    Returns:
        A delay between ``delay`` and ``delay * (1 + RETRY_JITTER_FRACTION)``
    """
    if delay <= 0:
        return 0.0
    return delay + random.uniform(0.0, delay * RETRY_JITTER_FRACTION)


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: Tuple[type, ...] = (Exception,),
    retryable_status_codes: Tuple[int, ...] = (500, 502, 503, 504),
    name: str = "operation",
):
    """Decorator for retrying operations with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        retryable_exceptions: Exception types that should trigger a retry
        retryable_status_codes: HTTP status codes that should trigger a retry
        name: Name for logging
    """

    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    # Determine if this specific exception instance should be retried
                    should_retry = False
                    error_str = str(e)

                    # Always retry connection/timeout errors
                    if isinstance(e, (ConnectionError, TimeoutError)):
                        should_retry = True
                    else:
                        # Check for retryable status codes in error message
                        found_status_code = None
                        for code in retryable_status_codes:
                            if f"({code})" in error_str:
                                found_status_code = code
                                break

                        if found_status_code:
                            # Status code is in retry list → retry
                            should_retry = True
                        elif any(f"({c})" in error_str for c in (400, 401, 403, 404, 422)):
                            # Status code is a known non-retryable client error → don't retry
                            should_retry = False
                        else:
                            # No specific status code found - if the exception type
                            # is in retryable_exceptions, retry by default
                            # (e.g., generic RuntimeError without status code)
                            should_retry = True

                    if not should_retry or attempt >= max_retries:
                        raise

                    delay = add_jitter(min(base_delay * (2**attempt), max_delay))
                    logger.warning(
                        f"[{name}] Attempt {attempt + 1}/{max_retries + 1} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)

            # Should never reach here, but just in case
            raise RuntimeError(f"Unexpected exit from retry loop for {name}")

        return wrapper

    return decorator


class SyncProgress:
    """Tracks sync progress for resume capability.

    Saves which lists/playlists have been successfully processed so that
    an interrupted sync can resume without re-processing completed work.
    """

    def __init__(self, progress_file: Path):
        self.progress_file = progress_file
        self._completed: set = set()
        # reset() starts a new run's resume list, so that one save replaces the
        # completed set instead of merging it (issue #64).
        self._replace_completed = False
        self._load()

    def _load(self) -> None:
        """Load progress from disk."""
        if not self.progress_file.exists():
            return
        try:
            with open(self.progress_file, "r") as f:
                data = json.load(f)
            self._completed = set(data.get("completed", []))
        except (json.JSONDecodeError, FileNotFoundError, PermissionError) as e:
            logger.warning(f"Could not load sync progress: {e}")
            self._completed = set()

    def save(self) -> None:
        """Save progress to disk.

        The file also holds the run record (issue #13), so the progress keys are
        merged into the current JSON under a lock instead of replacing it: a
        plain overwrite deleted the record a run had just written, and the
        status endpoint then reported ``unknown`` for the whole run (issue #64).
        """
        with file_lock(self.progress_file):
            payload = read_json_object(self.progress_file)
            completed = set(self._completed)
            if not self._replace_completed:
                # Another run may have marked its own lists completed; a plain
                # overwrite left only the last writer's entries (issue #64).
                completed |= {name for name in payload.get("completed") or []}
            payload["completed"] = sorted(completed)
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
            written = write_text_atomic(self.progress_file, json.dumps(payload))
            self._replace_completed = False
        if not written:
            logger.warning(f"Could not save sync progress: {self.progress_file}")

    def is_completed(self, name: str) -> bool:
        """Check if a list/playlist has been completed."""
        return name in self._completed

    def mark_completed(self, name: str) -> None:
        """Mark a list/playlist as completed."""
        self._completed.add(name)
        self.save()

    def reset(self) -> None:
        """Clear all progress (call at start of new sync)."""
        self._completed.clear()
        self._replace_completed = True
        self.save()

    def get_stats(self) -> Dict[str, Any]:
        """Get progress statistics."""
        return {
            "completed_count": len(self._completed),
            "completed": sorted(list(self._completed)),
        }


# Global instances for mission-critical components. The thresholds and
# cooldowns are read from the variables RUNBOOK.md documents, so an operator
# can make a failing dependency fail fast (issue #115).
trakt_circuit_breaker = CircuitBreaker(
    failure_threshold=TRAKTOR_TRAKT_FAILURE_THRESHOLD,
    success_threshold=2,
    cooldown_seconds=TRAKTOR_TRAKT_COOLDOWN_SECONDS,
    name="trakt_api",
)

plex_circuit_breaker = CircuitBreaker(
    failure_threshold=TRAKTOR_PLEX_FAILURE_THRESHOLD,
    success_threshold=2,
    cooldown_seconds=TRAKTOR_PLEX_COOLDOWN_SECONDS,
    name="plex_api",
)

health_checker = HealthCheck()
backup_manager = BackupManager()
integrity_checker = IntegrityChecker()
