# traktor

**Sync your Trakt curation to Plex playlists — automatically.**

---

Turn your liked Trakt lists into Plex playlists. Sync your watched status between platforms. Resume movies on Plex exactly where you left off on Trakt. Run it once and forget it — traktor handles the rest with smart caching, parallel processing, and delta sync.

## Install in 30 seconds

**Prerequisites:** Python 3.10+ and [uv](https://docs.astral.sh/uv/getting-started/installation/) (one-line install)

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
| **🎯 Smart fallback** | Shows without S01E01 automatically fall back to the first available episode |
| **📊 Actionable reports** | `missing.txt` includes reasons (not just IDs) so you know why items weren't found |
| **🔍 Smart suggestions** | Missing items include fuzzy title matches and naming mismatch detection |
| **🖥️ Interactive mode** | Preview changes before applying, confirm deletions, inspect recent sync snapshots |
| **🧠 Advanced conflict resolution** | Per-media-type rules, timezone-aware timestamps, and confidence scoring |
| **🔒 Auth failure detection** | Auto-detects stale Trakt tokens and prompts for re-authentication |
| **📝 Structured logging** | Optional JSON logs with correlation IDs for machine parsing |
| **📈 Performance monitoring** | Built-in API timing, cache hit rates, and bottleneck detection |
| **🏥 Health endpoints** | HTTP `/health`, `/metrics`, and `/status` for container monitoring |
| **🔄 Auto-update checks** | Check for new versions via GitHub API with safe rollback capability |
| **⚙️ Resource management** | Configurable memory limits and CPU throttling for multi-tenant operation |

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
- Movies always pull Trakt's **full watched list** — movies watched on other services are
  marked watched in Plex regardless of how long ago they were watched (no delta window loss)
- Playback progress sync (resume points) from Trakt → Plex
- Conflict resolution strategies when states disagree

**Performance & Reliability**
- In-memory cache with 24-hour TTL and hash-based invalidation
- Incremental cache updates — only scan new items since last sync
- Batch API operations (100 items per call)
- Delta sync skips unchanged items after first run
- Smart playlist updates — only add/remove changed items instead of recreating entire playlists
- Parallel workers (default 8, configurable)
- Detailed `missing.txt` report with reasons for missing items

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
| `TRAKTOR_OFFICIAL_LISTS_ENABLED` | `true` | Enable official Trakt lists (trending, popular) |
| `DOCKER_MODE` | `false` | Use `/data/` paths instead of home directory |
| `TRAKTOR_HEALTH_PORT` | `8080` | Port for HTTP health endpoints |
| `TRAKTOR_MAX_MEMORY_MB` | `512` | Maximum memory usage in MB (CLI: `--max-memory-mb`) |
| `WATCH_SYNC_DIRECTION` | `both` | Default watch sync direction (`both`, `plex-to-trakt`, `trakt-to-plex`) |
| `WATCH_SYNC_CONFLICT_RESOLUTION` | `newest_wins` | Default conflict strategy (`newest_wins`, `plex_wins`, `trakt_wins`) |

**CLI flags:**
- `--verbose, -v` — Enable debug logging to console
- `--plex-url URL, -u URL` — Override `PLEX_URL` env var
- `--plex-token TOKEN, -t TOKEN` — Override `PLEX_TOKEN` env var
- `--force-auth` — Force re-authentication with Trakt (skip stored tokens)
- `--list-source={official,liked,both}` — Override `TRAKTOR_LIST_SOURCE` env var
- `--sync-watched`, `--sync-watched-only` — Watch status sync (OAuth required)
- `--watch-direction={both,plex-to-trakt,trakt-to-plex}` — Direction for watch sync (default: both)
- `--watch-conflict={newest,plex,trakt}` — Conflict resolution strategy (default: newest)
- `--sync-progress` — Resume point sync Trakt → Plex (OAuth required)
- `--sync-collection`, `--sync-watchlist` — Personal Trakt data (OAuth required)
- `--sync-movies-only`, `--sync-shows-only` — Filter watch sync by media type
- `--backfill-history` — Full history sync (initial setup)
- `--interactive` — Prompt for confirmation before significant changes
- `--undo` — Show the most recent sync snapshot (inspection only; nothing is restored)
- `--structured-logging` — Output JSON logs with correlation IDs
- `--performance-report` — Print detailed performance metrics after sync
- `--refresh-cache`, `--diagnose`, `--dry-run`, `--workers N` — Utility flags

**Mission-critical commands:**
- `--health-check` — Run health checks and report system status
- `--serve-health` — Start HTTP health server for container monitoring
- `--integrity-check` — Verify integrity of config, tokens, and cache files
- `--backup`, `--backup-list`, `--backup-restore PATH` — Backup management
- `--circuit-status` — Show circuit breaker status for API clients
- `--check-update`, `--apply-update` — Check for and apply updates

**Official list options:**
- `--official-lists`, `--no-official-lists` — Enable/disable official lists
- `--official-endpoints` — Comma-separated endpoints (e.g., `movies.trending,shows.popular`)
- `--official-period={daily,weekly,monthly,yearly}` — Time period for stats
- `--official-periods` — Multiple periods (e.g., `weekly,monthly`)
- `--official-playlist-mode={separate,merged}` — Per-endpoint or aggregated playlists

See `uv run traktor --help` for all options.

**Authentication notes:**
- **No OAuth required for:** Official lists (trending, popular, box office), basic playlist sync
- **OAuth required for:** Liked lists, collection, watchlist, watch status sync, progress sync
- On first run with OAuth-required features, you'll get a one-time browser prompt to authorize
- Set `TRAKTOR_LIST_SOURCE=both` in `.env` to always sync liked lists without adding the CLI flag

**Trakt OAuth details:**
- `TRAKT_REDIRECT_URI` in `.env` must **exactly match** the "Redirect URI" registered for your app
  at https://trakt.tv/oauth/applications (Trakt rejects mismatches with `invalid_redirect`).
- During `uv run traktor --force-auth`, traktor accepts the authorization code two ways at once:
  - **paste it** into the terminal (the bare code or the full callback URL) and press Enter — works
    on remote/headless boxes where the browser redirect can't reach the server, and
  - if the redirect URI is a localhost URL, a local listener captures the browser redirect
    automatically (paste still wins if both happen).
- Tokens are validated on load: placeholder/truncated garbage (e.g. literal `new-access-token`
  text, or anything under 20 characters) is treated as unauthenticated and triggers a clear
  re-auth prompt instead of silently failing later. `save_tokens()` also refuses to write invalid
  tokens. Trakt currently issues 32-character tokens; the floor is kept low so legitimate format
  changes never lock you out.
- Re-authenticate at any time with `uv run traktor --force-auth`.

**Note on Plex tokens:** Use your **server owner's token** for playlists visible to all users. Managed user tokens create private playlists only.

## Architecture

```
Trakt.tv ──OAuth──► TraktAuth/Client ──API──┐
     │                                       │
     └──IMDb/TMDb IDs──┐                    │
                       ▼                    ▼
              ┌──────────────────────────────────────┐
              │             traktor CLI              │
              │  sync engine │ cache │ watch sync    │
              │  official lists │ resilience │ log   │
              └─────────────┬────────────────────────────┘
                            │
                            ▼
                    Plex Media Server
```

Runtime files: `~/.traktor_config.json`, `~/.traktor_cache/`, `~/.traktor/traktor.log` (local mode) or `/data/config/`, `/data/logs/` (Docker mode).

## License

[MIT](LICENSE)
