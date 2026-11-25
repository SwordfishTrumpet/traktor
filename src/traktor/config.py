"""Configuration and credential helpers."""

import json
import os
import sys

from .log import logger
from .settings import CONFIG_FILE
from .utils import restrict_to_owner, write_json_atomic

# Config keys this build interprets. ``managed_playlists`` names the Plex
# playlists traktor owns and may delete, so a value of the wrong type is a
# corrupt file rather than a preference (issue #129).
CONFIG_LIST_OF_STRINGS_KEYS = ("managed_playlists", "blacklisted_lists")
CONFIG_STRING_KEYS = ("plex_url", "plex_token")


def _is_valid_plex_url(url):
    """Check if URL is a valid Plex server URL.

    Args:
        url: URL string to validate

    Returns:
        True if URL starts with http:// or https://
    """
    if not url:
        return False
    return url.startswith(("http://", "https://"))


def _config_value_is_valid(key, value):
    """Return True when ``value`` has the shape this key requires.

    Only the keys this build interprets are checked; a config written by a
    newer version keeps its unknown keys.

    Args:
        key: Config key
        value: Value read from the file

    Returns:
        True when the value can be used as-is
    """
    if key in CONFIG_LIST_OF_STRINGS_KEYS:
        return isinstance(value, list) and all(isinstance(entry, str) for entry in value)
    if key in CONFIG_STRING_KEYS:
        return isinstance(value, str)
    return True


def _config_is_valid(config):
    """Return True when every known key holds the value type it needs.

    The top-level shape was already checked (issue #47); nested containers were
    not, and ``managed_playlists`` is a delete list: a mapping there made the
    orphan cleanup delete Plex playlists named by its keys (issue #129).

    Args:
        config: Parsed configuration object

    Returns:
        True when the configuration is usable
    """
    return all(_config_value_is_valid(key, value) for key, value in config.items())


def load_config():
    """Load configuration from file."""
    logger.debug(f"Loading config from: {CONFIG_FILE}")

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                # json.load accepts [], null, 42, "text" and true; callers expect
                # a mapping, so a non-object config is corrupt, not data (#47).
                logger.error(
                    f"Config file holds a JSON {type(config).__name__}, expected an object; "
                    f"using defaults"
                )
                return {}
            if not _config_is_valid(config):
                logger.error(
                    "Config file has a value of the wrong type (managed_playlists and "
                    "blacklisted_lists must be lists of strings, plex_url and plex_token "
                    "strings); using defaults"
                )
                return {}
            logger.info("Configuration loaded successfully")
            logger.debug(f"Config keys: {list(config.keys())}")
            return config
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse config JSON: {e}", exc_info=True)
            return {}
        except UnicodeDecodeError as e:
            # A config saved by an editor in another encoding, or restored from a
            # torn backup, is corrupt for this build, not fatal: every command
            # used to abort at startup until the file was deleted (issue #112).
            logger.error(f"Config file is not valid UTF-8, using defaults: {e}")
            return {}
        except (PermissionError, FileNotFoundError, OSError) as e:
            logger.error(f"Failed to read config file: {e}", exc_info=True)
            return {}

    logger.info("No config file found, using defaults")
    return {}


def save_config(config):
    """Save configuration to file.

    The config file can hold the Plex credential, so it is restricted to the
    owner (issue #12).

    Args:
        config: Configuration dict to persist
    """
    logger.info(f"Saving config to: {CONFIG_FILE}")
    # Written through a temporary file so an interrupted save cannot empty the
    # file holding the Plex credential (issue #23).
    if not write_json_atomic(CONFIG_FILE, config, mode=0o600):
        logger.error(f"Failed to write config file: {CONFIG_FILE}")
        return
    if not restrict_to_owner(CONFIG_FILE):
        logger.warning(
            f"Could not restrict permissions on {CONFIG_FILE}; it can hold the Plex credential"
        )
    logger.info("Configuration saved successfully")
    logger.info(f"Saved config keys: {list(config.keys())}")


