"""Tests for resilience patterns."""

import json
import time
from unittest.mock import patch

import pytest

from traktor.resilience import (
    CircuitBreaker,
    CircuitBreakerOpen,
    SyncProgress,
    retry_with_backoff,
)


class TestRetryWithBackoff:
    """Tests for retry_with_backoff decorator."""

    def test_success_no_retry(self, tmp_path):
        """Successful call should not retry."""
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.01, name="test")
        def succeed():
            nonlocal call_count
            call_count += 1
            return "success"

        result = succeed()
        assert result == "success"
        assert call_count == 1

    def test_retry_then_success(self, tmp_path):
        """Retry on failure, then succeed."""
        call_count = 0

        @retry_with_backoff(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(RuntimeError,),
            name="test",
        )
        def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("fail")
            return "success"

        result = fail_then_succeed()
        assert result == "success"
        assert call_count == 2

    def test_max_retries_exceeded(self, tmp_path):
        """Should raise after max retries exceeded."""
        call_count = 0

        @retry_with_backoff(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(RuntimeError,),
            name="test",
        )
        def always_fail():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError, match="fail"):
            always_fail()

        assert call_count == 3  # initial + 2 retries

    def test_non_retryable_exception(self, tmp_path):
        """Should not retry non-retryable exceptions."""
        call_count = 0

        @retry_with_backoff(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(RuntimeError,),
            name="test",
        )
        def raise_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            raise_value_error()

        assert call_count == 1

    def test_retry_on_status_code(self, tmp_path):
        """Should retry on matching status code in error message."""
        call_count = 0

        @retry_with_backoff(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(RuntimeError,),
            retryable_status_codes=(503,),
            name="test",
        )
        def fail_with_503():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("(503) service_unavailable")
            return "success"

        result = fail_with_503()
        assert result == "success"
        assert call_count == 2

    def test_no_retry_on_non_matching_status_code(self, tmp_path):
        """Should not retry if status code doesn't match."""
        call_count = 0

        @retry_with_backoff(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(RuntimeError,),
            retryable_status_codes=(500, 502),
            name="test",
        )
        def fail_with_400():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("(400) bad_request")

        with pytest.raises(RuntimeError, match="400"):
            fail_with_400()

        assert call_count == 1

    def test_retry_on_connection_error(self, tmp_path):
        """Should always retry on ConnectionError and TimeoutError."""
        call_count = 0

        @retry_with_backoff(
            max_retries=1,
            base_delay=0.01,
            retryable_exceptions=(ConnectionError,),
            name="test",
        )
        def fail_connection():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("connection refused")
            return "success"

        result = fail_connection()
        assert result == "success"
        assert call_count == 2

    def test_exponential_backoff_delay(self, tmp_path):
        """Delay should increase exponentially."""
        delays = []

        @retry_with_backoff(
            max_retries=3,
            base_delay=0.1,
            max_delay=1.0,
            retryable_exceptions=(RuntimeError,),
            name="test",
        )
        def always_fail():
            raise RuntimeError("fail")

        with patch("time.sleep", side_effect=delays.append):
            with pytest.raises(RuntimeError):
                always_fail()

        # Delays should be: 0.1, 0.2, 0.4 (exponential)
        assert len(delays) == 3
        assert delays[0] == 0.1
        assert delays[1] == 0.2
        assert delays[2] == 0.4

    def test_max_delay_cap(self, tmp_path):
        """Delay should not exceed max_delay."""
        delays = []

        @retry_with_backoff(
            max_retries=5,
            base_delay=1.0,
            max_delay=2.0,
            retryable_exceptions=(RuntimeError,),
            name="test",
        )
        def always_fail():
            raise RuntimeError("fail")

        with patch("time.sleep", side_effect=delays.append):
            with pytest.raises(RuntimeError):
                always_fail()

        # All delays should be capped at 2.0
        assert all(d <= 2.0 for d in delays)


