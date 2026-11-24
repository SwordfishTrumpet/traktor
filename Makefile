.PHONY: help install run dev test lint format-check format typecheck clean

help:
	@echo "Traktor - Trakt to Plex Playlist Sync"
	@echo ""
	@echo "Available commands:"
	@echo "  make install      - Install dependencies with uv"
	@echo "  make run          - Run the sync script"
	@echo "  make dev          - Install with dev dependencies"
	@echo "  make test         - Run tests"
	@echo "  make lint         - Run every check CI runs: formatting (black) and linter (ruff)"
	@echo "  make format-check - Check formatting without changing files (black --check)"
	@echo "  make typecheck    - Run the type checker (mypy)"
	@echo "  make format       - Format code (black)"
	@echo "  make clean        - Clean build artifacts"
	@echo "  make setup        - Initial setup"

install:
	uv sync

run:
	uv run traktor

dev:
	uv sync --group dev

test:
	uv run pytest

# The inner-loop target: the same suite, stopping at the first failure, so a preflight
# over a batch answers in one pass instead of after every file. The pipeline's
# `gates.sh fast` looks for this name.
test-fast:
	uv run pytest -q -x

# `make lint` is the check CI runs, so a green local gate predicts a green CI:
# the CI job fails a formatting difference, and `make lint` used to run ruff
# only, so a batch could pass every local check and land red on main (issue #71).
lint: format-check
	uv run ruff check src/ tests/

format-check:
	uv run black --check src/ tests/

typecheck:
	uv run mypy src/

format:
	uv run black src/
	uv run ruff check --fix src/

clean:
	rm -rf .venv
	rm -rf src/*.egg-info
	rm -rf build/
	rm -rf dist/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

setup:
	./setup.sh
