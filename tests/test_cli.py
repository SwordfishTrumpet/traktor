import sys

import pytest

from traktor import cli


def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["traktor"])

    args = cli.parse_args()

    assert args.plex_url is None
    assert args.plex_token is None
    assert args.force_auth is False
    assert args.verbose is False
    assert args.refresh_cache is False
    assert args.workers == cli.MAX_WORKERS
    # Watch sync defaults
    assert args.sync_watched is False
    assert args.watch_direction == "both"
    assert args.watch_conflict == "newest"
    assert args.dry_run is False
    assert args.sync_movies_only is False
    assert args.sync_shows_only is False
    assert args.backfill_history is False


def test_parse_args_custom_values(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "traktor",
            "--plex-url",
            "http://plex.local:32400",
            "--plex-token",
            "secret",
            "--force-auth",
            "--verbose",
            "--refresh-cache",
            "--workers",
            "12",
        ],
    )

    args = cli.parse_args()

    assert args.plex_url == "http://plex.local:32400"
    assert args.plex_token == "secret"
    assert args.force_auth is True
    assert args.verbose is True
    assert args.refresh_cache is True
    assert args.workers == 12


def test_parse_args_watch_sync(monkeypatch):
    """Test watch sync CLI arguments."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "traktor",
            "--sync-watched",
            "--watch-direction",
            "plex-to-trakt",
            "--watch-conflict",
            "plex",
            "--dry-run",
            "--sync-movies-only",
        ],
    )

    args = cli.parse_args()

    assert args.sync_watched is True
    assert args.watch_direction == "plex-to-trakt"
    assert args.watch_conflict == "plex"
    assert args.dry_run is True
    assert args.sync_movies_only is True
    assert args.sync_shows_only is False


def test_parse_args_structured_logging(monkeypatch):
    """Test structured logging CLI argument."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["traktor", "--structured-logging"],
    )
    args = cli.parse_args()
    assert args.structured_logging is True


def test_parse_args_performance_report(monkeypatch):
    """Test performance report CLI argument."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["traktor", "--performance-report"],
    )
    args = cli.parse_args()
    assert args.performance_report is True


def test_parse_args_structured_logging_default(monkeypatch):
    """Test structured logging and performance report defaults."""
    monkeypatch.setattr(sys, "argv", ["traktor"])
    args = cli.parse_args()
    assert args.structured_logging is False
    assert args.performance_report is False


def test_parse_args_interactive(monkeypatch):
    """Test interactive mode CLI argument."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["traktor", "--interactive"],
    )
    args = cli.parse_args()
    assert args.interactive is True


def test_parse_args_interactive_default(monkeypatch):
    """Test interactive mode default is False."""
    monkeypatch.setattr(sys, "argv", ["traktor"])
    args = cli.parse_args()
    assert args.interactive is False


def test_parse_args_undo(monkeypatch):
    """Test undo CLI argument."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["traktor", "--undo"],
    )
    args = cli.parse_args()
    assert args.undo is True


def test_parse_args_undo_default(monkeypatch):
    """Test undo default is False."""
    monkeypatch.setattr(sys, "argv", ["traktor"])
    args = cli.parse_args()
    assert args.undo is False


def test_parse_args_check_update(monkeypatch):
    """Test check-update CLI argument."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["traktor", "--check-update"],
    )
    args = cli.parse_args()
    assert args.check_update is True


def test_parse_args_check_update_default(monkeypatch):
    """Test check-update default is False."""
    monkeypatch.setattr(sys, "argv", ["traktor"])
    args = cli.parse_args()
    assert args.check_update is False


