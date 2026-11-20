"""Configuration and credential helpers."""

import json
import os
import sys

from .log import logger
from .settings import CONFIG_FILE
from .utils import restrict_to_owner, write_json_atomic


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
            logger.info("Configuration loaded successfully")
            logger.debug(f"Config keys: {list(config.keys())}")
            return config
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse config JSON: {e}", exc_info=True)
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


def get_plex_credentials(args=None):
    """Get Plex credentials from environment, config, or user input."""
    logger.debug("Getting Plex credentials...")

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
        return plex_url, plex_token

    if args and args.plex_url and args.plex_token:
        # Validate CLI credentials
        if not _is_valid_plex_url(args.plex_url):
            raise ValueError(
                f"Invalid --plex-url format: {args.plex_url}. URL must start with http:// or https://"
            )
        logger.info("Using Plex credentials from command line arguments")
        return args.plex_url, args.plex_token

    # A lone --plex-url or --plex-token is a mistake, not a partial override:
    # falling through used to reach the saved server while the user believed
    # the flag had been applied (issue #48).
    cli_url = getattr(args, "plex_url", None) if args else None
    cli_token = getattr(args, "plex_token", None) if args else None
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
        # Check if running in non-interactive mode
        if not sys.stdin.isatty():
            logger.info("Non-interactive mode detected, using saved credentials")
            return config["plex_url"], config["plex_token"]
        use_saved = input(f"Use saved Plex server at {config['plex_url']}? [Y/n]: ").lower()
        if use_saved != "n":
            return config["plex_url"], config["plex_token"]

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
