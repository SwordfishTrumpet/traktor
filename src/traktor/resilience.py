"""Mission-critical resilience patterns for traktor.

Provides circuit breakers, health monitoring, automated backups,
and integrity checks for production deployments.
"""

import gzip
import hashlib
import json
import shutil
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .log import logger
from .settings import CACHE_DIR, CONFIG_FILE, TOKEN_FILE


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

    def check(self, name: str) -> HealthStatus:
        """Run a single health check.

        Args:
            name: Component name

        Returns:
            Health status for the component
        """
        with self._lock:
            if name not in self._checks:
                return HealthStatus.UNHEALTHY

            check = self._checks[name]

        try:
            result = check["func"]()
            with self._lock:
                check["last_result"] = result
                check["last_check"] = datetime.utcnow().isoformat()
                if result:
                    check["consecutive_failures"] = 0
                    check["status"] = HealthStatus.HEALTHY
                else:
                    check["consecutive_failures"] += 1
                    # Degraded after 2 failures, unhealthy after 5
                    if check["consecutive_failures"] >= 5:
                        check["status"] = HealthStatus.UNHEALTHY
                    elif check["consecutive_failures"] >= 2:
                        check["status"] = HealthStatus.DEGRADED
                return check["status"]
        except Exception as e:
            logger.error(f"Health check '{name}' failed: {e}")
            with self._lock:
                check["last_result"] = False
                check["last_check"] = datetime.utcnow().isoformat()
                check["consecutive_failures"] += 1
                if check["consecutive_failures"] >= 5:
                    check["status"] = HealthStatus.UNHEALTHY
                elif check["consecutive_failures"] >= 2:
                    check["status"] = HealthStatus.DEGRADED
                return check["status"]

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
            "timestamp": datetime.utcnow().isoformat(),
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
        max_backups: int = 10,
        compress: bool = True,
    ):
        """Initialize backup manager.

        Args:
            backup_dir: Directory for backups (default: ~/.traktor_backups)
            max_backups: Maximum number of backups to retain
            compress: Whether to gzip compress backups
        """
        if backup_dir is None:
            backup_dir = Path.home() / ".traktor_backups"
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.max_backups = max_backups
        self.compress = compress

        self._items_to_backup = [
            ("config", CONFIG_FILE),
            ("token", TOKEN_FILE),
            ("cache", CACHE_DIR),
        ]

    def create_backup(self, reason: str = "manual") -> Path:
        """Create a backup of all traktor state.

        Args:
            reason: Reason for backup (manual, scheduled, pre_sync, etc.)

        Returns:
            Path to created backup
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_name = f"traktor_backup_{timestamp}_{reason}"
        backup_path = self.backup_dir / backup_name

        logger.info(f"Creating backup: {backup_name}")

        # Create backup directory
        backup_path.mkdir(parents=True, exist_ok=True)

        # Backup each item
        manifest = {
            "created": datetime.utcnow().isoformat(),
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

        # Write manifest
        manifest_path = backup_path / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        # Cleanup old backups
        self._cleanup_old_backups()

        logger.info(f"Backup completed: {backup_path}")
        return backup_path

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
            for item in source.rglob("*"):
                if item.is_file():
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

        with open(manifest_path) as f:
            manifest = json.load(f)

        # Verify backup integrity
        if verify:
            for name, info in manifest.get("items", {}).items():
                if not self._verify_backup_item(backup_path / name, info["checksum"]):
                    logger.error(f"Backup verification failed for {name}")
                    return False

        # Perform restore
        for name, info in manifest.get("items", {}).items():
            source = backup_path / name
            dest = Path(info["source"])

            # Remove existing
            if dest.exists():
                if dest.is_file():
                    dest.unlink()
                else:
                    shutil.rmtree(dest)

            # Restore
            self._restore_item(source, dest, info.get("compressed", True))

        logger.info("Restore completed successfully")
        return True

    def _verify_backup_item(self, backup_item: Path, expected_checksum: str) -> bool:
        """Verify a backed up item matches its checksum."""
        sha256 = hashlib.sha256()

        if backup_item.is_file() or backup_item.with_suffix(".gz").exists():
            # Single file
            file_path = backup_item if backup_item.exists() else backup_item.with_suffix(".gz")
            if file_path.suffix == ".gz":
                with gzip.open(file_path, "rb") as f:
                    sha256.update(f.read())
            else:
                with open(file_path, "rb") as f:
                    sha256.update(f.read())
        else:
            # Directory
            for file_path in backup_item.rglob("*"):
                if file_path.is_file():
                    if file_path.suffix == ".gz":
                        with gzip.open(file_path, "rb") as f:
                            sha256.update(f.read())
                    else:
                        with open(file_path, "rb") as f:
                            sha256.update(f.read())

        return sha256.hexdigest() == expected_checksum

    def _restore_item(self, source: Path, dest: Path, compressed: bool) -> None:
        """Restore a single item from backup."""
        if source.is_file() or source.with_suffix(".gz").exists():
            file_path = source if source.exists() else source.with_suffix(".gz")
            dest.parent.mkdir(parents=True, exist_ok=True)

            if file_path.suffix == ".gz":
                with gzip.open(file_path, "rb") as f_in:
                    with open(dest, "wb") as f_out:
                        f_out.write(f_in.read())
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

                    if item.suffix == ".gz":
                        with gzip.open(item, "rb") as f_in:
                            # Remove .gz suffix
                            final_dest = item_dest.with_suffix("")
                            with open(final_dest, "wb") as f_out:
                                f_out.write(f_in.read())
                    else:
                        shutil.copy2(item, item_dest)

    def _cleanup_old_backups(self) -> None:
        """Remove old backups exceeding max_backups limit."""
        backups = sorted(
            [d for d in self.backup_dir.iterdir() if d.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for old_backup in backups[self.max_backups :]:
            logger.info(f"Removing old backup: {old_backup.name}")
            shutil.rmtree(old_backup)

    def list_backups(self) -> List[Dict[str, Any]]:
        """List all available backups with metadata."""
        backups = []
        for backup_dir in sorted(
            self.backup_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            if not backup_dir.is_dir():
                continue

            manifest_path = backup_dir / "manifest.json"
            if manifest_path.exists():
                with open(manifest_path) as f:
                    manifest = json.load(f)
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
            "timestamp": datetime.utcnow().isoformat(),
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
        """Check token file integrity."""
        if not TOKEN_FILE.exists():
            return {
                "healthy": True,
                "details": {"exists": False, "note": "No token file (will auth on first run)"},
            }

        try:
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            required_keys = ["access_token", "refresh_token"]
            has_keys = all(k in data for k in required_keys)
            return {
                "healthy": has_keys,
                "details": {
                    "exists": True,
                    "valid_json": True,
                    "has_required_keys": has_keys,
                    "keys": list(data.keys()),
                },
            }
        except json.JSONDecodeError as e:
            return {
                "healthy": False,
                "details": {"exists": True, "valid_json": False, "error": str(e)},
            }
        except Exception as e:
            return {"healthy": False, "details": {"exists": True, "error": str(e)}}

    def _check_cache(self) -> Dict[str, Any]:
        """Check cache directory integrity."""
        if not CACHE_DIR.exists():
            return {
                "healthy": True,
                "details": {"exists": False, "note": "No cache (will build on first run)"},
            }

        cache_files = list(CACHE_DIR.rglob("*.json"))
        corrupt_files = []

        for cache_file in cache_files:
            try:
                with open(cache_file) as f:
                    json.load(f)
            except json.JSONDecodeError:
                corrupt_files.append(str(cache_file.relative_to(CACHE_DIR)))

        return {
            "healthy": len(corrupt_files) == 0,
            "details": {
                "exists": True,
                "file_count": len(cache_files),
                "corrupt_files": corrupt_files if corrupt_files else None,
            },
        }


# Global instances for mission-critical components
trakt_circuit_breaker = CircuitBreaker(
    failure_threshold=5, success_threshold=2, cooldown_seconds=60.0, name="trakt_api"
)

plex_circuit_breaker = CircuitBreaker(
    failure_threshold=3, success_threshold=2, cooldown_seconds=30.0, name="plex_api"
)

health_checker = HealthCheck()
backup_manager = BackupManager()
integrity_checker = IntegrityChecker()
