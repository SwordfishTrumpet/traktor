import argparse
import builtins
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from traktor import config


def test_get_plex_credentials_prefers_environment(monkeypatch):
    monkeypatch.setenv("PLEX_URL", "http://env-plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "env-token")

    args = SimpleNamespace(plex_url="http://cli-plex:32400", plex_token="cli-token")

    url, token = config.get_plex_credentials(args)

    assert url == "http://env-plex:32400"
    assert token == "env-token"


def test_get_plex_credentials_uses_cli_when_env_missing(monkeypatch):
    monkeypatch.delenv("PLEX_URL", raising=False)
    monkeypatch.delenv("PLEX_TOKEN", raising=False)

    args = SimpleNamespace(plex_url="http://cli-plex:32400", plex_token="cli-token")

    url, token = config.get_plex_credentials(args)

    assert url == "http://cli-plex:32400"
    assert token == "cli-token"


def test_get_plex_credentials_uses_saved_config_when_accepted(monkeypatch):
    monkeypatch.delenv("PLEX_URL", raising=False)
    monkeypatch.delenv("PLEX_TOKEN", raising=False)
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: {"plex_url": "http://saved-plex:32400", "plex_token": "saved-token"},
    )
    monkeypatch.setattr("builtins.input", lambda _: "")

    url, token = config.get_plex_credentials(SimpleNamespace(plex_url=None, plex_token=None))

    assert url == "http://saved-plex:32400"
    assert token == "saved-token"


def test_get_plex_credentials_raises_on_partial_url_only(monkeypatch):
    """Test that ValueError is raised when only PLEX_URL is set."""
    monkeypatch.setenv("PLEX_URL", "http://partial-plex:32400")
    monkeypatch.delenv("PLEX_TOKEN", raising=False)

    args = SimpleNamespace(plex_url=None, plex_token=None)

    try:
        config.get_plex_credentials(args)
        assert False, "Expected ValueError to be raised"
    except ValueError as e:
        assert "PLEX_URL" in str(e)
        assert "PLEX_TOKEN" in str(e)


def test_get_plex_credentials_raises_on_partial_token_only(monkeypatch):
    """Test that ValueError is raised when only PLEX_TOKEN is set."""
    monkeypatch.delenv("PLEX_URL", raising=False)
    monkeypatch.setenv("PLEX_TOKEN", "partial-token")

    args = SimpleNamespace(plex_url=None, plex_token=None)

    try:
        config.get_plex_credentials(args)
        assert False, "Expected ValueError to be raised"
    except ValueError as e:
        assert "PLEX_URL" in str(e)
        assert "PLEX_TOKEN" in str(e)


class TestConfigCredentialsEdgeCases:
    """Edge-case coverage for config.py (TODO audit D10)."""

    def test_invalid_env_plex_url_raises(self, monkeypatch):
        """Invalid PLEX_URL in environment raises ValueError."""
        monkeypatch.setenv("PLEX_URL", "not-a-url")
        monkeypatch.setenv("PLEX_TOKEN", "sometoken")

        with pytest.raises(ValueError) as exc_info:
            config.get_plex_credentials()
        assert "Invalid PLEX_URL" in str(exc_info.value)

    def test_invalid_cli_plex_url_raises(self, monkeypatch):
        """Invalid --plex-url from CLI raises ValueError."""
        monkeypatch.delenv("PLEX_URL", raising=False)
        monkeypatch.delenv("PLEX_TOKEN", raising=False)
        args = argparse.Namespace(plex_url="ftp://bad", plex_token="token123")

        with pytest.raises(ValueError) as exc_info:
            config.get_plex_credentials(args)
        assert "Invalid --plex-url" in str(exc_info.value)

    def test_saved_config_non_interactive(self, monkeypatch, tmp_path, capsys):
        """Saved config is used directly in non-interactive mode."""
        import traktor.config as config_module

        monkeypatch.delenv("PLEX_URL", raising=False)
        monkeypatch.delenv("PLEX_TOKEN", raising=False)
        state_file = tmp_path / "config.json"
        state_file.write_text(json.dumps({"plex_url": "http://plex:32400", "plex_token": "tok"}))
        monkeypatch.setattr(config_module, "CONFIG_FILE", state_file)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        url, token = config_module.get_plex_credentials()

        assert url == "http://plex:32400"
        assert token == "tok"

    def test_saved_config_declined_prompts(self, monkeypatch, tmp_path):
        """Declining saved config in interactive mode prompts for new credentials."""
        import traktor.config as config_module

        monkeypatch.delenv("PLEX_URL", raising=False)
        monkeypatch.delenv("PLEX_TOKEN", raising=False)
        state_file = tmp_path / "config.json"
        state_file.write_text(json.dumps({"plex_url": "http://plex:32400", "plex_token": "tok"}))
        monkeypatch.setattr(config_module, "CONFIG_FILE", state_file)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        # input: decline saved config, then provide URL and token
        inputs = iter(["n", "http://plex.local:32400", "validtoken123"])
        monkeypatch.setattr(builtins, "input", lambda *a, **k: next(inputs))

        url, token = config_module.get_plex_credentials()

        assert url == "http://plex.local:32400"
        assert token == "validtoken123"

    def test_no_credentials_non_interactive_raises(self, monkeypatch, tmp_path):
        """No credentials at all in non-interactive mode raises."""
        import traktor.config as config_module

        monkeypatch.delenv("PLEX_URL", raising=False)
        monkeypatch.delenv("PLEX_TOKEN", raising=False)
        empty = tmp_path / "empty.json"
        empty.write_text("{}")
        monkeypatch.setattr(config_module, "CONFIG_FILE", empty)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        with pytest.raises(ValueError) as exc_info:
            config_module.get_plex_credentials()
        assert "Plex credentials not configured" in str(exc_info.value)

    def test_prompt_for_url_empty_then_valid(self, monkeypatch, capsys):
        """URL prompt rejects empty input then accepts a valid URL."""
        import traktor.config as config_module

        monkeypatch.setattr(builtins, "input", lambda *a, **k: "http://plex.local:32400")
        result = config_module._prompt_for_url()
        assert result == "http://plex.local:32400"

    def test_prompt_for_token_short_confirm(self, monkeypatch, capsys):
        """Token prompt asks for confirmation on short tokens."""
        import traktor.config as config_module

        monkeypatch.setattr(builtins, "input", lambda *a, **k: "short-token")
        result = config_module._prompt_for_token()
        assert result == "short-token"

    def test_load_config_missing_file(self, monkeypatch, tmp_path):
        """Missing config file returns defaults."""
        import traktor.config as config_module

        monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "missing.json")
        assert config_module.load_config() == {}

    def test_load_config_corrupt_json(self, monkeypatch, tmp_path):
        """Corrupt config JSON returns defaults."""
        import traktor.config as config_module

        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        monkeypatch.setattr(config_module, "CONFIG_FILE", bad)
        assert config_module.load_config() == {}

    def test_save_config_permission_error(self, monkeypatch, tmp_path):
        """Save config write failure is logged, not raised."""
        import builtins as _builtins

        import traktor.config as config_module

        target = tmp_path / "config.json"
        monkeypatch.setattr(config_module, "CONFIG_FILE", target)

        real_open = _builtins.open

        def deny_write(*args, **kwargs):
            if args and "w" in (args[1] if len(args) > 1 else kwargs.get("mode", "")):
                raise PermissionError("denied")
            return real_open(*args, **kwargs)

        monkeypatch.setattr(_builtins, "open", deny_write)
        config_module.save_config({"key": "value"})  # should not raise


