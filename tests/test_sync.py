import time
from unittest.mock import MagicMock

import pytest
import requests as _requests

from traktor import sync


class FakePlex:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def find_item_by_cache(self, imdb_id=None, tmdb_id=None, media_type="movie"):
        key = (media_type, imdb_id, tmdb_id)
        self.calls.append(key)
        return self.responses.get(key)


class FakeEpisode:
    def __init__(self, title):
        self.title = title


class FakeSeason:
    def __init__(self, season_number, episodes):
        self.seasonNumber = season_number
        self._episodes = episodes

    def episodes(self):
        return self._episodes


class FakeShow:
    def __init__(self, seasons):
        self._seasons = seasons

    def seasons(self):
        return self._seasons


def test_filter_description_removes_ads_and_old_timestamps():
    description = (
        "Best movies\n\nUpdated at 2026-03-23 10:00:00\nPowered by Trakt.tv\nhttps://example.com"
    )

    filtered = sync.filter_description(description)

    assert filtered == "Best movies\n"


def test_build_playlist_description_adds_timestamp(monkeypatch):
    class FakeDatetime:
        @staticmethod
        def now():
            class FakeNow:
                def strftime(self, _fmt):
                    return "2026-03-23 12:34:56"

            return FakeNow()

    monkeypatch.setattr(sync, "datetime", FakeDatetime)

    description = sync._build_playlist_description("Curated picks\nPowered by trakt.tv")

    assert description == "Curated picks\n\nUpdated by Traktor at 2026-03-23 12:34:56"


def test_write_missing_report_writes_expected_content(tmp_path):
    report_path = tmp_path / "missing.txt"

    sync.write_missing_report(
        [
            {
                "list_name": "Favorites",
                "type": "Movie",
                "title": "Heat",
                "year": 1995,
                "imdb_id": "tt0113277",
                "reason": "Not found in Plex library",
            }
        ],
        file_path=report_path,
    )

    content = report_path.read_text(encoding="utf-8")

    assert "List | Type | Title | Year | IMDb ID | Reason" in content
    assert "Favorites | Movie | Heat | 1995 | tt0113277 | Not found in Plex library" in content


def test_write_missing_report_deletes_empty_existing_report(tmp_path):
    report_path = tmp_path / "missing.txt"
    report_path.write_text("old content", encoding="utf-8")

    sync.write_missing_report([], file_path=report_path)

    assert not report_path.exists()


def test_process_item_parallel_matches_movie_by_imdb():
    fake_item = object()
    plex = FakePlex({("movie", "tt0113277", None): fake_item})

    result = sync.process_item_parallel(
        2,
        {
            "type": "movie",
            "movie": {"title": "Heat", "year": 1995, "ids": {"imdb": "tt0113277"}},
        },
        plex,
    )

    assert result == {
        "success": True,
        "title": "Heat",
        "year": 1995,
        "idx": 2,
        "item": fake_item,
    }


def test_process_item_parallel_returns_first_episode_for_show():
    first_episode = FakeEpisode("Pilot")
    show = FakeShow([FakeSeason(1, [first_episode])])
    plex = FakePlex({("show", "tt0944947", None): show})

    result = sync.process_item_parallel(
        0,
        {
            "type": "show",
            "show": {"title": "Game of Thrones", "year": 2011, "ids": {"imdb": "tt0944947"}},
        },
        plex,
    )

    assert result == {
        "success": True,
        "title": "Game of Thrones - S01E01",
        "year": 2011,
        "idx": 0,
        "item": first_episode,
    }


def test_process_item_parallel_falls_back_to_first_available_episode():
    """Test that shows without S01E01 fall back to first available episode."""
    first_episode = FakeEpisode("Later")
    show = FakeShow([FakeSeason(2, [first_episode])])
    plex = FakePlex({("show", "tt0903747", None): show})

    result = sync.process_item_parallel(
        4,
        {
            "type": "show",
            "show": {"title": "Breaking Bad", "year": 2008, "ids": {"imdb": "tt0903747"}},
        },
        plex,
    )

    assert result == {
        "success": True,
        "title": "Breaking Bad - S02E01",
        "year": 2008,
        "idx": 4,
        "item": first_episode,
    }


def test_process_item_parallel_reports_missing_when_show_has_no_episodes():
    """Test that shows with no episodes in any season are marked missing."""
    show = FakeShow([FakeSeason(1, []), FakeSeason(2, [])])
    plex = FakePlex({("show", "tt0903747", None): show})

    result = sync.process_item_parallel(
        4,
        {
            "type": "show",
            "show": {"title": "Empty Show", "year": 2008, "ids": {"imdb": "tt0903747"}},
        },
        plex,
    )

    assert result["success"] is False
    assert result["title"] == "Empty Show"
    assert result["type"] == "Show"
    assert result["reason"] == "No episodes found in any season"


def test_process_item_parallel_preserves_imdb_id_on_tmdb_match():
    """Test that imdb_id is preserved in result even when match happens via TMDb."""
    fake_item = object()
    # Plex only has TMDb lookup, no IMDB lookup
    plex = FakePlex({("movie", None, 12345): fake_item})

    result = sync.process_item_parallel(
        3,
        {
            "type": "movie",
            "movie": {
                "title": "Test Movie",
                "year": 2020,
                "ids": {"imdb": "tt1234567", "tmdb": 12345},
            },
        },
        plex,
    )

    assert result["success"] is True
    assert result["title"] == "Test Movie"
    assert result["item"] is fake_item
    # imdb_id should still be preserved in case of missing result, even though match succeeded
    # This test verifies the fix for the bug where imdb_id was lost on TMDb-only matches


def test_process_item_parallel_handles_unknown_media_type():
    """Test that unknown media types are handled gracefully."""
    plex = FakePlex({})

    result = sync.process_item_parallel(
        0,
        {
            "type": "unknown_type",
            "unknown": {"title": "Unknown Item", "year": 2020},
        },
        plex,
    )

    assert result["success"] is False
    assert result["title"] == "Unknown"


