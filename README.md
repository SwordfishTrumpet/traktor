# traktor

**Sync your Trakt curation to Plex playlists — automatically.**

[![Release](https://img.shields.io/github/v/release/SwordfishTrumpet/traktor?logo=github&color=blue)](https://github.com/SwordfishTrumpet/traktor/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/SwordfishTrumpet/traktor/ci.yml?branch=main&logo=github&label=CI)](https://github.com/SwordfishTrumpet/traktor/actions)
[![License](https://img.shields.io/github/license/SwordfishTrumpet/traktor?color=green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8+-blue?logo=python)](https://pepy.tech/project/traktor)

---

Turn your liked Trakt lists into Plex playlists. Sync your watched status between platforms. Resume movies on Plex exactly where you left off on Trakt. Run it once and forget it — traktor handles the rest with smart caching, parallel processing, and delta sync.

## Install in 30 seconds

**Prerequisites:** Python 3.8+ and [uv](https://docs.astral.sh/uv/getting-started/installation/) (one-line install)

```bash
git clone https://github.com/SwordfishTrumpet/traktor.git && cd traktor
uv sync
cp .env.example .env  # Then edit .env with your credentials
```

**Get your credentials:**
- [Trakt API app](https://trakt.tv/oauth/applications) (create one, copy Client ID/Secret)
- [Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) (from Plex Web settings)

## Run it

```bash
uv run traktor
```

By default, traktor syncs Trakt's official curated lists (trending, popular movies/shows) to Plex — **no OAuth required**, just your Trakt Client ID.

To sync your personal liked lists from Trakt, either set `TRAKTOR_LIST_SOURCE=both` in your `.env` file, or run with `--list-source=both` and complete the one-time OAuth prompt.

### Common commands

```bash
uv run traktor                       # Sync based on TRAKTOR_LIST_SOURCE in .env (default: official)
uv run traktor --list-source=both    # Sync official + your liked lists (OAuth required)
uv run traktor --sync-watched        # Bidirectional watch status sync (OAuth required)
uv run traktor --sync-watched-only   # Sync only watch status (fast cron job)
uv run traktor --diagnose            # Check your setup
uv run traktor --refresh-cache       # After adding new media to Plex
```

See all options: `uv run traktor --help`

## Why traktor?

| Feature | What it means for you |
|---------|----------------------|
| **⚡ Fast** | Parallel processing + delta sync means subsequent runs complete in seconds, not minutes |
| **🔁 Bidirectional sync** | Watch a movie on Plex → marked watched on Trakt. Watch on Trakt → marked watched on Plex |
| **📺 Episode-aware** | Shows in Trakt lists resolve to S01E01 episodes so playlists contain playable items |
| **🧠 Smart matching** | Matches by IMDb/TMDb IDs, not fuzzy titles — accurate even with renaming |
| **📦 Official lists** | Sync Trakt's algorithmic lists (trending, popular, box office) without extra OAuth |
| **🐳 Docker ready** | One `docker compose up` and you're scheduled |
| **🧹 Self-cleaning** | Unlike a list? Un-like it on Trakt — the playlist disappears automatically |

## Everything it does

**Playlist Sync (No OAuth Required)**
- Trakt Official Lists → Trending, popular, most played, box office, anticipated movies/shows
  - Works with just `TRAKT_CLIENT_ID` — no authentication needed
  - Great for new users to get curated playlists immediately

**Playlist Sync (OAuth Required)**
- Liked Trakt lists → Plex playlists (your personal curation from Trakt users)
- Trakt Collection → "Trakt Collection - Movies/Shows" playlists  
- Trakt Watchlist → "Trakt Watchlist" playlist

**Watch Status & Progress (OAuth Required)**
- Bidirectional watched/unwatched sync (movies & episodes)
- Playback progress sync (resume points) from Trakt → Plex
- Conflict resolution strategies when states disagree

**Performance & Reliability**
- In-memory cache with 24-hour TTL and hash-based invalidation
- Batch API operations (100 items per call)
- Delta sync skips unchanged items after first run
- Parallel workers (default 8, configurable)
- Detailed `missing.txt` report for items not found in Plex

## Documentation

- **[QUICKSTART.md](QUICKSTART.md)** — 3-step install, common commands, file locations
- **[DOCKER.md](DOCKER.md)** — Docker setup, scheduling with cron, troubleshooting
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Dev setup, running tests, PR guidelines

## Docker (alternative to uv)

```bash
cp .env.example .env
docker compose build
docker compose run --rm traktor
```

See [DOCKER.md](DOCKER.md) for scheduling and volume mounts.

## Configuration

**Environment variables** (in `.env`):

| Variable | Required | Description |
|----------|----------|-------------|
| `TRAKT_CLIENT_ID` | Yes | From your Trakt API app |
| `TRAKT_CLIENT_SECRET` | Yes | From your Trakt API app |
| `PLEX_URL` | Yes | `http://your-plex-host:32400` |
| `PLEX_TOKEN` | Yes | See [Plex guide](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) |

| Variable | Default | Description |
|----------|---------|-------------|
| `TRAKTOR_LIST_SOURCE` | `official` | Which lists to sync: `official` (public lists only), `liked` (your liked lists), or `both` |
| `TRAKTOR_WORKERS` | `8` | Parallel workers for processing |
| `WATCH_SYNC_ENABLED` | `false` | Enable bidirectional watch status sync |
| `TRAKTOR_OFFICIAL_LISTS_ENABLED` | `true` | Enable official Trakt lists (trending, popular) |
| `DOCKER_MODE` | `false` | Use `/data/` paths instead of home directory |

**CLI flags:**
- `--list-source={official,liked,both}` — Override `TRAKTOR_LIST_SOURCE` env var
- `--sync-watched`, `--sync-watched-only` — Watch status sync (OAuth required)
- `--sync-progress` — Resume point sync Trakt → Plex (OAuth required)
- `--sync-collection`, `--sync-watchlist` — Personal Trakt data (OAuth required)
- `--refresh-cache`, `--diagnose`, `--dry-run`, `--workers N` — Utility flags

See `uv run traktor --help` for all options.

**Authentication notes:**
- **No OAuth required for:** Official lists (trending, popular, box office), basic playlist sync
- **OAuth required for:** Liked lists, collection, watchlist, watch status sync, progress sync
- On first run with OAuth-required features, you'll get a one-time browser prompt to authorize
- Set `TRAKTOR_LIST_SOURCE=both` in `.env` to always sync liked lists without adding the CLI flag

**Note on Plex tokens:** Use your **server owner's token** for playlists visible to all users. Managed user tokens create private playlists only.

## Architecture

```
Trakt.tv ──OAuth──► TraktAuth/Client ──API──┐
     │                                       │
     └──IMDb/TMDb IDs──┐                    │
                       ▼                    ▼
              ┌──────────────────────────────────┐
              │           traktor CLI            │
              │  sync engine │ cache │ watch sync │
              └─────────────┬──────────────────────┘
                            │
                            ▼
                    Plex Media Server
```

Runtime files: `~/.traktor_config.json`, `~/.traktor_cache/`, `~/.traktor/traktor.log` (local mode) or `/data/config/`, `/data/logs/` (Docker mode).

## License

[MIT](LICENSE)
