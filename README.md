# traktor

**Turn Trakt lists into Plex playlists, and sync watch status in both directions.**

traktor is for people who keep their watchlist on Trakt but watch in Plex. Watch a film in Plex and Trakt records it, watch it in Trakt and Plex marks it watched. It also copies the lists you follow into Plex playlists. Run it from cron and it keeps up on its own.

## Contents

**[Part I: Welcome](#part-i-welcome)**

1. [What traktor is](#1-what-traktor-is)
2. [What it syncs](#2-what-it-syncs)
3. [How a run works](#3-how-a-run-works)
4. [Features at a glance](#4-features-at-a-glance)
5. [Who it is for and who it is not for](#5-who-it-is-for-and-who-it-is-not-for)

**[Part II: Getting started](#part-ii-getting-started)**

6. [Install](#6-install)
7. [Credentials](#7-credentials)
8. [Check the install](#8-check-the-install)
9. [First run: playlists](#9-first-run-playlists)
10. [First run: watch status](#10-first-run-watch-status)
11. [Running on a schedule](#11-running-on-a-schedule)
12. [Docker](#12-docker)

**[Part III: Concepts](#part-iii-concepts)**

13. [Lists: official and liked](#13-lists-official-and-liked)
14. [Matching by ID](#14-matching-by-id)
15. [Shows become episodes](#15-shows-become-episodes)
16. [Watch state: direction, conflicts, delta](#16-watch-state-direction-conflicts-delta)
17. [When a pull fails](#17-when-a-pull-fails)
18. [Caching and rate limits](#18-caching-and-rate-limits)

**[Part IV: Reference](#part-iv-reference)**

19. [Command line](#19-command-line)
20. [Environment variables](#20-environment-variables)
21. [Files and paths](#21-files-and-paths)
22. [The missing report](#22-the-missing-report)
23. [Logs and health endpoints](#23-logs-and-health-endpoints)
24. [Backups and integrity checks](#24-backups-and-integrity-checks)
25. [Tuning](#25-tuning)

**[Part V: Operating](#part-v-operating)**

26. [Cron recipes](#26-cron-recipes)
27. [Monitoring a scheduled install](#27-monitoring-a-scheduled-install)
28. [Troubleshooting](#28-troubleshooting)
29. [Limits and known behaviour](#29-limits-and-known-behaviour)

**[Part VI: Development](#part-vi-development)**

30. [Working on traktor](#30-working-on-traktor)
31. [Repository layout](#31-repository-layout)
32. [Tests and gates](#32-tests-and-gates)
33. [Where the rest of the docs live](#33-where-the-rest-of-the-docs-live)

**[License](#license)**

---

# Part I: Welcome

## 1. What traktor is

traktor is a command line tool with two jobs:

- It reads the Trakt lists you follow and builds matching Plex playlists.
- It compares watch state between Trakt and Plex and moves whatever is missing from one side to the other.

The two jobs are independent. You can run only the lists, only the watch status, or both in one pass. Nothing runs on a schedule unless you schedule it.

## 2. What it syncs

Playlists, by source:

| Source | Becomes | Login needed |
|--------|---------|--------------|
| Trakt official lists (trending, popular, most played, box office, anticipated, collected) | one Plex playlist per endpoint and period, or one merged playlist | no |
| Your liked Trakt lists | one Plex playlist per list | yes |
| The Trakt lists your own account created | one Plex playlist per list | yes |
| Trakt collection | "Trakt Collection - Movies/Shows" playlists | yes |
| Trakt watchlist | "Trakt Watchlist" playlist | yes |

Watch state, by direction:

| What | Plex to Trakt | Trakt to Plex | Flag |
|------|---------------|---------------|------|
| Movie watched / unwatched | yes | yes | `--sync-watched` |
| Episode watched / unwatched | yes | yes | `--sync-watched` |
| Playback resume point | no | yes | `--sync-progress` |

Un-like a list on Trakt and its playlist disappears on the next run (`--interactive` asks first).

## 3. How a run works

A list run reads your config and credentials, loads the Plex library into a cache, fetches the lists from Trakt, matches each item against the library, then creates or updates the playlists. Items it cannot match are written to a report with a reason.

A watch run pulls Trakt's watched history first, then the Plex library state, compares the two item by item, computes the changes, and applies them in batches. Trakt is pulled first on purpose: the Plex pass needs to know what Trakt already holds before it decides to skip anything.

Every run records its outcome, which is what the `/status` endpoint serves.

## 4. Features at a glance

**Lists**

- Official Trakt lists need only a client ID, no login
- Playlists are updated in place: only added items are added and removed items are removed
- Playlists that no longer have a list behind them are cleaned up
- Shows resolve to a playable episode rather than an unplayable series entry

**Watch status**

- Watched and unwatched travel both ways, for films and for episodes
- Whatever the two sides disagree about is settled by a strategy you pick
- Repeated runs send only the difference, not the whole history
- A failed pull stops the run instead of being read as "nothing is watched"

**Reliability**

- Circuit breakers around the Trakt and Plex APIs, so an outage fails fast instead of hanging
- Pre-sync and manual backups with an integrity check on restore
- Retries with backoff on rate limits, server errors, timeouts and connection errors
- A cache that survives restarts and refreshes only what changed

**Operations**

- `--serve-health` exposes `/health`, `/metrics` and `/status` for a container probe
- `/status` reports the last run's outcome, so a monitor can tell a healthy install from a broken one
- JSON logs with correlation IDs for machine parsing (`--structured-logging`)
- Per-endpoint timing, cache hit rates and bottleneck detection (`--performance-report`)

## 5. Who it is for and who it is not for

traktor fits you if you have a Plex server, a Trakt account, and a habit of curating lists on Trakt. It expects your library to carry IMDb or TMDb IDs, which Plex provides through its own metadata agents.

It is not for you if:

- You need a playlist to mirror a whole series. A show in a list becomes one episode.
- You want ratings synced. traktor does not sync ratings.
- You want resume points to travel from Plex to Trakt. Only the Trakt to Plex direction exists.
- You want a server that runs itself. traktor is a command you schedule.

# Part II: Getting started

## 6. Install

**Prerequisites:** Python 3.10 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), a Plex server and a Trakt account.

```bash
git clone https://github.com/SwordfishTrumpet/traktor.git && cd traktor
uv sync
cp .env.example .env  # then edit .env with your credentials
```

`uv sync` installs the runtime and the development tools in one step, because they live in the project's dependency groups.

## 7. Credentials

**Trakt.** Create an application at [trakt.tv/oauth/applications](https://trakt.tv/oauth/applications) and copy its Client ID and Client Secret. `TRAKT_REDIRECT_URI` must exactly match the redirect URI you registered, or Trakt rejects the login with `invalid_redirect`. The default `urn:ietf:wg:oauth:2.0:oob` matches the "out of band" style app.

**Plex.** Copy your token from Plex Web, following [Plex's own instructions](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/). Use the server owner's token if you want playlists visible to everyone on the server; a managed user's token creates private playlists only.

Put both in `.env`:

```dotenv
TRAKT_CLIENT_ID=your-client-id
TRAKT_CLIENT_SECRET=your-client-secret
PLEX_URL=http://your-plex-host:32400
PLEX_TOKEN=your-plex-token
```

The first command that needs Trakt asks you to authorize once. Paste the code, or the whole callback URL, into the terminal; if your redirect URI is a localhost URL, a listener picks up the browser redirect instead. The tokens are then written back to the same `.env` file, with the file's permissions restricted to your user. When a refresh happens, traktor prints the new tokens and asks you to update that file.

## 8. Check the install

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

The timestamp is the moment you ran it. A component that is not healthy names the file traktor could not read.

## 9. First run: playlists

```bash
uv run traktor
```

By default this syncs Trakt's official lists, which needs no login beyond your client ID. The run logs its progress, then prints a summary of what it matched and what it could not find, and ends like this:

```text
============================================================
Sync complete!
Total time: ...
============================================================

Summary:
  ...
```

The playlists are on your Plex server under Playlists. Anything traktor could not match is written to `~/.traktor/missing.txt` (`/data/logs/missing.txt` in Docker), with the reason.

To sync your own liked lists as well:

```bash
uv run traktor --list-source=both
```

That needs the Trakt login. To make it the default, set `TRAKTOR_LIST_SOURCE=both` in `.env` instead of passing the flag each time.

If the lists you keep up to date are ones you created on your own Trakt account, sync those instead:

```bash
uv run traktor --list-source=own      # your own lists, plus the official ones
uv run traktor --list-source=all      # your own lists, your liked lists, and the official ones
```

## 10. First run: watch status

Look before you leap: `--dry-run` prints the changes it would make and applies nothing.

```bash
uv run traktor --sync-watched-only --dry-run
uv run traktor --sync-watched-only
```

The output counts what moved in each direction:

```text
Watch sync complete!
  Plex: ... marked watched, ... marked unwatched
  Trakt: ... marked watched, ... marked unwatched
```

If this is the first watch sync on an established account, run it once with `--backfill-history` so both sides start from their full history rather than a recent window:

```bash
uv run traktor --sync-watched --backfill-history
```

`--sync-watched` does the lists and the watch status in one run. `--sync-watched-only` does nothing else, which is what you want on a short interval. `--watch-direction` limits which way state may travel and `--watch-conflict` decides which side wins a disagreement.

## 11. Running on a schedule

traktor is a command, so the scheduler is yours. The usual split is a slow list sync and a fast watch sync, because playlists change rarely and watch state changes constantly:

```bash
# Watch status only, every 15 minutes
*/15 * * * * cd /path/to/traktor && uv run traktor --sync-watched-only

# Lists every six hours
0 */6 * * * cd /path/to/traktor && uv run traktor

# Daily health check
0 7 * * * cd /path/to/traktor && uv run traktor --health-check
```

Each job exits non-zero when something failed, so your scheduler's mail on failure already works. Give the watch job its own interval rather than running everything together: the list sync builds and prunes playlists and takes longer.

## 12. Docker

```bash
cp .env.example .env
docker compose build
docker compose run --rm traktor
```

The compose file mounts your `.env` into the container so the login can write refreshed tokens back, and binds `./data/logs` and `./data/config` for logs and cache. Inside the container `DOCKER_MODE=true` moves every path under `/data`.

Scheduling inside Docker, the health server port and the volume layout are covered in [DOCKER.md](DOCKER.md).

# Part III: Concepts

## 13. Lists: official and liked

Trakt publishes algorithmic lists (trending, popular, most played, box office, anticipated, collected) that anyone can read with a client ID. The lists on your own account are your curation, and reading them needs the OAuth login.

`TRAKTOR_LIST_SOURCE` picks which of the sources traktor syncs:

| Value | What it syncs | Login needed |
|-------|---------------|--------------|
| `official` (the default) | Trakt's curated lists | no |
| `liked` | The lists you liked, whoever wrote them | yes |
| `own` | The lists your account created, private ones included | yes |
| `both` | Liked lists plus the official ones | yes |
| `all` | Own lists, liked lists, and the official ones | yes |

This choice decides which playlists get built and nothing else: watch status reads your Trakt history and your Plex library, and works the same whichever source you pick. A list whose name is in `blacklisted_lists` is skipped from every source, and `own` needs the lists to carry a Trakt list ID, which lists created on trakt.tv always do.

Official lists can be narrowed with `--official-endpoints` and widened across periods with `--official-periods`. `--official-playlist-mode=separate` (the default) writes one playlist per endpoint and period; `merged` combines them by media type, which keeps the playlist count down.

## 14. Matching by ID

traktor looks each item up in your library by IMDb ID first, then by TMDb ID. A retitled file still matches, and a similar title never decides which film you meant. TMDb IDs are stored as strings in the cache for exactly this reason: a numeric comparison would miss them.

When neither ID is in the library the item goes into the missing report with the reason, plus up to three near-matches (a fuzzy title hit, a naming pattern, a year difference) so you can see whether the fault is your file or your ID.

The library cache is what makes this fast: it is built once, keyed by both IDs, and reused until it expires (24 hours) or the library changes.

## 15. Shows become episodes

A Plex playlist holds playable items and a show is not one, so a listed show resolves to its S01E01 episode. If S01E01 is not in the library, traktor takes the first episode it can find in any season. If there is no episode at all, the show is reported missing with that reason.

This is deliberate: it keeps the playlist playable. The cost is that a playlist entry names one episode, not a series, so do not read a playlist as a series watchlist.

## 16. Watch state: direction, conflicts, delta

For every item on either side, traktor asks: what does Plex say, what does Trakt say, and what should move.

- **Direction.** `--watch-direction=both` (the default) allows either way; `plex-to-trakt` and `trakt-to-plex` restrict it.
- **Agreement.** If both sides already say the same thing, nothing moves.
- **Disagreement.** `--watch-conflict=newest` (the default) sends the more recent state, `plex` always copies Plex to Trakt, and `trakt` always copies Trakt to Plex. When one side has no timestamp at all, the side that has one wins, so a fresh Plex watch is not erased by Trakt's silence.
- **Un-watching propagates.** A film watched in Plex and un-watched in Trakt (with the later timestamp) is marked unwatched in Plex.

Repeated runs are cheap because of a delta window: the run only considers items changed since the last sync ended. That window never loses a change, because the filter skips an item only when the other platform already holds the same state. Films are always checked against Trakt's full watched list, and episodes carry their own view count and timestamp.

What traktor keeps for itself lives in `~/.traktor_watch_sync.json`: which items it has already synced and when the last run ended. Entries older than `TRAKTOR_HISTORY_RETENTION_DAYS` (180 by default) are pruned. Nothing is lost by pruning, because every decision is made from the live platform data, not from that file.

## 17. When a pull fails

If either side cannot be read, the run stops before applying anything and records a failure. It never treats an unreachable Trakt or Plex as "nothing is watched there": that reading would push a full rewrite of your history against the other platform.

The practical result: a network outage costs you one skipped run, and the next run picks the work up, because the delta window did not advance.

## 18. Caching and rate limits

The Plex library cache is a JSON file in the cache directory, versioned so an old format is rebuilt rather than misread. It expires after 24 hours, is invalidated when the library hash changes, and can be refreshed on demand with `--refresh-cache` after you add titles.

Trakt's API allows 1000 requests per 5 minutes, so traktor keeps at least 0.3 seconds between requests and retries 429 and 5xx responses with exponential backoff. Official-list calls are limited to about one per second. Batch endpoints are used where Trakt offers them: history writes go 100 items at a time.

Playlists are updated in place up to 1000 items: traktor diffs the current playlist against the new one and only adds and removes the difference. Above that size it deletes and recreates the playlist, which is faster but loses the playlist's own metadata.

# Part IV: Reference

## 19. Command line

`uv run traktor --help` prints every flag. They fall into six groups.

**Core**

| Flag | What it does |
|------|--------------|
| `--plex-url URL`, `-u URL` | Override `PLEX_URL` |
| `--plex-token TOKEN`, `-t TOKEN` | Override `PLEX_TOKEN` |
| `--workers N`, `-w N` | Parallel workers for matching (default 8) |
| `--verbose`, `-v` | Debug logging to the console |
| `--refresh-cache` | Rebuild the Plex library cache |
| `--force-auth` | Re-run the Trakt login |
| `--list-source {official,liked,own,both,all}` | Which list sources to sync |
| `--official-lists`, `--no-official-lists` | Enable or disable official lists |
| `--official-endpoints LIST` | Which official endpoints, comma separated |
| `--official-period {daily,weekly,monthly,yearly}` | Period for statistics endpoints |
| `--official-periods LIST` | Several periods at once |
| `--official-playlist-mode {separate,merged}` | One playlist per endpoint, or merged by type |
| `--sync-collection`, `--sync-watchlist` | Also sync the Trakt collection or watchlist |

**Watch status**

| Flag | What it does |
|------|--------------|
| `--sync-watched` | Lists plus two-way watch status |
| `--sync-watched-only` | Watch status only |
| `--watch-direction {both,plex-to-trakt,trakt-to-plex}` | Which way state may travel |
| `--watch-conflict {newest,plex,trakt}` | Who wins a disagreement |
| `--sync-movies-only`, `--sync-shows-only` | Restrict to one media type |
| `--backfill-history` | Ignore the delta window for one run |
| `--sync-progress` | Resume points, Trakt to Plex |
| `--dry-run` | Preview watch-sync changes, apply nothing |
| `--interactive` | Preview and confirm before deleting playlists or applying changes |
| `--undo` | Print the most recent snapshot (inspection only) |

**Maintenance**

| Flag | What it does |
|------|--------------|
| `--diagnose` | Check configuration and connectivity |
| `--health-check` | Run the health checks and print the result |
| `--integrity-check` | Verify config, tokens and cache files parse |
| `--circuit-status` | Show the API circuit breakers |
| `--backup` | Write a backup of config, credentials and cache |
| `--backup-list` | List the backups |
| `--backup-restore PATH` | Restore one backup |

**Monitoring**

| Flag | What it does |
|------|--------------|
| `--serve-health` | Serve `/health`, `/metrics` and `/status` |
| `--structured-logging` | JSON to the log file, plain text to the console |
| `--performance-report` | API timing, cache hit rate, bottlenecks after a run |

**Tuning**

| Flag | What it does |
|------|--------------|
| `--max-memory-mb N` | Log a warning once memory use passes N MB |
| `--cpu-throttle PCT` | Target CPU percentage for throttling |

**Updates**

| Flag | What it does |
|------|--------------|
| `--check-update` | Ask GitHub whether a newer release exists |
| `--apply-update` | Print the update command to run |

Exit codes: `0` when every list and every watch change succeeded, `1` when something failed. A scheduled run that fails is meant to be visible, so your scheduler's failure mail is the alert.

## 20. Environment variables

Everything here can live in `.env`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `TRAKT_CLIENT_ID` | required | Trakt application client ID |
| `TRAKT_CLIENT_SECRET` | required | Trakt application client secret |
| `TRAKT_REDIRECT_URI` | `urn:ietf:wg:oauth:2.0:oob` | Must match the redirect registered on the Trakt app |
| `TRAKT_ACCESS_TOKEN` | empty | Written by the login; leave it alone |
| `TRAKT_REFRESH_TOKEN` | empty | Written by the login; leave it alone |
| `PLEX_URL` | required | Plex server URL |
| `PLEX_TOKEN` | required | Plex token |
| `TRAKTOR_LIST_SOURCE` | `official` | `official`, `liked`, `own`, `both` or `all` |
| `TRAKTOR_OFFICIAL_LISTS_ENABLED` | `true` | Sync official lists at all |
| `TRAKTOR_OFFICIAL_ENDPOINTS` | empty | Comma-separated endpoints |
| `TRAKTOR_OFFICIAL_PERIOD` | `weekly` | Period for statistics endpoints |
| `TRAKTOR_OFFICIAL_PERIODS` | empty | Several periods, comma separated |
| `TRAKTOR_OFFICIAL_CACHE_TTL_TRENDING` | `1` | Cache lifetime in hours |
| `TRAKTOR_OFFICIAL_CACHE_TTL_POPULAR` | `6` | Cache lifetime in hours |
| `TRAKTOR_OFFICIAL_CACHE_TTL_PLAYED` | `6` | Cache lifetime in hours |
| `TRAKTOR_OFFICIAL_CACHE_TTL_WATCHED` | `6` | Cache lifetime in hours |
| `TRAKTOR_OFFICIAL_CACHE_TTL_COLLECTED` | `24` | Cache lifetime in hours |
| `TRAKTOR_OFFICIAL_CACHE_TTL_ANTICIPATED` | `24` | Cache lifetime in hours |
| `TRAKTOR_OFFICIAL_CACHE_TTL_BOXOFFICE` | `12` | Cache lifetime in hours |
| `TRAKTOR_WORKERS` | `8` | Parallel workers |
| `WATCH_SYNC_DIRECTION` | `both` | Default watch direction |
| `WATCH_SYNC_CONFLICT_RESOLUTION` | `newest_wins` | `newest_wins`, `plex_wins`, `trakt_wins` |
| `TRAKTOR_HISTORY_RETENTION_DAYS` | `180` | Prune synced-item bookkeeping older than this; `0` disables pruning |
| `TRAKTOR_BACKUP_BEFORE_SYNC` | `false` | Write a backup before each sync |
| `TRAKTOR_MAX_MEMORY_MB` | `512` | Warning threshold, not a hard limit |
| `TRAKTOR_HEALTH_PORT` | `8080` | Port for the health server |
| `TRAKTOR_HEALTH_HOST` | all interfaces | Bind address; set `127.0.0.1` for local only |
| `DOCKER_MODE` | `false` | Move every path under `/data` |

## 21. Files and paths

| What | Local | Docker |
|------|-------|--------|
| Config | `~/.traktor_config.json` | `/data/config/.traktor_config.json` |
| Credentials | `.env` in the working directory | `/app/.env` |
| Plex library cache | `~/.traktor_cache/` | `/data/config/.traktor_cache/` |
| Watch-sync state | `~/.traktor_watch_sync.json` | `/data/.traktor_watch_sync.json` |
| Run record | `~/.traktor_cache/sync_progress.json` | `/data/config/.traktor_cache/sync_progress.json` |
| Log | `~/.traktor/traktor.log` | `/data/logs/traktor.log` |
| Missing report | `~/.traktor/missing.txt` | `/data/logs/missing.txt` |
| Backups | `~/.traktor_backups/` | the home directory in the container |

Credentials live in the `.env` file, not in a separate token file, because that is the file traktor reads and writes on refresh.

## 22. The missing report

Every run rewrites `missing.txt` from scratch. Each line names the item, the list it came from, and why it could not be matched: a film not in the library, a show with no episodes, a show whose data could not be read, or an error with its message. Where a near-match exists, it is listed with the score so you can tell a wrong ID from a missing file.

The report is the fastest way to fix a short playlist: it says whether the problem is your library, your IDs, or your naming.

## 23. Logs and health endpoints

The log file rotates at 5 MB and keeps five files. `--verbose` sends debug lines to the console too, and `--structured-logging` writes the file as JSON with correlation IDs while the console stays readable.

`--serve-health` starts an HTTP server for a container probe:

| Endpoint | Answers |
|----------|---------|
| `/health` | Component health, 200 when healthy, 503 when degraded or unhealthy |
| `/metrics` | Prometheus text format, one line per component |
| `/status` | The last run's outcome |

`/status` reports `success`, `failed` with a short reason, `running` while a run is in progress, or `unknown` until a run has recorded one. It answers 503 for `failed` and `unknown`, so a monitor can tell a healthy install from one that has been failing quietly. A failed run does not make `/health` fail: the process is alive, the sync is not.

## 24. Backups and integrity checks

```bash
uv run traktor --backup
uv run traktor --backup-list
uv run traktor --backup-restore ~/.traktor_backups/traktor_backup_YYYYMMDD_HHMMSS_manual
```

A backup carries your config, your `.env` credentials and the cache directory, with a manifest of what it wrote. Ten are kept by default. Restoring refuses a manifest that names a path this build did not write, so a doctored backup cannot scatter files across your disk.

`--integrity-check` verifies the same files without restoring anything: config JSON parses, credentials look like credentials, and every cache file is readable. A pre-sync check runs before each sync and asks before continuing when something is corrupt.

## 25. Tuning

- **Workers.** `--workers` (or `TRAKTOR_WORKERS`) controls how many items are matched in parallel. The default of 8 suits a small server; raise it only if matching is your bottleneck.
- **Memory.** `--max-memory-mb` is a warning threshold. Linux does not enforce the resident-set limit this setting once applied, so traktor logs a warning when use passes it and keeps running; the process is bounded by your container, not by this number.
- **CPU.** `--cpu-throttle` sleeps between work units to hit an approximate CPU ceiling, which matters on a shared box.
- **Rate limits.** The Trakt limits are fixed by Trakt. Raising workers will not make Trakt answer faster; it will only make you wait inside the limiter.

# Part V: Operating

## 26. Cron recipes

```bash
# Watch state both ways, every 15 minutes
*/15 * * * * cd /path/to/traktor && uv run traktor --sync-watched-only >> ~/.traktor/cron.log 2>&1

# Lists and watch state, every six hours
0 */6 * * * cd /path/to/traktor && uv run traktor >> ~/.traktor/cron.log 2>&1

# Health check, every morning at 07:00
0 7 * * * cd /path/to/traktor && uv run traktor --health-check
```

Point the jobs at an absolute path, because cron's working directory is not your shell's. If a run fails, the non-zero exit is your alert; `/status` tells you what happened.

## 27. Monitoring a scheduled install

Three things are worth watching:

1. **The run's own verdict.** `curl -s localhost:8080/status` (with `--serve-health` running) reports the last outcome and when a run last succeeded. A `failed` that never clears is a broken login most of the time.
2. **The log.** `grep -E "ERROR|CRITICAL" ~/.traktor/traktor.log` shows what failed. The log rotates, so this is a recent window, not a history.
3. **The missing report.** It is rewritten each run. If a title you own has moved into it, the fix is usually `--refresh-cache`, then the reason line tells you the rest.

## 28. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Titles you own are reported missing | Stale library cache | `uv run traktor --refresh-cache` |
| 401 from Trakt, or "authentication expired" | The refresh token was revoked or replaced | `uv run traktor --force-auth` |
| "incomplete Plex credentials" | Only one of `PLEX_URL` and `PLEX_TOKEN` is set | Set both, or neither to be prompted |
| A film shows as watched in the wrong place | The other side has a newer timestamp | `--watch-conflict=plex` or `trakt`, or fix the timestamp at the source |
| Nothing happens on the first list run | No official lists enabled, or none configured | Check `TRAKTOR_OFFICIAL_LISTS_ENABLED` and `--official-endpoints` |
| Circuit breaker reports OPEN | Trakt or Plex is failing repeatedly | Wait the cooldown (60s Trakt, 30s Plex) or check `--circuit-status` |
| Playlists exist but other users cannot see them | Playlist was created with a managed-user token | Re-run with the server owner's token |
| A restore refuses to run | The backup manifest names a path this build did not write | Restore the backup by hand from the paths it lists |
| Every run warns about memory | The threshold is below your steady state | Raise `TRAKTOR_MAX_MEMORY_MB`, or ignore it: nothing is capped |

## 29. Limits and known behaviour

- **A show in a playlist is one episode.** Lists resolve shows to S01E01, or to the first episode that exists, so a playlist never mirrors a whole series.
- **A playlist over 1000 items is rebuilt.** Above that size traktor deletes and recreates it instead of updating it, so custom playlist metadata is lost.
- **`--dry-run` covers watch status only.** It previews the watch-sync changes; playlists are still written.
- **`--undo` restores nothing.** It prints the most recent snapshot for inspection, and recovery is manual from the paths it names.
- **`--apply-update` prints the update command.** It does not install anything.
- **`TRAKTOR_MAX_MEMORY_MB` warns; it does not cap.** Linux does not enforce the resident-set limit this setting used to apply.
- **Trakt calls are serialized.** At least 0.3 seconds between requests and about a second for official lists, so a first sync of a large library takes a while. The cache and the delta window shorten later runs.
- **A failed pull stops the run.** `last_sync` stays where it was and the failure is reported, rather than either side being treated as empty.
- **Resume points travel one way.** Trakt to Plex only.
- **One Plex server per configuration.** The credentials in `.env` name a single server.

# Part VI: Development

## 30. Working on traktor

```bash
uv sync                      # runtime and dev tools
uv run pytest -q -W error    # the suite, with warnings as errors
uv run ruff check src/ tests/
uv run mypy src/
uv run black src/ tests/
```

`make test`, `make lint` and `make format` wrap the same commands. The Python floor is 3.10.

## 31. Repository layout

```text
src/traktor/
  cli.py              argument parsing and the entry point
  sync.py             list, collection, watchlist orchestration
  watch_sync.py       watch state comparison and application
  clients.py          Trakt and Plex clients, cache, rate limiting
  conflict_resolver.py how a disagreement is settled
  run_status.py       the run record /status serves
  history_manager.py  what has been synced, and when
  official_lists.py   official list fetching and caching
  resilience.py       circuit breakers, health checks, backups
  health_server.py    /health, /metrics, /status
  interactive.py      previews, confirmations, snapshots
  progress.py         progress and ETA
  performance.py      timing metrics
  settings.py         paths and environment
tests/                pytest suite, one file per module
docs/                 design notes
```

## 32. Tests and gates

A change lands with its test: name the failing behaviour, prove it fails before the fix and passes after. CI runs the suite with warnings as errors, so a resource leak fails the build, and uploads coverage to Codecov without gating on it.

Before you open a pull request, run the three commands above and make sure the suite is green. A test that had to be weakened to pass is a red flag, not a green run.

## 33. Where the rest of the docs live

| Document | What is in it |
|----------|----------------|
| [QUICKSTART.md](QUICKSTART.md) | The three-step install and the commands you will use most |
| [DOCKER.md](DOCKER.md) | Compose setup, scheduling, volume layout, container troubleshooting |
| [RUNBOOK.md](RUNBOOK.md) | Operating a long-running install: incidents, recovery, circuit breakers |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup, tests, pull request expectations |
| [SECURITY.md](SECURITY.md) | How to report a vulnerability |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each release |

# License

[MIT](LICENSE)
