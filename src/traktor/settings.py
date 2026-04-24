"""Application settings and runtime paths."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Try to load .env from multiple possible locations
# 1. Current working directory (for local development)
# 2. /app/.env (for Docker with volume mount)
# 3. Parent of current file (for various runtime contexts)
_env_loaded = False
for env_path in [".env", "/app/.env", Path(__file__).parent.parent.parent / ".env"]:
    path = Path(env_path)
    if path.exists():
        load_dotenv(path)
        _env_loaded = True
        break

if not _env_loaded:
    # Fallback to default behavior (searches CWD)
    load_dotenv()


# Constants for environment variable parsing
def _parse_bool_env(env_var, default="false"):
    """Parse boolean environment variable (case-insensitive)."""
    return os.getenv(env_var, default).lower() == "true"


# Trakt API configuration
TRAKT_CLIENT_ID = os.getenv("TRAKT_CLIENT_ID")
TRAKT_CLIENT_SECRET = os.getenv("TRAKT_CLIENT_SECRET")
TRAKT_ACCESS_TOKEN = os.getenv("TRAKT_ACCESS_TOKEN")
TRAKT_REFRESH_TOKEN = os.getenv("TRAKT_REFRESH_TOKEN")
TRAKT_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
TRAKT_API_URL = "https://api.trakt.tv"

# Runtime mode
DOCKER_MODE = _parse_bool_env("DOCKER_MODE", "false")
DATA_DIR = Path("/data") if DOCKER_MODE else Path.home()

if DOCKER_MODE:
    CONFIG_FILE = DATA_DIR / "config" / ".traktor_config.json"
    TOKEN_FILE = DATA_DIR / "config" / ".traktor_trakt_token.json"
    CACHE_DIR = DATA_DIR / "config" / ".traktor_cache"
    LOG_FILE = DATA_DIR / "logs" / "traktor.log"
else:
    CONFIG_FILE = DATA_DIR / ".traktor_config.json"
    TOKEN_FILE = DATA_DIR / ".traktor_trakt_token.json"
    CACHE_DIR = DATA_DIR / ".traktor_cache"
    LOG_FILE = DATA_DIR / ".traktor" / "traktor.log"


def ensure_dirs():
    """Create runtime directories (cache and log) if they don't exist."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


CACHE_VERSION = "2.0"
CACHE_MAX_AGE_HOURS = 24
MAX_WORKERS = int(os.getenv("TRAKTOR_WORKERS", "8"))

# Watch sync settings
WATCH_SYNC_ENABLED = _parse_bool_env("WATCH_SYNC_ENABLED", "false")
WATCH_SYNC_DIRECTION = os.getenv("WATCH_SYNC_DIRECTION", "both")
WATCH_SYNC_CONFLICT_RESOLUTION = os.getenv("WATCH_SYNC_CONFLICT_RESOLUTION", "newest_wins")

# Official lists settings
TRAKTOR_OFFICIAL_LISTS_ENABLED = _parse_bool_env(
    "TRAKTOR_OFFICIAL_LISTS_ENABLED", "true"
)  # Default: enabled for onboarding
TRAKTOR_OFFICIAL_ENDPOINTS = os.getenv("TRAKTOR_OFFICIAL_ENDPOINTS", "")
TRAKTOR_OFFICIAL_PERIOD = os.getenv("TRAKTOR_OFFICIAL_PERIOD", "weekly")
TRAKTOR_OFFICIAL_PERIODS = os.getenv("TRAKTOR_OFFICIAL_PERIODS", "")

# List source settings
# Options: "liked", "official", or "both"
TRAKTOR_LIST_SOURCE = os.getenv("TRAKTOR_LIST_SOURCE", "official")

# Official lists cache TTLs (in hours)
TRAKTOR_OFFICIAL_CACHE_TTL_TRENDING = int(os.getenv("TRAKTOR_OFFICIAL_CACHE_TTL_TRENDING", "1"))
TRAKTOR_OFFICIAL_CACHE_TTL_POPULAR = int(os.getenv("TRAKTOR_OFFICIAL_CACHE_TTL_POPULAR", "6"))
TRAKTOR_OFFICIAL_CACHE_TTL_PLAYED = int(os.getenv("TRAKTOR_OFFICIAL_CACHE_TTL_PLAYED", "6"))
TRAKTOR_OFFICIAL_CACHE_TTL_WATCHED = int(os.getenv("TRAKTOR_OFFICIAL_CACHE_TTL_WATCHED", "6"))
TRAKTOR_OFFICIAL_CACHE_TTL_COLLECTED = int(os.getenv("TRAKTOR_OFFICIAL_CACHE_TTL_COLLECTED", "24"))
TRAKTOR_OFFICIAL_CACHE_TTL_ANTICIPATED = int(
    os.getenv("TRAKTOR_OFFICIAL_CACHE_TTL_ANTICIPATED", "24")
)
TRAKTOR_OFFICIAL_CACHE_TTL_BOXOFFICE = int(os.getenv("TRAKTOR_OFFICIAL_CACHE_TTL_BOXOFFICE", "12"))
