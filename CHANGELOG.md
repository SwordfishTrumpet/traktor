# Changelog

All notable changes to this project should be documented in this file.

The format is based on Keep a Changelog and this project currently uses a simple manual release flow.

## [Unreleased]

### Fixed

- **Episodes watched on Trakt are marked watched in Plex again (high)**
  - `GET /sync/watched/shows` stopped nesting `seasons`/`episodes`: it now returns one
    show-level entry per show (`plays`, `last_watched_at`, `show`), with or without
    `extended=full`. `_pull_from_trakt()` parsed that payload as an episode list, so every
    run pulled **zero episodes** - an episode watched anywhere that scrobbles to Trakt
    (Stremio, a phone, a TV) could never be marked watched in Plex. The log line that hid
    it, `Retrieved 100 watched show(s) with 0 episode(s)`, was reported as an INFO count
    and never checked
  - Watched episodes now come from `GET /sync/history/episodes`, the only watched-episode
    source that carries both halves of an episode's identity: the parent show's ids and the
    episode's `season`/`number`. `TraktClient.get_watched_shows()` is replaced by
    `get_watched_episodes()`, which paginates through `_paginated_get()`
  - The same endpoint was also truncated: `get_watched_shows()` made a single unpaginated
    request, so only the first 100 watched shows (of 297 here) were ever read. Live check:
    `X-Pagination-Item-Count: 297`, `X-Pagination-Page-Count: 3`
  - Rewatches arrive once per play, so the pull keeps the newest `watched_at` per
    (show, season, episode) via `utils.parse_timestamp()` and counts the plays. Entries
    without a show IMDb id are skipped and reported: their key would be shared by every
    show lacking one, and matching one show's episode for another's is worse than a skip
  - Live account: 3,714 plays collapse to 3,563 watched episodes, including the three
    newest South Park episodes that had to be marked in Plex by hand

## [1.1.1] - 2026-11-20

### Added