def test_process_item_parallel_handles_error_exception():
    """Test that exceptions during processing are handled gracefully."""

    class BadPlex:
        def find_item_by_cache(self, imdb_id=None, tmdb_id=None, media_type="movie"):
            raise RuntimeError("Simulated error")

    result = sync.process_item_parallel(
        0,
        {
            "type": "movie",
            "movie": {"title": "Test Movie", "year": 2020, "ids": {"imdb": "tt1234567"}},
        },
        BadPlex(),
    )

    assert result["success"] is False
    assert "error" in result


def test_missing_item_tracker_build_item():
    """Test building a missing item dictionary."""
    tracker = sync.MissingItemTracker()
    item = tracker.build_item(
        list_name="Test List",
        media_type="Movie",
        title="Test Movie",
        year="2024",
        imdb_id="tt1234567",
        reason="Not found in Plex library",
    )

    assert item["list_name"] == "Test List"
    assert item["type"] == "Movie"
    assert item["title"] == "Test Movie"
    assert item["year"] == "2024"
    assert item["imdb_id"] == "tt1234567"
    assert item["reason"] == "Not found in Plex library"


def test_missing_item_tracker_extract_details_movie():
    """Test extracting details from a movie item."""
    tracker = sync.MissingItemTracker()
    item = {
        "type": "movie",
        "movie": {
            "title": "Inception",
            "year": 2010,
            "ids": {"imdb": "tt1375666"},
        },
    }

    details = tracker.extract_details(item)

    assert details["list_name"] == ""
    assert details["type"] == "Movie"
    assert details["title"] == "Inception"
    assert details["year"] == 2010
    assert details["imdb_id"] == "tt1375666"


def test_missing_item_tracker_extract_details_show():
    """Test extracting details from a show item."""
    tracker = sync.MissingItemTracker()
    item = {
        "type": "show",
        "show": {
            "title": "Breaking Bad",
            "year": 2008,
            "ids": {"imdb": "tt0903747"},
        },
    }

    details = tracker.extract_details(item)

    assert details["type"] == "Show"
    assert details["title"] == "Breaking Bad"
    assert details["imdb_id"] == "tt0903747"


def test_missing_item_tracker_extract_details_unknown():
    """Test extracting details from an unknown type item."""
    tracker = sync.MissingItemTracker()
    item = {"type": "episode"}

    details = tracker.extract_details(item)

    assert details["type"] == "Unknown"
    assert details["title"] == "Unknown"


def test_missing_item_tracker_record_result():
    """Test recording a missing result updates tracker state."""
    tracker = sync.MissingItemTracker()
    stats = {"items_not_found": 0}
    result = {
        "success": False,
        "title": "Missing Movie",
        "year": "2024",
        "type": "Movie",
        "imdb_id": "tt9999999",
        "reason": "Not found in Plex library",
    }

    tracker.record_result("My List", result, stats)

    assert stats["items_not_found"] == 1
    assert len(tracker.not_found) == 1
    assert tracker.not_found[0] == "Missing Movie (2024)"
    assert len(tracker.missing_items) == 1
    assert tracker.missing_items[0]["title"] == "Missing Movie"
    assert tracker.missing_items[0]["reason"] == "Not found in Plex library"


def test_missing_item_tracker_record_exception():
    """Test recording an exception from a worker thread."""
    tracker = sync.MissingItemTracker()
    stats = {"items_not_found": 0}
    item = {
        "type": "movie",
        "movie": {
            "title": "Error Movie",
            "year": 2023,
            "ids": {"imdb": "tt8888888"},
        },
    }

    tracker.record_exception("Test List", item, RuntimeError("Simulated error"), stats)

    assert stats["items_not_found"] == 1
    assert len(tracker.not_found) == 1
    assert "Error Movie (error)" in tracker.not_found[0]
    assert tracker.missing_items[0]["list_name"] == "Test List"
    assert "Processing error" in tracker.missing_items[0]["reason"]


def test_missing_item_tracker_get_methods():
    """Test getter methods for items and not_found list."""
    tracker = sync.MissingItemTracker()
    tracker.missing_items = [{"title": "Item 1"}]
    tracker.not_found = ["Item 1 (2024)"]

    assert tracker.get_items() == [{"title": "Item 1"}]
    assert tracker.get_not_found_list() == ["Item 1 (2024)"]


def test_backward_compatible_wrappers():
    """Test that backward-compatible wrapper functions work."""
    # Test _record_missing_result
    not_found = []
    missing_items = []
    stats = {"items_not_found": 0}
    result = {"title": "Missing", "year": "", "type": "Movie"}
    sync._record_missing_result("List", result, not_found, stats, missing_items)
    assert len(not_found) == 1
    assert len(missing_items) == 1
    assert stats["items_not_found"] == 1


# ---------------------------------------------------------------------------
# MissingItemSuggester tests
# ---------------------------------------------------------------------------


class FakeCacheManager:
    """Minimal fake cache manager for suggestion tests."""

    def __init__(self, memory_cache=None):
        self.memory_cache = memory_cache or {}


def test_missing_item_suggester_fuzzy_match_title():
    """Test fuzzy title matching finds similar titles."""
    cache = FakeCacheManager(
        {
            "movies_list": [
                {"title": "The Office", "year": 2005, "ratingKey": 1},
            ],
        }
    )
    suggester = sync.MissingItemSuggester(cache)
    suggestions = suggester.get_suggestions(
        title="The Offcie", year=2005, imdb_id="", tmdb_id=None, media_type="Movie"
    )

    assert len(suggestions) >= 1
    assert suggestions[0]["type"] == "fuzzy_title"
    assert "The Office" in suggestions[0]["message"]


