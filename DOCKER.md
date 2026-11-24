# Docker Guide

Run `traktor` in Docker when you want isolated dependencies and persisted runtime state under `./data`.

## Quick start

```bash
cp .env.example .env
docker compose build
docker compose run --rm traktor
```

Required `.env` values:

```env
TRAKT_CLIENT_ID=your_trakt_client_id
TRAKT_CLIENT_SECRET=your_trakt_client_secret
PLEX_URL=http://your-plex-host:32400
PLEX_TOKEN=your_plex_token
```

## Persisted Docker paths

When `DOCKER_MODE=true`, the app uses:

- `/data/config/.traktor_config.json`
- `/data/config/.traktor_trakt_token.json`
- `/data/config/.traktor_cache`
- `/data/logs/traktor.log`
- `/data/.traktor_watch_sync.json` (the delta window the watch sync reuses)
- `/data/.traktor/undo/` (undo snapshots)
- `/home/traktor/.traktor_backups/` (snapshots, unless `TRAKTOR_BACKUP_DIR` says otherwise)

`docker-compose.yml` mounts exactly two directories for these: `./data` on `/data` and
`./data/backups` on `/home/traktor/.traktor_backups`. Everything the container persists lives
under one of them, so `docker compose run --rm` does not discard it.

Compose creates a missing host directory as `root`, and the container runs as user `traktor`
(uid 1000), so create them yourself once:

```bash
mkdir -p data/backups && sudo chown -R 1000:1000 data
```

## Common commands

```bash
docker compose build
docker compose run --rm traktor
docker compose run --rm traktor -v
docker compose run --rm traktor --refresh-cache
docker compose run --rm traktor --force-auth
docker compose run --rm traktor -v --refresh-cache -w 16
docker compose down
```

## Scheduling

Example cron entry:

```bash
0 */6 * * * cd /path/to/traktor && docker compose run --rm traktor
```

## Troubleshooting

- `.env` missing: create it from `.env.example`
- Plex unreachable: verify `PLEX_URL` is reachable from inside Docker
- auth loop: rerun with `--force-auth`
- stale matches: rerun with `--refresh-cache`
- logs: inspect `data/logs/traktor.log`
- snapshots: `data/backups/` on the host, `/home/traktor/.traktor_backups` in the container

## Security notes

- mount `.env` read-only when possible
- do not commit real `.env`, token files, cache files, or logs
- treat `data/config` as secret-bearing runtime state
- `data/` also holds the watch-sync delta file, undo snapshots and snapshots; treat it the same