class TestSyncProgress:
    """Tests for SyncProgress tracker."""

    def test_load_nonexistent_file(self, tmp_path):
        """Should start empty if file doesn't exist."""
        progress_file = tmp_path / "progress.json"
        progress = SyncProgress(progress_file)
        assert progress.get_stats()["completed_count"] == 0

    def test_mark_and_check_completed(self, tmp_path):
        """Should track completed items."""
        progress_file = tmp_path / "progress.json"
        progress = SyncProgress(progress_file)

        progress.mark_completed("list1")
        assert progress.is_completed("list1")
        assert not progress.is_completed("list2")

    def test_persistence(self, tmp_path):
        """Should persist across instances."""
        progress_file = tmp_path / "progress.json"

        progress1 = SyncProgress(progress_file)
        progress1.mark_completed("list1")
        progress1.mark_completed("list2")

        progress2 = SyncProgress(progress_file)
        assert progress2.is_completed("list1")
        assert progress2.is_completed("list2")
        assert not progress2.is_completed("list3")

    def test_reset(self, tmp_path):
        """Should clear all progress on reset."""
        progress_file = tmp_path / "progress.json"
        progress = SyncProgress(progress_file)

        progress.mark_completed("list1")
        progress.reset()

        assert not progress.is_completed("list1")
        assert progress.get_stats()["completed_count"] == 0

    def test_get_stats(self, tmp_path):
        """Should return correct statistics."""
        progress_file = tmp_path / "progress.json"
        progress = SyncProgress(progress_file)

        progress.mark_completed("c")
        progress.mark_completed("a")
        progress.mark_completed("b")

        stats = progress.get_stats()
        assert stats["completed_count"] == 3
        assert stats["completed"] == ["a", "b", "c"]

    def test_corrupt_file_handling(self, tmp_path):
        """Should handle corrupt progress file gracefully."""
        progress_file = tmp_path / "progress.json"
        progress_file.write_text("not json")

        progress = SyncProgress(progress_file)
        assert progress.get_stats()["completed_count"] == 0

    def test_save_includes_timestamp(self, tmp_path):
        """Saved file should include timestamp."""
        progress_file = tmp_path / "progress.json"
        progress = SyncProgress(progress_file)
        progress.mark_completed("list1")

        with open(progress_file) as f:
            data = json.load(f)

        assert "completed" in data
        assert "timestamp" in data
        assert data["completed"] == ["list1"]


class TestCircuitBreaker:
    """Tests for CircuitBreaker."""

    def test_initially_closed(self):
        """Circuit should start closed."""
        breaker = CircuitBreaker()
        assert breaker.state.value == "closed"

    def test_successful_call(self):
        """Successful call should return result."""
        breaker = CircuitBreaker()
        result = breaker.call(lambda: "success")
        assert result == "success"

    def test_opens_after_failures(self):
        """Circuit should open after threshold failures."""
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=1.0)

        def fail():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            breaker.call(fail)
        with pytest.raises(RuntimeError):
            breaker.call(fail)

        with pytest.raises(CircuitBreakerOpen):
            breaker.call(fail)

        assert breaker.state.value == "open"

    def test_half_open_after_cooldown(self):
        """Circuit should enter half-open after cooldown."""
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.01, success_threshold=1)

        def fail():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            breaker.call(fail)
        assert breaker.state.value == "open"

        time.sleep(0.02)

        result = breaker.call(lambda: "success")
        assert result == "success"
        assert breaker.state.value == "closed"

    def test_fallback_when_open(self):
        """Should use fallback when circuit is open."""
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=1.0)

        def fail():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            breaker.call(fail)

        result = breaker.call(fail, fallback=lambda: "fallback")
        assert result == "fallback"

    def test_get_stats(self):
        """Should return circuit statistics."""
        breaker = CircuitBreaker(name="test_breaker")
        stats = breaker.get_stats()

        assert stats["name"] == "test_breaker"
        assert stats["state"] == "closed"
        assert stats["failure_count"] == 0
        assert stats["failure_threshold"] == 5

    def test_success_resets_failure_count(self):
        """Success should reset failure count in closed state."""
        breaker = CircuitBreaker(failure_threshold=5)

        def fail():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            breaker.call(fail)
        assert breaker.get_stats()["failure_count"] == 1

        breaker.call(lambda: "success")
        assert breaker.get_stats()["failure_count"] == 0


