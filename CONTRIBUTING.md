# Contributing to traktor

Thank you for your interest in contributing to traktor! This document provides guidelines and information for contributors.

## Table of Contents

- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Workflow](#workflow)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Commit Guidelines](#commit-guidelines)
- [Pull Request Process](#pull-request-process)
- [Release Process](#release-process)
- [Questions?](#questions)

## Development Setup

### Prerequisites

- Python 3.8 or higher
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager
- Git

### Initial Setup

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/traktor.git
   cd traktor
   ```

3. Install dependencies:
   ```bash
   uv sync --extra dev
   ```

4. Create your environment configuration:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials (do not commit this file)
   ```

5. Verify everything works:
   ```bash
   uv run traktor --help
   uv run pytest
   ```

## Project Structure

```
src/traktor/
├── __init__.py              # Package version and exports
├── __main__.py              # Entry point for `python -m traktor`
├── cli.py                   # CLI argument parsing and main entrypoint
├── sync.py                  # Core sync orchestration (lists → playlists)
├── clients.py               # TraktAuth, TraktClient, PlexClient, CacheManager
├── config.py                # Configuration and credential helpers
├── log.py                   # Logging setup with rotating file handler
├── settings.py              # Runtime settings and path resolution
├── watch_sync.py            # Watch status sync engine
├── history_manager.py       # Watch sync state tracking
├── conflict_resolver.py     # Conflict resolution strategies
├── progress.py              # Progress tracking and visualization
├── diagnose.py              # Self-diagnosis command for troubleshooting
├── official_lists.py        # Official Trakt lists service
└── trakt_official.py        # Trakt official lists API client

tests/
├── conftest.py              # Shared test fixtures
├── test_cli.py
├── test_sync.py
├── test_clients.py
├── test_config.py
├── test_watch_sync.py
├── test_history_manager.py
├── test_conflict_resolver.py
├── test_diagnose.py
├── test_official_lists.py
└── test_trakt_official.py
```

## Workflow

1. **Create a branch** for your changes:
   - `feature/description` for new features
   - `bugfix/description` for bug fixes
   - `docs/description` for documentation updates
   - `refactor/description` for code refactoring

2. **Make your changes** following our coding standards

3. **Test your changes** thoroughly

4. **Submit a pull request** following our PR template

## Coding Standards

We use the following tools to maintain code quality:

### Formatting
- **Black** for code formatting (100 character line length)
- Run: `uv run black src/ tests/`

### Linting
- **Ruff** for fast Python linting (rules: E, F, I, W)
- Run: `uv run ruff check src/ tests/`

### Type Hints
- Use type hints where practical, especially for public APIs
- Follow Google-style docstrings

### Import Order
1. Standard library imports
2. Third-party imports
3. Local imports

Example:
```python
import os
import sys
from datetime import datetime

import requests
from plexapi.server import PlexServer

from .config import load_config
from .log import logger
```

## Testing

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov

# Run specific test file
uv run pytest tests/test_sync.py

# Run with verbose output
uv run pytest -v
```

### Writing Tests

- Use `pytest` for all tests
- Use `monkeypatch` for mocking external dependencies
- Test files should be named `test_<module>.py`
- Test functions should be named `test_<description>`

Example:
```python
def test_cache_lookup_by_imdb(monkeypatch):
    """Test that cache lookup works with IMDb IDs."""
    # Arrange
    mock_cache = {"movies_by_imdb": {"tt123": {"title": "Test Movie"}}}
    
    # Act
    result = find_movie_by_imdb("tt123")
    
    # Assert
    assert result is not None
    assert result["title"] == "Test Movie"
```

### Testing Guidelines

- **DO** test pure logic and helper functions
- **DO** mock external API calls (Plex, Trakt)
- **DO** test error handling paths
- **DO NOT** run live syncs in automated tests
- **DO NOT** commit real credentials in tests

## Commit Guidelines

We follow conventional commit format:

```
type(scope): subject

body (optional)

footer (optional)
```

### Types

- **feat**: New feature
- **fix**: Bug fix
- **docs**: Documentation changes
- **style**: Code style changes (formatting, no logic change)
- **refactor**: Code refactoring
- **test**: Test additions or updates
- **chore**: Build process, dependencies, etc.

### Examples

```
feat(sync): add support for custom playlist descriptions

fix(cache): handle TMDb IDs as strings consistently

docs(readme): update Docker setup instructions
test(watch): add tests for conflict resolution strategies
```

### Commit Best Practices

- Keep commits atomic and focused
- Write clear, descriptive commit messages
- Reference issues when applicable: `Fixes #123`
- Do not commit sensitive data (tokens, credentials)

## Pull Request Process

1. **Update your branch** with the latest `main`:
   ```bash
   git fetch origin
   git rebase origin/main
   ```

2. **Run all checks** before submitting:
   ```bash
   uv run ruff check src/ tests/
   uv run black src/ tests/
   uv run pytest
   ```

3. **Fill out the PR template** completely

4. **Request review** from maintainers

5. **Address feedback** promptly

6. **Squash commits** if requested (maintainers can also squash on merge)

### PR Checklist

- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Tests added/updated and passing
- [ ] Documentation updated (if needed)
- [ ] No sensitive data exposed
- [ ] Commit messages are clear

## Release Process

This project uses a manual release flow:

1. Update `CHANGELOG.md` under `## [Unreleased]`
2. Run full test suite: `uv run pytest`
3. Run linting: `uv run ruff check src/ tests/`
4. Bump version in `pyproject.toml` when needed
5. Create GitHub release with summary from changelog

## Questions?

- **General questions**: Open a [GitHub Discussion](https://github.com/SwordfishTrumpet/traktor/discussions)
- **Bug reports**: Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md)
- **Feature requests**: Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md)

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## License

By contributing to traktor, you agree that your contributions will be licensed under the MIT License.
