# traktor

**Turn Trakt lists into Plex playlists, and keep watch status in step on both sides.**

traktor is for people who keep their watchlist on Trakt but watch in Plex. It copies the lists you
follow into Plex playlists, and keeps watched state and resume points in agreement between the two
services. Run it from cron and it keeps itself up to date.

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

## Check the install

```bash
uv run traktor --health-check
```

It answers with the state of its own files, which is what a broken install gets wrong first:

```text
Running health checks...

Overall Status: ✅ HEALTHY
Timestamp: 2026-11-06T14:01:00.445126+00:00

Component Status:
  ✅ cache: healthy
  ✅ config: healthy
```

The timestamp is the moment you ran it. A line that is not healthy names the file traktor could
not read.

## Run it

```bash
uv run traktor
```

Your first sync logs its progress, prints a summary of what it matched and what it could not find,
and ends like this:

```text
============================================================
Sync complete!
Total time: ...
============================================================

Summary:
  ...
```

The playlists are on your Plex server under Playlists. Anything traktor could not match is written
to `~/.traktor/missing.txt` (or `/data/logs/missing.txt` in Docker) with the reason, so a short
playlist has an explanation rather than a mystery.

By default, traktor syncs Trakt's official curated lists (trending, popular movies/shows) to Plex with **no OAuth required**: just your Trakt Client ID.

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

## The ideas you need

### Official lists and liked lists
Trakt publishes algorithmic lists (trending, popular, most played, box office, anticipated) that
anyone can read with just a client ID. Your liked lists are your own curation and need the OAuth
login. `TRAKTOR_LIST_SOURCE` picks which of the two traktor syncs: `official` (the default),
`liked`, or `both`.

### Matching is by ID, not by title
traktor looks each item up in your library by IMDb ID first, then by TMDb ID. A retitled file still
matches, and a similar title never decides which film you meant. When neither ID is in the library
the item is reported missing with the reason, plus up to three near-matches so you can see why.

### A show in a list becomes an episode
A Plex playlist holds playable items and a show is not one, so a listed show resolves to its S01E01
episode. If S01E01 is not in the library it takes the first episode it can find in any season, and
if there is none the show is reported missing.

### Watch sync moves state, not history
Watch status is compared per item, and only the difference is sent. `WATCH_SYNC_DIRECTION` limits
which way it may travel and `WATCH_SYNC_CONFLICT_RESOLUTION` decides which side wins when the two
disagree. A pull that fails stops the run and says so rather than reading the failure as "nothing
is watched", so an outage cannot wipe your history.

## What it syncs

| Source | Becomes | OAuth |
|--------|---------|-------|
| Trakt official lists (trending, popular, most played, box office, anticipated) | a Plex playlist per endpoint and period, or one merged playlist | no |
| Your liked Trakt lists | a Plex playlist per list | yes |
| Trakt collection | "Trakt Collection - Movies/Shows" playlists | yes |
| Trakt watchlist | "Trakt Watchlist" playlist | yes |

Un-like a list and its playlist disappears on the next run. Watch status, resume points and
conflicts between them travel through `--sync-watched`, `--sync-watched-only` and `--sync-progress`.

## Documentation

- **[QUICKSTART.md](QUICKSTART.md)**: 3-step install, common commands, file locations
- **[DOCKER.md](DOCKER.md)**: Docker setup, scheduling with cron, troubleshooting
- **[CONTRIBUTING.md](CONTRIBUTING.md)**: Dev setup, running tests, PR guidelines

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
| `TRAKTOR_HEALTH_HOST` | all interfaces | Bind address for the health server (set `127.0.0.1` for local-only) |
| `TRAKTOR_HISTORY_RETENTION_DAYS` | `180` | Days to keep watch-history entries (`0` disables pruning) |
| `TRAKTOR_MAX_MEMORY_MB` | `512` | Logs a warning once memory use passes this many MB (CLI: `--max-memory-mb`). It is a threshold, not a hard limit |
| `WATCH_SYNC_DIRECTION` | `both` | Default watch sync direction (`both`, `plex-to-trakt`, `trakt-to-plex`) |
| `WATCH_SYNC_CONFLICT_RESOLUTION` | `newest_wins` | Default conflict strategy (`newest_wins`, `plex_wins`, `trakt_wins`) |