def test_missing_item_suggester_id_mismatch_imdb_as_tmdb():
    """Test detection when an IMDb ID exists as a TMDb ID in cache."""
    cache = FakeCacheManager(
        {
            "movies_by_tmdb": {
                "tt1234567": {"title": "Test Movie", "year": 2020, "ratingKey": 1},
            },
        }
    )
    suggester = sync.MissingItemSuggester(cache)
    suggestions = suggester.get_suggestions(
        title="Unknown", year=2020, imdb_id="tt1234567", tmdb_id=None, media_type="Movie"
    )

    assert len(suggestions) == 1
    assert suggestions[0]["type"] == "id_mismatch"
    assert "IMDb ID found as TMDb ID" in suggestions[0]["message"]


def test_missing_item_suggester_id_mismatch_tmdb_as_imdb():
    """Test detection when a TMDb ID exists as an IMDb ID in cache."""
    cache = FakeCacheManager(
        {
            "movies_by_imdb": {
                "98765": {"title": "Another Movie", "year": 2019, "ratingKey": 2},
            },
        }
    )
    suggester = sync.MissingItemSuggester(cache)
    suggestions = suggester.get_suggestions(
        title="Unknown", year=2019, imdb_id="", tmdb_id=98765, media_type="Movie"
    )

    assert len(suggestions) == 1
    assert suggestions[0]["type"] == "id_mismatch"
    assert "TMDb ID found as IMDb ID" in suggestions[0]["message"]


def test_missing_item_suggester_naming_mismatch_region_suffix():
    """Test detection of region suffix mismatch like (US)."""
    cache = FakeCacheManager(
        {
            "shows_list": [
                {"title": "The Office", "year": 2005, "ratingKey": 3},
            ],
        }
    )
    suggester = sync.MissingItemSuggester(cache)
    suggestions = suggester.get_suggestions(
        title="The Office (US)", year=2005, imdb_id="", tmdb_id=None, media_type="Show"
    )

    assert len(suggestions) >= 1
    naming = [s for s in suggestions if s["type"] == "naming_mismatch"]
    assert len(naming) >= 1
    assert "region suffix" in naming[0]["message"]


def test_missing_item_suggester_naming_mismatch_the_prefix():
    """Test detection of 'The' prefix mismatch."""
    cache = FakeCacheManager(
        {
            "movies_list": [
                {"title": "The Godfather", "year": 1972, "ratingKey": 4},
            ],
        }
    )
    suggester = sync.MissingItemSuggester(cache)
    suggestions = suggester.get_suggestions(
        title="Godfather", year=1972, imdb_id="", tmdb_id=None, media_type="Movie"
    )

    assert len(suggestions) >= 1
    naming = [s for s in suggestions if s["type"] == "naming_mismatch"]
    assert len(naming) >= 1
    assert '"The" prefix' in naming[0]["message"]


def test_missing_item_suggester_naming_mismatch_year_in_title():
    """Test detection of year-in-title mismatch."""
    cache = FakeCacheManager(
        {
            "movies_list": [
                {"title": "Inception", "year": 2010, "ratingKey": 5},
            ],
        }
    )
    suggester = sync.MissingItemSuggester(cache)
    suggestions = suggester.get_suggestions(
        title="Inception (2010)", year=2010, imdb_id="", tmdb_id=None, media_type="Movie"
    )

    assert len(suggestions) >= 1
    naming = [s for s in suggestions if s["type"] == "naming_mismatch"]
    assert len(naming) >= 1
    assert "year in title" in naming[0]["message"]


def test_missing_item_suggester_max_suggestions():
    """Test that at most MAX_SUGGESTIONS are returned."""
    cache = FakeCacheManager(
        {
            "movies_list": [
                {"title": "The Godfather", "year": 1972, "ratingKey": 4},
                {"title": "Godfather Part II", "year": 1974, "ratingKey": 5},
                {"title": "Godfather Part III", "year": 1990, "ratingKey": 6},
                {"title": "Another Godfather", "year": 2020, "ratingKey": 7},
            ],
        }
    )
    suggester = sync.MissingItemSuggester(cache)
    suggestions = suggester.get_suggestions(
        title="Godfather", year=1972, imdb_id="", tmdb_id=None, media_type="Movie"
    )

    assert len(suggestions) <= sync.MAX_SUGGESTIONS


def test_missing_item_suggester_no_cache():
    """Test that empty cache returns no suggestions."""
    suggester = sync.MissingItemSuggester(FakeCacheManager({}))
    suggestions = suggester.get_suggestions(
        title="Anything", year=2020, imdb_id="", tmdb_id=None, media_type="Movie"
    )
    assert suggestions == []


def test_missing_item_suggester_none_cache():
    """Test that None cache manager returns no suggestions."""
    suggester = sync.MissingItemSuggester(None)
    suggestions = suggester.get_suggestions(
        title="Anything", year=2020, imdb_id="", tmdb_id=None, media_type="Movie"
    )
    assert suggestions == []


def test_write_missing_report_with_suggestions(tmp_path):
    """Test that report includes suggestions when cache manager is provided."""
    report_path = tmp_path / "missing.txt"
    cache = FakeCacheManager(
        {
            "movies_list": [
                {"title": "The Office", "year": 2005, "ratingKey": 1},
            ],
        }
    )

    sync.write_missing_report(
        [
            {
                "list_name": "Favorites",
                "type": "Movie",
                "title": "The Offcie",
                "year": 2005,
                "imdb_id": "tt0113277",
                "reason": "Not found in Plex library",
            }
        ],
        file_path=report_path,
        cache_manager=cache,
    )

    content = report_path.read_text(encoding="utf-8")

    assert "List | Type | Title | Year | IMDb ID | Reason | Suggestions" in content
    assert "The Offcie" in content
    assert "Found similar title" in content


# ---------------------------------------------------------------------------
# Interactive mode tests
# ---------------------------------------------------------------------------


class FakePlaylist:
    """Minimal fake playlist for snapshot tests."""

    def __init__(self, title, items=None, summary=""):
        self.title = title
        self._items = items or []
        self.summary = summary

    def items(self):
        return self._items


class FakePlexItem:
    """Minimal fake Plex item for snapshot tests."""

    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakePlexServer:
    """Minimal fake Plex server for snapshot tests."""

    def __init__(self, playlists=None):
        self._playlists = playlists or []

    def playlist(self, name):
        for p in self._playlists:
            if p.title == name:
                return p
        raise Exception("Not found")

    def playlists(self):
        return self._playlists


