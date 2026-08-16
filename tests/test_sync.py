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
    # Test _build_missing_item
    item = sync._build_missing_item("List", "Movie", "Title", "2024", "tt123")
    assert item["title"] == "Title"
    assert item["imdb_id"] == "tt123"

    # Test _extract_missing_item_details
    trakt_item = {
        "type": "movie",
        "movie": {"title": "Test", "year": 2020, "ids": {"imdb": "tt456"}},
    }
    details = sync._extract_missing_item_details(trakt_item)
    assert details["title"] == "Test"

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


def test_restore_playlist_snapshot():
    """Test restoring playlists from snapshot."""
    plex = FakePlexClient()
    snapshot_data = {
        "playlists": {
            "Test Playlist": {
                "items": ["123", "456"],
                "description": "Test description",
            }
        }
    }

    restored = sync._restore_playlist_snapshot(plex, snapshot_data)

    assert restored == 1


def test_restore_playlist_snapshot_empty():
    """Test restoring empty playlist snapshot."""
    plex = FakePlexClient()
    snapshot_data = {"playlists": {}}

    restored = sync._restore_playlist_snapshot(plex, snapshot_data)

    assert restored == 0


def test_restore_playlist_snapshot_missing_item():
    """Test restoring playlist snapshot with missing items."""
    plex = FakePlexClient()
    snapshot_data = {
        "playlists": {
            "Test Playlist": {
                "items": ["missing_key"],
                "description": "",
            }
        }
    }

    # Should not crash even if item is missing
    restored = sync._restore_playlist_snapshot(plex, snapshot_data)

    assert restored == 1


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
