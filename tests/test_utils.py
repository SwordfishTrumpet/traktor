"""Tests for utils module."""

from traktor.utils import normalize_tmdb_id


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
