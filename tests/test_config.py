from types import SimpleNamespace

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