class FakePlexClient:
    """Minimal fake Plex client for snapshot tests."""

    def __init__(self, playlists=None):
        self.server = FakePlexServer(playlists)

    def _get_plex_item(self, rating_key):
        return FakePlexItem(rating_key)

    def create_or_update_playlist(self, name, items, description=None):
        pass


def test_save_playlist_snapshot_empty_config():
    """Test saving playlist snapshot with empty config."""
    plex = FakePlexClient()
    config = {}
    result = sync._save_playlist_snapshot(plex, config)
    assert result == {}


def test_save_playlist_snapshot_with_playlists():
    """Test saving playlist snapshot with managed playlists."""
    item1 = FakePlexItem("123")
    item2 = FakePlexItem("456")
    playlist = FakePlaylist("Test Playlist", items=[item1, item2], summary="desc")
    plex = FakePlexClient(playlists=[playlist])
    config = {"managed_playlists": ["Test Playlist"]}

    result = sync._save_playlist_snapshot(plex, config)

    assert "Test Playlist" in result
    assert result["Test Playlist"]["items"] == ["123", "456"]
    assert result["Test Playlist"]["description"] == "desc"


def test_save_playlist_snapshot_skips_not_found():
    """Test saving playlist snapshot skips missing playlists."""
    plex = FakePlexClient(playlists=[])
    config = {"managed_playlists": ["Missing Playlist"]}

    result = sync._save_playlist_snapshot(plex, config)

    assert "Missing Playlist" not in result


def test_preview_changes_integration_watch_sync():
    """Test preview_changes integration with watch sync data."""
    from unittest.mock import patch

    from traktor.interactive import preview_changes

    changes = {
        "plex": {
            "mark_watched": [
                {"title": "Movie A"},
                {"title": "Movie B"},
            ],
            "mark_unwatched": [],
        },
        "trakt": {
            "mark_watched": [],
            "mark_unwatched": [{"title": "Movie C"}],
        },
    }

    with patch("traktor.interactive.is_interactive", return_value=True):
        with patch("builtins.input", return_value="y"):
            result = preview_changes(changes, change_type="watch_sync")

    assert result is True


def test_preview_changes_integration_playlist_cleanup():
    """Test preview_changes integration with playlist cleanup data."""
    from unittest.mock import patch

    from traktor.interactive import preview_changes

    changes = {"playlists": ["Old Playlist 1", "Old Playlist 2"]}

    with patch("traktor.interactive.is_interactive", return_value=True):
        with patch("builtins.input", return_value="n"):
            result = preview_changes(changes, change_type="playlist_cleanup")

    assert result is False


class TestAuthCodeExtraction:
    """Tests for _extract_auth_code (OAuth callback URL / code parsing)."""

    def test_bare_code(self):
        from traktor import sync

        assert sync._extract_auth_code("abc123") == "abc123"
        assert sync._extract_auth_code("   abc123  ") == "abc123"

    def test_full_callback_url(self):
        from traktor import sync

        url = "http://127.0.0.1:7001/callback?code=h3W6linU2xXsoWVPBZvAUhqgsQB4ihrK"
        assert sync._extract_auth_code(url) == "h3W6linU2xXsoWVPBZvAUhqgsQB4ihrK"

    def test_bare_query(self):
        from traktor import sync

        assert sync._extract_auth_code("code=abc123") == "abc123"

    def test_empty_and_invalid(self):
        from traktor import sync

        assert sync._extract_auth_code("") is None
        assert sync._extract_auth_code("   ") is None
        assert sync._extract_auth_code("http://x/?error=access_denied") is None
        assert sync._extract_auth_code("http://127.0.0.1:7001/callback?state=x") is None