class TestSaveConfigPermissions:
    """The config file can hold the Plex credential (issue #12)."""

    def test_save_config_restricts_permissions(self, monkeypatch, tmp_path):
        import traktor.config as config_module

        target = tmp_path / "config.json"
        monkeypatch.setattr(config_module, "CONFIG_FILE", target)

        config_module.save_config({"plex_url": "http://plex:32400", "plex_token": "fixture-value"})

        assert (target.stat().st_mode & 0o777) == 0o600

    def test_save_config_tightens_an_existing_wide_open_file(self, monkeypatch, tmp_path):
        import traktor.config as config_module

        target = tmp_path / "config.json"
        target.write_text("{}")
        target.chmod(0o644)
        monkeypatch.setattr(config_module, "CONFIG_FILE", target)

        config_module.save_config({"managed_playlists": ["x"]})

        assert (target.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# Issue #47: a config that is valid JSON but not an object
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["[]", "null", "42", '"text"', "true"])
def test_load_config_rejects_non_object_json(raw, tmp_path, monkeypatch):
    """A non-object config is corrupt, not data, so defaults are used (#47)."""
    target = tmp_path / "config.json"
    target.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", target)

    assert config.load_config() == {}


def test_load_config_still_returns_an_object(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    target.write_text('{"managed_playlists": ["Café"]}', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", target)

    assert config.load_config() == {"managed_playlists": ["Café"]}


# ---------------------------------------------------------------------------
# Issue #49: config reads use UTF-8 whatever the ambient locale is
# ---------------------------------------------------------------------------


def test_load_config_reads_utf8_under_a_c_locale(tmp_path):
    """A non-UTF locale must not make a valid UTF-8 config unreadable (#49)."""
    import os
    import subprocess
    from pathlib import Path

    target = tmp_path / "config.json"
    payload = {"managed_playlists": ["Café", "日本語"]}
    target.write_text(json.dumps(payload), encoding="utf-8")

    src_root = Path(__file__).resolve().parents[1] / "src"
    env = {
        **os.environ,
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONPATH": str(src_root),
    }
    code = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from traktor import config\n"
        "config.CONFIG_FILE = Path(sys.argv[1])\n"
        "print(json.dumps(config.load_config()))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(target)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["managed_playlists"] == ["Café", "日本語"]


# ---------------------------------------------------------------------------
# Issue #48: a lone --plex-url or --plex-token is a mistake
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        SimpleNamespace(plex_url="http://new-server:32400", plex_token=None),
        SimpleNamespace(plex_url=None, plex_token="new-token"),
    ],
)
def test_partial_cli_plex_credentials_are_rejected(args, monkeypatch, tmp_path):
    """One flag alone must not silently fall back to the saved server (#48)."""
    monkeypatch.delenv("PLEX_URL", raising=False)
    monkeypatch.delenv("PLEX_TOKEN", raising=False)
    saved = tmp_path / "config.json"
    saved.write_text(
        json.dumps({"plex_url": "http://old-server:32400", "plex_token": "old-token"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_FILE", saved)

    with pytest.raises(ValueError, match="must be given together"):
        config.get_plex_credentials(args)


# ---------------------------------------------------------------------------
# Issue #23: an interrupted config save keeps the previous file
# ---------------------------------------------------------------------------


def test_failed_config_save_keeps_the_previous_file(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", target)
    config.save_config({"plex_url": "http://kept:32400"})
    before = target.read_text(encoding="utf-8")

    with patch("traktor.utils.os.fsync", side_effect=OSError(28, "No space left on device")):
        config.save_config({"plex_url": "http://lost:32400"})

    assert target.read_text(encoding="utf-8") == before
    assert "kept" in before
