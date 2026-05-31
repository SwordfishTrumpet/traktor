import sys

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