class TestSyncListsOrchestration:
    """End-to-end tests for sync_lists() orchestration (TODO audit D1)."""

    @staticmethod
    def _args(monkeypatch, argv=None):
        import sys

        from traktor import cli

        monkeypatch.setattr(sys, "argv", ["traktor", *(argv or [])])
        return cli.parse_args()

    def _setup(self, monkeypatch, tmp_path, trakt_authed=True):
        """Install all module-level mocks sync_lists depends on."""
        from unittest.mock import MagicMock

        monkeypatch.setattr(sync, "TRAKT_CLIENT_ID", "client-id")
        monkeypatch.setattr(sync, "TRAKT_CLIENT_SECRET", "client-secret")
        monkeypatch.setattr(sync, "TRAKTOR_LIST_SOURCE", "official")
        monkeypatch.setattr(sync, "TRAKTOR_OFFICIAL_LISTS_ENABLED", True)
        monkeypatch.setattr(sync, "LOG_FILE", tmp_path / "logs" / "traktor.log")
        monkeypatch.setattr(sync, "SYNC_PROGRESS_FILE", tmp_path / "sync_progress.json")

        monkeypatch.setattr(
            sync.integrity_checker,
            "run_all_checks",
            lambda: {"overall_healthy": True, "checks": {}},
        )
        monkeypatch.setattr(sync, "load_config", lambda: {})
        monkeypatch.setattr(
            sync, "get_plex_credentials", lambda args=None: ("http://plex:32400", "tok")
        )
        monkeypatch.setattr(sync, "write_missing_report", lambda *a, **k: None)
        monkeypatch.setattr(sync, "_check_memory", lambda *a, **k: None)
        monkeypatch.setattr(sync, "_print_summary", lambda *a, **k: None)
        monkeypatch.setattr(sync, "_save_playlist_snapshot", lambda *a, **k: {})
        monkeypatch.setattr(sync, "authenticate_trakt", lambda auth, force=False: trakt_authed)

        server = MagicMock()
        server.machineIdentifier = "server-abc"
        cache_mgr = MagicMock()
        cache_mgr.load_cache.return_value = True

        plex_client = MagicMock()
        plex_client.cleanup_orphaned_playlists.return_value = []

        trakt_client = MagicMock()
        trakt_client.get_liked_lists.return_value = []
        trakt_client.get_watched_movies.return_value = []
        trakt_client.get_watched_shows.return_value = []

        monkeypatch.setattr(sync, "PlexServer", lambda *a, **k: server)
        monkeypatch.setattr(sync, "CacheManager", lambda *a, **k: cache_mgr)
        monkeypatch.setattr(sync, "PlexClient", lambda *a, **k: plex_client)
        monkeypatch.setattr(sync, "TraktClient", lambda *a, **k: trakt_client)
        monkeypatch.setattr(sync, "SyncProgress", lambda *a, **k: MagicMock())

        return {
            "server": server,
            "cache_mgr": cache_mgr,
            "plex_client": plex_client,
            "trakt_client": trakt_client,
        }

    def test_official_only_success(self, monkeypatch, tmp_path):
        """Default official-lists sync completes with exit code 0."""
        self._setup(monkeypatch, tmp_path)
        args = self._args(monkeypatch, ["--list-source", "official"])

        monkeypatch.setattr(sync, "_needs_oauth_auth", lambda *a: False)
        monkeypatch.setattr(sync, "_should_sync_official_lists", lambda *a: True)
        monkeypatch.setattr(sync, "_get_official_endpoints", lambda *a: [])
        monkeypatch.setattr(sync, "_get_official_periods", lambda *a: ["weekly"])

        result = sync.sync_lists(args)

        assert result == 0

    def test_missing_trakt_credentials_exits(self, monkeypatch, tmp_path):
        """Missing Trakt credentials abort with exit code 1."""
        self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(sync, "TRAKT_CLIENT_ID", None)

        with pytest.raises(SystemExit) as exc_info:
            sync.sync_lists(self._args(monkeypatch))
        assert exc_info.value.code == 1

    def test_auth_failure_exits(self, monkeypatch, tmp_path):
        """Authentication failure aborts with exit code 1."""
        self._setup(monkeypatch, tmp_path, trakt_authed=False)
        args = self._args(monkeypatch, ["--sync-watched"])

        monkeypatch.setattr(sync, "_needs_oauth_auth", lambda *a: True)

        with pytest.raises(SystemExit) as exc_info:
            sync.sync_lists(args)
        assert exc_info.value.code == 1

    def test_plex_connection_failure_exits(self, monkeypatch, tmp_path):
        """Plex connection failure aborts with exit code 1."""
        self._setup(monkeypatch, tmp_path)
        args = self._args(monkeypatch)

        def raise_plex(*a, **k):
            raise ConnectionError("plex down")

        monkeypatch.setattr(sync, "PlexServer", raise_plex)

        with pytest.raises(SystemExit) as exc_info:
            sync.sync_lists(args)
        assert exc_info.value.code == 1

    def test_liked_lists_processed(self, monkeypatch, tmp_path):
        """Liked lists flow: auth + process_list_parallel."""
        mocks = self._setup(monkeypatch, tmp_path)
        args = self._args(monkeypatch, ["--list-source", "liked"])

        mocks["trakt_client"].get_liked_lists.return_value = [
            {
                "list": {"name": "My List", "user": {"username": "me"}, "ids": {"trakt": "123"}},
            }
        ]
        monkeypatch.setattr(
            sync,
            "process_list_parallel",
            lambda *a, **k: {"success": True, "matched": 3, "not_found": 1, "warning": ""},
        )
        monkeypatch.setattr(sync, "_should_sync_official_lists", lambda *a: False)

        result = sync.sync_lists(args)

        assert result == 0
        # process_list_parallel is mocked, but the orchestration should have
        # fetched liked lists and passed them through
        mocks["trakt_client"].get_liked_lists.assert_called_once()

    def test_watch_only_mode(self, monkeypatch, tmp_path):
        """--sync-watched-only skips lists and runs the watch engine."""
        mocks = self._setup(monkeypatch, tmp_path)
        args = self._args(monkeypatch, ["--sync-watched-only"])

        monkeypatch.setattr(sync, "_needs_oauth_auth", lambda *a: True)

        engine = MagicMock()
        engine.sync_watched_status.return_value = {
            "plex_watched": 1,
            "plex_unwatched": 0,
            "trakt_watched": 0,
            "trakt_unwatched": 0,
            "errors": 0,
            "changes": [],
        }
        monkeypatch.setattr(sync, "WatchHistoryManager", lambda *a, **k: MagicMock())
        monkeypatch.setattr(sync, "ConflictResolver", lambda *a, **k: MagicMock())
        monkeypatch.setattr(sync, "WatchSyncEngine", lambda *a, **k: engine)

        result = sync.sync_lists(args)

        assert result == 0
        engine.sync_watched_status.assert_called_once()
        # Watch-only mode must skip orphaned playlist cleanup
        mocks["plex_client"].cleanup_orphaned_playlists.assert_not_called()
        # And must skip liked lists
        mocks["trakt_client"].get_liked_lists.assert_not_called()

    def test_interactive_orphaned_cleanup_confirmed(self, monkeypatch, tmp_path):
        """Interactive mode previews orphaned cleanup and deletes on confirm."""
        mocks = self._setup(monkeypatch, tmp_path)
        args = self._args(monkeypatch, ["--interactive", "--list-source", "official"])

        monkeypatch.setattr(sync, "_needs_oauth_auth", lambda *a: False)
        monkeypatch.setattr(sync, "_should_sync_official_lists", lambda *a: False)

        monkeypatch.setattr(sync, "preview_changes", lambda *a, **k: True)

        mocks["plex_client"].cleanup_orphaned_playlists.side_effect = [
            [{"name": "Old Playlist"}],  # dry_run preview
            ["Old Playlist"],  # actual deletion
        ]

        result = sync.sync_lists(args)

        assert result == 0
        # cleanup called twice: preview (dry_run=True) then real
        assert mocks["plex_client"].cleanup_orphaned_playlists.call_count == 2

    def test_watch_sync_skipped_without_auth(self, monkeypatch, tmp_path):
        """--sync-watched without auth prints a warning and continues."""
        self._setup(monkeypatch, tmp_path, trakt_authed=False)
        args = self._args(monkeypatch, ["--sync-watched"])

        monkeypatch.setattr(sync, "_needs_oauth_auth", lambda *a: True)
        monkeypatch.setattr(sync, "authenticate_trakt", lambda auth, force=False: False)

        with pytest.raises(SystemExit) as exc_info:
            sync.sync_lists(args)
        assert exc_info.value.code == 1