**CLI flags:**
- `--verbose, -v`: Enable debug logging to console
- `--plex-url URL, -u URL`: Override `PLEX_URL` env var
- `--plex-token TOKEN, -t TOKEN`: Override `PLEX_TOKEN` env var
- `--force-auth`: Force re-authentication with Trakt (skip stored tokens)
- `--list-source={official,liked,both}`: Override `TRAKTOR_LIST_SOURCE` env var
- `--sync-watched`, `--sync-watched-only`: Watch status sync (OAuth required)
- `--watch-direction={both,plex-to-trakt,trakt-to-plex}`: Direction for watch sync (default: both)
- `--watch-conflict={newest,plex,trakt}`: Conflict resolution strategy (default: newest)
- `--sync-progress`: Resume point sync Trakt → Plex (OAuth required)
- `--sync-collection`, `--sync-watchlist`: Personal Trakt data (OAuth required)
- `--sync-movies-only`, `--sync-shows-only`: Filter watch sync by media type
- `--backfill-history`: Full history sync (initial setup)
- `--interactive`: Prompt for confirmation before significant changes
- `--undo`: Show the most recent sync snapshot (inspection only; nothing is restored)
- `--structured-logging`: Output JSON logs with correlation IDs
- `--performance-report`: Print detailed performance metrics after sync
- `--refresh-cache`, `--diagnose`, `--dry-run`, `--workers N`: Utility flags

**Mission-critical commands:**
- `--health-check`: Run health checks and report system status
- `--serve-health`: Start HTTP health server for container monitoring
- `--integrity-check`: Verify integrity of config, tokens, and cache files
- `--backup`, `--backup-list`, `--backup-restore PATH`: Backup management
- `--circuit-status`: Show circuit breaker status for API clients
- `--check-update`, `--apply-update`: Check for and apply updates

`--serve-health` answers on `TRAKTOR_HEALTH_PORT`: `/health` for component health, `/metrics` in
Prometheus text format, and `/status` for the last run's outcome. `/status` reports `success`, `failed`
with a short reason, `running` while a run is in progress, or `unknown` until a run has recorded one;
it answers 503 for `failed` and `unknown`, so a monitor can tell a healthy sync from a broken one.

**Official list options:**
- `--official-lists`, `--no-official-lists`: Enable/disable official lists
- `--official-endpoints`: Comma-separated endpoints (e.g., `movies.trending,shows.popular`)
- `--official-period={daily,weekly,monthly,yearly}`: Time period for stats
- `--official-periods`: Multiple periods (e.g., `weekly,monthly`)
- `--official-playlist-mode={separate,merged}`: Per-endpoint or aggregated playlists

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
  - **paste it** into the terminal (the bare code or the full callback URL) and press Enter. That works
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

## Current status and limits

- **A show in a playlist is one episode.** Lists resolve shows to S01E01, or to the first episode
  that exists, so a playlist never mirrors a whole series.
- **A playlist over 1000 items is rebuilt.** Above that size traktor deletes and recreates it
  instead of updating it, so custom playlist metadata is lost.
- **`--undo` restores nothing.** It prints the most recent snapshot for inspection; recovery is
  manual, from the paths it names.
- **`--apply-update` prints the update command.** It does not install anything.
- **`TRAKTOR_MAX_MEMORY_MB` warns; it does not cap.** Linux does not enforce the resident-set
  limit this setting used to apply, so traktor logs a warning once use passes the threshold and
  keeps running.
- **Trakt calls are serialized.** The rate limiter keeps at least 0.3 seconds between Trakt API
  requests, and about a second between official-list calls, so a first sync of a large library takes
  a while; the cache and the delta filter shorten later runs.
- **A failed pull stops the run.** Watch sync leaves `last_sync` where it was and reports the
  failure, rather than assuming either side is empty.

## License

[MIT](LICENSE)
