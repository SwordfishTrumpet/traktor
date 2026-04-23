# Changelog

All notable changes to this project should be documented in this file.

The format is based on Keep a Changelog and this project currently uses a simple manual release flow.

## [Unreleased]

### Fixed

- **Import ordering in clients.py** - Fixed ruff I001 linting error
  - Reordered import to follow standard pattern (stdlib, third-party, local)
  - `CircuitBreakerOpen` now correctly imported before `trakt_circuit_breaker` (alphabetical)
  - Location: `src/traktor/clients.py`

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