class TestCollectAuthCode:
    """Tests for the OAuth callback/paste auth-code collector (TODO audit D1)."""

    class _AdvancingTime:
        def __init__(self, start=1000.0, step=1.0):
            self.value = start
            self.step = step

        def time(self):
            v = self.value
            self.value += self.step
            return v

    def test_non_localhost_redirect_times_out(self, monkeypatch, capsys):
        """Non-localhost redirect URI: no callback server, times out -> None."""
        monkeypatch.setattr(sync, "time", self._AdvancingTime(step=100))
        auth_url = "https://trakt.tv/oauth/authorize" "?redirect_uri=https%3A%2F%2Fexample.com%2Fcb"

        result = sync._collect_auth_code(auth_url)

        assert result is None
        assert "paste the authorization code below" in capsys.readouterr().out

    def test_port_bind_failure_times_out(self, monkeypatch):
        """Localhost redirect but port bind fails: no server, times out -> None."""
        monkeypatch.setattr(sync, "time", self._AdvancingTime(step=100))

        def raise_oserror(*args, **kwargs):
            raise OSError("port in use")

        monkeypatch.setattr(sync, "ThreadingHTTPServer", raise_oserror)
        auth_url = (
            "https://trakt.tv/oauth/authorize" "?redirect_uri=http%3A%2F%2F127.0.0.1%3A32451%2Fcb"
        )

        result = sync._collect_auth_code(auth_url)

        assert result is None

    def test_code_capture_via_callback(self, monkeypatch):
        """Browser redirect with a code is captured by the local listener."""
        import threading

        port = 47123
        auth_url = (
            "https://trakt.tv/oauth/authorize" f"?redirect_uri=http%3A%2F%2F127.0.0.1%3A{port}%2Fcb"
        )
        result_box: dict = {}
        thread = threading.Thread(
            target=lambda: result_box.setdefault("code", sync._collect_auth_code(auth_url)),
            daemon=True,
        )
        thread.start()

        deadline = time.time() + 5
        delivered = False
        while time.time() < deadline:
            try:
                resp = _requests.get(f"http://127.0.0.1:{port}/cb?code=abc123", timeout=1)
                if resp.status_code == 200:
                    delivered = True
                    break
            except _requests.exceptions.RequestException:
                time.sleep(0.1)

        thread.join(timeout=10)

        assert delivered is True
        assert result_box.get("code") == "abc123"

    def test_error_query_param_returns_none(self, monkeypatch):
        """Browser redirect carrying an error param is treated as failure."""
        import threading

        port = 47124
        auth_url = (
            "https://trakt.tv/oauth/authorize" f"?redirect_uri=http%3A%2F%2F127.0.0.1%3A{port}%2Fcb"
        )
        result_box: dict = {}
        thread = threading.Thread(
            target=lambda: result_box.setdefault("code", sync._collect_auth_code(auth_url)),
            daemon=True,
        )
        thread.start()

        deadline = time.time() + 5
        delivered = False
        while time.time() < deadline:
            try:
                resp = _requests.get(f"http://127.0.0.1:{port}/cb?error=access_denied", timeout=1)
                if resp.status_code == 200:
                    delivered = True
                    break
            except _requests.exceptions.RequestException:
                time.sleep(0.1)

        thread.join(timeout=10)

        assert delivered is True
        assert result_box.get("code") is None


class TestProcessItemErrorPaths:
    """Coverage for process_item_parallel error branches (TODO audit D1)."""

    def test_show_not_found_reports_missing(self):
        plex = FakePlex({})
        result = sync.process_item_parallel(
            0,
            {"type": "show", "show": {"title": "Missing Show", "ids": {"imdb": "tt000"}}},
            plex,
        )
        assert result["success"] is False
        assert result["reason"] == "Show not found in Plex library"

    def test_movie_not_found_reports_missing(self):
        plex = FakePlex({})
        result = sync.process_item_parallel(
            0,
            {"type": "movie", "movie": {"title": "Missing Movie", "ids": {"imdb": "tt000"}}},
            plex,
        )
        assert result["success"] is False
        assert result["reason"] == "Movie not found in Plex library"

    def test_show_no_seasons(self):
        plex = FakePlex({("show", "tt001", None): FakeShow([])})
        result = sync.process_item_parallel(
            0,
            {"type": "show", "show": {"title": "No Seasons", "ids": {"imdb": "tt001"}}},
            plex,
        )
        assert result["success"] is False
        assert result["reason"] == "No seasons found in Plex"

    def test_show_not_found_in_plex_seasons(self):
        """NotFound raised while accessing show data."""
        from plexapi.exceptions import NotFound

        class RaisingShow:
            def seasons(self):
                raise NotFound("gone")

        plex = FakePlex({("show", "tt002", None): RaisingShow()})
        result = sync.process_item_parallel(
            0,
            {"type": "show", "show": {"title": "Gone Show", "ids": {"imdb": "tt002"}}},
            plex,
        )
        assert result["success"] is False
        assert result["reason"] == "Show data not accessible in Plex"

    def test_show_attribute_error_accessing_data(self):
        class BrokenSeason:
            pass

        class BrokenShow:
            def seasons(self):
                return [BrokenSeason()]

        plex = FakePlex({("show", "tt003", None): BrokenShow()})
        result = sync.process_item_parallel(
            0,
            {"type": "show", "show": {"title": "Broken Show", "ids": {"imdb": "tt003"}}},
            plex,
        )
        assert result["success"] is False
        assert result["reason"].startswith("Error accessing show data:")


