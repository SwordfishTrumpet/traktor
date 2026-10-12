"""Tests for utils module."""

import traktor.utils as utils
from traktor.utils import normalize_tmdb_id, parse_timestamp, restrict_to_owner


class TestNormalizeTmdbId:
    """Tests for normalize_tmdb_id function."""

    def test_normalize_tmdb_id_with_int(self):
        """Test that integer TMDb IDs are converted to strings."""
        result = normalize_tmdb_id(12345)
        assert result == "12345"
        assert isinstance(result, str)

    def test_normalize_tmdb_id_with_string(self):
        """Test that string TMDb IDs are preserved."""
        result = normalize_tmdb_id("67890")
        assert result == "67890"
        assert isinstance(result, str)

    def test_normalize_tmdb_id_with_none(self):
        """Test that None returns None."""
        result = normalize_tmdb_id(None)
        assert result is None

    def test_normalize_tmdb_id_with_zero(self):
        """Test that zero returns None (falsy check)."""
        result = normalize_tmdb_id(0)
        assert result is None

    def test_normalize_tmdb_id_with_empty_string(self):
        """Test that empty string returns None (falsy check)."""
        result = normalize_tmdb_id("")
        assert result is None

    def test_normalize_tmdb_id_with_negative_int(self):
        """Test that negative integers are converted to strings."""
        result = normalize_tmdb_id(-1)
        assert result == "-1"

    def test_normalize_tmdb_id_with_large_int(self):
        """Test that large integers are handled correctly."""
        result = normalize_tmdb_id(999999999)
        assert result == "999999999"

    def test_normalize_tmdb_id_with_whitespace_string(self):
        """Test that whitespace in strings is preserved (not stripped)."""
        result = normalize_tmdb_id(" 123 ")
        assert result == " 123 "

    def test_normalize_tmdb_id_consistency(self):
        """Test that the same input always produces the same output."""
        assert normalize_tmdb_id(123) == normalize_tmdb_id("123")
        assert normalize_tmdb_id(123) == "123"

    def test_normalize_tmdb_id_with_float(self):
        """Test that float values are converted to strings."""
        result = normalize_tmdb_id(123.45)
        assert result == "123.45"

    def test_normalize_tmdb_id_with_boolean_true(self):
        """Test that True is converted to string 'True'."""
        result = normalize_tmdb_id(True)
        assert result == "True"

    def test_normalize_tmdb_id_with_boolean_false(self):
        """Test that False returns None (falsy check)."""
        result = normalize_tmdb_id(False)
        assert result is None


class TestParseTimestamp:
    """Tests for the canonical parse_timestamp function."""

    def test_none_returns_none(self):
        assert parse_timestamp(None) is None

    def test_aware_datetime_converted_to_utc(self):
        from datetime import datetime, timedelta, timezone

        est = timezone(timedelta(hours=-5))
        dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=est)
        result = parse_timestamp(dt)
        assert result.tzinfo == timezone.utc
        assert result.hour == 17

    def test_naive_datetime_interpreted_as_local(self):
        from datetime import datetime, timezone

        dt = datetime(2024, 6, 15, 12, 0, 0)
        result = parse_timestamp(dt)
        assert result.tzinfo == timezone.utc
        assert result == dt.astimezone(timezone.utc)

    def test_epoch_int(self):
        from datetime import datetime, timezone

        result = parse_timestamp(0)
        assert result == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_epoch_float(self):
        from datetime import datetime, timezone

        result = parse_timestamp(1718467200.0)
        assert result == datetime(2024, 6, 15, 16, 0, 0, tzinfo=timezone.utc)

    def test_iso_string_with_z(self):
        from datetime import datetime, timezone

        result = parse_timestamp("2024-06-15T12:00:00Z")
        assert result == datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_naive_iso_string_as_local(self):
        from datetime import datetime, timezone

        result = parse_timestamp("2024-06-15T12:00:00")
        assert result == datetime(2024, 6, 15, 12, 0, 0).astimezone(timezone.utc)

    def test_numeric_string_epoch(self):
        from datetime import datetime, timezone

        result = parse_timestamp("1718467200")
        assert result == datetime(2024, 6, 15, 16, 0, 0, tzinfo=timezone.utc)

    def test_unparseable_string_returns_none(self):
        assert parse_timestamp("not-a-timestamp") is None

    def test_invalid_type_returns_none(self):
        assert parse_timestamp({"dict": True}) is None

    def test_tz_name_interprets_naive(self):
        from datetime import datetime, timezone

        # 12:00 America/New_York (EDT, UTC-4 in June) -> 16:00 UTC
        result = parse_timestamp(datetime(2024, 6, 15, 12, 0, 0), tz_name="America/New_York")
        assert result.tzinfo == timezone.utc
        assert result.hour == 16

    def test_invalid_tz_name_falls_back_to_local(self):
        from datetime import datetime, timezone

        dt = datetime(2024, 6, 15, 12, 0, 0)
        result = parse_timestamp(dt, tz_name="Invalid/Zone")
        assert result.tzinfo == timezone.utc
        assert result == dt.astimezone(timezone.utc)


class TestRestrictToOwnerPermissions:
    """A credential file must end up readable by its owner only (issue #12)."""

    def test_restrict_to_owner_sets_owner_only_permissions(self, tmp_path):
        target = tmp_path / "secret.json"
        target.write_text("data")
        target.chmod(0o644)

        assert restrict_to_owner(target) is True
        assert (target.stat().st_mode & 0o777) == 0o600

    def test_restrict_to_owner_reports_a_refusal(self, tmp_path, monkeypatch):
        target = tmp_path / "secret.json"
        target.write_text("data")

        def deny_chmod(path, mode):
            raise OSError("not permitted")

        monkeypatch.setattr(utils.os, "chmod", deny_chmod)
        assert restrict_to_owner(target) is False