class TestBackupManager:
    """Coverage for BackupManager create/restore/verify/cleanup (TODO audit D5)."""

    @pytest.fixture
    def backup_env(self, tmp_path, monkeypatch):
        """BackupManager pointed at temp sources and backup dir."""
        import traktor.resilience as res

        config_file = tmp_path / "config.json"
        config_file.write_text('{"key": "value"}')
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "entry.json").write_text('{"cached": true}')

        env_file = tmp_path / ".env"
        env_file.write_text(
            "TRAKT_CLIENT_ID=abc\nTRAKT_ACCESS_TOKEN=tok1234567890\nTRAKT_REFRESH_TOKEN=ref9876543210\n"
        )

        monkeypatch.setattr(res, "CONFIG_FILE", config_file)
        monkeypatch.setattr(res, "ENV_FILE", env_file)
        monkeypatch.setattr(res, "CACHE_DIR", cache_dir)

        backup_dir = tmp_path / "backups"
        manager = res.BackupManager(backup_dir=backup_dir, max_backups=2, compress=True)
        return manager, tmp_path

    def test_create_backup_compressed(self, backup_env):
        """Backup compresses files and writes a manifest."""
        manager, tmp_path = backup_env
        backup_path = manager.create_backup(reason="test")

        assert backup_path.exists()
        manifest = json.loads((backup_path / "manifest.json").read_text())
        assert "config" in manifest["items"]
        assert manifest["items"]["config"]["compressed"] is True
        # The live .env token store is included (issue #6)
        assert "env" in manifest["items"]
        # Cache dir backed up with gz entry
        gz_files = [p.name for p in (backup_path / "cache").rglob("*.gz")]
        assert "entry.json.gz" in gz_files

    def test_create_and_restore_roundtrip(self, backup_env):
        """Restore brings back the backed-up files."""
        import traktor.resilience as res

        manager, tmp_path = backup_env
        backup_path = manager.create_backup(reason="test")

        # Corrupt the original, then restore
        res.CONFIG_FILE.write_text('{"corrupted": true}')
        assert manager.restore_backup(backup_path) is True
        assert res.CONFIG_FILE.read_text() == '{"key": "value"}'

    def test_restore_missing_manifest(self, backup_env):
        """Backup without manifest cannot be restored."""
        manager, tmp_path = backup_env
        empty = tmp_path / "empty"
        empty.mkdir()
        assert manager.restore_backup(empty) is False

    def test_restore_corrupt_manifest(self, backup_env):
        """Corrupt manifest is handled gracefully (returns False)."""
        manager, tmp_path = backup_env
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "manifest.json").write_text("{not json")
        assert manager.restore_backup(bad) is False

    def test_restore_verification_failure(self, backup_env):
        """Checksum mismatch aborts restore."""
        manager, tmp_path = backup_env
        backup_path = manager.create_backup(reason="test")

        # Corrupt a backed-up item so verification fails
        item_gz = backup_path / "config.gz"
        item_gz.write_bytes(b"tampered")

        assert manager.restore_backup(backup_path, verify=True) is False

    def test_list_backups(self, backup_env):
        """list_backups returns metadata from manifests."""
        manager, _ = backup_env
        manager.create_backup(reason="alpha")
        manager.create_backup(reason="beta")

        backups = manager.list_backups()

        assert len(backups) == 2
        reasons = {b["reason"] for b in backups}
        assert reasons == {"alpha", "beta"}

    def test_cleanup_old_backups(self, backup_env):
        """max_backups bounds the number of retained backups."""
        manager, _ = backup_env
        for i in range(4):
            manager.create_backup(reason=f"run-{i}")

        backups = manager.list_backups()
        assert len(backups) <= 2

    def test_restore_uncompressed_backup(self, backup_env, monkeypatch):
        """Restore works with compress=False backups too."""
        import traktor.resilience as res

        manager, tmp_path = backup_env
        manager.compress = False
        backup_path = manager.create_backup(reason="uncompressed")

        res.CONFIG_FILE.write_text('{"corrupted": true}')
        assert manager.restore_backup(backup_path) is True
        assert res.CONFIG_FILE.read_text() == '{"key": "value"}'