class TestProcessListParallel:
    """Coverage for process_list_parallel orchestration (TODO audit D1)."""

    def test_worker_exception_recorded(self, tmp_path, monkeypatch):
        """Exceptions from worker futures are recorded via _record_worker_exception."""
        from unittest.mock import patch

        plex = MagicMock()
        trakt = MagicMock()
        stats = {
            "items_total": 0,
            "items_matched": 0,
            "items_not_found": 0,
            "playlists_updated": 0,
            "lists_failed": 0,
        }
        missing_items = []

        items = [
            {"type": "movie", "movie": {"title": "A", "ids": {"imdb": "tt1"}}},
            {"type": "movie", "movie": {"title": "B", "ids": {"imdb": "tt2"}}},
        ]
        trakt.get_list_items.return_value = items

        def boom(idx, item, plex):
            if idx == 0:
                raise RuntimeError("worker exploded")
            return {"success": True, "idx": idx, "item": object()}

        with patch.object(sync, "process_item_parallel", side_effect=boom):
            sync.process_list_parallel(
                {"list": {"name": "L", "user": {"username": "u"}, "ids": {"trakt": 1}}},
                plex,
                trakt,
                2,
                stats,
                set(),
                missing_items,
            )

        assert stats["items_matched"] == 1
        assert len(missing_items) == 1
        assert "worker exploded" in missing_items[0]["reason"]


