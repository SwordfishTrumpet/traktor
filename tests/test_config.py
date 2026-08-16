import argparse
import builtins
import json
import sys
from types import SimpleNamespace

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
