.PHONY: help install run dev test lint format clean

help:
	@echo "Traktor - Trakt to Plex Playlist Sync"
	@echo ""
	@echo "Available commands:"
	@echo "  make install    - Install dependencies with uv"
	@echo "  make run        - Run the sync script"
	@echo "  make dev        - Install with dev dependencies"
	@echo "  make test       - Run tests"
	@echo "  make lint       - Run linter (ruff)"
	@echo "  make format     - Format code (black)"
	@echo "  make clean      - Clean build artifacts"
	@echo "  make setup      - Initial setup"

install:
	uv sync

run:
	uv run traktor

dev:
	uv sync --extra dev

test:
	uv run pytest

lint:
	uv run ruff check src/

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
