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

In this repository, those map to the checked-in `data/` folder when you mount it through Compose.

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

## Security notes

- mount `.env` read-only when possible
- do not commit real `.env`, token files, cache files, or logs
- treat `data/config` as secret-bearing runtime state