class TestIntegrityChecker:
    """Coverage for IntegrityChecker checks (TODO audit D5)."""

    @pytest.fixture
    def checker(self, tmp_path, monkeypatch):
        import traktor.resilience as res

        config = tmp_path / "config.json"
        env_file = tmp_path / ".env"
        cache = tmp_path / "cache"
        cache.mkdir()
        monkeypatch.setattr(res, "CONFIG_FILE", config)
        monkeypatch.setattr(res, "ENV_FILE", env_file)
        monkeypatch.setattr(res, "CACHE_DIR", cache)
        return res.IntegrityChecker(), tmp_path

    def test_missing_files_are_healthy(self, checker):
        """Missing config/token/cache are reported as healthy (optional)."""
        checker, tmp_path = checker
        results = checker.run_all_checks()
        assert results["overall_healthy"] is True

    def test_valid_config_and_token(self, checker):
        """Valid config and token files pass."""
        checker, tmp_path = checker
        (tmp_path / "config.json").write_text('{"key": "value"}')
        (tmp_path / ".env").write_text(
            "TRAKT_ACCESS_TOKEN=tok1234567890\nTRAKT_REFRESH_TOKEN=ref9876543210\n"
        )

        results = checker.run_all_checks()

        assert results["overall_healthy"] is True
        assert results["checks"]["config"]["healthy"] is True

    def test_corrupt_config_fails(self, checker):
        """Corrupt config JSON is flagged."""
        checker, tmp_path = checker
        (tmp_path / "config.json").write_text("{bad json")

        results = checker.run_all_checks()

        assert results["checks"]["config"]["healthy"] is False
        assert results["overall_healthy"] is False

    def test_env_without_tokens_is_still_healthy(self, checker):
        """A .env without token lines is normal before first auth (issue #6).

        The pre-sync gate must not block fresh installs; only unreadability
        of the credential store is unhealthy.
        """
        checker, tmp_path = checker
        (tmp_path / ".env").write_text("TRAKT_CLIENT_ID=abc\n")

        results = checker.run_all_checks()

        check = results["checks"]["token"]
        assert check["healthy"] is True
        assert check["details"]["has_required_keys"] is False

    def test_unreadable_env_fails(self, checker):
        """An unreadable .env credential store is flagged."""
        checker, tmp_path = checker
        env_file = tmp_path / ".env"
        env_file.write_text("TRAKT_ACCESS_TOKEN=x\n")
        env_file.chmod(0o000)
        try:
            results = checker.run_all_checks()
        finally:
            env_file.chmod(0o644)

        assert results["checks"]["token"]["healthy"] is False

    def test_corrupt_cache_file_fails(self, checker):
        """Corrupt JSON in the cache directory is flagged."""
        checker, tmp_path = checker
        (tmp_path / "cache" / "bad.json").write_text("{bad json")

        results = checker.run_all_checks()

        assert results["checks"]["cache"]["healthy"] is False

    def test_check_exception_handled(self, checker, monkeypatch):
        """A throwing check is reported as unhealthy, not raised."""
        checker, tmp_path = checker

        def boom():
            raise RuntimeError("check crashed")

        checker.checks = [
            ("config", boom),
            ("token", checker._check_token),
            ("cache", checker._check_cache),
        ]

        results = checker.run_all_checks()

        assert results["overall_healthy"] is False
        assert "error" in results["checks"]["config"]