def test_parse_args_apply_update(monkeypatch):
    """Test apply-update CLI argument."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["traktor", "--apply-update"],
    )
    args = cli.parse_args()
    assert args.apply_update is True


def test_parse_args_apply_update_default(monkeypatch):
    """Test apply-update default is False."""
    monkeypatch.setattr(sys, "argv", ["traktor"])
    args = cli.parse_args()
    assert args.apply_update is False


def _make_args(monkeypatch, *extra):
    """Build a real argparse Namespace with optional extra flags."""
    import sys as _sys

    monkeypatch.setattr(_sys, "argv", ["traktor", *extra])
    return cli.parse_args()


def test_run_health_check_healthy(monkeypatch, capsys):
    """Test health check returns 0 when all components healthy."""

    monkeypatch.setattr(cli.health_checker, "register", lambda *a, **k: None)
    monkeypatch.setattr(
        cli.health_checker,
        "check_all",
        lambda: {
            "status": "healthy",
            "timestamp": "2026-01-01T00:00:00",
            "components": {"cache": "healthy", "config": "healthy"},
        },
    )

    exit_code = cli._run_health_check()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "HEALTHY" in out


def test_run_health_check_unhealthy(monkeypatch, capsys):
    """Test health check returns 1 when a component is unhealthy."""
    monkeypatch.setattr(cli.health_checker, "register", lambda *a, **k: None)
    monkeypatch.setattr(
        cli.health_checker,
        "check_all",
        lambda: {
            "status": "unhealthy",
            "timestamp": "2026-01-01T00:00:00",
            "components": {"cache": "unhealthy"},
        },
    )

    exit_code = cli._run_health_check()

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "UNHEALTHY" in out


def test_run_backup(monkeypatch, capsys, tmp_path):
    """Test manual backup creation."""
    backup_path = tmp_path / "backup-2026"
    monkeypatch.setattr(cli.backup_manager, "create_backup", lambda reason="manual": backup_path)

    exit_code = cli._run_backup(_make_args(monkeypatch))

    assert exit_code == 0
    assert "backup-2026" in capsys.readouterr().out


def test_list_backups_empty(monkeypatch, capsys):
    """Test listing backups when none exist."""
    monkeypatch.setattr(cli.backup_manager, "list_backups", lambda: [])

    assert cli._list_backups() == 0
    assert "No backups found." in capsys.readouterr().out


def test_list_backups_with_items(monkeypatch, capsys):
    """Test listing backups when backups exist."""
    monkeypatch.setattr(
        cli.backup_manager,
        "list_backups",
        lambda: [{"name": "b1", "created": "now", "reason": "manual", "path": "/tmp/b1"}],
    )

    assert cli._list_backups() == 0
    out = capsys.readouterr().out
    assert "b1" in out
    assert "manual" in out


def test_restore_backup_missing(monkeypatch, capsys, tmp_path):
    """Test restore with a non-existent backup path."""
    exit_code = cli._restore_backup(str(tmp_path / "nope"))

    assert exit_code == 1
    assert "Backup not found" in capsys.readouterr().out


def test_restore_backup_success(monkeypatch, capsys, tmp_path):
    """Test restore success."""
    backup_path = tmp_path / "backup"
    backup_path.mkdir()
    monkeypatch.setattr(cli.backup_manager, "restore_backup", lambda path, verify=True: True)

    exit_code = cli._restore_backup(str(backup_path))

    assert exit_code == 0
    assert "Restore completed" in capsys.readouterr().out


def test_restore_backup_failure(monkeypatch, capsys, tmp_path):
    """Test restore failure."""
    backup_path = tmp_path / "backup"
    backup_path.mkdir()
    monkeypatch.setattr(cli.backup_manager, "restore_backup", lambda path, verify=True: False)

    exit_code = cli._restore_backup(str(backup_path))

    assert exit_code == 1
    assert "Restore failed" in capsys.readouterr().out


def test_run_integrity_check_healthy(monkeypatch, capsys):
    """Test integrity check success path."""
    monkeypatch.setattr(
        cli.integrity_checker,
        "run_all_checks",
        lambda: {
            "overall_healthy": True,
            "timestamp": "t",
            "checks": {"config": {"healthy": True, "details": {}}},
        },
    )

    assert cli._run_integrity_check() == 0
    assert "All integrity checks passed" in capsys.readouterr().out


def test_run_integrity_check_failed(monkeypatch, capsys):
    """Test integrity check failure path."""
    monkeypatch.setattr(
        cli.integrity_checker,
        "run_all_checks",
        lambda: {
            "overall_healthy": False,
            "timestamp": "t",
            "checks": {"config": {"healthy": False, "details": {"error": "x"}}},
        },
    )

    assert cli._run_integrity_check() == 1
    assert "Some integrity checks failed" in capsys.readouterr().out


def test_show_circuit_status(monkeypatch, capsys):
    """Test circuit breaker status output."""
    stats = {
        "name": "trakt",
        "state": "closed",
        "failure_count": 0,
        "failure_threshold": 5,
        "cooldown_seconds": 60,
        "last_failure": None,
    }
    monkeypatch.setattr(cli.trakt_circuit_breaker, "get_stats", lambda: stats)
    monkeypatch.setattr(cli.plex_circuit_breaker, "get_stats", lambda: dict(stats, name="plex"))

    assert cli._show_circuit_status() == 0
    out = capsys.readouterr().out
    assert "trakt" in out
    assert "closed" in out


def test_handle_undo_no_snapshots(monkeypatch, capsys):
    """Test undo with no snapshots available."""
    monkeypatch.setattr(cli, "list_undo_snapshots", lambda: [])
    assert cli._handle_undo() == 1
    assert "No undo snapshots available" in capsys.readouterr().out


def test_handle_undo_non_interactive(monkeypatch, capsys):
    """Test undo shows snapshot info without claiming restoration (issue #4)."""
    monkeypatch.setattr(cli, "list_undo_snapshots", lambda: [{"operation_type": "playlist_sync"}])
    monkeypatch.setattr(
        cli,
        "restore_undo_snapshot",
        lambda: {
            "operation_type": "playlist_sync",
            "timestamp": "2026-01-01",
            "data": {"playlists": {"Movies": {"items": [1, 2]}}},
        },
    )

    assert cli._handle_undo() == 0
    out = capsys.readouterr().out
    assert "playlist_sync" in out
    # Honest messaging: no restore claim, inspection only
    assert "does not currently restore state from snapshots" in out
    assert "inspection only" in out.lower()
    # Snapshot contents are displayed for manual recovery
    assert "playlists" in out


def test_handle_undo_no_restore_prompt(monkeypatch, capsys):
    """--undo must not offer or simulate a restore path (issue #4)."""
    monkeypatch.setattr(cli, "list_undo_snapshots", lambda: [{"operation_type": "watch_sync"}])
    monkeypatch.setattr(
        cli,
        "restore_undo_snapshot",
        lambda: {"operation_type": "watch_sync", "timestamp": "2026-01-01"},
    )

    assert cli._handle_undo() == 0
    out = capsys.readouterr().out
    # The old misleading prompts are gone
    assert "Restore from this snapshot on next sync?" not in out
    assert "will be used for restoration on next sync run" not in out


def test_main_dispatch_diagnose(monkeypatch, capsys):
    """Test main() dispatch for --diagnose."""
    monkeypatch.setattr(cli, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli, "setup_logging", lambda verbose=False, structured=False: None)
    monkeypatch.setattr(cli, "run_diagnosis", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["traktor", "--diagnose"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0


def test_main_dispatch_health_check(monkeypatch, capsys):
    """Test main() dispatch for --health-check."""
    monkeypatch.setattr(cli, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli, "setup_logging", lambda verbose=False, structured=False: None)
    monkeypatch.setattr(cli, "_run_health_check", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["traktor", "--health-check"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0


def test_main_dispatch_backup_list(monkeypatch, capsys):
    """Test main() dispatch for --backup-list."""
    monkeypatch.setattr(cli, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli, "setup_logging", lambda verbose=False, structured=False: None)
    monkeypatch.setattr(cli, "_list_backups", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["traktor", "--backup-list"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0


def test_main_dispatch_integrity_check(monkeypatch, capsys):
    """Test main() dispatch for --integrity-check."""
    monkeypatch.setattr(cli, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli, "setup_logging", lambda verbose=False, structured=False: None)
    monkeypatch.setattr(cli, "_run_integrity_check", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["traktor", "--integrity-check"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0


def test_main_dispatch_circuit_status(monkeypatch, capsys):
    """Test main() dispatch for --circuit-status."""
    monkeypatch.setattr(cli, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli, "setup_logging", lambda verbose=False, structured=False: None)
    monkeypatch.setattr(cli, "_show_circuit_status", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["traktor", "--circuit-status"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0


def test_main_dispatch_check_update(monkeypatch, capsys):
    """Test main() dispatch for --check-update."""
    monkeypatch.setattr(cli, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli, "setup_logging", lambda verbose=False, structured=False: None)
    monkeypatch.setattr(cli, "check_and_print_update", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["traktor", "--check-update"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0


def test_main_sync_path(monkeypatch, capsys):
    """Test main() calls sync_lists for a normal run."""
    monkeypatch.setattr(cli, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli, "setup_logging", lambda verbose=False, structured=False: None)
    monkeypatch.setattr(cli, "sync_lists", lambda *a, **k: 0)
    monkeypatch.setattr(sys, "argv", ["traktor"])

    cli.main()  # exit code 0 means no sys.exit call


def test_main_sync_exit_code(monkeypatch, capsys):
    """Test main() propagates non-zero sync_lists exit code."""
    monkeypatch.setattr(cli, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli, "setup_logging", lambda verbose=False, structured=False: None)
    monkeypatch.setattr(cli, "sync_lists", lambda *a, **k: 3)
    monkeypatch.setattr(sys, "argv", ["traktor"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 3


def test_main_unhandled_exception(monkeypatch, capsys):
    """Test main() exits 1 on unhandled exception."""
    monkeypatch.setattr(cli, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli, "setup_logging", lambda verbose=False, structured=False: None)
    monkeypatch.setattr(
        cli, "sync_lists", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setattr(sys, "argv", ["traktor"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
