# Quick Start

## 1. Install dependencies

```bash
uv sync
```

## 2. Create your config

```bash
cp .env.example .env
```

Fill in at least:

```env
TRAKT_CLIENT_ID=your_trakt_client_id
TRAKT_CLIENT_SECRET=your_trakt_client_secret
PLEX_URL=http://your-plex-host:32400
PLEX_TOKEN=your_plex_token
```

## 3. Run the sync

```bash
uv run traktor
```

On first run, the app prompts you to complete Trakt OAuth.

## Common commands

```bash
uv run traktor
uv run traktor -v
uv run traktor --refresh-cache
uv run traktor --force-auth
uv run traktor -v --refresh-cache -w 16
uv run traktor --help
```

## Helpful paths

- local log: `~/.traktor/traktor.log`
- local config: `~/.traktor_config.json`
- local token file: `~/.traktor_trakt_token.json`
- local cache: `~/.traktor_cache`
- per-run missing report: `missing.txt`

## Development

```bash
uv sync --extra dev
uv run ruff check src/
uv run black src/
uv run pytest
```