class TestIntegrityCheckerGzipCache:
    """Regression tests for issue #5: the primary .json.gz library cache must
    be included in integrity checks (previously only *.json was scanned)."""

    @pytest.fixture
    def checker(self, tmp_path, monkeypatch):
        import traktor.resilience as res

        cache = tmp_path / "cache"
        cache.mkdir()
        monkeypatch.setattr(res, "CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(res, "ENV_FILE", tmp_path / ".env")
        monkeypatch.setattr(res, "CACHE_DIR", cache)
        return res.IntegrityChecker(), tmp_path / "cache"

    def test_valid_gzip_cache_is_healthy(self, checker):
        """A well-formed plex_library_cache.json.gz passes the check."""
        import gzip as _gzip
        import json as _json

        checker_obj, cache_dir = checker
        payload = {"movies_by_imdb": {}, "version": 3}
        with _gzip.open(cache_dir / "plex_library_cache.json.gz", "wt", encoding="utf-8") as f:
            _json.dump(payload, f)

        results = checker_obj._check_cache()

        assert results["healthy"] is True
        assert results["details"]["file_count"] == 1
        assert results["details"]["corrupt_files"] is None

    def test_truncated_gzip_cache_is_flagged(self, checker):
        """A truncated gzip stream is reported corrupt with the file listed."""
        checker_obj, cache_dir = checker
        # Write a valid gzip header then truncate before any deflate data
        (cache_dir / "plex_library_cache.json.gz").write_bytes(b"\x1f\x8b\x08\x00")

        results = checker_obj._check_cache()

        assert results["healthy"] is False
        assert "plex_library_cache.json.gz" in (results["details"]["corrupt_files"] or [])

    def test_gzip_with_invalid_json_is_flagged(self, checker):
        """Gzipped content that is not valid JSON is flagged."""
        import gzip as _gzip

        checker_obj, cache_dir = checker
        with _gzip.open(cache_dir / "official_trending.json.gz", "wt", encoding="utf-8") as f:
            f.write("{bad json")

        results = checker_obj._check_cache()

        assert results["healthy"] is False
        assert "official_trending.json.gz" in (results["details"]["corrupt_files"] or [])

    def test_plain_json_still_checked_alongside_gzip(self, checker):
        """Plain *.json files remain covered when .gz files are present."""
        checker_obj, cache_dir = checker
        (cache_dir / "cache_metadata.json").write_text('{"ok": true}')
        (cache_dir / "bad.json").write_text("{bad json")

        results = checker_obj._check_cache()

        assert results["healthy"] is False
        assert "bad.json" in (results["details"]["corrupt_files"] or [])
        assert results["details"]["file_count"] == 2


class TestEnvBackupRoundtrip:
    """Regression tests for issue #6: the live .env token store must survive
    create_backup() -> restore_backup() so disaster recovery restores auth."""

    @pytest.fixture
    def env_backup_env(self, tmp_path, monkeypatch):
        import traktor.resilience as res

        config_file = tmp_path / "config.json"
        config_file.write_text('{"key": "value"}')
        env_file = tmp_path / ".env"
        env_content = (
            "# traktor credentials\n"
            "TRAKT_CLIENT_ID=abc\n"
            "TRAKT_ACCESS_TOKEN=tok1234567890abcdef\n"
            "TRAKT_REFRESH_TOKEN=ref9876543210abcdef\n"
        )
        env_file.write_text(env_content)
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        monkeypatch.setattr(res, "CONFIG_FILE", config_file)
        monkeypatch.setattr(res, "ENV_FILE", env_file)
        monkeypatch.setattr(res, "CACHE_DIR", cache_dir)

        manager = res.BackupManager(backup_dir=tmp_path / "backups", compress=True)
        return manager, tmp_path, env_file, env_content

    def test_env_roundtrip_through_backup_and_restore(self, env_backup_env):
        """A .env with token lines round-trips through backup and restore."""
        import json

        manager, tmp_path, env_file, env_content = env_backup_env

        backup_path = manager.create_backup(reason="test")
        manifest = json.loads((backup_path / "manifest.json").read_text())
        assert "env" in manifest["items"]

        # Simulate a fresh machine: the .env is gone
        env_file.unlink()
        assert not env_file.exists()

        assert manager.restore_backup(backup_path) is True
        assert env_file.read_text() == env_content

        # Restrictive permissions on restored credential material
        assert (env_file.stat().st_mode & 0o777) == 0o600

    def test_backed_up_env_has_restrictive_permissions(self, env_backup_env):
        """The archived .env inside the backup directory is mode 0o600."""

        manager, tmp_path, _, _ = env_backup_env

        backup_path = manager.create_backup(reason="test")
        archived = backup_path / "env.gz"

        assert archived.exists()
        assert (archived.stat().st_mode & 0o777) == 0o600

    def test_manifest_does_not_leak_env_contents(self, env_backup_env):
        """The manifest stores only path + checksum, never token values."""

        manager, tmp_path, _, _ = env_backup_env

        backup_path = manager.create_backup(reason="test")
        manifest_text = (backup_path / "manifest.json").read_text()

        assert "TRAKT_ACCESS_TOKEN" not in manifest_text
        assert "tok1234567890" not in manifest_text