def _configured_plex_credentials(args=None):
    """Resolve Plex credentials without prompting.

    The order matches what a sync has always done: environment (or .env), then
    the ``--plex-url``/``--plex-token`` flags, then the credentials saved by a
    previous interactive run. A partial or invalid pair raises, exactly as
    get_plex_credentials() does.

    This is the one resolver for both doors: diagnose used to read only the
    environment, so an install whose credentials were saved by the first run
    was told its credentials were missing and the Plex checks were skipped
    (issue #62).

    Args:
        args: Parsed CLI arguments, if any

    Returns:
        Tuple of (plex_url, plex_token, source), where source is
        ``"environment"``, ``"cli"``, ``"config"`` or None
    """
    plex_url = os.getenv("PLEX_URL")
    plex_token = os.getenv("PLEX_TOKEN")

    # Check for partial credentials - both must be provided
    if (plex_url and not plex_token) or (plex_token and not plex_url):
        logger.error(
            "Partial Plex credentials found in environment: both PLEX_URL and PLEX_TOKEN must be set"
        )
        raise ValueError(
            "Incomplete Plex credentials: both PLEX_URL and PLEX_TOKEN environment variables must be set together. "
            "Please set both variables or neither (to use saved config or interactive prompt)."
        )

    if plex_url and plex_token:
        # Validate environment credentials
        if not _is_valid_plex_url(plex_url):
            raise ValueError(
                f"Invalid PLEX_URL format: {plex_url}. URL must start with http:// or https://"
            )
        logger.info("Using Plex credentials from environment/.env file")
        logger.debug(f"URL length: {len(plex_url)}")
        logger.debug(f"Token length: {len(plex_token)}")
        return plex_url, plex_token, "environment"

    cli_url = getattr(args, "plex_url", None) if args else None
    cli_token = getattr(args, "plex_token", None) if args else None
    if cli_url and cli_token:
        # Validate CLI credentials
        if not _is_valid_plex_url(cli_url):
            raise ValueError(
                f"Invalid --plex-url format: {cli_url}. URL must start with http:// or https://"
            )
        logger.info("Using Plex credentials from command line arguments")
        return cli_url, cli_token, "cli"

    # A lone --plex-url or --plex-token is a mistake, not a partial override:
    # falling through used to reach the saved server while the user believed
    # the flag had been applied (issue #48).
    if (cli_url and not cli_token) or (cli_token and not cli_url):
        missing_flag = "--plex-token" if cli_url else "--plex-url"
        logger.error(f"Partial Plex credentials on the command line: {missing_flag} is missing")
        raise ValueError(
            f"Incomplete Plex credentials: --plex-url and --plex-token must be given together. "
            f"Please pass both flags or neither (to use saved config or the interactive prompt). "
            f"Missing: {missing_flag}"
        )

    logger.debug("Checking saved config...")
    config = load_config()
    if "plex_url" in config and "plex_token" in config:
        logger.info("Found saved Plex configuration")
        return config["plex_url"], config["plex_token"], "config"

    return None, None, None


def resolve_plex_credentials(args=None):
    """Return the Plex credentials the sync would use, without prompting (issue #62).

    Args:
        args: Parsed CLI arguments, if any

    Returns:
        Tuple of (plex_url, plex_token); (None, None) when none are configured

    Raises:
        ValueError: When only half of a pair is configured, or a URL is invalid
    """
    plex_url, plex_token, _source = _configured_plex_credentials(args)
    return plex_url, plex_token


def get_plex_credentials(args=None):
    """Get Plex credentials from environment, config, or user input."""
    logger.debug("Getting Plex credentials...")

    plex_url, plex_token, source = _configured_plex_credentials(args)

    if source in ("environment", "cli"):
        return plex_url, plex_token

    if source == "config":
        # Check if running in non-interactive mode
        if not sys.stdin.isatty():
            logger.info("Non-interactive mode detected, using saved credentials")
            return plex_url, plex_token
        use_saved = input(f"Use saved Plex server at {plex_url}? [Y/n]: ").lower()
        if use_saved != "n":
            return plex_url, plex_token

    # Check if running in non-interactive mode before prompting
    if not sys.stdin.isatty():
        logger.error("Cannot prompt for Plex credentials in non-interactive mode")
        raise ValueError(
            "Plex credentials not configured. Running in non-interactive mode. "
            "Please set PLEX_URL and PLEX_TOKEN environment variables, "
            "or run interactively first to save credentials."
        )

    logger.info("Prompting user for Plex credentials...")
    print("\nPlex Server Setup")
    print("=================")

    plex_url = _prompt_for_url()
    plex_token = _prompt_for_token()

    config = load_config()
    config["plex_url"] = plex_url
    config["plex_token"] = plex_token
    save_config(config)

    return plex_url, plex_token


def _prompt_for_url():
    """Prompt user for a valid Plex URL."""
    while True:
        print("Enter your Plex server URL (e.g., http://192.168.1.100:32400)")
        plex_url = input("Plex URL: ").strip()

        if not plex_url:
            print("Error: URL cannot be empty. Please try again.")
            continue

        if not plex_url.startswith(("http://", "https://")):
            print("Error: URL must start with http:// or https://. Please try again.")
            continue

        logger.debug(f"User entered URL length: {len(plex_url)}")
        return plex_url


def _prompt_for_token():
    """Prompt user for a valid Plex token."""
    while True:
        print("\nEnter your Plex token.")
        print("You can find this at: https://plex.tv/claim or in your Plex settings")
        plex_token = input("Plex Token: ").strip()

        if not plex_token:
            print("Error: Token cannot be empty. Please try again.")
            continue

        if len(plex_token) < 10:
            print("Warning: Token seems very short. Please verify it's correct.")
            confirm = input("Continue anyway? [y/N]: ").lower()
            if confirm != "y":
                continue

        logger.debug(f"User entered token length: {len(plex_token)}")
        return plex_token
