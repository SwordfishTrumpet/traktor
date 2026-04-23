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
            }
        ],
        file_path=report_path,
    )

    content = report_path.read_text(encoding="utf-8")

    assert "List | Type | Title | Year | IMDb ID" in content
    assert "Favorites | Movie | Heat | 1995 | tt0113277" in content


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


def test_process_item_parallel_reports_missing_when_show_has_no_season_one():
    show = FakeShow([FakeSeason(2, [FakeEpisode("Later")])])
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
        "success": False,
        "title": "Breaking Bad",
        "year": 2008,
        "idx": 4,
        "type": "Show",
        "imdb_id": "tt0903747",
    }


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
    )

    assert item["list_name"] == "Test List"
    assert item["type"] == "Movie"
    assert item["title"] == "Test Movie"
    assert item["year"] == "2024"
    assert item["imdb_id"] == "tt1234567"


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
    }

    tracker.record_result("My List", result, stats)

    assert stats["items_not_found"] == 1
    assert len(tracker.not_found) == 1
    assert tracker.not_found[0] == "Missing Movie (2024)"
    assert len(tracker.missing_items) == 1
    assert tracker.missing_items[0]["title"] == "Missing Movie"


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
