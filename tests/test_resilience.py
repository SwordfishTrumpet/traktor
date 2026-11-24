"""Tests for resilience patterns."""

import gzip
import json
import time
from unittest.mock import patch

import pytest

from traktor.resilience import (
    CircuitBreaker,
    CircuitBreakerOpen,
    HealthCheck,
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

    def test_restore_refuses_a_manifest_path_outside_the_expected_set(self, backup_env):
        """A manifest naming a foreign file is refused and the file is untouched
        (issue #11)."""
        manager, tmp_path = backup_env
        backup_path = manager.create_backup(reason="test")

        victim = tmp_path / "victim.txt"
        victim.write_text("original content\n")

        manifest_path = backup_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["items"]["config"]["source"] = str(victim)
        manifest_path.write_text(json.dumps(manifest))

        assert manager.restore_backup(backup_path) is False
        assert victim.read_text() == "original content\n"

    def test_restore_refuses_a_foreign_directory_before_removing_it(self, backup_env):
        """A directory entry pointed outside the backup is refused before any
        removal (issue #11)."""
        manager, tmp_path = backup_env
        backup_path = manager.create_backup(reason="test")

        victim_dir = tmp_path / "victim-dir"
        (victim_dir / "nested").mkdir(parents=True)
        (victim_dir / "nested" / "keep.txt").write_text("keep me\n")

        manifest_path = backup_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["items"]["cache"]["source"] = str(victim_dir)
        manifest_path.write_text(json.dumps(manifest))

        assert manager.restore_backup(backup_path) is False
        assert (victim_dir / "nested" / "keep.txt").read_text() == "keep me\n"

    def test_restore_refuses_an_unknown_item_name(self, backup_env):
        """An item name this build never wrote is refused, including a
        path-traversal name (issue #11)."""
        import traktor.resilience as res

        manager, tmp_path = backup_env
        backup_path = manager.create_backup(reason="test")

        manifest_path = backup_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["items"]["../../../etc/passwd"] = {
            "source": str(res.CONFIG_FILE),
            "checksum": "0" * 64,
            "compressed": False,
        }
        manifest_path.write_text(json.dumps(manifest))

        assert manager.restore_backup(backup_path) is False
        assert res.CONFIG_FILE.read_text() == '{"key": "value"}'

    def test_restore_refuses_an_item_without_a_source(self, backup_env):
        """A manifest entry that names no source is refused, not trusted."""
        manager, tmp_path = backup_env
        backup_path = manager.create_backup(reason="test")

        manifest_path = backup_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        del manifest["items"]["config"]["source"]
        manifest_path.write_text(json.dumps(manifest))

        assert manager.restore_backup(backup_path) is False

    def test_restore_roundtrip_still_restores_the_expected_paths(self, backup_env):
        """A manifest this build wrote still restores normally (issue #11)."""
        import traktor.resilience as res

        manager, tmp_path = backup_env
        backup_path = manager.create_backup(reason="test")

        res.CONFIG_FILE.write_text('{"changed": true}')
        res.ENV_FILE.write_text("TRAKT_ACCESS_TOKEN=gone\n")

        assert manager.restore_backup(backup_path) is True
        assert res.CONFIG_FILE.read_text() == '{"key": "value"}'
        assert "TRAKT_CLIENT_ID" in res.ENV_FILE.read_text()
        assert (res.ENV_FILE.stat().st_mode & 0o777) == 0o600

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
        assert results["details"]["corrupt_files"] is None
        # No count here on purpose (issue #70): the property is "healthy", and a
        # pinned file count turns a correct change into a red test.

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


# ---------------------------------------------------------------------------
# Issue #42: a multi-file backup this build wrote must verify and restore
# ---------------------------------------------------------------------------


class TestBackupVerificationOrder:
    """The checksum order must not depend on the file system (issue #42)."""

    @pytest.fixture
    def multi_file_env(self, tmp_path, monkeypatch):
        import traktor.resilience as res

        config_file = tmp_path / "config.json"
        config_file.write_text('{"managed_playlists": ["a"]}', encoding="utf-8")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        for index, name in enumerate(
            [
                "plex_library_cache.json.gz",
                "plex_users.json",
                "trakt_lists.json",
                "watch_sync.json",
                "official_lists.json",
                "config_snapshot.json",
            ]
        ):
            (cache_dir / name).write_text(json.dumps({"n": index}), encoding="utf-8")
        env_file = tmp_path / ".env"
        env_file.write_text("TRAKT_CLIENT_ID=x\n", encoding="utf-8")

        monkeypatch.setattr(res, "CONFIG_FILE", config_file)
        monkeypatch.setattr(res, "ENV_FILE", env_file)
        monkeypatch.setattr(res, "CACHE_DIR", cache_dir)
        return res, tmp_path

    @pytest.mark.parametrize("compress", [True, False])
    def test_backup_of_several_cache_files_restores(self, multi_file_env, compress):
        import shutil

        res, tmp_path = multi_file_env
        manager = res.BackupManager(backup_dir=tmp_path / "backups", compress=compress)
        backup_path = manager.create_backup(reason="test")

        # The recovery path: the config is gone, the backup is all there is.
        res.CONFIG_FILE.unlink()
        shutil.rmtree(res.CACHE_DIR)

        assert manager.restore_backup(backup_path) is True
        assert res.CONFIG_FILE.exists()
        assert len(list(res.CACHE_DIR.iterdir())) == 6


# ---------------------------------------------------------------------------
# Issue #59: one sample is enough to report a failure
# ---------------------------------------------------------------------------


class TestHealthCheckReportsTheFirstFailure:
    """A one-shot process must not report a broken component as healthy (#59)."""

    def test_first_failure_is_never_healthy(self):
        checker = HealthCheck()
        checker.register("config", lambda: False)

        assert checker.check("config").value == "degraded"
        assert checker.check_all()["status"] != "healthy"

    def test_repeated_failures_escalate(self):
        checker = HealthCheck()
        checker.register("config", lambda: False)

        statuses = [checker.check_all()["status"] for _ in range(3)]

        assert statuses[0] == "degraded"
        assert statuses[-1] == "unhealthy"

    def test_a_raising_check_counts_as_a_failure(self):
        checker = HealthCheck()

        def explode():
            raise RuntimeError("boom")

        checker.register("cache", explode)

        assert checker.check("cache").value == "degraded"

    def test_recovery_returns_to_healthy(self):
        checker = HealthCheck()
        state = {"ok": False}
        checker.register("cache", lambda: state["ok"])

        assert checker.check("cache").value == "degraded"
        state["ok"] = True
        assert checker.check("cache").value == "healthy"


# ---------------------------------------------------------------------------
# Issue #23: an interrupted progress save keeps the previous file
# ---------------------------------------------------------------------------


def test_interrupted_progress_save_keeps_the_previous_file(tmp_path):
    progress_file = tmp_path / "sync_progress.json"
    progress_file.write_text(json.dumps({"completed": ["kept-list"]}), encoding="utf-8")
    progress = SyncProgress(progress_file)

    with patch("traktor.utils.os.fsync", side_effect=OSError(28, "No space left on device")):
        progress.mark_completed("new-list")

    assert json.loads(progress_file.read_text(encoding="utf-8")) == {"completed": ["kept-list"]}


# ---------------------------------------------------------------------------
# Issue #64: the resume writer must not erase the run record
# ---------------------------------------------------------------------------


class TestSyncProgressKeepsTheRunRecord:
    """`/status` reads the run record from the same file SyncProgress writes."""

    def test_saving_progress_keeps_the_run_record(self, tmp_path):
        """A run in progress stays visible while lists are marked completed (#64)."""
        from traktor.run_status import RUN_STATUS_RUNNING, read_run_status, record_run_started

        progress_file = tmp_path / "sync_progress.json"
        record_run_started(progress_file)

        progress = SyncProgress(progress_file)
        progress.reset()
        progress.mark_completed("list:A")

        run = read_run_status(progress_file)
        assert run.get("status") == RUN_STATUS_RUNNING
        assert json.loads(progress_file.read_text())["completed"] == ["list:A"]

    def test_two_instances_keep_each_others_progress(self, tmp_path):
        """Two overlapping runs each keep the lists the other marked (#64)."""
        progress_file = tmp_path / "sync_progress.json"

        first = SyncProgress(progress_file)
        second = SyncProgress(progress_file)
        first.mark_completed("list:A")
        second.mark_completed("list:B")

        assert json.loads(progress_file.read_text())["completed"] == ["list:A", "list:B"]

    def test_reset_clears_the_previous_runs_resume_list(self, tmp_path):
        """A new run starts from an empty resume list, not a union of old runs."""
        progress_file = tmp_path / "sync_progress.json"

        first = SyncProgress(progress_file)
        first.mark_completed("list:A")

        second = SyncProgress(progress_file)
        second.reset()

        assert SyncProgress(progress_file).get_stats()["completed_count"] == 0


# ---------------------------------------------------------------------------
# Issue #67: two backups in one second are two snapshots
# ---------------------------------------------------------------------------


def test_two_backups_in_the_same_second_keep_both_snapshots(tmp_path, monkeypatch):
    """A second backup in the same second must not overwrite the first (#67)."""
    import traktor.resilience as res

    config_file = tmp_path / "config.json"
    config_file.write_text('{"version": 1}')
    monkeypatch.setattr(res, "CONFIG_FILE", config_file)
    monkeypatch.setattr(res, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(res, "CACHE_DIR", tmp_path / "cache")

    manager = res.BackupManager(backup_dir=tmp_path / "backups", compress=True, max_backups=10)

    first = manager.create_backup(reason="manual")
    config_file.write_text('{"version": 2}')
    second = manager.create_backup(reason="manual")

    assert first != second
    assert first.exists() and second.exists()
    # The first snapshot still holds the pre-change config it was taken for.
    with gzip.open(first / "config.gz", "rt", encoding="utf-8") as f:
        assert json.load(f) == {"version": 1}
    with gzip.open(second / "config.gz", "rt", encoding="utf-8") as f:
        assert json.load(f) == {"version": 2}


# ---------------------------------------------------------------------------
# Issue #84: retention deleted any directory under the backup location
# ---------------------------------------------------------------------------


def test_cleanup_leaves_a_foreign_directory_in_the_backup_location(tmp_path, monkeypatch):
    """Only directories this build created are candidates for deletion."""
    import traktor.resilience as res

    monkeypatch.setattr(res, "CONFIG_FILE", tmp_path / "missing-config.json")
    monkeypatch.setattr(res, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setattr(res, "CACHE_DIR", tmp_path / "missing-cache")
    backup_dir = tmp_path / "backups"
    manager = res.BackupManager(backup_dir=backup_dir, max_backups=1)

    mine = backup_dir / "my-notes"
    mine.mkdir()
    (mine / "important.txt").write_text("keep me", encoding="utf-8")

    for run in range(3):
        manager.create_backup(reason=f"run{run}")

    assert (mine / "important.txt").exists()


# ---------------------------------------------------------------------------
# Issue #83: one truncated manifest hid every readable backup
# ---------------------------------------------------------------------------


def test_list_backups_skips_a_truncated_manifest(tmp_path, monkeypatch):
    """A half-written manifest is skipped instead of aborting the listing."""
    import traktor.resilience as res

    monkeypatch.setattr(res, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(res, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setattr(res, "CACHE_DIR", tmp_path / "missing-cache")
    backup_dir = tmp_path / "backups"
    manager = res.BackupManager(backup_dir=backup_dir, max_backups=10)
    manager.create_backup(reason="first")
    manager.create_backup(reason="second")

    broken = backup_dir / "traktor_backup_20200101_000000_000000_broken"
    broken.mkdir()
    (broken / "manifest.json").write_text('{"created": "2020-01-01T00:00:0', encoding="utf-8")

    backups = manager.list_backups()

    assert {backup["reason"] for backup in backups} == {"first", "second"}


# ---------------------------------------------------------------------------
# Issue #98: backup archives were readable by every local account
# ---------------------------------------------------------------------------


def test_every_backup_archive_is_owner_only(tmp_path, monkeypatch):
    """The config archive holds the Plex token and must not be world-readable."""
    import traktor.resilience as res

    config_file = tmp_path / "config.json"
    config_file.write_text('{"plex_token": "fixture"}', encoding="utf-8")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "plex_library_cache.json").write_text('{"movies_list": []}', encoding="utf-8")
    monkeypatch.setattr(res, "CONFIG_FILE", config_file)
    monkeypatch.setattr(res, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setattr(res, "CACHE_DIR", cache_dir)
    manager = res.BackupManager(backup_dir=tmp_path / "backups", compress=True)

    backup_path = manager.create_backup(reason="pre_sync")

    for archive in (
        backup_path / "config.gz",
        backup_path / "cache" / "plex_library_cache.json.gz",
    ):
        assert archive.exists()
        assert (archive.stat().st_mode & 0o777) == 0o600
    assert (backup_path.stat().st_mode & 0o777) == 0o700


# ---------------------------------------------------------------------------
# Issue #89: the documented backup settings did nothing
# ---------------------------------------------------------------------------


def test_documented_backup_settings_are_used(tmp_path, monkeypatch):
    """TRAKTOR_BACKUP_DIR and TRAKTOR_MAX_BACKUPS reach the manager."""
    import traktor.resilience as res

    wanted = tmp_path / "var-backups"
    monkeypatch.setattr(res, "TRAKTOR_BACKUP_DIR", wanted)
    monkeypatch.setattr(res, "TRAKTOR_MAX_BACKUPS", 1)
    monkeypatch.setattr(res, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(res, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setattr(res, "CACHE_DIR", tmp_path / "missing-cache")

    manager = res.BackupManager()
    for run in range(3):
        manager.create_backup(reason=f"run{run}")

    assert manager.backup_dir == wanted
    assert manager.max_backups == 1
    assert wanted.exists()
    assert len(list(wanted.iterdir())) == 1


# ---------------------------------------------------------------------------
# Issue #81: a doctored backup must not expand past the size bound on restore
# ---------------------------------------------------------------------------


class TestBackupRestoreIsBounded:
    """A backup archive is input from disk, and a small one can be a bomb."""

    @pytest.fixture
    def restore_env(self, tmp_path, monkeypatch):
        import traktor.resilience as res

        config_file = tmp_path / "config.json"
        env_file = tmp_path / ".env"
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.setattr(res, "CONFIG_FILE", config_file)
        monkeypatch.setattr(res, "ENV_FILE", env_file)
        monkeypatch.setattr(res, "CACHE_DIR", cache_dir)
        return res, tmp_path, cache_dir

    @staticmethod
    def _doctored_backup(tmp_path, payload_size):
        import hashlib
        from datetime import datetime, timezone

        backup = tmp_path / "doctored_backup"
        (backup / "cache").mkdir(parents=True)
        raw = b"0" * payload_size
        with gzip.open(backup / "cache" / "bomb.json.gz", "wb", compresslevel=9) as f:
            f.write(raw)
        manifest = {
            "created": datetime.now(timezone.utc).isoformat(),
            "reason": "manual",
            "version": "1.0.0",
            "items": {
                "cache": {
                    "source": str(tmp_path / "cache"),
                    "checksum": hashlib.sha256(raw).hexdigest(),
                    "compressed": True,
                }
            },
        }
        (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return backup

    def test_a_bomb_is_refused_before_anything_is_restored(self, restore_env, monkeypatch):
        import traktor.utils as utils

        monkeypatch.setattr(utils, "MAX_DECOMPRESSED_BYTES", 4096, raising=False)
        res, tmp_path, cache_dir = restore_env
        backup = self._doctored_backup(tmp_path, 256 * 1024)
        manager = res.BackupManager(backup_dir=tmp_path / "backups", compress=True)

        assert manager.restore_backup(backup) is False
        assert cache_dir.exists()
        assert list(cache_dir.iterdir()) == []

    def test_a_bomb_that_skips_verification_leaves_no_partial_file(self, restore_env, monkeypatch):
        import traktor.utils as utils

        monkeypatch.setattr(utils, "MAX_DECOMPRESSED_BYTES", 4096, raising=False)
        res, tmp_path, cache_dir = restore_env
        backup = self._doctored_backup(tmp_path, 256 * 1024)
        manager = res.BackupManager(backup_dir=tmp_path / "backups", compress=True)

        assert manager.restore_backup(backup, verify=False) is False
        assert list(cache_dir.iterdir()) == []

    def test_an_ordinary_backup_still_restores(self, restore_env):
        res, tmp_path, cache_dir = restore_env
        cache_dir.joinpath("plex_library_cache.json.gz").write_text("{}", encoding="utf-8")
        manager = res.BackupManager(backup_dir=tmp_path / "backups", compress=True)
        backup = manager.create_backup(reason="test")
        cache_dir.joinpath("plex_library_cache.json.gz").unlink()

        assert manager.restore_backup(backup) is True
        assert cache_dir.joinpath("plex_library_cache.json.gz").exists()


# ---------------------------------------------------------------------------
# Issue #107: overlapping backup runs deleted each other's snapshots
# ---------------------------------------------------------------------------


class TestOverlappingBackups:
    """A backup run must not see, or die on, a snapshot another run owns."""

    @pytest.fixture
    def backup_env(self, tmp_path, monkeypatch):
        import traktor.resilience as res

        config_file = tmp_path / "config.json"
        config_file.write_text('{"key": "value"}')
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "entry.json").write_text('{"cached": true}')
        env_file = tmp_path / ".env"
        env_file.write_text("TRAKT_CLIENT_ID=abc\nTRAKT_ACCESS_TOKEN=tok1234567890\n")

        monkeypatch.setattr(res, "CONFIG_FILE", config_file)
        monkeypatch.setattr(res, "ENV_FILE", env_file)
        monkeypatch.setattr(res, "CACHE_DIR", cache_dir)

        manager = res.BackupManager(backup_dir=tmp_path / "backups", max_backups=2, compress=True)
        return manager, res

    def test_a_snapshot_is_invisible_until_its_manifest_is_written(self, backup_env, monkeypatch):
        manager, res = backup_env
        visible_while_writing = []
        real_write = res.write_json_atomic

        def spy(path, data, **kwargs):
            prefix = res.BACKUP_DIR_PREFIX
            visible_while_writing.extend(
                p.name for p in manager.backup_dir.iterdir() if p.name.startswith(prefix)
            )
            return real_write(path, data, **kwargs)

        monkeypatch.setattr(res, "write_json_atomic", spy)
        backup_path = manager.create_backup(reason="overlap")

        assert visible_while_writing == []
        assert backup_path.name.startswith(res.BACKUP_DIR_PREFIX)
        assert (backup_path / "manifest.json").exists()

    def test_cleanup_ignores_a_snapshot_another_run_removed(self, backup_env, monkeypatch):
        manager, res = backup_env
        for i in range(3):
            manager.create_backup(reason=f"run-{i}")

        def gone(path):
            raise FileNotFoundError(str(path))

        monkeypatch.setattr(res.shutil, "rmtree", gone)
        manager._cleanup_old_backups()
        assert len(manager.list_backups()) == 3


# ---------------------------------------------------------------------------
# Issue #115: the runbook's circuit breaker variables were never read
# ---------------------------------------------------------------------------


def test_breaker_tuning_comes_from_the_documented_variables(monkeypatch):
    import importlib

    import traktor.resilience as resilience
    import traktor.settings as settings

    monkeypatch.setenv("TRAKTOR_TRAKT_FAILURE_THRESHOLD", "3")
    monkeypatch.setenv("TRAKTOR_TRAKT_COOLDOWN_SECONDS", "120")
    monkeypatch.setenv("TRAKTOR_PLEX_FAILURE_THRESHOLD", "5")
    monkeypatch.setenv("TRAKTOR_PLEX_COOLDOWN_SECONDS", "60")
    try:
        importlib.reload(settings)
        importlib.reload(resilience)

        assert resilience.trakt_circuit_breaker.failure_threshold == 3
        assert resilience.trakt_circuit_breaker.cooldown_seconds == 120.0
        assert resilience.plex_circuit_breaker.failure_threshold == 5
        assert resilience.plex_circuit_breaker.cooldown_seconds == 60.0
    finally:
        for name in (
            "TRAKTOR_TRAKT_FAILURE_THRESHOLD",
            "TRAKTOR_TRAKT_COOLDOWN_SECONDS",
            "TRAKTOR_PLEX_FAILURE_THRESHOLD",
            "TRAKTOR_PLEX_COOLDOWN_SECONDS",
        ):
            monkeypatch.delenv(name, raising=False)
        importlib.reload(settings)
        importlib.reload(resilience)


def test_an_unreadable_breaker_variable_keeps_the_default(monkeypatch):
    import importlib

    import traktor.resilience as resilience
    import traktor.settings as settings

    monkeypatch.setenv("TRAKTOR_TRAKT_FAILURE_THRESHOLD", "not-a-number")
    try:
        importlib.reload(settings)
        importlib.reload(resilience)

        assert resilience.trakt_circuit_breaker.failure_threshold == 5
    finally:
        monkeypatch.delenv("TRAKTOR_TRAKT_FAILURE_THRESHOLD", raising=False)
        importlib.reload(settings)
        importlib.reload(resilience)