- **Your own Trakt lists can be synced now (issue #26)**
  - `--list-source=own` / `TRAKTOR_LIST_SOURCE=own` syncs the lists your Trakt account
    created (private ones included) through the new `TraktClient.get_own_lists()`
  - `--list-source=all` syncs own lists, liked lists and the official ones in one run;
    `liked`, `both` and `official` keep their meaning
  - A private list is read through `users/me/lists/{id}/items` when the owner-named path
    answers 403, so private lists sync too
  - A list that matches nothing (or is empty) no longer crashes the run: creating a playlist
    with no items is skipped with a log line, and one list's unexpected failure is reported
    for that list instead of aborting the sync

### Changed

- **Watch sync only reconciles watch state, never watch dates (issue #37)**
  - When both platforms already agree an item is watched, the sync now reports `no_action`
    instead of pushing the newer timestamp to the older side. Both pushes restamped the
    winner with "now", so the difference never closed: every run re-marked the same ~500
    movies in Plex and re-added ~100 plays to Trakt, inflating play counts and overwriting
    real watch dates with the run time
  - `ConflictResolver._is_newer()`, `_should_update_timestamp()` and
    `DEFAULT_TIMESTAMP_THRESHOLD_SECONDS` are gone with the behavior they enabled

- **Watch history state growth is now bounded (issue #7 follow-up)**
  - Synced-item entries carry an `updated_at` stamp; `_apply_changes()` prunes entries older
    than the retention window before each batch persist
  - Configurable via new `TRAKTOR_HISTORY_RETENTION_DAYS` env var (default: 180 days, 0 disables);
    pruning is safe because reconciliation resolves from live platform data, not history state

### Fixed

- **A show Plex cannot answer for no longer drops out of the watch sync silently (issue #22)**
  - A transient read failure (timeout, connection reset) is retried once, 2 s apart. If the
    retry fails too, the pull raises `WatchStatePullError` naming the show, the watch sync
    reports the failure, and `_sync_lists()` returns 1 so the run is recorded failed instead of
    succeeding with that show's watches missing
  - A show Plex no longer has stays a skip, but is now counted (`stats["shows_unreadable"]`) and
    logged by name at WARNING, so it cannot be mistaken for a show with nothing watched

- **A delta run no longer re-reads every show's episodes (issue #21)**
  - The Plex pull now keeps each show's episode watch rows in the library cache, next to the
    show counters they were read with (`viewedLeafCount`, `leafCount`, `lastViewedAt`). A delta
    run reuses those rows while the counters are unchanged, and re-reads only the shows whose
    counters moved. The state handed to the resolver is identical; only the requests are gone
  - `PlexClient.get_show_counters()` reads the show level for the whole library (one paged read
    per section, 52.9 s for 4,596 shows on the owner's server) instead of one `fetchItem` plus
    roughly two season requests per show on every run
  - A show whose read fails, or whose counters Plex does not report, has its snapshot dropped, so
    a later run reads it again instead of trusting a partial read. `--backfill-history` still
    reads every show

- **A Plex→Trakt push keeps the Plex watch date (issue #37)**
  - `_apply_changes()` dropped the `watched_at` value the resolver had already attached to
    the change, so Trakt stamped every pushed play with the moment of the request. Movie and
    episode payloads now carry it (rendered by the new `utils.format_trakt_timestamp()`),
    which also lets a sync converge instead of re-pushing on every run
- **A Trakt response with unresolved items no longer aborts the push accounting (issue #38)**
  - `not_found` lists the offending payloads instead of counting them, so summing it raised
    `TypeError: unsupported operand type(s) for +: 'int' and 'list'`. Every scheduled run
    logged "Failed to add items to Trakt history" and counted all pushed items as errors
    even though the batches had succeeded. Trakt response fields are now counted through
    `clients.count_trakt_items()`, which accepts a number, a payload list or a missing field

- **A retried playlist create no longer leaves a duplicate that stops updating (issue #24)**
  - Plex can answer 500 after it already stored a playlist; the retry now looks the title up
    first instead of creating a twin playlist
  - A title that already resolves to several playlists keeps the first and deletes the rest
    with a logged warning, so existing duplicates are cleaned up on the next sync
- **An unusable Trakt token no longer stops the official lists (issue #25)**
  - When authentication fails and official lists are enabled, the run logs the degradation and
    continues with them; it exits non-zero only when nothing is left to sync or an OAuth-only
    mode (`--sync-watched-only`, `--sync-watched`, collection/watchlist/progress) was requested
- **Trakt batch history failures are now visible and tracked (issue #9)**
  - `_batch_history_operation()` swallowed per-batch RequestExceptions; the combined result
    now reports `failed` item counts (and failed payloads) alongside successes
  - `_apply_changes()` adds batch failure counts to `stats["errors"]` for both add and remove
    operations, so partial outages are visible in sync stats
  - Plex batch operations report per-item `failed_rating_keys`; items whose Plex mark operation
    failed are no longer recorded as synced in watch history state
- **Dependency Review workflow fixed** — scoped to pull_request events (the action cannot run
  on push: it needs a base/head ref diff) and granted the write permissions that
  `comment-summary-in-pr` requires

- **CI Bandit job now actually gates on findings (issue #8)**
  - The security job ran Bandit with `|| true` (could never fail) and uploaded its report under
    `if: failure()` (unreachable); the floating `PyCQA/bandit@main` action ref was replaced with a
    version-pinned `bandit==1.9.4` invocation failing on medium+ severity / medium+ confidence
  - Report artifact is now uploaded on every run (`if: always()`)
- Resolved the three pre-existing Bandit findings so the new gate starts green:
  - `auto_update`: `urlopen` → `requests` (removes B310; error handling preserved)
  - `health_server`: bind host configurable via `TRAKTOR_HEALTH_HOST` (default: all interfaces;
    set `127.0.0.1` for local-only) instead of a hardcoded `0.0.0.0` literal (B104)
  - `sync.py`: silent `except Exception: pass` in playlist snapshotting now logs at debug (B110)

- **Watch history apply phase no longer rewrites the state file per item (issue #7)**
  - `add_or_update_synced_item()` performed a linear scan plus a full-file `save_state()` per
    item — O(N·(M+N)) CPU and N disk writes during watch-sync apply
  - State is now mutated in memory and persisted exactly once after the batch (also on error);
    `plex_rating_key` lookups use a dict index instead of scanning the list; rating-key matches
    are exact-only, which also stops episodes of the same show from cross-matching via shared IMDb ID

- **Backups now include the live .env token store (issue #6)**
  - BackupManager archived the legacy token JSON file that current code never writes,
    so backups could not restore Trakt authentication after a disaster
  - The backup manifest now covers the active `.env` (Docker `/app/.env` or CWD `.env`,
    matching `TraktAuth.save_tokens()` resolution); archived and restored copies get
    mode 0o600; the manifest stores only path + checksum, never token values

- **Integrity checks now cover the gzip-compressed library cache (issue #5)**
  - `IntegrityChecker._check_cache()` only scanned `*.json`, so the primary
    `plex_library_cache.json.gz` was never validated and corrupt caches passed the gate
  - `.json.gz` files are now decompressed and JSON-parsed; truncated/invalid gzip streams
    (`BadGzipFile`, `EOFError`) are reported as corrupt files

- **`--undo` no longer claims restore capability it never had (issue #4)**
  - `--undo` is now honest snapshot inspection: shows the most recent snapshot and its
    contents; the misleading "Restore from this snapshot on next sync?" prompt and the
    "restoration on next sync run" claim were removed (no restore code path exists)
  - Removed the post-sync watch-sync snapshot save in the non-interactive branch — it
    captured post-change state, which can never serve as undo state
  - README/CLI help updated to match actual behavior

- **Pre-sync prompts no longer crash non-interactive/cron runs (issue #3)**
  - Integrity-check and backup-failure prompts called `input()` without a TTY guard,
    raising unhandled `EOFError` in cron/Docker runs
  - New `_confirm_continue()` helper fails closed (clean exit 1) when stdin is not a TTY;
    interactive behavior unchanged

- **Watch sync no longer proceeds after failed state pulls (HIGH, issue #1)**
  - `_pull_from_plex()` / `_pull_from_trakt()` caught all exceptions and returned an empty
    dict; a transient Trakt outage made every Plex-watched item resolve to `push_to_trakt`,
    triggering a full-history rewrite against Trakt while reporting success
  - Pull failures now raise `WatchStatePullError`; `sync_watched_status()` aborts before any
    change is applied, increments `errors`, and leaves `last_sync_timestamp` untouched

### Releasing and rollback

A `v*` tag is the release. `.github/workflows/release.yml` tests the tree, builds the wheel and
sdist, and attaches both to the GitHub Release it creates from this section; nothing is
published to PyPI, so no package-index account is involved. Updating means pulling a checkout
(`git pull && uv sync`) or installing that release's wheel. Rolling back a release still has no
written procedure - issue #72 tracks it.

### Every commit in this release

- Format the batch with black so CI accepts it
- Make the C-locale regression test decode non-ASCII bytes (#49)
- Register the runnable reproductions for this batch (#23 through #59)
- Document the Plex flag pair and the automatic token removal (#48, #44)
- Ship a container that builds and a first-run example that syncs (#56, #57, #58)
- Use the same config validation in --health-check (#47, #49, #59)
- Report the same version that the package declares (#52)
- Escape remote release text before printing it (#43)
- Write the watch state and the official-list cache atomically (#23)
- Verify a multi-file backup in a stable order and report the first health failure (#42, #59, #23)
- Index the library once for suggestions and keep the auth code out of the log (#53, #45, #43, #54, #23)
- Validate cached rating keys, drop revoked tokens, and stop repeating reads (#41, #44, #46, #50, #51, #54, #55, #23)
- Reject a non-object config, partial Plex flags, and read the config as UTF-8 (#47, #48, #49, #23)
- Add one atomic writer for state files and escape terminal control bytes (#23, #43)
- Declare the lint and typecheck commands so the gate can run them (#39)
- Skip shows with no watch state on either platform in the watch pull (#39)
- Fail the run when a show's episode state cannot be read (#22)
- bump ruff from 0.17.2 to 0.17.5
- bump pytest from 9.1.1 to 9.2.0
- Cache each show's episode watch state so a delta run stops re-reading the library (#21)
- Count Trakt response fields that arrive as payload lists (#38)
- Carry the watch date into Trakt and stop pushing agreed states (#37)
- Call the list counts personal, since owned lists are not liked lists (#26)
- Skip the create when a list has nothing to add (#26)
- Read private lists through the owner route (#26)
- bump mypy from 2.4.0 to 2.4.1
- bump types-requests
- bump actions/cache from 4 to 6 in /.github/workflows
- bump actions/upload-artifact in /.github/workflows
- bump codecov/codecov-action in /.github/workflows
- bump actions/checkout from 4 to 7 in /.github/workflows
- bump actions/setup-python from 5 to 7 in /.github/workflows
- Sync your own lists, stop duplicate playlists, survive a bad token (#24, #25, #26)
- Rewrite the README as a manual (#17)
- Advertise bidirectional watch status in the README (#17)
- Separate watch status from the list source in the README (#17)
- Make the README read as a manual (#17)
- Document the batch and register its reproductions (#13, #16, #17, #19, #20)
- Say what the memory setting does instead of promising a ceiling (#16)
- Report the real last-run outcome from /status (#13)
- Stop watch sync aborting when both platforms watched a title (#20)
- Stop losing watches the delta filter never saw (#19)
- advisory floors, dev-only stubs, drop the Docker certifi hack
- Make the documented official-list cache TTLs take effect (#15)
- Fail closed when an interactive confirmation has no terminal (#14)
- Restrict credential files to their owner (#12)
- Refuse a backup manifest that names a path this build did not write (#11)
- Require the OAuth state this login issued before accepting a code (#10)
- Quote version variable in release notes extraction (SC2086)
- Bound watch-history state growth with age-based pruning (#7 follow-up)
- Bump CodeQL action to v4; add actionlint job to CI
- Surface per-batch history failures; skip recording failed items (#9)
- Fix Dependency Review workflow: PR-only trigger + required permissions
- Make CI Bandit job gate on findings; resolve existing findings (#8)
- Batch-persist watch history state; index rating-key lookups (#7)
- Back up the live .env token store instead of the legacy token file (#6)
- Include .json.gz library cache in integrity checks (#5)
- Demote --undo to snapshot inspection; remove false restore claims (#4)
- Fail closed on pre-sync prompts in non-interactive runs (#3)
- Fix false 'Failed to process list' print on successful syncs (#2)
- Propagate watch-state pull failures instead of degrading to empty state (#1)

## [1.1.0] - 2026-08-16

### Performance

- **Episode watch-status fetch no longer storms the Plex API (CRITICAL)**
  - `_process_episode_batch()` called `PlexClient.is_watched()` per episode, which always fell
    through to `fetchItem` (episodes are not in the rating-key cache) — ~1 API call per episode,
    ~100k+ calls for a large library per watch-sync run
  - Watch state is now read directly from the `season.episodes()` response
    (`isWatched` = `viewCount > 0`, plus `lastViewedAt`); zero per-episode API calls
  - Also fixed a latent `KeyError: 'items_skipped_due_to_delta'` when the engine ran without
    `sync_watched_status()` having initialized the full stats dict
  - Location: `src/traktor/watch_sync.py`

- **`PerformanceMonitor.api_calls` is now LRU-bounded**
  - `MAX_API_CALLS_TRACKED` (1000) was defined but never enforced; the endpoint dict could grow
    without bound in long-running processes (health server, cron)
  - `record_api_call()` now evicts the least-recently-recorded endpoint at the cap
  - Location: `src/traktor/performance.py`

- **Watch sync reverse maps are built once per cache load**
  - `_pull_from_plex()` and `sync_playback_progress()` rebuilt `rating_key -> (imdb, tmdb)` maps
    from the cache on every run
  - New `CacheManager` properties (`movie_key_to_ids`, `show_key_to_imdb`, `movie_imdb_to_rating_key`)
    build them lazily and invalidate on cache changes
  - Location: `src/traktor/clients.py`, `src/traktor/watch_sync.py`

### Fixed

- **POST history operations never refreshed expired tokens**
  - `_post_with_token_refresh()` caught `HTTPError` for 401s, but `_request_with_retry()` returns
    401 responses instead of raising — so token refresh never ran for Trakt history writes
  - New shared `_execute_with_token_refresh()` helper handles 401 -> refresh -> retry-once for
    both GET and POST paths
  - Location: `src/traktor/clients.py`

- **Trakt collection sync silently dropped all shows**
  - `get_collection("shows")` looked up the `"shows"` key, but Trakt returns the singular
    `"show"` key — every show was skipped
  - Location: `src/traktor/clients.py`

- **Restore path hardened for corrupt backups**
  - `restore_backup()` now returns False (not raises) on an unreadable manifest; `_verify_backup_item()`
    returns False on corrupt gzip streams instead of raising `BadGzipFile`
  - Location: `src/traktor/resilience.py`

### Changed

- **Timestamp parsing unified in `utils.parse_timestamp()`**
  - Three near-identical implementations (`CacheManager._normalize_added_at`,
    `WatchSyncEngine._parse_plex_timestamp`, `ConflictResolver._normalize_timestamp`) replaced by
    one canonical parser handling naive (system local), aware, epoch, ISO string, and named-tz input
  - The conflict resolver now interprets naive timestamps as system local time (was: assume UTC),
    matching plexapi's documented behavior
  - Location: `src/traktor/utils.py`, `src/traktor/clients.py`, `src/traktor/watch_sync.py`, `src/traktor/conflict_resolver.py`

- **Trakt pagination centralized**
  - New `TraktClient._paginated_get()` shared by `get_watched_movies()` and `get_list_items()`
    (page-count headers, short-page break, `MAX_HISTORY_PAGES` safety)
  - Location: `src/traktor/clients.py`

- **CI matrix aligned with Python floor**
  - Removed 3.8/3.9 from the test matrix (project floor is now >= 3.10); tests run on 3.10-3.12
  - `uv sync --extra dev` -> `--group dev` in CI and Release workflows (dev tools moved to the
    PEP 735 `[dependency-groups] dev` group); CI now emits `coverage.xml` for the Codecov upload
  - Location: `.github/workflows/ci.yml`, `.github/workflows/release.yml`

- **Dead code removed (audit C1-C13)**
  - Deleted: `update_cache_incremental`, `get_all_watched_history`, `get_watched_items`,
    `get_play_history`, `get_all_synced_items`, `restore_last_undo` (+ `_restore_playlist_snapshot`),
    `ConflictResolver.set_strategy/get_strategy/get_valid_strategies`, `_get_cache_metadata_file`,
    `get_sync_summary`, `_build_missing_item`/`_extract_missing_item_details` wrappers, and the
    unused `compressed` parameter of `BackupManager._restore_item`
  - Removed env vars with no consumers: `WATCH_SYNC_ENABLED`, `TRAKTOR_CPU_THROTTLE`,
    `TRAKTOR_BANDWIDTH_LIMIT_KBPS` (and the unused constants `CONNECTION_MAX_REUSE`,
    `MEMORY_SAMPLE_INTERVAL`, `TIMEZONE_DRIFT_THRESHOLD_SECONDS`)
  - Watch sync is gated by CLI flags only; resource limits via `--max-memory-mb` / `--cpu-throttle`

### Testing

- 719 tests (was 550), line coverage 56% -> 79%
- New coverage: `sync_lists()` orchestration, `cli.py` handlers + `main()` dispatch,
  `_collect_auth_code()` callback paths, `sync_playback_progress()`, `_apply_changes()` failure
  paths, BackupManager create/restore/verify/cleanup, IntegrityChecker, TraktAuth refresh paths,
  Trakt pagination/batch history, PlexClient orphaned-playlist cleanup, `__main__` runner,
  conflict-resolver confidence branches, config/diagnose edge cases

### Fixed

- **Watch sync: movies watched on other services were never marked watched in Plex (CRITICAL)**
  - `_pull_from_trakt()` fetched movies via Trakt *history* filtered by `start_at` (last sync),
    so any movie watched before the sync window (first run = last 7 days) never appeared in the
    Trakt state and was never pushed to Plex. Shows were unaffected (always full `get_watched_shows()`)
  - Movies now always fetch the full `get_watched_movies()` list (authoritative watched state);
    delta filtering stays on the Plex side where unwatched items (no `lastViewedAt`) are never skipped
  - Location: `src/traktor/watch_sync.py`

- **Watch sync: naive/aware datetime crash silently emptied the Plex pull (CRITICAL)**
  - plexapi returns `lastViewedAt` as a naive *local* datetime, serialized to naive ISO strings in the
    cache; comparing it against the aware `since` datetime raised `TypeError: can't compare offset-naive
    and offset-aware datetimes`, the broad `except` swallowed it, and `_pull_from_plex()` returned `{}` —
    so nothing was ever synced to Plex whenever `since` was set and any library item had been watched
  - `_parse_plex_timestamp()` now always returns an aware UTC datetime (naive values interpreted as
    system local time, matching plexapi's `datetime.fromtimestamp()` behavior); also handles datetime
    objects from API lookups
  - Location: `src/traktor/watch_sync.py`

- **Trakt auth: placeholder/truncated tokens silently "authenticated" for months (CRITICAL)**
  - `authenticate_trakt()` only checked that the access token was non-empty, so literal placeholder
    text like `new-access-token` in `.env` passed the check while every API call 401'd (liked lists /
    watch sync silently dead; official lists kept working so it went unnoticed)
  - New `TraktAuth.has_valid_tokens()` rejects known placeholder strings and values far too short
    to be real tokens; `save_tokens()` refuses to write invalid tokens
  - Location: `src/traktor/clients.py`, `src/traktor/sync.py`

- **Trakt auth: 32-char token format wrongly rejected (FIXED IMMEDIATELY AFTER)**
  - Trakt now issues 32-character access/refresh tokens (verified via a live OAuth exchange), but
    the length floors above assumed the old JWT format and rejected valid tokens, preventing
    `save_tokens()` from persisting them
  - Floors lowered to 20 chars — still catches the 16-17 char placeholder garbage while never
    locking out a legitimate Trakt format change
  - Location: `src/traktor/clients.py`

- **Trakt auth: pasted code ignored while waiting on localhost callback (remote/headless UX bug)**
  - On remote boxes the browser redirect to `http://127.0.0.1:7001/callback` lands on the user's
    own machine, never the server, so the 5-minute listener wait swallowed pasted input and the
    run had to be Ctrl+C'd
  - `_collect_auth_code()` now watches stdin (`select`) and the callback listener concurrently;
    a pasted code or full callback URL is accepted at any time and wins immediately
  - `_extract_auth_code()` parses bare codes, bare query strings, and full callback URLs
  - Location: `src/traktor/sync.py`


- **Incremental cache update broken by plexapi datetime change (CRITICAL)**
  - `plexapi >= 4.17` returns `addedAt` as a naive local `datetime` (via `utils.toDatetime()`), but `_incremental_cache_update()` called `datetime.fromtimestamp(item.addedAt)`, raising `TypeError` on every run
  - Symptom in production logs: "Could not get recentlyAdded for section 'Films': 'datetime.datetime' object cannot be interpreted as an integer" → silent fallback to full cache rebuild (~30-60s wasted per run)
  - Added `CacheManager._normalize_added_at()` handling naive/aware datetimes, legacy epoch ints, and unparseable values (item skipped)
  - 3 regression tests added (naive datetime, aware datetime, old-item cutoff)
  - Location: `src/traktor/clients.py`

- **Resource leak: unclosed log handlers**
  - `setup_logging()` detached existing handlers via `logger.handlers = []` without `close()`, leaking a file descriptor per re-initialization
  - Now iterates handlers, calls `removeHandler()` + `close()`
  - Location: `src/traktor/log.py`

- **Resource leak: health server socket never closed**
  - `HealthServer.stop()` called `shutdown()` but not `server_close()`, leaving the listening socket open
  - Now calls `server_close()` and joins the server thread (timeout 5s)
  - Location: `src/traktor/health_server.py`

- **Removed all remaining `# type: ignore` suppressions (zero-suppression compliance)**
  - 4 leftover suppressions in conditional imports (`resource_manager.py`, `performance.py`, `conflict_resolver.py`) replaced with `importlib.util.find_spec()` guards + `__import__()` fallback per the documented pattern
  - Codebase now contains zero suppression directives (`type: ignore`, `noqa`, `filterwarnings`)

- **Duplicate auth-failure logging**
  - Trakt auth failures were logged with tracebacks at three layers (`_request`, `get_liked_lists`, `sync_lists`)
  - Single authoritative traceback now kept at `_request`; upper layers log context only
  - Location: `src/traktor/clients.py`, `src/traktor/sync.py`

- **Import ordering in clients.py** - Fixed ruff I001 linting error
  - Reordered import to follow standard pattern (stdlib, third-party, local)
  - `CircuitBreakerOpen` now correctly imported before `trakt_circuit_breaker` (alphabetical)
  - Location: `src/traktor/clients.py`

### Changed

- **Watch sync: movies always pull the full Trakt watched list**
  - `_pull_from_trakt()` no longer delta-filters movie history; `get_watched_movies()` is used in all
    modes so movies watched before the last sync are never lost (matches how shows were already handled)
  - `get_watched_movies()` is now paginated (respects Trakt `X-Pagination-Page-Count`) so large watch
    histories are not truncated
  - Location: `src/traktor/watch_sync.py`, `src/traktor/clients.py`

- **BREAKING: Python >= 3.10 now required** (was >=3.8)
  - `plexapi 4.18.2` and `requests 2.34.2` no longer support Python 3.8/3.9
  - `requires-python` bumped to `>=3.10`; black `target-version` bumped to `py310`

- **Dependency upgrades to newest stable**
  - plexapi 4.18.0 → 4.18.2, requests 2.32.5 → 2.34.2, certifi 2026.2.25 → 2026.7.22, urllib3 2.6.3 → 2.7.0, types-requests → 2.33.0.20260712

- **Dev toolchain now pinned in project venv**
  - Consolidated dev tools into `[dependency-groups] dev` (PEP 735): pytest >=9.0, black >=26.0, ruff >=0.16.0, mypy >=2.1 + type stubs
  - Removed duplicated `[project.optional-dependencies] dev`
  - `uv sync` now installs the full toolchain; verification no longer falls back to global installs
  - Result: `uv run mypy src/` reports **0 errors** (previously 8 stub-discovery errors from a global mypy that could not see project stubs)

### Improved

- **Codebase reformatted with black 26** (7 files adjusted to current style rules)

### Improved

- **Type Hints for Public Methods** - Better IDE support and code documentation
  - Added comprehensive type hints to all public methods in `clients.py`:
    - `RateLimiter`, `CacheManager`, `TraktAuth`, `TraktClient`, `PlexClient` classes
    - Return types for all methods (e.g., `-> Optional[Dict[str, Any]]`)
    - Parameter types for complex data structures
  - Added type hints to key public functions in `sync.py`:
    - `process_item_parallel()`, `process_list_parallel()`, `process_collection_sync()`, `process_watchlist_sync()`, `sync_lists()`
  - Added type hints to main `WatchSyncEngine` methods in `watch_sync.py`:
    - `sync_watched_status()`, `sync_playback_progress()`, `get_sync_summary()`
  - All type hints follow Python 3.8+ compatibility (no `list[str]` syntax)
  - Location: `src/traktor/clients.py`, `src/traktor/sync.py`, `src/traktor/watch_sync.py`

### Added

- **Trakt OAuth: automatic localhost callback capture**
  - When `TRAKT_REDIRECT_URI` is a localhost URL (e.g. `http://127.0.0.1:7001/callback`), traktor
    briefly listens on that port during `--force-auth` and captures the authorization code from the
    browser redirect automatically (no copy/paste); falls back to manual code input on timeout/error
  - `TRAKT_REDIRECT_URI` must exactly match the Redirect URI registered in the Trakt app settings;
    documented in `.env.example` and README
  - Location: `src/traktor/sync.py`

- **Playback Progress Sync (Resume Points)** - Sync where you left off
  - New `--sync-progress` CLI flag to sync playback progress from Trakt to Plex
  - Fetches progress data from Trakt's sync/playback endpoint
  - Updates Plex items to match Trakt's paused positions
  - Smart threshold: only updates if progress differs by >30 seconds
  - Skips fully watched items (progress >90%)
  - Works with `--dry-run` for previewing changes
  - Supports filtering with `--sync-movies-only` and `--sync-shows-only`
  - Location: `src/traktor/watch_sync.py` in `WatchSyncEngine.sync_playback_progress()`

- **Self-Diagnosis Command** - New `traktor --diagnose` command for troubleshooting
  - Comprehensive system checks (environment, configuration, connectivity)
  - Validates Python version, dependencies, and credentials
  - Tests Trakt API and Plex server connectivity
  - Provides actionable suggestions for common issues
  - Location: `src/traktor/diagnose.py`
  - 20 comprehensive tests added

- **Rate Limiting and Retry Logic** - Production-ready API reliability
  - TraktClient now enforces rate limiting (1000 req/5min = 0.3s interval)
  - Automatic retry with exponential backoff for 429 (rate limit) responses
  - Retry logic for 5xx server errors and connection issues
  - Thread-safe rate limiter using threading.Lock()
  - Location: `src/traktor/clients.py` in `TraktClient._rate_limit()` and `_request_with_retry()`

- **Incremental Cache Updates** - Faster cache management for large libraries
  - Uses Plex's `recentlyAdded` API to fetch only new/changed items
  - Merges new items with existing cache instead of full rebuild
  - Falls back to full rebuild when cache is expired (>24 hours)
  - 50% faster updates for libraries with few new items
  - Location: `src/traktor/clients.py` in `CacheManager._incremental_cache_update()`

- **Multiple Period Support for Official Lists** - Complete TRAKTOR_OFFICIAL_PERIODS implementation
  - New `--official-periods` CLI flag for comma-separated periods (e.g., "weekly,monthly")
  - Environment variable `TRAKTOR_OFFICIAL_PERIODS` support
  - Generates separate playlists for each period (e.g., "Played Weekly", "Played Monthly")
  - Location: `src/traktor/sync.py` and `src/traktor/cli.py`

- **Playback Progress Sync** - Resume point synchronization (foundation)
  - PlexClient: `get_playback_progress()`, `set_playback_progress()`, `batch_set_playback_progress()`
  - TraktClient: `get_playback_progress()` for retrieving playback state
  - Methods ready for integration into watch sync engine
  - Location: `src/traktor/clients.py` in both `PlexClient` and `TraktClient`

- **Docker Health Check** - Production container orchestration support
  - Added `healthcheck` section to `docker-compose.yml`
  - Uses `traktor --diagnose` for health validation
  - Configurable interval, timeout, and retries

### Fixed

- **Thread-safety fix in TraktOfficialClient** - Added threading.Lock() for rate limiter
  - Fixed race condition when `_last_request_time` was accessed by multiple threads
  - Location: `src/traktor/trakt_official.py`
- **Implemented parallel fetching in OfficialListsService** - Now uses ThreadPoolExecutor
  - Previously accepted `max_workers` parameter but used sequential for-loop
  - Now properly fetches multiple endpoints in parallel with thread-safe rate limiting
  - Location: `src/traktor/official_lists.py`
- **Fixed KeyError risk in `_parse_items`** - Added proper validation for entry structure
  - Changed `media_type in entry` check to safe `.get()` access
  - Added type checking for media_data before using it
  - Location: `src/traktor/trakt_official.py`
- **Extracted batch operation helper** - Consolidated duplicate code in clients.py
  - Created `_batch_history_operation()` helper for add/remove operations
  - Reduced ~150 lines of duplicated batch processing code
  - Both `add_to_history()` and `remove_from_history()` now use shared helper
  - Location: `src/traktor/clients.py`
- **Simplified official lists check** - Refactored nested conditionals in sync.py
  - Cleaner early-exit logic when no liked lists but official lists enabled
  - Location: `src/traktor/sync.py`
- **Improved exception handling** - More specific exception catching in watch_sync.py
  - Catches `AttributeError` and `NotFound` as expected errors (debug level)
  - Catches generic `Exception` as unexpected errors (warning level)
  - Added `NotFound` import from plexapi.exceptions
  - Location: `src/traktor/watch_sync.py`
- **Updated CONTRIBUTING.md** - Added missing modules to project structure
  - Added `progress.py`, `diagnose.py`, `official_lists.py`, `trakt_official.py`
  - Updated test file list to include all current tests
  - Location: `CONTRIBUTING.md`
- **Fixed unused `end_at` parameter** - Now properly passes `end_at` to Trakt API
  - The parameter was accepted but never passed to the API request
  - Location: `src/traktor/clients.py` in `get_watched_history()`

### Improved

- **Specific Exception Handling** - Replaced generic `except Exception` with specific exception types
  - `clients.py`: `mark_as_watched()` and `mark_as_unwatched()` now catch `NotFound`, `ConnectionError`, `TimeoutError` separately
  - `official_lists.py`: `fetch_endpoint()` now catches `RequestException`, `KeyError`, `ValueError`, `TypeError` with stale cache fallback
  - `watch_sync.py`: `_pull_from_trakt()` now catches `RequestException`, `KeyError`, `ValueError`, `TypeError` separately
  - `sync.py`: `process_collection_sync()` and `process_watchlist_sync()` now catch specific exceptions
    - `requests.exceptions.RequestException` for API errors
    - `(ValueError, KeyError, TypeError)` for data processing errors
  - `official_lists.py`: Cache operations now catch specific I/O exceptions
    - `(json.JSONDecodeError, FileNotFoundError, PermissionError, OSError)` for file operations
    - `(OSError, ValueError)` for cache validation
    - `(PermissionError, FileNotFoundError, OSError)` for cache writes
  - `history_manager.py`: State management now catches specific file/JSON exceptions
    - `(json.JSONDecodeError, FileNotFoundError, PermissionError, OSError)` for state operations
  - Better error messages and more accurate debugging information
  - All exception handlers maintain stale cache fallback for resilience
  - Location: `src/traktor/clients.py`, `src/traktor/official_lists.py`, `src/traktor/watch_sync.py`, `src/traktor/sync.py`, `src/traktor/history_manager.py`

- **Code Quality Improvements**
  - Removed unused `get_multiple_endpoints` method from `trakt_official.py` (functionality provided by `OfficialListsService`)
  - Removed unused `ThreadPoolExecutor` and `as_completed` imports from `trakt_official.py`
  - Converted `.format()` to f-strings in `sync.py` collection and watchlist descriptions (3 occurrences)
  - Added comprehensive tests for `utils.py` module (`normalize_tmdb_id()` function)
  - 12 new test cases covering various input types and edge cases
  - Converted `.format()` to f-strings in `trakt_official.py` (`_build_endpoint_path` method)
  - Added `requests` import to `official_lists.py` and `watch_sync.py` for specific exception handling

### Documentation

- **Updated AGENTS.md** - Added new code patterns and conventions section
  - Exception handling best practices with specific exception types
  - Stale cache fallback pattern for API error resilience
  - String formatting standards (always use f-strings)
  - Location: `AGENTS.md`

- **Updated README.md** - Added missing CLI flag documentation
  - Added `--official-periods` flag for multiple period playlist generation
  - Location: `README.md`

- **Comprehensive bug audit in TODO.md** - Documented all known issues in priority order
  - HIGH: Progress/resume point sync not implemented
  - HIGH: Rate limiting and retry logic missing
  - HIGH: Incremental cache updates not implemented
  - HIGH: TRAKTOR_OFFICIAL_PERIODS feature incomplete
  - MEDIUM: Health endpoint not implemented
  - MEDIUM: CONTRIBUTING.md outdated (now fixed)
  - LOW: unused `end_at` parameter (now fixed)
  - Location: `TODO.md`

### Added

- **Trakt Official Curated Lists** - Dynamic content discovery from Trakt's algorithmic lists
  - New `trakt_official.py` module with `TraktOfficialClient` for 13 public API endpoints
  - New `official_lists.py` module with `OfficialListsService` for caching and deduplication
  - Support for: trending, popular, played, watched, collected, anticipated, box office
  - Separate playlists per endpoint (e.g., "Trakt Movies - Trending", "Trakt Shows - Popular")
  - Smart caching with different TTLs per endpoint type (trending: 1h, anticipated: 24h)
  - Endpoint scoring for deduplication (items in multiple lists rank higher)
  - Period selection for stats endpoints: daily, weekly (default), monthly, yearly
  - New CLI flags: `--official-lists`, `--official-endpoints`, `--official-period`, `--list-source`
  - New environment variables: `TRAKTOR_OFFICIAL_LISTS_ENABLED`, `TRAKTOR_OFFICIAL_ENDPOINTS`, `TRAKTOR_OFFICIAL_PERIOD`
  - Comprehensive test coverage: 56 new tests

- **Two-way Watch Status Sync** - Bidirectional synchronization of watched status between Plex and Trakt TV
  - New `history_manager.py` module for tracking sync state
  - New `watch_sync.py` module with sync engine
  - New `conflict_resolver.py` module with resolution strategies (newest_wins, plex_wins, trakt_wins)
  - Extended `TraktClient` with watch history APIs (`get_watched_history`, `get_watched_movies`, `get_watched_shows`, `add_to_history`, `remove_from_history`)
  - Extended `PlexClient` with watch status methods (`get_watched_items`, `mark_as_watched`, `mark_as_unwatched`, `get_play_history`, `is_watched`)
  - New CLI flags: `--sync-watched`, `--watch-direction`, `--watch-conflict`, `--dry-run`, `--sync-movies-only`, `--sync-shows-only`, `--backfill-history`
  - New environment variables: `WATCH_SYNC_ENABLED`, `WATCH_SYNC_DIRECTION`, `WATCH_SYNC_CONFLICT_RESOLUTION`
  - New `AGENTS.md` documentation file for AI coding agent guidance
  - Comprehensive unit tests for new modules (`test_history_manager.py`, `test_watch_sync.py`, `test_conflict_resolver.py`)

- **Progress Visualization** - Real-time progress tracking with ETA calculation
  - New `progress.py` module with `ProgressTracker` and `SyncProgress` classes
  - Multi-stage progress tracking for sync operations
  - Speed metrics (items/second) and ETA calculations

### Fixed

- **CRITICAL**: Fixed episode key unpacking bug in `watch_sync.py` `_calculate_changes()` method
  - Episode keys are 4-tuples `("episode", show_imdb, season_num, episode_num)` but code was unpacking as 3-tuple
  - This would have crashed when processing episodes in watch sync
- **CRITICAL**: Fixed potential `AttributeError` when `plex_info` is `None` in `_calculate_changes()`
  - Now properly checks for item existence in Plex before attempting to mark as watched/unwatched
- **CRITICAL**: Fixed early return bug in `sync.py` when no liked lists exist but official lists are enabled
  - The function would return early and skip official lists processing when `liked_lists` was empty
  - Now correctly checks environment setting `TRAKTOR_OFFICIAL_LISTS_ENABLED` when args is None
- **HIGH**: Fixed duplicate watched shows processing in `watch_sync.py` `_pull_from_trakt()` method
  - Removed duplicate episode processing block that caused double API calls and potential data overwrites
  - Now only uses `get_watched_shows()` for episode data
- **MEDIUM**: Fixed DRY violation in `clients.py` - extracted token refresh retry logic
  - Created `_post_with_token_refresh()` helper method
  - Reduced ~60 lines of duplicate code in `add_to_history()` and `remove_from_history()`
- TMDb ID type mismatch in cache lookups - now consistently stored as strings
- Removed incorrect `@staticmethod` decorator from `_update_playlist_description`
- Fixed imdb_id loss when media is matched via TMDb instead of IMDB
- Added clear error message when partial Plex credentials (only URL or only token) are provided
- Fixed unused `MAX_WORKERS` import in `log.py`
- Fixed watch sync `_pull_from_plex` using non-existent `plex_cache` key - now properly uses CacheManager
- Implemented unused CLI flags: `--sync-movies-only`, `--sync-shows-only`, `--backfill-history`
- Fixed unused settings `WATCH_SYNC_DIRECTION` and `WATCH_SYNC_CONFLICT_RESOLUTION` now used as defaults
- Refactored `_extract_external_ids` duplicated logic into `_parse_guid_for_ids` helper
- Added URL validation for Plex credentials
- Optimized cache lookups in `_pull_from_plex()` to use pre-built reverse mappings
  - Changed from O(n*m) to O(n) complexity for external ID lookups
- Removed unused `not_found` list from `_collect_plex_items()` in `sync.py`
- Refactored backward-compatible wrapper functions to use module-level tracker instance
- Simplified episode count calculation in `get_watched_shows()` in `clients.py`
- Removed unused `SyncDecisionMatrix` class from `conflict_resolver.py`
- Consolidated duplicate TMDb ID normalization logic in `watch_sync.py`
  - Added `_normalize_tmdb_id()` helper method
  - Ensures consistent string format across all TMDb ID handling
- Removed redundant try/except block in `sync.py` `_collect_plex_items()` (lines 302-308)
  - Exception was caught only to be logged and immediately re-raised
  - Simplified code flow without losing error visibility
- Added explicit default values to `dict.get()` calls throughout codebase
  - Fixed `sync.py`: `item.get("type")` → `item.get("type", None)`, `media.get("ids", {}).get("imdb")` → `media.get("ids", {}).get("imdb", None)`
  - Fixed `sync.py`: `result.get("success")` → `result.get("success", False)` for safer boolean checks
  - Prevents unexpected `None` returns that could cause type errors

### Changed

- Improved error messages throughout codebase with more context
- Enhanced logging with better formatting and more debug information
- Playlist items now sorted with movies first to ensure proper playlist type categorization in Plex

## [1.0.0] - 2026-03-23

### Added

- Initial public-ready release of `traktor`
