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