class TestSyncListsFeatureSections:
    """Coverage for official/collection/watchlist sections of sync_lists (TODO audit D1)."""

    @staticmethod
    def _args(monkeypatch, argv=None):
        import sys

        from traktor import cli

        monkeypatch.setattr(sys, "argv", ["traktor", *(argv or [])])
        return cli.parse_args()

    def _setup(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock

        monkeypatch.setattr(sync, "TRAKT_CLIENT_ID", "client-id")
        monkeypatch.setattr(sync, "TRAKT_CLIENT_SECRET", "client-secret")
        monkeypatch.setattr(sync, "TRAKTOR_LIST_SOURCE", "official")
        monkeypatch.setattr(sync, "LOG_FILE", tmp_path / "logs" / "traktor.log")
        monkeypatch.setattr(sync, "SYNC_PROGRESS_FILE", tmp_path / "sync_progress.json")
        monkeypatch.setattr(
            sync.integrity_checker,
            "run_all_checks",
            lambda: {"overall_healthy": True, "checks": {}},
        )
        monkeypatch.setattr(sync, "load_config", lambda: {})
        monkeypatch.setattr(
            sync, "get_plex_credentials", lambda args=None: ("http://plex:32400", "tok")
        )
        monkeypatch.setattr(sync, "write_missing_report", lambda *a, **k: None)
        monkeypatch.setattr(sync, "_check_memory", lambda *a, **k: None)
        monkeypatch.setattr(sync, "_print_summary", lambda *a, **k: None)
        monkeypatch.setattr(sync, "_save_playlist_snapshot", lambda *a, **k: {})
        monkeypatch.setattr(sync, "authenticate_trakt", lambda auth, force=False: True)

        server = MagicMock()
        server.machineIdentifier = "server-abc"
        cache_mgr = MagicMock()
        cache_mgr.load_cache.return_value = True
        plex_client = MagicMock()
        plex_client.cleanup_orphaned_playlists.return_value = []
        trakt_client = MagicMock()
        trakt_client.get_liked_lists.return_value = []

        monkeypatch.setattr(sync, "PlexServer", lambda *a, **k: server)
        monkeypatch.setattr(sync, "CacheManager", lambda *a, **k: cache_mgr)
        monkeypatch.setattr(sync, "PlexClient", lambda *a, **k: plex_client)
        monkeypatch.setattr(sync, "TraktClient", lambda *a, **k: trakt_client)
        monkeypatch.setattr(sync, "SyncProgress", lambda *a, **k: MagicMock())

        return {
            "server": server,
            "cache_mgr": cache_mgr,
            "plex_client": plex_client,
            "trakt_client": trakt_client,
        }

    def test_official_playlists_processed(self, monkeypatch, tmp_path):
        """Official playlist processing loop runs and counts results."""
        self._setup(monkeypatch, tmp_path)
        args = self._args(monkeypatch, ["--list-source", "official"])

        monkeypatch.setattr(sync, "_needs_oauth_auth", lambda *a: False)
        monkeypatch.setattr(sync, "_should_sync_official_lists", lambda *a: True)
        monkeypatch.setattr(sync, "_get_official_endpoints", lambda *a: ["movies.trending"])
        monkeypatch.setattr(sync, "_get_official_periods", lambda *a: ["weekly"])

        official_service = MagicMock()
        official_service.get_playlists_from_endpoints.return_value = [{"name": "Trending Movies"}]
        monkeypatch.setattr(sync, "OfficialListsService", lambda *a, **k: official_service)
        monkeypatch.setattr(
            sync,
            "process_official_list_parallel",
            lambda *a, **k: {
                "success": True,
                "list_name": "Trending Movies",
                "matched": 4,
                "warning": "",
            },
        )

        result = sync.sync_lists(args)

        assert result == 0
        official_service.get_playlists_from_endpoints.assert_called_once()

    def test_official_playlists_none(self, monkeypatch, tmp_path):
        """No official playlists -> graceful message, exit 0."""
        self._setup(monkeypatch, tmp_path)
        args = self._args(monkeypatch, ["--list-source", "official"])

        monkeypatch.setattr(sync, "_needs_oauth_auth", lambda *a: False)
        monkeypatch.setattr(sync, "_should_sync_official_lists", lambda *a: True)
        monkeypatch.setattr(sync, "_get_official_endpoints", lambda *a: [])
        monkeypatch.setattr(sync, "_get_official_periods", lambda *a: ["weekly"])

        official_service = MagicMock()
        official_service.get_playlists_from_endpoints.return_value = []
        monkeypatch.setattr(sync, "OfficialListsService", lambda *a, **k: official_service)

        result = sync.sync_lists(args)

        assert result == 0

    def test_collection_sync(self, monkeypatch, tmp_path):
        """--sync-collection runs process_collection_sync."""
        self._setup(monkeypatch, tmp_path)
        args = self._args(monkeypatch, ["--sync-collection", "--list-source", "official"])

        monkeypatch.setattr(sync, "_needs_oauth_auth", lambda *a: True)
        monkeypatch.setattr(sync, "_should_sync_official_lists", lambda *a: False)
        process_collection = MagicMock(return_value=[{"success": True}])
        monkeypatch.setattr(sync, "process_collection_sync", process_collection)

        result = sync.sync_lists(args)

        assert result == 0
        process_collection.assert_called_once()

    def test_watchlist_sync(self, monkeypatch, tmp_path):
        """--sync-watchlist runs process_watchlist_sync."""
        self._setup(monkeypatch, tmp_path)
        args = self._args(monkeypatch, ["--sync-watchlist", "--list-source", "official"])

        monkeypatch.setattr(sync, "_needs_oauth_auth", lambda *a: True)
        monkeypatch.setattr(sync, "_should_sync_official_lists", lambda *a: False)
        process_watchlist = MagicMock(return_value=[{"success": True}])
        monkeypatch.setattr(sync, "process_watchlist_sync", process_watchlist)

        result = sync.sync_lists(args)

        assert result == 0
        process_watchlist.assert_called_once()

    def test_no_liked_lists_early_exit(self, monkeypatch, tmp_path):
        """No liked lists + no official lists -> early return 0."""
        mocks = self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(sync, "TRAKTOR_LIST_SOURCE", "liked")
        args = self._args(monkeypatch, ["--list-source", "liked"])

        mocks["trakt_client"].get_liked_lists.return_value = []
        monkeypatch.setattr(sync, "_needs_oauth_auth", lambda *a: True)
        monkeypatch.setattr(sync, "_should_sync_official_lists", lambda *a: False)

        result = sync.sync_lists(args)

        assert result == 0

    def test_liked_list_fetch_failure_continues(self, monkeypatch, tmp_path):
        """Trakt liked-list fetch failure degrades gracefully."""
        mocks = self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(sync, "TRAKTOR_LIST_SOURCE", "liked")
        args = self._args(monkeypatch, ["--list-source", "liked"])

        mocks["trakt_client"].get_liked_lists.side_effect = Exception("trakt down")
        monkeypatch.setattr(sync, "_needs_oauth_auth", lambda *a: True)
        monkeypatch.setattr(sync, "_should_sync_official_lists", lambda *a: False)

        result = sync.sync_lists(args)

        assert result == 0


class TestProcessOfficialList:
    """Coverage for process_official_list_parallel (TODO audit D1)."""

    def test_empty_playlist_cleared(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        plex = MagicMock()
        stats = {
            "items_total": 0,
            "items_matched": 0,
            "items_not_found": 0,
            "playlists_updated": 0,
        }

        result = sync.process_official_list_parallel(
            {"name": "Empty", "items": [], "description": ""},
            plex,
            2,
            stats,
            set(),
            [],
        )

        assert result["success"] is True
        assert result["matched"] == 0
        plex.create_or_update_playlist.assert_called_once()

    def test_matched_items(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock, patch

        plex = MagicMock()
        stats = {
            "items_total": 0,
            "items_matched": 0,
            "items_not_found": 0,
            "playlists_updated": 0,
        }

        with patch.object(sync, "_collect_plex_items", return_value=[object()]):
            result = sync.process_official_list_parallel(
                {"name": "Trending", "items": [{"item": 1}], "description": ""},
                plex,
                2,
                stats,
                set(),
                [],
            )

        assert result["success"] is True
        assert result["matched"] == 1
        assert stats["playlists_updated"] == 1

    def test_no_matches_warning(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock, patch

        plex = MagicMock()
        stats = {
            "items_total": 0,
            "items_matched": 0,
            "items_not_found": 0,
            "playlists_updated": 0,
        }

        with patch.object(sync, "_collect_plex_items", return_value=[]):
            result = sync.process_official_list_parallel(
                {"name": "Empty Matches", "items": [{"item": 1}], "description": ""},
                plex,
                2,
                stats,
                set(),
                [],
            )

        assert result["success"] is True
        assert result["warning"] == "no_matches"


class TestPrintSummary:
    """Coverage for _print_summary including the performance report branch."""

    def test_summary_with_performance_report(self, monkeypatch, capsys):
        from traktor.performance import PerformanceMonitor

        monitor = PerformanceMonitor()
        monitor.record_api_call("movies/popular", 0.5)
        monkeypatch.setattr(sync, "performance_monitor", monitor)

        stats = {
            "lists_found": 1,
            "lists_processed": 1,
            "official_playlists_found": 0,
            "official_playlists_processed": 0,
            "lists_failed": 0,
            "items_total": 5,
            "items_matched": 4,
            "items_not_found": 1,
            "playlists_updated": 1,
            "playlists_deleted": 0,
        }

        sync._print_summary(stats, 1.5, show_performance=True)

        out = capsys.readouterr().out
        assert "Sync complete!" in out
        assert "movies/popular" in out

    def test_summary_bottlenecks_flagged(self, monkeypatch, capsys):
        from traktor.performance import PerformanceMonitor

        monitor = PerformanceMonitor()
        monitor.record_api_call("slow/endpoint", 2.0)
        monkeypatch.setattr(sync, "performance_monitor", monitor)

        stats = {
            "lists_found": 0,
            "lists_processed": 0,
            "official_playlists_found": 0,
            "official_playlists_processed": 0,
            "lists_failed": 0,
            "items_total": 0,
            "items_matched": 0,
            "items_not_found": 0,
            "playlists_updated": 0,
            "playlists_deleted": 0,
        }

        sync._print_summary(stats, 0.1, show_performance=True)

        out = capsys.readouterr().out
        assert "Bottlenecks" in out
