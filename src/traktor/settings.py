"""Application settings and runtime paths."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Try to load .env from multiple possible locations
# 1. Current working directory (for local development)
# 2. /app/.env (for Docker with volume mount)
# 3. Parent of current file (for various runtime contexts)
_env_loaded = False
_env_file_loaded: Path | None = None
for env_path in [".env", "/app/.env", str(Path(__file__).parent.parent.parent / ".env")]:
    path = Path(env_path)
    if path.exists():
        load_dotenv(path)
        _env_loaded = True
        _env_file_loaded = path.resolve()
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
TRAKT_REDIRECT_URI = os.getenv("TRAKT_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob")
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

SYNC_PROGRESS_FILE = CACHE_DIR / "sync_progress.json"

# Backups: the directory snapshots are written to and how many are kept. Both are
# documented in RUNBOOK.md, so both must be read here; they used to be hardcoded
# and the documented names did nothing (issue #89).
_backup_dir_env = os.getenv("TRAKTOR_BACKUP_DIR", "").strip()
TRAKTOR_BACKUP_DIR = Path(_backup_dir_env).expanduser() if _backup_dir_env else None
TRAKTOR_MAX_BACKUPS = int(os.getenv("TRAKTOR_MAX_BACKUPS", "10"))


def _parse_positive_env(name, default, cast):
    """Read a positive number from the environment, or fall back.

    RUNBOOK.md tells an operator to export the circuit breaker thresholds and
    cooldowns, so these names must be read here. A value that is not a
    positive number keeps the default instead of stopping the run
    (issue #115).
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Circuit breaker tuning, documented in RUNBOOK.md under "Circuit breakers".
TRAKTOR_TRAKT_FAILURE_THRESHOLD = _parse_positive_env(
    "TRAKTOR_TRAKT_FAILURE_THRESHOLD", 5, int
)
TRAKTOR_TRAKT_COOLDOWN_SECONDS = _parse_positive_env(
    "TRAKTOR_TRAKT_COOLDOWN_SECONDS", 60.0, float
)
TRAKTOR_PLEX_FAILURE_THRESHOLD = _parse_positive_env(
    "TRAKTOR_PLEX_FAILURE_THRESHOLD", 3, int
)
TRAKTOR_PLEX_COOLDOWN_SECONDS = _parse_positive_env(
    "TRAKTOR_PLEX_COOLDOWN_SECONDS", 30.0, float
)

# Active .env file location - the canonical Trakt token store since the .env
# persistence change (legacy TOKEN_FILE below is no longer written by current
# code). This is the file this process actually loaded: the Docker mount wins,
# then the file found by the search above (which includes the checkout root, so
# a run from a subdirectory loads the same tokens it later has to clear).
ENV_FILE = (
    Path("/app/.env")
    if Path("/app/.env").exists()
    else (_env_file_loaded or Path(".env").resolve())
)


def ensure_dirs():
    """Create runtime directories (cache and log) if they don't exist."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


CACHE_VERSION = "2.0"
CACHE_MAX_AGE_HOURS = 24
MAX_WORKERS = int(os.getenv("TRAKTOR_WORKERS", "8"))

# Watch sync settings
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

# Health server settings
HEALTH_SERVER_PORT = int(os.getenv("TRAKTOR_HEALTH_PORT", "8080"))

# Watch-history retention: synced-item entries older than this many days are pruned
# during watch-sync apply (issue #7 follow-up). 0 disables pruning.
HISTORY_RETENTION_DAYS = int(os.getenv("TRAKTOR_HISTORY_RETENTION_DAYS", "180"))

# Resource management settings
TRAKTOR_MAX_MEMORY_MB = int(os.getenv("TRAKTOR_MAX_MEMORY_MB", "512"))

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
